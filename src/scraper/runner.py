"""
Collection orchestrator.

Flow per keyword:
    search page -> unique listings -> product page -> parse -> append JSONL

Resilience is delegated: fetch.py handles blocks and retries, store.py handles
resumability, driver.py recycles the browser. This module only sequences work
and reports progress.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

from config import settings
from src.scraper import selectors
from src.scraper.driver import DriverManager
from src.scraper.fetch import block_tracker, fetch_page
from src.scraper.keywords import load_keywords
from src.scraper.logger import get_logger
from src.scraper.parsers.product_parser import parse_product
from src.scraper.parsers.review_parser import parse_reviews
from src.scraper.parsers.search_parser import parse_search
from src.scraper.store import ProductStore

log = get_logger()


@dataclass
class RunStats:
    keywords_total: int = 0
    keywords_ok: int = 0
    listings_seen: int = 0
    sponsored_skipped: int = 0
    already_collected: int = 0
    products_collected: int = 0
    products_failed: int = 0
    reviews_collected: int = 0
    failed_keywords: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"\n{'='*62}\n"
            f"  COLLECTION SUMMARY\n"
            f"{'='*62}\n"
            f"  Keywords processed    : {self.keywords_ok}/{self.keywords_total}\n"
            f"  Listings seen         : {self.listings_seen}\n"
            f"  Sponsored skipped     : {self.sponsored_skipped}\n"
            f"  Already collected     : {self.already_collected}\n"
            f"  Products collected    : {self.products_collected}\n"
            f"  Products failed       : {self.products_failed}\n"
            f"  Reviews collected     : {self.reviews_collected}\n"
            f"  Blocks encountered    : {block_tracker().total}\n"
            f"  Failed keywords       : {', '.join(self.failed_keywords) or 'none'}\n"
            f"{'='*62}\n"
        )


def search_url(keyword: str) -> str:
    return settings.DOMAIN + settings.SEARCH_PATH.format(query=quote_plus(keyword))


def product_url(asin: str) -> str:
    return settings.DOMAIN + settings.PRODUCT_PATH.format(asin=asin)


def collect(
    keywords: Optional[list[str]] = None,
    *,
    limit_per_keyword: Optional[int] = None,
) -> RunStats:
    settings.ensure_dirs()

    keywords = keywords or load_keywords()
    limit = limit_per_keyword or settings.MAX_PRODUCTS_PER_KW

    store = ProductStore()
    stats = RunStats(keywords_total=len(keywords))

    with DriverManager() as manager:
        for index, keyword in enumerate(keywords, start=1):
            log.info("[%s/%s] keyword: %s", index, len(keywords), keyword)

            result = fetch_page(
                manager.driver, search_url(keyword), keyword, "search",
                anchor_selector="div.s-main-slot", scroll=True,
            )
            manager.page_served()

            if not result.ok:
                log.error("  search failed (%s) - skipping keyword", result.outcome)
                stats.failed_keywords.append(keyword)
                continue

            listings = parse_search(result.html)
            log.info("  %s unique listings found", len(listings))
            stats.listings_seen += len(listings)

            collected_here = 0
            for listing in listings:
                if limit is not None and collected_here >= limit:
                    break

                asin = listing["asin"]

                if listing["sponsored"] and not settings.INCLUDE_SPONSORED:
                    stats.sponsored_skipped += 1
                    continue

                if store.has(asin):
                    store.add_keyword_alias(asin, keyword)
                    stats.already_collected += 1
                    continue

                url = product_url(asin)
                page = fetch_page(
                    manager.driver, url, keyword, "product", asin=asin,
                    anchor_selector="#productTitle", scroll=True,
                )
                manager.page_served()

                if not page.ok:
                    stats.products_failed += 1
                    continue

                try:
                    row = parse_product(
                        page.html,
                        asin=asin,
                        keyword=keyword,
                        product_url=url,
                        search_rank=listing["search_rank"],
                        sponsored=listing["sponsored"],
                        raw_file=page.raw_file,
                    )
                    from bs4 import BeautifulSoup
                    reviews = parse_reviews(BeautifulSoup(page.html, "lxml"))
                except Exception as exc:
                    log.error("  parse failed for %s: %s", asin, exc)
                    stats.products_failed += 1
                    continue

                row["reviews"] = reviews
                row["reviews_collected"] = len(reviews)

                store.add(row)
                collected_here += 1
                stats.products_collected += 1
                stats.reviews_collected += len(reviews)

                if stats.products_collected % 25 == 0:
                    log.info("  ... %s products collected so far", stats.products_collected)

            stats.keywords_ok += 1
            log.info("  keyword done: %s new products", collected_here)

    log.info(stats.summary())
    return stats
