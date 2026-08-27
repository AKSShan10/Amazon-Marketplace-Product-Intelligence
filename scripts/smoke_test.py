#!/usr/bin/env python
"""
Day-1 verification. Run this BEFORE the full collection.

Checks, on a single keyword:
  1. the search page parses and yields ASINs
  2. a product page parses and yields core fields
  3. REVIEW STAR RATINGS parse  <- Feature B has no labels without this
  4. a high-resolution image URL is found
  5. video detection runs

    python scripts/smoke_test.py
    python scripts/smoke_test.py --keyword "gaming mouse"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup                                    # noqa: E402
from src.scraper.driver import DriverManager                     # noqa: E402
from src.scraper.fetch import fetch_page                         # noqa: E402
from src.scraper.parsers.product_parser import parse_product     # noqa: E402
from src.scraper.parsers.review_parser import parse_reviews      # noqa: E402
from src.scraper.parsers.search_parser import parse_search       # noqa: E402
from src.scraper.runner import product_url, search_url           # noqa: E402
from config import settings                                      # noqa: E402


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{(' - ' + str(detail)) if detail else ''}")
    return bool(condition)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="wireless earbuds")
    args = parser.parse_args()

    settings.ensure_dirs()
    results = []

    print(f"\nSmoke test - keyword: {args.keyword!r}\n")

    with DriverManager() as manager:
        search = fetch_page(manager.driver, search_url(args.keyword),
                            args.keyword, "search",
                            anchor_selector="div.s-main-slot")
        if not check("search page fetched", search.ok, search.outcome):
            return 1

        listings = parse_search(search.html)
        results.append(check("listings parsed", len(listings) > 0, f"{len(listings)} found"))
        if not listings:
            return 1

        organic = [l for l in listings if not l["sponsored"]] or listings
        target = organic[0]
        url = product_url(target["asin"])
        print(f"\n  Testing product {target['asin']}\n")

        page = fetch_page(manager.driver, url, args.keyword, "product",
                          asin=target["asin"], anchor_selector="#productTitle")
        if not check("product page fetched", page.ok, page.outcome):
            return 1

        row = parse_product(page.html, asin=target["asin"], keyword=args.keyword,
                            product_url=url, search_rank=target["search_rank"],
                            sponsored=target["sponsored"], raw_file=page.raw_file)
        reviews = parse_reviews(BeautifulSoup(page.html, "lxml"))

        results.append(check("title", row["title"], (row["title"] or "")[:60]))
        results.append(check("brand", row["brand"], row["brand"]))
        results.append(check("price", row["price"] is not None, row["price_raw"]))
        results.append(check("rating", row["rating"] is not None, row["rating"]))
        results.append(check("review_count", row["review_count"] is not None, row["review_count"]))
        results.append(check("bullets", len(row["bullets"]) > 0, f"{len(row['bullets'])} items"))
        results.append(check("hi-res image", row["image_url_hires"],
                             (row["image_url_hires"] or "")[:60]))
        check("video detected", row["has_video"], row["video_url"] or "no direct URL")

        results.append(check("reviews parsed", len(reviews) > 0, f"{len(reviews)} found"))
        rated = [r for r in reviews if r["rating"] is not None]
        results.append(check("REVIEW STAR RATINGS  <-- critical for Feature B",
                             len(rated) > 0, f"{len(rated)}/{len(reviews)} rated"))

    passed = sum(results)
    print(f"\n  {passed}/{len(results)} critical checks passed\n")
    if passed < len(results):
        print("  Fix the failing selectors in src/scraper/selectors.py before the full run.\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
