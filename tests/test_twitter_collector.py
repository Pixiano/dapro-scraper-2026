"""Offline tests for the bespoke X/Twitter collector (og parsing, no network/browser)."""

import pytest

from backend.collectors import twitter as tw
from backend.collectors.base import TWITTER, classify_url


PROFILE_HTML = """
<html><head>
  <title>Jane Dev (@janedev) / X</title>
  <meta property="og:title" content="Jane Dev (@janedev)">
  <meta property="og:description" content="Systems engineer. Rust, distributed
  databases, and bad coffee. Opinions my own.">
</head><body><div id="react-root"></div></body></html>
"""

BARE_HTML = "<html><head><title>X</title></head><body></body></html>"


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("no json")


def _make_client(resp):
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *a, **k):
            return resp

    return Client


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(tw, "_screenshot", lambda *a, **k: None)


def test_classify_routes_twitter():
    assert classify_url("https://x.com/janedev") == TWITTER
    assert classify_url("https://twitter.com/janedev") == TWITTER


@pytest.mark.parametrize("url,expected", [
    ("https://x.com/janedev", "janedev"),
    ("https://twitter.com/janedev/", "janedev"),
    ("https://x.com/@janedev", "janedev"),
    ("https://x.com/home", None),
    ("https://x.com/", None),
])
def test_handle_from_url(url, expected):
    assert tw._handle_from_url(url) == expected


def test_og_bio_parsed(monkeypatch, tmp_path):
    monkeypatch.setattr(tw.httpx, "Client", _make_client(_Resp(text=PROFILE_HTML)))
    art = tw.collect("https://x.com/janedev", tmp_path)

    assert art.ok is True
    assert art.platform == "twitter"
    assert art.method == "og"

    f = art.facts
    assert f["handle"] == "janedev"
    assert f["og_title"] == "Jane Dev (@janedev)"
    assert "Systems engineer" in f["bio"]

    block = next(b for b in art.text_blocks if b["label"] == "x/twitter profile")
    assert "Jane Dev" in block["text"]
    assert "distributed" in block["text"]


def test_twitter_com_host_works(monkeypatch, tmp_path):
    monkeypatch.setattr(tw.httpx, "Client", _make_client(_Resp(text=PROFILE_HTML)))
    art = tw.collect("https://twitter.com/janedev", tmp_path)
    assert art.ok is True
    assert art.facts["handle"] == "janedev"


def test_no_og_content_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(tw.httpx, "Client", _make_client(_Resp(text=BARE_HTML)))
    art = tw.collect("https://x.com/janedev", tmp_path)

    assert art.ok is False
    assert art.text_blocks == []
    assert any("no og content" in e for e in art.errors)


def test_http_error_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(tw.httpx, "Client", _make_client(_Resp(status_code=404)))
    art = tw.collect("https://x.com/ghost", tmp_path)

    assert art.ok is False
    assert any("404" in e for e in art.errors)


def test_transport_error_contained(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no network")

    monkeypatch.setattr(tw.httpx, "Client", Boom)
    art = tw.collect("https://x.com/janedev", tmp_path)
    assert art.ok is False
    assert any("RuntimeError" in e for e in art.errors)
