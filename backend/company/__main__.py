"""CLI: python -m backend.company "<Company name>" [--url https://...]"""

import argparse
import os
import sys
import time

from ..config import settings
from ..llm import server
from .pipeline import run

EXAMPLES = """examples:
  python -m backend.company "Alphabet"
  python -m backend.company "Adaption Labs" --url https://adaptionlabs.ai
  python -m backend.company "OpenAI" --competitors "Anthropic,Mistral AI" --no-vision
  python -m backend.company "Gemini" --url https://www.gemini.com --context crypto --open

A full run takes 2-7 minutes; --competitors, --no-vision and --no-analysis make it faster.
Output: data/company/<company>/<date>/report.pdf (+ report.md, raw.json)."""


TIPS = (("competitors", 'know the rivals?         --competitors "A,B"   (the most reliable list)'),
        ("url", "startup or shared name?  --url https://...   (and --context WORD)"),
        ("no_vision", "slow, or low on memory?  --no-vision --no-analysis"))
KEY_TIP = ("few rivals found?        put BRAVE_API_KEY=... in the .env file "
           "(without it, search is DuckDuckGo only)")


def tips(used: set[str], brave: bool) -> list[str]:
    """Recommendations for the options this run did not use; shown at startup and in --help."""
    return [t for k, t in TIPS if k not in used] + (
        [] if brave or "competitors" in used else [KEY_TIP])


def _mmss(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def summary(r: dict) -> str:
    """The end-of-run text: competitors, item counts per quarter, where the PDF is."""
    data, comps = r["collected"], r["competitors"]["overall"]
    qs = [q["label"] for q in data["quarters"]]
    lines = [f"\nCompetitors ({len(comps)}):"]
    lines += [f"  {c['label']:28.28s} {c['confidence']}" for c in comps] or ["  none found"]
    lines.append(f"\nItems per quarter:\n  {'':28s} " + "  ".join(f"{q:>8s}" for q in qs))
    for e in [data["company"], *data["competitors"]]:
        n = [sum(i["quarter"] == q for i in e["items"]) for q in qs]
        err = f"  ({len(e['errors'])} source errors)" if e["errors"] else ""
        lines.append(f"  {e['label']:28.28s} " + "  ".join(f"{x:8d}" for x in n) + err)
    ls = r["link_stats"]
    lines.append("\nLinks: " + ", ".join(f"{v} {k}" for k, v in ls.items()))
    lines.append("Time:  " + ", ".join(f"{k} {v:.0f}s" for k, v in r["timing_s"].items()))
    lines.append(f"\nReport: {r['pdf']}\n        (raw data in {r['out_dir']})")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m backend.company",
        description="Build a PDF report on a company's AI activity vs its competitors.",
        epilog=EXAMPLES + "\n\nwhich option when:\n  " + "\n  ".join(tips(set(), False)),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="company name, e.g. Alphabet")
    who = ap.add_argument_group("which company and rivals")
    who.add_argument("--url", help="official website; pins the company (startups, ambiguous names)")
    who.add_argument("--context", metavar="WORD", help='extra word required in news/search for a shared '
                     'name, e.g. "Gemini" --context crypto')
    who.add_argument("--competitors", metavar='"A,B"',
                     help='skip the competitor search: "OpenAI,Anthropic"')
    who.add_argument("--categories", type=int, default=5, metavar="N",
                     help="top N product categories to search (default 5)")
    who.add_argument("--per-category", type=int, default=3, metavar="N",
                     help="competitors per category (default 3)")
    who.add_argument("--top", type=int, default=10, metavar="N", help="competitors overall (default 10)")
    what = ap.add_argument_group("what goes in the report")
    what.add_argument("--no-vision", action="store_true",
                      help="skip the homepage screenshots + local vision model notes")
    what.add_argument("--no-analysis", action="store_true",
                      help="skip the local-model analysis paragraphs (summary, meaning, next quarter)")
    what.add_argument("--social", action="store_true",
                      help="add Instagram/Facebook profile summaries (public page, no login)")
    out = ap.add_argument_group("output and run")
    out.add_argument("--bw", action="store_true", help="black-and-white PDF (printing)")
    out.add_argument("--open", action="store_true", help="open the PDF when done")
    out.add_argument("--fresh", action="store_true",
                     help="ignore cached search/Wikidata results (24 h / 30 d) for this run")
    out.add_argument("--debug", action="store_true", help="show full tracebacks on errors")
    if len(sys.argv) == 1:  # started bare: show the examples and tips, not an error
        sys.argv.append("--help")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows cp1252 pipes

    t0 = time.perf_counter()

    def progress(msg: str) -> None:
        print(f"[{_mmss(time.perf_counter() - t0)}] {msg}", flush=True)

    names = [n.strip() for n in (args.competitors or "").split(",") if n.strip()]
    print(f"Building the AI report for {args.name!r} (usually 2-7 min; Ctrl+C to stop)")
    todo = tips({k for k, v in vars(args).items() if v}, bool(settings.brave_api_key))
    if todo:
        print("Tips for next time:\n  " + "\n  ".join(todo))
    try:
        r = run(args.name, args.url, args.categories, args.per_category, args.top, names,
                args.social, vision=not args.no_vision, fresh=args.fresh,
                context=args.context, analysis=not args.no_analysis, bw=args.bw,
                progress=progress)
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
    except Exception as exc:
        if args.debug:
            raise
        sys.exit(f"\nerror: {type(exc).__name__}: {exc}\n"
                 "(check your network; rerun with --debug for the full traceback)")
    finally:
        server.stop()  # free the GPU: no model is needed after the run
    print(f"\nDone in {_mmss(time.perf_counter() - t0)}." + summary(r))
    if args.open and sys.platform == "win32":
        os.startfile(r["pdf"])
    elif args.open:
        print("--open works on Windows only; open the path above.")  # ponytail: no xdg-open/open fallback
    else:
        print(f'Open it:  ii "{r["pdf"]}"   (PowerShell; or add --open next time)')


if __name__ == "__main__":
    main()
