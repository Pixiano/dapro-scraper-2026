"""Synthesis stage: artifacts → one research dossier, via gpt-oss map-reduce.

Three levels, so arbitrarily large sources still fit the 40k context:
  map    — each source's content is chunked; every chunk → extracted signals
  reduce — a source's chunk-notes → one per-source brief
  final  — all briefs → the dossier (factual layer + Inferred Insights layer)

The Inferred Insights layer is the point of the product, and also the most
dangerous: an ungrounded guess would read as authoritative. Every inference must
carry a confidence and cite the evidence that supports it, and the model is told
to use only the briefs — never outside knowledge about the entity. That part is
prompt-only; the one check done in code is on direct quotations: a quoted phrase
that is not in the collected source text is marked unverified in the dossier.

An empty model reply (budget spent on reasoning) is retried once at low effort.
If a source's notes are still empty the source gets a "synthesis:" error and the
dossier says so; an empty final dossier raises, so the job ends in error."""

import json
import re
from pathlib import Path

from .config import settings
from .llm import client

_SKIP_FACTS = {"provenance", "methods_attempted", "methods_succeeded", "pages_visited"}
_NOTHING = "BRIEF: No notable public information was found for this source."
_QUOTE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
UNVERIFIED = " *(unverified: not verbatim in the sources)*"

MAP_PROMPT = """You are extracting research signals about an entity from ONE source.

{header}

CONTENT:
{content}

Extract concisely and factually:
- What the entity does, offers, or publishes
- Who it appears to target
- Products / services / pricing signals
- Positioning, tone of voice, notable claims
- Any concrete numbers (followers, views, counts, dates)
Quote key phrases verbatim where useful. Only state what the content supports —
do not speculate or use outside knowledge. If there is nothing useful, say so briefly."""

BRIEF_PROMPT = """Combine these extracted notes from a SINGLE source into one brief
of at most 250 words. Keep concrete facts, numbers and quotes; drop repetition.
No speculation.

{header}

NOTES:
{notes}"""

DOSSIER_PROMPT = """You are writing a research dossier about: {entity}

Below are briefs from {n} source(s) collected about this entity.

{briefs}

Write a Markdown document with EXACTLY these sections:

# Research Dossier — {entity}

## Overview
2-4 sentences: who/what this entity is, based only on the evidence below.

## Findings by Source
One `### <platform> — <url>` subsection per source, with the concrete facts found.
If a source yielded nothing notable, write exactly this line and nothing else:
"No notable public information found." NEVER mention HTTP status codes, 404s,
"page not found", errors, blocking, rate limits, or any technical reason a source
was thin — just that neutral line.

## Inferred Insights
Read between the lines. Where the evidence allows, cover: likely business model and
monetization; target audience; brand / market positioning; content strategy;
competitive angle; and any other non-obvious analytics worth knowing.
STRICT RULES for this section:
- Format each item as: **Claim** — *(confidence: high|medium|low)* — Evidence: "<quote or specific fact>" [source]
- EVERY claim needs a confidence and cited evidence from the briefs above.
- Never invent private facts (revenue, headcount, ownership, staff names) that are
  not evidenced. If something is unknown, do not guess it — list it under Gaps.

## Gaps & Caveats
List only substantive things that could not be determined ABOUT THE ENTITY ITSELF
(e.g. financials, headcount, strategy). Do NOT list which sources failed, and never
mention technical errors, 404s, blocking, or data-collection problems of any kind.

Base everything ONLY on the briefs above. Do not use outside knowledge about this
entity, even if you recognise it."""


def _header(artifact: dict) -> str:
    # Raw errors are intentionally NOT included: they must never reach the model,
    # or a technical failure reason (404, blocked, ProfileNotExists...) can leak
    # into the dossier. They remain on the artifact for the UI's per-source view.
    facts = {k: v for k, v in (artifact.get("facts") or {}).items()
             if k not in _SKIP_FACTS}
    lines = [
        f"SOURCE: {artifact.get('platform')} — {artifact.get('url')}",
        f"STATUS: {'collected' if artifact.get('ok') else 'limited data available'}",
    ]
    if facts:
        lines.append("FACTS: " + json.dumps(facts, ensure_ascii=False)[:1200])
    return "\n".join(lines)


def _source_text(artifact: dict) -> str:
    parts = []
    for b in artifact.get("text_blocks") or []:
        parts.append(f"[{b.get('label', 'text')}]\n{b.get('text', '')}")
    for n in artifact.get("vision_notes") or []:
        if n.get("description"):
            ref = Path(str(n.get("ref", ""))).name or "image"
            parts.append(f"[vision: {ref}]\n{n['description']}")
    return "\n\n".join(p for p in parts if p.strip())


def _chunks(text: str, size: int) -> list[str]:
    """Split on paragraph boundaries, packing up to `size` chars per chunk."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(para) > size:  # a single oversized block: hard-split it
            if cur:
                out.append(cur)
                cur = ""
            for i in range(0, len(para), size):
                out.append(para[i:i + size])
            continue
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out or [""]


def _ask(prompt: str, max_tokens: int, effort: str) -> str:
    """An empty reply means gpt-oss spent the whole budget reasoning: retry once
    at low effort. Still empty -> "", the caller decides what that means."""
    out = client.complete(prompt, max_tokens=max_tokens, reasoning_effort=effort).strip()
    if not out:
        out = client.complete(prompt, max_tokens=max_tokens,
                              reasoning_effort="low").strip()
    return out


def summarize_source(artifact: dict) -> str:
    header = _header(artifact)
    text = _source_text(artifact)
    if not text.strip():
        return f"{header}\n{_NOTHING}"
    chunks = _chunks(text, settings.synthesis_chunk_chars)
    notes = [n for ch in chunks
             if (n := _ask(MAP_PROMPT.format(header=header, content=ch),
                           settings.synthesis_map_tokens, "medium"))]
    if len(notes) < len(chunks):  # shown per source in the UI and noted in the dossier
        artifact.setdefault("errors", []).append(
            f"synthesis: the model returned nothing for {len(chunks) - len(notes)} "
            f"of {len(chunks)} part(s) of this source")
    if not notes:
        return f"{header}\n{_NOTHING}"
    joined = "\n\n".join(notes)
    # an empty reduce falls back to the notes themselves: longer, but nothing lost
    brief = joined if len(notes) == 1 else _ask(
        BRIEF_PROMPT.format(header=header, notes=joined),
        settings.synthesis_brief_tokens, "medium") or joined
    return f"{header}\nBRIEF:\n{brief}"


def _words(s: str) -> str:
    return " ".join(re.findall(r"\w+", s.lower()))


def flag_unverified_quotes(md: str, artifacts: list[dict]) -> str:
    """Mark each direct quotation that is not in the collected source text.
    Compared as lower-case words only, so whitespace, punctuation and quote style
    do not matter; "a ... b" passes if both halves are present."""
    # ponytail: exact wording only, so a reworded quote ("we operate" -> "NASA
    # operates") is flagged like an invented one: about 1 in 5 quotes on the old
    # dossiers in data/jobs. Quotes under 3 words are skipped as scare quotes, and
    # an odd number of " on one line mispairs. Fuzzy matching if that is too noisy.
    hay = _words(" ".join(
        f"{a.get('url')} {_source_text(a)} "
        + re.sub(r"\\[nrt]", " ", json.dumps(a.get("facts") or {}, ensure_ascii=False))
        for a in artifacts) + " No notable public information found")

    def mark(m: re.Match) -> str:
        parts = [_words(p) for p in re.split(r"\.{3}|…", m.group(1) or m.group(2))]
        if sum(len(p.split()) for p in parts) < 3 or all(p in hay for p in parts):
            return m.group(0)
        return m.group(0) + UNVERIFIED

    return _QUOTE.sub(mark, md)


def _entity_name(explicit: str | None, artifacts: list[dict]) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for a in artifacts:  # fall back to the best label any collector found
        f = a.get("facts") or {}
        for key in ("channel_title", "og_site_name", "fullName", "title", "og_title"):
            if f.get(key):
                return str(f[key])
    return "Unnamed entity"


def build_dossier(entity_name: str | None, artifacts: list[dict]) -> str:
    entity = _entity_name(entity_name, artifacts)
    briefs = [summarize_source(a) for a in artifacts]
    prompt = DOSSIER_PROMPT.format(
        entity=entity, n=len(artifacts), briefs="\n\n---\n\n".join(briefs))
    md = _ask(prompt, settings.synthesis_dossier_tokens, settings.llm_reasoning_effort)
    if not md:
        raise RuntimeError("the model returned an empty dossier, even at low reasoning effort")
    md = flag_unverified_quotes(md, artifacts)
    lost = [str(a.get("url")) for a in artifacts
            if any(e.startswith("synthesis:") for e in a.get("errors") or [])]
    if lost:
        md += ("\n\n> Note: the model returned nothing for part or all of these sources, "
               "so their content may be missing above: " + ", ".join(lost))
    return md + "\n"
