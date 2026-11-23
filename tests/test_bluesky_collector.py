"""Offline tests for the Bluesky collector (public AppView JSON, no network/browser).
Payloads are trimmed copies of real public.api.bsky.app responses."""

import pytest

from backend.collectors import bluesky as bs
from backend.collectors.base import BLUESKY, classify_url, get_collector

PROFILE = {
    "did": "did:plc:z72i7hdynmk6r22z27h6tvur", "handle": "bsky.app",
    "displayName": "Bluesky", "description": "official Bluesky account",
    "createdAt": "2023-04-12T04:53:57.057Z",
    "followersCount": 35695442, "followsCount": 43, "postsCount": 923,
}


def _post(handle, text, day, likes, reason=None):
    item = {"post": {
        "uri": "at://did:plc:x/app.bsky.feed.post/3n27", "author": {"handle": handle},
        "record": {"text": text, "createdAt": f"2026-11-{day}T22:03:20.663Z"},
        "replyCount": 176, "repostCount": 667, "likeCount": likes, "quoteCount": 46}}
    if reason:
        item["reason"] = {"$type": reason}
    return item


FEED = {"cursor": "c", "feed": [
    _post("bsky.app", "The moderation inbox is now available", 13, 7445),
    _post("cpsc.gov", "Outlook down Bluesky up", 12, 3684,
          reason="app.bsky.feed.defs#reasonRepost"),
]}


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _make_client(responses, seen):
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            seen.append((url, params))
            return next((r for key, r in responses.items() if key in url),
                        _Resp(status_code=404))

    return Client


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(bs, "_screenshot", lambda *a, **k: None)


def test_classify_and_registration():
    assert classify_url("https://bsky.app/profile/bsky.app") == BLUESKY
    assert get_collector(BLUESKY) is bs.collect


def test_profile_and_recent_posts(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(bs.settings, "bluesky_posts", 7)
    monkeypatch.setattr(bs.httpx, "Client", _make_client({
        "getProfile": _Resp(payload=PROFILE), "getAuthorFeed": _Resp(payload=FEED)}, seen))
    art = bs.collect("https://bsky.app/profile/bsky.app/post/3n27", tmp_path)

    assert art.ok and not art.errors and art.platform == BLUESKY
    assert seen[0] == ("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                       {"actor": "bsky.app"})
    assert seen[1][1]["limit"] == 7  # capped by config
    assert art.facts == {"handle": "bsky.app", "name": "Bluesky",
                         "bio": "official Bluesky account", "followers": 35695442,
                         "following": 43, "posts": 923,
                         "created_at": "2023-04-12T04:53:57.057Z"}
    posts = next(b["text"] for b in art.text_blocks if b["label"] == "recent posts (2)")
    assert posts.splitlines() == [
        "- 2026-11-13: The moderation inbox is now available "
        "[7445 likes, 667 reposts, 176 replies, 46 quotes]",
        "- 2026-11-12 (repost of @cpsc.gov): Outlook down Bluesky up "
        "[3684 likes, 667 reposts, 176 replies, 46 quotes]"]


def test_unknown_handle_and_bad_url_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(bs.httpx, "Client", _make_client(
        {"getProfile": _Resp(status_code=400, payload={"error": "InvalidRequest"})}, []))
    art = bs.collect("https://bsky.app/profile/nobody.bsky.social", tmp_path)
    assert art.ok is False and any("400" in e for e in art.errors)

    art = bs.collect("https://bsky.app/", tmp_path)
    assert art.ok is False and any("no /profile/" in e for e in art.errors)
