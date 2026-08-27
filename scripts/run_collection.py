#!/usr/bin/env python
"""
Entry point: collect products and reviews for every assigned keyword.

    python scripts/run_collection.py
    python scripts/run_collection.py --limit 3           # smoke test
    python scripts/run_collection.py --keyword "gaming mouse"

Safe to interrupt and re-run: already-collected ASINs are skipped.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scraper.runner import collect          # noqa: E402
from src.scraper.keywords import load_keywords  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Amazon collection pipeline")
    parser.add_argument("--keyword", action="append",
                        help="run a single keyword (repeatable)")
    parser.add_argument("--limit", type=int, default=None,
                        help="max products per keyword")
    parser.add_argument("--first", type=int, default=None,
                        help="only the first N keywords from the CSV")
    args = parser.parse_args()

    keywords = args.keyword or load_keywords()
    if args.first:
        keywords = keywords[: args.first]

    stats = collect(keywords, limit_per_keyword=args.limit)
    return 0 if stats.products_collected else 1


if __name__ == "__main__":
    raise SystemExit(main())
