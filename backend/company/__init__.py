"""Company competitor mechanism — live sources; local-model notes and analysis are optional. See docs/plans/PLAN-COMPANY.md."""

from ..config import settings

# Wikimedia only: it requires a contact. Every other host gets the plain UA,
# so the contact address is not sent to arbitrary news/blog sites.
UA = f"ScraperCompanyCLI/0.1 (+{settings.contact_url or 'contact not set'})"
UA_PLAIN = "Mozilla/5.0 (compatible; ScraperCompanyCLI/0.1)"

FRESH = False  # pipeline.run(fresh=True): skip cache reads (results are still re-stored)


def cached(key: str, kind: str, fn, ok=bool):
    """Persistent SQLite cache (backend/cache.py, TTL by kind) for web search and
    Wikidata. Results failing `ok` (empty = usually throttled) are never stored.
    Link checks and feeds are NOT cached: they must be live."""
    from .. import cache
    cache.init_db()
    if not FRESH and (hit := cache.get(key)) is not None:
        return hit[0]
    val = fn()
    if ok(val):
        cache.set(key, kind, val)
    return val
