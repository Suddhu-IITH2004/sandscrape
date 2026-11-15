"""
strategies.py — self-contained extraction programs that run INSIDE Daytona sandboxes.

Each strategy is assembled into one Python source string:  PRELUDE + strategy body + params.
The host never imports this code's runtime helpers; it ships the string to
`sandbox.process.code_run()` (or a local subprocess in rehearsal mode).

Contract: the program prints exactly one line starting with RESULT_MARKER, followed by JSON:
    {"valid": bool, "markdown": str, "images": [{"url", "alt", "width"}], "stats": {...}}
"""
from __future__ import annotations

import json

RESULT_MARKER = "__SANDSCRAPE_RESULT__"

STRATEGY_ORDER = ["bs4-fast", "playwright-dom", "ai-heuristic"]

STRATEGY_META = {
    "bs4-fast": {"label": "Sandbox Alpha", "desc": "httpx + BeautifulSoup4 + markdownify"},
    "playwright-dom": {"label": "Sandbox Beta", "desc": "Playwright headless Chromium"},
    "ai-heuristic": {"label": "Sandbox Gamma", "desc": "Text-density + JSON-LD structural heuristics"},
}

# ---------------------------------------------------------------------------
# Shared prelude (runs inside the sandbox)
# ---------------------------------------------------------------------------
PRELUDE = r'''
import json, re, sys, time, subprocess, importlib
from urllib.parse import urljoin, urlparse

RESULT_MARKER = "__SANDSCRAPE_RESULT__"
T0 = time.time()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SandScrape/1.0")

def emit(result):
    result.setdefault("stats", {})["elapsed_ms"] = int((time.time() - T0) * 1000)
    print(RESULT_MARKER + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()

def fail(reason, **stats):
    emit({"valid": False, "markdown": "", "images": [], "stats": dict(stats, reason=reason)})

def ensure(*pkgs):
    """Import packages, pip-installing any that the snapshot lacks (slow path)."""
    missing = []
    for mod, pip_name in pkgs:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *missing],
                       check=False, capture_output=True, timeout=60)
    return missing

ensure(("httpx", "httpx"), ("bs4", "beautifulsoup4"), ("markdownify", "markdownify"))
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as _md

def fetch(url, timeout=8.0):
    with httpx.Client(follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text, str(r.url)

BROKEN_LAYOUT = "<html><head><title>Redesigned</title></head><body><div class='v2-root' data-hydrate></div></body></html>"

NOISE_TAGS = ["script", "style", "noscript", "template", "iframe", "svg", "form", "button"]
CHROME_TAGS = ["nav", "footer", "header", "aside"]
ICON_HINTS = re.compile(r"(sprite|pixel|tracking|spacer|badge|icon|logo|avatar|emoji|1x1|blank)", re.I)

def to_md(node):
    return _md(str(node), heading_style="ATX", bullets="-")

def clean_md(text, limit=14000):
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    return text[:limit] + ("\n\n_…truncated_" if len(text) > limit else "")

def _int(v):
    try:
        return int(re.sub(r"[^0-9]", "", str(v)) or 0)
    except Exception:
        return 0

def pick_src(img):
    for attr in ("data-src", "data-original", "data-lazy-src", "src"):
        v = img.get(attr)
        if v and not v.startswith("data:"):
            return v
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        return srcset.split(",")[-1].strip().split(" ")[0]
    return None

def collect_images(root, base, limit=12):
    seen, out = set(), []
    for img in root.find_all("img"):
        src = pick_src(img)
        if not src:
            continue
        url = urljoin(base, src)
        path = urlparse(url).path.lower()
        w, h = _int(img.get("width")), _int(img.get("height"))
        if path.endswith((".svg", ".ico")) or (path.endswith(".gif") and w and w < 120):
            continue
        if (w and w < 80) or (h and h < 60):
            continue
        if ICON_HINTS.search(path) and not (w >= 200):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "alt": (img.get("alt") or "").strip()[:200], "width": w or None})
        if len(out) >= limit:
            break
    # Larger declared images first: they are more likely to be content (charts, products).
    out.sort(key=lambda i: -(i["width"] or 150))
    return out

def collect_meta(soup, base):
    def m(**kw):
        tag = soup.find("meta", attrs=kw)
        return tag.get("content", "").strip() if tag else ""
    meta = {
        "title": (soup.title.string or "").strip() if soup.title and soup.title.string else "",
        "description": m(name="description") or m(property="og:description"),
        "og:title": m(property="og:title"),
        "og:image": m(property="og:image"),
        "lang": (soup.html.get("lang") if soup.html else "") or "",
    }
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        meta["canonical"] = urljoin(base, canon["href"])
    return {k: v for k, v in meta.items() if v}

def collect_links(root, base, limit=40):
    seen, out = set(), []
    for a in root.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if not href.startswith("http") or href in seen:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())[:90]
        if not text:
            continue
        seen.add(href)
        out.append((text, href))
        if len(out) >= limit:
            break
    return out

def build(html, base, fields, strategy, main_picker, extra_stats=None):
    soup = BeautifulSoup(html, "html.parser")
    meta = collect_meta(soup, base)
    for t in soup(NOISE_TAGS):
        t.decompose()
    root = main_picker(soup) or soup.body or soup
    images = collect_images(root, base) if "images" in fields else []
    links = collect_links(root, base) if "links" in fields else []

    tables_md = []
    if "tables" in fields:
        for tbl in root.find_all("table")[:6]:
            rows = tbl.find_all("tr")
            if len(rows) < 2:
                continue
            md_tbl = clean_md(to_md(tbl), 4000)
            if md_tbl.count("|") >= 4:
                tables_md.append(md_tbl)

    for t in root.find_all(["table", "img", "figure", "picture"]):
        t.decompose()
    for t in root.find_all(CHROME_TAGS):
        t.decompose()
    text_md = clean_md(to_md(root)) if "text" in fields else ""

    title = meta.get("og:title") or meta.get("title") or base
    parts = [f"# {title}", f"> Source: <{base}> · extracted by `{strategy}`"]
    if meta.get("description"):
        parts.append(f"_{meta['description']}_")
    if text_md:
        parts.append("## Content\n\n" + text_md)
    if tables_md:
        parts.append("## Tables\n\n" + "\n\n".join(tables_md))
    if "links" in fields:
        block = ["## Links & Metadata", "", "| Key | Value |", "|---|---|"]
        block += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in meta.items()]
        block.append("")
        block += [f"- [{t}]({h})" for t, h in links]
        parts.append("\n".join(block))
    markdown = "\n\n".join(parts)

    words = len(re.findall(r"\w+", text_md))
    valid = (words >= 40) or bool(tables_md) or (len(images) >= 1 and words >= 10)
    stats = {"words": words, "tables": len(tables_md), "images": len(images),
             "links": len(links), "html_bytes": len(html)}
    stats.update(extra_stats or {})
    if not valid:
        stats["reason"] = "content validation failed (layout changed or empty render)"
    return {"valid": valid, "markdown": markdown, "images": images, "stats": stats}

def semantic_main(soup):
    for sel in ["main", "article", "[role=main]", "#content", "#main", ".content", "#mw-content-text"]:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    return None
'''

# ---------------------------------------------------------------------------
# Strategy bodies
# ---------------------------------------------------------------------------
BS4_FAST = r'''
def run(p):
    try:
        html, final_url = fetch(p["url"])
    except Exception as e:
        return fail(f"fetch failed: {type(e).__name__}: {e}")
    if p.get("simulate_break"):
        html = BROKEN_LAYOUT
    emit(build(html, final_url, p["fields"], "bs4-fast", semantic_main))
'''

PLAYWRIGHT_DOM = r'''
def run(p):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return fail("playwright not installed in this sandbox image (run setup_snapshot.py)")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
            try:
                page.goto(p["url"], wait_until="networkidle", timeout=8000)
            except Exception:
                page.goto(p["url"], wait_until="domcontentloaded", timeout=8000)
            # Scroll to trigger lazy-loaded tables and images.
            for _ in range(4):
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(150)
            html, final_url = page.content(), page.url
            browser.close()
    except Exception as e:
        return fail(f"browser render failed: {type(e).__name__}: {str(e)[:160]}")
    if p.get("simulate_break"):
        html = BROKEN_LAYOUT
    emit(build(html, final_url, p["fields"], "playwright-dom", semantic_main, {"rendered": True}))
'''

AI_HEURISTIC = r'''
def density_main(soup):
    """Selector-free main-content detection: score blocks by paragraph text vs link density.
    Survives class/id renames because it never looks at CSS selectors."""
    best, best_score = None, 0.0
    for node in soup.find_all(["article", "main", "section", "div", "td"]):
        text = node.get_text(" ", strip=True)
        if len(text) < 200:
            continue
        link_text = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        link_density = link_text / max(len(text), 1)
        paras = len(node.find_all(["p", "li", "tr", "h2", "h3"]))
        depth_penalty = 1.0 / (1 + 0.04 * len(list(node.parents)))
        score = (len(text) ** 0.5) * (1 - link_density) ** 2 * (1 + paras ** 0.5) * (2 - depth_penalty)
        if score > best_score:
            best, best_score = node, score
    return best

def jsonld(soup):
    out = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            out.append(json.loads(s.string or ""))
        except Exception:
            pass
    return out

def run(p):
    try:
        html, final_url = fetch(p["url"])
    except Exception as e:
        return fail(f"fetch failed: {type(e).__name__}: {e}")
    if p.get("simulate_break"):
        html = BROKEN_LAYOUT
    raw = BeautifulSoup(html, "html.parser")
    ld = jsonld(raw)
    has_next = bool(raw.find("script", id="__NEXT_DATA__"))
    res = build(html, final_url, p["fields"], "ai-heuristic", density_main,
                {"jsonld_blocks": len(ld), "next_data": has_next})
    # Structured data recovery: JSON-LD often survives redesigns untouched.
    if ld and p["fields"]:
        snippet = json.dumps(ld[0], ensure_ascii=False, indent=2)[:1500]
        res["markdown"] += "\n\n## Structured Data (JSON-LD)\n\n```json\n" + snippet + "\n```"
        if not res["valid"] and len(snippet) > 200:
            res["valid"] = True
            res["stats"].pop("reason", None)
    emit(res)
'''

_BODIES = {"bs4-fast": BS4_FAST, "playwright-dom": PLAYWRIGHT_DOM, "ai-heuristic": AI_HEURISTIC}


def build_script(strategy: str, url: str, fields: list[str], simulate_break: bool = False) -> str:
    """Return the full Python program to execute inside a sandbox for one strategy."""
    params = json.dumps({"url": url, "fields": list(fields), "simulate_break": bool(simulate_break)})
    return (
        PRELUDE
        + _BODIES[strategy]
        + "\ntry:\n"
        + f"    run(json.loads({params!r}))\n"
        + "except Exception as _e:\n"
        + "    fail(f'unhandled: {type(_e).__name__}: {_e}')\n"
    )


def parse_output(stdout: str) -> dict:
    """Pull the result JSON out of sandbox stdout (pip noise etc. is ignored)."""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(line[len(RESULT_MARKER):])
            except json.JSONDecodeError:
                break
    tail = (stdout or "").strip()[-300:]
    return {"valid": False, "markdown": "", "images": [], "stats": {"reason": f"no result emitted: {tail}"}}
