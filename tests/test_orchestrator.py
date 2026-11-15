import asyncio
import unittest
from unittest.mock import patch

from orchestrator import SandboxOrchestrator
from strategies import Extraction


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_sandbox_cap_is_shared_across_urls(self):
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def fake_extract(url, strategy, fields):
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.02 if strategy == "bs4-fast" else 0.5)
                return Extraction(
                    valid=strategy == "bs4-fast",
                    markdown=f"# {url}",
                    stats={"strategy": strategy},
                )
            finally:
                async with lock:
                    active -= 1

        orchestrator = SandboxOrchestrator(
            backend="local",
            daytona=None,
            max_sandboxes=2,
            nosana_url="",
            nosana_model="qwen3.5:9b",
        )
        events = asyncio.Queue()

        with patch("orchestrator.extract_local", fake_extract):
            output = await orchestrator.run(
                job_id="test-job",
                urls=["https://one.example", "https://two.example", "https://three.example"],
                fields={"text"},
                concurrency=3,
                queue=events,
            )

        self.assertEqual(len(output["results"]), 3)
        self.assertLessEqual(max_active, 2)
        self.assertEqual(orchestrator.active_sandboxes, 0)
        event_types = []
        while not events.empty():
            event_types.append((await events.get())["type"])
        self.assertEqual(event_types.count("race_winner"), 3)
