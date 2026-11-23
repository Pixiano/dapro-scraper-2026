"""Bespoke X/Twitter collector — deliberately thin.

X requires auth for anything substantive: the logged-out profile page ships an
empty app shell, and the timeline is fetched by an authenticated GraphQL call we
neither can nor should make. What logged-out X *does* publish is the Open Graph
card — display name and bio — which is public by design. So this collector reads
the og tags for facts and captures a screenshot for the vision stage, and stops
there. Thin coverage is the correct, ToS-respecting outcome, not a bug.

Never raises: every failure lands in art.errors and ok stays False."""

from pathlib import Path
from urllib.parse import urlparse

import httpx

from .base import TWITTER, SourceArtifact, screenshot as _screenshot
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
