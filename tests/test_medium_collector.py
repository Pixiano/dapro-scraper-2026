"""Offline tests for the bespoke Medium collector (RSS parsing, no network/browser)."""

import pytest

from backend.collectors import medium as md
from backend.collectors.base import MEDIUM, classify_url


def _item(n: int) -> str:
    return f"""
  <item>
    <title>Post Number {n}</title>
    <link>https://medium.com/@user/post-{n}</link>
    <pubDate>Mon, 0{n} Feb 2026 10:00:00 GMT</pubDate>
    <content:encoded><![CDATA[<p>Body of post {n} with a <a href="https://x.test">link</a>.</p>]]></content:encoded>
  </item>"""


def _feed(n_items: int, title: str = "Ada Lovelace on Medium") -> str:
    items = "".join(_item(i) for i in range(1, n_items + 1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
 <channel>
  <title>{title}</title>
  <link>https://medium.com/@user</link>
  <description>Stories by Ada</description>{items}
 </channel>
</rss>"""


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _make_client(responses, seen):
    """responses: dict of URL-substring -> _Resp (or a single _Resp for any URL)."""
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kwargs):
            seen.append(url)
            if isinstance(responses, _Resp):
                return responses
            for key, resp in responses.items():
                if key in url:
                    return resp
            return _Resp(status_code=404)

    return Client


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(md, "_screenshot", lambda *a, **k: None)


def test_classify_routes_medium():
    assert classify_url("https://medium.com/@user") == MEDIUM
    assert classify_url("https://user.medium.com") == MEDIUM


@pytest.mark.parametrize("page,feed", [
    ("https://medium.com/@user", "https://medium.com/feed/@user"),
    ("https://medium.com/@user/some-post-slug-abc123", "https://medium.com/feed/@user"),
    ("https://www.medium.com/@user", "https://medium.com/feed/@user"),
    ("https://medium.com/better-programming", "https://medium.com/feed/better-programming"),
    ("https://medium.com/better-programming/an-article", "https://medium.com/feed/better-programming"),
    ("https://user.medium.com", "https://user.medium.com/feed"),
    ("https://user.medium.com/some-post", "https://user.medium.com/feed"),
])
def test_feed_url_normalisation(monkeypatch, tmp_path, page, feed):
    seen = []
    monkeypatch.setattr(md.httpx, "Client", _make_client(_Resp(text=_feed(3)), seen))
    art = md.collect(page, tmp_path)

    assert seen == [feed]
    assert art.facts["feed_url"] == feed


def test_happy_path_parses_feed(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(md.httpx, "Client", _make_client(_Resp(text=_feed(3)), seen))
    art = md.collect("https://medium.com/@user", tmp_path)

    assert art.ok is True
    assert art.platform == "medium"
    assert art.method == "rss"
    assert not art.errors

    assert art.facts["feed_title"] == "Ada Lovelace on Medium"
    assert art.facts["post_count"] == 3

    listing = next(b["text"] for b in art.text_blocks
                   if b["label"].startswith("recent posts"))
    for n in (1, 2, 3):
        assert f"Post Number {n}" in listing
    assert "Feb 2026" in listing

    bodies = [b["text"] for b in art.text_blocks if b["label"].startswith("post: ")]
    assert len(bodies) == 3
    joined = " ".join(bodies)
    assert "Body of post 1" in joined
    assert "link" in joined
    # HTML must be stripped, not carried through raw.
    assert "<p>" not in joined
    assert "<a " not in joined
    assert "href=" not in joined


def test_cap_respected(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(md.settings, "medium_articles", 2)
    monkeypatch.setattr(md.httpx, "Client", _make_client(_Resp(text=_feed(5)), seen))
    art = md.collect("https://medium.com/@user", tmp_path)

    assert art.ok is True
    assert art.facts["post_count"] == 2
    listing = next(b["text"] for b in art.text_blocks
                   if b["label"].startswith("recent posts"))
    assert "Post Number 2" in listing
    assert "Post Number 3" not in listing
    assert len([b for b in art.text_blocks if b["label"].startswith("post: ")]) == 2


def test_404_contained(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(md.httpx, "Client", _make_client(_Resp(status_code=404), seen))
    art = md.collect("https://medium.com/@ghost", tmp_path)

    assert art.ok is False
    assert any("404" in e for e in art.errors)


def test_empty_feed_contained(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(md.httpx, "Client", _make_client(_Resp(text=_feed(0)), seen))
    art = md.collect("https://medium.com/@user", tmp_path)

    assert art.ok is False
    assert art.facts["post_count"] == 0
    assert any("no items" in e for e in art.errors)


def test_transport_error_contained(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no network")

    monkeypatch.setattr(md.httpx, "Client", Boom)
    art = md.collect("https://medium.com/@user", tmp_path)

    assert art.ok is False
    assert any("RuntimeError" in e for e in art.errors)
