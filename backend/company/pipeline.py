"""The whole company pipeline; shared by the CLI and the localhost page."""

import json
import re
import time
from datetime import date

from ..config import BASE_DIR
from . import collect, competitors, report, wikidata

OUT = BASE_DIR / "data" / "company"


def run(name: str, url: str | None = None, categories: int = 5, per_category: int = 3,
        top: int = 10, names: list[str] | None = None, social: bool = False) -> dict:
    t0 = time.perf_counter()
    company = wikidata.resolve(name, top=categories, url=url)
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
    raw = {"company": company, "competitors": comps, "collected": data,
           "timing_s": {"resolve": round(t1 - t0, 1), "competitors": round(t2 - t1, 1),
                        "collect": round(t3 - t2, 1)}}
    rep = report.build(raw, out_dir)
    raw["link_checks"] = rep["checks"]
    raw["timing_s"]["report"] = round(time.perf_counter() - t3, 1)
    (out_dir / "raw.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    return {**raw, "out_dir": out_dir, "pdf": rep["pdf"], "link_stats": rep["link_stats"]}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"
