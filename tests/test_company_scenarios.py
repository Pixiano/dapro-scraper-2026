"""Live scenario suite for the company report: one general run plus variants that
each flip one branch (named rivals, startup, context word, each flag, odd names).

Real network + local models, 30-45 min:
    pytest -m live tests/test_company_scenarios.py
Each scenario runs the real CLI into data/scenarios/<name>/ and leaves cli.log and
result.json there (competitors, counts, rules broken). A scenario already finished
today is not rerun, so an interrupted run picks up where it stopped."""

import contextlib
import json
import re
import sys
import time
from datetime import date

import pytest

from backend.company import __main__ as cli
from backend.company import pipeline
from backend.pdf import TAGS

OUT = pipeline.OUT.parent / "scenarios"
FAST = ["--no-vision", "--no-analysis"]
SCENARIOS = {
    "general":      ["Alphabet"],
    "named":        ["OpenAI", "--competitors", "Anthropic,Mistral AI", "--no-vision"],
    "startup-url":  ["Adaption Labs", "--url", "https://adaptionlabs.ai", "--no-vision"],
    "context":      ["Gemini", "--url", "https://www.gemini.com", "--context", "crypto",
                     "--no-vision"],
    "subsidiaries": ["CapGemini", "--no-vision"],  # only subsidiaries as products -> self search
    "small-limits": ["Alphabet", "--categories", "2", "--per-category", "2", "--top", "3",
                     "--no-vision"],
    "unknown":      ["Zzqxv Holdings", "--no-vision"],  # nothing to find: must fail soft
    "no-analysis":  ["Alphabet", *FAST],
    "vision-only":  ["OpenAI", "--competitors", "Anthropic", "--no-analysis"],
    "bw":           ["OpenAI", "--competitors", "Anthropic,Mistral AI", *FAST, "--bw"],
    "social":       ["OpenAI", "--competitors", "Anthropic", *FAST, "--social"],
    "fresh":        ["Adaption Labs", "--url", "https://adaptionlabs.ai", *FAST, "--fresh"],
    "lowercase":    ["tata consultancy services", "--top", "3", *FAST],
    "unicode":      ["Nestlé", "--top", "3", *FAST],
    "punctuation":  ["Verizon", "--competitors", "AT&T,T-Mobile US", *FAST],
    "ambiguous":    ["Apple", "--top", "3", *FAST],  # fruit or company, no --url
}


def inspect(argv: list[str], md: str, raw: dict, log: str) -> dict:
    """Report text + raw data + CLI output -> {"problems": rules broken, facts...}."""
    def opt(flag: str) -> str:
        return argv[argv.index(flag) + 1] if flag in argv else ""
    p: list[str] = []
    head, _, rest = md.partition("\n## Sources\n")
    sources = rest.partition("\n## ")[0]
    # the report's own words: no URLs, no headlines (those are other people's text)
    plain = "\n".join(ln for ln in re.sub(r"\]\([^)]*\)", "]", head).split("\n")
                      if not ln.startswith("~ "))
    for pat, what in ((r"\b\d{4}-\d{2}-\d{2}\b", "ISO date"),
                      (r"\bNone\b|\bnan\b|\{'|Traceback", "code text")):
        if m := re.search(pat, plain):
            p.append(f"{what} in the report: ...{plain[max(m.start() - 40, 0):m.end() + 20]!r}")
    p += [f"unknown badge {w!r}" for w in sorted(set(re.findall(r"\{\{(\w+)\}\}", md)) - set(TAGS))]

    cited = {int(n) for n in re.findall(r"\[(\d+)\](?!\()", head)}
    listed = {int(n) for n in re.findall(r"^- \[(\d+)\] ", sources, re.M)}
    if cited - listed:
        p.append(f"cites sources missing from the list: {sorted(cited - listed)[:5]}")
    if listed - cited:
        p.append(f"{len(listed - cited)} listed sources are never cited")
    titles = [t for t, kind in re.findall(r"^- \[\d+\] \[(.*?)\]\(\S+\) · (.*?) \(", sources, re.M)
              if kind != "linked article"]  # an HN post and its article share a title on purpose
    if len(titles) > len(set(titles)):
        p.append(f"{len(titles) - len(set(titles))} sources repeat a title already listed")

    comps = raw["competitors"]["overall"]
    low = [c["label"].lower() for c in comps]
    own = {x.lower() for x in [raw["company"]["label"], *raw["company"].get("own_labels", [])]}
    names = [n for n in opt("--competitors").split(",") if n.strip()]
    if names and len(comps) != len(names):
        p.append(f"{len(names)} rivals named, {len(comps)} in the report")
    if not names and len(comps) > int(opt("--top") or 10):
        p.append("more competitors than --top")
    if len(set(low)) < len(low):
        p.append("a competitor is listed twice")
    if own & set(low):
        p.append("the company is listed as its own competitor")
    if len(re.findall(r"^\| (?:-|\d+) \|", head, re.M)) != 1 + len(comps):
        p.append("scorecard rows do not match the competitor list")

    for flag, mark in (("--no-analysis", "## Summary (analysis)"), ("--no-vision", "## Vision notes")):
        if (flag in argv) == (mark in md):
            p.append(f"{mark!r} {'present despite' if flag in argv else 'missing without'} {flag}")
    if "Done in" not in log or len(re.findall(r"\] \d/5 ", log)) != 5:
        p.append("CLI did not print all 5 steps and finish")
    links = re.search(r"^- (\d+) links fetched: (.*?)\.", rest, re.M)
    dead = re.search(r"dead (\d+)", links[2]) if links else None
    if dead and int(dead[1]) * 10 > int(links[1]):  # type: ignore[index]
        p.append(f"over 10% dead links: {links[2]}")  # type: ignore[index]

    secs = [s for s in head.split("\n## ")[1:] if re.match(r"\d+\. |.*\(the company\)", s)]
    dropped = re.search(r"\((\d+) sentences? failed", md)
    return {"problems": p,
            "competitors": [f"{c['label']} ({c['confidence']})" for c in comps],
            "low_confidence": re.findall(r"^- (.+?): \[", md.partition("## Appendix")[2]
                                         .partition("\n## ")[0], re.M),
            "no_ai_items": [s.split("\n")[0] for s in secs if "\n~ " not in s],
            "analysis_sections": md.count("### What this means"),
            "sentences_dropped": int(dropped[1]) if dropped else 0,
            "links": links[2] if links else "", "timing": raw.get("timing_s")}


def test_inspect_flags_a_broken_report():
    md = ("# r\n## Scorecard\n| - | Acme | x |\n| 1 | Acme | y |\n## Acme (the company)\n"
          "- made 2026-08-01 {{huge}} None [1][3]\n## Sources\n"
          "- [1] [A](http://a) · news (live)\n- [2] [A](http://b) · news (live)\n")
    raw = {"company": {"label": "Acme"},
           "competitors": {"overall": [{"label": "acme", "confidence": "low"}]}}
    got = " | ".join(inspect(["Acme", "--no-vision"], md, raw, "")["problems"])
    for want in ("ISO date", "code text", "huge", "[3]", "never cited", "repeat a title",
                 "own competitor", "Summary (analysis)", "5 steps"):
        assert want in got, want


@pytest.mark.live
@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name, monkeypatch):
    d = OUT / name
    res = d / "result.json"
    if not (res.exists() and json.loads(res.read_text("utf-8"))["date"] == str(date.today())):
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(pipeline, "OUT", d)
        monkeypatch.setattr(sys, "argv", ["company", *SCENARIOS[name]])
        t, exit_ = time.time(), ""
        with open(d / "cli.log", "w", encoding="utf-8") as f, contextlib.redirect_stdout(f):
            try:
                cli.main()
            except SystemExit as exc:
                exit_ = str(exc.code or "")
        r: dict = {"date": str(date.today()), "argv": SCENARIOS[name], "exit": exit_,
                   "seconds": round(time.time() - t)}
        mds = sorted(d.glob("*/*/report.md"), key=lambda f: f.stat().st_mtime)
        if exit_ or not mds:
            r["problems"] = [f"run failed: {exit_.strip() or 'no report written'}"]
        else:
            out = mds[-1].parent
            r["dir"] = str(out)
            r.update(inspect(SCENARIOS[name], mds[-1].read_text("utf-8"),
                             json.loads((out / "raw.json").read_text("utf-8")),
                             (d / "cli.log").read_text("utf-8")))
            if (out / "report.pdf").read_bytes()[:4] != b"%PDF":
                r["problems"].append("report.pdf is not a PDF")
        res.write_text(json.dumps(r, indent=1, ensure_ascii=False), "utf-8")
    r = json.loads(res.read_text("utf-8"))
    assert not r["problems"], "\n".join(r["problems"])
