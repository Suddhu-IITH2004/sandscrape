# AGENTS.md — SandScrape (Daytona + Nosana HackSprint Tokyo MVP)

## 1. Project Overview & Winning Thesis
**SandScrape** is a massively parallel, self-healing web extraction engine and instant API generator powered by **Daytona Sandboxes** and **Nosana Decentralized GPUs**.

- **Core Problem:** Traditional web scrapers break the moment a site updates its HTML, choke when scraping multiple URLs sequentially, and leave rich visual data (charts, infographics, product photos) trapped inside raw `<img>` tags that text pipelines cannot read.
- **Core Solution (Dual-Sponsor Architecture):**
  1. **Elastic Daytona Sandbox Fan-Out (Up to 10 Concurrent Sandboxes):**
     - **Single-URL Race Mode (3 Parallel Sandboxes):** Races 3 extraction strategies (`bs4-fast`, `playwright-dom`, `ai-heuristic`) simultaneously in isolated Daytona sandboxes (~100ms startup). The fastest valid sandbox wins and terminates the others immediately.
     - **Multi-URL Batch Mode (Up to 10 Parallel Sandboxes):** Users can enter multiple URLs (up to 10 lines/URLs at once) or configure sandbox concurrency (`max_sandboxes = 10`, enforced via `asyncio.Semaphore(10)`). SandScrape spins up a dedicated Daytona sandbox pool across all target URLs simultaneously, racing/executing them in parallel and aggregating the results in real time.
  2. **Nosana GPU Vision Pipeline (`qwen3.5:9b` via Ollama):**
     - Whenever **`Images`** is selected, extracted images from the winning Daytona sandboxes are downloaded and sent as base64 payloads to our **Ollama (`qwen3.5:9b`)** VLM server running on **Nosana Decentralized GPU Compute** (port `11434`).
     - Nosana's GPU VLM analyzes charts, product labels, and UI screenshots, injecting structured visual descriptions directly into the final GitHub-Flavored Markdown.
  3. **Self-Healing Endpoint:** Exposes a live REST API (`/api/v1/extract`) that automatically re-triggers the parallel Daytona sandbox race if a target site ever changes its DOM structure.

---

## 2. Nosana GPU Deployment Specification
Our VLM inference server is deployed on **Nosana** using Ollama (`docker.io/ollama/ollama:0.35.1`) exposing port `11434` (`GET /api/tags` health check, `9216 MB` VRAM requirement) with model `qwen3.5:9b`.

### Reference Nosana Job Definition
```json
{
  "version": "0.1",
  "ops": [
    {
      "type": "container/run",
      "id": "server",
      "args": {
        "image": "docker.io/ollama/ollama:0.35.1",
        "expose": [
          {
            "port": 11434,
            "health_checks": [
              {
                "type": "http",
                "path": "/api/tags",
                "method": "GET",
                "expected_status": 200,
                "continuous": false
              }
            ]
          }
        ],
        "gpu": true,
        "resources": [
          {
            "type": "Ollama",
            "model": "qwen3.5:9b"
          }
        ]
      }
    }
  ],
  "type": "container",
  "deployment_id": "EkQHsK7dE9pPi6Ue4gjVtbRX2ajbpqSButCXP2ZwbfRV",
  "meta": {
    "trigger": "deployment-manager",
    "system_requirements": {
      "vram_total_mb": 9216
    }
  },
  "global": {
    "variables": {
      "MODEL": "qwen3.5:9b"
    }
  }
}
```

### Environment Variables (`.env`)
```env
DAYTONA_API_KEY=your_daytona_api_key
NOSANA_OLLAMA_URL=https://<your-nosana-deployment-host>
NOSANA_MODEL=qwen3.5:9b
MAX_CONCURRENT_SANDBOXES=10
```

---

## 3. Tech Stack & Architecture (Optimized for 75-Minute Build)
- **Backend:** Python 3.11+, `FastAPI`, `uvicorn`, `asyncio`, `sse-starlette` (Server-Sent Events for live multi-sandbox telemetry).
- **Sponsor 1 — Daytona (`daytona-sdk`):** Programmatic creation, parallel execution, and immediate cleanup of up to **10 concurrent isolated sandboxes** bounded by `asyncio.Semaphore(10)`.
- **Sponsor 2 — Nosana (`httpx` -> Ollama `/api/chat`):** Async client calling `NOSANA_OLLAMA_URL/api/chat` with `model: "qwen3.5:9b"`, `stream: false`, and `images: ["<base64_encoded_image>"]`. *(Important: Ollama requires base64 image strings rather than raw URLs—download image bytes via `httpx` and base64-encode before sending to Nosana).*
- **Frontend:** Zero-build-step static HTML5 + **Tailwind CSS (CDN)** + **Alpine.js (CDN)** + `marked.js` + `lucide-icons`.

---

## 4. UI/UX Design Specifications ("Modern Dark Minimalist")
- **Aesthetic:** Linear / Vercel / Raycast inspired dark theme.
  - Background: `#09090B` (Zinc 950)
  - Elevated Cards/Panels: `#121215` with `1px solid #27272A` borders
  - Accents: `#10B981` (Emerald 500 — Daytona Sandboxes) & `#8B5CF6` (Violet 500 — Nosana `qwen3.5:9b` GPU VLM)
- **Top Navigation (2 Instant Views, Zero Reload):**
  1. **`[ ⚡ Live Engine ]`** (Main Multi-Sandbox Workspace)
  2. **`[ 🎯 Architecture & Pitch ]`** (Built-in Judge Presentation Deck)

### View 1: Live Engine (Main Page)
1. **Multi-URL & Concurrency Input Panel:**
   - Expandable URL input supporting **1 to 10 URLs** (one URL per line) + 1-click preset buttons:
     - *Single URL Race (3 Sandboxes)*
     - *Multi-Site Parallel Batch (3 Sites × 3 Sandboxes = 9 Sandboxes)*
   - **Sandbox Scale Slider / Badge:** `Active Pool Cap: Max 10 Daytona Sandboxes` (shows live active count `e.g. 9 / 10 Sandboxes Running`).
   - **Field Selection Pills:**
     - `[x] Clean Text`
     - `[x] Structured Tables`
     - `[x] Images + Nosana VLM (qwen3.5:9b)`
     - `[ ] Links & Metadata`
   - Primary CTA Button: **`Launch Parallel Sandboxes`**
2. **Real-Time Sandbox Fleet Grid (Live SSE Telemetry):**
   - Dynamically renders a live grid of up to **10 active Daytona Sandbox cards** grouped by target URL.
   - Each sandbox card shows: Sandbox ID (`dtn-...`), Target Domain, Strategy (`bs4-fast` / `playwright-dom` / `ai-heuristic`), Live Status (`SPINNING UP` → `SCRAPING` → `WINNER 🏆` / `TERMINATED`), and elapsed `ms` timer.
   - **Nosana GPU Vision Monitor Card:** Displays live `qwen3.5:9b` inference progress (`QUEUED` → `ANALYZING IMAGE 1/3 ON NOSANA GPU` → `COMPLETE`) and GPU latency.
   - **Live Telemetry Console:** Streaming real-time logs of sandbox spin-ups, race winners, teardowns, and Nosana VLM calls.
3. **Output Workspace:**
   - Per-URL tabs or unified **Rendered Markdown Preview** vs. **Raw Markdown** view (with 1-click Copy & Download `.md`).
   - Images render with a styled Violet **"Nosana `qwen3.5:9b` Visual Insight"** callout card directly beneath each image.
   - **Self-Healing API Card:** Live `curl` snippet + **"Simulate Site Breakage (Self-Heal)"** button.

### View 2: Architecture & Pitch Page (Replaces External Slide Deck)
Designed to present directly during the 3-minute live demo:
1. **The Pain Point:** Sequential scraping bottlenecks, brittle CSS selectors, and blind spots on visual web content.
2. **Comparison Matrix:** `Traditional Scrapers` vs. `Single-Agent Scrapers` vs. `SandScrape (10x Daytona Sandboxes + Nosana VLM)`.
3. **System Architecture Diagram:** Shows parallel fan-out across up to 10 Daytona sandboxes, instant teardown of losing sandboxes, and base64 image routing to Nosana's `qwen3.5:9b` GPU node.
4. **High-Impact Use Cases:** Multi-site competitor intelligence, multimodal RAG ingestion, and autonomous web agents.

---

## 5. Directory Structure
```text
sandscrape/
├── AGENTS.md
├── requirements.txt
├── .env.example
├── main.py                  # FastAPI server, SSE telemetry stream, self-heal endpoint, static host
├── orchestrator.py          # Async Daytona 10-sandbox pool manager (Semaphore(10)) & race engine
├── strategies.py            # Self-contained extraction scripts executed inside Daytona sandboxes
├── nosana_vlm.py            # Async image downloader + base64 Ollama (qwen3.5:9b) Nosana VLM client
└── static/
    └── index.html           # Complete dark-mode Tailwind + Alpine.js UI (Fleet Grid + Pitch Deck)
```

---

## 6. Step-by-Step Build Instructions for AI Coding Agent
1. **Create `requirements.txt`:**
   `fastapi`, `uvicorn[standard]`, `daytona-sdk`, `sse-starlette`, `pydantic`, `python-dotenv`, `httpx`, `beautifulsoup4`, `markdownify`.
2. **Build `strategies.py`:**
   - Self-contained Python scripts executed inside Daytona sandboxes.
   - Outputs JSON to stdout: `{"valid": bool, "markdown": str, "images": [{"url": str, "alt": str}], "stats": dict}`.
   - Filters out tiny icons/trackers (`< 80px` or `.svg`) so only meaningful images go to Nosana.
3. **Build `nosana_vlm.py`:**
   - Implement `analyze_images_with_nosana(images, custom_prompt, event_queue)`:
     - Downloads up to 3 top images per URL using `httpx.AsyncClient(timeout=8.0)` and encodes bytes to base64.
     - Sends POST request to `{NOSANA_OLLAMA_URL}/api/chat` with JSON payload:
       ```json
       {
         "model": "qwen3.5:9b",
         "messages": [
           {
             "role": "user",
             "content": "Describe this scraped web image concisely in 2-3 sentences for Markdown documentation. Extract any visible text, chart metrics, or product details.",
             "images": ["<base64_string>"]
           }
         ],
         "stream": false
       }
       ```
     - Emits live SSE updates (`nosana_started`, `nosana_done`) and appends formatted Markdown insights under each image.
4. **Build `orchestrator.py`:**
   - Enforce a global `semaphore = asyncio.Semaphore(10)` so total active Daytona sandboxes across all concurrent URLs never exceeds **10 sandboxes simultaneously**.
   - Support batching multiple URLs (`asyncio.gather` across URLs, where each URL races up to 3 strategies bounded by the 10-sandbox semaphore).
   - Always delete sandboxes inside `try...finally` blocks immediately after each task finishes.
5. **Build `main.py` & `static/index.html`:**
• Stream live events for all active sandboxes (up to 10 cards) and Nosana GPU vision tasks to the dark-mode UI.

7. Guardrails & Live Demo Reliability

• Hard 10-Sandbox Cap: Never exceed MAX_CONCURRENT_SANDBOXES=10 so Daytona quota and API rate limits remain rock-solid.
• Strict Timeouts: 12s max per Daytona sandbox run; 12s max per Nosana qwen3.5:9b Ollama vision request.
• Keep Nosana Warm: Ensure {NOSANA_OLLAMA_URL}/api/tags returns 200 OK before demo time so qwen3.5:9b is already loaded in VRAM.
