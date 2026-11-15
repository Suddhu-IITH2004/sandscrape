"""Nosana-hosted Ollama vision client."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from strategies import ImageRef, validate_public_url

logger = logging.getLogger(__name__)
DEFAULT_PROMPT = (
    "Describe this scraped web image concisely in 2-3 sentences for Markdown "
    "documentation. Extract any visible text, chart metrics, or product details."
)
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class VisionInsight:
    url: str
    description: str
    latency_ms: int


async def _download_image(client: httpx.AsyncClient, image_url: str) -> tuple[bytes, str]:
    current_url = image_url
    for _ in range(6):
        await validate_public_url(current_url)
        async with client.stream("GET", current_url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("The image returned an invalid redirect.")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/") or content_type == "image/svg+xml":
                raise ValueError("Only raster images can be sent for visual analysis.")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_IMAGE_BYTES:
                    raise ValueError("The image exceeded the 8 MB size limit.")
            if not content:
                raise ValueError("The image response was empty.")
            return bytes(content), content_type
    raise ValueError("The image redirected too many times.")


async def analyze_images_with_nosana(
    images: list[ImageRef],
    custom_prompt: str | None,
    event_queue: asyncio.Queue[dict[str, object]],
    ollama_url: str,
    model: str,
) -> list[VisionInsight]:
    """Describe up to three page images through the configured Ollama endpoint."""
    insights: list[VisionInsight] = []
    selected = images[:3]
    if not selected:
        return insights
    prompt = (custom_prompt or "").strip()[:1000] or DEFAULT_PROMPT
    endpoint = urljoin(ollama_url.rstrip("/") + "/", "api/chat")
    client_timeout = httpx.Timeout(8.0, connect=4.0)

    async with httpx.AsyncClient(timeout=client_timeout, trust_env=False) as client:
        async def analyze_one(index: int, image: ImageRef) -> VisionInsight | None:
            started = time.monotonic()
            await event_queue.put({
                "type": "nosana_started",
                "image": image.url,
                "index": index,
                "total": len(selected),
                "model": model,
                "status": "ANALYZING",
            })
            try:
                async with asyncio.timeout(12):
                    image_bytes, content_type = await _download_image(client, image.url)
                    response = await client.post(
                        endpoint,
                        json={
                            "model": model,
                            "messages": [{
                                "role": "user",
                                "content": prompt,
                                "images": [base64.b64encode(image_bytes).decode("ascii")],
                            }],
                            "stream": False,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("The vision service returned an invalid response.")
                    message = payload.get("message", {})
                    if not isinstance(message, dict):
                        raise ValueError("The vision service returned an invalid message.")
                    raw_description = message.get("content", "")
                    if not isinstance(raw_description, str) or not raw_description.strip():
                        raise ValueError("The vision service returned an invalid description.")
                    description = raw_description.strip()
                    if not description:
                        raise ValueError("The vision service returned an empty description.")
                    elapsed = round((time.monotonic() - started) * 1000)
                    insight = VisionInsight(image.url, description, elapsed)
                    await event_queue.put({
                        "type": "nosana_done",
                        "image": image.url,
                        "index": index,
                        "total": len(selected),
                        "model": model,
                        "latency_ms": elapsed,
                        "content_type": content_type,
                        "status": "COMPLETE",
                    })
                    return insight
            except (TimeoutError, httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning("Nosana image analysis failed for %s: %s", image.url, exc)
                await event_queue.put({
                    "type": "nosana_error",
                    "image": image.url,
                    "index": index,
                    "total": len(selected),
                    "model": model,
                    "status": "ERROR",
                    "error": str(exc),
                })
                return None

        results = await asyncio.gather(
            *(analyze_one(index, image) for index, image in enumerate(selected, start=1))
        )
    return [result for result in results if result is not None]


def append_insights(markdown: str, insights: list[VisionInsight]) -> str:
    """Place each vision result directly after its corresponding Markdown image."""
    for insight in insights:
        escaped_url = insight.url.replace(")", r"\)")
        image_pattern = f"]({escaped_url})"
        marker = (
            f"]({escaped_url})\n\n"
            f"> **Nosana {insight.latency_ms} ms Visual Insight**\n"
            + "\n".join(f"> {line}" for line in insight.description.splitlines())
        )
        if image_pattern in markdown:
            markdown = markdown.replace(image_pattern, marker, 1)
        else:
            markdown += (
                f"\n\n![Image]({escaped_url})\n\n"
                f"> **Nosana {insight.latency_ms} ms Visual Insight**\n"
                + "\n".join(f"> {line}" for line in insight.description.splitlines())
            )
    return markdown
