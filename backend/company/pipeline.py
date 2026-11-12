"""The whole company pipeline; shared by the CLI and the localhost page."""

import json
import re
import time
from collections.abc import Callable
from datetime import date

from .. import company as pkg
from ..config import BASE_DIR
from . import collect, competitors, report, visual, wikidata

OUT = BASE_DIR / "data" / "company"


def run(name: str, url: str | None = None, categories: int = 5, per_category: int = 3,
        top: int = 10, names: list[str] | None = None, social: bool = False,
        vision: bool = True, fresh: bool = False, context: str | None = None,
        analysis: bool = True, bw: bool = False,
        progress: Callable[[str], None] | None = None) -> dict:
    """progress (optional): called with one line per stage, for the CLI."""
    say = progress or (lambda _msg: None)
    pkg.FRESH = fresh  # ponytail: module flag; fine while runs are one at a time (web: 1 worker)
    t0 = time.perf_counter()
    say(f"1/5 resolve      looking up {name!r} on Wikidata")
    company = wikidata.resolve(name, top=categories, url=url)
    company["context"] = (context or "").strip() or None
    if not company["links"]["website"]:
        company["links"]["website"] = competitors.guess_website(name, company["errors"])
        company["website_guessed"] = bool(company["links"]["website"])
    if company["on_wikidata"]:
        say(f"               found {company['label']} ({company['qid']}), "
            f"{len(company['categories'])} product categories")
    else:
        say("               not on Wikidata: searched as a startup"
            + ("" if url else " (add --url https://... to pin it)"))
    if any("Connect" in e for e in company["errors"]):
        say("               warning: network errors (offline?); the report may be empty")
    t1 = time.perf_counter()
    say("2/5 competitors  " + (f"looking up {len(names)} given names" if names else
                               "web search per category (~15 s each)"))
    comps = competitors.discover(company, per_category, top, names or None)
    t2 = time.perf_counter()
    say(f"3/5 collect      news, blogs, YouTube for {1 + len(comps['overall'])} companies")
    data = collect.collect(company, comps["overall"], social=social)
    t3 = time.perf_counter()

    out_dir = OUT / slug(company["label"]) / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    ents = [data["company"], *data["competitors"]]
    say("4/5 vision       " + ("homepage screenshots + local vision model" if vision
                               else "skipped (--no-vision)"))
    for e, n in zip(ents, visual.notes(ents, out_dir) if vision else [None] * len(ents)):
        e["vision"] = n  # model output: report labels it unverified, counts ignore it
    t4 = time.perf_counter()
    raw = {"company": company, "competitors": comps, "collected": data,
           "timing_s": {"resolve": round(t1 - t0, 1), "competitors": round(t2 - t1, 1),
                        "collect": round(t3 - t2, 1), "vision": round(t4 - t3, 1)}}
    say("5/5 report       link checks" + (", local-model analysis" if analysis else "")
        + ", PDF")
    rep = report.build(raw, out_dir, use_model=analysis, bw=bw)
    raw["link_checks"] = rep["checks"]
    raw["timing_s"]["report"] = round(time.perf_counter() - t4, 1)
    (out_dir / "raw.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    return {**raw, "out_dir": out_dir, "pdf": rep["pdf"], "link_stats": rep["link_stats"]}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"
