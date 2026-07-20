"""Offline tests for the Google News collector (canned RSS, no network)."""

import pytest

from backend.collectors import news
from backend.collectors.base import NEWS, classify_url


def _item(title, publisher, date):
    return f"""
  <item>
    <title>{title}</title>
    <link>https://news.google.com/articles/{title.replace(' ', '-')}</link>
    <pubDate>{date}</pubDate>
    <source url="https://example.com">{publisher}</source>
  </item>"""


ITEMS = [
    ("Acme Corp raises Series B", "Reuters", "Mon, 13 Jul 2026 09:00:00 GMT"),
    ("Acme Corp opens Berlin office", "TechCrunch", "Tue, 14 Jul 2026 11:30:00 GMT"),
    ("Analysts weigh in on Acme Corp", "Reuters", "Wed, 15 Jul 2026 08:15:00 GMT"),
]

RSS = ("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Acme Corp - Google News</title>"""
       + "".join(_item(*i) for i in ITEMS)
       + "</channel></rss>")


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _make_client(resp, seen=None):
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *a, **k):
            if seen is not None:
                seen.append(url)
            return resp

    return Client


@pytest.fixture
def seen():
    return []


def test_classify_routes_google_news():
    assert classify_url("https://news.google.com/rss/search?q=acme") == NEWS


def test_collect_for_entity_parses_headlines(monkeypatch, tmp_path, seen):
    monkeypatch.setattr(news.httpx, "Client", _make_client(_Resp(text=RSS), seen))
    art = news.collect_for_entity("Acme Corp", tmp_path)

    assert art.ok is True
    assert art.platform == NEWS
    assert art.method == "google-news-rss"
    assert art.errors == []
    assert art.screenshots == []

    # query is URL-encoded into the feed URL that was actually fetched
    assert "q=Acme+Corp" in art.url
    assert seen and seen[0] == art.url
    assert "hl=en-US" in art.url
    assert "ceid=US:en" in art.url

    block = art.text_blocks[0]
    assert block["label"] == "recent news: Acme Corp"
    for title, publisher, date in ITEMS:
        assert f"- {title} ({publisher}, {date})" in block["text"]

    assert art.facts["headline_count"] == 3
    assert art.facts["publishers"] == ["Reuters", "TechCrunch"]  # unique, ordered
    assert art.facts["query"] == "Acme Corp"


def test_news_items_cap_respected(monkeypatch, tmp_path):
    monkeypatch.setattr(news.httpx, "Client", _make_client(_Resp(text=RSS)))
    monkeypatch.setattr(news.settings, "news_items", 2)
    art = news.collect_for_entity("Acme Corp", tmp_path)

    assert art.facts["headline_count"] == 2
    assert len(art.text_blocks[0]["text"].splitlines()) == 2
    assert "Analysts weigh in" not in art.text_blocks[0]["text"]


def test_http_error_is_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(news.httpx, "Client", _make_client(_Resp(status_code=503)))
    art = news.collect_for_entity("Acme Corp", tmp_path)

    assert art.ok is False
    assert art.text_blocks == []
    assert any("503" in e for e in art.errors)


def test_transport_exception_is_contained(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("dns down")

    monkeypatch.setattr(news.httpx, "Client", Boom)
    art = news.collect_for_entity("Acme Corp", tmp_path)

    assert art.ok is False
    assert any("RuntimeError" in e for e in art.errors)


def test_empty_feed_is_not_ok(monkeypatch, tmp_path):
    empty = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    monkeypatch.setattr(news.httpx, "Client", _make_client(_Resp(text=empty)))
    art = news.collect_for_entity("Nobody At All", tmp_path)

    assert art.ok is False
    assert art.facts["headline_count"] == 0
    assert art.errors


def test_collect_url_with_query_delegates(monkeypatch, tmp_path, seen):
    monkeypatch.setattr(news.httpx, "Client", _make_client(_Resp(text=RSS), seen))
    art = news.collect(
        "https://news.google.com/rss/search?q=Acme%20Corp&hl=en-US", tmp_path)

    assert art.ok is True
    assert art.facts["query"] == "Acme Corp"
    assert art.facts["headline_count"] == 3
    assert "q=Acme+Corp" in art.url
