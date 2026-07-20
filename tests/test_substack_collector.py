"""Offline tests for the bespoke Substack collector (RSS parsing, no network/browser)."""

import pytest

from backend.collectors import substack as ss
from backend.collectors.base import SUBSTACK, classify_url


def _item(n: int) -> str:
    return f"""
  <item>
    <title>Issue Number {n}</title>
    <link>https://astral.substack.com/p/issue-{n}</link>
    <pubDate>Tue, 0{n} Mar 2026 08:30:00 GMT</pubDate>
    <description><![CDATA[<p>Newsletter body {n} with an <a href="https://x.test">anchor</a>.</p>]]></description>
  </item>"""


def _feed(n_items: int, title: str = "Astral Codex Ten") -> str:
    items = "".join(_item(i) for i in range(1, n_items + 1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
 <channel>
  <title>{title}</title>
  <link>https://astral.substack.com</link>
  <description>A newsletter</description>{items}
 </channel>
</rss>"""


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _make_client(response, seen):
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kwargs):
            seen.append(url)
            return response

    return Client


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(ss, "_screenshot", lambda *a, **k: None)


def test_classify_routes_substack():
    assert classify_url("https://astral.substack.com") == SUBSTACK


@pytest.mark.parametrize("page,feed", [
    ("https://astral.substack.com", "https://astral.substack.com/feed"),
    ("https://astral.substack.com/", "https://astral.substack.com/feed"),
    ("https://www.astral.substack.com", "https://astral.substack.com/feed"),
    ("astral.substack.com", "https://astral.substack.com/feed"),
    ("https://astral.substack.com/p/some-post-title", "https://astral.substack.com/feed"),
    ("https://astral.substack.com/archive", "https://astral.substack.com/feed"),
    ("https://substack.com/@astral", "https://astral.substack.com/feed"),
])
def test_feed_url_normalisation(monkeypatch, tmp_path, page, feed):
    seen = []
    monkeypatch.setattr(ss.httpx, "Client", _make_client(_Resp(text=_feed(3)), seen))
    art = ss.collect(page, tmp_path)

    assert seen == [feed]
    assert art.facts["feed_url"] == feed


def test_happy_path_parses_feed(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ss.httpx, "Client", _make_client(_Resp(text=_feed(3)), seen))
    art = ss.collect("https://astral.substack.com", tmp_path)

    assert art.ok is True
    assert art.platform == "substack"
    assert art.method == "rss"
    assert not art.errors

    assert art.facts["feed_title"] == "Astral Codex Ten"
    assert art.facts["post_count"] == 3

    listing = next(b["text"] for b in art.text_blocks
                   if b["label"].startswith("recent posts"))
    for n in (1, 2, 3):
        assert f"Issue Number {n}" in listing
    assert "Mar 2026" in listing

    bodies = [b["text"] for b in art.text_blocks if b["label"].startswith("post: ")]
    assert len(bodies) == 3
    joined = " ".join(bodies)
    assert "Newsletter body 1" in joined
    assert "anchor" in joined
    assert "<p>" not in joined
    assert "<a " not in joined
    assert "href=" not in joined


def test_cap_respected(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ss.settings, "substack_posts", 2)
    monkeypatch.setattr(ss.httpx, "Client", _make_client(_Resp(text=_feed(5)), seen))
    art = ss.collect("https://astral.substack.com", tmp_path)

    assert art.ok is True
    assert art.facts["post_count"] == 2
    listing = next(b["text"] for b in art.text_blocks
                   if b["label"].startswith("recent posts"))
    assert "Issue Number 2" in listing
    assert "Issue Number 3" not in listing
    assert len([b for b in art.text_blocks if b["label"].startswith("post: ")]) == 2


def test_404_contained(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ss.httpx, "Client", _make_client(_Resp(status_code=404), seen))
    art = ss.collect("https://ghost.substack.com", tmp_path)

    assert art.ok is False
    assert any("404" in e for e in art.errors)


def test_empty_feed_contained(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ss.httpx, "Client", _make_client(_Resp(text=_feed(0)), seen))
    art = ss.collect("https://astral.substack.com", tmp_path)

    assert art.ok is False
    assert art.facts["post_count"] == 0
    assert any("no items" in e for e in art.errors)


def test_transport_error_contained(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no network")

    monkeypatch.setattr(ss.httpx, "Client", Boom)
    art = ss.collect("https://astral.substack.com", tmp_path)

    assert art.ok is False
    assert any("RuntimeError" in e for e in art.errors)
