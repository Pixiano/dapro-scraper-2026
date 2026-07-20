"""Offline tests for the bespoke GitHub collector (API parsing, no network/browser)."""

import pytest

from backend.collectors import github as gh
from backend.collectors.base import GITHUB, classify_url


USER = {
    "name": "Linus Torvalds", "login": "torvalds", "type": "User",
    "bio": "Creator of Linux and Git.", "company": "Linux Foundation",
    "blog": "https://kernel.org", "location": "Portland, OR",
    "followers": 200000, "public_repos": 8,
}
REPOS = [
    {"name": "linux", "description": "Linux kernel source tree",
     "language": "C", "stargazers_count": 170000, "forks_count": 54000},
    {"name": "subsurface", "description": "Dive log program",
     "language": "C", "stargazers_count": 2600, "forks_count": 700},
    {"name": "test-tlb", "description": None,
     "language": "Python", "stargazers_count": 400, "forks_count": 40},
]
README = "# Hi\nThis is the profile README for torvalds."


class _Resp:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _make_client(responses):
    """responses: dict of URL-substring -> _Resp."""
    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            for key, resp in responses.items():
                if key in url:
                    return resp
            return _Resp(status_code=404)

    return Client


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(gh, "_screenshot", lambda *a, **k: None)


def test_classify_routes_github():
    assert classify_url("https://github.com/torvalds") == GITHUB


def test_profile_extracts_facts_and_text(monkeypatch, tmp_path):
    responses = {
        "/users/torvalds/repos": _Resp(payload=REPOS),
        "/users/torvalds": _Resp(payload=USER),
        "/readme": _Resp(text=README),
    }
    monkeypatch.setattr(gh.httpx, "Client", _make_client(responses))
    art = gh.collect("https://github.com/torvalds", tmp_path)

    assert art.ok is True
    assert art.platform == "github"
    assert art.method == "github_api"

    f = art.facts
    assert f["login"] == "torvalds"
    assert f["followers"] == 200000
    assert f["public_repos"] == 8
    assert f["type"] == "User"
    # C appears twice, Python once → C first
    assert f["top_languages"] == ["C", "Python"]

    labels = [b["label"] for b in art.text_blocks]
    assert "bio" in labels
    assert any(l.startswith("repositories (3)") for l in labels)
    assert "profile README" in labels

    repo_block = next(b["text"] for b in art.text_blocks
                      if b["label"].startswith("repositories"))
    assert "linux" in repo_block
    assert "⭐170000" in repo_block
    assert "[C, ⭐170000]" in repo_block

    readme_block = next(b["text"] for b in art.text_blocks
                        if b["label"] == "profile README")
    assert "profile README for torvalds" in readme_block


def test_user_404_contained(monkeypatch, tmp_path):
    responses = {"/users/ghost": _Resp(status_code=404)}
    monkeypatch.setattr(gh.httpx, "Client", _make_client(responses))
    art = gh.collect("https://github.com/ghost", tmp_path)

    assert art.ok is False
    assert any("404" in e for e in art.errors)


def test_missing_login_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(gh.httpx, "Client", _make_client({}))
    art = gh.collect("https://github.com/", tmp_path)
    assert art.ok is False
    assert any("no login" in e for e in art.errors)


def test_missing_readme_is_quiet(monkeypatch, tmp_path):
    responses = {
        "/users/torvalds/repos": _Resp(payload=REPOS),
        "/users/torvalds": _Resp(payload=USER),
        "/readme": _Resp(status_code=404),
    }
    monkeypatch.setattr(gh.httpx, "Client", _make_client(responses))
    art = gh.collect("https://github.com/torvalds", tmp_path)

    assert art.ok is True
    assert not any("readme" in e.lower() for e in art.errors)
    assert not any(b["label"] == "profile README" for b in art.text_blocks)
