"""Tests for server-side PDF rendering (reportlab, offline)."""

from backend import pdf

SAMPLE = """# Research Dossier — Acme

## Overview
Acme makes **robots** for *warehouses*. Visit [site](https://acme.io) or
<https://acme.io/about>.

## Inferred Insights
- **Sells hardware** — *(confidence: high)* — Evidence: "we sell robots" [website]
- Uses `SQLite` internally

---

## Gaps
Nothing else known.
"""


def test_renders_valid_pdf(tmp_path):
    out = pdf.render_markdown_pdf(SAMPLE, tmp_path / "d.pdf", "Acme")
    assert out.exists()
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"        # valid PDF header
    assert data.rstrip().endswith(b"%%EOF")
    assert len(data) > 1500            # non-trivial content


def test_emoji_stripped_and_devanagari_font_selected():
    # emoji removed so no tofu boxes
    assert pdf._EMOJI.sub("", "Great ✨🥀 job 🙏") == "Great  job "
    pdf._register_fonts()
    # a Devanagari doc picks the Indic family when available, else falls back
    fam = pdf._family("एक अ लॉयर टेक्स्ट")
    assert fam in ("Indic", "Arial", "Helvetica")
    assert pdf._family("plain english") in ("Arial", "Helvetica")


def test_inline_escapes_then_marks_up():
    out = pdf._inline("a **b** and <tag> and [x](https://y.z)")
    assert "&lt;tag&gt;" in out              # raw HTML escaped
    assert "<b>b</b>" in out                 # bold applied after escaping
    assert '<a href="https://y.z"' in out    # link applied


def test_empty_markdown_still_valid(tmp_path):
    out = pdf.render_markdown_pdf("", tmp_path / "e.pdf", "Empty")
    assert out.read_bytes()[:5] == b"%PDF-"
