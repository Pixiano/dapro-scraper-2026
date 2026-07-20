"""SocialCollector — Instagram / Facebook, best-effort, three methods merged.

Per the plan (§4), run all three and merge whatever each returns, tagging every
piece with its provenance so the dossier can weight confidence:

  method 1  logged-out Playwright render → DOM/og text + viewport screenshot
  method 2  unofficial lib/endpoint      → instaloader (IG) / raw-HTML og (FB)
  method 3  screenshot → vision          → screenshot captured here, the vision
                                           stage (P6) fills vision_notes from it

Honest limits: logged-out IG/FB expose very little and block aggressively. Any
method may fail; failures are recorded, never raised. No logged-in automation
(ToS + account-ban risk)."""

from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

from ..config import settings
from ..instagram import service as ig_service
from .base import FACEBOOK, INSTAGRAM, SourceArtifact, classify_url
from .website import UA, extract_text


# Path prefixes that are content, not a profile handle — no username to extract.
_NON_PROFILE = {"p", "reel", "reels", "tv", "stories", "explore",
                "watch", "groups", "profile.php"}


def _username(url: str) -> str | None:
    segs = [s for s in urlparse(url if "://" in url else "https://" + url).path.split("/") if s]
    if not segs:
        return None
    first = segs[0].lower()
    if first == "pages":  # facebook.com/pages/<Name>/<id>
        return segs[1] if len(segs) > 1 else None
    if first in _NON_PROFILE:
        return None
    return segs[0].lstrip("@")


def _prov(art: SourceArtifact, field: str, method: str) -> None:
    art.facts.setdefault("provenance", {})[field] = method


def _method_playwright(url: str, job_dir: Path, art: SourceArtifact) -> bool:
    """Method 1: render logged-out, extract og/DOM text, viewport screenshot."""
    method = "playwright-loggedout"
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, locale="en-US",
                                      viewport={"width": 1280, "height": 1200})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(1800)
            html = page.content()
            shot = shots / f"social_{classify_url(url)}_{_username(url) or 'page'}.png"
            page.screenshot(path=str(shot))  # viewport, not full_page (login walls)
            art.screenshots.append(str(shot))
            browser.close()
    except Exception as exc:
        art.errors.append(f"playwright: {type(exc).__name__}: {exc}")
        return False

    text, facts = extract_text(html)
    got = False
    for key in ("og_title", "og_description", "meta_description"):
        if facts.get(key):
            _prov(art, key, method)
            art.facts.setdefault(key, facts[key])
            got = True
    # og:description on IG/FB typically holds "N followers, M posts — bio…"
    blurb = "\n".join(v for v in (facts.get("og_title"), facts.get("og_description")) if v)
    if blurb:
        art.text_blocks.append({"label": "social og-summary", "text": blurb, "method": method})
        got = True
    elif text and len(text) > 40:
        art.text_blocks.append({"label": "social page-text",
                                "text": text[:settings.social_text_cap], "method": method})
        got = True
    return got


def _method_instaloader(url: str, art: SourceArtifact) -> bool:
    """Method 2 (Instagram): anonymous instaloader profile fields."""
    user = _username(url)
    if not user:
        art.errors.append("instaloader: no username in URL")
        return False
    data = ig_service.fetch_profile(user)
    if not data.get("available"):
        art.errors.append(f"instaloader: {data.get('reason')}")
        return False
    for f in ("followers", "following", "posts", "fullName", "verified", "private"):
        if data.get(f) is not None:
            art.facts[f] = data[f]
            _prov(art, f, "instaloader")
    if data.get("bio"):
        art.text_blocks.append({"label": f"instagram bio: @{user}",
                                "text": data["bio"], "method": "instaloader"})
        _prov(art, "bio", "instaloader")
    return True


def _method_httpx_og(url: str, art: SourceArtifact) -> bool:
    """Method 2 (Facebook): raw-HTML og tags (no JS) as an independent signal."""
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            art.errors.append(f"httpx-og: HTTP {r.status_code}")
            return False
        _, facts = extract_text(r.text)
    except Exception as exc:
        art.errors.append(f"httpx-og: {type(exc).__name__}")
        return False
    blurb = "\n".join(v for v in (facts.get("og_title"), facts.get("og_description"),
                                  facts.get("meta_description")) if v)
    if blurb:
        art.text_blocks.append({"label": "facebook og-summary (raw html)",
                                "text": blurb, "method": "httpx-og"})
        for key in ("og_title", "og_description", "meta_description"):
            if facts.get(key):
                _prov(art, key + "_rawhtml", "httpx-og")
        return True
    return False


def collect(url: str, job_dir: Path) -> SourceArtifact:
    platform = classify_url(url)
    art = SourceArtifact(url=url, platform=platform, method="multi")
    attempted, succeeded = [], []

    # Method 1: logged-out render (both platforms)
    attempted.append("playwright-loggedout")
    if _method_playwright(url, job_dir, art):
        succeeded.append("playwright-loggedout")

    # Method 2: platform-specific unofficial route
    if platform == INSTAGRAM:
        attempted.append("instaloader")
        if _method_instaloader(url, art):
            succeeded.append("instaloader")
    elif platform == FACEBOOK:
        attempted.append("httpx-og")
        if _method_httpx_og(url, art):
            succeeded.append("httpx-og")

    # Method 3: screenshot → vision (executed in the P6 vision stage)
    attempted.append("screenshot-vision")
    if art.screenshots:
        succeeded.append("screenshot-vision(queued)")

    art.facts["methods_attempted"] = attempted
    art.facts["methods_succeeded"] = succeeded
    art.ok = bool(art.text_blocks) or any(
        art.facts.get(k) for k in ("followers", "og_description"))
    return art
