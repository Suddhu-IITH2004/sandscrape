# SandScrape — Daytona × Nosana

Parallel, self-healing web extraction. Three strategies race in three Daytona sandboxes; the first
valid result wins, the rest are torn down, and images are described by a VLM on Nosana GPUs.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in DAYTONA_API_KEY and NOSANA_VLM_ENDPOINT
python setup_snapshot.py      # one time: prebuilds the sandbox image (deps + Chromium)
uvicorn main:app --port 8000  # open http://localhost:8000
```

No keys yet? Leave `SANDSCRAPE_MODE=auto` and it runs the same sandbox programs as local
subprocesses. The top bar shows **LOCAL REHEARSAL** so nobody mistakes it for the live demo.

## API

| Route | Purpose |
|---|---|
| `GET /api/stream?url=&fields=text,tables,images,links&prompt=` | Start a run; SSE telemetry |
| `GET /api/v1/endpoints/{id}?format=json\|markdown` | Generated self-healing endpoint |
| `GET /api/v1/endpoints/{id}?simulate_break=true` | Same, with a simulated site redesign |
| `GET /api/heal/stream/{id}?simulate_break=true` | Self-heal with live telemetry (UI button) |
| `GET /api/health` | Mode + configured VLM providers |

## Before the demo (checklist)

1. **Build the snapshot.** Without it, sandboxes pip-install mid-race and `playwright-dom`
   fails because there is no Chromium.
2. **Check sandbox network access.** Lower Daytona tiers restrict outbound internet. Run each
   demo preset once with real keys.
3. **Warm the Nosana node.** Send one request a few minutes before you present. If it's cold,
   the circuit breaker routes to OpenAI or Gemini. Those insights are labelled
   "VLM Insight — OpenAI (fallback)", not "Nosana".
4. **Rehearse "Simulate Site Breakage".** It feeds the cached strategy an empty redesigned
   page. Validation fails, the other strategies re-race, and the endpoint switches strategy.

## Files

- `strategies.py`: extraction programs that run inside the sandboxes (bs4-fast, playwright-dom, ai-heuristic)
- `orchestrator.py`: Daytona race, teardown, self-heal registry, and a local executor for rehearsal
- `nosana_vlm.py`: Nosana VLM client with a fallback chain
- `main.py`: FastAPI server with SSE and static UI hosting
- `static/index.html`: Live Engine and Architecture & Pitch views
