# V2 Pivot — Multi-Source Entity Research Aggregator

## What changed

The app stops being a "YouTube stats card" and becomes an **entity research
aggregator**: you paste several links for one entity (YouTube, Instagram,
Facebook, personal/brand websites), the system pulls the **actual content** (not
upload-date metadata), a **local vision model** describes the visual material, a
**local text model** synthesizes everything, and you get **one research document**
about that entity.

Decisions locked in from discussion:
- Local models run on **llama.cpp** (I install + set up). Vision analysis *and*
  final synthesis are both **local** — nothing leaves the PC.
- Extraction is **text-first, screenshots + vision as complement/fallback**.
- Social platforms (IG/FB) use **three collection methods in parallel**, merging
  whatever each returns (details in §4).

## Target pipeline (async job)

```
links[] ──▶ INTAKE (create job)
             │
             ▼
        COLLECTORS (per source, in parallel)
          ├─ WebsiteCollector   (Playwright: render → readability text + screenshots + images + key sub-pages)
          ├─ YouTubeCollector   (existing API: about text, video descriptions, CAPTIONS/transcripts, top comments)
          └─ SocialCollector    (IG/FB: 3 methods merged — §4)
             │  each emits a normalized SourceArtifact
             ▼
        VISION STAGE  (screenshots/images → llama.cpp vision server → text descriptions)
             │
             ▼
        SYNTHESIS STAGE (all text + vision notes → llama.cpp text server → dossier;
                         map-reduce if it exceeds context)
             │
             ▼
        DOCUMENT (Markdown + optional PDF) ──▶ downloadable, with per-source raw artifacts
```

## 1. Normalized artifact

Every collector returns the same shape so downstream stages are source-agnostic:

```
SourceArtifact = {
  url, platform, ok, method,           # provenance
  text_blocks: [ {label, text} ],      # extracted written content
  images:      [ {url, local_path} ],  # media worth vision analysis
  screenshots: [ local_path ],         # full-page captures
  vision_notes:[ {ref, description} ], # filled by the vision stage
  facts:       { … },                  # any structured bits (title, follower count if visible)
  errors:      [ … ]
}
```

## 2. Collectors

- **WebsiteCollector** — Playwright (Chromium), full render, then: main text via a
  readability pass; full-page screenshot; collect meaningful images; follow a
  small allowlist of internal links (about / bio / contact / team) up to a depth
  and page cap. This is the reliable, legitimate core.
- **YouTubeCollector** — reuse the V1 API layer, but reframed for *content*:
  channel description/branding text, each recent video's title+description,
  **caption/transcript text where available**, and top comments. The API is a
  better content source than screenshots here; keep it.
- **SocialCollector** — see §4.

## 3. Local model stack (llama.cpp)

- Runtime: prebuilt **`llama-server`** (CUDA build for Blackwell / recent release;
  I'll confirm exact VRAM with `nvidia-smi` and pick the offload split). OpenAI-
  compatible HTTP — clean to call from Python.
- **Vision model** (default, swappable): a 7B-class multimodal GGUF —
  Qwen2.5-VL-7B or MiniCPM-V-2.6, Q4_K_M (~5 GB) + its mmproj. Endpoint on one port.
- **Text synthesis model** (default, swappable): a 7–8B instruct GGUF
  (Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct), Q4_K_M (~5 GB). Endpoint on a
  second port.
- **VRAM plan:** if the card is 16 GB, both can be resident; if 8 GB, the text
  model stays resident and the vision model loads on demand (config flag
  `MODELS_SEQUENTIAL=true`). Endpoints/models all live in `.env`.
- A thin `llm_client.py` wraps both (chat + image-attachment calls) with timeouts
  and retries; everything else talks to that, so models are swappable without code
  changes.

## 4. Social: three methods, merged ("use all 3")

For IG/FB (and extensible to others), run these and merge by provenance/confidence:

1. **Logged-out Playwright** — render the public URL, extract whatever DOM/text is
   visible, full-page screenshot. No account, no ban risk.
2. **Unofficial library / public endpoint** — IG via instaloader (anonymous
   profile fields); FB via `mbasic`/public-page routes and oEmbed where available.
   Best-effort, wrapped in try/except.
3. **Screenshot → vision extraction** — feed method-1's screenshot (and any
   image-only cards) to the vision model to recover text the DOM didn't expose and
   to describe visual content.

Merge step dedupes overlapping facts and tags each with which method produced it,
so the dossier can show confidence. **Honest limits:** logged-out IG/FB now show
very little; methods 1–3 improve coverage but guarantee nothing, and this stays
best-effort by design. Logged-in automation is deliberately **not** a default
method (ToS + account-ban risk); if you later want it, it becomes an opt-in 4th
method with a burner account.

## 5. Jobs & infrastructure

- **Job model** (SQLite `jobs` table): id, status (queued/collecting/analyzing/
  synthesizing/done/error), inputs, artifact paths, doc path, per-stage errors,
  timestamps. Scraping+vision+synthesis is slow, so this is async with a status
  endpoint the frontend polls.
- **Runner:** a single in-process asyncio worker queue (fits local/personal scale;
  no external broker). One job at a time keeps VRAM predictable.
- **Reuse from V1:** FastAPI shell, SQLite cache (now also caches rendered
  pages/artifacts), the YouTube service, and the quota ledger (still meaningful
  for the YouTube collector only). Rate limiting stays for the API endpoints.
- **Storage:** per-job folder under `data/jobs/<id>/` for screenshots, images, the
  synthesized `dossier.md` / `.pdf`.

## 6. Frontend

Multi-link intake (add/remove link rows + optional entity name) → submit → **job
progress view** (per-stage status, live) → **result view**: rendered dossier, a
download button, and collapsible raw artifacts per source (extracted text, each
screenshot with its vision note). Keeps the "everything is inspectable" principle.

## 7. Phases (deliverables / exit criteria)

- **P1 — Environment:** install Playwright (+ Chromium); download & launch
  `llama-server` for vision and text; `llm_client.py` smoke test (caption a test
  image, get a text completion). *Exit:* both endpoints answer from Python.
- **P2 — Artifact model + job scaffold:** `SourceArtifact`, jobs table, async
  worker, `/api/jobs` create/status, per-job storage. *Exit:* a no-op job runs
  through all states and persists.
- **P3 — WebsiteCollector:** Playwright render + readability text + screenshots +
  sub-page crawl. *Exit:* a real website yields text+screenshots into an artifact.
- **P4 — YouTubeCollector (content reframe):** transcripts/descriptions/comments
  into the artifact shape, reusing the API layer. *Exit:* a channel+video produce
  content artifacts; quota accounted.
- **P5 — SocialCollector (3 methods):** the merge pipeline. *Exit:* an IG/FB URL
  returns a merged best-effort artifact with per-method provenance, no crash on
  total failure.
- **P6 — Vision stage:** screenshots/images → vision notes on every artifact.
  *Exit:* artifacts come back with populated `vision_notes`.
- **P7 — Synthesis + document:** map-reduce over artifacts via gpt-oss → `dossier.md`
  (+ PDF via the pdf skill). The dossier has two layers: (a) **factual** — an
  overview and per-platform findings drawn only from collected content; and (b) an
  **Inferred Insights** section that reads between the lines — likely business
  model & monetization, target audience, brand/market positioning, content
  strategy, competitive angle, and other "hidden" analytics. Every inference is
  explicitly marked as such with a confidence and grounded in cited evidence from
  the artifacts (no invented private facts). *Exit:* a multi-link job produces one
  coherent document with both layers, end to end.
- **P8 — Frontend:** intake → progress → dossier/download/raw artifacts. *Exit:*
  full flow works in the browser on a real multi-link entity.

## 8. Open items — RESOLVED

- **VRAM → 16 GB** (RTX 5060 Ti, driver 596.36 / CUDA 13.2). Two 7–14B models don't
  both fit with headroom, so models load **sequentially**: the vision stage runs
  with the vision model, it's torn down, then the text model loads for synthesis.
  Clean, because the pipeline stages are already sequential. One job at a time.
- **Runtime → llama.cpp `llama-server`, CUDA 13.3 build (b9964).** 12.4 build lacks
  Blackwell `sm_120` kernels; 13.3 has them and runs on the 13.2 driver via CUDA
  minor-version compatibility. LM Studio's server is **not** used; only the GGUF
  files LM Studio downloaded are reused (they're plain data files).
- **Models — reuse what's already installed, no downloads:**
  - *Vision:* **`Qwen3.5-9B-Q8_0`** (8.9 GB) + `mmproj-Qwen3.5-9B-BF16` (0.86 GB).
    Chosen over `gemma-4-12B-it-QAT-Q4_0` by a head-to-head bench on a real,
    text-dense YouTube screenshot (`scripts/bench_vision.py`):
    - Gemma **hallucinated** ("PowerDog" for PewDiePie, "Messi" for "Mixes") and
      misread **"451 views" as "451K views"** (1000× error), "2.9m"→"2.1M",
      "2026™ Semi-Finals"→"2022 Final" — exactly the plausible-but-wrong failure
      that would poison gpt-oss's inferred insights downstream.
    - Qwen read view counts, durations, the full sponsored ad copy, thumbnail
      typography and the exact FIFA title correctly, and attempted Devanagari.
    - Cost: ~2× slower per image (10.2 s vs 5.7 s) and ~9.7 GB vs ~7 GB. Worth it:
      accuracy is the product; speed is a convenience. Gemma kept as fallback.
  - *Vision-stage gotchas found while benching (both would have silently broken P6):*
    1. llama.cpp **silently drops oversized images** — a 1896x970 screenshot
       contributed 0 image tokens. Client now downscales to max edge 1024.
    2. Both vision models are **reasoning models**; llama.cpp routes thinking to
       `reasoning_content` and they can exhaust the token budget mid-thought,
       returning empty `content`. Vision server now runs `--reasoning off`, with a
       `reasoning_content` fallback in the client.
    3. `--image-min-tokens 1024` **measured and adopted**: at the llama.cpp default
       (~258 image tokens) Qwen scored 1/7 exact ground-truth hits on a real
       screenshot ("Rinks Labs", "MrWhoseBoss", "Web3"); at 1024 it scored **6/7**
       ("Rienks Labs", "Mrwhosetheboss", "Website", "Bex", "451 views"), for
       +1.7 s/image (8.3 s → 10.0 s). Accuracy is the product; adopted as default.
    4. `ensure()` used to reuse **any** healthy server on the port — including an
       orphan left by a crashed run, silently serving a different model/flags.
       It now kills untracked servers before starting (`_kill_orphans`).
  - *Text synthesis:* **`gpt-oss-20b`** (11.3 GB, reasoning model, Apache-2.0) run
    at **high reasoning effort** — chosen specifically to surface non-obvious /
    inferred insight, not just restate surface facts. Fits VRAM alone. Fallbacks:
    `Qwen3-14B-Q4_K_M` (8.4 GB), `Mistral-7B` (4.1 GB). Swappable via `.env`.
- **CPU-vs-GPU speed:** vision over many screenshots is the slow step → one job at a
  time, a per-source screenshot cap (default 8), and GPU offload keep it bounded.
- **Social fragility** (§4) — unchanged reality, mitigated not solved.
- **ToS / subjects:** aggregating public info is fine; logged-in social automation
  stays off by default; if subjects are individuals, keep to public sources.
- **Model licensing:** Gemma (Google Gemma Terms) and Qwen (Apache-2.0 / Qwen
  license) both permit personal/research use; gpt-oss is Apache-2.0. Fine here.
```
