# 🕵️ Entity Research Aggregator

> *"I want to know everything about this YouTuber/creator/small company, but I refuse to open 16 tabs."* — you, probably

You paste a bunch of links — their website, YouTube, Instagram, GitHub, Patreon, that Linktree they definitely forgot to update — and this thing scrapes all of it, points a **local vision model** at every screenshot, hands the whole pile to a **local LLM**, and hands you back one Markdown (or PDF, we're fancy now) research dossier with an actual "here's how they probably make money" section that cites its sources like a functioning adult.

Everything runs **on your own machine**. No data leaves your PC except the actual HTTP requests to the actual websites you asked it to look at, plus the (optional, free, quota-capped) YouTube Data API. Your GPU does the thinking. Your GPU also gets tired, so be nice to it.

<sub>Yes, this README has jokes in it. Yes, it also has real documentation. It's giving "built a whole app in one sitting and is still a little feral about it" energy, and honestly? We're not sorry.</sub>

---

## What it actually does

1. **You give it links.** As many as you want, for one entity. Website, YouTube, Instagram, Facebook, LinkedIn, GitHub, Medium, Substack, Reddit, X/Twitter, Linktree/Beacons/stan.store, Patreon, Ko-fi/Buy Me a Coffee, Twitch, or literally any other website (it has a generic fallback collector for that — no site left behind).
2. **It scrapes each one** with a purpose-built collector per platform (not just "screenshot and pray") — real API calls where APIs exist (YouTube, GitHub, Reddit's JSON endpoints), structured data extraction where it's hiding in the page (LinkedIn's JSON-LD, Patreon's embedded JSON:API blob), RSS where that's the sane option (Medium, Substack, Google News), and a headless-browser render + text extraction for everything else.
3. **It optionally adds a news feed for free** — if you gave it an entity name, it auto-attaches a Google News search for that name. You didn't even ask. It just does it. Overachiever.
4. **A local vision model reads every screenshot.** Not to transcribe text we already scraped (that's how you get an AI confidently telling you a YouTuber has "451K views" when it's actually 451 — true story, ask us how we know), but to describe layout, branding, vibe, and to OCR the stuff that genuinely only exists as pixels (login-walled social previews, infographics, thumbnails).
5. **A local reasoning LLM synthesizes everything** into one dossier: factual findings per source, plus an "Inferred Insights" section that reads between the lines on business model, target audience, positioning, and monetization — where **every single claim carries a confidence level and a cited quote**. No vibes-based guessing allowed. If it doesn't know, it says so in Gaps & Caveats instead of making something up.
6. **You get a document**, not a spreadsheet of raw scrape dumps. Markdown, or click one button for a proper server-rendered PDF with page numbers and everything.

---

## The "wait, why should I trust this" section

Good question. Here's what we specifically built in to stop this thing from confidently lying to you:

- **Text-first, vision-as-a-backup.** If we already scraped a page's real text, the vision model is told to describe the *design*, not re-read the words — because re-reading them is exactly how numbers get hallucinated. (See: the entire 451-vs-451K incident above. We learned. Painfully.)
- **Every inference needs receipts.** The synthesis prompt legally cannot let the model write "they probably make $10K/month" with nothing backing it — it has to be `**Claim** — (confidence: medium) — Evidence: "quote from a real source" [platform]`, or it doesn't go in.
- **Failures are boring on purpose.** If a source 404s, gets rate-limited, or a platform decides your IP looks like a robot (rude, but fair, it is a robot), the dossier just says *"No notable public information found."* It will never print "HTTP 403" or a stack trace into your nice research document. That stuff lives in the debug view where it belongs.
- **No login automation, anywhere.** Instagram, Facebook, LinkedIn, Reddit, Patreon — all logged-out, best-effort, public-data-only. We are not trying to get your account banned or your IP blacklisted. Some sources will come back thin. That's the deal with polite scraping, and we think it's the right one.

---

## Architecture, for the "I actually want to know how" crowd

```
  you paste links
        │
        ▼
   COLLECTORS (per platform, run per job)  ──▶  screenshots + text + structured facts
        │
        ▼
   VISION STAGE  (local Qwen3.5-9B-VL via llama.cpp)
        │            reads screenshots; role-aware prompt (full OCR vs. "just vibes/branding")
        ▼
   SYNTHESIS STAGE  (local gpt-oss-20b, high reasoning, map-reduce over every source)
        │
        ▼
   ONE DOSSIER  (Markdown + on-demand PDF)
```

- **Backend:** FastAPI + a single-worker async job queue. Why single-worker? Because your GPU can only hold one model at a time — the vision model and the synthesis model take turns, tag-team style, and swapping two at once would just set your VRAM on fire. SQLite handles the job store, the YouTube quota ledger, and a TTL cache so repeat lookups are free.
- **Scraping:** Playwright (headless Chromium) + httpx + BeautifulSoup. Screenshots feed vision; extracted DOM text feeds synthesis directly (it's usually more accurate than making an AI squint at a screenshot).
- **Local AI runtime:** [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`, CUDA build, running:
  - **Vision:** `Qwen3.5-9B-Q8_0` — chosen after an actual empirical bake-off against Gemma-4-12B on a real screenshot. Gemma read "451 views" as "451K views" and hallucinated a channel called **"PowerDog"** that does not exist. Qwen got it right. We have the receipts in the commit history. This is not a drill.
  - **Synthesis:** `gpt-oss-20b` at high reasoning effort — because inferring a creator's monetization strategy from three sentences of bio text is, unlironically, a reasoning task.
- **Frontend:** Vanilla HTML/CSS/JS. No framework, no build step, no 400MB of `node_modules` judging you from a corner. It just works, and you can read all of it in one sitting.
- **Job IDs are sequential and kind of delightful:** your first job is `ABC0001`, then `ABC0002`, all the way to `ABC9999`, then it rolls over to `ABD0001` like an odometer, and if you somehow run 26³ × 9999 jobs it grows a fourth letter. We are choosing to believe you will never hit that limit. If you do, please tell us, we have questions.

---

## Platform coverage

15 dedicated collectors, plus a generic Playwright-based fallback that will politely attempt literally any other website you throw at it.

| Platform | How | Notes |
|---|---|---|
| **YouTube** | Official Data API v3 + free transcript fetch + comments | The one platform with a real, sanctioned API. We treat it like royalty. |
| **GitHub** | REST API | Repos, READMEs, languages, followers. No auth needed, no drama. |
| **LinkedIn** | Parses the JSON-LD Google-SEO blob every public page embeds | Company *and* person pages. Yes, that data is just sitting there in plain sight. |
| **Reddit** | Public `.json` endpoints, with an `old.reddit.com` HTML fallback | Because sometimes the JSON API just 403s you for existing, and old Reddit still answers the door. |
| **Patreon** | Parses the embedded Next.js JSON blob for real membership tiers + prices | This one took real detective work — Patreon hides tier data behind a JSON:API "sideloading" pattern that we had to reverse-engineer against a live page. Worth it: you get actual `$3/mo`, `$7/mo` tier pricing, not vibes. |
| **Ko-fi / Buy Me a Coffee** | og-tag scraping + a soft-404 detector | Fun fact: Ko-fi 200s a fake "page" for creators who don't exist instead of a real 404. We catch that so it doesn't get reported as a real profile. Sneaky, Ko-fi. Sneaky. |
| **Instagram / Facebook** | Best-effort logged-out render + og parsing | These platforms *really* don't want anonymous scrapers around, so expect partial data sometimes. That's not a bug, that's Instagram being Instagram. |
| **Medium / Substack** | RSS feeds | The wholesome, well-behaved corner of the internet where scraping is basically just... reading. |
| **Linktree / Beacons / stan.store** | Discovers every outbound link on the page | One pasted link-in-bio → a full map of everywhere else that creator lives online. It's basically a treasure map. |
| **X / Twitter, Twitch** | og-tag based | Logged-out means thin data, but the bio/tagline is real signal. |
| **News** | Auto-attached Google News RSS search for the entity name | Free bonus source. You're welcome. |
| **Everything else** | Generic Playwright render → text + screenshot | The "we got you" fallback. |

---

## Setup

You'll need: **Python 3.13**, **an NVIDIA GPU with ~16GB VRAM** (this was built and tuned against an RTX 5060 Ti — smaller cards may need to swap to lighter models), and patience for one big model download if you don't already have GGUFs sitting around from LM Studio or similar.

```powershell
cd Scraper
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
copy .env.example .env
```

### 1. YouTube API key (free, ~3 minutes, no credit card)

Google Cloud console → new project → enable *YouTube Data API v3* → Credentials → Create API key → **restrict it** to that API only. Paste it into `.env` as `YOUTUBE_API_KEY`. This gets you 10,000 free quota units/day, which is genuinely a lot — the app also caches aggressively and never touches the expensive `search.list` endpoint unless you explicitly ask it to.

### 2. Local LLM runtime (llama.cpp)

Download a [llama.cpp release](https://github.com/ggml-org/llama.cpp/releases) build matching your CUDA version, drop `llama-server.exe` + its DLLs into `tools/llamacpp/`. Point `backend/config.py` (or your `.env`, your call) at:

- **Vision:** a Qwen3.5-VL-9B GGUF (Q8 recommended) + its `mmproj` file
- **Synthesis:** a gpt-oss-20b GGUF

If you already grabbed these via LM Studio, the config defaults already point at LM Studio's default model folder — you may not need to change anything.

### 3. Run it

```powershell
.venv\Scripts\python -m uvicorn backend.main:app --port 8123
```

Open `http://localhost:8123`. Paste links. Click the big red button. Go get a coffee — a real job with vision + synthesis on several sources genuinely takes a few minutes, because your GPU is doing actual work, not just vibing.

### 4. Run the tests

```powershell
.venv\Scripts\python -m pytest -q
```

214 tests, fully offline (everything's mocked — no real network calls, no GPU spin-up), runs in under 2 seconds. If a test suite ever takes suspiciously longer than that, something snuck a real network/model call into a unit test again. We've caught ourselves doing this more than once. It happens to the best of us.

---

## API surface, for the curious / the automators

```
POST /api/jobs                              create a job — {links: [...], entity_name: "..."}
GET  /api/jobs                               list recent jobs
GET  /api/jobs/{id}                          job status + all collected artifacts
GET  /api/jobs/{id}/dossier                  the final Markdown
GET  /api/jobs/{id}/pdf                      server-rendered PDF, generated on demand
GET  /api/jobs/{id}/file?path=...            fetch a screenshot/image (path-traversal safe, we checked)

GET  /api/resolve?q=...                      raw YouTube channel/video resolution
GET  /api/channel/{id}, /api/video/{id}      full YouTube Data API pass-through (everything public)
GET  /api/quota                              live YouTube quota ledger
GET  /api/instagram/{username}               standalone Instagram lookup (feature-flagged)
```

Every response follows `{data, cached, fetched_at, quota}`; every error follows `{error: {reason, message}}` with a real HTTP status. Rate-limited, because we like our quota and assume you like yours too.

---

## Known limitations (we're being honest here, not humble-bragging)

- **Reddit, Instagram, Facebook, LinkedIn, Patreon, Ko-fi** are all logged-out best-effort. Sometimes they'll give you everything, sometimes they'll give you nothing and a shrug. This is the nature of scraping platforms that actively don't want to be scraped, and we chose "degrade gracefully" over "log in and risk getting banned."
- **TikTok** isn't supported. It's geo-blocked from the dev machine this was built on and thin logged-out anyway. Recognized as a valid future addition, not implemented.
- **No server-side video/audio analysis.** We read pixels and text, not sound.
- **This is a research tool, not a mass-surveillance tool.** It's built for looking up one entity at a time from links you already have — please don't point it at thousands of people. Also: be a decent human about what you do with the output.
- **There's no LICENSE file yet.** That's a deliberate blank the project owner hasn't filled in — check with them before assuming you can redistribute this.

See [`suggestions.txt`](suggestions.txt) for the "here's what we'd build next" list (GitHub deeper-dive, comparison mode for 2+ entities, custom research questions, scheduled re-runs to track changes over time, and more).

---

## A brief, honest "war stories" section

Because a README with zero personality is a crime against README-kind:

- **The Great Screenshot Disappearing Act:** llama.cpp silently drops oversized images instead of erroring. We spent a while wondering why the vision model was "reading" a 7000px-tall screenshot and returning nothing, before realizing the image never actually made it to the model. It just... vanished. Into the void. No apology, no explanation. Fixed by downscaling everything to a sane size first.
- **The AI Almost Leaked Its Inner Monologue Into a Professional Document:** gpt-oss is a reasoning model, and when it runs out of tokens mid-thought, it can return its raw internal reasoning instead of a final answer. Our first version had a fallback that would print that reasoning straight into the dossier if the real answer was empty. Which meant, briefly, a research document could open with something like *"Okay, I need to figure out what this creator does..."* instead of, y'know, an actual answer. Extremely unprofessional. Fixed.
- **Ko-fi's soft-404s:** covered above, but genuinely one of the sneakier bugs — a "successful" 200 response that's secretly just the homepage in a trench coat.
- **The Gemma vs. Qwen Vision Showdown:** we didn't just pick a vision model and hope. We ran an actual head-to-head bake-off on a real screenshot and one model straight-up invented a YouTuber named "PowerDog." We are not making that up. It's in the git log if you don't believe us.

---

## Credits / vibe check

Built collaboratively across one (extremely long, extremely caffeinated-in-spirit-if-not-in-fact) session between a human and Claude. It has the chaotic-but-it-actually-works energy of a school project you started way too casually and then couldn't stop improving at 1am — subagents debating vision models like a courtroom drama, live bug hunts on real Patreon pages, the works. We think that's a feature, not a bug. The code, however, is not chaotic — that part's tested, contained, and grounded on purpose. 214 tests say so.

If you use this: be honest with it, be honest about what you build with it, and maybe don't scrape someone's grandma's blog just because you can.
