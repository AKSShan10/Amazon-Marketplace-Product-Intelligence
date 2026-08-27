"""
Request/response audit log - a graded deliverable, not a debugging aid.

Every request attempt produces exactly one row, including retries.
Written from inside the fetch layer so it can never drift from reality.
"""
from __future__ import annotations

import csv
import logging
import threading
from typing import Optional

from config import settings
from src.common.utils import utc_now

FIELDS = [
    "timestamp_utc",
    "keyword",
    "request_type",   # search | product
    "url",
    "asin",
    "attempt",
    "http_ok",
    "outcome",        # success | blocked | empty | error
    "note",
    "elapsed_sec",
    "page_chars",
    "raw_file",
]

OUTCOMES = {"success", "blocked", "empty", "error"}

_lock = threading.Lock()
_initialised = False


def _init_file() -> None:
    global _initialised
    if _initialised and settings.REQUEST_LOG.exists():
        return
    settings.REQUEST_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not settings.REQUEST_LOG.exists():
        with open(settings.REQUEST_LOG, "w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, FIELDS).writeheader()
    _initialised = True


def log_request(
    keyword: str,
    request_type: str,
    url: str,
    *,
    asin: Optional[str] = None,
    attempt: int = 1,
    http_ok: bool = True,
    outcome: str = "success",
    note: str = "",
    elapsed_sec: Optional[float] = None,
    page_chars: Optional[int] = None,
    raw_file: str = "",
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}, got {outcome!r}")

    row = {
        "timestamp_utc": utc_now(),
        "keyword": keyword,
        "request_type": request_type,
        "url": url,
        "asin": asin or "",
        "attempt": attempt,
        "http_ok": http_ok,
        "outcome": outcome,
        "note": note,
        "elapsed_sec": round(elapsed_sec, 2) if elapsed_sec is not None else "",
        "page_chars": page_chars if page_chars is not None else "",
        "raw_file": raw_file,
    }

    with _lock:
        _init_file()
        with open(settings.REQUEST_LOG, "a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, FIELDS).writerow(row)


# ---------------------------------------------------------------------------
# Console / run log
# ---------------------------------------------------------------------------
def get_logger(name: str = "scraper") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(settings.RUN_LOG, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
