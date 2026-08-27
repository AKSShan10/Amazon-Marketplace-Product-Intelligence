"""
Product detail page -> one schema-complete row.

Every text field goes through an ordered fallback chain defined in
selectors.py. When a null rate is high, the fix belongs there, not here.

Leakage note: list_price and discount_pct are collected for completeness but
must be quarantined from Feature D's input features, along with price itself.
"""
from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from src.common.utils import (
    all_texts,
    clean_text,
    first_text,
    parse_int,
    parse_price,
    parse_rating,
    utc_now,
)
from src.scraper import selectors
from src.scraper.parsers.media_parser import parse_images, parse_video


def _availability_status(text: Optional[str], price: Optional[float]) -> str:
    if price is not None:
        return "available"
    if not text:
        return "unknown"
    lowered = text.lower()
    if "unavailable" in lowered or "out of stock" in lowered:
        return "out_of_stock"
    return "variant_or_unpriced"


def parse_product(
    html: str,
    *,
    asin: str,
    keyword: str,
    product_url: str,
    search_rank: Optional[int] = None,
    sponsored: bool = False,
    raw_file: str = "",
) -> dict:
    soup = BeautifulSoup(html, "lxml")

    price_raw = first_text(soup, selectors.PRICE)
    price, currency = parse_price(price_raw)

    list_price_raw = first_text(soup, selectors.LIST_PRICE)
    list_price, _ = parse_price(list_price_raw)

    bullets = all_texts(soup, selectors.BULLETS)
    availability_raw = first_text(soup, selectors.AVAILABILITY)

    discount_pct = None
    if price is not None and list_price and list_price > price:
        discount_pct = round((list_price - price) / list_price * 100, 2)

    row: dict = {
        # identity and provenance
        "asin": asin,
        "product_url": product_url,
        "search_keyword": keyword,
        "search_keywords": [keyword],
        "search_rank": search_rank,
        "sponsored": sponsored,
        "collected_at": utc_now(),
        "raw_html_path": raw_file,

        # product attributes
        "title": first_text(soup, selectors.TITLE),
        "brand": clean_text(_strip_brand_prefix(first_text(soup, selectors.BRAND))),
        "bullets": bullets,
        "description": first_text(soup, selectors.DESCRIPTION),
        "category_breadcrumb": first_text(soup, selectors.BREADCRUMB, joiner=" > "),

        # pricing  (labels only for Feature D - never input features)
        "price": price,
        "price_raw": price_raw,
        "currency": currency,
        "list_price": list_price,
        "discount_pct": discount_pct,
        "availability_raw": availability_raw,

        # ratings
        "rating": parse_rating(first_text(soup, selectors.RATING)),
        "review_count": parse_int(first_text(soup, selectors.REVIEW_COUNT)),
    }

    row["price_status"] = _availability_status(availability_raw, price)
    row.update(parse_images(soup))
    row.update(parse_video(html, soup, product_url))

    return row


def _strip_brand_prefix(value: Optional[str]) -> Optional[str]:
    """'Visit the Anker Store' / 'Brand: Anker' -> 'Anker'"""
    if not value:
        return None
    text = value.strip()
    for prefix in ("Visit the ", "Brand: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    for suffix in (" Store",):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip() or None
