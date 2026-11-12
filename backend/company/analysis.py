"""Local-model analysis for the company report (gpt-oss, same server as the creator
dossiers): a summary, the company vs its rivals, and per company "what this means"
plus a next-quarter prediction.

Model output, checked by code before it reaches the report. A claim is dropped
unless it cites existing source numbers, has a high/medium/low confidence, and
uses no number missing from the facts the model was given (no invented "$5B").
Fail soft: no model / bad JSON -> {"error": ...}, the report still builds."""

import json
import re

from ..llm import client

SYSTEM = ("You are a careful technology analyst. You write only what the given facts "
          "support, and you cite them. Reply with JSON only.")
PROMPT = """Below are verified facts about {company} and its competitors: counts from a
scorecard, rule-based outlooks, and numbered news headlines/posts [n]. Today is {today}.
The last full quarter is {last}; the current quarter so far is {cur}; "next quarter" is {nxt}.

Write an analysis in plain, readable English, as JSON with this exact shape:
{{"summary": [claims],   // 4-6 claims: who is doing what in AI, who is accelerating, where {company} stands
  "compare": [claims],   // 2-4 claims: where {company} leads or lags its competitors in AI
  "companies": {{"<exact company name>": {{"means": [claims], "next": [claims]}}}}}}
"means": 2-3 claims reading that company's headlines together (strategy, partners, themes).
"next": 1-3 claims predicting what it will do in AI in {nxt}, based on its trend and headlines.
Include every company listed below in "companies", using its exact name.

Each claim is {{"text": "...", "confidence": "high|medium|low", "sources": [n, ...]}}.
Rules:
- Every claim cites 1-4 source numbers [n] from the facts that support it. Every
  product, partner or person you name must appear in one of the headlines you cite.
- Use no number that is not written in the facts (no amounts, dates or percentages from memory).
- No outside knowledge: if the facts do not show it, do not say it.
- Compare what the headlines cover; do not say one company "leads" or is "better"
  unless a cited headline says so.
- confidence: high = stated directly by several sources; medium = one source or a clear
  pattern; low = a reasonable guess from thin evidence. Predictions are at most medium.
- Write full sentences without the [n] markers inside "text".

FACTS
{facts}"""
NUM = re.compile(r"\d+(?:[.,]\d+)*")
CONF = {"high", "medium", "low"}


def _names(text: str) -> list[str]:
    """Capitalised words not starting a sentence: the names a claim relies on."""
    words, out = text.split(), []
    for prev, w in zip([""] + words, words):
        w = re.sub(r"(?:['’]s)?[^\w-]*$", "", w.strip("\"'“‘(["))
        # "Q4" etc.: words with digits are the number check's job
        if prev and not prev.endswith((".", "!", "?", ":")) and w[:1].isupper() \
                and not re.search(r"\d", w):
            out.append(w)
    return out


def check(c: object, refs: dict[int, str], allowed: set[str], own: str) -> str | None:
    """-> why the claim is dropped, or None if it passes. refs: source number ->
    headline; own: the companies' names (always allowed)."""
    if not isinstance(c, dict) or not str(c.get("text") or "").strip():
        return "empty"
    if c.get("confidence") not in CONF:
        return "no confidence"
    src = c.get("sources")
    if not isinstance(src, list) or not src or not all(isinstance(s, int) and s in refs
                                                       for s in src):
        return "bad citation"
    text = str(c["text"])
    if any(n not in allowed for n in NUM.findall(text)):
        return "number not in the facts"
    # ponytail: word match, not meaning; catches "Claude" cited to a Qantas headline
    cited = " ".join(refs[s] for s in src).lower() + " " + own
    if any(w.lower() not in cited and w.lower().removesuffix("s") not in cited  # LLMs ~ LLM
           for w in _names(text)):
        return "name not in the cited headlines"
    return None


def parse(text: str, names: list[str], refs: dict[int, str], facts: str) -> dict:
    """Model reply -> checked claims. Unknown company names are ignored;
    predictions, and "high" claims citing one source, are lowered to medium."""
    js = json.loads(text[text.index("{"):text.rindex("}") + 1])
    allowed = set(NUM.findall(facts))
    own = " ".join(names).lower()
    dropped: list[str] = []

    def ok(xs: object, cap: bool = False) -> list[dict]:
        out = []
        for c in xs if isinstance(xs, list) else []:
            why = check(c, refs, allowed, own)
            if why:
                dropped.append(why)
            else:
                # high = several sources agree (the prompt's own definition)
                conf = ("medium" if c["confidence"] == "high"
                        and (cap or len(set(c["sources"])) < 2) else c["confidence"])
                out.append({"text": str(c["text"]).strip(), "confidence": conf,
                            "sources": c["sources"]})
        return out
    comps = js.get("companies") if isinstance(js.get("companies"), dict) else {}
    return {"summary": ok(js.get("summary")), "compare": ok(js.get("compare")),
            "companies": {n: {"means": ok((comps.get(n) or {}).get("means")),
                              "next": ok((comps.get(n) or {}).get("next"), cap=True)}
                          for n in names},
            "dropped": dropped, "error": None}


def run(facts: str, names: list[str], refs: dict[int, str], **fmt: str) -> dict:
    """Free text first; on broken JSON, one retry with llama.cpp's JSON mode.
    "raw" keeps the last reply for the audit file next to the report."""
    text, err = "", ""
    for json_mode in (False, True):
        try:
            text = client.complete(PROMPT.format(facts=facts, **fmt), system=SYSTEM,
                                   max_tokens=8000, temperature=0.2,
                                   reasoning_effort="medium", json_mode=json_mode)
            return {**parse(text, names, refs, facts), "raw": text}
        except (ValueError, AttributeError) as exc:  # bad / non-object JSON: retry
            err = f"{type(exc).__name__}: {str(exc)[:160]}"
        except Exception as exc:  # no server / model / GPU: no point retrying
            return {"error": f"{type(exc).__name__}: {str(exc)[:160]}", "raw": text}
    return {"error": err, "raw": text}


def prose(claims: list[dict]) -> str:
    """Claims -> one readable paragraph, confidence and sources after each sentence."""
    return " ".join(f"{c['text'].rstrip('.')}. *({c['confidence']})* "
                    + "".join(f"[{s}]" for s in c["sources"]) for c in claims)
