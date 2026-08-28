"""
config.py
=========
Every setting for the whole project lives in this one file.

RULE: no other file may hardcode a path, a delay, or a threshold.
If you want to change how the scraper behaves, change it HERE.
"""

from pathlib import Path

# ============================================================================
# PATHS
# ============================================================================
# Path(__file__) is this file. .resolve() makes it absolute. .parent is the
# folder holding it. So ROOT = the amazon-intelligence/ folder.
ROOT = Path(__file__).resolve().parent

DATA_DIR   = ROOT / "data"
LOGS_DIR   = ROOT / "logs"
MODELS_DIR = ROOT / "models"

# --- inputs -----------------------------------------------------------------
KEYWORDS_CSV = DATA_DIR / "keywords.csv"   # your 28 search keywords
KEYWORD_COLUMN = "Search Keywords"          # the column holding the keyword

# --- outputs ----------------------------------------------------------------
PRODUCTS_JSONL = DATA_DIR / "products.jsonl"  # master file + resume state
PRODUCTS_CSV   = DATA_DIR / "products.csv"    # the M1 deliverable
FAILED_JSON    = DATA_DIR / "failed.json"     # keywords/ASINs to retry later

# --- logs -------------------------------------------------------------------
REQUEST_LOG = LOGS_DIR / "request_log.csv"  # audit trail, one row per attempt
RUN_LOG     = LOGS_DIR / "run.txt"          # human-readable run log

# ============================================================================
# TARGET SITE  (US marketplace)
# ============================================================================
DOMAIN       = "https://www.amazon.com"
SEARCH_URL   = DOMAIN + "/s?k={query}"
PRODUCT_URL  = DOMAIN + "/dp/{asin}"

# ============================================================================
# BROWSER
# ============================================================================
HEADLESS = False          # False = visible window. Amazon blocks headless more.
WINDOW_WIDTH  = 1920
WINDOW_HEIGHT = 1080

PAGE_LOAD_TIMEOUT = 30    # max seconds for driver.get() before it gives up
SCRIPT_TIMEOUT    = 20    # FIX #2: caps execute_script()
ELEMENT_TIMEOUT   = 12    # max seconds waiting for an element to appear

# FIX #9: the outermost safety net.
# The two timeouts above only work while ChromeDriver still ANSWERS us. If
# Chrome freezes, ChromeDriver goes quiet and Selenium waits forever - that is
# the 63-minute gap in your log (09:03:09 -> 10:06:42). This caps EVERY
# command sent to ChromeDriver, so a frozen browser raises instead of hanging.
COMMAND_TIMEOUT = 60

# --- when to rebuild the browser -------------------------------------------
# Your pages were 2.0-3.0 MB each (see the page_chars column in your log).
# 150 of those is a lot of memory for one Chrome process, which is very likely
# what killed it. 100 is safer and costs only a few extra seconds per run.
RESTART_AFTER_PAGES  = 100  # scheduled recycle (memory grows over time)
RESTART_AFTER_ERRORS = 3    # FIX #1: rebuild after N errors IN A ROW.
                            # Your run died because the driver went dead and
                            # nothing rebuilt it - every later call failed in
                            # ~16s (just retry delays, no real page load).

# ============================================================================
# POLITENESS AND RETRIES
# ============================================================================
MIN_DELAY = 4.0           # random pause between requests, lower bound
MAX_DELAY = 6.5           # upper bound
MAX_RETRIES = 3           # attempts per URL before giving up
BACKOFF_SECONDS = 30      # wait after a block = BACKOFF_SECONDS * attempt

SCROLL_STEPS = 3          # scroll passes to trigger lazy-loaded images/video
SCROLL_PAUSE = 1.0        # seconds to wait after each scroll

# Amazon's "Continue shopping" page is a throttling signal. Each batch of 5
# adds a second to the delay between requests, up to this ceiling.
MAX_EXTRA_DELAY = 5.0

# After this many blocks in a row, stop and wait a long time
BLOCK_LIMIT_BEFORE_COOLOFF = 5
COOLOFF_SECONDS = 600     # 10 minutes

# ============================================================================
# INTERSTITIAL HANDLING          <-- READ THIS, IT IS A PROJECT DECISION
# ============================================================================
# Amazon sometimes shows a plain page with a "Continue shopping" button.
# It is a soft speed bump, not a puzzle: no CAPTCHA is solved, no account is
# used, nothing is bypassed. Clicking it is closer to dismissing a cookie
# banner than to defeating an access control.
#
# Set to False if you would rather treat it as a block and skip the page.
# EITHER WAY, record your choice in docs/clarifications_log.md.
CLICK_CONTINUE_BUTTON = True

# NEVER changed by this project: we do not log in, do not solve CAPTCHAs,
# do not rotate proxies, and do not spoof fingerprints.

# ============================================================================
# SCOPE
# ============================================================================
INCLUDE_SPONSORED = False   # sponsored slots change per request -> not stable
MAX_PRODUCTS_PER_KEYWORD = None   # None = every unique listing on page 1

# ============================================================================
# DEBUG
# ============================================================================
# Raw HTML is NOT saved (you asked for this).
# Turn this on ONLY if a selector breaks and you need a sample page to inspect.
SAVE_FAILED_PAGES = False
DEBUG_DIR = DATA_DIR / "debug"

# ============================================================================
# EXPORT
# ============================================================================
LINK_CHECK_TIMEOUT = 10   # seconds per URL when validating links
LINK_CHECK_WORKERS = 8    # parallel checks - CDN URLs only (images, video)

# Delay between PRODUCT link checks. These go to Amazon, so they follow the
# same "modest request rate" rule as the scraper itself. 850 links at 0.4s is
# about 6 minutes - slow, but it keeps us inside the project constraints and
# avoids fake 503 "broken link" results.
LINK_CHECK_DELAY = 0.4

# ============================================================================
# VIDEO
# ============================================================================
# An .m3u8 is an HLS *playlist* - a text file listing stream segments. Click
# one in a browser and you download a text file, you do not see a video.
# Criterion 7.1 says "video links should work", so playlists are rejected by
# default and video_url is left None (has_video and video_page_url still carry
# the information).
#
# Set True only if your reviewer accepts a playlist URL.
ACCEPT_HLS_VIDEO = False

# ============================================================================
# IMAGE DOWNLOAD  (Feature C needs local copies)
# ============================================================================
# Images live on Amazon's CDN, which is built for heavy traffic - unlike the
# product PAGES, which stay slow and single-threaded. Parallel is fine here.
IMAGE_WORKERS = 8
IMAGE_TIMEOUT = 20          # seconds per image
MIN_IMAGE_BYTES = 2000      # anything smaller is a placeholder, not a photo

# Fields checked in the M1 completeness report, with acceptable null rates
NULL_RATE_LIMITS = {
    "title": 0.02,
    "brand": 0.15,
    "price": 0.20,
    "rating": 0.25,
    "review_count": 0.25,
    "image_url": 0.05,
    "description": 0.20,
}


def make_folders():
    """Create every folder the project writes into. Safe to call repeatedly."""
    for folder in (DATA_DIR, LOGS_DIR, MODELS_DIR, DATA_DIR / "clean"):
        folder.mkdir(parents=True, exist_ok=True)
    if SAVE_FAILED_PAGES:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
