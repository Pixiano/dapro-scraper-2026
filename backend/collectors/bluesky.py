"""Bluesky collector.

Bluesky's public AppView (public.api.bsky.app) serves profiles and author feeds
as JSON with no login or key: name, bio, follower and post counts, and recent
posts with dates and engagement counts. We read those two endpoints for facts
and text, and use Playwright only to screenshot the profile for the vision stage.

Best-effort and contained: an unknown handle is HTTP 400 ("Profile not found").
Never raises out of collect(); every failure lands in art.errors."""

from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import settings
from .base import BLUESKY, SourceArtifact, screenshot as _screenshot
from .website import UA

_API = "https://public.api.bsky.app/xrpc"


def _handle_from_url(url: str) -> str | None:
    """bsky.app/profile/<handle or did>[/post/...] -> handle."""
    parts = [p for p in urlparse(url if "://" in url else "https://" + url).path.split("/") if p]
    return parts[1].lstrip("@") if len(parts) > 1 and parts[0] == "profile" else None


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=BLUESKY, method="bluesky_api")
    handle = _handle_from_url(url)
    if not handle:
        art.errors.append("bluesky: no /profile/<handle> in URL")
        return art

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(f"{_API}/app.bsky.actor.getProfile", params={"actor": handle})
            if r.status_code != 200:
                art.errors.append(f"bluesky: profile fetch HTTP {r.status_code}")
                _screenshot(url, job_dir, art)
                return art
            p = r.json()
            for key, fact in (("handle", "handle"), ("displayName", "name"),
                              ("description", "bio"), ("followersCount", "followers"),
                              ("followsCount", "following"), ("postsCount", "posts"),
                              ("createdAt", "created_at")):
                if p.get(key) not in (None, ""):
                    art.facts[fact] = p[key]
            if (p.get("description") or "").strip():
                art.text_blocks.append({"label": "bio", "text": p["description"].strip()})

            # limit: the API allows 1-100
            rf = c.get(f"{_API}/app.bsky.feed.getAuthorFeed",
                       params={"actor": handle, "filter": "posts_no_replies",
                               "limit": max(1, min(settings.bluesky_posts, 100))})
            if rf.status_code != 200:
                art.errors.append(f"bluesky: feed fetch HTTP {rf.status_code}")
            else:
                lines = []
                for item in rf.json().get("feed") or []:
                    post = item.get("post") or {}
                    rec = post.get("record") or {}
                    text = (rec.get("text") or "").strip() or "(no text)"
                    author = (post.get("author") or {}).get("handle")
                    repost = f" (repost of @{author})" if item.get("reason") else ""
                    lines.append(
                        f"- {(rec.get('createdAt') or '')[:10]}{repost}: {text} "
                        f"[{post.get('likeCount', 0)} likes, {post.get('repostCount', 0)} reposts, "
                        f"{post.get('replyCount', 0)} replies, {post.get('quoteCount', 0)} quotes]")
                if lines:
                    art.text_blocks.append({"label": f"recent posts ({len(lines)})",
                                            "text": "\n".join(lines)})
    except Exception as exc:
        art.errors.append(f"bluesky: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    art.ok = bool(art.text_blocks) or "followers" in art.facts
    return art
