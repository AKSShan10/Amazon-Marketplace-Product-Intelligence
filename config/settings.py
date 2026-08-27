"""
Central configuration. Every tunable value lives here.
No module may hardcode a path, delay or threshold.
"""
from pathlib import Path

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

DATA_DIR      = ROOT / "data"
INPUT_CSV     = DATA_DIR / "input" / "keywords.csv"
RAW_HTML_DIR  = DATA_DIR / "raw_html"
RAW_JSONL     = DATA_DIR / "raw" / "products.jsonl"
FINAL_CSV     = DATA_DIR / "final" / "products.csv"
FINAL_JSONL   = DATA_DIR / "final" / "products.jsonl"

LOG_DIR       = ROOT / "logs"
REQUEST_LOG   = LOG_DIR / "request_log.csv"
RUN_LOG       = LOG_DIR / "run.log"

REPORT_DIR    = ROOT / "reports"

# Columns expected in the input CSV
KEYWORD_COLUMN = "Search Keywords"
SN_COLUMN      = "SN"

# ----------------------------------------------------------------------------
# Target site
# ----------------------------------------------------------------------------
DOMAIN        = "https://www.amazon.com"
SEARCH_PATH   = "/s?k={query}"
PRODUCT_PATH  = "/dp/{asin}"

# ----------------------------------------------------------------------------
# Browser
# ----------------------------------------------------------------------------
HEADLESS          = False        # visible browser is blocked less often
WINDOW_SIZE       = (1920, 1080)
PAGE_LOAD_TIMEOUT = 30           # seconds
IMPLICIT_WAIT     = 0            # must stay 0 - never mix with explicit waits
ELEMENT_TIMEOUT   = 15           # explicit wait ceiling
LOCALE            = "en-US"

# Restart the driver every N product pages to contain memory growth
DRIVER_RESTART_EVERY = 150

# ----------------------------------------------------------------------------
# Politeness and resilience
# ----------------------------------------------------------------------------
MIN_DELAY     = 4.0              # seconds between requests
MAX_DELAY     = 6.5
MAX_RETRIES   = 3
BACKOFF_BASE  = 30               # seconds; multiplied by attempt number
SCROLL_PASSES = 4                # progressive scrolls to trigger lazy content
SCROLL_PAUSE  = 1.2              # seconds after each scroll

# A page shorter than this is treated as empty/interstitial
MIN_PAGE_CHARS = 5000

# Cool-off applied after this many consecutive blocks
CONSECUTIVE_BLOCK_LIMIT = 5
COOL_OFF_SECONDS        = 600

# ----------------------------------------------------------------------------
# Collection scope
# ----------------------------------------------------------------------------
INCLUDE_SPONSORED   = False      # see clarification Q5
MAX_PRODUCTS_PER_KW = None       # None = all unique listings on page 1

# ----------------------------------------------------------------------------
# Validation thresholds (used by the completeness report)
# ----------------------------------------------------------------------------
NULL_RATE_THRESHOLDS = {
    "title":            0.02,
    "brand":            0.15,
    "price":            0.20,
    "rating":           0.25,
    "review_count":     0.25,
    "image_url_hires":  0.05,
    "description":      0.20,
    "category_breadcrumb": 0.30,
}

LINK_CHECK_TIMEOUT = 10
LINK_CHECK_WORKERS = 8


def ensure_dirs() -> None:
    """Create every directory the pipeline writes to."""
    for path in (
        RAW_HTML_DIR, RAW_JSONL.parent, FINAL_CSV.parent,
        LOG_DIR, REPORT_DIR, INPUT_CSV.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)
