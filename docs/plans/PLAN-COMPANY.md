# Plan — Company competitor mechanism

> **Historical plan.** The mechanism was built from this, then grew: it now also has optional
> local-model vision notes and analysis paragraphs (so "No LLM anywhere" below is out of date),
> and the outlook became a share of headlines. The current behaviour is described in
> [../company-reports.md](../company-reports.md).

A second mechanism, separate from the creator/YouTuber one. CLI first:

```
python -m backend.company "Alphabet" [--competitors "A,B"] [--social]
```

**No LLM anywhere.** Every fact comes from a live source with a live-checked link.
The creator mechanism (`main.py`, `pipeline.py`, `collectors/*`) is untouched; we only import helpers from it.

## Pipeline

1. **Resolve** — Wikidata `wbsearchentities` → QID → `Special:EntityData/<QID>.json`.
   If the entity has no handles/products (Alphabet Q20800404), also read its top P355 child (Google Q95).
2. **Categories** — P1056 products + P355 subsidiaries with an enwiki sitelink, ranked by
   Wikimedia pageviews, top 5.
3. **Competitors** — web search `"<category> vs"` on **Brave Search API and `ddgs` together, always**
   (results merged; either failing alone is fine; Brave skipped while `BRAVE_API_KEY` is unset), regex `X vs Y`
   on titles, drop ads. Each name verified on Wikidata (enwiki sitelink required).
   Drop the company's own entities (P127 owned-by / P749 parent org — e.g. Waze is Google's).
   Confidence = distinct domains naming it: ≥3 high, 2 medium, 1 low (low → appendix).
   3 per category, 10 overall after dedupe. `--competitors` overrides discovery.
4. **First-party links (Wikidata only)** — P856 website, P2397 YouTube, P2003 Instagram,
   P2013 Facebook, P1581 blog, P3899 Medium. Missing → "not listed", never guessed.
5. **Collect** — bucketed into Q2 2026, Q3 2026 (Jul–Sep), Q4-to-date:
   - Hacker News Algolia `search_by_date` (story, points>20, name in title)
   - Google News RSS with `after:/before:` (reuse `news._fetch/_feed_url/_parse_items`)
   - Blogs: P1581 feed, else probe `/feed /rss /rss/ /rss.xml /atom.xml /news/rss.xml`; Medium = "latest 10"
   - YouTube: `youtube._get("playlistItems")` paged to Q2 start (quota-charged), RSS fallback (last 15)
   - `--social`: Instagram/Facebook og-tags, profile-level only, not in quarter counts
6. **Link check** — dead only on 404/410/DNS/connection error (dropped); 401/403/429 = "unverified (bot-blocked)". Status + checked_at recorded.
7. **Report** — Markdown → `pdf.render_markdown_pdf`. AI = word-boundary keyword regex.
   Item confidence: first-party high; HN/news with name in title medium; search-only low.
   Outlook = AI-item count Q3 vs Q2 (±20% → ↑/↓/flat), labelled a rule-based count, not a forecast.
   Output: `data/company/<slug>/<date>/report.pdf` + `raw.json`.

Wikimedia requires a User-Agent with contact info (403 otherwise).

## Phases

| # | Phase | Files | Exit check | ETA |
|---|---|---|---|---|
| 0 | Deps + smoke | requirements, config, `company/__init__.py` | ddgs returns results; Wikimedia 200 with UA | 0.5 h |
| 1 | Resolve + categories | `wikidata.py`, `__main__.py` | `"Alphabet"` → QID + 5 categories | 2.5 h |
| 2 | Competitors | `competitors.py` | competitors per category with confidence; override works | 3 h |
| 3 | Collect | `collect.py` | `raw.json` bucketed by quarter | 4.5 h |
| 4 | Link check + report | `report.py` | `report.pdf` with a section per competitor | 3 h |
| 5 | Live test + README | tests, README | `pytest -m live -k company` passes | 1 h |
| 6* | `--social` | `collect.py` | profile og data | 1 h |
| 7* | Localhost web UI | `company/web.py` | `localhost:8000/?q=Alphabet` → PDF | 3 h |

Core ≈ 14.5 h; with optional ≈ 18.5 h. Each phase: `pytest -q` + `mypy` green, one local commit.

**Cut order if short on time:** 7, 6 → YouTube API paging (RSS only) → blog probing (P1581 only)
→ pageview ranking → search discovery (`--competitors` only).

## Risks
- Search scraping (`ddgs`) is ToS-grey and brittle → Brave API first, fail soft, override flag.
- Meta blocks anonymous scraping → `--social` off by default, profile-level only.
- Name collisions (Meta, Apple) → exact-phrase queries + name-in-title rule.
- Wikidata coverage uneven → "not listed" instead of guessing.
- YouTube quota → channel cap, stop paging at Q2 start.

## User scenario
1. `python -m backend.company "Alphabet"`
2. Terminal shows: resolve → categories → competitors (with confidence + domain counts) →
   collection counts → link check summary → PDF path.
3. PDF per competitor: discovery evidence links, Wikidata profile, AI-activity table
   (HN / News / Blog / YouTube × Q2 / Q3 / Q4-so-far), rule-based outlook, top Q3 AI items
   each with date, source, link status and confidence.
4. Rerun with `--competitors "OpenAI,Anthropic,Meta"` to skip discovery.
5. `raw.json` beside the PDF holds every item, link, HTTP status and check time for audit.
