"""Bespoke Twitch collector — deliberately thin, og-based.

Twitch's channel page is a heavy JS app; querying it properly means going
through Twitch's unofficial GraphQL endpoint with a client-id, which is more
fragile and more aggressive than warranted for a research aggregator. Instead
we render the channel page with Playwright (needed just to let the client-side
app populate the og tags and visible DOM — a bare GET returns little), then
read what's publicly available: og title/description for channel name and
bio, and a couple of best-effort, defensive text signals (live status,
follower count) pulled from the rendered page's visible text. This mirrors
twitter.py's og-only philosophy — thin coverage is the correct outcome here,
not a bug.

Live-status detection is intentionally conservative: we only ever set
facts["is_live"] when a signal is confidently present. If nothing is found we
omit the key entirely rather than default it to False — a stale/incorrect
live claim is worse than no claim at all.

Never raises: every failure lands in art.errors and ok stays False."""

import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from ..config import settings
from .base import TWITCH, SourceArtifact
from .website import UA, extract_text

_FOLLOWERS_RE = re.compile(r"([\d,]+)\s*followers", re.I)
_LIVE_RE = re.compile(r"\bLIVE\b")
# Only look at the front of the visible text: the live badge sits right next
# to the channel name/title near the top of the page. Searching the whole
# page risks matching unrelated "live" occurrences further down.
_LIVE_SCAN_CHARS = 1500


def _channel_from_url(url: str) -> str | None:
    path = urlparse(url if "://" in url else "https://" + url).path
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else None


def _render(url: str, job_dir: Path, art: SourceArtifact) -> str | None:
    """Single Playwright session: navigate, capture screenshot, return HTML.

    One browser launch covers both needs (content + screenshot). Returns None
    and appends to art.errors on any failure; never raises."""
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "twitch"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=UA, locale="en-US",
                                       viewport={"width": 1280, "height": 1400}).new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(settings.social_page_wait_ms)
            html = page.content()
            shot = shots / f"{slug}.png"
            page.screenshot(path=str(shot))
            art.screenshots.append(str(shot))
            browser.close()
            return html
    except Exception as exc:
        art.errors.append(f"twitch render: {type(exc).__name__}")
        return None


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=TWITCH, method="og")

    channel = _channel_from_url(url)
    if not channel:
        art.errors.append("twitch: no channel path segment in URL")
        art.ok = False
        return art

    art.facts["channel_name"] = channel

    html = _render(url, job_dir, art)
    if html is None:
        art.ok = False
        return art

    try:
        text, facts = extract_text(html)
        og_title = facts.get("og_title") or ""
        og_desc = facts.get("og_description") or ""
        title = facts.get("title") or ""

        if og_desc:
            art.facts["bio"] = og_desc

        # Follower count: best-effort regex over the visible text.
        m = _FOLLOWERS_RE.search(text or "")
        if m:
            try:
                art.facts["followers"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

        # Live status: only set when confidently detected near the top of
        # the page; otherwise omit the key rather than guess.
        try:
            if _LIVE_RE.search((text or "")[:_LIVE_SCAN_CHARS]):
                art.facts["is_live"] = True
        except Exception:
            pass

        lines = [v for v in (og_title or title, og_desc) if v]
        if art.facts.get("is_live"):
            lines.append("Status: LIVE")
        if lines:
            art.text_blocks.append({"label": "twitch profile", "text": "\n".join(lines)})

        art.ok = bool(og_title or og_desc or title or channel)
    except Exception as exc:
        art.errors.append(f"twitch parse: {type(exc).__name__}: {exc}")
        art.ok = bool(channel)

    return art
