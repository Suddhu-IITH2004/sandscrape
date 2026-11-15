"""
nosana_vlm.py — sends images extracted by the winning Daytona sandbox to a Vision-Language
Model hosted on Nosana's decentralized GPU network (OpenAI-compatible endpoint, e.g. vLLM
serving Qwen2.5-VL). Falls back to OpenAI / Gemini if the Nosana node is cold, and labels
every insight with the provider that actually produced it.

`event_queue` is the run's telemetry queue (orchestrator.RunQueue): anything exposing
`async emit(event_type, **data)`.
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI

DEFAULT_PROMPT = (
    "Describe this image from a web page in 2-4 concise, factual sentences. "
    "Transcribe any important visible text or numbers. If it is a chart, state the chart type, "
    "axes and the main trend. If it is a product, name it and its key visible attributes. "
    "If it is a UI screenshot, say what the screen does. Do not speculate beyond what is visible."
)

MAX_IMAGE_BYTES = 4 * 1024 * 1024


@dataclass
class Provider:
    key: str          # "nosana" | "openai" | "gemini"
    label: str        # shown in the UI
    base_url: str | None
    api_key: str
    model: str


def configured_providers() -> list[Provider]:
    """Nosana first; fallbacks only if their keys are present."""
    out: list[Provider] = []
    endpoint = os.getenv("NOSANA_VLM_ENDPOINT", "").strip()
    if endpoint and "YOUR-DEPLOYMENT" not in endpoint:
        out.append(Provider("nosana", "Nosana GPU", endpoint.rstrip("/"),
                            os.getenv("NOSANA_API_KEY", "") or "nosana",
                            os.getenv("NOSANA_VLM_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")))
    if os.getenv("OPENAI_API_KEY"):
        out.append(Provider("openai", "OpenAI (fallback)", None, os.environ["OPENAI_API_KEY"],
                            os.getenv("OPENAI_VLM_MODEL", "gpt-4o-mini")))
    if os.getenv("GEMINI_API_KEY"):
        out.append(Provider("gemini", "Gemini (fallback)",
                            "https://generativelanguage.googleapis.com/v1beta/openai/",
                            os.environ["GEMINI_API_KEY"],
                            os.getenv("GEMINI_VLM_MODEL", "gemini-2.5-flash")))
    return out


def gpu_label() -> str:
    return os.getenv("NOSANA_GPU_LABEL", "Nosana GPU node")


async def _to_data_url(client: httpx.AsyncClient, url: str) -> str:
    """Download the image host-side so the VLM never has to hotlink (many sites block it).
    Falls back to the raw URL if the download fails."""
    try:
        r = await client.get(url, timeout=4.0, follow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
            return url
        return f"data:{ctype};base64,{base64.b64encode(r.content).decode()}"
    except Exception:
        return url


async def _ask(provider: Provider, image_ref: str, prompt: str, timeout: float) -> str:
    client = AsyncOpenAI(base_url=provider.base_url, api_key=provider.api_key,
                         timeout=timeout, max_retries=0)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=provider.model,
                max_tokens=220,
                temperature=0.2,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_ref}},
                    ],
                }],
            ),
            timeout=timeout,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("empty completion")
        return " ".join(text.split())
    finally:
        await client.close()


async def analyze_images_with_nosana(images: list[dict], custom_prompt: str, event_queue) -> list[dict]:
    """Analyze up to MAX_VLM_IMAGES images. Returns one insight dict per analyzed image:
    {url, alt, insight, provider, provider_label, model, ms, ok}."""
    max_images = int(os.getenv("MAX_VLM_IMAGES", "3"))
    timeout = float(os.getenv("VLM_TIMEOUT_S", "10"))
    targets = images[:max_images]
    providers = configured_providers()
    prompt = DEFAULT_PROMPT + (f"\nThe user's goal: {custom_prompt.strip()}" if custom_prompt.strip() else "")
    primary = providers[0] if providers else None
    t_start = time.perf_counter()

    await event_queue.emit(
        "nosana_started", count=len(targets),
        provider=primary.key if primary else "none",
        provider_label=primary.label if primary else "No VLM configured",
        model=primary.model if primary else "-", gpu=gpu_label(),
    )
    await event_queue.emit("log", msg=(
        f"Nosana GPU: generating VLM descriptions for {len(targets)} extracted image(s) "
        f"via {primary.model if primary else 'n/a'}"
    ))

    nosana_down = asyncio.Event()  # circuit breaker: skip Nosana for this run once it fails

    async with httpx.AsyncClient(headers={"User-Agent": "SandScrape/1.0"}) as http:
        async def one(idx: int, img: dict) -> dict:
            t0 = time.perf_counter()
            ref = await _to_data_url(http, img["url"])
            last_err = "no VLM provider configured"
            for prov in providers:
                if prov.key == "nosana" and nosana_down.is_set():
                    continue
                try:
                    text = await _ask(prov, ref, prompt, timeout)
                    ms = int((time.perf_counter() - t0) * 1000)
                    item = {"url": img["url"], "alt": img.get("alt", ""), "insight": text,
                            "provider": prov.key, "provider_label": prov.label,
                            "model": prov.model, "ms": ms, "ok": True}
                    await event_queue.emit("nosana_image_done", index=idx, **item)
                    return item
                except Exception as e:  # noqa: BLE001 — any provider failure means "try next"
                    last_err = f"{type(e).__name__}: {str(e)[:120]}"
                    if prov.key == "nosana":
                        nosana_down.set()
                    await event_queue.emit("log", msg=f"VLM {prov.label} failed on image {idx + 1} ({last_err}); routing to next provider")
            ms = int((time.perf_counter() - t0) * 1000)
            item = {"url": img["url"], "alt": img.get("alt", ""),
                    "insight": f"VLM analysis unavailable ({last_err}). Alt text: {img.get('alt') or 'none'}",
                    "provider": "none", "provider_label": "Unavailable", "model": "-", "ms": ms, "ok": False}
            await event_queue.emit("nosana_image_done", index=idx, **item)
            return item

        results = await asyncio.gather(*(one(i, im) for i, im in enumerate(targets)))

    total_ms = int((time.perf_counter() - t_start) * 1000)
    used = sorted({r["provider_label"] for r in results if r["ok"]}) or ["none"]
    await event_queue.emit("nosana_completed", count=sum(r["ok"] for r in results),
                           total=len(results), ms=total_ms, providers=used)
    return list(results)


def format_images_markdown(images: list[dict], insights: list[dict], extra_limit: int = 6) -> str:
    """`![alt](url)` + an insight blockquote under each analyzed image; remaining images listed plainly."""
    if not images:
        return ""
    by_url = {i["url"]: i for i in insights}
    blocks = ["## Images"]
    for img in images[:extra_limit]:
        alt = (img.get("alt") or "image").replace("]", ")").replace("[", "(")
        blocks.append(f"![{alt}]({img['url']})")
        ins = by_url.get(img["url"])
        if ins:
            heading = "Nosana VLM Insight" if ins["provider"] == "nosana" else f"VLM Insight — {ins['provider_label']}"
            blocks.append(f"> **{heading}:** {ins['insight']}\n>\n> <sub>{ins['model']} · {ins['ms']} ms</sub>")
    return "\n\n".join(blocks)
