import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from strategies import Extraction


class AppTests(unittest.TestCase):
    def test_health_ui_and_private_url_rejection(self):
        with patch.dict(
            os.environ,
            {
                "SCRAPE_BACKEND": "local",
                "MAX_CONCURRENT_SANDBOXES": "10",
                "DAYTONA_API_KEY": "",
                "NOSANA_OLLAMA_URL": "",
            },
        ):
            with TestClient(app) as client:
                health = client.get("/api/v1/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json()["backend"], "local")
                self.assertEqual(client.get("/").status_code, 200)
                rejected = client.post(
                    "/api/v1/extract",
                    json={"urls": ["http://127.0.0.1"]},
                )
                self.assertEqual(rejected.status_code, 422)

    def test_extraction_job_streams_events_and_returns_markdown(self):
        async def fake_extract(url, strategy, fields):
            return Extraction(
                valid=True,
                markdown="# Demo page\n\nExtracted test content.",
                stats={"strategy": strategy},
            )

        with patch.dict(
            os.environ,
            {
                "SCRAPE_BACKEND": "local",
                "MAX_CONCURRENT_SANDBOXES": "10",
                "DAYTONA_API_KEY": "",
                "NOSANA_OLLAMA_URL": "",
            },
        ):
            with patch("main.validate_public_url", new=AsyncMock(side_effect=lambda url: url)):
                with patch("orchestrator.extract_local", new=fake_extract):
                    with TestClient(app) as client:
                        started = client.post(
                            "/api/v1/extract",
                            json={"urls": ["https://example.com"], "fields": ["text"]},
                        )
                        self.assertEqual(started.status_code, 202)
                        events_url = started.json()["events_url"]
                        stream = client.get(events_url)
                        events = [
                            json.loads(line.removeprefix("data: "))
                            for line in stream.text.splitlines()
                            if line.startswith("data: ")
                        ]
                        self.assertIn("job_complete", [event["type"] for event in events])
                        result = client.get(
                            f"/api/v1/jobs/{started.json()['job_id']}"
                        ).json()
                        self.assertEqual(result["status"], "completed")
                        self.assertIn("Extracted test content", result["result"]["results"][0]["markdown"])


if __name__ == "__main__":
    unittest.main()
