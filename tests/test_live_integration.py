"""Live integration tests — the real thing, not mocks.

These hit real external services (network, real Chromium, real third-party
APIs) and are intentionally excluded from the default test run (see
pytest.ini: `addopts = -m "not live"`). They exist so a code review question
like "how do you know the mocks still match reality?" has a concrete answer:
run `pytest -m live` against a couple of small, stable, public, read-only
endpoints.

Rules for anything added here:
- read-only, public data only — never login, posting, or any state change
- skip (never fail) when a precondition isn't met, e.g. a missing API key
- keep the list short and the targets boringly stable (example.com, a large
  well-known GitHub org, ...) so this doesn't turn into a flaky-test graveyard
"""

import os

import pytest

from backend.collectors import github, website
from backend.config import settings


@pytest.mark.live
def test_website_collector_live_example_com(tmp_path):
    """Real Playwright + real network fetch of https://example.com."""
    art = website.collect("https://example.com", tmp_path)

    assert art.ok is True
    assert art.text_blocks, "expected at least one extracted text block"
    assert any("Example Domain" in b["text"] for b in art.text_blocks)
    assert art.screenshots, "expected at least one screenshot"
    for shot in art.screenshots:
        assert os.path.isfile(shot), f"screenshot path does not exist on disk: {shot}"
        assert os.path.getsize(shot) > 0


@pytest.mark.live
def test_github_collector_live_real_org(tmp_path):
    """Real GitHub REST API call against a real, stable public org."""
    art = github.collect("https://github.com/github", tmp_path)

    assert art.ok is True
    assert art.platform == "github"
    f = art.facts
    assert f.get("login") == "github"
    assert isinstance(f.get("followers"), int) and f["followers"] > 0
    assert isinstance(f.get("public_repos"), int) and f["public_repos"] > 0


@pytest.mark.live
def test_youtube_live_resolver_or_skip(tmp_path):
    """Real YouTube Data API call — skipped cleanly if no API key is configured."""
    if not settings.youtube_api_key:
        pytest.skip("no YOUTUBE_API_KEY configured")

    from backend.collectors import youtube

    art = youtube.collect("https://www.youtube.com/@YouTube", tmp_path)

    assert art.ok is True
    assert art.platform == "youtube"
    assert art.facts, "expected real channel facts back from the live API"
