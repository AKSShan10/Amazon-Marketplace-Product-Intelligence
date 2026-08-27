"""
Export the collected rows to the deliverable format.

One row per product; all reviews for that product held in a single `reviews`
field as a JSON array. CSV is the primary format: Excel silently truncates
cells beyond ~32,767 characters, which would destroy the reviews field.
JSONL is retained alongside as the lossless master.

Link validation runs before export so no dead URL ships. A video URL that
fails is nulled - has_video and video_page_url still carry the information.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pandas as pd
import requests

from config import settings
from src.scraper.logger import get_logger
from src.scraper.store import ProductStore

log = get_logger()

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; project-link-validator/1.0)"}


# ---------------------------------------------------------------------------
# Link validation
# ---------------------------------------------------------------------------
def check_url(url: Optional[str]) -> str:
    if not url or not str(url).startswith("http"):
        return "none"
    try:
        response = requests.head(
            url, timeout=settings.LINK_CHECK_TIMEOUT,
            allow_redirects=True, headers=_HEADERS,
        )
        if response.status_code == 405:  # some CDNs reject HEAD
            response = requests.get(
                url, timeout=settings.LINK_CHECK_TIMEOUT,
                stream=True, headers=_HEADERS,
            )
        return "ok" if response.status_code == 200 else f"http_{response.status_code}"
    except requests.RequestException:
        return "unreachable"


def _check_column(frame: pd.DataFrame, column: str) -> list[str]:
    urls = frame[column].tolist()
    with ThreadPoolExecutor(max_workers=settings.LINK_CHECK_WORKERS) as pool:
        return list(pool.map(check_url, urls))


def validate_links(frame: pd.DataFrame) -> pd.DataFrame:
    log.info("Validating links (%s rows)", len(frame))

    frame["product_url_status"] = _check_column(frame, "product_url")
    frame["image_url_status"] = _check_column(frame, "image_url_hires")
    frame["video_url_status"] = _check_column(frame, "video_url")

    dead = frame["video_url_status"].isin(["none", "unreachable"]) | \
        frame["video_url_status"].str.startswith("http_")
    nulled = int((dead & frame["video_url"].notna()).sum())
    frame.loc[dead, "video_url"] = None
    if nulled:
        log.info("Nulled %s expired/broken video URLs", nulled)

    for column in ("product_url_status", "image_url_status", "video_url_status"):
        rate = (frame[column] == "ok").mean()
        log.info("  %-22s ok: %.1f%%", column, rate * 100)

    return frame


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def build_frame(merge_keywords: bool = True) -> pd.DataFrame:
    store = ProductStore()
    rows = list(store.rows())
    if not rows:
        raise RuntimeError(f"No collected rows found at {store.path}")

    frame = pd.DataFrame(rows)

    if merge_keywords:
        aliases = store.aliases()
        frame["search_keywords"] = frame.apply(
            lambda r: sorted(set(r.get("search_keywords") or []) | aliases.get(r["asin"], set())),
            axis=1,
        )

    # Deduplicate defensively; the store already prevents this
    before = len(frame)
    frame = frame.drop_duplicates(subset="asin", keep="first")
    if len(frame) != before:
        log.warning("Dropped %s duplicate ASINs at export", before - len(frame))

    return frame


def export(skip_link_check: bool = False) -> pd.DataFrame:
    settings.ensure_dirs()
    frame = build_frame()

    if not skip_link_check:
        frame = validate_links(frame)

    # Lossless master
    frame.to_json(settings.FINAL_JSONL, orient="records", lines=True, force_ascii=False)

    # Deliverable CSV - nested fields serialised to JSON strings
    flat = frame.copy()
    for column in ("reviews", "bullets", "search_keywords"):
        if column in flat.columns:
            flat[column] = flat[column].apply(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )

    flat.to_csv(settings.FINAL_CSV, index=False, encoding="utf-8")

    log.info("Exported %s products", len(flat))
    log.info("  CSV   : %s", settings.FINAL_CSV)
    log.info("  JSONL : %s", settings.FINAL_JSONL)
    return frame
