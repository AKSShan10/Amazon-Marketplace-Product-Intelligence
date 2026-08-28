"""
scraper/browser.py
==================
Owns the Chrome window: creating it, checking it is alive, and rebuilding it
when it is not.

WHY THIS FILE EXISTS
--------------------
In your previous run the browser session died silently. The code kept calling
a dead driver, so every request failed in about 16 seconds (that was just the
retry delays - no page was actually being loaded). It never recovered, and the
last three keywords were lost.

Two things fix that:
  FIX #1  restart the browser after N failures in a row, not only after N pages
  FIX #6  probe the browser before each request to notice a dead session early
"""

import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.remote.remote_connection import RemoteConnection

import config
from scraper.logs import get_logger

log = get_logger()


# ===========================================================================
# FIX #9 - THE MOST IMPORTANT FIX IN THIS FILE
# ===========================================================================
# set_page_load_timeout() only works while ChromeDriver is still ANSWERING us.
#
# When Chrome itself freezes (usually out of memory), ChromeDriver stops
# replying, and Selenium's HTTP request to it waits FOREVER - there is no
# default socket timeout. That is the 63-minute silence in your run:
#
#     09:03:09  last successful page
#     ...       63 minutes of nothing
#     10:06:42  InvalidSessionIdException  (Chrome was already dead)
#
# We cap every command sent to ChromeDriver. If one does not answer within
# COMMAND_TIMEOUT seconds, Selenium raises instead of hanging, and the browser
# gets rebuilt.
#
# Selenium moved this setting between versions, so we try each known way and
# fall back to a plain socket timeout. Never let this crash the program.
def _apply_command_timeout(driver, seconds):
    """
    Cap how long we wait for ANY reply from ChromeDriver.

    Selenium keeps moving this setting between versions, so we try the known
    places in order and fall back to a process-wide socket timeout. This must
    never raise - a missing timeout is bad, a crash at startup is worse.
    """
    # Selenium 4.28+ : each connection carries a ClientConfig
    try:
        driver.command_executor.client_config.timeout = seconds
        return f"client_config {seconds}s"
    except Exception:
        pass

    # Older Selenium : a classmethod on RemoteConnection
    try:
        RemoteConnection.set_timeout(seconds)
        return f"set_timeout {seconds}s"
    except Exception:
        pass

    # Last resort : cap every socket in the process.
    # Safe here because requests (used for link checking) passes its own
    # explicit timeout, which takes priority over this default.
    try:
        import socket
        socket.setdefaulttimeout(seconds)
        return f"socket default {seconds}s"
    except Exception:
        return "NOT APPLIED - hangs possible"


_TIMEOUT_METHOD = "not set yet"


def _build_chrome():
    """
    Create one Chrome window with our settings.

    Note: we do NOT download chromedriver. Selenium 4.6+ ships "Selenium
    Manager", which finds and installs the right driver automatically.
    """
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument(f"--window-size={config.WINDOW_WIDTH},{config.WINDOW_HEIGHT}")
    options.add_argument("--lang=en-US")

    # Hides the "Chrome is being controlled by automated software" banner.
    # This is cosmetic - it does not defeat any access control.
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    # Stability flags. Without these Chrome can crash on low-memory machines.
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=options)

    # Three timeouts, each guarding a different failure.
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)   # slow page
    driver.set_script_timeout(config.SCRIPT_TIMEOUT)         # FIX #2 hung JS

    global _TIMEOUT_METHOD
    _TIMEOUT_METHOD = _apply_command_timeout(driver, config.COMMAND_TIMEOUT)

    return driver


class Browser:
    """
    A self-healing wrapper around the Chrome driver.

    Use it like this:

        with Browser() as browser:
            browser.driver.get("https://...")
            browser.page_done()          # after each page
            browser.record_error()       # when a page fails
            browser.record_success()     # when a page works
    """

    def __init__(self):
        self._driver = None          # the real Selenium driver (created lazily)
        self._pages_since_restart = 0
        self._errors_in_a_row = 0
        self._warmed_up = False

    # ------------------------------------------------------------------
    # Getting the driver
    # ------------------------------------------------------------------
    @property
    def driver(self):
        """
        Return a working driver, creating one if needed.

        FIX #6: before handing the driver back we ask it a trivial question
        (driver.title). If the session is dead, Selenium raises here, and we
        quietly build a fresh browser instead of returning a corpse.
        """
        if self._driver is None:
            self._driver = _build_chrome()
            # Reported AFTER building, because _build_chrome is what applies it
            log.info("Browser started (command timeout: %s)", _TIMEOUT_METHOD)
            self._pages_since_restart = 0
            self._warmed_up = False
            return self._driver

        try:
            _ = self._driver.title          # cheap liveness probe
        except WebDriverException:
            log.warning("Browser session is dead - rebuilding")
            self.restart()

        return self._driver

    # ------------------------------------------------------------------
    # Health tracking
    # ------------------------------------------------------------------
    def page_done(self):
        """Call after every page load. Triggers the scheduled recycle."""
        self._pages_since_restart += 1
        if self._pages_since_restart >= config.RESTART_AFTER_PAGES:
            log.info("Recycling browser after %s pages", self._pages_since_restart)
            self.restart()

    def record_success(self):
        """A page worked - clear the failure streak."""
        self._errors_in_a_row = 0

    def record_error(self):
        """
        A page failed.

        FIX #1: if enough failures happen back to back, the browser itself is
        probably the problem, so rebuild it. This is what your run needed at
        13:17 and again at 16:06.
        """
        self._errors_in_a_row += 1
        if self._errors_in_a_row >= config.RESTART_AFTER_ERRORS:
            log.warning(
                "%s failures in a row - rebuilding browser",
                self._errors_in_a_row,
            )
            self.restart()
            self._errors_in_a_row = 0

    # ------------------------------------------------------------------
    # Session warm-up
    # ------------------------------------------------------------------
    def warm_up(self):
        """
        FIX #3: visit the Amazon homepage once before the first search.

        A brand-new browser has no cookies. Amazon greets cold sessions with a
        consent / location interstitial instead of real content. That is why
        'wireless earbuds' - your very first keyword - failed with 'empty'
        while keyword 2 onwards worked fine.

        Loading the homepage first gives the session normal cookies.
        """
        if self._warmed_up:
            return
        try:
            log.info("Warming up session (loading homepage)")
            self.driver.get(config.DOMAIN)
            time.sleep(3)
            self._warmed_up = True
        except WebDriverException as exc:
            log.warning("Warm-up failed (continuing anyway): %s", type(exc).__name__)

    # ------------------------------------------------------------------
    # Safe JavaScript
    # ------------------------------------------------------------------
    def run_script(self, script, *args):
        """
        FIX #2: run JavaScript, but never let it hang the whole program.

        set_script_timeout() (in _build_chrome) caps how long Chrome may take.
        We also swallow errors, because scrolling is a nice-to-have: it must
        never be the reason a page fails.
        """
        try:
            return self.driver.execute_script(script, *args)
        except WebDriverException:
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def restart(self):
        """Close the current window and open a fresh one."""
        self.quit()
        self._driver = _build_chrome()
        self._pages_since_restart = 0
        self._warmed_up = False
        log.info("Browser restarted (command timeout: %s)", _TIMEOUT_METHOD)

    def quit(self):
        """Close the browser. Safe to call even if it is already gone."""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass          # already dead - nothing to clean up
            self._driver = None

    # `with Browser() as b:` support - guarantees the window always closes
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.quit()
