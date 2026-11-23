"""The collectors share one screenshot helper and one Medium/Substack feed collector."""

from backend.collectors import (base, bluesky, feed, github, linkedin, patreon, reddit,
                                twitter)


def test_one_screenshot_helper_shared():
    for mod in (bluesky, feed, github, linkedin, patreon, reddit, twitter):
        assert mod._screenshot is base.screenshot, mod.__name__


def test_screenshot_failure_lands_in_errors(monkeypatch, tmp_path):
    import playwright.sync_api as pw

    def boom():
        raise RuntimeError("no browser")

    monkeypatch.setattr(pw, "sync_playwright", boom)
    art = base.SourceArtifact(url="https://github.com/x", platform=base.GITHUB)
    base.screenshot(art.url, tmp_path, art)
    assert art.errors == ["github screenshot: RuntimeError"] and not art.screenshots


def test_medium_and_substack_share_the_feed_collector():
    assert base.get_collector(base.MEDIUM) is feed.collect
    assert base.get_collector(base.SUBSTACK) is feed.collect
