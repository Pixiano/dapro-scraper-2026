"""The whole company pipeline; shared by the CLI and the localhost page."""

import json
import re
import time
from datetime import date

from .. import company as pkg
from ..config import BASE_DIR
from . import collect, competitors, report, visual, wikidata

OUT = BASE_DIR / "data" / "company"


def run(name: str, url: str | None = None, categories: int = 5, per_category: int = 3,
        top: int = 10, names: list[str] | None = None, social: bool = False,
        vision: bool = True, fresh: bool = False, context: str | None = None) -> dict:
    pkg.FRESH = fresh  # ponytail: module flag; fine while runs are one at a time (web: 1 worker)
    t0 = time.perf_counter()
    company = wikidata.resolve(name, top=categories, url=url)
    company["context"] = (context or "").strip() or None
    if not company["links"]["website"]:
        company["links"]["website"] = competitors.guess_website(name, company["errors"])
        company["website_guessed"] = bool(company["links"]["website"])
    t1 = time.perf_counter()
    comps = competitors.discover(company, per_category, top, names or None)
    t2 = time.perf_counter()
    data = collect.collect(company, comps["overall"], social=social)
    t3 = time.perf_counter()

    out_dir = OUT / slug(company["label"]) / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    ents = [data["company"], *data["competitors"]]
    for e, n in zip(ents, visual.notes(ents, out_dir) if vision else [None] * len(ents)):
        e["vision"] = n  # model output: report labels it unverified, counts ignore it
    t4 = time.perf_counter()
    raw = {"company": company, "competitors": comps, "collected": data,
           "timing_s": {"resolve": round(t1 - t0, 1), "competitors": round(t2 - t1, 1),
                        "collect": round(t3 - t2, 1), "vision": round(t4 - t3, 1)}}
    rep = report.build(raw, out_dir)
    raw["link_checks"] = rep["checks"]
    raw["timing_s"]["report"] = round(time.perf_counter() - t4, 1)
    (out_dir / "raw.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    return {**raw, "out_dir": out_dir, "pdf": rep["pdf"], "link_stats": rep["link_stats"]}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"
