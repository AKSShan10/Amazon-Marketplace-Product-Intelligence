"""
scraper/images.py
=================
Downloads every product image to data/images/ so Feature C can cluster them.

WHY DOWNLOAD AT ALL
-------------------
Amazon's image URLs can change or expire. If Feature C reads them live, your
clustering is not reproducible - re-run it next week and some images are gone.
Downloading once makes the analysis repeatable and much faster.

WHAT THIS HANDLES
-----------------
  * resumable      - already-downloaded files are skipped, so re-running is free
  * fallback       - if the original URL fails, tries the resized copy
  * real-image check - verifies the file's magic bytes, so an HTML error page
                       saved as ".jpg" is caught instead of poisoning Feature C
  * manifest       - records what was downloaded, from where, and how big
"""

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config
from scraper.logs import get_logger
from scraper.storage import Storage

log = get_logger()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

# The first few bytes of a file tell you what it really is, regardless of the
# name. This catches an HTML error page that was saved as "B01ABC.jpg".
MAGIC_BYTES = {
    b"\xff\xd8\xff": ".jpg",           # JPEG
    b"\x89PNG\r\n\x1a\n": ".png",      # PNG
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"RIFF": ".webp",                  # WEBP starts RIFF....WEBP
}

MANIFEST = config.DATA_DIR / "images" / "manifest.csv"
MANIFEST_COLUMNS = ["asin", "filename", "url_used", "which_url", "bytes", "status"]


def _detect_format(data):
    """Return the correct extension from the file's first bytes, or None."""
    for signature, extension in MAGIC_BYTES.items():
        if data.startswith(signature):
            if extension == ".webp" and b"WEBP" not in data[:16]:
                continue              # RIFF but not a WEBP - reject
            return extension
    return None


def _existing_files():
    """ASINs we have already downloaded, so we can skip them."""
    folder = config.DATA_DIR / "images"
    if not folder.exists():
        return set()
    return {
        path.stem for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in
        (".jpg", ".jpeg", ".png", ".gif", ".webp")
    }


def _download_one(asin, urls):
    """
    Try each URL in turn until one returns a real image.

    `urls` is a list of (label, url) pairs, best first:
        [("original", "..."), ("sized", "...")]

    Returns a dict for the manifest. Never raises.
    """
    folder = config.DATA_DIR / "images"

    for label, url in urls:
        if not url or not str(url).startswith("http"):
            continue

        try:
            response = requests.get(url, timeout=config.IMAGE_TIMEOUT,
                                    headers=HEADERS, stream=True)

            if response.status_code != 200:
                continue

            data = response.content
            response.close()

            if len(data) < config.MIN_IMAGE_BYTES:
                continue              # too small to be a real product photo

            extension = _detect_format(data)
            if extension is None:
                continue              # not actually an image - try next URL

            path = folder / f"{asin}{extension}"
            path.write_bytes(data)

            return {
                "asin": asin,
                "filename": path.name,
                "url_used": url,
                "which_url": label,
                "bytes": len(data),
                "status": "ok",
            }

        except requests.RequestException:
            continue                  # network problem - try the next URL

    return {
        "asin": asin, "filename": "", "url_used": "",
        "which_url": "", "bytes": 0, "status": "failed",
    }


def download_all(limit=None, force=False):
    """
    Download an image for every collected product.

    limit : only do the first N products (useful for a quick trial)
    force : re-download even if the file already exists
    """
    folder = config.DATA_DIR / "images"
    folder.mkdir(parents=True, exist_ok=True)

    rows = Storage().read_all()
    if not rows:
        log.error("No products found. Run `python main.py collect` first.")
        return

    already = set() if force else _existing_files()

    jobs = []
    no_url = 0
    for row in rows:
        asin = row.get("asin")
        if not asin or asin in already:
            continue

        candidates = [
            ("original", row.get("image_url")),
            ("sized", row.get("image_url_sized")),
        ]
        if not any(url for _, url in candidates):
            no_url += 1
            continue

        jobs.append((asin, candidates))

    if limit:
        jobs = jobs[:limit]

    log.info("Products: %s | already downloaded: %s | no image URL: %s | to fetch: %s",
             len(rows), len(already), no_url, len(jobs))

    if not jobs:
        log.info("Nothing to download.")
        return

    results = []
    # These are CDN URLs built for heavy traffic, so parallel is fine here.
    # (Product PAGES are different - those stay slow and single-threaded.)
    with ThreadPoolExecutor(max_workers=config.IMAGE_WORKERS) as pool:
        futures = {pool.submit(_download_one, asin, urls): asin
                   for asin, urls in jobs}
        for index, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if index % 50 == 0:
                good = sum(1 for r in results if r["status"] == "ok")
                log.info("  %s/%s downloaded (%s ok)", index, len(jobs), good)

    _write_manifest(results)

    ok = sum(1 for r in results if r["status"] == "ok")
    failed = len(results) - ok
    fallbacks = sum(1 for r in results if r["which_url"] == "sized")
    megabytes = sum(r["bytes"] for r in results) / (1024 * 1024)

    log.info(
        "\n%s\n  IMAGE DOWNLOAD SUMMARY\n%s\n"
        "  Downloaded now   : %s\n"
        "  Failed           : %s\n"
        "  Used fallback URL: %s\n"
        "  Total on disk    : %s files\n"
        "  Size added       : %.1f MB\n"
        "  Folder           : %s\n%s",
        "=" * 60, "=" * 60, ok, failed, fallbacks,
        len(_existing_files()), megabytes, folder, "=" * 60,
    )

    if failed:
        log.info("Retry the failures with:  python main.py images")


def _write_manifest(results):
    """
    Append what we downloaded to data/images/manifest.csv.

    Feature C joins this to the product data on `asin`, so it always knows
    which local file belongs to which product.
    """
    existing = []
    if MANIFEST.exists():
        with open(MANIFEST, encoding="utf-8") as handle:
            existing = list(csv.DictReader(handle))

    by_asin = {row["asin"]: row for row in existing}
    for row in results:
        by_asin[row["asin"]] = row

    with open(MANIFEST, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(by_asin.values())


def verify():
    """
    Check the downloaded folder: how many files, how many are real images,
    and which products are still missing one.
    """
    folder = config.DATA_DIR / "images"
    rows = Storage().read_all()
    files = _existing_files()

    broken = []
    for path in folder.glob("*"):
        # Skip anything that is not meant to be an image
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name == "manifest.csv":
            continue
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            continue
        try:
            head = path.open("rb").read(16)
        except OSError:
            broken.append(path.name)
            continue
        if _detect_format(head) is None:
            broken.append(path.name)

    collected = {r["asin"] for r in rows if r.get("asin")}
    matched = files & collected          # images belonging to a real product
    orphans = files - collected          # files with no matching product

    missing = sorted(collected - files)

    print("\n" + "=" * 60)
    print("  IMAGE FOLDER CHECK")
    print("=" * 60)
    print(f"  Products collected : {len(collected)}")
    print(f"  Images on disk     : {len(files)}")
    print(f"  Matched to product : {len(matched)}")
    print(f"  Coverage           : {len(matched) / max(len(collected), 1) * 100:.1f}%")
    print(f"  Orphan files       : {len(orphans)}")
    print(f"  Corrupt files      : {len(broken)}")
    if broken[:5]:
        for name in broken[:5]:
            print(f"      {name}   <- delete and re-run `python main.py images`")
    print(f"  Products missing   : {len(missing)}")
    if missing[:5]:
        print(f"      {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}")
    print("=" * 60 + "\n")
