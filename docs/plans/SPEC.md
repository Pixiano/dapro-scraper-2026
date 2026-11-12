# YouTube/Instagram Stats Scraper — Build Spec

## Goal
A web app that takes a channel/profile input and returns textual stats. Free-tier only, no paid APIs.

## Scope

**YouTube (primary, reliable):**
- Subscriber count
- Total views
- Total video count
- Channel creation date
- Last upload date
- Recent video titles (last N)
- Category ID (rough genre proxy — YouTube has no true "genre" field)

**Instagram (secondary, fragile — treat as phase 2 / best-effort):**
- Follower count
- Post count
- Bio text
- No reliable genre field exists

## Tech approach

**YouTube**
- Use YouTube Data API v3 (official, free, 10,000 units/day quota)
- Channel stats call ≈ 1 unit; recent video list ≈ 3–5 units
- Requires: Google Cloud project + free API key (no credit card)
- This is stable and production-quality — build this first and make it the core.

**Instagram**
- No official free API for arbitrary public profiles.
- Use instaloader (open-source Python lib) as the scraping method.
- Expect rate-limiting / occasional breakage — Instagram changes backend often.
- Build with a clear fallback/error state in UI ("data unavailable, try again later") since this WILL fail sometimes.
- Do not rely on raw HTML scraping via requests/BeautifulSoup — gets blocked faster than instaloader.

## Architecture
1. Backend: simple API server (Flask/FastAPI or Node/Express — pick based on comfort)
2. Caching layer: SQLite or even a JSON file, cache results per channel/profile for X hours to avoid re-hitting quota/rate limits on repeat lookups
3. Frontend: input field → fetch → display stats card
4. Separate service modules: youtube_service (API-based, stable) and instagram_service (instaloader-based, wrap in try/except with graceful fallback)

## Build priority (for a one-day build)
1. YouTube Data API integration + stats display — get this fully working first
2. Caching layer to stay under quota
3. Basic frontend UI
4. Instagram via instaloader as a stretch/bonus feature, clearly marked as "may be unreliable"

## Known limitations to design around
- No true "genre" field on either platform — category ID (YouTube) or manual tagging is the closest proxy
- Instagram scraping is inherently unstable long-term; don't build core functionality dependent on it
- Stay under 10k YouTube API units/day — cache aggressively
