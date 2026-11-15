"""Extraction strategies shared by local execution and the Daytona sandbox runner."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup


STRATEGIES = ("bs4-fast", "playwright-dom", "ai-heuristic")
MAX_HTML_BYTES = 4 * 1024 * 1024
MIN_IMAGE_DIMENSION = 80
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template"}
BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "section", "ul",
}


@dataclass(slots=True)
class ImageRef:
    url: str
    alt: str = ""
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class Extraction:
    valid: bool
    markdown: str
    images: list[ImageRef] = field(default_factory=list)
    stats: dict[str, int | str] = field(default_factory=dict)
    error: str | None = None


class PageParser(HTMLParser):
    """Small dependency-free parser so the same extraction script runs in Daytona."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self.semantic_parts: list[list[str]] = []
        self.semantic_stack: list[list[str]] = []
        self.links: list[tuple[str, str]] = []
        self.images: list[ImageRef] = []
        self.tables: list[list[list[str]]] = []
        self.table_rows: list[list[str]] | None = None
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.current_link: tuple[str, list[str]] | None = None

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def _append_text(self, text: str) -> None:
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean or self.skip_depth:
            return
        self.body_parts.append(clean)
        for target in self.semantic_stack:
            target.append(clean)
        if self.current_cell is not None:
            self.current_cell.append(clean)
        if self.current_link is not None:
            self.current_link[1].append(clean)
        if self.in_title:
            self.title_parts.append(clean)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = self._attrs(attrs)
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = True
        if tag in {"main", "article"}:
            target: list[str] = []
            self.semantic_parts.append(target)
            self.semantic_stack.append(target)
        if tag in {"p", "h1", "h2", "h3", "h4", "li"}:
            self.body_parts.append("\n")
        if tag == "a" and attrs_dict.get("href"):
            link_url = urljoin(self.base_url, attrs_dict["href"])
            if urlsplit(link_url).scheme in {"http", "https"}:
                self.current_link = (link_url, [])
        if tag == "img":
            source = attrs_dict.get("src") or attrs_dict.get("data-src") or attrs_dict.get("data-lazy-src")
            if source:
                image_url = urljoin(self.base_url, source.strip())
                width = self._dimension(attrs_dict.get("width"))
                height = self._dimension(attrs_dict.get("height"))
                if self._keep_image(image_url, width, height):
                    self.images.append(
                        ImageRef(image_url, attrs_dict.get("alt", "").strip(), width, height)
                    )
        if tag == "table":
            self.table_rows = []
        elif tag == "tr" and self.table_rows is not None:
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = False
        if tag in {"main", "article"} and self.semantic_stack:
            self.semantic_stack.pop()
        if tag in {"td", "th"} and self.current_cell is not None:
            if self.current_row is not None:
                self.current_row.append(" ".join(self.current_cell))
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            if self.table_rows is not None:
                self.table_rows.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.table_rows is not None:
            if self.table_rows:
                self.tables.append(self.table_rows)
            self.table_rows = None
        if tag == "a" and self.current_link is not None:
            href, text = self.current_link
            self.links.append((href, " ".join(text).strip() or href))
            self.current_link = None
        if tag in BLOCK_TAGS:
            self.body_parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._append_text(data)

    @staticmethod
    def _dimension(value: str | None) -> int | None:
        if not value:
            return None
        match = re.match(r"\s*(\d+)", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _keep_image(url: str, width: int | None, height: int | None) -> bool:
        parsed = urlsplit(url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or path.endswith(".svg"):
            return False
        if width is not None and width < MIN_IMAGE_DIMENSION:
            return False
        if height is not None and height < MIN_IMAGE_DIMENSION:
            return False
        return True


def _clean_text(parts: list[str]) -> str:
    text = " ".join(parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _render_table(rows: list[list[str]]) -> str:
    cleaned = [[re.sub(r"\s+", " ", cell).strip().replace("|", r"\|") for cell in row] for row in rows]
    width = max((len(row) for row in cleaned), default=0)
    if not width:
        return ""
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    header = cleaned[0]
    body = cleaned[1:]
    return "\n".join(
        ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(row) + " |" for row in body]
    )


def parse_html(html: str, url: str, strategy: str, fields: set[str]) -> Extraction:
    parser = PageParser(url)
    parser.feed(html)
    title = _clean_text(parser.title_parts)
    if strategy == "ai-heuristic" and parser.semantic_parts:
        text = max((_clean_text(section) for section in parser.semantic_parts), key=len)
    else:
        text = _clean_text(parser.body_parts)

    sections: list[str] = []
    if title:
        sections.extend([f"# {title}", ""])
    if "text" in fields and text:
        sections.append(text)
    if "tables" in fields and parser.tables:
        sections.extend(["", "## Tables", ""])
        sections.extend(table for table in (_render_table(rows) for rows in parser.tables) if table)
    if "links" in fields and parser.links:
        sections.extend(["", "## Links", ""])
        sections.extend(f"- [{label}]({href})" for href, label in parser.links[:100])
    if "images" in fields and parser.images:
        sections.extend(["", "## Images", ""])
        sections.extend(f"![{image.alt or 'Image'}]({image.url})" for image in parser.images[:50])

    markdown = "\n".join(sections).strip()
    usable_text = len(re.sub(r"\s", "", text))
    valid = bool(usable_text >= 40 or parser.tables or parser.images)
    return Extraction(
        valid=valid,
        markdown=markdown,
        images=parser.images[:50] if "images" in fields else [],
        stats={
            "characters": len(text),
            "tables": len(parser.tables),
            "images": len(parser.images),
            "links": len(parser.links),
            "strategy": strategy,
        },
        error=None if valid else "The page did not contain enough extractable content.",
    )


def _validate_hostname(hostname: str) -> None:
    if not hostname or hostname.lower() == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Only public website URLs are supported.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("Only public website URLs are supported.")
        return

    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve website host: {hostname}") from exc
    if not addresses:
        raise ValueError(f"Could not resolve website host: {hostname}")
    for address_info in addresses:
        resolved = ipaddress.ip_address(address_info[4][0])
        if not resolved.is_global:
            raise ValueError("Only public website URLs are supported.")


def validate_url_syntax(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a complete http:// or https:// website URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Website URLs must not include usernames or passwords.")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("The URL contains an invalid port.")
    _validate_hostname(parsed.hostname)
    return url.strip()


async def validate_public_url(url: str) -> str:
    return await asyncio.to_thread(validate_url_syntax, url)


async def fetch_html(url: str) -> tuple[str, str]:
    timeout = httpx.Timeout(8.0, connect=4.0)
    headers = {
        "User-Agent": "SandScrape/1.0 (+https://example.invalid; respectful extraction)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    }
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False, headers=headers
    ) as client:
        current_url = url
        for _ in range(6):
            await validate_public_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("The website returned an invalid redirect.")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not any(
                    kind in content_type for kind in ("text/html", "application/xhtml+xml")
                ):
                    raise ValueError("The URL did not return an HTML page.")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_HTML_BYTES:
                        raise ValueError("The website response exceeded the 4 MB HTML limit.")
                encoding = response.encoding or "utf-8"
                return content.decode(encoding, errors="replace"), str(response.url)
    raise ValueError("The website redirected too many times.")


async def extract_local(url: str, strategy: str, fields: set[str]) -> Extraction:
    """Run one extraction strategy locally when Daytona credentials are absent."""
    try:
        html, final_url = await fetch_html(url)
        if strategy == "playwright-dom":
            return Extraction(
                valid=False,
                markdown="",
                error="Playwright strategy requires a configured Daytona sandbox.",
                stats={"strategy": strategy},
            )
        if strategy == "bs4-fast":
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(("script", "style", "noscript", "svg", "iframe", "template")):
                tag.decompose()
            html = str(soup)
        return parse_html(html, final_url, strategy, fields)
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        return Extraction(
            valid=False, markdown="", error=str(exc), stats={"strategy": strategy}
        )


def build_daytona_script(url: str, strategy: str, fields: set[str]) -> str:
    """Return a self-contained Python script to execute in a Daytona sandbox."""
    import json

    encoded_url = json.dumps(url)
    encoded_strategy = json.dumps(strategy)
    encoded_fields = json.dumps(sorted(fields))
    return f'''
import html.parser, ipaddress, json, re, socket, urllib.error, urllib.parse, urllib.request

URL = {encoded_url}
STRATEGY = {encoded_strategy}
FIELDS = set({encoded_fields})

class Parser(html.parser.HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.skip, self.title_on = base, 0, False
        self.body, self.title, self.semantic, self.semantic_stack = [], [], [], []
        self.images, self.links, self.tables = [], [], []
        self.rows, self.row, self.cell, self.link = None, None, None, None
    def data(self, value):
        value = re.sub(r"\\s+", " ", value).strip()
        if not value or self.skip: return
        self.body.append(value)
        for section in self.semantic_stack: section.append(value)
        if self.cell is not None: self.cell.append(value)
        if self.link is not None: self.link[1].append(value)
        if self.title_on: self.title.append(value)
    def handle_starttag(self, tag, raw):
        attrs = {{k: v or "" for k, v in raw}}
        if tag in ("script","style","noscript","svg","iframe","template"):
            self.skip += 1; return
        if self.skip: return
        if tag == "title": self.title_on = True
        if tag in ("main","article"):
            section = []; self.semantic.append(section); self.semantic_stack.append(section)
        if tag == "a" and attrs.get("href"):
            href = urllib.parse.urljoin(self.base, attrs["href"])
            if urllib.parse.urlsplit(href).scheme in ("http","https"): self.link = [href, []]
        if tag == "img":
            src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-lazy-src")
            if src:
                target = urllib.parse.urljoin(self.base, src)
                try: w = int(attrs.get("width", "0") or 0); h = int(attrs.get("height", "0") or 0)
                except ValueError: w, h = 0, 0
                if urllib.parse.urlsplit(target).scheme in ("http","https") and not urllib.parse.urlsplit(target).path.lower().endswith(".svg") and (not w or w >= 80) and (not h or h >= 80):
                    self.images.append((target, attrs.get("alt", "")))
        if tag == "table": self.rows = []
        elif tag == "tr" and self.rows is not None: self.row = []
        elif tag in ("td","th") and self.row is not None: self.cell = []
    def handle_endtag(self, tag):
        if tag in ("script","style","noscript","svg","iframe","template"):
            if self.skip: self.skip -= 1
            return
        if self.skip: return
        if tag == "title": self.title_on = False
        if tag in ("main","article") and self.semantic_stack: self.semantic_stack.pop()
        if tag in ("td","th") and self.cell is not None:
            if self.row is not None: self.row.append(" ".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.rows is not None: self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            if self.rows: self.tables.append(self.rows)
            self.rows = None
        if tag == "a" and self.link is not None:
            self.links.append((self.link[0], " ".join(self.link[1]).strip() or self.link[0]))
            self.link = None
    def handle_data(self, data): self.data(data)

def check(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http","https") or not p.hostname or p.username or p.password:
        raise ValueError("Only public http(s) website URLs are supported.")
    if p.hostname == "localhost" or p.hostname.endswith(".localhost"):
        raise ValueError("Only public website URLs are supported.")
    try:
        ip = ipaddress.ip_address(p.hostname)
        if not ip.is_global: raise ValueError("Only public website URLs are supported.")
    except ValueError as e:
        if str(e) == "Only public website URLs are supported.": raise
        try: ips = [x[4][0] for x in socket.getaddrinfo(p.hostname, None)]
        except OSError: raise ValueError("Could not resolve website host.")
        if not ips or any(not ipaddress.ip_address(x).is_global for x in ips):
            raise ValueError("Only public website URLs are supported.")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl); check(target)
        return super().redirect_request(req, fp, code, msg, headers, target)

try:
    check(URL)
    html = None
    final_url = URL
    if STRATEGY == "playwright-dom":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                def guard_request(route):
                    check(route.request.url)
                    route.continue_()
                page.route("**/*", guard_request)
                page.goto(URL, wait_until="domcontentloaded", timeout=8000)
                final_url = page.url
                check(final_url)
                html = page.content()
            finally:
                browser.close()
    else:
        opener = urllib.request.build_opener(NoRedirect())
        request = urllib.request.Request(URL, headers={{"User-Agent":"SandScrape/1.0","Accept":"text/html"}})
        with opener.open(request, timeout=8) as response:
            if "html" not in response.headers.get("Content-Type","text/html").lower():
                raise ValueError("The URL did not return an HTML page.")
            html = response.read(4 * 1024 * 1024 + 1).decode("utf-8", "replace")
            if len(html) > 4 * 1024 * 1024: raise ValueError("The website response exceeded the 4 MB HTML limit.")
            final_url = response.geturl()
    if STRATEGY == "bs4-fast":
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(("script","style","noscript","svg","iframe","template")): tag.decompose()
            html = str(soup)
        except ImportError:
            pass
    parser = Parser(final_url); parser.feed(html)
    clean = lambda xs: re.sub(r"\\s+", " ", " ".join(xs)).strip()
    title = clean(parser.title)
    text = clean(max(parser.semantic, key=lambda x: len(clean(x)))) if STRATEGY == "ai-heuristic" and parser.semantic else clean(parser.body)
    out = (["# " + title, ""] if title else [])
    if "text" in FIELDS and text: out.append(text)
    if "tables" in FIELDS and parser.tables:
        out.extend(["", "## Tables", ""])
        for rows in parser.tables:
            width = max(map(len, rows))
            rows = [r + [""] * (width - len(r)) for r in rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("| " + " | ".join(["---"] * width) + " |")
            out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    if "links" in FIELDS and parser.links:
        out.extend(["", "## Links", ""])
        out.extend("- [" + label + "](" + href + ")" for href, label in parser.links[:100])
    if "images" in FIELDS and parser.images:
        out.extend(["", "## Images", ""])
        out.extend("![" + (alt or "Image") + "](" + src + ")" for src, alt in parser.images[:50])
    markdown = "\\n".join(out).strip()
    valid = len(re.sub(r"\\s", "", text)) >= 40 or bool(parser.tables or parser.images)
    images = parser.images[:50] if "images" in FIELDS else []
    print(json.dumps({{"valid":valid,"markdown":markdown,"images":[{{"url":u,"alt":a}} for u,a in images],"stats":{{"characters":len(text),"tables":len(parser.tables),"images":len(parser.images),"links":len(parser.links),"strategy":STRATEGY}},"error":None if valid else "The page did not contain enough extractable content."}}))
except Exception as exc:
    print(json.dumps({{"valid":False,"markdown":"","images":[],"stats":{{"strategy":STRATEGY}},"error":str(exc)}}))
'''
