"""Offline tests for the bespoke Patreon / Ko-fi / Buy Me a Coffee collector.

No real browser or network: `_render` (Playwright page.content()), `_screenshot`,
and `_resolved_url` (the plain-httpx redirect check) are all monkeypatched.

Two JSON shapes are exercised for Patreon tiers, both confirmed against a real
`patreon.com/veritasium` fetch during development:

1. **The real shape** — JSON:API sideloading. Reward IDs are referenced from
   `campaign.data.relationships.rewards.data` as bare `{id, type}` pointers;
   the actual tier objects (title/amount_cents/description) live in a separate
   flat `campaign.included` array, matched by id+type=="reward". This is what
   `_extract_tiers_jsonapi` specifically targets.
2. **A plain nested list** (`campaign.data.rewards = [{title, amount_cents,
   description}, ...]`) — not confirmed live, but kept as a fallback shape the
   generic `_walk` scan still handles, in case older/alternate builds differ.

`_format_price`'s cents-vs-dollars guess from the original draft is gone for
the JSON:API path: `amount_cents` is unambiguously always cents there.
"""

import json

import pytest

from backend.collectors import patreon as pt
from backend.collectors.base import KOFI, PATREON, classify_url


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(pt, "_screenshot", lambda *a, **k: None)
    # Default: "can't tell" (not a network error, not evidence of a dead page).
    # Individual tests override this to exercise the soft-404 path.
    monkeypatch.setattr(pt, "_resolved_url", lambda url: None)


def _patreon_html_with_nextdata():
    data = {
        "props": {
            "pageProps": {
                "bootstrapEnvelope": {
                    "campaign": {
                        "data": {
                            "full_name": "Jane Creator",
                            "patron_count": 4211,
                            "url": "https://www.patreon.com/janecreator",
                            "rewards": [
                                {"title": "Supporter", "amount_cents": 300,
                                 "description": "Early access to posts"},
                                {"title": "Fan", "amount_cents": 500,
                                 "description": "All Supporter perks plus Discord"},
                                {"title": "Superfan", "amount_cents": 1500,
                                 "description": "All perks plus monthly call"},
                            ],
                        }
                    }
                }
            }
        }
    }
    script = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    og = ('<meta property="og:title" content="Jane Creator is creating things">'
          '<meta property="og:description" content="Join 4,211 patrons supporting Jane Creator">')
    return f"<html><head>{og}{script}</head><body>x</body></html>"


def _patreon_html_jsonapi_real_shape():
    """Mirrors the real patreon.com/<creator> payload shape (JSON:API sideload)."""
    data = {
        "props": {"pageProps": {"bootstrapEnvelope": {"pageBootstrap": {"campaign": {
            "data": {
                "attributes": {"patron_count": 18025, "paid_member_count": 2905},
                "relationships": {"rewards": {"data": [
                    {"id": "-1", "type": "reward"},
                    {"id": "376", "type": "reward"},
                    {"id": "377", "type": "reward"},
                ]}},
            },
            "included": [
                {"id": "-1", "type": "reward", "attributes": {"title": None, "amount_cents": 0}},
                {"id": "376", "type": "reward",
                 "attributes": {"title": "Hydrogen", "amount_cents": 100,
                               "description": "<p>Access to the feed</p>"}},
                {"id": "377", "type": "reward",
                 "attributes": {"title": "Lithium", "amount_cents": 300,
                               "description": "+ My gratitude"}},
                {"id": "999", "type": "user", "attributes": {"full_name": "Not A Tier"}},
            ],
        }}}}}
    }
    script = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    og = ('<meta property="og:title" content="Veritasium is creating Science Videos">'
          '<meta property="og:description" content="Join 18,025 patrons supporting Veritasium">')
    return f"<html><head>{og}{script}</head><body>x</body></html>"


def _patreon_html_og_only():
    og = ('<meta property="og:title" content="Bob Maker is creating videos">'
          '<meta property="og:description" content="Join 812 patrons supporting Bob Maker">')
    return f"<html><head>{og}</head><body>x</body></html>"


def _patreon_html_garbled_nextdata():
    og = ('<meta property="og:title" content="Bob Maker is creating videos">'
          '<meta property="og:description" content="Join 812 patrons supporting Bob Maker">')
    script = '<script id="__NEXT_DATA__" type="application/json">{not valid json</script>'
    return f"<html><head>{og}{script}</head><body>x</body></html>"


def _kofi_html():
    og = ('<meta property="og:title" content="Support Alex on Ko-fi">'
          '<meta property="og:description" content="Buy Alex a coffee to say thanks">')
    body = "<body><main>Support my work! <span>127 supporters</span> so far. Thank you!</main></body>"
    return f"<html><head>{og}</head>{body}</html>"


def test_classify_routes():
    assert classify_url("https://www.patreon.com/janecreator") == PATREON
    assert classify_url("https://ko-fi.com/alex") == KOFI
    assert classify_url("https://www.buymeacoffee.com/alex") == KOFI


def test_patreon_nextdata_extracts_tiers(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_render", lambda url: _patreon_html_with_nextdata())
    art = pt.collect("https://www.patreon.com/janecreator", tmp_path)

    assert art.ok is True
    assert art.platform == PATREON
    assert art.method == "nextdata"
    assert art.facts["creator_name"] == "Jane Creator"
    assert art.facts["patron_count"] == 4211
    tiers = art.facts["tiers"]
    assert len(tiers) == 3
    names = [t["name"] for t in tiers]
    assert names == ["Supporter", "Fan", "Superfan"]
    assert tiers[0]["price"] == "$3.00"
    assert tiers[2]["price"] == "$15.00"
    assert "Discord" in tiers[1]["description"]
    labels = [b["label"] for b in art.text_blocks]
    assert any(l.startswith("membership tiers") for l in labels)
    tier_block = next(b for b in art.text_blocks if b["label"].startswith("membership tiers"))
    assert "Supporter: $3.00" in tier_block["text"]


def test_patreon_jsonapi_real_shape_extracts_tiers(monkeypatch, tmp_path):
    """The confirmed-real shape: tiers via campaign.included, not a plain list."""
    monkeypatch.setattr(pt, "_render", lambda url: _patreon_html_jsonapi_real_shape())
    art = pt.collect("https://www.patreon.com/veritasium", tmp_path)

    assert art.ok is True and art.method == "nextdata"
    assert art.facts["patron_count"] == 18025
    tiers = art.facts["tiers"]
    names = [t["name"] for t in tiers]
    assert "Hydrogen" in names and "Lithium" in names
    assert len(tiers) == 2                      # the title=None "-1" placeholder excluded
    hydrogen = next(t for t in tiers if t["name"] == "Hydrogen")
    assert hydrogen["price"] == "$1.00"          # amount_cents=100 -> $1.00, unambiguous
    assert "Access to the feed" in hydrogen["description"]  # HTML stripped
    assert "<p>" not in hydrogen["description"]
    # the unrelated "user" included entry must not leak in as a tier
    assert "Not A Tier" not in names


def test_patreon_no_nextdata_falls_back_to_og(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_render", lambda url: _patreon_html_og_only())
    art = pt.collect("https://www.patreon.com/bobmaker", tmp_path)

    assert art.ok is True
    assert art.method == "og-fallback"
    assert art.facts["tiers"] == []
    assert art.facts["creator_name"].startswith("Bob Maker")
    assert art.facts["patron_count"] == 812
    assert any(b["label"] == "patreon summary" for b in art.text_blocks)


def test_patreon_garbled_nextdata_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_render", lambda url: _patreon_html_garbled_nextdata())
    art = pt.collect("https://www.patreon.com/bobmaker", tmp_path)

    assert art.ok is True
    assert art.method == "og-fallback"
    assert art.facts["tiers"] == []
    assert art.facts["patron_count"] == 812


def test_kofi_happy_path(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_render", lambda url: _kofi_html())
    art = pt.collect("https://ko-fi.com/alex", tmp_path)

    assert art.ok is True
    assert art.platform == KOFI
    assert art.method == "og"
    assert art.facts["creator_name"] == "Support Alex on Ko-fi"
    assert "coffee" in art.facts["bio"].lower()
    assert art.facts["supporter_count"] == 127
    assert any(b["label"] == "kofi summary" for b in art.text_blocks)


def test_buymeacoffee_routes_to_kofi_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_render", lambda url: _kofi_html())
    art = pt.collect("https://www.buymeacoffee.com/alex", tmp_path)

    assert art.platform == "kofi"
    assert art.method == "og"
    assert any(b["label"] == "buymeacoffee summary" for b in art.text_blocks)


def test_kofi_soft_404_skips_render(monkeypatch, tmp_path):
    """ko-fi.com/<dead slug> 200s to the bare homepage instead of a real 404
    (confirmed live). Must be detected and NOT reported as a real profile."""
    monkeypatch.setattr(pt, "_resolved_url", lambda url: "https://ko-fi.com/")
    monkeypatch.setattr(pt, "_render", lambda url: pytest.fail("must not render a dead page"))

    art = pt.collect("https://ko-fi.com/nonexistent-creator-xyz", tmp_path)

    assert art.ok is False
    assert art.facts == {}
    assert any("redirected to homepage" in e for e in art.errors)


def test_kofi_soft_404_detection_for_buymeacoffee(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_resolved_url", lambda url: "https://buymeacoffee.com/")
    monkeypatch.setattr(pt, "_render", lambda url: pytest.fail("must not render"))
    art = pt.collect("https://buymeacoffee.com/nobody", tmp_path)
    assert art.ok is False
    assert any("buymeacoffee" in e for e in art.errors)


def test_kofi_real_profile_not_mistaken_for_soft_404(monkeypatch, tmp_path):
    """A genuine profile keeps its own path after redirects (e.g. www stripped,
    trailing slash added) — must NOT be flagged as the homepage bounce."""
    monkeypatch.setattr(pt, "_resolved_url", lambda url: "https://ko-fi.com/wolfychu")
    monkeypatch.setattr(pt, "_render", lambda url: _kofi_html())
    art = pt.collect("https://ko-fi.com/wolfychu", tmp_path)
    assert art.ok is True
    assert not any("redirected to homepage" in e for e in art.errors)


@pytest.mark.parametrize("url,final,expected", [
    ("https://ko-fi.com/alex", "https://ko-fi.com/", True),
    ("https://ko-fi.com/alex", "https://ko-fi.com/alex", False),
    ("https://ko-fi.com/alex", "https://ko-fi.com/alex/", False),
    ("https://www.ko-fi.com/alex", "https://ko-fi.com/", True),  # www stripped either side
    ("https://ko-fi.com/", "https://ko-fi.com/", False),          # already the homepage
])
def test_is_soft_404(url, final, expected):
    assert pt._is_soft_404(url, final) is expected


def test_resolved_url_failure_does_not_block_collection(monkeypatch, tmp_path):
    """If the cheap redirect check itself fails (network hiccup), that's not
    evidence of anything — must fall through to the normal render path."""
    monkeypatch.setattr(pt, "_resolved_url", lambda url: None)
    monkeypatch.setattr(pt, "_render", lambda url: _kofi_html())
    art = pt.collect("https://ko-fi.com/alex", tmp_path)
    assert art.ok is True


def test_render_failure_contained(monkeypatch, tmp_path):
    def boom(url):
        raise RuntimeError("navigation timeout")
    monkeypatch.setattr(pt, "_render", boom)

    art = pt.collect("https://www.patreon.com/janecreator", tmp_path)
    assert art.ok is False
    assert any("render" in e for e in art.errors)

    art2 = pt.collect("https://ko-fi.com/alex", tmp_path)
    assert art2.ok is False
    assert any("render" in e for e in art2.errors)
