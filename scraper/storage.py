"""
scraper/storage.py
==================
Saving, resuming, exporting and reporting.

THE KEY IDEA: write every product to disk the moment it is parsed.

If the program crashes at product 900, you lose ONE product, not 900. And when
you start again, it reads back what is already saved and skips those - so
re-running is free. Your previous run collected 850 products; drop that
products.jsonl in place and the scraper will simply top up what is missing.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

import config
from scraper.logs import get_logger

log = get_logger()


# ===========================================================================
# SAVING AND RESUMING
# ===========================================================================
class Storage:
    """
    Products are stored as JSONL: one JSON object per line.

    Why not CSV directly? Because CSV cannot hold nested data (the reviews
    list) without escaping, and because appending one line at a time to JSONL
    is trivially safe. CSV is produced later, at export time.
    """

    def __init__(self):
        config.make_folders()
        self.path = config.PRODUCTS_JSONL
        self.seen = self._load_existing_asins()

    def _load_existing_asins(self):
        """
        Read back which products we already have.

        This is what makes the scraper resumable. A damaged final line (from a
        crash mid-write) is skipped rather than crashing the load.
        """
        found = set()
        side = self.path.with_name(self.path.stem + "_overflow.jsonl")

        for path in (self.path, side):
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        found.add(json.loads(line)["asin"])
                    except (ValueError, KeyError):
                        continue          # skip the broken line, keep going

        if found:
            log.info("Resuming - %s products already collected", len(found))
        return found

    def has(self, asin):
        """Have we already collected this product?"""
        return asin in self.seen

    def save(self, row):
        """
        Append one product. Called immediately after parsing.

        Like the logger, this must survive a locked file (Windows locks
        products.jsonl if you open it in an editor). We retry briefly, then
        fall back to a side file so a product is never silently lost.
        """
        line = json.dumps(row, ensure_ascii=False) + "\n"

        for attempt in range(3):
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
                self.seen.add(row["asin"])
                return
            except (PermissionError, OSError):
                time.sleep(1)

        # Still locked - write to a side file rather than lose the product.
        side = self.path.with_name(self.path.stem + "_overflow.jsonl")
        try:
            with open(side, "a", encoding="utf-8") as f:
                f.write(line)
            self.seen.add(row["asin"])
            log.warning("%s is locked - saved %s to %s instead",
                        self.path.name, row.get("asin"), side.name)
        except OSError as exc:
            log.error("Could not save product %s: %s", row.get("asin"), exc)

    def count(self):
        return len(self.seen)

    def read_all(self):
        """
        Load every saved product.

        Reads the main file AND any *_overflow.jsonl written while the main
        file was locked, so nothing is missed.
        """
        rows = []
        side = self.path.with_name(self.path.stem + "_overflow.jsonl")

        for path in (self.path, side):
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            continue
        return rows


# ===========================================================================
# FAILED ITEMS  (so a bad run can be retried instead of repeated)
# ===========================================================================
def save_failures(keywords, asins):
    """Remember what failed, so `python main.py collect --retry` can fix it."""
    config.FAILED_JSON.write_text(
        json.dumps({"keywords": sorted(set(keywords)),
                    "asins": sorted(set(asins))}, indent=2),
        encoding="utf-8",
    )


def load_failures():
    """Read back what failed last time."""
    if not config.FAILED_JSON.exists():
        return {"keywords": [], "asins": []}
    try:
        return json.loads(config.FAILED_JSON.read_text(encoding="utf-8"))
    except ValueError:
        return {"keywords": [], "asins": []}


# ===========================================================================
# LINK CHECKING
# ===========================================================================
# A real browser User-Agent.
#
# THE BUG THIS PREVENTS: with an unusual User-Agent, Amazon answers /dp/ URLs
# with 503 Service Unavailable. Every product link would be reported broken
# even though all of them work in a browser - a false alarm that would look
# terrible in the M1 report.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Status codes that mean "we were throttled", not "the link is dead".
# Amazon returns these when checked too quickly. Reporting them as broken
# links would be wrong, so they get their own label.
THROTTLE_CODES = {429, 503}


def check_link(url, delay=0.0, expect=None):
    """
    Ask a URL whether it is alive, without downloading the whole page.

    HEAD only fetches headers, so it is cheap. Some servers reject HEAD, in
    which case we retry with a streamed GET (which also does not download the
    body).

    `expect` guards against a subtle trap: a URL can return 200 OK and still
    not be the thing you wanted. An image link that answers with an HTML
    error page would otherwise be recorded as "ok" - and then a reviewer
    clicks it and sees a web page instead of a picture.

        expect="image"  -> the reply must be Content-Type: image/*
        expect="video"  -> the reply must be video/* or a byte stream

    `delay` spaces requests out - see _check_column for why that matters.
    """
    if not url or not str(url).startswith("http"):
        return "none"

    if delay:
        time.sleep(delay)

    try:
        response = requests.head(url, timeout=config.LINK_CHECK_TIMEOUT,
                                 allow_redirects=True, headers=HEADERS)

        # 403/405 usually mean "HEAD not allowed here" rather than "gone"
        if response.status_code in (403, 405):
            response = requests.get(url, timeout=config.LINK_CHECK_TIMEOUT,
                                    stream=True, headers=HEADERS)
            response.close()

        if response.status_code in THROTTLE_CODES:
            return "throttled"          # NOT a broken link
        if response.status_code != 200:
            return f"http_{response.status_code}"

        # --- 200 OK, but is it the RIGHT KIND of thing? ---
        if expect:
            content_type = response.headers.get("Content-Type", "").lower()
            if expect == "image" and not content_type.startswith("image/"):
                return f"not_an_image ({content_type.split(';')[0] or 'unknown'})"
            if expect == "video" and not (
                content_type.startswith("video/")
                or content_type.startswith("application/octet-stream")
                # HLS playlists. These ARE valid video responses - reporting
                # them as "not_a_video" was a false negative.
                or "mpegurl" in content_type          # vnd.apple.mpegurl
                or "application/x-mpegurl" in content_type
            ):
                return f"not_a_video ({content_type.split(';')[0] or 'unknown'})"

        return "ok"

    except requests.RequestException:
        return "unreachable"


def _check_column(frame, column, gentle=False, expect=None):
    """
    Check every URL in a column.

    `gentle=True` is used for Amazon product pages. The project constraints
    say to keep request rates modest - firing 850 parallel requests at Amazon
    would break that rule and get us throttled, producing fake "broken link"
    results. So product URLs are checked slowly, on a single thread.

    Image and video URLs live on a CDN built for high traffic, so those are
    checked in parallel.
    """
    if column not in frame.columns:
        return ["none"] * len(frame)

    urls = frame[column].tolist()

    if gentle:
        results = []
        for index, url in enumerate(urls, start=1):
            results.append(check_link(url, delay=config.LINK_CHECK_DELAY,
                                      expect=expect))
            if index % 100 == 0:
                log.info("    checked %s/%s product links", index, len(urls))
        return results

    with ThreadPoolExecutor(max_workers=config.LINK_CHECK_WORKERS) as pool:
        return list(pool.map(lambda u: check_link(u, expect=expect), urls))


# ===========================================================================
# EXPORT
# ===========================================================================
def export(check_links=True):
    """
    Build the deliverable: one row per product, reviews as JSON in one field.

    CSV is the primary format because that is what the brief asks for. But an
    Excel cell caps out around 32,767 characters and a reviews array can beat
    that, so JSONL is kept as the lossless master alongside it.
    """
    config.make_folders()

    rows = Storage().read_all()
    if not rows:
        raise RuntimeError(f"Nothing to export - {config.PRODUCTS_JSONL} is empty")

    frame = pd.DataFrame(rows)

    # Safety net: the store already prevents duplicates
    before = len(frame)
    frame = frame.drop_duplicates(subset="asin", keep="first")
    if len(frame) != before:
        log.warning("Removed %s duplicate products", before - len(frame))

    if check_links:
        # Product pages: SLOW and single-threaded. The project constraints
        # require modest request rates, and hammering Amazon would return
        # fake 503s that look like broken links.
        log.info("Checking %s product links (slow, single thread)...", len(frame))
        frame["product_url_status"] = _check_column(frame, "product_url", gentle=True)

        # Images and video sit on a CDN built for traffic - parallel is fine.
        # expect="image" means a 200 is not enough - the reply must actually
        # BE an image, so a stored link always opens as a picture.
        log.info("Checking image and video links...")
        frame["image_url_status"] = _check_column(frame, "image_url", expect="image")
        frame["video_url_status"] = _check_column(frame, "video_url", expect="video")

        # If the original image is unavailable, fall back to Amazon's resized
        # copy rather than shipping a broken link.
        if "image_url_sized" in frame.columns:
            broken = ~frame["image_url_status"].isin(["ok", "throttled", "none"])
            swapped = int((broken & frame["image_url_sized"].notna()).sum())
            frame.loc[broken, "image_url"] = frame.loc[broken, "image_url_sized"]
            if swapped:
                log.info("Swapped %s images to the resized fallback", swapped)
                frame.loc[broken, "image_url_status"] = _check_column(
                    frame.loc[broken], "image_url", expect="image")

        # Never ship a dead video link. has_video and video_page_url still
        # carry the information, so nothing is lost by nulling it.
        #
        # "throttled" is deliberately NOT treated as dead - it means we asked
        # too fast, not that the link is broken.
        dead = ~frame["video_url_status"].isin(["ok", "throttled", "none"])
        removed = int((dead & frame["video_url"].notna()).sum())
        frame.loc[dead, "video_url"] = None
        if removed:
            log.info("Removed %s expired video links "
                     "(has_video and video_page_url still valid)", removed)

        for column in ("product_url_status", "image_url_status", "video_url_status"):
            counts = frame[column].value_counts().to_dict()
            log.info("  %-20s %s", column, counts)

    # Master copy, keeps nested structures intact
    frame.to_json(config.PRODUCTS_JSONL.with_name("products_final.jsonl"),
                  orient="records", lines=True, force_ascii=False)

    # Deliverable CSV: nested fields become JSON strings
    flat = frame.copy()
    for column in ("reviews", "bullets", "search_keywords"):
        if column in flat.columns:
            flat[column] = flat[column].apply(
                lambda v: json.dumps(v, ensure_ascii=False)
                if isinstance(v, (list, dict)) else v
            )
    flat.to_csv(config.PRODUCTS_CSV, index=False, encoding="utf-8")

    log.info("Exported %s products to %s", len(flat), config.PRODUCTS_CSV)
    return frame


# ===========================================================================
# M1 COMPLETENESS REPORT
# ===========================================================================
def report():
    """
    Print the evidence M1 needs: coverage, request outcomes, and null rates.

    THE MOST USEFUL PART is the null rate BY KEYWORD.
      - a field null everywhere      -> genuinely often missing, document it
      - a field null for a few only  -> a layout you have not handled yet:
                                        go add a selector in selectors.py
    """
    rows = Storage().read_all()
    if not rows:
        print("No data collected yet.")
        return

    frame = pd.DataFrame(rows)

    print("\n" + "=" * 64)
    print("  M1 COMPLETENESS REPORT")
    print("=" * 64)
    print(f"  Products collected    : {len(frame)}")
    print(f"  Keywords covered      : {frame['search_keyword'].nunique()}")

    if "reviews_collected" in frame.columns:
        print(f"  Products with reviews : {(frame['reviews_collected'] > 0).sum()}")
        print(f"  Reviews collected     : {int(frame['reviews_collected'].sum())}")
    if "has_video" in frame.columns:
        print(f"  Products with video   : {int(frame['has_video'].sum())}")

    # --- request log summary -------------------------------------------
    if config.REQUEST_LOG.exists():
        log_frame = pd.read_csv(config.REQUEST_LOG)
        print(f"\n  Request outcomes:")
        for outcome, number in log_frame["outcome"].value_counts().items():
            print(f"    {outcome:<10} {number}")

        failures = log_frame[log_frame["outcome"] != "success"]
        if len(failures) and "reason" in failures.columns:
            print(f"\n  Why things failed:")
            for reason, number in failures["reason"].value_counts().head(8).items():
                print(f"    {number:>4}  {reason}")

    # --- null rates -----------------------------------------------------
    print(f"\n  Missing data by field:")
    print(f"    {'field':<22}{'missing':>9}  {'limit':>7}  status")
    for field, limit in config.NULL_RATE_LIMITS.items():
        if field not in frame.columns:
            continue
        rate = frame[field].isna().mean()
        status = "ok" if rate <= limit else "CHECK SELECTORS"
        print(f"    {field:<22}{rate:>8.1%}  {limit:>6.0%}   {status}")

    # --- null rates by keyword -----------------------------------------
    fields = [f for f in config.NULL_RATE_LIMITS if f in frame.columns]
    by_keyword = frame.groupby("search_keyword")[fields].apply(
        lambda group: group.isna().mean()
    ).round(2)
    out_path = config.DATA_DIR / "null_rates_by_keyword.csv"
    by_keyword.to_csv(out_path)
    print(f"\n  Per-keyword detail written to {out_path.name}")
    print("=" * 64 + "\n")
