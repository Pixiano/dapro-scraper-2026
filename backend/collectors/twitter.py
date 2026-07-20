"""Bespoke X/Twitter collector — deliberately thin.

X requires auth for anything substantive: the logged-out profile page ships an
empty app shell, and the timeline is fetched by an authenticated GraphQL call we
neither can nor should make. What logged-out X *does* publish is the Open Graph
card — display name and bio — which is public by design. So this collector reads
the og tags for facts and captures a screenshot for the vision stage, and stops
there. Thin coverage is the correct, ToS-respecting outcome, not a bug.

Never raises: every failure lands in art.errors and ok stays False."""

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

from ..config import settings
from .base import TWITTER, SourceArtifact
from .website import UA, extract_text

_RESERVED = {"i", "home", "explore", "search", "settings", "notifications",
             "messages", "intent", "share", "hashtag"}


def _handle_from_url(url: str) -> str | None:
    path = urlparse(url if "://" in url else "https://" + url).path
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    h = parts[0].lstrip("@")
    return None if h.lower() in _RESERVED else h


def _screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "twitter"
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
        art.errors.append(f"twitter screenshot: {type(exc).__name__}")


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=TWITTER, method="og")
    handle = _handle_from_url(url)
    if handle:
        art.facts["handle"] = handle

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            art.errors.append(f"twitter fetch: HTTP {r.status_code}")
        else:
            _, facts = extract_text(r.text or "")
            # og tags only: the <title> of a login-walled page is a bare "X",
            # which would read as success without carrying any real content.
            og_title = facts.get("og_title") or ""
            og_desc = facts.get("og_description") or ""
            if og_title:
                art.facts["og_title"] = og_title
            if og_desc:
                art.facts["bio"] = og_desc
            blurb = "\n".join(v for v in (og_title, og_desc) if v)
            if blurb:
                art.text_blocks.append({"label": "x/twitter profile", "text": blurb})
            else:
                art.errors.append("twitter: no og content (login-walled)")
    except Exception as exc:
        art.errors.append(f"twitter: {type(exc).__name__}")

    _screenshot(url, job_dir, art)
    art.ok = bool(art.text_blocks)
    return art
