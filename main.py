"""FastAPI application for SandScrape."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from orchestrator import SandboxOrchestrator
from strategies import validate_public_url

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
EVENT_END = {"_sand_scrape_stream_end": True}


def _read_settings() -> dict[str, str | int]:
    backend = os.getenv("SCRAPE_BACKEND", "auto").strip().lower()
    if backend not in {"auto", "local", "daytona"}:
        raise RuntimeError("SCRAPE_BACKEND must be auto, local, or daytona.")
    try:
        max_sandboxes = int(os.getenv("MAX_CONCURRENT_SANDBOXES", "10"))
    except ValueError as exc:
        raise RuntimeError("MAX_CONCURRENT_SANDBOXES must be an integer from 1 to 10.") from exc
    if not 1 <= max_sandboxes <= 10:
        raise RuntimeError("MAX_CONCURRENT_SANDBOXES must be between 1 and 10.")
    api_key = os.getenv("DAYTONA_API_KEY", "").strip()
    if backend == "daytona" and not api_key:
        raise RuntimeError("SCRAPE_BACKEND=daytona requires DAYTONA_API_KEY.")
    selected_backend = "daytona" if backend == "daytona" or (backend == "auto" and api_key) else "local"
    return {
        "backend": selected_backend,
        "max_sandboxes": max_sandboxes,
        "nosana_url": os.getenv("NOSANA_OLLAMA_URL", "").strip().rstrip("/"),
        "nosana_model": os.getenv("NOSANA_MODEL", "qwen3.5:9b").strip() or "qwen3.5:9b",
    }


class ExtractRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    fields: list[Literal["text", "tables", "images", "links"]] = Field(
        default_factory=lambda: ["text", "tables", "images"]
    )
    concurrency: int = Field(default=10, ge=1, le=10)
    custom_prompt: str | None = Field(default=None, max_length=1000)

    @field_validator("urls")
    @classmethod
    def normalize_urls(cls, urls: list[str]) -> list[str]:
        normalized = [url.strip() for url in urls]
        if any(not url for url in normalized):
            raise ValueError("URLs cannot be blank.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("URLs must be unique.")
        return normalized

    @field_validator("fields")
    @classmethod
    def require_unique_fields(cls, fields: list[str]) -> list[str]:
        if len(set(fields)) != len(fields):
            raise ValueError("Extraction fields must be unique.")
        if not fields:
            raise ValueError("Select at least one extraction field.")
        return fields


async def _start_job(request: ExtractRequest, app: FastAPI) -> dict[str, str]:
    try:
        validated_urls = await asyncio.gather(
            *(validate_public_url(url) for url in request.urls)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = uuid.uuid4().hex
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    job: dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": time.time(),
        "result": None,
        "error": None,
        "queue": queue,
        "task": None,
    }
    app.state.jobs[job_id] = job

    async def execute() -> None:
        job["status"] = "running"
        try:
            result = await app.state.orchestrator.run(
                job_id=job_id,
                urls=validated_urls,
                fields=set(request.fields),
                concurrency=request.concurrency,
                queue=queue,
                custom_prompt=request.custom_prompt,
            )
            job["result"] = result
            job["status"] = "completed"
            await queue.put({"type": "job_complete", "job_id": job_id, "result": result})
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            await queue.put({"type": "job_cancelled", "job_id": job_id})
            raise
        except Exception as exc:
            logger.exception("Extraction job %s failed.", job_id)
            job["status"] = "failed"
            job["error"] = str(exc)
            await queue.put({"type": "job_failed", "job_id": job_id, "error": str(exc)})
        finally:
            await queue.put(EVENT_END)

    task = asyncio.create_task(execute(), name=f"extract:{job_id}")
    job["task"] = task
    _prune_jobs(app)
    return {"job_id": job_id, "status": "queued", "events_url": f"/api/v1/jobs/{job_id}/events"}


def _prune_jobs(app: FastAPI) -> None:
    jobs = app.state.jobs
    completed = [
        (job_id, job)
        for job_id, job in jobs.items()
        if job["status"] in {"completed", "failed", "cancelled"}
    ]
    for job_id, _ in sorted(completed, key=lambda item: item[1]["created_at"])[:-100]:
        jobs.pop(job_id, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _read_settings()
    daytona = None
    if settings["backend"] == "daytona":
        try:
            from daytona import AsyncDaytona
        except ImportError as exc:
            raise RuntimeError("Install the Daytona SDK with `pip install daytona`.") from exc
        daytona = AsyncDaytona()
    app.state.settings = settings
    app.state.jobs = {}
    app.state.daytona = daytona
    app.state.orchestrator = SandboxOrchestrator(
        backend=str(settings["backend"]),
        daytona=daytona,
        max_sandboxes=int(settings["max_sandboxes"]),
        nosana_url=str(settings["nosana_url"]),
        nosana_model=str(settings["nosana_model"]),
    )
    try:
        yield
    finally:
        for job in app.state.jobs.values():
            task = job.get("task")
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        pending = [
            job["task"] for job in app.state.jobs.values()
            if isinstance(job.get("task"), asyncio.Task)
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if daytona is not None:
            await daytona.close()


app = FastAPI(
    title="SandScrape",
    description="Parallel, self-healing web extraction with Daytona and Nosana.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/v1/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "backend": settings["backend"],
        "active_sandboxes": request.app.state.orchestrator.active_sandboxes,
        "max_concurrent_sandboxes": settings["max_sandboxes"],
        "nosana_configured": bool(settings["nosana_url"]),
    }


@app.post("/api/v1/extract", status_code=202)
async def start_extraction(payload: ExtractRequest, request: Request) -> dict[str, str]:
    return await _start_job(payload, request.app)


@app.post("/api/v1/self-heal", status_code=202)
async def self_heal(payload: ExtractRequest, request: Request) -> dict[str, str]:
    """Re-run the strategy race for a target whose page structure may have changed."""
    return await _start_job(payload, request.app)


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, object]:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    }


@app.get("/api/v1/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    queue: asyncio.Queue[dict[str, object]] = job["queue"]

    async def stream():
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is EVENT_END:
                break
            data = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/nosana/health")
async def nosana_health(request: Request) -> dict[str, object]:
    base_url = request.app.state.settings["nosana_url"]
    if not base_url:
        raise HTTPException(status_code=503, detail="NOSANA_OLLAMA_URL is not configured.")
    try:
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                response = await client.get(urljoin(str(base_url).rstrip("/") + "/", "api/tags"))
                response.raise_for_status()
                payload = response.json()
    except (TimeoutError, httpx.HTTPError, ValueError) as exc:
        logger.warning("Nosana health check failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Nosana Ollama health check failed: {exc}") from exc
    model = str(request.app.state.settings["nosana_model"])
    models = payload.get("models", []) if isinstance(payload, dict) else []
    loaded = any(
        isinstance(item, dict)
        and (item.get("name") == model or item.get("model") == model)
        for item in models
    )
    return {"status": "ok", "model": model, "model_available": loaded}
