"""
dump_review.py
==============
Standalone diagnostic. Save this in the project root and run:

    python dump_review.py
    python dump_review.py --asin B0D4QVF1C3

It does not import main.py, so it works regardless of which version of the
other files you have.

WHAT IT DOES
  1. reports which HTML engine BeautifulSoup can actually use
  2. loads one product page in your Chrome
  3. tries a long list of candidate selectors for review title and body
  4. writes the raw HTML of one review block to review_block.html

Send me review_block.html (or paste its contents) and I can give you the
exact selector.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bs4 import BeautifulSoup   # noqa: E402


# ---------------------------------------------------------------------------
# 1. Which engine can BeautifulSoup actually use?
# ---------------------------------------------------------------------------
def pick_engine():
    print("\n" + "=" * 70)
    print("  HTML ENGINE CHECK")
    print("=" * 70)

    working = []
    for name in ("lxml", "html5lib", "html.parser"):
        try:
            BeautifulSoup("<a></a>", name)
            print(f"  [OK  ] {name}")
            working.append(name)
        except Exception as exc:
            print(f"  [FAIL] {name}  ->  {type(exc).__name__}: {exc}")

    if "lxml" not in working:
        print("\n  lxml is NOT usable by BeautifulSoup.")
        print("  Try:  pip install --upgrade beautifulsoup4 soupsieve lxml")
        print("  This matters - html.parser builds a different tree from")
        print("  Amazon's nested review markup and selectors may not match.\n")

    return working[0] if working else "html.parser"


# ---------------------------------------------------------------------------
# 2. Candidate selectors to try
# ---------------------------------------------------------------------------
BLOCK_CANDIDATES = [
    "div[data-hook='review']",
    "li[data-hook='review']",
    "div[id^='R'][data-hook]",
    "div.review",
    "div[class*='review-views'] div[data-hook]",
    "#cm-cr-dp-review-list > div",
    "#cm_cr-review_list div[data-hook='review']",
]

BODY_CANDIDATES = [
    "span[data-hook='review-body'] span.cr-original-review-content",
    "span[data-hook='review-body'] span",
    "span[data-hook='review-body'] div span",
    "span[data-hook='review-body']",
    "div[data-hook='review-collapsed'] span",
    "div[data-hook='review-collapsed']",
    "span.review-text-content span",
    "span.review-text-content",
    "span.cr-original-review-content",
    "div[data-hook='review-body'] span",
    "div[data-hook='review-body']",
    "div.reviewText span",
    "[data-hook*='body']",
    "[class*='review-text']",
    "[class*='reviewText']",
]

TITLE_CANDIDATES = [
    "a[data-hook='review-title'] span.cr-original-review-content",
    "a[data-hook='review-title'] span:not([class])",
    "a[data-hook='review-title'] span",
    "a[data-hook='review-title']",
    "span[data-hook='review-title'] span.cr-original-review-content",
    "span[data-hook='review-title'] span",
    "span[data-hook='review-title']",
    "div[data-hook='review-title'] span",
    "div[data-hook='review-title']",
    "[data-hook*='title']",
    "[class*='review-title']",
]


def try_all(soup, label, candidates):
    print("\n" + "-" * 70)
    print(f"  {label}")
    print("-" * 70)
    hit = False
    for selector in candidates:
        try:
            found = soup.select(selector)
        except Exception as exc:
            print(f"    [ERR ] {selector}  ({type(exc).__name__})")
            continue
        if found:
            hit = True
            sample = found[0].get_text(" ", strip=True)[:65]
            print(f"    [MATCH] {len(found):>3}  {selector}")
            print(f"                  -> {sample!r}")
    if not hit:
        print("    (nothing matched)")
    return hit


# ---------------------------------------------------------------------------
# 3. Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asin", default="B0D4QVF1C3",
                        help="product to inspect")
    parser.add_argument("--keep-open", action="store_true",
                        help="leave the browser open afterwards")
    args = parser.parse_args()

    engine = pick_engine()
    print(f"  Using: {engine}")

    from scraper.browser import Browser
    from scraper.fetch import fetch

    url = f"https://www.amazon.com/dp/{args.asin}"
    print(f"\n  Loading {url}\n")

    browser = Browser()
    try:
        browser.warm_up()
        page = fetch(browser, url, "debug", "product",
                     asin=args.asin, anchor="#productTitle")
    finally:
        if not args.keep_open:
            browser.quit()

    if not page:
        print("\n  Could not load the page. Try a different --asin.\n")
        return 1

    # Save the whole page too - useful if the review section is missing
    Path("full_page.html").write_text(page, encoding="utf-8")
    print(f"  Saved full page ({len(page):,} chars) to full_page.html")

    soup = BeautifulSoup(page, engine)

    # Is the review section even present?
    print("\n" + "=" * 70)
    print("  IS THE REVIEW SECTION ON THIS PAGE?")
    print("=" * 70)
    for marker in ("#reviewsMedley", "#cm-cr-dp-review-list",
                   "#cm_cr-review_list", "[data-hook='review']",
                   "#customer-reviews_feature_div"):
        n = len(soup.select(marker))
        print(f"    {'[FOUND]' if n else '[     ]'} {n:>3}  {marker}")

    try_all(soup, "REVIEW BLOCK", BLOCK_CANDIDATES)
    try_all(soup, "REVIEW BODY", BODY_CANDIDATES)
    try_all(soup, "REVIEW TITLE", TITLE_CANDIDATES)

    # Dump one review block
    block = None
    for selector in BLOCK_CANDIDATES:
        found = soup.select(selector)
        if found:
            block = found[0]
            break

    print("\n" + "=" * 70)
    if block is None:
        print("  NO REVIEW BLOCK FOUND")
        print("=" * 70)
        print("\n  The review section may not be in the HTML at all.")
        print("  Search full_page.html for a word from a review you can see")
        print("  in the browser - if it is absent, the reviews are loaded")
        print("  separately and need a different approach.\n")
    else:
        Path("review_block.html").write_text(block.prettify(), encoding="utf-8")
        print("  RAW HTML OF ONE REVIEW BLOCK")
        print("=" * 70)
        print(block.prettify()[:6000])
        print("\n  Full version saved to review_block.html")
        print("  Send me that file and I can give you the exact selector.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
