"""Offline tests for the bespoke LinkedIn collector (JSON-LD parsing, no network)."""

import json

import pytest

from backend.collectors import linkedin as li
from backend.collectors.base import LINKEDIN, classify_url


def _html(*ld_objs, og_desc=""):
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in ld_objs)
    og = f'<meta property="og:description" content="{og_desc}">' if og_desc else ""
    return f"<html><head>{og}{scripts}</head><body>x</body></html>"


ORG = {
    "@type": "Organization",
    "name": "NASA - National Aeronautics and Space Administration",
    "description": "For more than 60 years, NASA has been breaking barriers.",
    "numberOfEmployees": {"@type": "QuantitativeValue", "value": 51249},
    "address": {"@type": "PostalAddress", "streetAddress": "300 E Street SW",
                "addressLocality": "Washington", "addressRegion": "DC",
                "postalCode": "20546", "addressCountry": "US"},
    "slogan": "Explore", "sameAs": ["https://twitter.com/nasa", "https://www.nasa.gov"],
    "url": "https://www.linkedin.com/company/nasa",
}
PERSON = {
    "@type": "Person", "name": "Satya Nadella",
    "jobTitle": ["Chairman and CEO", "Member Board Of Trustees"],
    "description": "As chairman and CEO of Microsoft, I define my mission...",
    "address": {"@type": "PostalAddress", "addressLocality": "Redmond, Washington",
                "addressCountry": "US"},
    "worksFor": [{"@type": "Organization", "name": "Microsoft"}],
    "alumniOf": [{"@type": "Organization", "name": "Starbucks"}],
    "awards": ["CIE Award"], "knowsLanguage": ["English"],
    "interactionStatistic": [{"@type": "InteractionCounter",
                              "interactionType": {"@type": "https://schema.org/FollowAction"},
                              "userInteractionCount": 11200000}],
}
POST = {"@type": "DiscussionForumPosting", "text": "To the skies! Astronaut launched.",
        "url": "https://linkedin.com/posts/x"}
ARTICLE = {"@type": "Article", "headline": "The age of AI transformation",
           "url": "https://linkedin.com/pulse/x"}


@pytest.fixture(autouse=True)
def no_screenshot(monkeypatch):
    monkeypatch.setattr(li, "_screenshot", lambda *a, **k: None)


def test_classify_routes_linkedin():
    assert classify_url("https://www.linkedin.com/company/nasa") == LINKEDIN
    assert classify_url("https://linkedin.com/in/satyanadella") == LINKEDIN
    assert classify_url("https://example.com") != LINKEDIN


def _run(url, html, monkeypatch, tmp_path):
    class Resp:
        status_code = 200
        text = html

    class Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, u): return Resp()

    monkeypatch.setattr(li.httpx, "Client", Client)
    return li.collect(url, tmp_path)


def test_company_extracts_structured_facts(monkeypatch, tmp_path):
    html = _html(ORG, POST, POST, og_desc="NASA | 7,047,198 followers on LinkedIn. Explore")
    art = _run("https://www.linkedin.com/company/nasa", html, monkeypatch, tmp_path)

    assert art.ok and art.platform == "linkedin"
    f = art.facts
    assert f["linkedin_type"] == "company"
    assert f["name"].startswith("NASA")
    assert f["employees"] == 51249
    assert "Washington" in f["headquarters"] and "20546" in f["headquarters"]
    assert f["followers"] == 7047198                 # parsed from og
    assert "https://www.nasa.gov" in f["other_links"]
    labels = [b["label"] for b in art.text_blocks]
    assert any(l.startswith("linkedin about") for l in labels)
    assert any(l.startswith("recent posts") for l in labels)


def test_person_extracts_structured_facts(monkeypatch, tmp_path):
    html = _html(PERSON, ARTICLE)
    art = _run("https://www.linkedin.com/in/satyanadella", html, monkeypatch, tmp_path)

    f = art.facts
    assert f["linkedin_type"] == "person"
    assert f["name"] == "Satya Nadella"
    assert "Chairman and CEO" in f["job_title"]
    assert f["works_for"] == ["Microsoft"]
    assert f["education"] == ["Starbucks"]
    assert f["followers"] == 11200000                # from interactionStatistic
    assert f["languages"] == ["English"]
    assert any(b["label"].startswith("published articles") for b in art.text_blocks)


def test_missing_jsonld_falls_back_to_og(monkeypatch, tmp_path):
    html = _html(og_desc="Some Company | 500 followers on LinkedIn. We do things.")
    art = _run("https://www.linkedin.com/company/x", html, monkeypatch, tmp_path)
    assert art.facts["linkedin_type"] == "company"
    assert any(b["label"] == "linkedin og-summary" for b in art.text_blocks)


def test_http_error_contained(monkeypatch, tmp_path):
    class Resp:
        status_code = 999
        text = ""

    class Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, u): return Resp()

    monkeypatch.setattr(li.httpx, "Client", Client)
    art = li.collect("https://www.linkedin.com/company/x", tmp_path)
    assert art.ok is False
    assert any("HTTP 999" in e for e in art.errors)


def test_helpers():
    assert li._to_int({"value": 51249}) == 51249
    assert li._to_int("7,047,198") == 7047198
    assert li._to_int("n/a") is None
    assert li._address({"addressLocality": "Paris", "addressCountry": "FR"}) == "Paris, FR"
    assert li._names([{"name": "A"}, "B"]) == ["A", "B"]
