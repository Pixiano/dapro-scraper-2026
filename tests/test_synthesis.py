"""Offline tests for the synthesis stage: gpt-oss calls mocked."""

import pytest

from backend import synthesis
from backend.config import settings


def _artifact(**kw):
    base = {"url": "https://acme.io", "platform": "website", "ok": True,
            "text_blocks": [{"label": "main", "text": "We sell robots."}],
            "images": [], "screenshots": [], "vision_notes": [], "facts": {},
            "errors": []}
    base.update(kw)
    return base


def test_chunks_pack_paragraphs_without_splitting():
    text = "\n\n".join(["a" * 100, "b" * 100, "c" * 100])
    chunks = synthesis._chunks(text, 250)
    assert len(chunks) == 2
    assert all(len(c) <= 250 for c in chunks)


def test_chunks_hard_split_oversized_block():
    chunks = synthesis._chunks("x" * 1000, 300)
    assert len(chunks) == 4
    assert all(len(c) <= 300 for c in chunks)


def test_source_text_includes_vision_notes():
    art = _artifact(vision_notes=[{"ref": "C:\\j\\shot.png", "description": "A red hero banner."}])
    text = synthesis._source_text(art)
    assert "We sell robots." in text
    assert "A red hero banner." in text
    assert "shot.png" in text          # ref reduced to a readable basename


def test_header_hides_noise_and_shows_errors():
    art = _artifact(facts={"followers": 100, "provenance": {"followers": "instaloader"},
                           "methods_succeeded": ["x"]},
                    ok=False, errors=["playwright: boom"])
    h = synthesis._header(art)
    assert "followers" in h and "100" in h
    assert "provenance" not in h and "methods_succeeded" not in h
    assert "limited data available" in h        # neutral, not "partial/failed"
    assert "playwright: boom" not in h          # errors never reach the model


def test_single_chunk_source_skips_brief_reduce(monkeypatch):
    calls = []
    monkeypatch.setattr(synthesis.client, "complete",
                        lambda p, **k: calls.append(p) or "extracted signals")
    out = synthesis.summarize_source(_artifact())
    assert len(calls) == 1                      # map only, no second reduce call
    assert "extracted signals" in out


def test_multi_chunk_source_reduces_to_brief(monkeypatch):
    monkeypatch.setattr(settings, "synthesis_chunk_chars", 100)
    calls = []

    def fake(prompt, **k):
        calls.append(prompt)
        return "BRIEFED" if "Combine these extracted notes" in prompt else "note"

    monkeypatch.setattr(synthesis.client, "complete", fake)
    art = _artifact(text_blocks=[{"label": "main", "text": "\n\n".join(["x" * 90] * 4)}])
    out = synthesis.summarize_source(art)
    assert len(calls) == 5                      # 4 map + 1 brief reduce
    assert "BRIEFED" in out


def test_empty_source_needs_no_model_call(monkeypatch):
    monkeypatch.setattr(synthesis.client, "complete",
                        lambda *a, **k: pytest.fail("should not call model"))
    out = synthesis.summarize_source(_artifact(text_blocks=[], ok=False,
                                               errors=["noApiKey"]))
    assert "No notable public information was found" in out


def test_header_never_leaks_errors():
    # errors + technical failure reasons must NOT appear in what the model sees
    art = _artifact(ok=False, errors=["404 Not Found", "ProfileNotExistsException"],
                    facts={"followers": 100})
    h = synthesis._header(art)
    assert "404" not in h and "ProfileNotExists" not in h
    assert "COLLECTION ERRORS" not in h
    assert "failed" not in h.lower()          # neutral status wording
    assert "limited data available" in h      # neutral marker for a thin source
    assert "followers" in h                    # real facts still pass through


@pytest.mark.parametrize("explicit,facts,expected", [
    ("Acme Corp", {}, "Acme Corp"),
    (None, {"channel_title": "Acme Films"}, "Acme Films"),
    (None, {"og_site_name": "Acme"}, "Acme"),
    (None, {}, "Unnamed entity"),
])
def test_entity_name_resolution(explicit, facts, expected):
    assert synthesis._entity_name(explicit, [_artifact(facts=facts)]) == expected


def test_dossier_prompt_carries_grounding_rules(monkeypatch):
    seen = {}

    def fake(prompt, **k):
        seen["prompt"] = prompt
        seen["effort"] = k.get("reasoning_effort")
        return "# Research Dossier — Acme\n## Overview\nStuff."

    monkeypatch.setattr(synthesis.client, "complete", fake)
    arts = [_artifact(), _artifact(url="https://x.com/a", platform="instagram",
                                   ok=False, errors=["429 blocked"], text_blocks=[])]

    md = synthesis.build_dossier("Acme", arts)

    p = seen["prompt"]
    assert "## Inferred Insights" in p and "## Gaps & Caveats" in p
    assert "confidence: high|medium|low" in p
    assert "Never invent private facts" in p
    assert "Do not use outside knowledge" in p
    # the failed source's error must NOT be in the prompt (no leak to the model)
    assert "429 blocked" not in p
    assert "{failures}" not in p                 # old placeholder fully removed
    # the prompt instructs neutral handling of thin sources
    assert "No notable public information found" in p
    assert seen["effort"] == settings.llm_reasoning_effort
    assert md.endswith("\n")
