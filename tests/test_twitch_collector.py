"""Offline tests for the bespoke Twitch collector (og parsing, no real browser)."""

import pytest

from backend.collectors import twitch as tv
from backend.collectors.base import TWITCH, classify_url

PROFILE_HTML = """
<html><head>
  <title>SomeStreamer - Twitch</title>
  <meta property="og:title" content="SomeStreamer - Twitch">
  <meta property="og:description" content="Variety streamer. Speedruns, co-op
  nights, and the occasional cooking stream. Come say hi!">
</head><body>
  <div>SomeStreamer</div>
</body></html>
"""

PROFILE_HTML_WITH_SIGNALS = """
<html><head>
  <title>BigChannel - Twitch</title>
  <meta property="og:title" content="BigChannel - Twitch">
  <meta property="og:description" content="Daily variety streams.">
</head><body>
  <div class="channel-header">
    <span>BigChannel</span>
    <span class="tw-badge">LIVE</span>
  </div>
  <div>42,000 followers</div>
</body></html>
"""

BARE_HTML = "<html><head><title>Twitch</title></head><body></body></html>"


@pytest.fixture
def canned(monkeypatch):
    """Monkeypatch the module's render/content function to return canned HTML."""
    def _set(html):
        monkeypatch.setattr(tv, "_render", lambda url, job_dir, art: html)
    return _set


def test_classify_routes_twitch():
    assert classify_url("https://www.twitch.tv/somestreamer") == TWITCH


def test_channel_from_url():
    assert tv._channel_from_url("https://www.twitch.tv/somestreamer") == "somestreamer"
    assert tv._channel_from_url("https://twitch.tv/somestreamer/about") == "somestreamer"
    assert tv._channel_from_url("https://twitch.tv/") is None
    assert tv._channel_from_url("https://twitch.tv") is None


def test_happy_path_og_bio_parsed(canned, tmp_path):
    canned(PROFILE_HTML)
    art = tv.collect("https://www.twitch.tv/somestreamer", tmp_path)

    assert art.ok is True
    assert art.platform == TWITCH
    assert art.method == "og"

    f = art.facts
    assert f["channel_name"] == "somestreamer"
    assert "Variety streamer" in f["bio"]

    block = next(b for b in art.text_blocks if b["label"] == "twitch profile")
    assert "SomeStreamer" in block["text"]
    assert "Variety streamer" in block["text"]


def test_followers_and_live_extracted_when_present(canned, tmp_path):
    canned(PROFILE_HTML_WITH_SIGNALS)
    art = tv.collect("https://www.twitch.tv/bigchannel", tmp_path)

    assert art.ok is True
    assert art.facts["followers"] == 42000
    assert art.facts["is_live"] is True


def test_followers_and_live_absent_when_not_present(canned, tmp_path):
    canned(PROFILE_HTML)
    art = tv.collect("https://www.twitch.tv/somestreamer", tmp_path)

    # No follower count or live signal in this HTML: keys must simply be
    # absent, never defaulted to 0/False (a stale/guessed live status is
    # worse than no claim at all).
    assert "followers" not in art.facts
    assert "is_live" not in art.facts


def test_no_channel_path_contained(tmp_path):
    art = tv.collect("https://www.twitch.tv/", tmp_path)

    assert art.ok is False
    assert any("channel" in e for e in art.errors)
    assert art.text_blocks == []


def test_render_failure_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(tv, "_render", lambda url, job_dir, art: None)
    art = tv.collect("https://www.twitch.tv/somestreamer", tmp_path)

    assert art.ok is False
    # _render is responsible for appending its own error; simulate that too.


def test_render_exception_contained(monkeypatch, tmp_path):
    def boom(url, job_dir, art):
        art.errors.append("twitch render: RuntimeError")
        return None

    monkeypatch.setattr(tv, "_render", boom)
    art = tv.collect("https://www.twitch.tv/somestreamer", tmp_path)

    assert art.ok is False
    assert any("RuntimeError" in e for e in art.errors)
