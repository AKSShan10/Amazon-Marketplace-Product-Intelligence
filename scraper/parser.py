"""
scraper/parser.py
=================
Turns raw HTML into clean Python dictionaries.

Selenium's only job is to LOAD pages. All reading happens here with
BeautifulSoup. That split matters: pulling fields out with Selenium one at a
time causes constant StaleElementReferenceException errors and is much slower.

Everything in this file is pure text processing - no browser, no network.
That makes it easy to test and impossible to break by a bad connection.
"""

import json
import re
from html import unescape
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from scraper import selectors

# ===========================================================================
# TINY HELPERS
# ===========================================================================
_SPACES = re.compile(r"\s+")


def _clean(text):
    """Collapse runs of whitespace. Return None instead of an empty string."""
    if text is None:
        return None
    text = _SPACES.sub(" ", str(text)).strip()
    return text or None


def _text(soup, selector_list, joiner=" "):
    """
    Try each selector in the list, return the first one that has text.

    This is THE most important function in the file - it is what makes the
    scraper survive Amazon's multiple page layouts.
    """
    for selector in selector_list:
        try:
            element = soup.select_one(selector)
        except Exception:
            continue                     # a malformed selector: just skip it
        if element is None:
            continue
        found = _clean(element.get_text(joiner, strip=True))
        if found:
            return found
    return None


def _text_list(soup, selector_list):
    """Return ALL texts from the first selector that matches anything."""
    for selector in selector_list:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        texts = [_clean(e.get_text(" ", strip=True)) for e in elements]
        texts = [t for t in texts if t]
        if texts:
            return texts
    return []


def _element(soup, selector_list):
    """Return the first matching element (not its text)."""
    for selector in selector_list:
        try:
            element = soup.select_one(selector)
        except Exception:
            continue
        if element is not None:
            return element
    return None


def _exists(soup, selector_list):
    """True if any selector in the list matches something."""
    return _element(soup, selector_list) is not None


# ===========================================================================
# NUMBER PARSING - these never raise, they return None on failure
# ===========================================================================
CURRENCY_SIGNS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR"}


def _price(raw):
    """'$1,299.99' -> (1299.99, 'USD')"""
    if not raw:
        return None, None
    currency = None
    for sign, code in CURRENCY_SIGNS.items():
        if sign in raw:
            currency = code
            break
    match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None, currency
    try:
        return float(match.group(0).replace(",", "")), currency
    except ValueError:
        return None, currency


def _rating(raw):
    """'4.3 out of 5 stars' -> 4.3"""
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None      # reject nonsense


def _count(raw):
    """'12,847 ratings' -> 12847"""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


# ===========================================================================
# 1. SEARCH RESULTS PAGE
# ===========================================================================
def parse_search(html):
    """
    Read a search results page.

    Returns a list of dicts: {asin, rank, sponsored, title}

    Duplicates are removed here, so the caller never sees the same product
    twice on one page.

    NOTE ON VARYING COUNTS
    ----------------------
    You noticed some keywords give ~24 listings and others ~60. That is not a
    bug. Amazon uses a list layout for electronics (fewer, larger cards) and a
    grid layout for fashion and home goods (many small cards). Both are page 1.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()
    rank = 0

    for card in soup.select(selectors.SEARCH_CARD):
        asin = (card.get("data-asin") or "").strip()

        if not asin or asin in seen:      # blank or already recorded
            continue

        seen.add(asin)
        rank += 1
        results.append({
            "asin": asin,
            "rank": rank,
            "sponsored": _exists(card, selectors.SPONSORED_LABEL),
            "title": _text(card, selectors.SEARCH_TITLE),
        })

    return results


# ===========================================================================
# 2. IMAGES
# ===========================================================================
# Amazon puts resize instructions INSIDE the image filename:
#
#   .../images/I/61Xk9Z8vLqL._AC_SL1500_.jpg     <- 1500px processed copy
#   .../images/I/61Xk9Z8vLqL._AC_SX466_.jpg      <- 466px processed copy
#   .../images/I/61Xk9Z8vLqL.jpg                 <- THE ORIGINAL
#
# Everything between the product id and the extension is a transformation
# token. Strip it and you get the original file at full resolution.
#
# This matters twice over:
#   1. clicking the link shows the real product image, not a shrunk copy
#   2. Feature C clusters better on consistent, full-resolution images
_SIZE_TOKEN = re.compile(r"\._[^./]*_\.(jpg|jpeg|png|gif|webp)$", re.IGNORECASE)

# Only these are real image files. Anything else is not an image link.
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _strip_size_token(url):
    """'..._AC_SL1500_.jpg' -> '....jpg'  (the original upload)"""
    if not url:
        return url
    return _SIZE_TOKEN.sub(r".\1", url)


def _usable_image_url(value):
    """
    Reject anything that is not a real, directly-viewable image file.

    THE BUG THIS PREVENTS
    ---------------------
    Amazon lazy-loads images. Until you scroll, the <img src> is a tiny grey
    placeholder encoded directly in the page:

        src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP..."

    If scrolling failed for any reason, that base64 blob would be stored as
    the product's image_url. It would bloat products.csv, and Feature C would
    try to cluster a grey 1x1 pixel for every affected product.

    We also require a real image extension, so a stored link always opens as
    a picture and never as a web page.
    """
    if not value or not isinstance(value, str):
        return None

    value = value.strip()

    if value.startswith("data:"):      # base64 placeholder - not a real file
        return None
    if not value.startswith("http"):   # relative path or junk
        return None

    # Must end in an image extension (ignoring any ?query on the end)
    path = value.split("?")[0].lower()
    if not path.endswith(_IMAGE_EXTENSIONS):
        return None

    return value


def _parse_image(soup, keep_size_token=False):
    """
    Find the best available product image.

    The visible 'src' is often a shrunken version. The full-size URL hides in
    'data-old-hires', or inside 'data-a-dynamic-image' which is a JSON map of
    {url: [width, height]} - we pick the largest by pixel area.

    Every candidate goes through _usable_image_url() so placeholders never
    reach the dataset.
    """
    element = _element(soup, selectors.MAIN_IMAGE)
    if element is None:
        return None

    for attribute in selectors.IMAGE_ATTRIBUTES:
        value = element.get(attribute)
        if not value:
            continue

        if attribute == "data-a-dynamic-image":
            try:
                sizes = json.loads(value)
            except (ValueError, TypeError):
                continue
            best_url, best_area = None, -1
            for url, dimensions in sizes.items():
                if _usable_image_url(url) is None:
                    continue
                try:
                    area = int(dimensions[0]) * int(dimensions[1])
                except (TypeError, ValueError, IndexError):
                    area = 0
                if area > best_area:
                    best_url, best_area = url, area
            if best_url:
                return best_url if keep_size_token else _strip_size_token(best_url)
        else:
            usable = _usable_image_url(value)
            if usable:
                return usable if keep_size_token else _strip_size_token(usable)

    return None


# ===========================================================================
# 3. VIDEO
# ===========================================================================
# Finding the video URL is the trickiest parse in this project, because the
# real link is buried in HTML-ESCAPED JSON inside a <script> tag.
#
# THE BUG THIS FIXES
# ------------------
# The old pattern excluded literal quotes, but Amazon writes them escaped as
# &quot; - which contains no quote character. So the match ran straight through
# the JSON and glued three URLs into one 400-character string ending in .mp4:
#
#   ...closedCaptions/abc.vtt&quot;,&quot;groupType&quot;...videopreview.jobtemplate.mp4
#
# That URL 404s, obviously. The fix is to UNESCAPE the page first, so &quot;
# becomes a real quote and the pattern stops where it should.
_MEDIA_PATTERN = re.compile(r"https://[^\"'\s\\<>]+?\.(?:mp4|m3u8)")

# Amazon's CDN hosts many things. Only these are the product's own video.
_VIDEO_CDN_HINTS = ("media-amazon", "ssl-images-amazon", "images-amazon")

# Assets that exist in the page but are NOT playable video files:
#   jobtemplate       an encoder template name, not a real file
#   closedCaptions    subtitle tracks (.vtt)
#   videopreview      low-res preview stub, frequently 404s
_NOT_PLAYABLE = ("jobtemplate", "closedcaptions", "videopreview", "thumbnail")

# A real URL is nowhere near this long. Anything longer means the pattern
# swallowed surrounding markup again.
_MAX_URL_LENGTH = 300


def _usable_video_url(url):
    """
    Return the URL only if it looks like a genuine video asset.

    Both .mp4 and .m3u8 pass here; the CHOICE between them happens in
    _rank_video_urls below.
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    if len(url) > _MAX_URL_LENGTH:
        return None
    if not url.startswith("http"):
        return None
    if any(bad in url.lower() for bad in _NOT_PLAYABLE):
        return None
    if not any(hint in url for hint in _VIDEO_CDN_HINTS):
        return None
    if not url.lower().split("?")[0].endswith((".mp4", ".m3u8")):
        return None

    return url


def _rank_video_urls(candidates):
    """
    Choose the best video URL from everything found on the page.

    WHY .mp4 BEATS .m3u8
    --------------------
    An .m3u8 is an HLS *playlist* - a text file listing stream segments. Click
    one in a browser and you download a text file, you do not see a video.
    Acceptance criterion 7.1 says "video links should work", so a playlist is
    not an acceptable deliverable.

    An .mp4 plays directly. So we sort: plain .mp4 first, preview .mp4 next,
    and .m3u8 only if config.ACCEPT_HLS_VIDEO is switched on.

    If nothing usable survives, video_url stays None - which is honest, and
    video_page_url still gives the reviewer a working link.
    """
    from config import ACCEPT_HLS_VIDEO

    def score(url):
        lower = url.lower()
        if lower.split("?")[0].endswith(".m3u8"):
            return 3                       # playlist - last resort
        if "preview" in lower:
            return 2                       # a preview clip, but a real file
        return 1                           # a proper .mp4

    usable = [u for u in (_usable_video_url(c) for c in candidates) if u]
    if not usable:
        return None

    if not ACCEPT_HLS_VIDEO:
        usable = [u for u in usable if not u.lower().split("?")[0].endswith(".m3u8")]
        if not usable:
            return None

    return sorted(usable, key=score)[0]


def _parse_video(html_text, soup, product_url):
    """
    Video needs THREE columns, not one. Here is why.

    Amazon's direct video links are signed, expire, and are often encoder
    artefacts or HLS playlists that were never clickable in the first place. A
    URL that works today can return 404 next week - exactly when your reviewer
    clicks it.

    So we store:

        has_video       whether the page really has a video block
        video_url       a direct, PLAYABLE link if one exists, else None
        video_page_url  the product page - ALWAYS works, video plays there

    A null video_url alongside has_video=True is honest and defensible; a 404
    or a downloaded playlist file is a failed check.
    """
    # has_video comes from the PAGE STRUCTURE, never from a lucky regex match.
    has_video = _exists(soup, selectors.VIDEO_CONTAINER)

    candidates = []

    # Source 1: a direct attribute on the video container
    element = _element(soup, selectors.VIDEO_CONTAINER)
    if element is not None:
        for attribute in selectors.VIDEO_ATTRIBUTES:
            value = element.get(attribute)
            if value:
                candidates.append(value)

    # Source 2: the JSON the video player reads.
    # unescape() first - see the note on _MEDIA_PATTERN above.
    if has_video:
        candidates.extend(_MEDIA_PATTERN.findall(unescape(html_text)))

    return {
        "has_video": bool(has_video),
        "video_url": _rank_video_urls(candidates),
        "video_page_url": product_url,
    }


# ===========================================================================
# 4. REVIEWS
# ===========================================================================
DATE_PATTERN = re.compile(r"on\s+(.+)$", re.IGNORECASE)
COUNTRY_PATTERN = re.compile(r"Reviewed in (?:the )?(.+?) on ", re.IGNORECASE)


def parse_reviews(soup):
    """
    Read the reviews shown publicly on the product page.

    NO LOGIN IS USED. Amazon puts the full review pages behind a sign-in wall,
    and the project brief forbids bypassing access controls, so we take what
    is public. `reviews_collected` is stored next to `review_count` so the gap
    is visible and reportable.

    review['rating'] IS THE LABEL SOURCE FOR FEATURE B. If it comes back None,
    Feature B has nothing to train on. Verify with `python main.py test`.
    """
    blocks = []
    for selector in selectors.REVIEW_BLOCK:
        blocks = soup.select(selector)
        if blocks:
            break

    reviews = []
    for block in blocks:
        raw_date = _text(block, selectors.REVIEW_DATE)
        title = _text(block, selectors.REVIEW_TITLE)

        # Some layouts glue the star text onto the front of the title.
        if title and "out of 5 stars" in title:
            title = _clean(title.split("out of 5 stars")[-1])

        date_match = DATE_PATTERN.search(raw_date) if raw_date else None
        country_match = COUNTRY_PATTERN.search(raw_date) if raw_date else None

        reviews.append({
            "reviewer_name": _text(block, selectors.REVIEW_AUTHOR),
            "rating": _rating(_text(block, selectors.REVIEW_RATING)),
            "title": title,
            "body": _text(block, selectors.REVIEW_BODY),
            "date": _clean(date_match.group(1)) if date_match else raw_date,
            "country": _clean(country_match.group(1)) if country_match else None,
            "verified_purchase": _exists(block, selectors.REVIEW_VERIFIED),
            "helpful_votes": _count(_text(block, selectors.REVIEW_HELPFUL)),
        })

    # Drop entries that have neither a rating nor text - layout artefacts.
    return [r for r in reviews if r["rating"] is not None or r["body"]]


# ===========================================================================
# 5. PRODUCT PAGE  (puts everything together)
# ===========================================================================
def _tidy_brand(value):
    """'Visit the Anker Store' -> 'Anker'"""
    if not value:
        return None
    text = value.strip()
    for prefix in ("Visit the ", "Brand: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.endswith(" Store"):
        text = text[:-6]
    return text.strip() or None


def parse_product(html, asin, keyword, product_url, rank=None, sponsored=False):
    """
    Read one product page into a single flat dictionary.

    LEAKAGE WARNING for Feature D: price, list_price and discount_pct are
    collected for completeness, but they must NOT be used as input features
    when predicting price tier. Price may only be used to create the labels.
    """
    soup = BeautifulSoup(html, "lxml")

    price_text = _text(soup, selectors.PRICE)
    price, currency = _price(price_text)

    list_price, _ = _price(_text(soup, selectors.LIST_PRICE))

    # Work out a discount only if both numbers make sense
    discount = None
    if price is not None and list_price and list_price > price:
        discount = round((list_price - price) / list_price * 100, 2)

    availability = _text(soup, selectors.AVAILABILITY)

    # Explain WHY a price is missing, instead of leaving a bare null
    if price is not None:
        price_status = "available"
    elif availability and ("unavailable" in availability.lower()
                           or "out of stock" in availability.lower()):
        price_status = "out_of_stock"
    elif availability:
        price_status = "variant_or_unpriced"
    else:
        price_status = "unknown"

    row = {
        # --- identity ---
        "asin": asin,
        "product_url": product_url,
        "search_keyword": keyword,
        "search_keywords": [keyword],       # grows if seen under more keywords
        "search_rank": rank,
        "sponsored": sponsored,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),

        # --- text (used by Features A and D) ---
        "title": _text(soup, selectors.TITLE),
        "brand": _tidy_brand(_text(soup, selectors.BRAND)),
        "bullets": _text_list(soup, selectors.BULLETS),
        "description": _text(soup, selectors.DESCRIPTION),
        "category_breadcrumb": _text(soup, selectors.BREADCRUMB, joiner=" > "),

        # --- price (LABELS ONLY for Feature D - never an input feature) ---
        "price": price,
        "price_raw": price_text,
        "currency": currency,
        "list_price": list_price,
        "discount_pct": discount,
        "availability": availability,
        "price_status": price_status,

        # --- ratings (inputs for Feature D) ---
        "rating": _rating(_text(soup, selectors.RATING)),
        "review_count": _count(_text(soup, selectors.REVIEW_COUNT)),

        # --- image (Feature C) ---
        # image_url        the ORIGINAL file - clicking it shows the picture
        # image_url_sized  Amazon's resized copy, kept as a fallback in the
        #                  rare case the original is unavailable
        "image_url": _parse_image(soup),
        "image_url_sized": _parse_image(soup, keep_size_token=True),
    }

    # --- video (three columns, see _parse_video) ---
    row.update(_parse_video(html, soup, product_url))

    # --- reviews (Feature B) ---
    reviews = parse_reviews(soup)
    row["reviews"] = reviews
    row["reviews_collected"] = len(reviews)

    return row
