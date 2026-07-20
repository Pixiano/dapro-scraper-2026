"""Offline tests for the SocialCollector: all three methods mocked."""

import pytest

from backend.collectors import social
from backend.collectors.base import FACEBOOK, INSTAGRAM


@pytest.mark.parametrize("url,expected", [
    ("https://instagram.com/nasa", "nasa"),
    ("https://www.instagram.com/nasa/", "nasa"),
    ("https://instagram.com/@nasa", "nasa"),
    ("https://www.instagram.com/p/ABC123/", None),         # a post, not a profile
    ("https://www.instagram.com/reel/XYZ/", None),
    ("https://facebook.com/NASA", "NASA"),
    ("https://www.facebook.com/pages/Something/123", "Something"),
    ("https://facebook.com/", None),
])
def test_username_parsing(url, expected):
    assert social._username(url) == expected


def test_instagram_merges_three_methods(monkeypatch, tmp_path):
    def fake_pw(url, job_dir, art):
        art.screenshots.append(str(tmp_path / "shot.png"))
        art.facts["og_description"] = "1M Followers, 3,000 Posts - NASA"
        art.text_blocks.append({"label": "social og-summary",
                                "text": "NASA\n1M Followers", "method": "playwright-loggedout"})
        social._prov(art, "og_description", "playwright-loggedout")
        return True

    monkeypatch.setattr(social, "_method_playwright", fake_pw)
    monkeypatch.setattr(social.ig_service, "fetch_profile", lambda u: {
        "available": True, "followers": 1000000, "posts": 3000,
        "fullName": "NASA", "verified": True, "private": False, "bio": "Exploring the universe."})

    art = social.collect("https://instagram.com/nasa", tmp_path)

    assert art.ok and art.platform == INSTAGRAM
    assert art.facts["followers"] == 1000000
    assert art.facts["provenance"]["followers"] == "instaloader"
    assert art.facts["provenance"]["og_description"] == "playwright-loggedout"
    assert "instaloader" in art.facts["methods_succeeded"]
    assert "playwright-loggedout" in art.facts["methods_succeeded"]
    assert "screenshot-vision(queued)" in art.facts["methods_succeeded"]
    assert any(b["method"] == "instaloader" for b in art.text_blocks)


def test_facebook_uses_httpx_og(monkeypatch, tmp_path):
    monkeypatch.setattr(social, "_method_playwright", lambda u, j, a: False)

    class Resp:
        status_code = 200
        text = ('<meta property="og:title" content="NASA">'
                '<meta property="og:description" content="Official NASA page. 50M likes.">')

    class Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return Resp()

    monkeypatch.setattr(social.httpx, "Client", Client)

    art = social.collect("https://facebook.com/NASA", tmp_path)
    assert art.ok and art.platform == FACEBOOK
    assert any(b["method"] == "httpx-og" for b in art.text_blocks)
    assert "50M likes" in art.text_blocks[0]["text"]


def test_total_failure_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(social, "_method_playwright", lambda u, j, a: (
        a.errors.append("playwright: boom") or False))
    monkeypatch.setattr(social.ig_service, "fetch_profile",
                        lambda u: {"available": False, "reason": "429 rate limited"})

    art = social.collect("https://instagram.com/someone", tmp_path)
    assert art.ok is False
    assert any("boom" in e for e in art.errors)
    assert any("429" in e for e in art.errors)
    assert art.facts["methods_succeeded"] == []      # nothing worked, still no crash
    assert set(art.facts["methods_attempted"]) == {
        "playwright-loggedout", "instaloader", "screenshot-vision"}
