"""Aggregation layer: quota-aware call sequences over the raw client.

Every public function returns (payload, cached: bool, fetched_at_iso)."""

from .. import cache
from . import client
from .client import YouTubeError

# Every part accessible with a plain API key. auditDetails/contentOwnerDetails
# (channels) and fileDetails/processingDetails/suggestions (videos) need OAuth.
CHANNEL_PARTS = "snippet,statistics,contentDetails,topicDetails,brandingSettings,status,localizations"
VIDEO_PARTS = (
    "snippet,statistics,contentDetails,status,player,topicDetails,"
    "recordingDetails,liveStreamingDetails,localizations,paidProductPlacementDetails"
)


def _thumb(snippet: dict) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for k in ("medium", "high", "default"):
        if k in thumbs:
            return thumbs[k].get("url")
    return None


def _video_summary(v: dict, cats: dict) -> dict:
    sn = v.get("snippet") or {}
    st = v.get("statistics") or {}
    cd = v.get("contentDetails") or {}
    cid = sn.get("categoryId")
    return {
        "id": v.get("id"),
        "title": sn.get("title"),
        "publishedAt": sn.get("publishedAt"),
        "thumbnail": _thumb(sn),
        "duration": cd.get("duration"),
        "viewCount": st.get("viewCount"),
        "likeCount": st.get("likeCount"),
        "commentCount": st.get("commentCount"),
        "categoryId": cid,
        "categoryName": cats.get(cid),
        "live": sn.get("liveBroadcastContent"),
    }


def _derive_genre(channel: dict, videos: list[dict], cats: dict) -> dict:
    # Channels have no categoryId; topicDetails.topicCategories (Wikipedia URLs)
    # plus the modal category of recent videos is the closest "genre".
    topics = [
        url.rsplit("/", 1)[-1].replace("_", " ")
        for url in (channel.get("topicDetails") or {}).get("topicCategories") or []
    ]
    counts: dict[str, int] = {}
    for v in videos:
        cid = (v.get("snippet") or {}).get("categoryId")
        if cid:
            counts[cid] = counts.get(cid, 0) + 1
    modal = max(counts, key=counts.get) if counts else None
    return {"topics": topics, "modalCategoryId": modal, "modalCategoryName": cats.get(modal)}


async def get_category_map() -> dict:
    async def produce():
        data = await client.api_get("videoCategories", part="snippet", regionCode="US")
        return {it["id"]: it["snippet"]["title"] for it in data.get("items") or []}

    payload, _, _ = await cache.cached_call("videoCategories:US", "static", produce)
    return payload


def _cache_video(item: dict, cats: dict) -> None:
    # Videos fetched in bulk are individually cached so the detail view is free.
    cid = (item.get("snippet") or {}).get("categoryId")
    cache.set(f"video:{item['id']}", "snapshot",
              {"video": item, "categoryName": cats.get(cid)})


async def _fetch_upload_page(uploads_id: str, page_token: str | None, cats: dict) -> dict:
    try:
        pi = await client.api_get(
            "playlistItems", part="contentDetails", playlistId=uploads_id,
            maxResults=50, pageToken=page_token,
        )
    except YouTubeError as exc:
        if exc.reason == "playlistNotFound":  # channel with zero uploads
            return {"videos": [], "summaries": [], "nextPageToken": None, "lastUpload": None}
        raise
    items = pi.get("items") or []
    ids = [it["contentDetails"]["videoId"] for it in items]
    videos = []
    if ids:
        v = await client.api_get("videos", part=VIDEO_PARTS, id=",".join(ids), maxResults=50)
        videos = v.get("items") or []
        videos.sort(key=lambda x: (x.get("snippet") or {}).get("publishedAt") or "", reverse=True)
        for item in videos:
            _cache_video(item, cats)
    last = items[0]["contentDetails"].get("videoPublishedAt") if items else None
    return {
        "videos": videos,
        "summaries": [_video_summary(v, cats) for v in videos],
        "nextPageToken": pi.get("nextPageToken"),
        "lastUpload": last,
    }


async def channel_snapshot(channel_id: str, force: bool = False):
    async def produce():
        data = await client.api_get("channels", part=CHANNEL_PARTS, id=channel_id)
        items = data.get("items") or []
        if not items:
            raise YouTubeError(404, "channelNotFound",
                               f"No channel with ID {channel_id}.", negative=True)
        ch = items[0]
        cats = await get_category_map()
        uploads_id = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        cache.set(f"uploads:{channel_id}", "static", {"uploadsId": uploads_id})

        page = (await _fetch_upload_page(uploads_id, None, cats)) if uploads_id else {
            "videos": [], "summaries": [], "nextPageToken": None, "lastUpload": None}
        playlists = await client.api_get(
            "playlists", part="snippet,contentDetails,status", channelId=channel_id, maxResults=50)
        sections = await client.api_get(
            "channelSections", part="snippet,contentDetails", channelId=channel_id)

        return {
            "channel": ch,  # raw resource, all public parts
            "genre": _derive_genre(ch, page["videos"], cats),
            "lastUpload": page["lastUpload"],
            "recentVideos": page["summaries"],
            "recentVideosNextPage": page["nextPageToken"],
            "playlists": {"items": playlists.get("items") or [],
                          "nextPageToken": playlists.get("nextPageToken")},
            "sections": sections.get("items") or [],
        }

    return await cache.cached_call(f"channel:{channel_id}", "snapshot", produce, force=force)


async def _uploads_id(channel_id: str) -> str:
    async def produce():
        data = await client.api_get("channels", part="contentDetails", id=channel_id)
        items = data.get("items") or []
        if not items:
            raise YouTubeError(404, "channelNotFound",
                               f"No channel with ID {channel_id}.", negative=True)
        rp = (items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}
        return {"uploadsId": rp.get("uploads")}

    payload, _, _ = await cache.cached_call(f"uploads:{channel_id}", "static", produce)
    uploads = payload.get("uploadsId")
    if not uploads:
        raise YouTubeError(404, "playlistNotFound",
                           "Channel has no uploads playlist.", negative=True)
    return uploads


async def channel_videos(channel_id: str, page_token: str | None, force: bool = False):
    uploads = await _uploads_id(channel_id)
    key = f"videos:{channel_id}:{page_token or 'p1'}"

    async def produce():
        cats = await get_category_map()
        page = await _fetch_upload_page(uploads, page_token, cats)
        return {"videos": page["summaries"], "nextPageToken": page["nextPageToken"]}

    return await cache.cached_call(key, "snapshot", produce, force=force)


async def video_detail(video_id: str, force: bool = False):
    async def produce():
        data = await client.api_get("videos", part=VIDEO_PARTS, id=video_id)
        items = data.get("items") or []
        if not items:
            raise YouTubeError(404, "videoNotFound",
                               f"No video with ID {video_id}.", negative=True)
        cats = await get_category_map()
        cid = (items[0].get("snippet") or {}).get("categoryId")
        return {"video": items[0], "categoryName": cats.get(cid)}

    return await cache.cached_call(f"video:{video_id}", "snapshot", produce, force=force)


async def comments(video_id: str, page_token: str | None, order: str = "relevance",
                   force: bool = False):
    order = order if order in ("relevance", "time") else "relevance"
    key = f"comments:{video_id}:{order}:{page_token or 'p1'}"

    async def produce():
        return await client.api_get(
            "commentThreads", part="snippet,replies", videoId=video_id,
            maxResults=20, order=order, textFormat="plainText", pageToken=page_token)

    return await cache.cached_call(key, "list", produce, force=force)


async def comment_replies(parent_id: str, page_token: str | None, force: bool = False):
    key = f"replies:{parent_id}:{page_token or 'p1'}"

    async def produce():
        return await client.api_get(
            "comments", part="snippet", parentId=parent_id,
            maxResults=50, textFormat="plainText", pageToken=page_token)

    return await cache.cached_call(key, "list", produce, force=force)


async def captions(video_id: str, force: bool = False):
    async def produce():  # 50 quota units — route gates this behind confirm=true
        return await client.api_get("captions", part="snippet", videoId=video_id)

    return await cache.cached_call(f"captions:{video_id}", "captions", produce, force=force)


async def activities(channel_id: str, force: bool = False):
    async def produce():
        return await client.api_get(
            "activities", part="snippet,contentDetails", channelId=channel_id, maxResults=25)

    return await cache.cached_call(f"activities:{channel_id}", "list", produce, force=force)


async def subscriptions(channel_id: str, force: bool = False):
    async def produce():  # usually 403 subscriptionForbidden (private) — negative-cached
        return await client.api_get(
            "subscriptions", part="snippet", channelId=channel_id, maxResults=50)

    return await cache.cached_call(f"subs:{channel_id}", "list", produce, force=force)


async def search(q: str, stype: str = "channel", force: bool = False):
    stype = stype if stype in ("channel", "video", "playlist") else "channel"
    key = f"search:{stype}:{q.strip().lower()}"

    async def produce():  # 100 quota units — route gates this behind confirm=true
        return await client.api_get("search", part="snippet", q=q, type=stype, maxResults=25)

    return await cache.cached_call(key, "list", produce, force=force)
