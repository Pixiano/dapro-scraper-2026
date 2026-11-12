"""CLI: python -m backend.company "<Company name>" [--url https://...]"""

import argparse
import sys

from ..llm import server
from .pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.company")
    ap.add_argument("name", help="company name, e.g. Alphabet")
    ap.add_argument("--url", help="official website; pins the company (startups, ambiguous names)")
    ap.add_argument("--categories", type=int, default=5, help="top N categories")
    ap.add_argument("--per-category", type=int, default=3, help="competitors per category")
    ap.add_argument("--top", type=int, default=10, help="competitors overall")
    ap.add_argument("--competitors", help='skip search: "OpenAI,Anthropic"')
    ap.add_argument("--no-vision", action="store_true",
                    help="skip the homepage screenshots + local vision model notes")
    ap.add_argument("--bw", action="store_true", help="black-and-white PDF (printing)")
    ap.add_argument("--no-analysis", action="store_true",
                    help="skip the local-model analysis paragraphs (summary, meaning, next quarter)")
    ap.add_argument("--social", action="store_true",
                    help="add Instagram/Facebook profile summaries (public page, no login)")
    ap.add_argument("--context", help='extra word required in news/search for a shared '
                    'name, e.g. "Gemini" --context crypto')
    ap.add_argument("--fresh", action="store_true",
                    help="ignore cached search/Wikidata results (24 h / 30 d) for this run")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows cp1252 pipes

    names = [n.strip() for n in (args.competitors or "").split(",") if n.strip()]
    try:
        r = run(args.name, args.url, args.categories, args.per_category, args.top, names,
                args.social, vision=not args.no_vision, fresh=args.fresh,
                context=args.context, analysis=not args.no_analysis, bw=args.bw)
    finally:
        server.stop()  # free the GPU: no model is needed after the run
    data = r["collected"]
    for e in [data["company"], *data["competitors"]]:
        counts = {q["label"]: sum(i["quarter"] == q["label"] for i in e["items"])
                  for q in data["quarters"]}
        print(f"{e['label']:28.28s} {counts}  errors={len(e['errors'])}")
    print(f"links {r['link_stats']}\ntiming {r['timing_s']}\n"
          f"raw: {r['out_dir'] / 'raw.json'}\npdf: {r['pdf']}")


if __name__ == "__main__":
    main()
