"""Offline tests for the YouTubeCollector: API mocked, no network, no quota."""

import pytest

from backend.collectors import youtube as yc
from backend.config import settings
from backend.youtube.client import YouTubeError


@pytest.fixture(autouse=True)
def no_images(monkeypatch):
    monkeypatch.setattr(yc, "_save_image", lambda *a, **k: False)
    # keyed by default: without a key collect() takes the RSS route (real network)
    monkeypatch.setattr(settings, "youtube_api_key", "k")


CHANNEL = {"items": [{
    "id": "UC" + "x" * 22,
    "snippet": {"title": "Acme Films", "description": "We make documentaries.",
                "customUrl": "@acmefilms", "country": "US",
                "thumbnails": {"high": {"url": "http://t/avatar.jpg"}}},
    "statistics": {"subscriberCount": "12300", "viewCount": "999", "videoCount": "42"},
    "brandingSettings": {"channel": {"keywords": "docs film history"}},
    "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Film"]},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUxx"}},
}]}

PLAYLIST = {"items": [{"contentDetails": {"videoId": "vid0000000{}".format(i)}}
                      for i in range(3)]}

VIDEOS = {"items": [{
    "id": f"vid0000000{i}",
    "snippet": {"title": f"Episode {i}", "channelId": "UC" + "x" * 22,
                "description": f"Description of episode {i}.",
                "thumbnails": {"high": {"url": f"http://t/{i}.jpg"}}},
} for i in range(3)]}

COMMENTS = {"items": [{"snippet": {"topLevelComment": {"snippet": {
    "authorDisplayName": "viewer", "textDisplay": "Great episode!"}}}}]}


def _mock_get(responses):
    def fake(client, resource, **params):
        result = responses.get(resource)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise AssertionError(f"unexpected API call: {resource} {params}")
        return result
    return fake


def test_channel_flow_builds_content_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(yc, "_get", _mock_get({
        "channels": CHANNEL, "playlistItems": PLAYLIST,
        "videos": VIDEOS, "commentThreads": COMMENTS,
    }))
    monkeypatch.setattr(yc, "fetch_transcript",
                        lambda vid: (f"spoken words of {vid}", None))

    art = yc.collect("https://youtube.com/@acmefilms", tmp_path)

    assert art.ok and art.platform == "youtube"
    labels = [b["label"] for b in art.text_blocks]
    assert "channel: Acme Films" in labels
    assert sum(l.startswith("video: ") for l in labels) == 3
    assert sum(l.startswith("transcript: ") for l in labels) == 3
    assert sum(l.startswith("comments: ") for l in labels) == 3
    ch_text = art.text_blocks[0]["text"]
    assert "We make documentaries." in ch_text and "docs film history" in ch_text
    assert art.facts["channel_title"] == "Acme Films"
    assert art.facts["topics"] == ["Film"]
    assert art.errors == []


def test_video_flow_includes_channel_context(monkeypatch, tmp_path):
    single = {"items": [VIDEOS["items"][0]]}
    monkeypatch.setattr(yc, "_get", _mock_get({
        "videos": single, "channels": CHANNEL, "commentThreads": COMMENTS,
    }))
    monkeypatch.setattr(yc, "fetch_transcript", lambda vid: (None, "NoTranscriptFound"))

    art = yc.collect("https://youtu.be/vid00000000", tmp_path)

    labels = [b["label"] for b in art.text_blocks]
    assert "video: Episode 0" in labels
    assert "channel: Acme Films" in labels          # channel context attached
    assert any("transcript vid00000000" in e for e in art.errors)
    assert art.ok


def test_comments_disabled_is_tolerated(monkeypatch, tmp_path):
    monkeypatch.setattr(yc, "_get", _mock_get({
        "channels": CHANNEL, "playlistItems": PLAYLIST, "videos": VIDEOS,
        "commentThreads": YouTubeError(403, "commentsDisabled", "off"),
    }))
    monkeypatch.setattr(yc, "fetch_transcript", lambda vid: (None, "err"))

    art = yc.collect("@acmefilms", tmp_path)
    assert art.ok
    assert not any("commentsDisabled" in e for e in art.errors)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Acme Films</title>
 <entry>
  <yt:videoId>vid00000001</yt:videoId>
  <title>Episode 1</title>
  <published>2026-11-13T14:00:08+00:00</published>
  <media:group>
   <media:title>Episode 1</media:title>
   <media:thumbnail url="https://i3.ytimg.com/vi/vid00000001/hqdefault.jpg"/>
   <media:description>Description of episode 1.</media:description>
   <media:community><media:statistics views="842097"/></media:community>
  </media:group>
 </entry>
</feed>"""


def _rss_client(pages, seen):
    class Resp:
        def __init__(self, text, status_code=200):
            self.text, self.status_code = text, status_code

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            seen.append(url)
            return next((Resp(t) for key, t in pages.items() if key in url), Resp("", 404))

    return Client


def test_no_api_key_falls_back_to_channel_rss(monkeypatch, tmp_path):
    cid = "UC" + "x" * 22
    seen = []
    monkeypatch.setattr(settings, "youtube_api_key", "")
    monkeypatch.setattr(yc, "_get", lambda *a, **k: pytest.fail("Data API called without a key"))
    monkeypatch.setattr(yc, "fetch_transcript", lambda vid: ("hello transcript", None))
    monkeypatch.setattr(yc.httpx, "Client", _rss_client({
        "/@acmefilms": '<link rel="alternate" type="application/rss+xml" '
                       f'href="https://www.youtube.com/feeds/videos.xml?channel_id={cid}">',
        f"feeds/videos.xml?channel_id={cid}": RSS}, seen))
    art = yc.collect("https://www.youtube.com/@acmefilms", tmp_path)

    assert art.ok and not art.errors
    assert seen == ["https://www.youtube.com/@acmefilms",
                    f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"]
    assert art.method == "rss+timedtext" and "reduced" in art.facts["view"]
    assert art.facts["channel_title"] == "Acme Films" and art.facts["channel_id"] == cid
    blocks = {b["label"]: b["text"] for b in art.text_blocks}
    assert blocks["recent videos (RSS, latest 1)"] == "- Episode 1 (2026-11-13, 842097 views)"
    assert blocks["video: Episode 1"] == "Description of episode 1."
    assert blocks["transcript: Episode 1"] == "hello transcript"


def test_no_api_key_unresolvable_channel_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "youtube_api_key", "")
    monkeypatch.setattr(yc.httpx, "Client", _rss_client({}, []))
    art = yc.collect("@whoever", tmp_path)
    assert art.ok is False
    assert any("channelNotFound" in e for e in art.errors)


def test_bad_input_contained(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "k")
    art = yc.collect("https://youtube.com/playlist?list=PL123", tmp_path)
    assert art.ok is False
    assert any("badInput" in e for e in art.errors)


def test_transcript_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "yt_transcript_char_cap", 50)
    monkeypatch.setattr(yc, "_get", _mock_get({
        "channels": CHANNEL, "playlistItems": PLAYLIST,
        "videos": VIDEOS, "commentThreads": COMMENTS,
    }))
    monkeypatch.setattr(yc, "fetch_transcript", lambda vid: ("x" * 500, None))
    art = yc.collect("@acmefilms", tmp_path)
    t = next(b for b in art.text_blocks if b["label"].startswith("transcript"))
    assert len(t["text"]) <= 50 + len("\n[...truncated]")