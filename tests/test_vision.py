"""Offline tests for the vision stage: model calls mocked."""

import pytest
from PIL import Image, ImageDraw

from backend import vision
from backend.config import settings


def _img(path, color="white", text=None):
    im = Image.new("RGB", (300, 200), color)
    if text:
        ImageDraw.Draw(im).text((10, 90), text, fill="black")
    im.save(path)
    return str(path)


def test_is_blank_detects_uniform(tmp_path):
    assert vision.is_blank(_img(tmp_path / "blank.png")) is True
    assert vision.is_blank(_img(tmp_path / "busy.png", text="LOTS OF TEXT HERE")) is False


def test_blank_screenshots_skipped_without_model_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vision.client, "describe_image",
                        lambda *a, **k: calls.append(a) or "should not run")
    art = {"screenshots": [_img(tmp_path / "blank.png")], "images": []}

    vision.analyze_artifact(art)

    assert calls == []                                   # no model call wasted
    assert art["vision_notes"][0]["skipped"] == "blank image"
    assert art["vision_notes"][0]["description"] is None


def _capture(monkeypatch):
    seen = []

    def fake(path, prompt, **k):
        seen.append(prompt)
        return "  transcribed text\nDESIGN: clean  "

    monkeypatch.setattr(vision.client, "describe_image", fake)
    return seen


def test_notes_populated_and_prompt_differs_by_kind(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    art = {
        "screenshots": [_img(tmp_path / "s.png", text="SHOT")],
        "images": [{"url": "http://x/a.jpg", "local_path": _img(tmp_path / "a.png", text="IMG")}],
    }

    vision.analyze_artifact(art)

    notes = art["vision_notes"]
    assert len(notes) == 2
    assert notes[0]["kind"] == "screenshot" and notes[1]["kind"] == "image"
    assert notes[1]["ref"] == "http://x/a.jpg"           # image ref is its URL
    assert notes[0]["description"] == "transcribed text\nDESIGN: clean"
    # no DOM text on this artifact → full OCR prompt for the screenshot
    assert seen[0] is vision.SCREENSHOT_PROMPT and seen[1] is vision.IMAGE_PROMPT
    # anti-hallucination instruction must survive in both prompts
    assert "Do NOT" in seen[0] and "Do NOT" in seen[1]


def test_text_rich_artifact_uses_visual_prompt(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    art = {
        "screenshots": [_img(tmp_path / "s.png", text="SHOT")],
        "images": [{"local_path": _img(tmp_path / "a.png", text="IMG")}],
        "text_blocks": [{"label": "main", "text": "x" * 200},
                        {"label": "about", "text": "y" * 200}],  # 400 = threshold
    }

    vision.analyze_artifact(art)

    assert seen[0] is vision.VISUAL_PROMPT   # text already known → brand read only
    assert seen[1] is vision.IMAGE_PROMPT    # images always get the image prompt
    assert "Do NOT" in vision.VISUAL_PROMPT  # anti-hallucination survives


def test_thin_text_artifact_uses_full_ocr_prompt(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    art = {
        "screenshots": [_img(tmp_path / "s.png", text="SHOT")],
        "images": [],
        "text_blocks": [{"label": "main", "text": "x" * (vision.TEXT_RICH_CHARS - 1)}],
    }

    vision.analyze_artifact(art)

    assert seen == [vision.SCREENSHOT_PROMPT]


def test_visual_prompt_forbids_body_transcription():
    assert "not transcribe body text" in vision.VISUAL_PROMPT.lower()
    assert "DESIGN:" not in vision.VISUAL_PROMPT


def test_cap_limits_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vision_images_per_source", 3)
    monkeypatch.setattr(vision.client, "describe_image", lambda *a, **k: "note")
    art = {"screenshots": [_img(tmp_path / f"s{i}.png", text=f"S{i}") for i in range(5)],
           "images": [{"local_path": _img(tmp_path / "i.png", text="I")}]}

    vision.analyze_artifact(art)
    assert len(art["vision_notes"]) == 3                 # screenshots prioritised


def test_model_failure_contained(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cuda oom")

    monkeypatch.setattr(vision.client, "describe_image", boom)
    art = {"screenshots": [_img(tmp_path / "s.png", text="X")], "images": [], "errors": []}

    vision.analyze_artifact(art)                          # must not raise

    assert art["vision_notes"] == []
    assert any("cuda oom" in e for e in art["errors"])


def test_missing_file_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(vision.client, "describe_image", lambda *a, **k: "note")
    art = {"screenshots": [str(tmp_path / "nope.png")], "images": []}
    vision.analyze_artifact(art)
    assert art["vision_notes"] == []


def test_analyze_processes_all_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(vision.client, "describe_image", lambda *a, **k: "note")
    arts = [{"screenshots": [_img(tmp_path / f"a{i}.png", text=f"T{i}")], "images": []}
            for i in range(2)]
    out = vision.analyze(arts)
    assert all(len(a["vision_notes"]) == 1 for a in out)
