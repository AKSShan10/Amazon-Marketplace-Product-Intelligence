"""
scraper/refresh.py
==================
Re-fetch ONLY the reviews for products you have already collected.

WHY THIS EXISTS
---------------
The review selectors were stale (Amazon renamed data-hook="review-body" to
data-hook="reviewRichContentContainer"), so every review was saved with a null
body. Ratings survived, but Feature B has nothing to classify.

A full re-collect would work, but it would also throw away the validated
image URLs, video links and export results. This command revisits each product
page and updates ONLY the `reviews` field, leaving everything else untouched.

HOW IT WORKS
------------
    1. reads data/products.jsonl
    2. picks products whose reviews have no body text  (or --all)
    3. loads each product page and re-parses just the reviews
    4. appends the result to data/reviews_refresh.jsonl  (resume state)
    5. --apply merges that back into products.jsonl, after a backup

It is resumable in the same way as collect: interrupt it, run it again, and it
picks up where it stopped.
"""

import json
from datetime import datetime, timezone

import config
from scraper import selectors
from scraper.browser import Browser
from scraper.collect import product_url
from scraper.fetch import fetch, total_interstitials
from scraper.logs import flush as flush_logs
from scraper.logs import get_logger
from scraper.parser import parse_reviews, soup_of
from scraper.storage import Storage

log = get_logger()

REFRESH_FILE = config.DATA_DIR / "reviews_refresh.jsonl"


# ---------------------------------------------------------------------------
# Working out what needs refreshing
# ---------------------------------------------------------------------------
def _has_usable_reviews(product):
    """True if at least one review carries body text."""
    return any(r.get("body") for r in (product.get("reviews") or []))


def _already_refreshed():
    """ASINs we have already re-fetched, so a re-run skips them."""
    if not REFRESH_FILE.exists():
        return set()
    done = set()
    with open(REFRESH_FILE, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["asin"])
            except (ValueError, KeyError):
                continue
    return done


# ---------------------------------------------------------------------------
# The refresh run
# ---------------------------------------------------------------------------
def run(limit=None, refresh_all=False):
    config.make_folders()

    products = Storage().read_all()
    if not products:
        log.error("No products found. Nothing to refresh.")
        return

    done = _already_refreshed()

    targets = []
    skipped_ok = 0
    for product in products:
        asin = product.get("asin")
        if not asin or asin in done:
            continue
        if not refresh_all and _has_usable_reviews(product):
            skipped_ok += 1          # this one is already fine
            continue
        targets.append(product)

    if limit:
        targets = targets[:limit]

    log.info(
        "Products: %s | already refreshed: %s | already had bodies: %s | to fetch: %s",
        len(products), len(done), skipped_ok, len(targets),
    )

    if not targets:
        log.info("Nothing to do. Run with --apply to merge results in.")
        return

    estimate = len(targets) * (config.MIN_DELAY + config.MAX_DELAY) / 2 / 60
    log.info("Rough estimate: %.0f minutes (interstitials will add to this)", estimate)

    recovered = 0
    empty = 0
    failed = 0

    with Browser() as browser:
        browser.warm_up()

        for index, product in enumerate(targets, start=1):
            asin = product["asin"]
            url = product.get("product_url") or product_url(asin)

            page = fetch(browser, url, product.get("search_keyword", ""),
                         "product", asin=asin, anchor=selectors.PRODUCT_ANCHOR)
            browser.page_done()

            if page is None:
                failed += 1
                continue

            try:
                reviews = parse_reviews(soup_of(page))
            except Exception as exc:
                log.error("   parse failed for %s: %s", asin, exc)
                failed += 1
                continue

            with_body = sum(1 for r in reviews if r.get("body"))
            if with_body:
                recovered += with_body
            else:
                empty += 1

            with open(REFRESH_FILE, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "asin": asin,
                    "reviews": reviews,
                    "reviews_collected": len(reviews),
                    "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }, ensure_ascii=False) + "\n")

            if index % 25 == 0:
                log.info("   %s/%s products | %s review bodies recovered",
                         index, len(targets), recovered)

    flush_logs()

    log.info(
        "\n%s\n  REVIEW REFRESH SUMMARY\n%s\n"
        "  Products processed   : %s\n"
        "  Review bodies found  : %s\n"
        "  Products with none   : %s\n"
        "  Products failed      : %s\n"
        "  Interstitials        : %s\n%s",
        "=" * 60, "=" * 60, len(targets), recovered, empty, failed,
        total_interstitials(), "=" * 60,
    )
    log.info("Now merge the results in:  python main.py refresh-reviews --apply")


# ---------------------------------------------------------------------------
# Merging back
# ---------------------------------------------------------------------------
def apply():
    """
    Merge reviews_refresh.jsonl into products.jsonl.

    The original file is backed up first. Only the `reviews` and
    `reviews_collected` fields are replaced - images, video, price and every
    other field are left exactly as they were.
    """
    if not REFRESH_FILE.exists():
        log.error("%s not found. Run the refresh first.", REFRESH_FILE.name)
        return

    updates = {}
    with open(REFRESH_FILE, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                updates[record["asin"]] = record
            except (ValueError, KeyError):
                continue

    if not updates:
        log.error("No usable records in %s", REFRESH_FILE.name)
        return

    products = Storage().read_all()
    if not products:
        log.error("No products to update.")
        return

    # Back up before touching anything
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = config.PRODUCTS_JSONL.with_name(f"products_before_refresh_{stamp}.jsonl")
    backup.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in products) + "\n",
        encoding="utf-8",
    )
    log.info("Backed up %s products to %s", len(products), backup.name)

    changed = 0
    bodies_before = 0
    bodies_after = 0

    for product in products:
        bodies_before += sum(1 for r in (product.get("reviews") or []) if r.get("body"))
        record = updates.get(product.get("asin"))
        if record:
            product["reviews"] = record["reviews"]
            product["reviews_collected"] = record["reviews_collected"]
            changed += 1
        bodies_after += sum(1 for r in (product.get("reviews") or []) if r.get("body"))

    with open(config.PRODUCTS_JSONL, "w", encoding="utf-8") as handle:
        for product in products:
            handle.write(json.dumps(product, ensure_ascii=False) + "\n")

    log.info(
        "\n%s\n  MERGE COMPLETE\n%s\n"
        "  Products updated     : %s\n"
        "  Review bodies before : %s\n"
        "  Review bodies after  : %s\n"
        "  Backup               : %s\n%s",
        "=" * 60, "=" * 60, changed, bodies_before, bodies_after,
        backup.name, "=" * 60,
    )
    log.info("Re-run 'python main.py export' to rebuild products.csv")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def status():
    """How many products currently have usable review text."""
    products = Storage().read_all()
    if not products:
        print("\nNo products collected yet.\n")
        return

    total_reviews = 0
    with_body = 0
    products_ok = 0

    for product in products:
        reviews = product.get("reviews") or []
        total_reviews += len(reviews)
        bodies = sum(1 for r in reviews if r.get("body"))
        with_body += bodies
        if bodies:
            products_ok += 1

    done = _already_refreshed()

    print("\n" + "=" * 60)
    print("  REVIEW TEXT STATUS")
    print("=" * 60)
    print(f"  Products                  : {len(products)}")
    print(f"  Products with review text : {products_ok}")
    print(f"  Products still empty      : {len(products) - products_ok}")
    print(f"  Reviews total             : {total_reviews}")
    print(f"  Reviews with body text    : {with_body}")
    if total_reviews:
        print(f"  Coverage                  : {with_body / total_reviews * 100:.1f}%")
    print(f"  Refreshed, not yet merged : {len(done)}")
    print("=" * 60 + "\n")
