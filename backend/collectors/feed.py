"""Medium and Substack collector — both publish a full RSS feed.

A plain GET of the feed returns recent post titles, dates, and full HTML bodies —
no login, no JS. We turn the human-facing URL into its feed URL, parse the RSS
with BeautifulSoup (lxml's XML mode), and use Playwright only to capture a
screenshot of the human page for the vision stage.

Best-effort and contained: a 404 or empty feed yields errors and ok=False, never
an exception."""

from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .base import MEDIUM, SUBSTACK, SourceArtifact, classify_url, screenshot as _screenshot
from .website import UA


def _feed_url(url: str) -> str:
    """Human page URL -> RSS feed URL.

    - medium.com/@user[/post]          -> https://medium.com/feed/@user
    - medium.com/<publication>[/post]  -> https://medium.com/feed/<publication>
    - substack.com/@name               -> https://name.substack.com/feed
    - <name>.medium.com, <name>.substack.com, anything else -> https://<host>/feed
    """
    u = urlparse(url)
    host = (u.netloc or "").lower().removeprefix("www.")
    seg = u.path.strip("/").split("/")[0]
    if host == "medium.com" and seg:
        return f"https://medium.com/feed/{seg}"
    if host == "substack.com" and seg:
        return f"https://{seg.lstrip('@')}.substack.com/feed"
    return f"https://{host}/feed"


def _parse_items(soup: BeautifulSoup, cap: int) -> list[dict]:
    def text(it, name):
        el = it.find(name)
        return el.get_text(strip=True) if el else ""

    items = []
    for it in soup.find_all("item")[:cap]:
        body_el = it.find("encoded") or it.find("description")
        body = BeautifulSoup(body_el.get_text() if body_el else "",
                             "lxml").get_text(" ", strip=True)
        items.append({"title": text(it, "title"), "link": text(it, "link"),
                      "date": text(it, "pubDate"), "body": body})
    return items


def collect(url: str, job_dir: Path) -> SourceArtifact:
    url = url if "://" in url else "https://" + url
    platform = SUBSTACK if classify_url(url) == SUBSTACK else MEDIUM
    cap = settings.substack_posts if platform == SUBSTACK else settings.medium_articles
    art = SourceArtifact(url=url, platform=platform, method="rss")
    feed = _feed_url(url)
    art.facts["feed_url"] = feed

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(feed)
        if r.status_code != 200:
            art.errors.append(f"{platform} feed: HTTP {r.status_code}")
        else:
            soup = BeautifulSoup(r.text, "xml")
            channel = soup.find("channel")
            t = channel.find("title") if channel is not None else None
            if t and t.get_text(strip=True):
                art.facts["feed_title"] = t.get_text(strip=True)

            items = _parse_items(soup, cap)
            art.facts["post_count"] = len(items)

            if items:
                listing = "\n".join(
                    f"- {it['title']} ({it['date']})" for it in items)
                art.text_blocks.append(
                    {"label": f"recent posts ({len(items)})", "text": listing})
                # Newest first; stop at the per-source budget so one blog
                # cannot swamp the dossier.
                left = settings.feed_total_chars
                for it in items:
                    body = it["body"][:min(settings.feed_post_chars, left)]
                    if body:
                        art.text_blocks.append(
                            {"label": f"post: {it['title']}", "text": body})
                        left -= len(body)
            else:
                art.errors.append(f"{platform} feed: no items")
    except Exception as exc:
        art.errors.append(f"{platform}: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    art.ok = art.facts.get("post_count", 0) > 0
    return art
