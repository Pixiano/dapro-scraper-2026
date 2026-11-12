"""Server-side PDF generation from a dossier's Markdown, via reportlab.

Pure-Python (no wkhtmltopdf / GTK native deps), so it runs cleanly on Windows.
Renders the same Markdown the UI shows into a styled, paginated PDF.

Fonts: Arial (full family) for the Latin-heavy default; Nirmala UI is registered
as a fallback and used for documents containing Devanagari (e.g. Hindi captions
picked up by the vision stage). Emoji are stripped — no embeddable PDF font
renders colour emoji, so they would otherwise show as empty boxes."""

import re
from datetime import date
from pathlib import Path

from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

_FONTS = r"C:\Windows\Fonts"
PALETTES = {
    "color": {"title": "#1c2330", "h2": "#2c3a52", "h3": "#33518f", "link": "#33518f",
              "rule": "#dfe4ee", "head": "#eef1f7", "zebra": "#f8f9fc",
              "high": "#1a7f37", "medium": "#b26a00", "low": "#7a828e",
              "up": "#1a7f37", "down": "#c62828", "flat": "#5f6b7a", "new": "#1a7f37",
              "none": "#7a828e", "box": "#eef4ff", "boxbar": "#5b8def",
              "warn": "#fff5dc", "warnbar": "#e0a100",
              "first": "#1a7f37", "news": "#3366cc", "hn": "#e8710a", "axis": "#9aa3b2"},
    # B&W: greys only; words and shapes (filled/hollow/square, up/down arrows) carry meaning
    "bw": {"title": "#000000", "h2": "#000000", "h3": "#333333", "link": "#000000",
           "rule": "#bbbbbb", "head": "#e4e4e4", "zebra": "#f6f6f6",
           "high": "#000000", "medium": "#444444", "low": "#888888",
           "up": "#000000", "down": "#000000", "flat": "#666666", "new": "#000000",
           "none": "#888888", "box": "#f2f2f2", "boxbar": "#666666",
           "warn": "#f2f2f2", "warnbar": "#000000",
           "first": "#000000", "news": "#000000", "hn": "#000000", "axis": "#888888"},
}
# {{high}} etc. in the Markdown -> coloured badge; symbols are WGL4 (Arial has them)
TAGS = {"high": "● high", "medium": "● medium", "low": "○ low", "up": "▲ up",
        "down": "▼ down", "flat": "► flat", "new": "+ new", "none": "– none"}
# ponytail: module palette, set per render; fine while PDFs render one at a time
_P = PALETTES["color"]
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
        "title": st("t", fontSize=19, leading=23, spaceAfter=4, textColor=_P["title"]),
        "h2": st("h2", fontSize=14, leading=18, spaceBefore=12, spaceAfter=4, keepWithNext=1,
                 textColor=_P["h2"]),
        "h3": st("h3", fontSize=12, leading=15, spaceBefore=8, spaceAfter=2, keepWithNext=1,
                 textColor=_P["h3"]),
        "body": st("b", fontSize=10.5, leading=15, spaceAfter=5),
        "bullet": st("bul", fontSize=10.5, leading=15, spaceAfter=3,
                     leftIndent=16, bulletIndent=4),
    }


def _inline(s: str) -> str:
    """Markdown inline → reportlab mini-markup, XML-escaping text first."""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # one level of balanced parens (Wikipedia "Foo_(bar)"); a raw " would end href=
    s = re.sub(r"\[([^\]]+)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)",
               lambda m: f'<a href="{m[2].replace(chr(34), "%22")}" color="{_P['link']}">{m[1]}</a>',
               s)
    s = re.sub(r"&lt;(https?://[^&\s]+)&gt;",
               rf'<a href="\1" color="{_P['link']}">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', s)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: f'<font color="{_P[m[1]]}"><b>{TAGS[m[1]]}</b></font>'
                  if m[1] in TAGS else m[0], s)


def render_markdown_pdf(markdown: str, out_path: Path, title: str, bw: bool = False) -> Path:
    """bw: black-and-white palette (print / photocopy friendly)."""
    global _P
    _P = PALETTES["bw" if bw else "color"]
    _register_fonts()
    markdown = _normalize(_EMOJI.sub("", markdown))
    family = _family(markdown)
    styles = _styles(family)

    flow: list = []
    rows: list[list[str]] = []
    quote: list[str] = []
    warn = False
    for raw in markdown.split("\n") + [""]:
        line = raw.rstrip()
        s = line.strip()
        if s.startswith("|"):  # Markdown table row: buffer until the table ends
            rows.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if rows:
            flow.append(_table(rows, styles["body"]))
            rows = []
        if s.startswith(">"):  # "> " box, ">! " warning box; consecutive lines share one
            if quote and warn != s.startswith(">!"):
                flow += [_box(quote, warn, styles["body"]), Spacer(1, 6)]
                quote = []
            warn = s.startswith(">!")
            quote.append(s.lstrip(">!").strip())
            continue
        if quote:
            flow += [_box(quote, warn, styles["body"]), Spacer(1, 6)]
            quote = []
        if not s:
            continue
        if s.startswith("@@timeline"):
            flow += _timeline(s, family)
        elif s.startswith("# "):
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


def _table(rows: list[list[str]], body: ParagraphStyle) -> Table:
    """Pipe table, first row = header; column widths follow text length.
    ponytail: no alignment syntax or cell spans; add when a report needs them"""
    rows = [r for r in rows if not all(re.fullmatch(r":?-{3,}:?", c) for c in r)]
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=11, spaceAfter=0)
    def vis(c: str) -> str:  # what the reader sees: badges expanded, link/markup removed
        c = re.sub(r"\{\{(\w+)\}\}", lambda m: TAGS.get(m[1], m[0]), c)
        return re.sub(r"\[([^\]]+)\]\([^)]*\)|[*`]", lambda m: m[1] or "", c)
    # points: every column fits its longest word ("10", "Capgemini"); the rest of the
    # page width goes to columns with longer text, in proportion to what they lack
    ch, pad = 5.0, 13  # ~avg glyph width at 8.5 pt, cell padding
    low = [max(len(w) for r in rows for w in vis(r[j]).split() or [""]) * ch + pad
           for j in range(n)]
    want = [max(lo, min(max(len(vis(r[j])) for r in rows), 40) * ch + pad)
            for j, lo in enumerate(low)]
    total = A4[0] - 4 * cm
    extra = sum(want) - sum(low)
    room = max(total - sum(low), 0)
    width = [lo + (wa - lo) * min(1, room / extra if extra else 1) for lo, wa in zip(low, want)]
    t = Table([[Paragraph(_inline(c), cell) for c in r] for r in rows],
              colWidths=width, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, _P["rule"]),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), ["#ffffff", _P["zebra"]]),
                           ("BACKGROUND", (0, 0), (-1, 0), _P["head"]),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t

def _box(lines: list[str], warn: bool, body: ParagraphStyle) -> Table:
    """'> ' lines -> tinted box with a left bar; '>! ' lines -> warning box."""
    bg, bar = (_P["warn"], _P["warnbar"]) if warn else (_P["box"], _P["boxbar"])
    t = Table([[[Paragraph(_inline(x), body) for x in lines]]], colWidths=[A4[0] - 4 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _mark(x: float, y: float, kind: str) -> list:
    """Source kind -> shape, so B&W still tells them apart:
    first-party = filled circle, news = hollow circle, Hacker News = square."""
    c = _P[kind]
    if kind == "hn":
        return [Rect(x - 3, y - 3, 6, 6, fillColor=c, strokeColor=c)]
    return [Circle(x, y, 3.2, fillColor=c if kind == "first" else "#ffffff",
                   strokeColor=c, strokeWidth=1.2)]


def _timeline(spec: str, family: str) -> list:
    """'@@timeline 2026-07-01 2026-11-12 | 2026-09-17:1:news, ...' -> drawing + legend.
    Labels [n] stack into rows when dates are close.
    ponytail: greedy row packing; fine for the ~10 items a section lists"""
    head, _, body = spec.partition("|")
    start, end = (date.fromisoformat(x) for x in head.split()[1:3])
    pts = sorted((date.fromisoformat(d), n, k) for d, n, k
                 in (t.strip().split(":") for t in body.split(",") if t.strip()))
    w = A4[0] - 4 * cm
    x0, x1 = 8, w - 8
    span = max((end - start).days, 1)

    def X(d: date) -> float:
        return x0 + (x1 - x0) * min(max((d - start).days, 0), span) / span
    ends: list[float] = []
    placed = []
    for d, n, k in pts:
        x, half = X(d), 3.5 * len(n) + 6
        r = next((i for i, e in enumerate(ends) if e < x - half), len(ends))
        if r == len(ends):
            ends.append(x + half)
        else:
            ends[r] = x + half
        placed.append((x, r, n, k))
    base = 20
    dr = Drawing(w, base + 16 + 11 * max(len(ends), 1))
    dr.add(Line(x0, base, x1, base, strokeColor=_P["axis"], strokeWidth=1))
    m = date(start.year, start.month, 1)
    while m <= end:
        if m >= start:
            q = m.month in (1, 4, 7, 10)
            dr.add(Line(X(m), base - 3, X(m), base + (6 if q else 3),
                        strokeColor=_P["axis"], strokeWidth=1.2 if q else 0.6))
            dr.add(String(X(m) + 2, base - 11,
                          f"Q{(m.month - 1) // 3 + 1} {m:%b}" if q else f"{m:%b}",
                          fontName=family, fontSize=7, fillColor=_P["axis"]))
        m = date(m.year + m.month // 12, m.month % 12 + 1, 1)
    dr.add(String(x1, base - 11, "today", fontName=family, fontSize=7,
                  fillColor=_P["axis"], textAnchor="end"))
    for x, r, n, k in placed:
        y = base + 11 + 11 * r
        if r:
            dr.add(Line(x, base, x, y - 3, strokeColor=_P["axis"], strokeWidth=0.4))
        for s in _mark(x, base, k):
            dr.add(s)
        dr.add(String(x, y, f"[{n}]", fontName=family, fontSize=7, fillColor=_P[k],
                      textAnchor="middle"))
    legend = (f'<font color="{_P["first"]}">●</font> own blog / YouTube &nbsp; '
              f'<font color="{_P["news"]}">○</font> news &nbsp; '
              f'<font color="{_P["hn"]}">■</font> Hacker News &nbsp; · numbers are sources')
    return [dr, Paragraph(legend, ParagraphStyle("lg", fontName=family, fontSize=7.5,
                                                  leading=10, textColor=_P["axis"],
                                                  spaceAfter=6))]



def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor("#8892a6")
    canvas.drawRightString(A4[0] - 2 * cm, 1 * cm, f"Page {doc.page}")
    canvas.drawString(2 * cm, 1 * cm, "Entity Research Aggregator")
    canvas.restoreState()
