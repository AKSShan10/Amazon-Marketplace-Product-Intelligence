#!/usr/bin/env python
"""
Entry point: validate links and write the deliverable dataset.

    python scripts/run_export.py
    python scripts/run_export.py --skip-link-check
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scraper.export import export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export collected dataset")
    parser.add_argument("--skip-link-check", action="store_true",
                        help="skip URL validation (faster, not for delivery)")
    args = parser.parse_args()

    export(skip_link_check=args.skip_link_check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
