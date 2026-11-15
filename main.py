"""
main.py — FastAPI server: SSE telemetry stream, self-healing REST endpoint, static UI host.

Run:  uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from sse_starlette.sse import EventSourceResponse  # noqa: E402

import orchestrator  # noqa: E402
from nosana_vlm import configured_providers, gpu_label  # noqa: E402

STATIC = Path(__file__).parent / "static"
VALID_FIELDS = {"text", "tables", "images", "links"}

app = FastAPI(title="SandScrape", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _fields(raw: str) -> list[str]:
    f = [x.strip().lower() for x in raw.split(",") if x.strip()]
    f = [x for x in f if x in VALID_FIELDS]
    return f or ["text"]


def _check_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    return url


async def _sse(request: Request, q: orchestrator.RunQueue, job: asyncio.Task):
    """Drain the run queue to the browser until the run emits 'done'."""
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            yield {"event": "message", "data": json.dumps(ev, ensure_ascii=False)}
            if ev["type"] == "done":
                break
    finally:
        if not job.done() and await request.is_disconnected():
            job.cancel()  # client left: stop the run (sandbox teardown still happens in finally)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    provs = configured_providers()
    return {
        "mode": orchestrator.execution_mode(),
        "daytona_configured": bool(os.getenv("DAYTONA_API_KEY")),
        "snapshot": os.getenv("DAYTONA_SNAPSHOT") or None,
        "vlm_providers": [{"key": p.key, "label": p.label, "model": p.model} for p in provs],
        "nosana_configured": any(p.key == "nosana" for p in provs),
        "gpu": gpu_label(),
    }


@app.get("/api/stream")
async def stream(request: Request, url: str, fields: str = "text,tables,images", prompt: str = ""):
    """Start a SandScrape run and stream live sandbox + Nosana telemetry as SSE."""
    q = orchestrator.RunQueue()
    job = asyncio.create_task(orchestrator.run_sandscrape(_check_url(url), _fields(fields), prompt[:500], q))
    return EventSourceResponse(_sse(request, q, job))


@app.get("/api/heal/stream/{api_id}")
async def heal_stream(request: Request, api_id: str, simulate_break: bool = False):
    """Call the self-healing endpoint with live telemetry (used by the 'Simulate Site Breakage' button)."""
    if api_id not in orchestrator.ENDPOINTS:
        raise HTTPException(404, "unknown endpoint")
    q = orchestrator.RunQueue()
    job = asyncio.create_task(orchestrator.serve_endpoint(api_id, q, simulate_break))
    return EventSourceResponse(_sse(request, q, job))


@app.get("/api/v1/endpoints/{api_id}")
async def endpoint(api_id: str, simulate_break: bool = False,
                   format: str = Query("json", pattern="^(json|markdown)$")):
    """The generated live REST API. Re-runs the cached strategy; re-races if the site changed."""
    if api_id not in orchestrator.ENDPOINTS:
        raise HTTPException(404, "unknown endpoint")
    q = orchestrator.RunQueue()
    result = await orchestrator.serve_endpoint(api_id, q, simulate_break)
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "extraction failed on all strategies")
    if format == "markdown":
        return PlainTextResponse(result["markdown"], media_type="text/markdown")
    return {k: result.get(k) for k in ("api_id", "winner", "healed", "heals", "total_ms", "markdown", "insights", "stats")}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
