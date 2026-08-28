"""
scraper/selectors.py
====================
Every CSS selector in the project lives here.

WHY THEY ARE LISTS
------------------
Amazon does not serve one page layout - it serves several. A single selector
works on some pages and silently returns nothing on others, which is how you
end up with a 40% null rate and no idea why.

So each field is a LIST, tried in order. The first one that finds text wins.

WHEN A FIELD HAS TOO MANY NULLS, FIX IT HERE. Never inside parser.py.

HOW TO ADD A SELECTOR
---------------------
  1. Open a product page in Chrome
  2. Right-click the value you want -> Inspect
  3. Right-click the highlighted HTML -> Copy -> Copy selector
  4. Test it in the Console:  document.querySelectorAll("your-selector")
  5. Add it to the bottom of the right list below
"""

# ===========================================================================
# SEARCH RESULTS PAGE
# ===========================================================================
# The container holding all results. Used as the "anchor" - if this is missing,
# the page did not really load.
SEARCH_CONTAINER = "div.s-main-slot"

# One box per product in the results.
SEARCH_CARD = (
    "div.s-result-item[data-asin], "
    "div[data-asin][data-component-type='s-search-result']"
)

# Marks a paid placement. Sponsored slots change per request, so we skip them.
SPONSORED_LABEL = [
    "span.s-sponsored-label-text",
    "span.puis-sponsored-label-text",
    "a.puis-sponsored-label-text",
    "div.puis-sponsored-label-text",
]

SEARCH_TITLE = ["h2 a span", "h2 span", "span.a-text-normal"]

# ===========================================================================
# PRODUCT PAGE - anchor
# ===========================================================================
PRODUCT_ANCHOR = "#productTitle"

# ===========================================================================
# PRODUCT PAGE - text fields
# ===========================================================================
TITLE = ["#productTitle", "span#title", "h1.a-size-large span"]

BRAND = [
    "#bylineInfo",
    "tr.po-brand td.a-span9 span",
    "a#bylineInfo",
    "#brand",
]

PRICE = [
    "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
    "#corePrice_feature_div span.a-price span.a-offscreen",
    "span.a-price.priceToPay span.a-offscreen",
    "span.a-price span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
]

LIST_PRICE = [
    "span.a-price.a-text-price span.a-offscreen",
    "span.basisPrice span.a-offscreen",
]

RATING = [
    "span[data-hook='rating-out-of-text']",
    "#acrPopover span.a-icon-alt",
    "#averageCustomerReviews span.a-icon-alt",
    "i.a-icon-star span.a-icon-alt",
]

REVIEW_COUNT = [
    "#acrCustomerReviewText",
    "span[data-hook='total-review-count']",
]

BULLETS = [
    "#feature-bullets ul li span.a-list-item",
    "#featurebullets_feature_div ul li span",
]

DESCRIPTION = ["#productDescription", "#aplus", "#bookDescription_feature_div"]

BREADCRUMB = ["#wayfinding-breadcrumbs_feature_div", "#nav-subnav"]

AVAILABILITY = ["#availability span", "#availability", "#outOfStock span"]

# ===========================================================================
# PRODUCT PAGE - images
# ===========================================================================
MAIN_IMAGE = [
    "#landingImage",
    "#imgTagWrapperId img",
    "#main-image-container img",
    "#imgBlkFront",
]

# Attributes checked on the image element, best first.
# 'data-a-dynamic-image' holds a JSON map of {url: [width, height]} - we pick
# the biggest one, because Feature C clusters these images.
IMAGE_ATTRIBUTES = ["data-old-hires", "data-a-dynamic-image", "src"]

# ===========================================================================
# PRODUCT PAGE - video
# ===========================================================================
VIDEO_CONTAINER = [
    "#vse-related-videos-container",
    "#main-video-container",
    "#videoBlock",
    "div.vse-video-container",
    "[data-video-url]",
    "#altImages li.videoThumbnail",
]

VIDEO_ATTRIBUTES = ["data-video-url", "data-src"]

# ===========================================================================
# REVIEWS  (only the ones shown publicly on the product page)
# ===========================================================================
REVIEW_BLOCK = ["div[data-hook='review']", "div.review"]

# review rating is the LABEL SOURCE for Feature B - if this breaks, Feature B
# has nothing to learn from. Check it with `python main.py test`.
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
]

REVIEW_BODY = [
    "span[data-hook='review-body'] span",
    "span[data-hook='review-body']",
]

REVIEW_DATE = ["span[data-hook='review-date']"]
REVIEW_AUTHOR = ["span.a-profile-name"]
REVIEW_VERIFIED = ["span[data-hook='avp-badge']"]
REVIEW_HELPFUL = ["span[data-hook='helpful-vote-statement']"]

# ===========================================================================
# INTERSTITIALS AND BLOCKS
# ===========================================================================
# The soft "Continue shopping" speed bump. Not a CAPTCHA.
CONTINUE_BUTTON = [
    "button.a-button-text",
    "input[type='submit'][value*='Continue']",
    "button[alt='Continue shopping']",
    "span.a-button-inner button",
]

# Text that only appears on a genuine robot-check page.
# If we see any of these, we log 'blocked' and move on. We never solve them.
CAPTCHA_MARKERS = [
    "Enter the characters you see below",
    "Sorry, we just need to make sure you're not a robot",
    "Type the characters you see in this image",
    "api-services-support@amazon.com",
    "To discuss automated access to Amazon data please contact",
    "Robot Check",
]
