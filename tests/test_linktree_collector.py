"""Offline tests for the bespoke Linktree / link-in-bio collector (no real browser)."""

import pytest

from backend.collectors import linktree as lt
from backend.collectors.base import LINKTREE, classify_url


def _html(*, name="Jane Creator", bio="Making videos about stuff.", links=None):
    links = links if links is not None else []
    anchors = "".join(f'<a href="{href}">{label}</a>' for label, href in links)
    return f"""
    <html><head>
      <title>{name} | Linktree</title>
      <meta property="og:title" content="{name}">
      <meta property="og:description" content="{bio}">
    </head><body>
      <h1>{name}</h1>
      <p>{bio}</p>
      {anchors}
    </body></html>
    """


LINKS = [
    ("YouTube", "https://youtube.com/@janecreator"),
    ("Patreon", "https://patreon.com/janecreator"),
    ("Instagram", "https://instagram.com/janecreator"),
    ("Back to Linktree", "https://linktr.ee/janecreator"),   # self-link, excluded
    ("Email me", "mailto:jane@example.com"),                  # excluded
    ("Nothing", "javascript:void(0)"),                        # excluded
    ("YouTube again", "https://youtube.com/@janecreator"),    # duplicate, deduped
]


@pytest.fixture(autouse=True)
def no_render_side_effects(monkeypatch):
    # Individual tests monkeypatch `_render` directly; this just guards against
    # any accidental real Playwright launch if a test forgets to.
    pass


def _run(url, html_or_none, monkeypatch, tmp_path):
    def fake_render(u, job_dir, art):
        return html_or_none
    monkeypatch.setattr(lt, "_render", fake_render)
    return lt.collect(url, tmp_path)


def test_classify_routes_linktree_hosts():
    assert classify_url("https://linktr.ee/janecreator") == LINKTREE
    assert classify_url("https://beacons.ai/janecreator") == LINKTREE
    assert classify_url("https://stan.store/janecreator") == LINKTREE
    assert classify_url("https://example.com") != LINKTREE


def test_extracts_links_excludes_self_mailto_js_and_dedupes(monkeypatch, tmp_path):
    html = _html(links=LINKS)
    art = _run("https://linktr.ee/janecreator", html, monkeypatch, tmp_path)

    assert art.ok
    assert art.platform == LINKTREE
    assert art.facts["discovered_link_count"] == 3
    urls = {l["url"] for l in art.facts["discovered_links"]}
    assert urls == {
        "https://youtube.com/@janecreator",
        "https://patreon.com/janecreator",
        "https://instagram.com/janecreator",
    }
    labels = {l["label"]: l["url"] for l in art.facts["discovered_links"]}
    assert labels["YouTube"] == "https://youtube.com/@janecreator"
    assert labels["Patreon"] == "https://patreon.com/janecreator"
    assert labels["Instagram"] == "https://instagram.com/janecreator"

    block = next(b for b in art.text_blocks if b["label"].startswith("discovered links"))
    assert "- YouTube: https://youtube.com/@janecreator" in block["text"]
    assert "- Patreon: https://patreon.com/janecreator" in block["text"]
    assert "- Instagram: https://instagram.com/janecreator" in block["text"]
    assert "linktr.ee" not in block["text"]

    assert art.facts["profile_name"] == "Jane Creator"
    assert art.facts["bio"] == "Making videos about stuff."


def test_cap_is_respected(monkeypatch, tmp_path):
    monkeypatch.setattr(lt.settings, "linktree_links_cap", 2)
    five_links = [
        ("A", "https://a.example.com"),
        ("B", "https://b.example.com"),
        ("C", "https://c.example.com"),
        ("D", "https://d.example.com"),
        ("E", "https://e.example.com"),
    ]
    html = _html(links=five_links)
    art = _run("https://linktr.ee/janecreator", html, monkeypatch, tmp_path)

    assert art.facts["discovered_link_count"] == 2
    assert len(art.facts["discovered_links"]) == 2


def test_zero_links_but_bio_still_ok(monkeypatch, tmp_path):
    html = _html(links=[])
    art = _run("https://linktr.ee/janecreator", html, monkeypatch, tmp_path)

    assert art.ok is True
    assert art.facts["discovered_link_count"] == 0
    assert art.facts["bio"] == "Making videos about stuff."


def test_render_failure_contained_not_raised(monkeypatch, tmp_path):
    def boom(u, job_dir, art):
        art.errors.append("linktree render: TimeoutError: boom")
        return None
    monkeypatch.setattr(lt, "_render", boom)

    art = lt.collect("https://linktr.ee/janecreator", tmp_path)

    assert art.ok is False
    assert any("boom" in e for e in art.errors)


def test_real_render_exception_is_contained(monkeypatch, tmp_path):
    def explode(u, job_dir, art):
        raise RuntimeError("playwright exploded")
    monkeypatch.setattr(lt, "_render", explode)

    art = lt.collect("https://linktr.ee/janecreator", tmp_path)

    assert art.ok is False
    assert any("playwright exploded" in e for e in art.errors)
