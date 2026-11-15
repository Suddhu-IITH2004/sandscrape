# SandScrape

Turn public web pages into Markdown through a bounded parallel strategy race. SandScrape uses Daytona sandboxes when configured, supports a local development backend, streams run telemetry over SSE, and can send selected images to a Nosana-hosted Ollama vision model.

## Features

- Race `bs4-fast`, `playwright-dom`, and `ai-heuristic` extraction strategies for each URL.
- Process batches of up to 10 public URLs with a shared pool capped at 10 active workers.
- Stream sandbox, winner, and Nosana status events to the browser.
- Convert extracted content and tables to Markdown; filter SVG and known tiny images.
- Analyze up to three raster images per URL with Ollama `/api/chat` when Nosana is configured.
- Re-run an extraction race through the self-heal API when a page changes.
- Reject private/local hosts and re-check redirect targets to reduce SSRF risk.

## Requirements

- Python 3.11+
- Daytona API key for isolated sandbox mode
- Optional Nosana Ollama endpoint for image understanding

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The workspace includes a placeholder-only `.env`. Put your own credentials in it; do not commit it. The app runs in local mode when Daytona credentials are absent.

```env
DAYTONA_API_KEY=
DAYTONA_API_URL=https://app.daytona.io/api
DAYTONA_TARGET=
NOSANA_OLLAMA_URL=
NOSANA_MODEL=qwen3.5:9b
SCRAPE_BACKEND=auto
MAX_CONCURRENT_SANDBOXES=10
```

`SCRAPE_BACKEND=auto` uses Daytona if `DAYTONA_API_KEY` is set and otherwise selects local mode. Use `SCRAPE_BACKEND=daytona` to require Daytona or `SCRAPE_BACKEND=local` to force local execution. In local mode, the Playwright strategy reports unavailable and the remaining strategies race. A Daytona sandbox must have Playwright and its browser installed for the browser strategy to run; failures are reported without blocking the other strategies.

Run the app:

```powershell
python -m uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). The default page is a public demo target; change it to any public website you are authorized to access.

### Nosana Ollama

Set `NOSANA_OLLAMA_URL` to the base URL of the deployed Ollama service, without `/api/chat`. The app requests up to three images per page, downloads and base64-encodes each image, then calls `/api/chat` with `NOSANA_MODEL` (default `qwen3.5:9b`). Requests and downloads are time-limited. Check readiness and model availability with:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/nosana/health
```

## API

### Start an extraction job

`POST /api/v1/extract` (returns `202 Accepted`):

```json
{
  "urls": ["https://example.com"],
  "fields": ["text", "tables", "images"],
  "concurrency": 10
}
```

The response contains a `job_id` and `events_url`. Connect to that URL using Server-Sent Events, then read the final result with `GET /api/v1/jobs/{job_id}`. `fields` accepts `text`, `tables`, `images`, and `links`; `urls` accepts 1–10 unique HTTP(S) URLs.

### Self-heal

`POST /api/v1/self-heal` accepts the same payload and starts a fresh strategy race. This provides a race-based recovery primitive for applications that detect a failed or changed page; it does not persist or deploy a per-site endpoint.

### Other endpoints

- `GET /api/v1/health` — backend and active pool status.
- `GET /api/v1/jobs/{job_id}` — job status and completed results.
- `GET /api/v1/jobs/{job_id}/events` — live SSE telemetry.
- `GET /api/v1/nosana/health` — Ollama `/api/tags` readiness and configured model availability.

## Development checks

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py orchestrator.py strategies.py nosana_vlm.py
```
