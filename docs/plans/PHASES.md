> **Superseded.** These are the V1 (YouTube-stats) phases, all complete.
> The project pivoted to a multi-source entity research aggregator — see
> [PLAN-V2-AGGREGATOR.md](PLAN-V2-AGGREGATOR.md) for the V2 architecture and
> phases P1–P8 (also complete).

# Build Phases

Formalized from the approved implementation plan (§11). Each phase has deliverables and exit criteria; a phase is done only when its exit criteria pass.

## Phase 1 — Scaffold
**Deliverables:** project skeleton (`backend/`, `frontend/`, `tests/`), `config.py` (pydantic-settings), `requirements.txt`, `.env.example`, `.gitignore`, `README.md`, virtualenv with dependencies installed.
**Exit criteria:** `pip install -r requirements.txt` succeeds; `python -c "from backend.config import settings"` runs clean.

## Phase 2 — Foundations (quota ledger, cache, API client)
**Deliverables:** `backend/quota.py` (persistent Pacific-day ledger, cost table, 9,500-unit soft stop), `backend/cache.py` (SQLite TTL cache + negative caching + `cached_call` helper), `backend/youtube/client.py` (httpx wrapper, typed `YouTubeError`, charges quota on every call).
**Exit criteria:** `pytest tests/test_quota.py tests/test_cache.py` green; calling the client without an API key raises a typed `noApiKey` error (no crash, no key leakage).

## Phase 3 — Input resolution
**Deliverables:** `backend/youtube/resolver.py` — accepts `UC…` IDs, `@handles`, all YouTube URL forms (`/channel/`, `/user/`, `/c/`, `/@`, `watch?v=`, `youtu.be/`, `/shorts/`, `/live/`), and bare names; never uses `search.list`; worst case ≤2 quota units; misses are negative-cached 1 h.
**Exit criteria:** `pytest tests/test_resolver.py` green across the full input matrix.

## Phase 4 — Channel core API
**Deliverables:** `backend/youtube/service.py` channel snapshot (channels.list all 7 public parts → uploads playlist page 1 → batched videos.list all 10 public parts → playlists → sections → cached category map → derived genre), routes `/api/resolve`, `/api/channel/{id}`, `/api/quota`.
**Exit criteria:** server boots; endpoints return typed JSON errors without a key; with a key, a fresh channel load costs ≈5–6 units and an immediate repeat costs 0 (cache hit). *(Live part blocked on user-provided API key.)*

## Phase 5 — Video & sub-resources
**Deliverables:** `/api/video/{id}` (all public parts + category name), comments + replies (paginated), captions metadata (50 u, `confirm=true` gate), activities, subscriptions (best-effort, typed "not public"), further video pages, explicit search (100 u, `confirm=true` gate).
**Exit criteria:** all endpoints wired; expensive endpoints refuse without `confirm=true` and state their cost; comments-disabled and subs-private return typed, negative-cached errors.

## Phase 6 — Hardening
**Deliverables:** per-IP rate limiting (slowapi: ~10/min fresh, 60/min cheap sub-resources, 3/min expensive), consistent `{data, cached, fetched_at, quota}` envelope and `{error:{reason,message}}` error shape, `?refresh=true` force-refresh, quota soft-stop behavior end to end.
**Exit criteria:** request burst returns 429; API key appears in no response body; quota-exhausted state returns typed 429 with cached data still servable.

## Phase 7 — Frontend
**Deliverables:** static single page (`frontend/`): input → resolve → channel view (all parts, hidden/rounded subscriber notes, genre line, collapsible playlists/sections/activities/subscriptions, recent-videos grid + load more) → video view (all parts grouped, lazy comments, cost-labelled captions button), deep-search behind cost confirm, quota meter, cached-at badge + refresh, raw-JSON toggle per section.
**Exit criteria:** full flow works in a browser against the running server; quota meter updates from every response.

## Phase 8 — Instagram (feature-flagged, best-effort)
**Deliverables:** `backend/instagram/service.py` (instaloader, anonymous only, every failure typed), `/api/instagram/{username}`, UI section marked best-effort; `ENABLE_INSTAGRAM=false` by default; README documents the ToS caveat and ban-risk rationale for no login support.
**Exit criteria:** disabled flag returns typed 503; enabled without instaloader installed returns typed "not installed"; failures return typed `unavailable`, never a 500.

## Final verification (from plan §12)
Unit tests green; live E2E with a real key (user supplies it): all six input forms resolve, quota accounting matches predictions, cache hit = 0 units, rate limit fires, key never visible in browser network traces.
