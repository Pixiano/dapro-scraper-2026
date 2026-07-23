"""YouTubeCollector — the V1 API layer reframed for CONTENT, not stats.

Pulls what the entity actually says and publishes: channel description and
keywords, recent video titles+descriptions, transcript text (via YouTube's
public timedtext route through youtube-transcript-api — the Data API only
allows caption downloads with owner OAuth), and top comments. Thumbnails and
the avatar are saved as images for the vision stage.

Runs synchronously (worker threadpool) with its own httpx.Client, reusing the
V1 quota ledger, error parsing, and input-resolution rules. Typical cost:
~5-10 units per channel job; transcripts cost no quota."""

from pathlib import Path

import httpx

from .. import quota
from ..config import settings
from ..youtube.client import BASE, YouTubeError, _parse_error
from ..youtube.resolver import parse_input
from .base import YOUTUBE, SourceArtifact

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _get(client: httpx.Client, resource: str, **params) -> dict:
    if not settings.youtube_api_key:
        raise YouTubeError(503, "noApiKey", "YOUTUBE_API_KEY is not configured.")
    quota.charge(resource)
    params = {k: v for k, v in params.items() if v is not None}
    params["key"] = settings.youtube_api_key
    r = client.get(f"{BASE}/{resource}", params=params)
    if r.status_code == 200:
        return r.json()
    reason, message = _parse_error(r)
    raise YouTubeError(r.status_code, reason, message)


def _resolve(client: httpx.Client, url: str) -> tuple[str, str]:
    """→ ("channel"|"video", id). Same no-search.list rules as the V1 resolver."""
    parsed = parse_input(url)
    if parsed.kind == "video_id":
        return "video", parsed.value
    attempts = {
        "channel_id": [{"id": parsed.value}],
        "handle": [{"forHandle": parsed.value}],
        "username": [{"forUsername": parsed.value}],
        "name": [{"forHandle": "@" + parsed.value}, {"forUsername": parsed.value}],
    }[parsed.kind]
    for params in attempts:
        items = _get(client, "channels", part="id", **params).get("items") or []
        if items:
            return "channel", items[0]["id"]
    raise YouTubeError(404, "channelNotFound", f"No channel found for '{url}'.")


def fetch_transcript(video_id: str) -> tuple[str | None, str | None]:
    """→ (text, error). Best-effort across youtube-transcript-api versions."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, "youtube-transcript-api not installed"
    try:
        try:  # v1.x instance API
            api = YouTubeTranscriptApi()
            try:
                fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB", "hi"])
            except Exception:
                tl = api.list(video_id)  # fall back to whatever language exists
                fetched = next(iter(tl)).fetch()
            parts = [s.text for s in fetched]
        except AttributeError:  # v0.x static API
            data = YouTubeTranscriptApi.get_transcript(  # type: ignore[attr-defined]
                # Only exists on the pre-1.x static API; deliberately reached via
                # the AttributeError fallback above when a v0.x package is installed.
                video_id, languages=["en", "en-US", "en-GB", "hi"])
            parts = [d["text"] for d in data]
        text = " ".join(p.strip() for p in parts if p and p.strip())
        return (text or None), (None if text else "empty transcript")
    except Exception as exc:
        return None, type(exc).__name__


def _thumb_url(snippet: dict) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for k in ("high", "medium", "default"):
        if k in thumbs:
            return thumbs[k].get("url")
    return None


def _save_image(client: httpx.Client, url: str, dest: Path) -> bool:
    try:
        r = client.get(url)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            dest.write_bytes(r.content)
            return True
    except httpx.HTTPError:
        pass
    return False


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:cap] + "\n[...truncated]"


def _channel_block(art: SourceArtifact, ch: dict) -> None:
    sn = ch.get("snippet") or {}
    bs = (ch.get("brandingSettings") or {}).get("channel") or {}
    st = ch.get("statistics") or {}
    topics = [u.rsplit("/", 1)[-1].replace("_", " ")
              for u in (ch.get("topicDetails") or {}).get("topicCategories") or []]
    parts = []
    if sn.get("description"):
        parts.append(sn["description"])
    if bs.get("keywords"):
        parts.append(f"Channel keywords: {bs['keywords']}")
    if topics:
        parts.append(f"Topic categories: {', '.join(topics)}")
    if parts:
        art.text_blocks.append({"label": f"channel: {sn.get('title', '')}",
                                "text": "\n\n".join(parts)})
    art.facts.update({k: v for k, v in {
        "channel_title": sn.get("title"),
        "custom_url": sn.get("customUrl"),
        "country": sn.get("country"),
        "subscribers": st.get("subscriberCount"),
        "total_views": st.get("viewCount"),
        "video_count": st.get("videoCount"),
        "topics": topics or None,
    }.items() if v})


def _video_content(art: SourceArtifact, client: httpx.Client, videos: list[dict],
                   imgs_dir: Path) -> None:
    """Descriptions, transcripts, comments, thumbnails for a list of video items."""
    for i, v in enumerate(videos):
        sn = v.get("snippet") or {}
        vid, title = v.get("id"), sn.get("title", "")
        desc = (sn.get("description") or "").strip()
        if desc:
            art.text_blocks.append({"label": f"video: {title}",
                                    "text": _clip(desc, 4000)})
        if vid and i < settings.yt_transcript_videos:
            text, err = fetch_transcript(vid)
            if text:
                art.text_blocks.append(
                    {"label": f"transcript: {title}",
                     "text": _clip(text, settings.yt_transcript_char_cap)})
            elif err:
                art.errors.append(f"transcript {vid}: {err}")
        if i < settings.yt_comments_videos:
            try:
                data = _get(client, "commentThreads", part="snippet", videoId=vid,
                            order="relevance", textFormat="plainText",
                            maxResults=settings.yt_comments_per_video)
                lines = []
                for t in data.get("items") or []:
                    c = ((t.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
                    if c.get("textDisplay"):
                        lines.append(f"- {c.get('authorDisplayName', '?')}: "
                                     f"{c['textDisplay'][:400]}")
                if lines:
                    art.text_blocks.append({"label": f"comments: {title}",
                                            "text": "\n".join(lines)})
            except YouTubeError as exc:
                if exc.reason != "commentsDisabled":
                    art.errors.append(f"comments {vid}: {exc.reason}")
        if i < settings.yt_thumb_images:
            turl = _thumb_url(sn)
            if turl and _save_image(client, turl, imgs_dir / f"yt_thumb_{i}.jpg"):
                art.images.append({"url": turl,
                                   "local_path": str(imgs_dir / f"yt_thumb_{i}.jpg")})


CHANNEL_PARTS = "snippet,statistics,brandingSettings,topicDetails,contentDetails"


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=YOUTUBE, method="data_api+timedtext")
    imgs_dir = job_dir / "images"
    imgs_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": UA},
                          follow_redirects=True) as client:
            kind, ident = _resolve(client, url)

            if kind == "video":
                vitems = _get(client, "videos", part="snippet", id=ident).get("items") or []
                if not vitems:
                    raise YouTubeError(404, "videoNotFound", f"No video {ident}.")
                _video_content(art, client, vitems, imgs_dir)
                ch_id = (vitems[0].get("snippet") or {}).get("channelId")
                if ch_id:
                    citems = _get(client, "channels", part=CHANNEL_PARTS,
                                  id=ch_id).get("items") or []
                    if citems:
                        _channel_block(art, citems[0])
            else:
                citems = _get(client, "channels", part=CHANNEL_PARTS,
                              id=ident).get("items") or []
                if not citems:
                    raise YouTubeError(404, "channelNotFound", f"No channel {ident}.")
                ch = citems[0]
                _channel_block(art, ch)
                avatar = _thumb_url(ch.get("snippet") or {})
                if avatar and _save_image(client, avatar, imgs_dir / "yt_avatar.jpg"):
                    art.images.append({"url": avatar,
                                       "local_path": str(imgs_dir / "yt_avatar.jpg")})
                uploads = ((ch.get("contentDetails") or {})
                           .get("relatedPlaylists") or {}).get("uploads")
                if uploads:
                    try:
                        pi = _get(client, "playlistItems", part="contentDetails",
                                  playlistId=uploads,
                                  maxResults=settings.yt_videos_recent)
                        ids = [it["contentDetails"]["videoId"]
                               for it in pi.get("items") or []]
                        if ids:
                            vids = _get(client, "videos", part="snippet",
                                        id=",".join(ids)).get("items") or []
                            _video_content(art, client, vids, imgs_dir)
                    except YouTubeError as exc:
                        if exc.reason != "playlistNotFound":  # empty channel is fine
                            raise
    except YouTubeError as exc:
        art.errors.append(f"{exc.reason}: {exc.message}")
    except Exception as exc:
        art.errors.append(f"{type(exc).__name__}: {exc}")

    art.ok = bool(art.text_blocks)
    return art
