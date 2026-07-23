"""Bespoke Substack collector.

Every Substack publication exposes a full RSS feed at <name>.substack.com/feed
with recent post titles, dates, and HTML bodies — no login, no JS. We derive the
feed URL from the human page, parse the RSS with BeautifulSoup (lxml's XML mode),
and use Playwright only to capture a screenshot for the vision stage.

Best-effort and contained: a 404 or empty feed yields errors and ok=False, never
an exception."""

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .base import SUBSTACK, SourceArtifact
from .website import UA, extract_text  # noqa: F401  (UA reused; extract_text kept available)


def _norm_url(url: str) -> str:
    return url if "://" in url else "https://" + url


def _feed_url(url: str) -> str:
    """Normalize a human Substack URL to its RSS feed URL.

    Derives the <name>.substack.com host from the url and appends /feed.
    """
    u = urlparse(_norm_url(url))
    host = (u.netloc or "").lower().removeprefix("www.")

    if host.endswith(".substack.com"):
        return f"https://{host}/feed"
    if host == "substack.com":
        # e.g. substack.com/@name or substack.com/name -> name.substack.com
        seg = u.path.strip("/").split("/")[0].lstrip("@")
        if seg:
            return f"https://{seg}.substack.com/feed"
        return "https://substack.com/feed"
    # Custom domain (some Substacks use their own): try /feed on the host.
    return f"https://{host}/feed"


def _html_to_text(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)


def _screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    from playwright.sync_api import sync_playwright

    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "substack"
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
        art.errors.append(f"substack screenshot: {type(exc).__name__}")


def _parse_items(soup: BeautifulSoup, cap: int) -> list[dict]:
    items = []
    for it in soup.find_all("item")[:cap]:
        title_el, link_el, pub_el = it.find("title"), it.find("link"), it.find("pubDate")
        title = (title_el.get_text(strip=True) if title_el else "").strip()
        link = (link_el.get_text(strip=True) if link_el else "").strip()
        pub = (pub_el.get_text(strip=True) if pub_el else "").strip()
        body_el = it.find("encoded") or it.find("description")
        body = _html_to_text(body_el.get_text() if body_el else "")
        items.append({"title": title, "link": link, "date": pub, "body": body})
    return items


def collect(url: str, job_dir: Path) -> SourceArtifact:
    url = _norm_url(url)
    art = SourceArtifact(url=url, platform=SUBSTACK, method="rss")
    feed = _feed_url(url)
    art.facts["feed_url"] = feed

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(feed)
        if r.status_code != 200:
            art.errors.append(f"substack feed: HTTP {r.status_code}")
        else:
            soup = BeautifulSoup(r.text, "xml")
            channel = soup.find("channel")
            feed_title = ""
            if channel is not None:
                t = channel.find("title", recursive=False) or channel.find("title")
                feed_title = t.get_text(strip=True) if t else ""
            if feed_title:
                art.facts["feed_title"] = feed_title

            items = _parse_items(soup, settings.substack_posts)
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
                art.errors.append("substack feed: no items")
    except Exception as exc:
        art.errors.append(f"substack: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    art.ok = art.facts.get("post_count", 0) > 0
    return art
