"""Bespoke Medium collector.

Medium exposes a full RSS feed for every user and publication. A plain GET of the
feed returns recent post titles, dates, and full HTML bodies — no login, no JS.
We normalize the human-facing profile URL to its feed URL, parse the RSS with
BeautifulSoup (lxml's XML mode), and use Playwright only to capture a screenshot
of the human page for the vision stage.

Best-effort and contained: a 404 or empty feed yields errors and ok=False, never
an exception."""

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .base import MEDIUM, SourceArtifact
from .website import UA, extract_text  # noqa: F401  (UA reused; extract_text kept available)


def _norm_url(url: str) -> str:
    return url if "://" in url else "https://" + url


def _feed_url(url: str) -> str:
    """Normalize a human Medium URL to its RSS feed URL.

    - path starts with /@user           -> https://medium.com/feed/@user
    - medium.com/<publication> (no @)   -> https://medium.com/feed/<publication>
    - <user>.medium.com                 -> https://<user>.medium.com/feed
    """
    u = urlparse(_norm_url(url))
    host = (u.netloc or "").lower().removeprefix("www.")
    path = u.path.strip("/")

    if host == "medium.com":
        if path.startswith("@"):
            handle = path.split("/")[0]
            return f"https://medium.com/feed/{handle}"
        if path:
            pub = path.split("/")[0]
            return f"https://medium.com/feed/{pub}"
        return "https://medium.com/feed"
    if host.endswith(".medium.com"):
        return f"https://{host}/feed"
    # Fallback: treat as a generic RSS-capable host.
    return f"{u.scheme}://{host}/feed"


def _html_to_text(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)


def _screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    from playwright.sync_api import sync_playwright

    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "medium"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=UA, locale="en-US",
                                       viewport={"width": 1280, "height": 1400}).new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(1800)
            shot = shots / f"{slug}.png"
            page.screenshot(path=str(shot))
            art.screenshots.append(str(shot))
            browser.close()
    except Exception as exc:
        art.errors.append(f"medium screenshot: {type(exc).__name__}")


def _parse_items(soup: BeautifulSoup, cap: int) -> list[dict]:
    items = []
    for it in soup.find_all("item")[:cap]:
        title = (it.find("title").get_text(strip=True) if it.find("title") else "").strip()
        link = (it.find("link").get_text(strip=True) if it.find("link") else "").strip()
        pub = (it.find("pubDate").get_text(strip=True) if it.find("pubDate") else "").strip()
        body_el = it.find("encoded") or it.find("description")
        body = _html_to_text(body_el.get_text() if body_el else "")
        items.append({"title": title, "link": link, "date": pub, "body": body})
    return items


def collect(url: str, job_dir: Path) -> SourceArtifact:
    url = _norm_url(url)
    art = SourceArtifact(url=url, platform=MEDIUM, method="rss")
    feed = _feed_url(url)
    art.facts["feed_url"] = feed

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(feed)
        if r.status_code != 200:
            art.errors.append(f"medium feed: HTTP {r.status_code}")
        else:
            soup = BeautifulSoup(r.text, "xml")
            channel = soup.find("channel")
            feed_title = ""
            if channel is not None:
                t = channel.find("title", recursive=False) or channel.find("title")
                feed_title = t.get_text(strip=True) if t else ""
            if feed_title:
                art.facts["feed_title"] = feed_title

            items = _parse_items(soup, settings.medium_articles)
            art.facts["post_count"] = len(items)

            if items:
                listing = "\n".join(
                    f"- {it['title']} ({it['date']})" for it in items)
                art.text_blocks.append(
                    {"label": f"recent posts ({len(items)})", "text": listing})
                for it in items:
                    if it["body"]:
                        art.text_blocks.append(
                            {"label": f"post: {it['title']}",
                             "text": it["body"][:300]})
            else:
                art.errors.append("medium feed: no items")
    except Exception as exc:
        art.errors.append(f"medium: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    art.ok = art.facts.get("post_count", 0) > 0
    return art
