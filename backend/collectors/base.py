"""Shared collector contract.

Every collector takes (url, job_dir) and returns a SourceArtifact — one normalized
shape so the vision and synthesis stages are source-agnostic. Real collectors
(website/youtube/social) register themselves in P3–P5; until then the registry
falls back to a stub so the pipeline runs end to end."""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..config import settings

# Platform tags
YOUTUBE = "youtube"
INSTAGRAM = "instagram"
FACEBOOK = "facebook"
LINKEDIN = "linkedin"
GITHUB = "github"
MEDIUM = "medium"
SUBSTACK = "substack"
REDDIT = "reddit"
TWITTER = "twitter"
NEWS = "news"
LINKTREE = "linktree"
PATREON = "patreon"
KOFI = "kofi"
TWITCH = "twitch"
BLUESKY = "bluesky"
WEBSITE = "website"


@dataclass
class SourceArtifact:
    url: str
    platform: str
    ok: bool = False
    method: str = ""
    text_blocks: list[dict] = field(default_factory=list)   # {label, text}
    images: list[dict] = field(default_factory=list)         # {url, local_path}
    screenshots: list[str] = field(default_factory=list)     # local paths
    vision_notes: list[dict] = field(default_factory=list)   # {ref, description}
    facts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    """Viewport screenshot of the public page for the vision stage. Never raises:
    a failure lands in art.errors."""
    from playwright.sync_api import sync_playwright

    from .website import UA  # imported here: website.py imports this module

    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or art.platform
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=UA, locale="en-US",
                                       viewport={"width": 1280, "height": 1400}).new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(settings.social_page_wait_ms)
            shot = shots / f"{slug}.png"
            page.screenshot(path=str(shot))
            art.screenshots.append(str(shot))
            browser.close()
    except Exception as exc:
        art.errors.append(f"{art.platform} screenshot: {type(exc).__name__}")


def classify_url(url: str) -> str:
    host = (urlparse(url if "://" in url else "https://" + url).netloc or "").lower()
    for p in ("www.", "m.", "mobile.", "mbasic."):
        host = host.removeprefix(p)
    if host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com"):
        return YOUTUBE
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return INSTAGRAM
    if host in ("facebook.com", "fb.com", "fb.watch") or host.endswith(".facebook.com"):
        return FACEBOOK
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return LINKEDIN
    if host == "github.com":
        return GITHUB
    if host == "medium.com" or host.endswith(".medium.com"):
        return MEDIUM
    if host == "substack.com" or host.endswith(".substack.com"):
        return SUBSTACK
    if host == "reddit.com" or host.endswith(".reddit.com"):
        return REDDIT
    if host in ("twitter.com", "x.com") or host.endswith(".twitter.com"):
        return TWITTER
    if host == "news.google.com":
        return NEWS
    if host in ("linktr.ee",):
        return LINKTREE
    if host in ("linktree.com",) or host.endswith(".linktr.ee"):
        return LINKTREE
    if host in ("beacons.ai", "stan.store") or host.endswith((".beacons.ai", ".stan.store")):
        return LINKTREE
    if host == "patreon.com" or host.endswith(".patreon.com"):
        return PATREON
    if host in ("ko-fi.com", "buymeacoffee.com") or host.endswith((".ko-fi.com", ".buymeacoffee.com")):
        return KOFI
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return TWITCH
    if host == "bsky.app":
        return BLUESKY
    return WEBSITE


Collector = Callable[[str, Path], SourceArtifact]
_REGISTRY: dict[str, Collector] = {}


def register(platform: str, fn: Collector) -> None:
    _REGISTRY[platform] = fn


def _stub(url: str, job_dir: Path) -> SourceArtifact:
    return SourceArtifact(
        url=url, platform=classify_url(url), ok=False, method="stub",
        errors=["collector not implemented yet"],
    )


def get_collector(platform: str) -> Collector:
    return _REGISTRY.get(platform, _stub)
