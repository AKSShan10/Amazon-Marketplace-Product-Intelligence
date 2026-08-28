"""
scraper/logs.py
===============
Two logs, two purposes.

  logs/request_log.csv   the audit trail. One row per request ATTEMPT,
                         including retries. This is a graded deliverable -
                         acceptance criterion 7.3 says it must match the work
                         you actually did.

  logs/run.txt           the human-readable story of the run, the same lines
                         you see in your terminal.

THE GOLDEN RULE OF THIS FILE
----------------------------
Logging must NEVER crash the scraper.

If you open request_log.csv in Excel while the scraper is running, Windows
locks the file and Python cannot append to it. That used to raise
PermissionError from inside the fetch layer and kill the whole run - losing
hours of work because a spreadsheet was open.

Now a failed write is buffered in memory and retried on the next call. Nothing
is lost, and the scrape keeps going. You are warned once, not on every row.
"""

import csv
import logging
import time
from datetime import datetime, timezone

import config

# The exact columns of request_log.csv, in order.
COLUMNS = [
    "timestamp",      # when the attempt happened (UTC)
    "keyword",        # which search keyword we were working on
    "type",           # "search" or "product"
    "url",            # the URL requested
    "asin",           # product id, blank for search pages
    "attempt",        # 1, 2 or 3
    "outcome",        # success | blocked | empty | error
    "reason",         # short human explanation - THE IMPORTANT COLUMN
    "seconds",        # how long the attempt took
]

VALID_OUTCOMES = {"success", "blocked", "empty", "error"}

# Rows we could not write yet (file locked). Flushed on the next successful
# write, and again at the end of the run.
_pending = []
_warned_about_lock = False
_header_written = False


def _write_rows(rows):
    """
    Try to append rows to the CSV.

    Returns True if written, False if the file is locked or unavailable.
    NEVER raises - that is the whole point of this module.
    """
    global _header_written

    if not rows:
        return True

    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        need_header = not _header_written and not config.REQUEST_LOG.exists()

        with open(config.REQUEST_LOG, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if need_header:
                writer.writerow(COLUMNS)
            writer.writerows(rows)

        _header_written = True
        return True

    except (PermissionError, OSError):
        # File open in Excel, or the disk is unavailable. Not fatal.
        return False


def log_request(keyword, req_type, url, outcome, reason="",
                asin="", attempt=1, seconds=None):
    """
    Record one request attempt.

    `reason` is what makes this log useful to a human. Instead of a bare
    'blocked' you get 'redirected to sign-in page', so you can diagnose
    without re-running anything.

    This function never raises. A locked file means the row waits in memory.
    """
    global _warned_about_lock

    if outcome not in VALID_OUTCOMES:
        # A bad value is a programming mistake, but even that must not kill a
        # long scrape. Record it honestly and carry on.
        reason = f"[invalid outcome '{outcome}'] {reason}"
        outcome = "error"

    _pending.append([
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        keyword, req_type, url, asin, attempt, outcome, reason,
        round(seconds, 1) if seconds is not None else "",
    ])

    # Try to flush everything we are holding, oldest first.
    if _write_rows(_pending):
        _pending.clear()
    elif not _warned_about_lock:
        _warned_about_lock = True
        get_logger().warning(
            "Cannot write %s - it is probably open in Excel. Rows are held in "
            "memory and written once the file is free. CLOSE THE FILE; the "
            "scrape continues either way.",
            config.REQUEST_LOG.name,
        )


def flush(retries=3):
    """
    Called at the end of a run. Final effort to write buffered rows.

    If the file is still locked, write them to a timestamped fallback rather
    than losing them - the audit log is a graded deliverable.
    """
    global _warned_about_lock

    if not _pending:
        return

    for _ in range(retries):
        if _write_rows(_pending):
            get_logger().info("Wrote %s buffered log rows", len(_pending))
            _pending.clear()
            _warned_about_lock = False
            return
        time.sleep(2)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = config.LOGS_DIR / f"request_log_{stamp}.csv"
    try:
        with open(fallback, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(_pending)
        get_logger().warning(
            "%s is still locked. Wrote %s rows to %s instead - merge it into "
            "the main log afterwards.",
            config.REQUEST_LOG.name, len(_pending), fallback.name,
        )
        _pending.clear()
    except OSError as exc:
        get_logger().error("Could not save %s buffered log rows: %s",
                           len(_pending), exc)


def pending_count():
    """How many rows are waiting to be written."""
    return len(_pending)


# ---------------------------------------------------------------------------
# Console / run log
# ---------------------------------------------------------------------------
def get_logger(name="scraper"):
    """
    Return a logger that prints to the terminal AND writes to logs/run.txt.

    If run.txt cannot be opened (also lockable on Windows), we still return a
    working console logger rather than crashing.
    """
    logger = logging.getLogger(name)

    if logger.handlers:        # already configured
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

    # Handler 1: the terminal - must always work
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Handler 2: logs/run.txt - nice to have, never required.
    # delay=True means the file is not opened until the first message, so an
    # import never fails because of a locked file.
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        to_file = logging.FileHandler(config.RUN_LOG, encoding="utf-8", delay=True)
        to_file.setFormatter(fmt)
        logger.addHandler(to_file)
    except OSError:
        pass                   # console-only is fine

    return logger
