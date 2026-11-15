"""
orchestrator.py — races 3 extraction strategies in 3 parallel Daytona sandboxes, keeps the first
valid result, tears every sandbox down, then (if Images was requested) enriches images with the
Nosana GPU VLM. Also holds the self-healing endpoint registry.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass, field

from nosana_vlm import analyze_images_with_nosana, configured_providers, format_images_markdown
from strategies import STRATEGY_ORDER, build_script, parse_output

try:  # current package name
    from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams, DaytonaConfig
except ImportError:  # pragma: no cover — legacy `daytona-sdk` package
    try:
        from daytona_sdk import AsyncDaytona, CreateSandboxFromSnapshotParams, DaytonaConfig  # type: ignore
    except ImportError:
        AsyncDaytona = None  # type: ignore

SANDBOX_TIMEOUT_S = int(os.getenv("SANDBOX_TIMEOUT_S", "12"))
_background: set[asyncio.Task] = set()  # keeps teardown tasks alive after the response returns


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
class RunQueue(asyncio.Queue):
    """SSE event queue that stamps every event with ms since the run started."""

    def __init__(self) -> None:
        super().__init__()
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)

    async def emit(self, event_type: str, **data) -> None:
        await self.put({"type": event_type, "t": self.ms(), **data})


def execution_mode() -> str:
    mode = os.getenv("SANDSCRAPE_MODE", "auto").lower()
    if mode == "auto":
        return "daytona" if (os.getenv("DAYTONA_API_KEY") and AsyncDaytona) else "local"
    return mode


# ---------------------------------------------------------------------------
# Executors: Daytona (real) and local subprocess (offline rehearsal)
# ---------------------------------------------------------------------------
class DaytonaExecutor:
    name = "daytona"

    def __init__(self) -> None:
        if AsyncDaytona is None:
            raise RuntimeError("daytona SDK not installed: pip install daytona")
        cfg = {"api_key": os.getenv("DAYTONA_API_KEY")}
        if os.getenv("DAYTONA_API_URL"):
            cfg["api_url"] = os.getenv("DAYTONA_API_URL")
        if os.getenv("DAYTONA_TARGET"):
            cfg["target"] = os.getenv("DAYTONA_TARGET")
        self.client = AsyncDaytona(DaytonaConfig(**cfg))
        self.snapshot = os.getenv("DAYTONA_SNAPSHOT") or None

    async def create(self, strategy: str, run_id: str):
        params = CreateSandboxFromSnapshotParams(
            language="python",
            snapshot=self.snapshot,
            labels={"app": "sandscrape", "run": run_id, "strategy": strategy},
            auto_stop_interval=5,      # safety net: idle sandboxes stop after 5 min
            auto_delete_interval=10,   # ...and are deleted shortly after, even if teardown is missed
        )
        sb = await self.client.create(params, timeout=30)
        return sb, sb.id

    async def run(self, sandbox, code: str, timeout: int) -> str:
        resp = await sandbox.process.code_run(code, timeout=timeout)
        return resp.result or ""

    async def delete(self, sandbox) -> None:
        await self.client.delete(sandbox, timeout=30)

    async def close(self) -> None:
        await self.client.close()


class LocalExecutor:
    """Runs the exact same sandbox programs as local subprocesses. For rehearsals without credentials."""
    name = "local"

    async def create(self, strategy: str, run_id: str):
        await asyncio.sleep(0.03)
        sid = f"local-{strategy[:4]}-{uuid.uuid4().hex[:6]}"
        return {"id": sid}, sid

    async def run(self, sandbox, code: str, timeout: int) -> str:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", code,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        sandbox["proc"] = proc
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            raise
        return out.decode(errors="replace")

    async def delete(self, sandbox) -> None:
        proc = sandbox.get("proc")
        if proc and proc.returncode is None:
            proc.kill()

    async def close(self) -> None:
        return None


def make_executor():
    return DaytonaExecutor() if execution_mode() == "daytona" else LocalExecutor()


# ---------------------------------------------------------------------------
# Race
# ---------------------------------------------------------------------------
@dataclass
class _Lane:
    strategy: str
    sandbox: object = None
    sandbox_id: str | None = None
    started: float = field(default_factory=time.perf_counter)

    def ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


async def _teardown(executor, lanes: list[_Lane], q: RunQueue, pending_creates: list[asyncio.Task]) -> None:
    # Sandboxes still being created when the race ended: wait for them, then delete.
    for t in pending_creates:
        try:
            sb, sid = await asyncio.wait_for(t, timeout=40)
            await executor.delete(sb)
        except Exception:
            pass
    results = await asyncio.gather(
        *(executor.delete(l.sandbox) for l in lanes if l.sandbox is not None), return_exceptions=True
    )
    n_ok = sum(1 for r in results if not isinstance(r, Exception))
    await q.emit("log", msg=f"{executor.name.capitalize()}: {n_ok}/{len(results)} sandbox(es) deleted")
    await q.emit("teardown", deleted=n_ok)
    await executor.close()


async def race(url: str, fields: list[str], q: RunQueue, strategies: list[str] | None = None,
               simulate_break: str | None = None) -> dict:
    """Run strategies in parallel sandboxes; first valid result wins. `simulate_break` names a
    strategy whose sandbox will see a 'redesigned' (empty) page, to demo self-healing."""
    strategies = strategies or STRATEGY_ORDER
    run_id = uuid.uuid4().hex[:8]
    executor = make_executor()
    lanes = {s: _Lane(s) for s in strategies}
    pending_creates: list[asyncio.Task] = []

    async def lane(s: str) -> tuple[str, dict]:
        try:
            return await _lane(s)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # sandbox create/exec error -> an invalid lane, never a crashed run
            return s, {"valid": False, "markdown": "", "images": [], "error": True,
                       "stats": {"reason": f"{type(e).__name__}: {str(e)[:160]}"},
                       "sandbox_id": lanes[s].sandbox_id, "ms": lanes[s].ms()}

    async def _lane(s: str) -> tuple[str, dict]:
        L = lanes[s]
        await q.emit("sandbox", strategy=s, status="SPINNING_UP", ms=0)
        create_task = asyncio.ensure_future(executor.create(s, run_id))
        try:
            L.sandbox, L.sandbox_id = await asyncio.shield(create_task)
        except asyncio.CancelledError:
            pending_creates.append(create_task)  # reaped in teardown
            raise
        await q.emit("sandbox", strategy=s, status="SCRAPING", sandbox_id=L.sandbox_id, ms=L.ms())
        code = build_script(s, url, fields, simulate_break=(simulate_break == s))
        try:
            out = await asyncio.wait_for(executor.run(L.sandbox, code, SANDBOX_TIMEOUT_S), SANDBOX_TIMEOUT_S + 2)
            res = parse_output(out)
        except asyncio.TimeoutError:
            res = {"valid": False, "markdown": "", "images": [], "stats": {"reason": f"timeout after {SANDBOX_TIMEOUT_S}s"}}
        res["sandbox_id"], res["ms"] = L.sandbox_id, L.ms()
        return s, res

    await q.emit("log", msg=f"{executor.name.capitalize()}: launching {len(strategies)} sandbox(es) in parallel (run {run_id})")
    tasks = {asyncio.ensure_future(lane(s)): s for s in strategies}
    winner: tuple[str, dict] | None = None
    failures: dict[str, dict] = {}
    online_logged = False
    try:
        for fut in asyncio.as_completed(list(tasks)):
            s, res = await fut
            if not online_logged:
                online_logged = True
                ids = [l.sandbox_id for l in lanes.values() if l.sandbox_id]
                await q.emit("log", msg=f"{executor.name.capitalize()}: {len(ids)} sandbox(es) online")
            if res.get("valid"):
                winner = (s, res)
                await q.emit("sandbox", strategy=s, status="WINNER", sandbox_id=res["sandbox_id"], ms=res["ms"], stats=res.get("stats", {}))
                losers = [n for t, n in tasks.items() if not t.done()]
                await q.emit("log", msg=f"{executor.name.capitalize()}: {s} won race in {res['ms']}ms, terminating {len(losers)} sandbox(es)")
                for t, n in tasks.items():
                    if not t.done():
                        t.cancel()
                        await q.emit("sandbox", strategy=n, status="TERMINATED", sandbox_id=lanes[n].sandbox_id, ms=lanes[n].ms())
                break
            failures[s] = res
            await q.emit("sandbox", strategy=s, status="FAILED" if res.get("error") else "INVALID",
                         sandbox_id=res.get("sandbox_id"), ms=res["ms"],
                         detail=res.get("stats", {}).get("reason", "invalid"))
            await q.emit("log", msg=f"{s}: rejected — {res.get('stats', {}).get('reason', 'invalid output')}")
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cleanup = asyncio.create_task(_teardown(executor, list(lanes.values()), q, pending_creates))
        _background.add(cleanup)
        cleanup.add_done_callback(_background.discard)

    if winner:
        s, res = winner
        return {"ok": True, "strategy": s, **res, "failures": failures}
    best = max(failures.values(), key=lambda r: len(r.get("markdown", "")), default={})
    return {"ok": False, "strategy": None, "markdown": best.get("markdown", ""), "images": [],
            "stats": {"reason": "all strategies failed"}, "failures": failures}


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
ENDPOINTS: dict[str, dict] = {}  # self-healing API registry: api_id -> config + last good strategy


async def _enrich_and_finish(url, fields, custom_prompt, q: RunQueue, raced: dict) -> dict:
    markdown = raced.get("markdown", "")
    insights: list[dict] = []
    images = raced.get("images") or []
    if raced.get("ok") and "images" in fields:
        if images:
            insights = await analyze_images_with_nosana(images, custom_prompt, q)
        else:
            await q.emit("nosana_skipped", reason="no content images found on page")
        img_md = format_images_markdown(images, insights)
        if img_md:
            markdown = markdown + "\n\n" + img_md
    return {"markdown": markdown, "images": images, "insights": insights,
            "winner": raced.get("strategy"), "stats": raced.get("stats", {}), "ok": raced.get("ok", False)}


async def run_sandscrape(url: str, fields: list[str], custom_prompt: str, event_queue: RunQueue) -> dict:
    q = event_queue
    try:
        await q.emit("run_started", url=url, fields=fields, mode=execution_mode(),
                     vlm=[p.label for p in configured_providers()])
        raced = await race(url, fields, q)
        result = await _enrich_and_finish(url, fields, custom_prompt, q, raced)
        if result["ok"]:
            api_id = uuid.uuid4().hex[:10]
            ENDPOINTS[api_id] = {"url": url, "fields": fields, "prompt": custom_prompt,
                                 "strategy": result["winner"], "heals": 0, "last": result}
            result["api_id"] = api_id
        else:
            await q.emit("error", msg="All 3 sandboxes failed validation. Try another preset or URL.")
        result["total_ms"] = q.ms()
        await q.emit("result", **result)
        return result
    except Exception as e:  # zero-crash demo protection
        await q.emit("error", msg=f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        await q.emit("done")


async def serve_endpoint(api_id: str, q: RunQueue, simulate_break: bool = False) -> dict:
    """Self-healing endpoint: try the cached winning strategy in a single sandbox; if it fails
    validation (site changed), re-race all strategies and update the cache."""
    cfg = ENDPOINTS.get(api_id)
    try:
        if not cfg:
            await q.emit("error", msg=f"unknown endpoint {api_id}")
            return {"ok": False, "error": "unknown endpoint"}
        cached = cfg["strategy"]
        await q.emit("log", msg=f"Endpoint {api_id}: replaying cached strategy {cached}"
                                + (" (simulated site redesign)" if simulate_break else ""))
        first = await race(cfg["url"], cfg["fields"], q, strategies=[cached],
                           simulate_break=cached if simulate_break else None)
        healed = False
        if not first["ok"]:
            healed = True
            await q.emit("self_heal", broken=cached, msg=f"{cached} broke — re-racing remaining strategies")
            others = [s for s in STRATEGY_ORDER if s != cached]
            # The redesign only affects the cached strategy's assumptions, so race the rest.
            first = await race(cfg["url"], cfg["fields"], q, strategies=others)
        result = await _enrich_and_finish(cfg["url"], cfg["fields"], cfg["prompt"], q, first)
        if result["ok"]:
            cfg["strategy"] = result["winner"]
            cfg["heals"] += int(healed)
            cfg["last"] = result
        result.update(api_id=api_id, healed=healed, heals=cfg["heals"], total_ms=q.ms())
        if healed and result["ok"]:
            await q.emit("log", msg=f"Self-heal complete: endpoint now served by {result['winner']}")
        await q.emit("result", **result)
        return result
    except Exception as e:
        await q.emit("error", msg=f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        await q.emit("done")
