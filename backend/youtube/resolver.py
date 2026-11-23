"""Input parsing for YouTube references.

Accepts channel IDs, @handles, legacy usernames, bare names, and every common
YouTube URL form, and says which kind it is. No network."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

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
