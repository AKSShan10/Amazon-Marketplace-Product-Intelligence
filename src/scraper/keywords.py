"""Load and normalise the assigned search keyword list."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from config import settings
from src.scraper.logger import get_logger

log = get_logger()


def load_keywords(path: Optional[Path] = None) -> list[str]:
    path = path or settings.INPUT_CSV
    if not path.exists():
        raise FileNotFoundError(
            f"Keyword file not found: {path}\n"
            f"Expected a CSV with columns '{settings.SN_COLUMN}' and "
            f"'{settings.KEYWORD_COLUMN}'."
        )

    frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    frame.columns = [str(c).strip() for c in frame.columns]

    column = _resolve_column(frame)
    keywords = (
        frame[column]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s != ""]
        .drop_duplicates()
        .tolist()
    )

    log.info("Loaded %s keywords from %s", len(keywords), path.name)
    return keywords


def _resolve_column(frame: pd.DataFrame) -> str:
    if settings.KEYWORD_COLUMN in frame.columns:
        return settings.KEYWORD_COLUMN
    # tolerate minor header variations
    for candidate in frame.columns:
        if "keyword" in candidate.lower():
            return candidate
    raise KeyError(
        f"No keyword column found. Columns present: {list(frame.columns)}. "
        f"Expected '{settings.KEYWORD_COLUMN}'."
    )
