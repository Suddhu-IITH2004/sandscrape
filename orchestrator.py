"""Bounded multi-URL strategy racing with Daytona and local execution modes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from nosana_vlm import analyze_images_with_nosana, append_insights
from strategies import (
    STRATEGIES,
    Extraction,
    ImageRef,
    build_daytona_script,
    extract_local,
)

logger = logging.getLogger(__name__)
SANDBOX_RUN_TIMEOUT_SECONDS = 12


class SandboxOrchestrator:
    def __init__(
        self,
        *,
        backend: str,
        daytona: Any | None,
        max_sandboxes: int,
        nosana_url: str,
        nosana_model: str,
    ) -> None:
        self.backend = backend
        self.daytona = daytona
        self.max_sandboxes = min(max_sandboxes, 10)
        self.nosana_url = nosana_url
        self.nosana_model = nosana_model
        self.sandbox_semaphore = asyncio.Semaphore(self.max_sandboxes)
        self.active_sandboxes = 0
        self._active_lock = asyncio.Lock()

    async def _emit(self, queue: asyncio.Queue[dict[str, object]], event: dict[str, object]) -> None:
        await queue.put(event)

    async def _run_one(
        self,
        *,
        url: str,
        strategy: str,
        fields: set[str],
        job_semaphore: asyncio.Semaphore,
        queue: asyncio.Queue[dict[str, object]],
    ) -> dict[str, Any]:
        local_id = f"{'dtn' if self.backend == 'daytona' else 'local'}-{uuid.uuid4().hex[:8]}"
        domain = urlsplit(url).hostname or url
        started = time.monotonic()
        sandbox: Any | None = None
        create_task: asyncio.Task[Any] | None = None
        job_acquired = False
        sandbox_acquired = False
        active = False
        was_cancelled = False
        result: Extraction | None = None
        error: str | None = None

        try:
            await job_semaphore.acquire()
            job_acquired = True
            await self.sandbox_semaphore.acquire()
            sandbox_acquired = True
            async with self._active_lock:
                self.active_sandboxes += 1
                active = True
                active_count = self.active_sandboxes
            await self._emit(queue, {
                "type": "sandbox_update",
                "sandbox_id": local_id,
                "domain": domain,
                "strategy": strategy,
                "status": "SPINNING UP",
                "elapsed_ms": 0,
                "active": active_count,
                "cap": self.max_sandboxes,
            })

            if self.backend == "daytona":
                if self.daytona is None:
                    raise RuntimeError("Daytona backend is enabled but the SDK client is unavailable.")
                create_task = asyncio.create_task(self.daytona.create(timeout=12))
                sandbox = await asyncio.wait_for(asyncio.shield(create_task), timeout=12)
                pending_id = local_id
                local_id = f"dtn-{sandbox.id}"
                await self._emit(queue, {
                    "type": "sandbox_update",
                    "sandbox_id": local_id,
                    "previous_id": pending_id,
                    "domain": domain,
                    "strategy": strategy,
                    "status": "SCRAPING",
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "active": self.active_sandboxes,
                    "cap": self.max_sandboxes,
                })
                script = build_daytona_script(url, strategy, fields)
                response = await asyncio.wait_for(
                    sandbox.process.code_run(script, timeout=SANDBOX_RUN_TIMEOUT_SECONDS),
                    timeout=SANDBOX_RUN_TIMEOUT_SECONDS + 1,
                )
                output = response.result
                if not output and getattr(response, "artifacts", None):
                    output = response.artifacts.stdout
                if getattr(response, "exit_code", 0) != 0:
                    raise RuntimeError(f"Daytona strategy exited with code {response.exit_code}.")
                payload = json.loads(output)
                result = Extraction(
                    valid=bool(payload.get("valid")),
                    markdown=str(payload.get("markdown", "")),
                    images=[
                        ImageRef(str(item["url"]), str(item.get("alt", "")))
                        for item in payload.get("images", [])
                        if isinstance(item, dict) and item.get("url")
                    ],
                    stats=payload.get("stats", {}),
                    error=payload.get("error"),
                )
            else:
                await self._emit(queue, {
                    "type": "sandbox_update",
                    "sandbox_id": local_id,
                    "domain": domain,
                    "strategy": strategy,
                    "status": "SCRAPING",
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "active": self.active_sandboxes,
                    "cap": self.max_sandboxes,
                })
                result = await asyncio.wait_for(
                    extract_local(url, strategy, fields),
                    timeout=SANDBOX_RUN_TIMEOUT_SECONDS,
                )
        except asyncio.CancelledError:
            was_cancelled = True
            if create_task is not None and not create_task.done():
                try:
                    sandbox = await asyncio.wait_for(asyncio.shield(create_task), timeout=12)
                except (TimeoutError, Exception):
                    logger.exception("Could not retrieve sandbox after a race was cancelled.")
            raise
        except Exception as exc:
            error = str(exc)
            logger.warning("%s strategy failed for %s: %s", strategy, url, exc)
        finally:
            if sandbox is None and create_task is not None and not create_task.done():
                try:
                    sandbox = await asyncio.wait_for(asyncio.shield(create_task), timeout=12)
                except asyncio.CancelledError:
                    if not create_task.done():
                        create_task.cancel()
                except Exception:
                    logger.exception("Daytona sandbox creation did not complete.")
            if sandbox is not None and self.backend == "daytona":
                try:
                    await asyncio.wait_for(self.daytona.delete(sandbox, timeout=5), timeout=6)
                except Exception as exc:
                    logger.exception("Failed to delete Daytona sandbox %s.", getattr(sandbox, "id", "unknown"))
                    await self._emit(queue, {
                        "type": "sandbox_cleanup_error",
                        "sandbox_id": f"dtn-{sandbox.id}",
                        "error": str(exc),
                    })
            if active:
                async with self._active_lock:
                    self.active_sandboxes -= 1
                    active_count = self.active_sandboxes
            else:
                active_count = self.active_sandboxes
            if sandbox_acquired:
                self.sandbox_semaphore.release()
            if job_acquired:
                job_semaphore.release()
            if was_cancelled and sandbox_acquired:
                await self._emit(queue, {
                    "type": "sandbox_update",
                    "sandbox_id": local_id,
                    "domain": domain,
                    "strategy": strategy,
                    "status": "TERMINATED",
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "active": active_count,
                    "cap": self.max_sandboxes,
                })

        elapsed = round((time.monotonic() - started) * 1000)
        output = {
            "sandbox_id": local_id,
            "domain": domain,
            "strategy": strategy,
            "elapsed_ms": elapsed,
            "valid": bool(result and result.valid),
            "markdown": result.markdown if result else "",
            "images": result.images if result else [],
            "stats": result.stats if result else {},
            "error": error or (result.error if result else "Strategy failed."),
        }
        await self._emit(queue, {
            "type": "sandbox_update",
            "sandbox_id": local_id,
            "domain": domain,
            "strategy": strategy,
            "status": "COMPLETE" if output["valid"] else "FAILED",
            "elapsed_ms": elapsed,
            "active": active_count,
            "cap": self.max_sandboxes,
        })
        return output

    async def _race_url(
        self,
        *,
        url: str,
        fields: set[str],
        concurrency: int,
        queue: asyncio.Queue[dict[str, object]],
        custom_prompt: str | None,
    ) -> dict[str, Any]:
        job_semaphore = asyncio.Semaphore(min(concurrency, self.max_sandboxes))
        tasks = [
            asyncio.create_task(
                self._run_one(
                    url=url,
                    strategy=strategy,
                    fields=fields,
                    job_semaphore=job_semaphore,
                    queue=queue,
                ),
                name=f"{strategy}:{url}",
            )
            for strategy in STRATEGIES
        ]
        winner: dict[str, Any] | None = None
        results: list[dict[str, Any]] = []
        try:
            for completed in asyncio.as_completed(tasks):
                try:
                    result = await completed
                    results.append(result)
                    if result["valid"]:
                        winner = result
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    results.append({"valid": False, "error": str(exc), "markdown": "", "images": []})
            if winner is not None:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._emit(queue, {
                    "type": "race_winner",
                    "url": url,
                    "strategy": winner["strategy"],
                    "sandbox_id": winner["sandbox_id"],
                    "elapsed_ms": winner["elapsed_ms"],
                })
            else:
                remaining = await asyncio.gather(*tasks, return_exceptions=True)
                for result in remaining:
                    if isinstance(result, dict):
                        results.append(result)
                winner = max(results, key=lambda item: len(item.get("markdown", "")), default=None)

            if not winner or not winner.get("valid"):
                errors = [item.get("error") for item in results if item.get("error")]
                raise RuntimeError("; ".join(dict.fromkeys(errors)) or "No strategy extracted usable content.")

            insights = []
            if "images" in fields and winner["images"]:
                if self.nosana_url:
                    insights = await analyze_images_with_nosana(
                        winner["images"], custom_prompt, queue, self.nosana_url, self.nosana_model
                    )
                    winner["markdown"] = append_insights(winner["markdown"], insights)
                else:
                    await self._emit(queue, {
                        "type": "nosana_status",
                        "status": "DISABLED",
                        "message": "Set NOSANA_OLLAMA_URL to enable image analysis.",
                    })
            elif "images" in fields:
                await self._emit(queue, {
                    "type": "nosana_status",
                    "status": "NO IMAGES",
                    "message": "No qualifying raster images were found.",
                })
            return {
                "url": url,
                "domain": urlsplit(url).hostname,
                "strategy": winner["strategy"],
                "elapsed_ms": winner["elapsed_ms"],
                "markdown": winner["markdown"],
                "stats": winner["stats"],
                "images": [asdict(image) for image in winner["images"]],
                "insights": [asdict(insight) for insight in insights],
            }
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def run(
        self,
        *,
        job_id: str,
        urls: list[str],
        fields: set[str],
        concurrency: int,
        queue: asyncio.Queue[dict[str, object]],
        custom_prompt: str | None = None,
    ) -> dict[str, Any]:
        await self._emit(queue, {
            "type": "job_started",
            "job_id": job_id,
            "urls": urls,
            "backend": self.backend,
            "cap": self.max_sandboxes,
        })
        results = await asyncio.gather(
            *(
                self._race_url(
                    url=url,
                    fields=fields,
                    concurrency=concurrency,
                    queue=queue,
                    custom_prompt=custom_prompt,
                )
                for url in urls
            ),
            return_exceptions=True,
        )
        outputs: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for url, result in zip(urls, results):
            if isinstance(result, BaseException):
                errors.append({"url": url, "error": str(result)})
                await self._emit(queue, {"type": "url_error", "url": url, "error": str(result)})
            else:
                outputs.append(result)
                await self._emit(queue, {"type": "url_complete", "url": url, "result": result})
        if errors and not outputs:
            raise RuntimeError("; ".join(f"{e['url']}: {e['error']}" for e in errors))
        return {
            "job_id": job_id,
            "backend": self.backend,
            "results": outputs,
            "errors": errors,
        }
