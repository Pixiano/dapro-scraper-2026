"""Offline tests for the bespoke Reddit collector (JSON parsing, no network/browser)."""

import pytest

from backend.collectors import reddit as rd
from backend.collectors.base import REDDIT, classify_url


USER_ABOUT = {"data": {
    "name": "spez", "link_karma": 150000, "comment_karma": 90000,
    "created_utc": 1118030400.0,
    "subreddit": {"public_description": "Reddit co-founder and CEO."},
}}
USER_POSTS = {"data": {"children": [
    {"data": {"title": "An update on our platform", "selftext": "Hello everyone, " * 40}},
    {"data": {"title": "Reddit is going public", "selftext": ""}},
    {"data": {"title": "", "selftext": "untitled should be skipped"}},
]}}

SUB_ABOUT = {"data": {
    "display_name": "python", "subscribers": 1300000, "active_user_count": 2400,
    "public_description": "News about the Python programming language.",
}}
SUB_TOP = {"data": {"children": [
    {"data": {"title": "Python 3.13 released", "selftext": "Highlights inside."}},
    {"data": {"title": "What's your favourite stdlib module?", "selftext": ""}},
]}}


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _make_client(responses):
    """responses: dict of URL-substring -> _Resp (first match wins)."""
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *a, **k):
            for key, resp in responses.items():
                if key in url:
                    return resp
            return _Resp(status_code=404)

    return Client


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(rd, "_screenshot", lambda *a, **k: None)


def test_classify_routes_reddit():
    assert classify_url("https://www.reddit.com/r/python/") == REDDIT


@pytest.mark.parametrize("url,expected", [
    ("https://www.reddit.com/user/spez/", ("user", "spez")),
    ("https://www.reddit.com/u/spez", ("user", "spez")),
    ("https://reddit.com/r/python/", ("subreddit", "python")),
    ("https://old.reddit.com/r/python/top/", ("subreddit", "python")),
    ("https://www.reddit.com/", (None, None)),
])
def test_parse_target(url, expected):
    assert rd._parse_target(url) == expected


def test_user_flow(monkeypatch, tmp_path):
    responses = {
        "/user/spez/about.json": _Resp(payload=USER_ABOUT),
        "/user/spez/submitted.json": _Resp(payload=USER_POSTS),
    }
    monkeypatch.setattr(rd.httpx, "Client", _make_client(responses))
    art = rd.collect("https://www.reddit.com/user/spez", tmp_path)

    assert art.ok is True
    assert art.platform == "reddit"
    assert art.method == "reddit-json"

    f = art.facts
    assert f["reddit_type"] == "user"
    assert f["name"] == "spez"
    assert f["link_karma"] == 150000
    assert f["comment_karma"] == 90000
    assert f["created_utc"] == 1118030400.0
    assert f["public_description"] == "Reddit co-founder and CEO."

    labels = [b["label"] for b in art.text_blocks]
    assert "reddit about" in labels
    # two of three posts have titles
    assert "recent posts (2)" in labels
    posts = next(b["text"] for b in art.text_blocks if b["label"].startswith("recent posts"))
    assert "- An update on our platform" in posts
    assert "- Reddit is going public" in posts
    assert "untitled should be skipped" not in posts
    # selftext excerpt is present but truncated
    assert "Hello everyone," in posts
    assert len(max(posts.splitlines(), key=len)) <= rd._SELFTEXT_EXCERPT + 4


def test_u_short_form_hits_same_endpoints(monkeypatch, tmp_path):
    seen = []

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *a, **k):
            seen.append(url)
            if "about.json" in url:
                return _Resp(payload=USER_ABOUT)
            return _Resp(payload=USER_POSTS)

    monkeypatch.setattr(rd.httpx, "Client", Client)
    art = rd.collect("https://www.reddit.com/u/spez", tmp_path)
    assert art.ok is True
    assert seen[0] == "https://www.reddit.com/user/spez/about.json"
    assert "submitted.json" in seen[1]
    assert art.facts["target"] == "u/spez"


def test_subreddit_flow(monkeypatch, tmp_path):
    responses = {
        "/r/python/about.json": _Resp(payload=SUB_ABOUT),
        "/r/python/top.json": _Resp(payload=SUB_TOP),
    }
    monkeypatch.setattr(rd.httpx, "Client", _make_client(responses))
    art = rd.collect("https://www.reddit.com/r/python/", tmp_path)

    assert art.ok is True
    f = art.facts
    assert f["reddit_type"] == "subreddit"
    assert f["display_name"] == "python"
    assert f["subscribers"] == 1300000
    assert f["active_user_count"] == 2400
    assert f["public_description"] == "News about the Python programming language."
    assert f["target"] == "r/python"

    about = next(b["text"] for b in art.text_blocks if b["label"] == "subreddit about")
    assert "Python programming language" in about
    top = next(b["text"] for b in art.text_blocks if b["label"].startswith("recent posts"))
    assert "- Python 3.13 released" in top
    assert "- What's your favourite stdlib module?" in top
    assert "Highlights inside." in top


def test_rate_limited_429_contained(monkeypatch, tmp_path):
    # JSON blocked AND the old.reddit fallback also blocked → contained, ok False
    responses = {"reddit.com": _Resp(status_code=429)}
    monkeypatch.setattr(rd.httpx, "Client", _make_client(responses))
    monkeypatch.setattr(rd, "_fetch_html", lambda url: (None, "blocked"))
    art = rd.collect("https://www.reddit.com/r/python/", tmp_path)

    assert art.ok is False
    assert art.text_blocks == []
    assert any("429" in e for e in art.errors)
    assert all(isinstance(e, str) for e in art.errors)


# --- old.reddit.com HTML fallback ---

SUB_OLD_HTML = """<html><body>
  <div class="thing"><a class="title" href="/a">Python 3.13 released</a></div>
  <div class="thing"><a class="title" href="/b">Favourite stdlib module?</a></div>
  <div class="side"><div class="md"><p>News about the Python programming language.</p></div>
    <span class="subscribers"><span class="number">1,300,000</span> readers</span></div>
</body></html>"""

USER_OLD_HTML = """<html><body>
  <div class="thing"><a class="title" href="/a">An update on our platform</a></div>
  <span class="karma">184,005</span>
</body></html>"""


def test_subreddit_fallback_to_old_html_on_block(monkeypatch, tmp_path):
    monkeypatch.setattr(rd.httpx, "Client", _make_client({"reddit.com": _Resp(status_code=403)}))
    monkeypatch.setattr(rd, "_fetch_html", lambda url: (SUB_OLD_HTML, None))
    art = rd.collect("https://www.reddit.com/r/python/", tmp_path)

    assert art.ok is True
    assert art.method == "reddit-json+old-html"
    assert art.facts["fallback"] == "old.reddit.com"
    assert art.facts["subscribers"] == 1300000
    assert "public_description" in art.facts
    posts = next(b["text"] for b in art.text_blocks if b["label"].startswith("recent posts"))
    assert "Python 3.13 released" in posts
    assert any("403" in e for e in art.errors)   # original block still recorded


def test_user_fallback_to_old_html(monkeypatch, tmp_path):
    monkeypatch.setattr(rd.httpx, "Client", _make_client({"reddit.com": _Resp(status_code=403)}))
    monkeypatch.setattr(rd, "_fetch_html", lambda url: (USER_OLD_HTML, None))
    art = rd.collect("https://www.reddit.com/user/spez", tmp_path)

    assert art.ok is True and art.facts["link_karma"] == 184005
    assert any("An update on our platform" in b["text"] for b in art.text_blocks)


def test_fallback_not_used_when_json_succeeds(monkeypatch, tmp_path):
    responses = {"/r/python/about.json": _Resp(payload=SUB_ABOUT),
                 "/r/python/top.json": _Resp(payload=SUB_TOP)}
    monkeypatch.setattr(rd.httpx, "Client", _make_client(responses))
    monkeypatch.setattr(rd, "_fetch_html", lambda url: pytest.fail("fallback must not run"))
    art = rd.collect("https://www.reddit.com/r/python/", tmp_path)
    assert art.ok is True and art.method == "reddit-json"


def test_fetch_html_uses_render_when_httpx_blocked(monkeypatch):
    monkeypatch.setattr(rd.httpx, "Client", _make_client({"reddit.com": _Resp(status_code=403)}))
    monkeypatch.setattr(rd, "_render_html", lambda url: "<html>rendered</html>")
    html, err = rd._fetch_html("https://old.reddit.com/r/x/")
    assert html == "<html>rendered</html>" and err is None


def test_fetch_html_prefers_httpx(monkeypatch):
    monkeypatch.setattr(rd.httpx, "Client",
                        _make_client({"reddit.com": _Resp(status_code=200, text="<html>ok</html>")}))
    monkeypatch.setattr(rd, "_render_html", lambda url: pytest.fail("should not render"))
    html, err = rd._fetch_html("https://old.reddit.com/r/x/")
    assert html == "<html>ok</html>" and err is None


def test_forbidden_403_contained(monkeypatch, tmp_path):
    responses = {"reddit.com": _Resp(status_code=403)}
    monkeypatch.setattr(rd.httpx, "Client", _make_client(responses))
    monkeypatch.setattr(rd, "_fetch_html", lambda url: (None, "blocked"))  # fallback also blocked
    art = rd.collect("https://www.reddit.com/user/spez", tmp_path)
    assert art.ok is False
    assert any("403" in e for e in art.errors)


def test_transport_error_contained(monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no network")

    monkeypatch.setattr(rd.httpx, "Client", Boom)
    monkeypatch.setattr(rd, "_fetch_html", lambda url: (None, "blocked"))
    art = rd.collect("https://www.reddit.com/r/python/", tmp_path)
    assert art.ok is False
    assert any("RuntimeError" in e for e in art.errors)


def test_unparseable_url_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(rd.httpx, "Client", _make_client({}))
    art = rd.collect("https://www.reddit.com/", tmp_path)
    assert art.ok is False
    assert any("no user or subreddit" in e for e in art.errors)
