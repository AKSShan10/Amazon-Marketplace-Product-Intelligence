"""
scraper/fetch.py
================
Every page request in the project goes through this one function.

WHAT IT GUARANTEES
------------------
  * it never crashes the program - a page that cannot be loaded returns None
  * every attempt (including retries) writes exactly one row to request_log.csv
  * every failure gets a plain-English REASON, so you can diagnose without
    re-running anything

WHAT IT DOES NOT DO
-------------------
  * it never types a username or password
  * it never solves a CAPTCHA
  * it never uses a proxy or fakes a browser fingerprint
  If Amazon insists on a login or a puzzle, we log it and move on.
"""

import random
import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from scraper import selectors
from scraper.logs import get_logger, log_request

log = get_logger()

# How many blocks have happened back to back, across the whole run.
_consecutive_blocks = 0
_total_blocks = 0

# The "Continue shopping" page is Amazon's soft speed bump. A few is normal.
# Many in a row means we are being throttled, so we slow ourselves down - the
# alternative is being escalated to real CAPTCHAs.
_interstitials = 0
_extra_delay = 0.0


def total_blocks():
    """Used by the run summary."""
    return _total_blocks


def total_interstitials():
    """Used by the run summary."""
    return _interstitials


# ===========================================================================
# SMALL HELPERS
# ===========================================================================
def _pause():
    """
    Wait a random few seconds so our requests do not look machine-timed.

    _extra_delay grows when Amazon keeps showing the "Continue shopping"
    interstitial - that is a throttling signal, and backing off is both
    politer and more effective than pushing through.
    """
    time.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY) + _extra_delay)


def _scroll(browser):
    """
    Scroll down the page in steps, then back to the top.

    WHY: Amazon does not load product images or the video block until they are
    near the screen. Without scrolling you get grey placeholder images and no
    video markup at all.

    browser.run_script() is used instead of driver.execute_script() because it
    has a timeout and swallows errors - scrolling must never break a fetch.
    """
    height = browser.run_script("return document.body.scrollHeight") or 0
    for step in range(1, config.SCROLL_STEPS + 1):
        target = int(height * step / config.SCROLL_STEPS)
        browser.run_script("window.scrollTo(0, arguments[0]);", target)
        time.sleep(config.SCROLL_PAUSE)
    browser.run_script("window.scrollTo(0, 0);")
    time.sleep(0.3)


def _handle_continue_button(browser):
    """
    Amazon sometimes shows a near-empty page with a "Continue shopping" button.

    This is NOT a CAPTCHA - there is no puzzle, no login, nothing to defeat.
    It behaves like a cookie banner. Clicking it is a project decision you
    control via config.CLICK_CONTINUE_BUTTON.

    Returns True if a button was clicked (so the caller can re-read the page).
    """
    if not config.CLICK_CONTINUE_BUTTON:
        return False

    for selector in selectors.CONTINUE_BUTTON:
        try:
            buttons = browser.driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for button in buttons:
            try:
                if button.is_displayed():
                    button.click()
                    time.sleep(2.5)
                    return True
            except WebDriverException:
                continue
    return False


# ===========================================================================
# DIAGNOSIS - what kind of page are we actually looking at?
# ===========================================================================
def _diagnose(browser, html, anchor):
    """
    Decide what happened, and say WHY in plain English.

    Returns a tuple: (outcome, reason)
      outcome is one of: success | blocked | empty
    """
    # --- Check the URL first ------------------------------------------------
    # FIX #4: Amazon sometimes REDIRECTS a /dp/ page to the sign-in page when
    # it suspects automation. Your run looked like the scraper was "pressing
    # sign in" - it was not. The browser was sent there by Amazon.
    # We detect it, log it as blocked, and never interact with that page.
    try:
        current = (browser.driver.current_url or "").lower()
    except WebDriverException:
        current = ""

    if "/ap/signin" in current or "/ap/cvf" in current:
        return "blocked", "Amazon redirected to the sign-in page (not entered)"

    if "validatecaptcha" in current or "/errors/" in current:
        return "blocked", "Amazon redirected to a CAPTCHA / error page"

    # --- Then look for CAPTCHA text in the page body ------------------------
    for marker in selectors.CAPTCHA_MARKERS:
        if marker in html:
            return "blocked", "CAPTCHA / robot-check page shown"

    # --- Then check the real content is present -----------------------------
    # FIX #5: previously a page was called "empty" if it was shorter than 5000
    # characters. That was a bad rule - Amazon's cold-start consent page is
    # short but not empty, and some valid pages are compact.
    # Now we ask a direct question: is the container we need actually here?
    if anchor:
        try:
            found = browser.driver.find_elements(By.CSS_SELECTOR, anchor)
        except WebDriverException:
            found = []
        if not found:
            return "empty", f"page loaded but '{anchor}' was not present"

    if len(html) < 2000:
        return "empty", f"page body was tiny ({len(html)} characters)"

    return "success", ""


# ===========================================================================
# THE MAIN FUNCTION
# ===========================================================================
def fetch(browser, url, keyword, req_type, asin="", anchor=None, scroll=True):
    """
    Load one page and return its HTML, or None if it could not be collected.

    Parameters
    ----------
    browser : Browser        the self-healing browser wrapper
    url     : str            the page to load
    keyword : str            which search keyword this belongs to (for the log)
    req_type: str            "search" or "product"
    asin    : str            product id, if this is a product page
    anchor  : str            CSS selector that MUST exist on a good page
    scroll  : bool           whether to scroll (needed for images and video)

    Returns
    -------
    str or None
    """
    global _consecutive_blocks, _total_blocks

    for attempt in range(1, config.MAX_RETRIES + 1):
        started = time.time()

        try:
            _pause()

            # browser.driver checks the session is alive before returning it
            driver = browser.driver
            driver.get(url)

            # Wait for the anchor element, but do not treat a timeout as fatal.
            # _diagnose() below makes the real decision.
            if anchor:
                try:
                    WebDriverWait(driver, config.ELEMENT_TIMEOUT).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, anchor))
                    )
                except TimeoutException:
                    pass

            # If the "Continue shopping" speed bump appeared, dismiss it and
            # give the real page a chance to load.
            if _handle_continue_button(browser):
                global _interstitials, _extra_delay
                _interstitials += 1

                # Every few interstitials, add a second to the base delay
                # (capped) and say so, so the slowdown is never mysterious.
                if _interstitials % 5 == 0 and _extra_delay < config.MAX_EXTRA_DELAY:
                    _extra_delay += 1.0
                    log.warning(
                        "      %s interstitials so far - slowing down "
                        "(now +%.0fs per request)", _interstitials, _extra_delay)
                else:
                    log.info("      dismissed 'Continue shopping' interstitial")
                if anchor:
                    try:
                        WebDriverWait(driver, config.ELEMENT_TIMEOUT).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, anchor))
                        )
                    except TimeoutException:
                        pass

            if scroll:
                _scroll(browser)

            html = driver.page_source or ""
            elapsed = time.time() - started

            outcome, reason = _diagnose(browser, html, anchor)

            # ---------------- BLOCKED ----------------
            if outcome == "blocked":
                _consecutive_blocks += 1
                _total_blocks += 1
                browser.record_error()
                log_request(keyword, req_type, url, "blocked", reason,
                            asin, attempt, elapsed)
                log.warning("      blocked (attempt %s): %s", attempt, reason)

                if _consecutive_blocks >= config.BLOCK_LIMIT_BEFORE_COOLOFF:
                    log.warning("%s blocks in a row - pausing %s seconds",
                                _consecutive_blocks, config.COOLOFF_SECONDS)
                    time.sleep(config.COOLOFF_SECONDS)
                    _consecutive_blocks = 0
                else:
                    time.sleep(config.BACKOFF_SECONDS * attempt)
                continue

            # ---------------- EMPTY ----------------
            if outcome == "empty":
                browser.record_error()
                log_request(keyword, req_type, url, "empty", reason,
                            asin, attempt, elapsed)
                log.warning("      empty (attempt %s): %s", attempt, reason)
                _save_debug_page(url, html)
                time.sleep(config.BACKOFF_SECONDS * attempt / 2)
                continue

            # ---------------- SUCCESS ----------------
            _consecutive_blocks = 0
            browser.record_success()
            log_request(keyword, req_type, url, "success", "",
                        asin, attempt, elapsed)
            return html

        # -------------------- TIMEOUT --------------------
        except TimeoutException:
            elapsed = time.time() - started
            browser.record_error()
            log_request(keyword, req_type, url, "error",
                        f"page did not finish loading in {config.PAGE_LOAD_TIMEOUT}s",
                        asin, attempt, elapsed)

        # -------------------- BROWSER PROBLEM --------------------
        except WebDriverException as exc:
            elapsed = time.time() - started
            name = type(exc).__name__

            # These four mean Chrome itself is GONE, not that the page failed.
            # Retrying against a dead session is pointless - it just burns
            # attempts, which is exactly what your log shows (24 rows, all
            # InvalidSessionIdException, ~5s each because nothing loaded).
            # So we rebuild the browser IMMEDIATELY rather than waiting for
            # the error counter to reach its limit.
            session_dead = name in (
                "InvalidSessionIdException",
                "NoSuchWindowException",
                "NoSuchSessionException",
                "MaxRetryError",
            ) or "invalid session id" in str(exc).lower()

            if session_dead:
                reason = f"Chrome died ({name}) - rebuilding browser now"
                log_request(keyword, req_type, url, "error", reason,
                            asin, attempt, elapsed)
                log.warning("      %s", reason)
                browser.restart()
                browser.warm_up()
                continue          # retry with the fresh browser

            browser.record_error()
            log_request(keyword, req_type, url, "error", name,
                        asin, attempt, elapsed)

        # -------------------- ANYTHING ELSE --------------------
        except Exception as exc:
            elapsed = time.time() - started
            browser.record_error()
            log_request(keyword, req_type, url, "error",
                        f"unexpected {type(exc).__name__}: {exc}",
                        asin, attempt, elapsed)

    log.warning("   gave up on %s after %s attempts", url, config.MAX_RETRIES)
    return None


def _save_debug_page(url, html):
    """
    Normally does nothing - raw HTML is NOT saved.

    Turn on config.SAVE_FAILED_PAGES only when a selector breaks and you want
    one sample page to inspect in a text editor.
    """
    if not config.SAVE_FAILED_PAGES:
        return
    import hashlib
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.md5(url.encode()).hexdigest()[:12] + ".html"
    (config.DEBUG_DIR / name).write_text(html, encoding="utf-8")
