"""Server-side PDF generation from a dossier's Markdown, via reportlab.

Pure-Python (no wkhtmltopdf / GTK native deps), so it runs cleanly on Windows.
Renders the same Markdown the UI shows into a styled, paginated PDF.

Fonts: Arial (full family) for the Latin-heavy default; Nirmala UI is registered
as a fallback and used for documents containing Devanagari (e.g. Hindi captions
picked up by the vision stage). Emoji are stripped — no embeddable PDF font
renders colour emoji, so they would otherwise show as empty boxes."""

import re
from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

_FONTS = r"C:\Windows\Fonts"
_registered = False
_have_arial = False
_have_indic = False

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
    "|️|‍")

# Exotic punctuation gpt-oss emits that common PDF fonts lack a glyph for
# (would render as □). Map to safe equivalents rather than dropping content.
_NORMALIZE = {
    "‑": "-", "‒": "-",              # non-breaking / figure hyphen
    " ": " ", " ": " ", " ": " ",  # nbsp / narrow nbsp / figure
    " ": " ", " ": " ",              # thin / hair space
    "​": "", "‌": "", "⁠": "", "﻿": "",  # zero-width
}
_NORMALIZE_RE = re.compile("|".join(map(re.escape, _NORMALIZE)))


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(lambda m: _NORMALIZE[m.group()], text)


def _register_fonts() -> None:
    global _registered, _have_arial, _have_indic
    if _registered:
        return
    _registered = True
    a = {k: Path(_FONTS) / v for k, v in {
        "n": "arial.ttf", "b": "arialbd.ttf", "i": "ariali.ttf", "bi": "arialbi.ttf"}.items()}
    if all(p.exists() for p in a.values()):
        pdfmetrics.registerFont(TTFont("Arial", str(a["n"])))
        pdfmetrics.registerFont(TTFont("Arial-Bold", str(a["b"])))
        pdfmetrics.registerFont(TTFont("Arial-Italic", str(a["i"])))
        pdfmetrics.registerFont(TTFont("Arial-BoldItalic", str(a["bi"])))
        registerFontFamily("Arial", normal="Arial", bold="Arial-Bold",
                           italic="Arial-Italic", boldItalic="Arial-BoldItalic")
        _have_arial = True
    ttc = Path(_FONTS) / "Nirmala.ttc"
    if ttc.exists():
        try:
            pdfmetrics.registerFont(TTFont("Indic", str(ttc), subfontIndex=0))
            # Nirmala ships regular-only here; map bold/italic to regular.
            registerFontFamily("Indic", normal="Indic", bold="Indic",
                               italic="Indic", boldItalic="Indic")
            _have_indic = True
        except Exception:
            _have_indic = False


def _family(text: str) -> str:
    if _DEVANAGARI.search(text) and _have_indic:
        return "Indic"
    return "Arial" if _have_arial else "Helvetica"


def _styles(family: str) -> dict:
    def st(name, **kw):
        return ParagraphStyle(name, fontName=family, alignment=TA_LEFT, **kw)
    return {
        "title": st("t", fontSize=19, leading=23, spaceAfter=4, textColor="#1c2330"),
        "h2": st("h2", fontSize=14, leading=18, spaceBefore=12, spaceAfter=4,
                 textColor="#2c3a52"),
        "h3": st("h3", fontSize=12, leading=15, spaceBefore=8, spaceAfter=2,
                 textColor="#33518f"),
        "body": st("b", fontSize=10.5, leading=15, spaceAfter=5),
        "bullet": st("bul", fontSize=10.5, leading=15, spaceAfter=3,
                     leftIndent=16, bulletIndent=4),
    }


def _inline(s: str) -> str:
    """Markdown inline → reportlab mini-markup, XML-escaping text first."""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               r'<a href="\2" color="#33518f">\1</a>', s)
    s = re.sub(r"&lt;(https?://[^&\s]+)&gt;",
               r'<a href="\1" color="#33518f">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', s)
    return s


def render_markdown_pdf(markdown: str, out_path: Path, title: str) -> Path:
    _register_fonts()
    markdown = _normalize(_EMOJI.sub("", markdown))
    family = _family(markdown)
    styles = _styles(family)

    flow = []
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            flow.append(Paragraph(_inline(s[2:]), styles["title"]))
            flow.append(HRFlowable(width="100%", thickness=1.2, color="#dfe4ee",
                                   spaceBefore=3, spaceAfter=8))
        elif s.startswith("## "):
            flow.append(Paragraph(_inline(s[3:]), styles["h2"]))
        elif s.startswith("### "):
            flow.append(Paragraph(_inline(s[4:]), styles["h3"]))
        elif re.match(r"^([-*])\s+", s):
            flow.append(Paragraph(_inline(re.sub(r"^([-*])\s+", "", s)),
                                  styles["bullet"], bulletText="•"))
        elif re.match(r"^-{3,}$", s):
            flow.append(HRFlowable(width="100%", thickness=0.7, color="#eef1f7",
                                   spaceBefore=6, spaceAfter=6))
        else:
            flow.append(Paragraph(_inline(s), styles["body"]))
    if not flow:
        flow.append(Paragraph("(empty dossier)", styles["body"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4, title=title or "Research Dossier",
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm)
    doc.build(flow, onLaterPages=_footer, onFirstPage=_footer)
    return out_path


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor("#8892a6")
    canvas.drawRightString(A4[0] - 2 * cm, 1 * cm, f"Page {doc.page}")
    canvas.drawString(2 * cm, 1 * cm, "Entity Research Aggregator")
    canvas.restoreState()
