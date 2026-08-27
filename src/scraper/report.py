"""
M1 completeness report.

Produces the evidence required by acceptance criteria 7.1 and 7.3: coverage,
outcome distribution from the request log, per-field null rates, and the
null-rate-by-keyword diagnostic that distinguishes a genuinely absent field
from a broken selector.
"""
from __future__ import annotations

import pandas as pd

from config import settings
from src.scraper.logger import get_logger

log = get_logger()

DIAGNOSTIC_FIELDS = [
    "title", "brand", "price", "rating", "review_count",
    "image_url_hires", "description", "category_breadcrumb",
]


def _load_products() -> pd.DataFrame:
    if not settings.FINAL_JSONL.exists():
        raise FileNotFoundError(
            f"{settings.FINAL_JSONL} not found - run the export step first."
        )
    return pd.read_json(settings.FINAL_JSONL, lines=True)


def _load_log() -> pd.DataFrame:
    if not settings.REQUEST_LOG.exists():
        return pd.DataFrame(columns=["outcome", "keyword", "request_type"])
    return pd.read_csv(settings.REQUEST_LOG)


def null_rates(frame: pd.DataFrame) -> pd.DataFrame:
    fields = [f for f in DIAGNOSTIC_FIELDS if f in frame.columns]
    rows = []
    for field in fields:
        rate = float(frame[field].isna().mean())
        threshold = settings.NULL_RATE_THRESHOLDS.get(field)
        rows.append({
            "field": field,
            "null_rate": round(rate, 4),
            "threshold": threshold,
            "status": (
                "ok" if threshold is None or rate <= threshold else "INVESTIGATE"
            ),
        })
    return pd.DataFrame(rows).sort_values("null_rate", ascending=False)


def null_rate_by_keyword(frame: pd.DataFrame) -> pd.DataFrame:
    """
    The diagnostic that matters.

    Uniform nulls across keywords  -> field genuinely often absent; document it.
    Nulls concentrated in a few    -> unhandled layout variant; open one of the
                                      saved pages and extend the selector chain.
    """
    fields = [f for f in DIAGNOSTIC_FIELDS if f in frame.columns]
    table = frame.groupby("search_keyword")[fields].apply(lambda g: g.isna().mean())
    return table.round(3)


def generate() -> dict:
    products = _load_products()
    request_log = _load_log()

    outcomes = request_log["outcome"].value_counts().to_dict() if len(request_log) else {}
    keywords_covered = products["search_keyword"].nunique()

    reviews_col = products.get("reviews_collected")
    with_reviews = int((reviews_col > 0).sum()) if reviews_col is not None else 0

    nulls = null_rates(products)
    by_keyword = null_rate_by_keyword(products)

    link_stats = {
        column: products[column].value_counts().to_dict()
        for column in ("product_url_status", "image_url_status", "video_url_status")
        if column in products.columns
    }

    settings.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    nulls.to_csv(settings.REPORT_DIR / "m1_null_rates.csv", index=False)
    by_keyword.to_csv(settings.REPORT_DIR / "m1_null_rates_by_keyword.csv")

    summary = {
        "products_total": int(len(products)),
        "keywords_covered": int(keywords_covered),
        "products_with_reviews": with_reviews,
        "reviews_total": int(reviews_col.sum()) if reviews_col is not None else 0,
        "products_with_video": int(products["has_video"].sum())
        if "has_video" in products.columns else 0,
        "request_outcomes": outcomes,
        "link_status": link_stats,
    }

    _print(summary, nulls)
    return summary


def _print(summary: dict, nulls: pd.DataFrame) -> None:
    print("\n" + "=" * 62)
    print("  M1 COMPLETENESS REPORT")
    print("=" * 62)
    print(f"  Products collected     : {summary['products_total']}")
    print(f"  Keywords covered       : {summary['keywords_covered']}")
    print(f"  Products with reviews  : {summary['products_with_reviews']}")
    print(f"  Reviews collected      : {summary['reviews_total']}")
    print(f"  Products with video    : {summary['products_with_video']}")
    print(f"\n  Request outcomes       : {summary['request_outcomes']}")
    print(f"\n  Link status            :")
    for column, counts in summary["link_status"].items():
        print(f"    {column:<22} {counts}")
    print("\n  Per-field null rates:")
    print(nulls.to_string(index=False))
    print("\n  Detail written to reports/m1_null_rates_by_keyword.csv")
    print("=" * 62 + "\n")
