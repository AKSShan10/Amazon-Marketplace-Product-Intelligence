"""
Browser lifecycle.

One driver is reused across many pages. DriverManager restarts it periodically
because Chrome's memory footprint grows over a long run and a degraded session
starts producing empty pages that look like blocks.

Selenium 4.6+ resolves ChromeDriver automatically via Selenium Manager, so no
driver binary needs to be downloaded or configured.
"""
from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from config import settings
from src.scraper.logger import get_logger

log = get_logger()


def build_driver() -> webdriver.Chrome:
    options = Options()

    if settings.HEADLESS:
        options.add_argument("--headless=new")

    width, height = settings.WINDOW_SIZE
    options.add_argument(f"--window-size={width},{height}")
    options.add_argument(f"--lang={settings.LOCALE}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(settings.PAGE_LOAD_TIMEOUT)
    if settings.IMPLICIT_WAIT:
        driver.implicitly_wait(settings.IMPLICIT_WAIT)
    return driver


class DriverManager:
    """Hands out a driver, recycling it every N pages."""

    def __init__(self, restart_every: int | None = None) -> None:
        self.restart_every = restart_every or settings.DRIVER_RESTART_EVERY
        self._driver: webdriver.Chrome | None = None
        self._pages_served = 0

    @property
    def driver(self) -> webdriver.Chrome:
        if self._driver is None:
            log.info("Starting browser session")
            self._driver = build_driver()
            self._pages_served = 0
        return self._driver

    def page_served(self) -> None:
        self._pages_served += 1
        if self._pages_served >= self.restart_every:
            log.info("Recycling browser after %s pages", self._pages_served)
            self.restart()

    def restart(self) -> None:
        self.quit()
        _ = self.driver  # eagerly rebuild

    def quit(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            self._pages_served = 0

    def __enter__(self) -> "DriverManager":
        return self

    def __exit__(self, *exc) -> None:
        self.quit()
