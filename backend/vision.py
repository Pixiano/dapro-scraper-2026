"""Vision stage: turn each artifact's screenshots and images into text notes.

Runs on the local Qwen3.5-9B vision model (chosen over gemma-4-12B on a real
screenshot bench — see PLAN-V2-AGGREGATOR.md §8). The model loads once for the
whole stage and is swapped out automatically when synthesis needs the text model.

Prompts explicitly forbid guessing: a fabricated view count or name would be
laundered into confident "insight" by the downstream synthesis model, which is
the worst failure mode this pipeline has."""

from pathlib import Path

from .config import settings
from .llm import client

SCREENSHOT_PROMPT = (
    "Transcribe ALL readable text in this screenshot accurately — preserve names, "
    "numbers, dates and labels exactly as written. Do NOT guess or invent text: if "
    "something is unclear, omit it rather than approximating. Then add a final line "
    "starting 'DESIGN:' describing the visual style, layout, colour palette and "
    "overall brand impression in one sentence."
)

VISUAL_PROMPT = (
    "Describe ONLY the visual and brand impression of this screenshot: layout and "
    "structure, colour palette, typography, imagery/photography style, and the "
    "overall brand impression in a few sentences. You may quote prominent headline "
    "or logo text, but do NOT transcribe body text — we already have the page text. "
    "Do NOT guess or invent details you cannot actually see."
)

IMAGE_PROMPT = (
    "Describe this image in 2-3 sentences: subject, style, colours, mood, and any "
    "branding or logos. Transcribe any visible text exactly. Do NOT invent details "
    "you cannot actually see."
)

# Above this many chars of extracted DOM text we trust the text we already have
# and ask vision only for the brand/visual read (cheaper, and it stops the model
# mis-transcribing numbers that would poison synthesis).
TEXT_RICH_CHARS = 400


def is_blank(path: str | Path) -> bool:
    """Near-uniform image (e.g. a blank logged-out social capture) — not worth a
    vision call."""
    try:
        from PIL import Image, ImageStat

        img = Image.open(path).convert("L")
        return ImageStat.Stat(img).stddev[0] < settings.vision_blank_stddev
    except Exception:
        return False


def _targets(artifact: dict) -> list[tuple[str, str, str]]:
    """→ [(kind, ref, path)], screenshots first, capped per source."""
    out: list[tuple[str, str, str]] = []
    for s in artifact.get("screenshots") or []:
        out.append(("screenshot", s, s))
    for im in artifact.get("images") or []:
        p = im.get("local_path")
        if p:
            out.append(("image", im.get("url") or p, p))
    return out[: settings.vision_images_per_source]


def _dom_text_len(artifact: dict) -> int:
    """Total chars of DOM text already extracted for this source."""
    return sum(len(b.get("text") or "") for b in artifact.get("text_blocks") or [])


def analyze_artifact(artifact: dict) -> dict:
    notes: list[dict] = []
    text_rich = _dom_text_len(artifact) >= TEXT_RICH_CHARS
    for kind, ref, path in _targets(artifact):
        name = Path(path).name
        if not Path(path).exists():
            continue
        if settings.vision_skip_blank and is_blank(path):
            notes.append({"ref": ref, "kind": kind, "description": None,
                          "skipped": "blank image"})
            continue
        if kind != "screenshot":
            prompt = IMAGE_PROMPT
        elif text_rich:
            prompt = VISUAL_PROMPT      # text already captured from the DOM
        else:
            prompt = SCREENSHOT_PROMPT  # thin/no text (e.g. login-walled social)
        try:
            desc = client.describe_image(path, prompt,
                                         max_tokens=settings.vision_max_tokens)
            desc = (desc or "").strip()
            if desc:
                notes.append({"ref": ref, "kind": kind, "description": desc})
            else:
                notes.append({"ref": ref, "kind": kind, "description": None,
                              "skipped": "model returned nothing"})
        except Exception as exc:  # one bad image must not sink the job
            artifact.setdefault("errors", []).append(
                f"vision {name}: {type(exc).__name__}: {exc}")
    artifact["vision_notes"] = notes
    return artifact


def analyze(artifacts: list[dict]) -> list[dict]:
    return [analyze_artifact(a) for a in artifacts]
