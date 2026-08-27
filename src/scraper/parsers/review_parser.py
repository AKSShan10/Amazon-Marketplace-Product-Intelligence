"""
Reviews rendered publicly on the product detail page.

No authentication is attempted. Amazon gates the full review pages behind a
sign-in wall and the brief forbids bypassing access controls, so the reviews
visible on the product page are what is collected. reviews_collected is stored
alongside the site-reported review_count so the gap is measurable and reportable.

review['rating'] is the entire label basis for Feature B. Verify on day 1 that
ratings parse, not merely review text - discovering this on day 3 leaves no
recovery time.
"""
from __future__ import annotations

import re
from typing import Optional

from src.common.utils import any_present, clean_text, first_text, parse_int, parse_rating
from src.scraper import selectors

_DATE_PATTERN = re.compile(r"on\s+(.+)$", re.IGNORECASE)


def _review_date(raw: Optional[str]) -> Optional[str]:
    """'Reviewed in the United States on March 3, 2024' -> 'March 3, 2024'"""
    if not raw:
        return None
    match = _DATE_PATTERN.search(raw)
    return clean_text(match.group(1)) if match else clean_text(raw)


def _review_country(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    match = re.search(r"Reviewed in (?:the )?(.+?) on ", raw, re.IGNORECASE)
    return clean_text(match.group(1)) if match else None


def parse_reviews(soup) -> list[dict]:
    blocks = []
    for selector in selectors.REVIEW_BLOCK:
        blocks = soup.select(selector)
        if blocks:
            break

    reviews: list[dict] = []
    for block in blocks:
        raw_date = first_text(block, selectors.REVIEW_DATE)
        title = first_text(block, selectors.REVIEW_TITLE)

        # The title node sometimes carries the star text as a leading fragment
        if title and "out of 5 stars" in title:
            title = clean_text(title.split("out of 5 stars")[-1])

        reviews.append({
            "review_id": block.get("id") or None,
            "reviewer_name": first_text(block, selectors.REVIEW_AUTHOR),
            "rating": parse_rating(first_text(block, selectors.REVIEW_RATING)),
            "title": title,
            "body": first_text(block, selectors.REVIEW_BODY),
            "date": _review_date(raw_date),
            "country": _review_country(raw_date),
            "verified_purchase": any_present(block, selectors.REVIEW_VERIFIED),
            "helpful_votes": parse_int(first_text(block, selectors.REVIEW_HELPFUL)),
        })

    # Drop entries with neither a rating nor a body - they are layout artefacts
    return [r for r in reviews if r["rating"] is not None or r["body"]]
