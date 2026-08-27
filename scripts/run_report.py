#!/usr/bin/env python
"""
Entry point: print the M1 completeness report.

    python scripts/run_report.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scraper.report import generate  # noqa: E402

if __name__ == "__main__":
    generate()
