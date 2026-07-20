"""Google News collector.

Google News publishes a public RSS feed for any search query, which gives us
recent headlines, publishers and dates for an entity without scraping a page or
needing an API key. The pipeline calls `collect_for_entity` automatically for
every job; `collect` handles a news.google.com URL a user pasted in explicitly.

Best-effort and never raises: failures are contained into `art.errors` so a dead
feed can never break a collection run. No screenshot — a feed is not a page."""

from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .base import NEWS, SourceArtifact
from .website import UA

_FEED = ("https://news.google.com/rss/search?q={q}"
         "&hl=en-US&gl=US&ceid=US:en")
_PUBLISHER_CAP = 10


def _feed_url(query: str) -> str:
    return _FEED.format(q=quote_plus(query))


def _fetch(url: str) -> tuple[str | None, str | None]:
    """(text, error) — never raises."""
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            return None, f"news fetch: HTTP {r.status_code}"
        return r.text, None
    except Exception as exc:
        return None, f"news fetch: {type(exc).__name__}: {exc}"


def _parse_items(xml_text: str) -> list[dict]:
    """Extract {title, link, published, publisher} from Google News RSS."""
    items: list[dict] = []
    soup = BeautifulSoup(xml_text, "xml")
    for it in soup.find_all("item"):
        title = (it.title.get_text(strip=True) if it.title else "")
        if not title:
            continue
        src = it.find("source")
        items.append({
            "title": title,
            "link": (it.link.get_text(strip=True) if it.link else ""),
            "published": (it.pubDate.get_text(strip=True) if it.pubDate else ""),
            "publisher": (src.get_text(strip=True) if src else ""),
        })
    return items


def _populate(art: SourceArtifact, query: str, xml_text: str) -> None:
    items = _parse_items(xml_text)[:max(1, int(settings.news_items))]
    art.facts["query"] = query
    art.facts["headline_count"] = len(items)
    if not items:
        art.errors.append("news: no headlines found")
        return

    lines = []
    publishers: list[str] = []
    for it in items:
        meta = ", ".join(p for p in (it["publisher"], it["published"]) if p)
        lines.append(f"- {it['title']}" + (f" ({meta})" if meta else ""))
        pub = it["publisher"]
        if pub and pub not in publishers:
            publishers.append(pub)

    art.text_blocks.append({"label": f"recent news: {query}",
                            "text": "\n".join(lines)})
    art.facts["publishers"] = publishers[:_PUBLISHER_CAP]
    art.ok = True


def collect_for_entity(entity_name: str, job_dir: Path) -> SourceArtifact:
    """Recent Google News headlines for an entity name."""
    query = (entity_name or "").strip()
    url = _feed_url(query)
    art = SourceArtifact(url=url, platform=NEWS, method="google-news-rss")
    if not query:
        art.errors.append("news: empty entity name")
        return art
    try:
        text, err = _fetch(url)
        if err:
            art.errors.append(err)
            return art
        _populate(art, query, text or "")
    except Exception as exc:  # belt and braces — this must never raise
        art.errors.append(f"news: {type(exc).__name__}: {exc}")
    return art


def _query_from_url(url: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
    except Exception:
        return ""
    vals = qs.get("q") or []
    return (vals[0] if vals else "").strip()


def collect(url: str, job_dir: Path) -> SourceArtifact:
    """Collector for a pasted news.google.com URL."""
    query = _query_from_url(url)
    if query:
        art = collect_for_entity(query, job_dir)
        art.facts["source_url"] = url
        return art

    art = SourceArtifact(url=url, platform=NEWS, method="google-news-rss")
    try:
        text, err = _fetch(url)
        if err:
            art.errors.append(err)
            return art
        _populate(art, url, text or "")
    except Exception as exc:
        art.errors.append(f"news: {type(exc).__name__}: {exc}")
    return art
