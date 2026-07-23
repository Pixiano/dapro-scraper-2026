"""Bespoke Linktree / link-in-bio collector.

Linktree, Beacons, and stan.store pages are link-in-bio hubs: a creator's
curated list of outbound links (YouTube, Instagram, Patreon, merch, Discord,
...) rendered client-side. The value here is *extraction*, not recursion —
we pull out every outbound link and its visible label as a structured fact
and a text block so a human (or later stage) can decide what to follow up
on. We deliberately do NOT enqueue the discovered links as new collector
jobs — that would risk uncontrolled job explosion.

The page is JS-rendered, so we use Playwright (not plain httpx) to get the
final DOM, then reuse the same readability pass as the website collector for
a title/description fallback."""

import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..config import settings
from .base import LINKTREE, SourceArtifact
from .website import UA, extract_text

_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "#")


def _host(url: str) -> str:
    h = (urlparse(url).netloc or "").lower()
    for p in ("www.", "m.", "mobile.", "mbasic."):
        h = h.removeprefix(p)
    return h


def _render(url: str, job_dir: Path, art: SourceArtifact) -> str | None:
    """Render the page with Playwright, capture a screenshot, and return the
    final HTML (or None on total failure — recorded into art.errors)."""
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "linktree"
    html = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, locale="en-US",
                                      viewport={"width": 1280, "height": 1400})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(settings.social_page_wait_ms)
            html = page.content()
            try:
                shot = shots / f"{slug}.png"
                page.screenshot(path=str(shot))
                art.screenshots.append(str(shot))
            except Exception as exc:
                art.errors.append(f"linktree screenshot: {type(exc).__name__}")
            browser.close()
    except Exception as exc:
        art.errors.append(f"linktree render: {type(exc).__name__}: {exc}")
    return html


def _profile_name_and_bio(soup: BeautifulSoup, facts: dict) -> tuple[str | None, str | None]:
    name = None
    for sel in ("h1", "[data-testid='ProfileTitle']", "[class*='profile'] h1"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            name = el.get_text(strip=True)
            break
    if not name:
        name = facts.get("og_title") or facts.get("title") or None

    bio = None
    for sel in ("[data-testid='ProfileDescription']", "p"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            bio = el.get_text(strip=True)
            break
    if not bio:
        bio = facts.get("og_description") or facts.get("meta_description") or None

    return name, bio


def _discovered_links(soup: BeautifulSoup, page_url: str) -> list[dict]:
    self_host = _host(page_url)
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.lower().startswith(_SKIP_SCHEMES):
            continue
        if not href.lower().startswith(("http://", "https://")):
            continue
        if _host(href) == self_host:
            continue
        if href in seen:
            continue
        seen.add(href)
        label = a.get_text(" ", strip=True)[:120]
        out.append({"label": label, "url": href})
        if len(out) >= settings.linktree_links_cap:
            break
    return out


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=LINKTREE, method="playwright")
    try:
        html = _render(url, job_dir, art)
        if html:
            text, facts = extract_text(html)
            soup = BeautifulSoup(html, "lxml")

            name, bio = _profile_name_and_bio(soup, facts)
            if name:
                art.facts["profile_name"] = name
            if bio:
                art.facts["bio"] = bio

            links = _discovered_links(soup, url)
            art.facts["discovered_link_count"] = len(links)
            art.facts["discovered_links"] = links
            if links:
                lines = "\n".join(f"- {l['label']}: {l['url']}" for l in links)
                art.text_blocks.append(
                    {"label": f"discovered links ({len(links)})", "text": lines})
    except Exception as exc:
        art.errors.append(f"linktree: {type(exc).__name__}: {exc}")

    art.ok = bool(art.facts.get("discovered_link_count")) or bool(
        art.facts.get("bio") or art.facts.get("profile_name"))
    return art
