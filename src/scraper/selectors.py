"""
Every CSS selector used by the project lives here, as ordered fallback chains.

Amazon serves several page layouts. A single selector silently nulls a large
share of rows; a chain tries each in order and accepts the first non-empty hit.

When a null rate is high, fix it HERE - never inside a parser.
"""

# ---------------------------------------------------------------------------
# Search results page
# ---------------------------------------------------------------------------
SEARCH_RESULT_CARD = "div.s-result-item[data-asin], div[data-asin][data-component-type='s-search-result']"

SPONSORED_MARKER = [
    "span.s-sponsored-label-text",
    "span.puis-sponsored-label-text",
    "a.puis-sponsored-label-text",
    "div.puis-sponsored-label-text",
]

SEARCH_TITLE = ["h2 a span", "h2 span", "span.a-text-normal"]

# ---------------------------------------------------------------------------
# Product page - text fields
# ---------------------------------------------------------------------------
TITLE = ["#productTitle", "span#title", "h1.a-size-large span"]

BRAND = [
    "#bylineInfo",
    "tr.po-brand td.a-span9 span",
    "#brand",
    "a#bylineInfo",
    "div#detailBullets_feature_div span.a-text-bold + span",
]

PRICE = [
    "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
    "#corePrice_feature_div span.a-price span.a-offscreen",
    "span.a-price.priceToPay span.a-offscreen",
    "span.a-price span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#price_inside_buybox",
]

LIST_PRICE = [
    "span.a-price.a-text-price span.a-offscreen",
    "#listPrice",
    "span.basisPrice span.a-offscreen",
]

RATING = [
    "span[data-hook='rating-out-of-text']",
    "#acrPopover span.a-icon-alt",
    "i.a-icon-star span.a-icon-alt",
    "#averageCustomerReviews span.a-icon-alt",
]

REVIEW_COUNT = [
    "#acrCustomerReviewText",
    "span[data-hook='total-review-count']",
    "#reviewsMedley span.a-size-base",
]

BULLETS = [
    "#feature-bullets ul li span.a-list-item",
    "#featurebullets_feature_div ul li span",
]

DESCRIPTION = [
    "#productDescription",
    "#aplus",
    "#aplus_feature_div",
    "#bookDescription_feature_div",
]

BREADCRUMB = [
    "#wayfinding-breadcrumbs_feature_div",
    "#nav-subnav",
    "div.a-breadcrumb",
]

AVAILABILITY = [
    "#availability span",
    "#availability",
    "#outOfStock span",
    "#exports_desktop_outOfStock_buybox_message_feature_div",
]

# ---------------------------------------------------------------------------
# Product page - media
# ---------------------------------------------------------------------------
MAIN_IMAGE = [
    "#landingImage",
    "#imgTagWrapperId img",
    "#main-image-container img",
    "#ebooksImgBlkFront",
    "#imgBlkFront",
]

# Attributes checked on the main image element, in priority order
IMAGE_HIRES_ATTRS = ["data-old-hires", "data-a-dynamic-image", "src"]

VIDEO_CONTAINER = [
    "#vse-related-videos-container",
    "#main-video-container",
    "#videoBlock",
    "div.vse-video-container",
    "[data-video-url]",
    "#altImages li.videoThumbnail",
]

VIDEO_URL_ATTR = ["data-video-url", "data-src"]

# ---------------------------------------------------------------------------
# Reviews (public block on the product page)
# ---------------------------------------------------------------------------
REVIEW_BLOCK = [
    "div[data-hook='review']",
    "div.review",
    "#cm-cr-dp-review-list div[data-hook='review']",
]

REVIEW_RATING = [
    "i[data-hook='review-star-rating'] span.a-icon-alt",
    "i[data-hook='cmps-review-star-rating'] span.a-icon-alt",
    "span.a-icon-alt",
]

REVIEW_TITLE = [
    "a[data-hook='review-title'] span:not([class])",
    "a[data-hook='review-title'] span",
    "span[data-hook='review-title'] span",
    "a[data-hook='review-title']",
    "span[data-hook='review-title']",
]

REVIEW_BODY = [
    "span[data-hook='review-body'] span",
    "span[data-hook='review-body']",
    "div[data-hook='review-collapsed'] span",
]

REVIEW_DATE = ["span[data-hook='review-date']"]

REVIEW_AUTHOR = ["span.a-profile-name", "div.a-profile-content span.a-profile-name"]

REVIEW_VERIFIED = ["span[data-hook='avp-badge']", "span.a-declarative span.a-size-mini"]

REVIEW_HELPFUL = ["span[data-hook='helpful-vote-statement']"]

# ---------------------------------------------------------------------------
# Block / interstitial detection
# ---------------------------------------------------------------------------
BLOCK_MARKERS = [
    "Enter the characters you see below",
    "Sorry, we just need to make sure you're not a robot",
    "api-services-support@amazon.com",
    "Type the characters you see in this image",
    "To discuss automated access to Amazon data please contact",
    "Robot Check",
    "/errors/validateCaptcha",
]

# Presence of any of these means the page rendered as a real product page
PRODUCT_PAGE_MARKERS = ["#productTitle", "#dp", "#ppd", "#centerCol"]
