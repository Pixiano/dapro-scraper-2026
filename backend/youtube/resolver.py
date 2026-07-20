"""Input resolution WITHOUT search.list (which costs 100 units).

Accepts channel IDs, @handles, legacy usernames, bare names, and every common
YouTube URL form, and resolves to a channel or video via channels.list /
videos.list lookups (1 unit each; worst case 2 for a bare name). Successful
resolutions are cached 30 days (IDs are stable); misses are negative-cached."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .. import cache
from . import client
from .client import YouTubeError

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")


@dataclass
class ParsedInput:
    kind: str  # channel_id | handle | username | name | video_id
    value: str


def _bad(msg: str) -> YouTubeError:
    return YouTubeError(400, "badInput", msg)


def parse_input(raw: str) -> ParsedInput:
    q = (raw or "").strip()
    if not q:
        raise _bad("Empty input.")
    lowered = q.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered or lowered.startswith(("http://", "https://")):
        return _parse_url(q)
    if CHANNEL_ID_RE.match(q):
        return ParsedInput("channel_id", q)
    if q.startswith("@"):
        return ParsedInput("handle", q)
    return ParsedInput("name", q)


def _parse_url(q: str) -> ParsedInput:
    if not q.lower().startswith(("http://", "https://")):
        q = "https://" + q
    u = urlparse(q)
    host = u.netloc.lower()
    for prefix in ("www.", "m.", "music."):
        host = host.removeprefix(prefix)
    segs = [s for s in u.path.split("/") if s]

    if host == "youtu.be":
        if segs and VIDEO_ID_RE.match(segs[0]):
            return ParsedInput("video_id", segs[0])
        raise _bad("youtu.be link without a valid video ID.")
    if host != "youtube.com":
        raise _bad(f"Not a YouTube URL: {host}")
    if not segs:
        raise _bad("YouTube URL has no path to resolve.")

    first = segs[0]
    if first == "watch":
        vid = parse_qs(u.query).get("v", [None])[0]
        if vid and VIDEO_ID_RE.match(vid):
            return ParsedInput("video_id", vid)
        raise _bad("watch URL without a valid ?v= video ID.")
    if first in ("shorts", "live", "embed") and len(segs) > 1 and VIDEO_ID_RE.match(segs[1]):
        return ParsedInput("video_id", segs[1])
    if first == "channel" and len(segs) > 1:
        if CHANNEL_ID_RE.match(segs[1]):
            return ParsedInput("channel_id", segs[1])
        raise _bad(f"Invalid channel ID in URL: {segs[1]}")
    if first == "user" and len(segs) > 1:
        return ParsedInput("username", segs[1])
    if first == "c" and len(segs) > 1:
        return ParsedInput("name", segs[1])
    if first.startswith("@"):
        return ParsedInput("handle", first)
    raise _bad(f"Unrecognized YouTube URL form: /{first}")


def _thumb(snippet: dict) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for k in ("medium", "high", "default"):
        if k in thumbs:
            return thumbs[k].get("url")
    return None


async def resolve(raw: str, force: bool = False):
    """Returns (result, cached, fetched_at). result = {type, id, title, thumbnail, ...}."""
    parsed = parse_input(raw)
    key = f"resolve:{parsed.kind}:{parsed.value.lower()}"

    async def produce():
        if parsed.kind == "video_id":
            data = await client.api_get("videos", part="snippet", id=parsed.value)
            items = data.get("items") or []
            if not items:
                raise YouTubeError(404, "videoNotFound",
                                   f"No video with ID {parsed.value}.", negative=True)
            sn = items[0]["snippet"]
            return {
                "type": "video", "id": parsed.value, "title": sn.get("title"),
                "thumbnail": _thumb(sn), "channelId": sn.get("channelId"),
                "channelTitle": sn.get("channelTitle"),
            }

        attempts = {
            "channel_id": [{"id": parsed.value}],
            "handle": [{"forHandle": parsed.value}],
            "username": [{"forUsername": parsed.value}],
            "name": [{"forHandle": "@" + parsed.value}, {"forUsername": parsed.value}],
        }[parsed.kind]
        for params in attempts:
            data = await client.api_get("channels", part="snippet", **params)
            items = data.get("items") or []
            if items:
                it = items[0]
                sn = it["snippet"]
                return {
                    "type": "channel", "id": it["id"], "title": sn.get("title"),
                    "thumbnail": _thumb(sn), "customUrl": sn.get("customUrl"),
                }
        raise YouTubeError(
            404, "channelNotFound",
            f"No channel found for '{raw.strip()}'. Try the exact @handle or a channel URL "
            "(name lookup does not use YouTube search).",
            negative=True,
        )

    return await cache.cached_call(key, "static", produce, force=force)
