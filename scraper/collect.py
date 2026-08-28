"""
scraper/collect.py
==================
The conductor. It decides WHAT to fetch and in WHICH ORDER.

All the hard parts live elsewhere on purpose:
    browser.py   keeps Chrome alive
    fetch.py     handles blocks, retries, sign-in redirects
    parser.py    reads the HTML
    storage.py   saves and resumes

So this file stays short enough to read in one sitting.

FLOW
    for each keyword:
        load the search page
        get the list of products on page 1
        for each product:
            load the product page
            parse it
            save it immediately
    then: retry anything that failed
"""

import time
from urllib.parse import quote_plus

import pandas as pd

import config
from scraper import selectors
from scraper.browser import Browser
from scraper.fetch import fetch, total_blocks, total_interstitials
from scraper.logs import flush as flush_logs, get_logger, pending_count
from scraper.parser import parse_product, parse_search
from scraper.storage import Storage, load_failures, save_failures

log = get_logger()


# ===========================================================================
# INPUT
# ===========================================================================
def load_keywords():
    """Read the keyword list from data/keywords.csv."""
    if not config.KEYWORDS_CSV.exists():
        raise FileNotFoundError(
            f"Keyword file missing: {config.KEYWORDS_CSV}\n"
            f"It needs a column named '{config.KEYWORD_COLUMN}'."
        )

    frame = pd.read_csv(config.KEYWORDS_CSV)
    frame.columns = [str(c).strip() for c in frame.columns]

    column = config.KEYWORD_COLUMN
    if column not in frame.columns:
        # be forgiving about the exact header text
        matches = [c for c in frame.columns if "keyword" in c.lower()]
        if not matches:
            raise KeyError(f"No keyword column found. Columns: {list(frame.columns)}")
        column = matches[0]

    keywords = (frame[column].dropna().astype(str).str.strip()
                .loc[lambda s: s != ""].drop_duplicates().tolist())

    log.info("Loaded %s keywords", len(keywords))
    return keywords


def search_url(keyword):
    """Turn 'gaming mouse' into a proper search URL."""
    return config.SEARCH_URL.format(query=quote_plus(keyword))


def product_url(asin):
    return config.PRODUCT_URL.format(asin=asin)


# ===========================================================================
# ONE KEYWORD
# ===========================================================================
def collect_keyword(browser, storage, keyword, limit=None):
    """
    Collect every product on page 1 for one keyword.

    Returns (products_saved, failed_asins, search_worked)
    """
    # ---- step 1: the search page ----
    html = fetch(browser, search_url(keyword), keyword, "search",
                 anchor=selectors.SEARCH_CONTAINER)
    browser.page_done()

    if html is None:
        log.error("   search page failed - keyword skipped for now")
        return 0, [], False

    # ---- step 2: the list of products ----
    listings = parse_search(html)
    log.info("   %s unique listings on page 1", len(listings))

    saved = 0
    failed = []

    # ---- step 3: each product ----
    for listing in listings:
        if limit is not None and saved >= limit:
            break

        asin = listing["asin"]

        # Skip paid placements - they change between requests, so including
        # them would make the dataset impossible to reproduce.
        if listing["sponsored"] and not config.INCLUDE_SPONSORED:
            continue

        # Already have it (perhaps from another keyword, or an earlier run)
        if storage.has(asin):
            continue

        url = product_url(asin)
        page = fetch(browser, url, keyword, "product", asin=asin,
                     anchor=selectors.PRODUCT_ANCHOR)
        browser.page_done()

        if page is None:
            failed.append(asin)
            continue

        # Parsing runs on plain text, but wrap it anyway: one weird page
        # must never end the whole run.
        try:
            row = parse_product(page, asin, keyword, url,
                                rank=listing["rank"],
                                sponsored=listing["sponsored"])
        except Exception as exc:
            log.error("   could not parse %s: %s", asin, exc)
            failed.append(asin)
            continue

        storage.save(row)
        saved += 1

        if storage.count() % 25 == 0:
            log.info("   ... %s products collected in total", storage.count())

    return saved, failed, True


# ===========================================================================
# THE WHOLE RUN
# ===========================================================================
def run(keywords=None, limit=None, retry_only=False):
    """
    Collect everything.

    retry_only=True re-runs just the keywords that failed last time - useful
    after a run where the browser died and took a few keywords with it.
    """
    config.make_folders()
    storage = Storage()

    if retry_only:
        keywords = load_failures()["keywords"]
        if not keywords:
            log.info("Nothing to retry.")
            return
        log.info("Retrying %s failed keywords", len(keywords))
    elif keywords is None:
        keywords = load_keywords()

    started = time.time()
    failed_keywords = []
    failed_asins = []
    total_saved = 0

    with Browser() as browser:
        # FIX #3: load the homepage once so the session has normal cookies.
        # Without this the FIRST keyword often fails - which is exactly what
        # happened to 'wireless earbuds' in your run.
        browser.warm_up()

        for index, keyword in enumerate(keywords, start=1):
            log.info("[%s/%s] %s", index, len(keywords), keyword)

            saved, bad_asins, ok = collect_keyword(browser, storage, keyword, limit)

            total_saved += saved
            failed_asins.extend(bad_asins)
            if not ok:
                failed_keywords.append(keyword)
            else:
                log.info("   done - %s new products", saved)

        # ---- FIX #7: second pass over the keywords that failed ----
        # In your run three keywords were lost simply because the browser was
        # dead at the time. A fresh browser usually collects them fine.
        if failed_keywords and not retry_only:
            log.info("Retrying %s failed keywords with a fresh browser",
                     len(failed_keywords))
            browser.restart()
            browser.warm_up()

            still_failing = []
            for keyword in failed_keywords:
                log.info("[retry] %s", keyword)
                saved, bad_asins, ok = collect_keyword(browser, storage, keyword, limit)
                total_saved += saved
                failed_asins.extend(bad_asins)
                if not ok:
                    still_failing.append(keyword)
                else:
                    log.info("   recovered - %s new products", saved)
            failed_keywords = still_failing

    # Write any log rows that were held in memory because the CSV was locked
    if pending_count():
        log.info("Flushing %s buffered log rows...", pending_count())
    flush_logs()

    save_failures(failed_keywords, failed_asins)

    # ---- summary ----
    minutes = (time.time() - started) / 60
    log.info(
        "\n%s\n  COLLECTION SUMMARY\n%s\n"
        "  Products in dataset : %s\n"
        "  New this run        : %s\n"
        "  Products failed     : %s\n"
        "  Keywords failed     : %s\n"
        "  Blocks encountered  : %s\n"
        "  Interstitials       : %s\n"
        "  Time taken          : %.0f minutes\n%s",
        "=" * 60, "=" * 60,
        storage.count(), total_saved, len(failed_asins),
        ", ".join(failed_keywords) if failed_keywords else "none",
        total_blocks(), total_interstitials(), minutes, "=" * 60,
    )

    if failed_keywords or failed_asins:
        log.info("Retry them any time with:  python main.py collect --retry")
