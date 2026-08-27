"""
The single choke point for every page request.

Guarantees:
  * never raises - a page that cannot be collected returns FetchResult(html=None)
  * every attempt, including retries, produces exactly one request-log row
  * every successful page is written to disk as raw evidence
  * lazy-loaded content (images, media block) is triggered before capture
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Optional

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import settings
from src.common.utils import url_hash
from src.scraper import selectors
from src.scraper.logger import get_logger, log_request

log = get_logger()


@dataclass
class FetchResult:
    html: Optional[str]
    outcome: str            # success | blocked | empty | error
    attempts: int
    raw_file: str = ""

    @property
    def ok(self) -> bool:
        return self.html is not None


class BlockTracker:
    """Escalates to a long cool-off when blocks arrive back to back."""

    def __init__(self) -> None:
        self.consecutive = 0
        self.total = 0

    def record_block(self) -> None:
        self.consecutive += 1
        self.total += 1

    def record_success(self) -> None:
        self.consecutive = 0

    def should_cool_off(self) -> bool:
        return self.consecutive >= settings.CONSECUTIVE_BLOCK_LIMIT

    def cool_off(self) -> None:
        log.warning(
            "%s consecutive blocks - cooling off for %s seconds",
            self.consecutive, settings.COOL_OFF_SECONDS,
        )
        time.sleep(settings.COOL_OFF_SECONDS)
        self.consecutive = 0


_tracker = BlockTracker()


def block_tracker() -> BlockTracker:
    return _tracker


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _polite_pause() -> None:
    time.sleep(random.uniform(settings.MIN_DELAY, settings.MAX_DELAY))


def _looks_blocked(html: str) -> bool:
    return any(marker in html for marker in selectors.BLOCK_MARKERS)


def _save_html(url: str, html: str) -> str:
    settings.RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.RAW_HTML_DIR / f"{url_hash(url)}.html"
    path.write_text(html, encoding="utf-8")
    return str(path.relative_to(settings.ROOT))


def _trigger_lazy_content(driver) -> None:
    """
    Progressive scroll to the bottom and back.

    Amazon defers product images AND the video/media block until they approach
    the viewport. Capturing page_source without this yields placeholder image
    URIs and no video markup - and video markup that never rendered cannot be
    recovered by re-parsing, only by re-scraping.
    """
    try:
        height = driver.execute_script("return document.body.scrollHeight") or 0
        for step in range(1, settings.SCROLL_PASSES + 1):
            target = int(height * step / settings.SCROLL_PASSES)
            driver.execute_script("window.scrollTo(0, arguments[0]);", target)
            time.sleep(settings.SCROLL_PAUSE)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.4)
    except WebDriverException:
        pass  # scrolling is best-effort; never fail the fetch over it


def _await_anchor(driver, anchor_selector: Optional[str]) -> None:
    if not anchor_selector:
        return
    try:
        WebDriverWait(driver, settings.ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, anchor_selector))
        )
    except TimeoutException:
        pass  # absence is handled downstream by the length/marker checks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_page(
    driver,
    url: str,
    keyword: str,
    request_type: str,
    *,
    asin: Optional[str] = None,
    anchor_selector: Optional[str] = None,
    scroll: bool = True,
) -> FetchResult:
    last_outcome = "error"

    for attempt in range(1, settings.MAX_RETRIES + 1):
        started = time.time()
        try:
            _polite_pause()
            driver.get(url)
            _await_anchor(driver, anchor_selector)
            if scroll:
                _trigger_lazy_content(driver)

            html = driver.page_source or ""
            elapsed = time.time() - started

            if _looks_blocked(html):
                last_outcome = "blocked"
                _tracker.record_block()
                log_request(
                    keyword, request_type, url, asin=asin, attempt=attempt,
                    http_ok=True, outcome="blocked", note="captcha/robot page",
                    elapsed_sec=elapsed, page_chars=len(html),
                )
                if _tracker.should_cool_off():
                    _tracker.cool_off()
                else:
                    time.sleep(settings.BACKOFF_BASE * attempt)
                continue

            if len(html) < settings.MIN_PAGE_CHARS:
                last_outcome = "empty"
                log_request(
                    keyword, request_type, url, asin=asin, attempt=attempt,
                    http_ok=True, outcome="empty", note="page below minimum length",
                    elapsed_sec=elapsed, page_chars=len(html),
                )
                time.sleep(settings.BACKOFF_BASE * attempt / 2)
                continue

            raw_file = _save_html(url, html)
            _tracker.record_success()
            log_request(
                keyword, request_type, url, asin=asin, attempt=attempt,
                http_ok=True, outcome="success", elapsed_sec=elapsed,
                page_chars=len(html), raw_file=raw_file,
            )
            return FetchResult(html=html, outcome="success", attempts=attempt, raw_file=raw_file)

        except TimeoutException:
            last_outcome = "error"
            log_request(
                keyword, request_type, url, asin=asin, attempt=attempt,
                http_ok=False, outcome="error", note="page load timeout",
                elapsed_sec=time.time() - started,
            )
        except WebDriverException as exc:
            last_outcome = "error"
            log_request(
                keyword, request_type, url, asin=asin, attempt=attempt,
                http_ok=False, outcome="error", note=type(exc).__name__,
                elapsed_sec=time.time() - started,
            )
        except Exception as exc:  # defensive: the fetch layer must never raise
            last_outcome = "error"
            log_request(
                keyword, request_type, url, asin=asin, attempt=attempt,
                http_ok=False, outcome="error", note=f"unexpected: {type(exc).__name__}",
                elapsed_sec=time.time() - started,
            )

    log.warning("Giving up on %s after %s attempts (%s)", url, settings.MAX_RETRIES, last_outcome)
    return FetchResult(html=None, outcome=last_outcome, attempts=settings.MAX_RETRIES)
