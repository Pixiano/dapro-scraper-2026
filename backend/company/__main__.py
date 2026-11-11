"""CLI: python -m backend.company "<Company name>" [--url https://...]"""

import argparse
import json
import re
import sys
import time
from datetime import date

from ..config import BASE_DIR
from . import collect, competitors, wikidata


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.company")
    ap.add_argument("name", help="company name, e.g. Alphabet")
    ap.add_argument("--url", help="official website; pins the company (startups, ambiguous names)")
    ap.add_argument("--categories", type=int, default=5, help="top N categories")
    ap.add_argument("--per-category", type=int, default=3, help="competitors per category")
    ap.add_argument("--top", type=int, default=10, help="competitors overall")
    ap.add_argument("--competitors", help='skip search: "OpenAI,Anthropic"')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows cp1252 pipes

    t0 = time.perf_counter()
    company = wikidata.resolve(args.name, top=args.categories, url=args.url)
    if not company["links"]["website"]:
        company["links"]["website"] = competitors.guess_website(args.name, company["errors"])
        company["website_guessed"] = bool(company["links"]["website"])
    t1 = time.perf_counter()
    names = [n.strip() for n in (args.competitors or "").split(",") if n.strip()]
    comps = competitors.discover(company, args.per_category, args.top, names or None)
    t2 = time.perf_counter()
    data = collect.collect(company, comps["overall"])
    t3 = time.perf_counter()

    out_dir = BASE_DIR / "data" / "company" / slug(company["label"]) / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = {"company": company, "competitors": comps, "collected": data,
           "timing_s": {"resolve": round(t1 - t0, 1), "competitors": round(t2 - t1, 1),
                        "collect": round(t3 - t2, 1)}}
    (out_dir / "raw.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    for e in [data["company"], *data["competitors"]]:
        counts = {q["label"]: sum(i["quarter"] == q["label"] for i in e["items"])
                  for q in data["quarters"]}
        print(f"{e['label']:28.28s} {counts}  errors={len(e['errors'])}")
    print(f"timing {raw['timing_s']}\nraw: {out_dir / 'raw.json'}")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"


if __name__ == "__main__":
    main()
