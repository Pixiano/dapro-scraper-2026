"""CLI: python -m backend.company "<Company name>" [--url https://...]"""

import argparse
import sys

from .pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.company")
    ap.add_argument("name", help="company name, e.g. Alphabet")
    ap.add_argument("--url", help="official website; pins the company (startups, ambiguous names)")
    ap.add_argument("--categories", type=int, default=5, help="top N categories")
    ap.add_argument("--per-category", type=int, default=3, help="competitors per category")
    ap.add_argument("--top", type=int, default=10, help="competitors overall")
    ap.add_argument("--competitors", help='skip search: "OpenAI,Anthropic"')
    ap.add_argument("--social", action="store_true",
                    help="add Instagram/Facebook profile summaries (public page, no login)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows cp1252 pipes

    names = [n.strip() for n in (args.competitors or "").split(",") if n.strip()]
    r = run(args.name, args.url, args.categories, args.per_category, args.top, names,
            args.social)
    data = r["collected"]
    for e in [data["company"], *data["competitors"]]:
        counts = {q["label"]: sum(i["quarter"] == q["label"] for i in e["items"])
                  for q in data["quarters"]}
        print(f"{e['label']:28.28s} {counts}  errors={len(e['errors'])}")
    print(f"links {r['link_stats']}\ntiming {r['timing_s']}\n"
          f"raw: {r['out_dir'] / 'raw.json'}\npdf: {r['pdf']}")


if __name__ == "__main__":
    main()
