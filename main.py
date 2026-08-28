"""
main.py
=======
The single entry point for the whole project.

    python main.py test              check the selectors still work (do FIRST)
    python main.py collect           collect everything
    python main.py collect --retry   re-run only what failed last time
    python main.py export            build products.csv
    python main.py report            print the M1 completeness report

Useful while developing:
    python main.py collect --first 2 --limit 3      tiny trial run
    python main.py collect --keyword "gaming mouse" one keyword only
"""

import argparse
import sys


# ===========================================================================
# COMMANDS
# ===========================================================================
def cmd_test(args):
    """
    Check that the selectors still match Amazon's live HTML.

    RUN THIS BEFORE A LONG COLLECTION. It takes about a minute and tells you
    whether anything has changed. In particular it checks REVIEW STAR RATINGS,
    which are the entire label source for Feature B.
    """
    from bs4 import BeautifulSoup

    from scraper import selectors
    from scraper.browser import Browser
    from scraper.collect import product_url, search_url
    from scraper.fetch import fetch
    from scraper.parser import parse_product, parse_search

    keyword = args.keyword or "wireless earbuds"
    print(f"\nTesting selectors with keyword: '{keyword}'\n")

    passed = []

    def check(label, condition, detail=""):
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {label}" + (f"  ->  {detail}" if detail else ""))
        passed.append(bool(condition))

    with Browser() as browser:
        browser.warm_up()

        html = fetch(browser, search_url(keyword), keyword, "search",
                     anchor=selectors.SEARCH_CONTAINER)
        if not check("search page loaded", html is not None):
            return 1

        listings = parse_search(html)
        check("listings found", len(listings) > 0, f"{len(listings)} listings")
        if not listings:
            return 1

        # Prefer a non-sponsored product for the test
        target = next((l for l in listings if not l["sponsored"]), listings[0])
        url = product_url(target["asin"])
        print(f"\n  Testing product page {target['asin']}\n")

        page = fetch(browser, url, keyword, "product",
                     asin=target["asin"], anchor=selectors.PRODUCT_ANCHOR)
        if not check("product page loaded", page is not None):
            return 1

        row = parse_product(page, target["asin"], keyword, url,
                            rank=target["rank"], sponsored=target["sponsored"])

        check("title", row["title"], (row["title"] or "")[:55])
        check("brand", row["brand"], row["brand"])
        check("price", row["price"] is not None, row["price_raw"])
        check("rating", row["rating"] is not None, row["rating"])
        check("review count", row["review_count"] is not None, row["review_count"])
        check("bullets", len(row["bullets"]) > 0, f"{len(row['bullets'])} bullets")
        check("image", row["image_url"], (row["image_url"] or "")[:55])

        # video is optional - many products genuinely have none
        print(f"  [INFO] video present: {row['has_video']}")

        reviews = row["reviews"]
        check("reviews", len(reviews) > 0, f"{len(reviews)} reviews")
        rated = [r for r in reviews if r["rating"] is not None]
        check("REVIEW STAR RATINGS (needed by Feature B)",
              len(rated) > 0, f"{len(rated)} of {len(reviews)} have a rating")

    total, good = len(passed), sum(passed)
    print(f"\n  {good}/{total} checks passed")
    if good < total:
        print("  Fix the failing fields in scraper/selectors.py before collecting.\n")
    else:
        print("  All good - you can start the collection.\n")
    return 0 if good == total else 1


def cmd_collect(args):
    """Run the collection."""
    from scraper.collect import load_keywords, run

    keywords = None
    if args.keyword:
        keywords = args.keyword           # one or more --keyword flags
    elif args.first:
        keywords = load_keywords()[: args.first]

    run(keywords=keywords, limit=args.limit, retry_only=args.retry)
    return 0


def cmd_export(args):
    """Validate links and write products.csv."""
    from scraper.storage import export
    try:
        export(check_links=not args.skip_links)
    except RuntimeError as exc:
        print(f"\n  {exc}")
        print("  Run `python main.py collect` first.\n")
        return 1
    return 0


def cmd_report(args):
    """Print the M1 completeness report."""
    from scraper.storage import report
    report()
    return 0


def cmd_spotcheck(args):
    """Build the spot-check sheet, or summarise filled-in results."""
    from scraper.spotcheck import build, summary
    if args.summary:
        summary()
    else:
        build(percent=args.percent, seed=args.seed)
    return 0


def cmd_images(args):
    """Download every product image to data/images/."""
    from scraper.images import download_all
    download_all(limit=args.limit, force=args.force)
    return 0


def cmd_images_check(args):
    """Check the downloaded image folder."""
    from scraper.images import verify
    verify()
    return 0


def cmd_media_check(args):
    """
    Print image and video URLs from your collected data so you can click them.

    For each sampled product it shows:
        image_url        the ORIGINAL (size token stripped)  <- click this
        image_url_sized  Amazon's resized copy               <- compare
        video            the video columns

    It also fetches each URL and reports the real Content-Type, so you can see
    whether the link genuinely returns a picture rather than a web page.
    """
    from scraper.storage import Storage, check_link

    rows = Storage().read_all()
    if not rows:
        print("\nNo data yet. Run `python main.py collect` first.\n")
        return 1

    # Prefer products that actually have a video, so both can be checked
    with_video = [r for r in rows if r.get("has_video") and r.get("video_url")]
    sample = (with_video[: args.count] +
              [r for r in rows if r.get("image_url")][: args.count])
    sample = sample[: args.count]

    print(f"\n{'=' * 70}")
    print(f"  MEDIA CHECK - {len(sample)} of {len(rows)} products")
    print(f"{'=' * 70}")

    for row in sample:
        print(f"\n  {row.get('asin')}  |  {(row.get('title') or '')[:52]}")
        print(f"  product page : {row.get('product_url')}")

        original = row.get("image_url")
        sized = row.get("image_url_sized")

        if original:
            status = "" if args.no_fetch else f"   [{check_link(original, expect='image')}]"
            print(f"\n  ORIGINAL  (click this){status}")
            print(f"    {original}")

        if sized and sized != original:
            status = "" if args.no_fetch else f"   [{check_link(sized, expect='image')}]"
            print(f"\n  RESIZED COPY (for comparison){status}")
            print(f"    {sized}")

        if row.get("has_video"):
            print(f"\n  VIDEO")
            if row.get("video_url"):
                status = "" if args.no_fetch else f"   [{check_link(row['video_url'], expect='video')}]"
                print(f"    direct : {row['video_url']}{status}")
            else:
                print(f"    direct : none captured (or expired and removed)")
            print(f"    page   : {row.get('video_page_url')}   <- always works")

        print(f"  {'-' * 66}")

    print("\n  What to look for:")
    print("    * ORIGINAL should open as a bare picture, larger than RESIZED")
    print("    * status [ok] means the server really returned an image")
    print("    * [not_an_image (...)] means a 200 that was NOT a picture")
    print("    * video 'page' link should open the product page with the video\n")
    return 0
    """Print the M1 completeness report."""
    from scraper.storage import report
    report()
    return 0


# ===========================================================================
# COMMAND LINE PARSING
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Amazon Product & Review Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- test ---
    p = subparsers.add_parser("test", help="check selectors against live pages")
    p.add_argument("--keyword", help="keyword to test with")
    p.set_defaults(func=cmd_test)

    # --- collect ---
    p = subparsers.add_parser("collect", help="collect products and reviews")
    p.add_argument("--keyword", action="append",
                   help="collect one keyword only (repeatable)")
    p.add_argument("--first", type=int, help="only the first N keywords")
    p.add_argument("--limit", type=int, help="max products per keyword")
    p.add_argument("--retry", action="store_true",
                   help="re-run only what failed last time")
    p.set_defaults(func=cmd_collect)

    # --- export ---
    p = subparsers.add_parser("export", help="build the deliverable CSV")
    p.add_argument("--skip-links", action="store_true",
                   help="skip link checking (faster, not for delivery)")
    p.set_defaults(func=cmd_export)

    # --- report ---
    p = subparsers.add_parser("report", help="print the M1 completeness report")
    p.set_defaults(func=cmd_report)

    # --- spotcheck ---
    p = subparsers.add_parser("spotcheck",
                              help="build the 5-10%% URL spot-check sheet (criterion 7.1)")
    p.add_argument("--percent", type=int, default=8, help="sample size, 5-10")
    p.add_argument("--seed", type=int, default=42, help="random seed (reproducible)")
    p.add_argument("--summary", action="store_true",
                   help="read back your results and print the pass rate")
    p.set_defaults(func=cmd_spotcheck)

    # --- images ---
    p = subparsers.add_parser("images",
                              help="download product images to data/images/")
    p.add_argument("--limit", type=int, help="only the first N products")
    p.add_argument("--force", action="store_true",
                   help="re-download even if the file exists")
    p.set_defaults(func=cmd_images)

    # --- images-check ---
    p = subparsers.add_parser("images-check",
                              help="verify the downloaded image folder")
    p.set_defaults(func=cmd_images_check)

    # --- media-check ---
    p = subparsers.add_parser("media-check",
                              help="print image/video URLs so you can click them")
    p.add_argument("--count", type=int, default=3, help="how many products to show")
    p.add_argument("--no-fetch", action="store_true",
                   help="just print URLs, do not check them")
    p.set_defaults(func=cmd_media_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
