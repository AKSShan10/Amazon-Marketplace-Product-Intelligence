"""Small, dependency-light helpers shared across the pipeline."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def clean_text(value: Optional[str]) -> Optional[str]:
    """Collapse whitespace; return None for empty results."""
    if value is None:
        return None
    text = _WS.sub(" ", str(value)).strip()
    return text or None


def first_text(soup, selectors: Iterable[str], joiner: str = " ") -> Optional[str]:
    """First non-empty text match across an ordered selector chain."""
    for selector in selectors:
        try:
            element = soup.select_one(selector)
        except Exception:
            continue
        if element is None:
            continue
        text = clean_text(element.get_text(joiner, strip=True))
        if text:
            return text
    return None


def all_texts(soup, selectors: Iterable[str]) -> list[str]:
    """All texts from the first selector in the chain that matches anything."""
    for selector in selectors:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        texts = [clean_text(e.get_text(" ", strip=True)) for e in elements]
        texts = [t for t in texts if t]
        if texts:
            return texts
    return []


def first_element(soup, selectors: Iterable[str]):
    for selector in selectors:
        try:
            element = soup.select_one(selector)
        except Exception:
            continue
        if element is not None:
            return element
    return None


def any_present(soup, selectors: Iterable[str]) -> bool:
    return first_element(soup, selectors) is not None


# ---------------------------------------------------------------------------
# Numeric coercion - never raises, returns None on failure
# ---------------------------------------------------------------------------
_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY"}


def parse_price(raw: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """'$1,299.99' -> (1299.99, 'USD')"""
    if not raw:
        return None, None
    currency = next((c for s, c in _CURRENCY_SYMBOLS.items() if s in raw), None)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None, currency
    try:
        return float(match.group(0).replace(",", "")), currency
    except ValueError:
        return None, currency


def parse_rating(raw: Optional[str]) -> Optional[float]:
    """'4.5 out of 5 stars' -> 4.5"""
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


def parse_int(raw: Optional[str]) -> Optional[int]:
    """'1,234 ratings' -> 1234"""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def largest_from_dynamic_image(raw: Optional[str]) -> Optional[str]:
    """
    data-a-dynamic-image holds {"url": [width, height], ...}.
    Return the URL with the greatest pixel area.
    """
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(mapping, dict) or not mapping:
        return None
    best, best_area = None, -1
    for url, dims in mapping.items():
        try:
            area = int(dims[0]) * int(dims[1])
        except (TypeError, ValueError, IndexError):
            area = 0
        if area > best_area:
            best, best_area = url, area
    return best


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:120]
