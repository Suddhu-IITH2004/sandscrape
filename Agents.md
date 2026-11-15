# AGENTS.md — SandScrape (Daytona HackSprint Tokyo MVP)

## 1. Project Overview & Winning Thesis
**sandScrape** is a parallel, self-healing web extraction engine and instant API generator powered by **Daytona Sandboxes**. 
- **Core Problem:** Traditional web scrapers are brittle. Hardcoded CSS/XPath selectors break the moment a site updates its DOM, and single-method scrapers fail on dynamic JavaScript pages or waste compute running heavy browsers on static HTML.
- **Core Solution:** When a user inputs a target URL and selects desired extraction fields (Text, Tables, Images, Links/Metadata, Custom Prompt), sandScrape uses `asyncio` to spin up **3 isolated Daytona Sandboxes simultaneously** (~100ms startup), racing 3 distinct extraction strategies in parallel:
  1. **Sandbox Alpha (Fast Static):** `httpx` + `BeautifulSoup4` + `markdownify` (ultra-fast, low compute).
  2. **Sandbox Beta (Dynamic DOM):** `Playwright` headless Chromium (renders SPAs, JS-heavy sites, lazy-loaded tables/images).
  3. **Sandbox Gamma (AI Heuristic / XHR):** Network/DOM structural extractor using LLM-guided extraction rules to survive layout changes.
- **Winning Output:** The fastest sandbox that passes schema validation wins, terminates the remaining sandboxes to save compute, renders clean **GitHub-Flavored Markdown** in the UI, and exposes a **Self-Healing Live Endpoint** (`/api/v1/extract?url=...`) that automatically re-runs the sandbox race if the target site ever changes its layout.

---

## 2. Tech Stack & Architecture (Optimized for 75-Minute Build)
Keep the stack lightweight, zero-build-step on the frontend, and strictly asynchronous on the backend so it builds reliably in under 45 minutes.

- **Backend:** Python 3.11+, `FastAPI`, `uvicorn`, `asyncio`, `sse-starlette` (Server-Sent Events for real-time terminal/status streaming).
- **Sponsor Integration:** `daytona-sdk` (Official Daytona Python SDK). *Fallback safety:* Include a clean toggleable `MOCK_SANDBOX=false` env flag only if API rate limits hit during live demo, defaulting to real Daytona cloud sandboxes.
- **LLM Integration:** `openai` or `google-genai` (used to generate/heal extraction logic and structure messy HTML into clean Markdown).
- **Frontend:** Single-server static HTML5 + **Tailwind CSS (via CDN)** + **Alpine.js (via CDN)** + `marked.js` (for live Markdown rendering) + `lucide-icons`. *Do NOT use React/Next.js/Vite build pipelines—zero npm build delays.*

---

## 3. UI/UX Design Specifications ("Modern Dark Minimalist")
- **Aesthetic:** Linear / Vercel / Raycast inspired dark mode.
  - Background: `#09090B` (Zinc 950)
  - Elevated Cards/Panels: `#121215` with `1px solid #27272A` subtle borders
  - Primary Accent: `#10B981` (Emerald 500 — signals live sandbox execution & success) and `#6366F1` (Indigo 500 — AI orchestration)
  - Typography: Inter (UI) & JetBrains Mono (logs, latency timers, Markdown raw view, API endpoints).
- **Navigation (Top Bar — 2 Views Only, Instant Tab Switch without reload):**
  1. **`[ ⚡ Live Engine ]` (Main Workspace)**
  2. **`[ 🎯 Architecture & Pitch ]` (Built-in Judge Deck)**

### View 1: Live Engine (Main Page)
1. **Hero Input Bar:**
   - Clean URL input field (`https://...`) with 3 one-click preset demo buttons (e.g., *Static Docs*, *Dynamic Table/Pricing*, *News/Media*) so the live demo never fails on a typo.
   - **Field Selection Pills (Multi-select toggles):**
     - `[x] Clean Text`
     - `[x] Structured Tables`
     - `[x] Images & Alt-Text`
     - `[ ] Links & Metadata`
     - Optional custom instruction input (e.g., *"Extract product names and prices"*).
   - Primary CTA Button: **`Launch Parallel Sandboxes`**
2. **Real-Time Sandbox Telemetry Strip (Interactive Live Updates via SSE):**
   - Displays 3 side-by-side live cards representing the 3 Daytona Sandboxes spinning up simultaneously:
     - **Sandbox #1 (`bs4-fast`)** — Status badge (`SPINNING UP` → `SCRAPING` → `WINNER 🏆` / `TERMINATED`), live millisecond timer, Sandbox ID (`dtn-...`).
     - **Sandbox #2 (`playwright-dom`)** — Status badge, live timer, Sandbox ID.
     - **Sandbox #3 (`ai-heuristic`)** — Status badge, live timer, Sandbox ID.
   - Streaming mini-console log showing real-time Daytona SDK lifecycle events (`[142ms] Sandbox dtn-8a9f ready`, `[310ms] Extracting tables...`, `[480ms] Validation passed — killing slower sandboxes`).
3. **Output Workspace (Split/Tabbed View):**
   - **Rendered Markdown Preview** (cleanly styled typography, tables, and image previews) vs. **Raw Markdown** (with 1-click Copy & Download `.md` button).
   - **Auto-Generated Self-Healing API Card:** Shows a live `curl` command (`curl -X POST http://localhost:8000/api/scrape -d ...`) and a **"Simulate Site Breakage (Self-Heal Demo)"** button that demonstrates how sandScrape detects a schema mismatch and re-races sandboxes automatically.

### View 2: Architecture & Pitch Page (Replaces Slide Deck)
Designed specifically to be presented on screen during the 3-minute judging pitch:
1. **The Problem (Why Traditional Scraping Fails):** Visual comparison showing brittle CSS selectors breaking on DOM changes vs. high cost of running headless browsers for every request.
2. **Comparison Matrix Table:** `Traditional Scrapers` vs. `Single-Agent Scrapers` vs. `sandScrape (Parallel Daytona Sandboxes)` across Speed, Resilience, Compute Cost, and Security Isolation.
3. **Why Daytona Makes This Possible:** Visual diagram explaining sub-second sandbox spin-up (`90-200ms`), isolated execution of AI-generated extraction scripts, and instant teardown of losing sandboxes.
4. **Real-World Use Cases:** LLM RAG ingestion pipelines, live competitor price monitoring, and autonomous AI agent web tooling.

---

## 4. Directory Structure
```text
sand-scrape/
├── AGENTS.md
├── requirements.txt
├── .env.example
├── main.py                  # FastAPI app, SSE streaming endpoint, static file server
├── orchestrator.py          # Async Daytona SDK parallel race controller & validator
├── strategies.py            # Python scripts injected & executed inside Daytona sandboxes
└── static/
    └── index.html           # Complete Tailwind + Alpine.js dark-mode UI (Engine + Pitch views)
```

---

## 5. Step-by-Step Build Instructions for AI Coding Agent
1. **Initialize Dependencies (`requirements.txt`):**
   `fastapi`, `uvicorn[standard]`, `daytona-sdk`, `sse-starlette`, `pydantic`, `python-dotenv`, `httpx`, `beautifulsoup4`, `markdownify`, `openai`.
2. **Build `strategies.py`:**
   - Write self-contained Python execution payloads that take `(url, selected_fields, custom_prompt)` and print structured JSON to stdout containing `markdown`, `metrics`, and `valid: bool`.
   - Ensure each strategy cleanly formats requested fields: headings/paragraphs for **Text**, GFM pipe tables (`| col |`) for **Tables**, and `![alt](src)` links for **Images**.
3. **Build `orchestrator.py`:**
   - Implement `run_parallel_scrape(url, fields, custom_prompt, event_queue)`:
     - Asynchronously create 3 Daytona sandboxes using `daytona.create()` via `asyncio.gather` / `asyncio.as_completed`.
     - Push real-time JSON events (`sandbox_created`, `strategy_started`, `sandbox_won`, `sandbox_killed`, `final_result`) into `event_queue` for SSE streaming.
     - Use `asyncio.as_completed` so the **first sandbox that returns valid non-empty Markdown** immediately wins, pushes the result to the frontend, and triggers cleanup (`sandbox.delete()`) for all 3 sandboxes.
4. **Build `main.py`:**
   - `GET /` -> Serves `static/index.html`.
   - `GET /api/stream` -> SSE endpoint streaming live sandbox telemetry and final Markdown output.
   - `POST /api/heal-test` -> Simulates a broken selector cache and triggers a fresh parallel race to prove self-healing.
5. **Build `static/index.html`:**
   - Implement the ultra-clean dark UI with both views (`Live Engine` and `Architecture & Pitch`), animated status pulses on the 3 sandbox cards, live millisecond counters, and clean Markdown rendering.

---

## 6. Guardrails & Hackathon Demo Reliability Rules
- **Strict Timeouts:** Cap every sandbox execution at `12 seconds` so a hung website never freezes the live demo.
- **Always Clean Up:** Wrap sandbox lifecycle in `try...finally` blocks calling `daytona.delete(sandbox)` so orphaned sandboxes don't exhaust hackathon credits.
- **Zero-Crash Fallback:** If a target site blocks bot User-Agents (403), Strategy 3 (AI Heuristic / Fallback fetcher) must gracefully return a clean structured summary rather than throwing an unhandled 500 error.
