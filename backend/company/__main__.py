"""CLI: python -m backend.company "<Company name>" """

import argparse
import json

from . import wikidata


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.company")
    ap.add_argument("name", help="company name, e.g. Alphabet")
    ap.add_argument("--categories", type=int, default=5, help="top N categories")
    args = ap.parse_args()
    print(json.dumps(wikidata.resolve(args.name, top=args.categories),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
