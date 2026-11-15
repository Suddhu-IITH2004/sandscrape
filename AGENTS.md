# AGENTS.md — SandScrape (Daytona + Nosana HackSprint Tokyo MVP)

## 1. Project Overview & Winning Thesis
**SandScrape** is a parallel, self-healing web extraction engine and instant API generator powered by **Daytona Sandboxes** and **Nosana Decentralized GPUs**.

- **Core Problem:** Traditional web scrapers break the moment a website updates its HTML layout, fail on dynamic JavaScript pages, and leave valuable visual information (charts, infographics, product photos) trapped inside raw `<img>` URLs that text-only LLMs cannot read.
- **Core Solution (Dual-Sponsor Architecture):**
  1. **Parallel Daytona Sandbox Race:** When a user enters a target URL and selects desired fields (`Text`, `Tables`, `Images`, `Links/Metadata`), SandScrape uses `asyncio` to spin up **3 isolated Daytona Sandboxes simultaneously** (~100ms startup), racing 3 extraction strategies in parallel:
     - **Sandbox Alpha (`bs4-fast`):** `httpx` + `BeautifulSoup4` + `markdownify` (ultra-fast static HTML extraction).
     - **Sandbox Beta (`playwright-dom`):** `Playwright` headless Chromium (renders SPAs, JS-heavy sites, lazy-loaded tables/images).
     - **Sandbox Gamma (`ai-heuristic`):** Structural DOM + network heuristic extractor resilient to layout shifts.
  2. **Nosana GPU Vision Pipeline (Image Understanding):** Whenever the **`Images`** field is selected, the winning Daytona sandbox extracts the top relevant images and dispatches them to a **Vision-Language Model (VLM)** deployed on **Nosana's decentralized GPU network** (e.g., `Qwen2-VL` / `LLaVA` via OpenAI-compatible inference endpoint). Nosana analyzes each image (reading charts, UI screenshots, product details, and infographics) and returns rich descriptive captions that SandScrape weaves directly into the final GitHub-Flavored Markdown.
  3. **Self-Healing Endpoint:** The fastest valid sandbox wins, kills the losing sandboxes immediately to save compute, displays the rendered Markdown, and provides a live self-healing REST endpoint that automatically re-races sandboxes if the target site ever changes.

---

## 2. Tech Stack & Architecture (Optimized for 75-Minute Build)
Keep the stack lightweight, zero-build-step on the frontend, and strictly asynchronous on the backend so it builds reliably in under 45 minutes.

- **Backend:** Python 3.11+, `FastAPI`, `uvicorn`, `asyncio`, `sse-starlette` (Server-Sent Events for live sandbox & Nosana GPU telemetry).
- **Sponsor 1 — Daytona (`daytona-sdk`):** Programmatic creation, parallel code execution, and automatic teardown of isolated cloud sandboxes.
- **Sponsor 2 — Nosana (`NOSANA_VLM_ENDPOINT` + `NOSANA_API_KEY`):** OpenAI-compatible HTTP client (`httpx` / `openai`) calling a VLM hosted on Nosana GPU compute to generate semantic descriptions from scraped image URLs/base64 payloads. *Safety Fallback:* Include a graceful fallback to any configured VLM (`OPENAI_API_KEY` / `GEMINI_API_KEY`) if the Nosana GPU node is cold-starting during the live 3-minute demo, while still logging the Nosana routing step cleanly.
- **Frontend:** Single-server static HTML5 + **Tailwind CSS (via CDN)** + **Alpine.js (via CDN)** + `marked.js` (Markdown renderer) + `lucide-icons`. *No React/Vite/npm build steps.*

---

## 3. UI/UX Design Specifications ("Modern Dark Minimalist")
- **Aesthetic:** Linear / Vercel / Raycast inspired dark theme.
  - Background: `#09090B` (Zinc 950)
  - Elevated Cards/Panels: `#121215` with `1px solid #27272A` subtle borders
  - Primary Accents:
    - `#10B981` (Emerald 500) — Daytona Sandbox execution & winner highlights
    - `#8B5CF6` (Violet 500) — Nosana GPU VLM inference badge & image insights
- **Navigation (Top Bar — 2 Instant Views, Zero Page Reload):**
  1. **`[ ⚡ Live Engine ]` (Main Workspace)**
  2. **`[ 🎯 Architecture & Pitch ]` (Built-in Judge Presentation)**

### View 1: Live Engine (Main Page)
1. **Hero Input Bar:**
   - Clean URL input (`https://...`) + 3 one-click demo presets (*Static Docs*, *Dynamic Pricing Table*, *Visual Product/Chart Page*) so the live demo has zero typo risk.
   - **Field Selection Pills (Multi-select toggles):**
     - `[x] Clean Text`
     - `[x] Structured Tables`
     - `[x] Images + Nosana VLM Analysis` *(Highlighted with a subtle Violet "GPU Vision" pill)*
     - `[ ] Links & Metadata`
   - Optional custom prompt input (e.g., *"Summarize pricing tiers and describe product images"*).
   - Primary CTA Button: **`Launch SandScrape`**
2. **Real-Time Telemetry Strip (Interactive Live Updates via SSE):**
   - **3 Daytona Sandbox Race Cards:**
     - **Sandbox #1 (`bs4-fast`)** — Live status (`SPINNING UP` → `SCRAPING` → `WINNER 🏆` / `TERMINATED`), live `ms` timer, Sandbox ID (`dtn-...`).
     - **Sandbox #2 (`playwright-dom`)** — Live status, `ms` timer, Sandbox ID.
     - **Sandbox #3 (`ai-heuristic`)** — Live status, `ms` timer, Sandbox ID.
   - **Nosana GPU Vision Card (Activates when `Images` is selected):**
     - Shows status (`WAITING FOR SANDBOX` → `NOSANA GPU VLM INFERENCE ⚡` → `3 IMAGES ANALYZED`), GPU model badge, and inference latency.
   - **Live Execution Console:** Streaming timestamps (`[118ms] Daytona: 3 sandboxes online`, `[410ms] Daytona: bs4-fast won race, terminating 2 sandboxes`, `[620ms] Nosana GPU: Generating VLM descriptions for 3 extracted images...`).
3. **Output Workspace:**
   - **Rendered Markdown Preview** vs. **Raw Markdown** toggle (with 1-click Copy & Download `.md`).
   - Extracted images render alongside a styled **"Nosana VLM Visual Insight"** callout box showing the AI-generated description of each image.
   - **Self-Healing Live API Card:** Shows ready-to-run `curl` endpoint + **"Simulate Site Breakage (Self-Heal)"** button for the live pitch.

### View 2: Architecture & Pitch Page (Replaces External Slide Deck)
Designed to be screen-shared directly during the 3-minute judging demo:
1. **The Pain Point:** Why traditional CSS/XPath scrapers break constantly and why raw image URLs leave multimodal data blind spots.
2. **Comparison Matrix:** `Traditional Scrapers` vs. `Single-Agent Scrapers` vs. `SandScrape (Daytona + Nosana)` across Speed, Layout Resilience, Visual Understanding, and Sandbox Isolation.
3. **Dual-Sponsor Architecture Flow:** Visual step-by-step diagram showing `User URL` → `3x Parallel Daytona Sandboxes (100ms)` → `Winner Validation & Teardown` → `Nosana GPU VLM Image Enrichment` → `Self-Healing Markdown & REST API`.
4. **Enterprise Use Cases:** Multimodal RAG pipelines, automated competitor catalog & visual monitoring, and autonomous AI agent web perception.

---

## 4. Directory Structure
```text
sandscrape/
├── AGENTS.md
├── requirements.txt
├── .env.example
├── main.py                  # FastAPI server, SSE telemetry stream, self-heal endpoint, static host
├── orchestrator.py          # Async Daytona parallel sandbox race + Nosana VLM pipeline trigger
├── strategies.py            # Self-contained extraction scripts executed inside Daytona sandboxes
├── nosana_vlm.py            # Async client sending scraped images to Nosana GPU VLM endpoint
└── static/
    └── index.html           # Complete dark-mode Tailwind + Alpine.js UI (Live Engine + Pitch Deck)
```

---

## 5. Step-by-Step Build Instructions for AI Coding Agent
1. **Initialize Dependencies (`requirements.txt`):**
   `fastapi`, `uvicorn[standard]`, `daytona-sdk`, `sse-starlette`, `pydantic`, `python-dotenv`, `httpx`, `beautifulsoup4`, `markdownify`, `openai`.
2. **Build `strategies.py`:**
   - Create self-contained Python scripts that run inside Daytona sandboxes.
   - Return structured JSON via stdout: `{"valid": bool, "markdown": str, "images": [{"url": str, "alt": str}], "stats": dict}`.
   - Filter out tiny tracking pixels/icons (`width < 80px` or `.svg` icons) so only meaningful content images are sent to Nosana GPU inference.
3. **Build `nosana_vlm.py`:**
   - Implement `analyze_images_with_nosana(images: list[dict], custom_prompt: str, event_queue)`:
     - Takes up to 3 primary images extracted by the winning Daytona sandbox.
     - Sends multimodal chat completion requests to `NOSANA_VLM_ENDPOINT` (using `NOSANA_API_KEY` and `NOSANA_VLM_MODEL`), asking for a concise, factual description of what is shown in the image (text, charts, products, UI).
     - Emits live SSE events (`nosana_started`, `nosana_image_done`, `nosana_completed`) so the UI updates in real time.
     - Formats the results into clean Markdown blocks under each image (`![alt](url)` + `> **Nosana VLM Insight:** ...`).
4. **Build `orchestrator.py`:**
   - Implement `run_sandscrape(url, fields, custom_prompt, event_queue)`:
     - Spins up 3 Daytona sandboxes concurrently via `asyncio`.
     - Uses `asyncio.as_completed` so the first valid sandbox wins immediately and deletes all 3 sandboxes in a `finally` block.
     - If `"images"` is in `fields` and images were found, calls `analyze_images_with_nosana()` and appends the enriched visual insights to the final Markdown.
5. **Build `main.py` & `static/index.html`:**
   - Connect SSE streaming (`/api/stream`) to Alpine.js reactive state so every sandbox state change and Nosana GPU inference step animates smoothly in real time.

---

## 6. Guardrails & Live Demo Reliability
- **Strict Timeouts:** Cap Daytona sandbox runs at `12s` and Nosana VLM image calls at `10s` (cap max images analyzed per run to `3` for sub-3-second demo speed).
- **Resource Hygiene:** Always delete Daytona sandboxes inside `try...finally` blocks immediately after the race concludes.
- **Zero-Crash Demo Protection:** Ensure clean error handling and fallback providers so a slow target URL or cold GPU never stalls the 3-minute live pitch.
