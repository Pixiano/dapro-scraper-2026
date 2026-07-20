"""Offline tests for the YouTubeCollector: API mocked, no network, no quota."""

import pytest

from backend.collectors import youtube as yc
from backend.config import settings
from backend.youtube.client import YouTubeError


@pytest.fixture(autouse=True)
def no_images(monkeypatch):
    monkeypatch.setattr(yc, "_save_image", lambda *a, **k: False)


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


def test_no_api_key_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "youtube_api_key", "")
    art = yc.collect("@whoever", tmp_path)
    assert art.ok is False
    assert any("noApiKey" in e for e in art.errors)


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