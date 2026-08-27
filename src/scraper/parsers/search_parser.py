"""Search results page -> ordered list of unique listings."""
from __future__ import annotations

from bs4 import BeautifulSoup

from src.common.utils import any_present, first_text
from src.scraper import selectors

_INVALID_ASIN_PREFIXES = ("", " ")


def parse_search(html: str) -> list[dict]:
    """
    Returns one dict per unique listing, in page order:
        {asin, search_rank, sponsored, search_title}

    Deduplication happens here so the caller never sees the same ASIN twice
    within a single results page.
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen: set[str] = set()
    rank = 0

    for card in soup.select(selectors.SEARCH_RESULT_CARD):
        asin = (card.get("data-asin") or "").strip()
        if not asin or asin in _INVALID_ASIN_PREFIXES or asin in seen:
            continue
        seen.add(asin)
        rank += 1
        results.append({
            "asin": asin,
            "search_rank": rank,
            "sponsored": any_present(card, selectors.SPONSORED_MARKER),
            "search_title": first_text(card, selectors.SEARCH_TITLE),
        })

    return results
