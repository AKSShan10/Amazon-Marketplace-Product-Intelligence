# Amazon Product & Review Intelligence

Scrapes product and review data from **amazon.com** for 28 assigned search
keywords, collecting product information, images, video links, and customer
reviews from every unique listing on page 1 of each search result.

**Part A (data collection) is complete and verified.** Parts B (analysis and
model development) and C (Streamlit application) have folders waiting.

### Completed run

| Metric | Value |
|---|---|
| Products | 859 |
| Keywords covered | 28 / 28 |
| Reviews with body text | 9,981 |
| Request success rate | 98.9% |
| Product links validated | 859 / 859 ok |
| Image links validated | 859 / 859 ok |
| Video direct links | 600 ok |
| Images downloaded | 859 files, 132 MB |

---

## 1. The keyword file

The scraper reads keywords from:

```
data/keywords.csv
```

It must have a column named exactly **`Search Keywords`**. The `SN` column is
optional and ignored.

```csv
SN,Search Keywords
1,wireless earbuds
2,gaming mouse
3,USB-C hub
```

All 28 assigned keywords are already in place. To change the filename or
column name, edit `config.py`:

```python
KEYWORDS_CSV   = DATA_DIR / "keywords.csv"
KEYWORD_COLUMN = "Search Keywords"
```

**If your list is an Excel file**, save it as CSV first:
*File → Save As → CSV (Comma delimited)* → `data/keywords.csv`.

---

## 2. Project structure

```
amazon-intelligence/
├── main.py                  run everything from here
├── config.py                every setting, one file
├── requirements.txt
├── dump_review.py           standalone review-selector diagnostic
│
├── scraper/                 ── PART A ── complete
│   ├── browser.py           Chrome: create, health-check, auto-restart
│   ├── fetch.py             load pages, handle all obstacles
│   ├── selectors.py         every CSS selector (fix null rates HERE)
│   ├── parser.py            HTML → clean dictionaries
│   ├── storage.py           save, resume, export, M1 report
│   ├── refresh.py           re-fetch reviews without re-scraping everything
│   ├── images.py            download images for Feature C
│   ├── spotcheck.py         the 5–10% URL check (criterion 7.1)
│   ├── collect.py           the conductor
│   └── logs.py              crash-proof request logging
│
├── analysis/                ── PART B ── days 3–5
├── notebooks/               ── PART B ── days 3–5
├── app/                     ── PART C ── day 6
│
├── data/
│   ├── keywords.csv         input: 28 search keywords
│   ├── products.jsonl       master file + resume state
│   ├── products.csv         THE M1 DELIVERABLE
│   ├── images/              859 downloaded images + manifest.csv
│   └── clean/               Part B outputs
│
├── logs/
│   ├── request_log.csv      audit trail, one row per attempt, with reason
│   └── run.txt              human-readable run log
│
├── models/                  saved models the app will load
└── docs/
    ├── data_flow.html       pipeline diagram (two figures)
    └── clarifications_log.md
```

---

## 3. Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac / Linux
pip install -r requirements.txt
```

Google Chrome must be installed. **ChromeDriver is not needed** — Selenium
4.6+ downloads the matching driver automatically.

**Important:** verify lxml actually works in your environment:

```bash
python -c "from bs4 import BeautifulSoup; BeautifulSoup('<a></a>','lxml'); print('lxml OK')"
```

If this fails, run `pip install --force-reinstall --no-cache-dir lxml`. The
code falls back to `html.parser` when lxml is unavailable, but `html.parser`
handles Amazon's deeply nested review markup less reliably and can cause
review bodies to come back null.

---

## 4. Commands

### Collection

| Command | What it does |
|---|---|
| `python main.py test` | Check selectors against live pages. **Run first** |
| `python main.py collect` | Collect all products. Resumable |
| `python main.py collect --retry` | Re-run only what failed last time |

### Review recovery

| Command | What it does |
|---|---|
| `python main.py refresh-reviews --status` | How many products have review text |
| `python main.py refresh-reviews` | Re-fetch reviews only, keep everything else |
| `python main.py refresh-reviews --apply` | Merge refreshed reviews into the dataset |

### Output and verification

| Command | What it does |
|---|---|
| `python main.py export` | Validate links, write `products.csv` |
| `python main.py report` | M1 completeness report |
| `python main.py images` | Download product images |
| `python main.py images-check` | Verify the image folder |
| `python main.py spotcheck` | Build the 5–10% URL check sheet |
| `python main.py spotcheck --summary` | Read results back, print pass rate |

### Diagnostics

| Command | What it does |
|---|---|
| `python main.py media-check` | Print image/video URLs so you can click them |
| `python main.py debug-review` | Dump a live review block's HTML to fix selectors |
| `python dump_review.py` | Standalone version, no dependency on main.py |

### Useful flags

```bash
python main.py collect --keyword "gaming mouse"       # one keyword only
python main.py collect --first 3                      # first 3 from the CSV
python main.py collect --limit 5                      # max 5 NEW products per keyword
python main.py refresh-reviews --limit 10             # test on 10 products
python main.py export --skip-links                    # faster, not for delivery
python main.py media-check --count 5 --no-fetch
```

### Recommended order

```bash
python main.py test            # 1 min
python main.py collect         # hours, resumable
python main.py export          # ~20 min (link validation)
python main.py report          # instant
python main.py images          # ~10 min
python main.py spotcheck       # then verify in browser
```

---

## 5. Output files

| File | Contents |
|---|---|
| `data/products.csv` | **The deliverable.** One row per product, reviews as JSON array |
| `data/products.jsonl` | Master copy and resume state |
| `data/images/` | Images named by ASIN + `manifest.csv` |
| `logs/request_log.csv` | One row per request attempt, with plain-English reason |
| `logs/run.txt` | Run log |
| `data/null_rates_by_keyword.csv` | Missing-field diagnostic per keyword |
| `data/spotcheck.html` | Manual verification sheet |

CSV is the deliverable because the brief asks for it. JSONL is kept alongside
because Excel silently truncates cells beyond ~32,767 characters, which
destroys the reviews field.

---

## 6. What each review contains

```json
{
  "reviewer_name": "The Tally Ho",
  "rating": 5.0,
  "title": "Best Mouse I Have Owned",
  "body": "This is the best mouse I have ever owned...",
  "date": "November 23, 2025",
  "country": "United States",
  "verified_purchase": true,
  "helpful_votes": 1
}
```

`rating` is the label source for Feature B (sentiment analysis).
`body` is what gets classified. Both are required for Feature B to work.

Reviews are collected from the public product page only — Amazon gates full
review pages behind sign-in, and the project constraints forbid bypassing
logins. `reviews_collected` vs `review_count` makes the gap measurable.

---

## 7. Images and video

### Images

Two columns stored:

| Column | Purpose |
|---|---|
| `image_url` | The **original** file. Click it → bare picture at full size |
| `image_url_sized` | Amazon's resized copy, used as fallback if the original fails |

Amazon encodes resize instructions in the filename and those are stripped:

```
.../61Xk9Z8vLqL._AC_SL1500_.jpg    resized copy
.../61Xk9Z8vLqL.jpg                original  ← stored
```

Guarded: base64 placeholders rejected, only real image extensions accepted,
link checker verifies `Content-Type: image/*`, magic-byte verification on
downloaded files so an HTML error page saved as `.jpg` is caught.

### Video — three columns

Amazon's `.mp4` links are signed, expire, and are often encoder artefacts.

| Column | Reliability |
|---|---|
| `has_video` | From page structure — always correct |
| `video_url` | A direct playable `.mp4` if one exists, else null |
| `video_page_url` | The product page — **always works** |

`.mp4` preferred over `.m3u8` (playlists download a text file). Non-playable
assets (`jobtemplate`, `closedCaptions`, `videopreview`) are rejected.
`ACCEPT_HLS_VIDEO` in `config.py` changes this if needed.

### Downloading images

```bash
python main.py images          # download all
python main.py images-check    # verify the folder
```

Files: `data/images/{ASIN}.jpg` + `manifest.csv`. Resumable, parallel (CDN
URLs), automatic fallback, magic-byte verification. ~132 MB for 859 products.

---

## 8. Obstacle handling

### What is handled automatically

| Obstacle | Response |
|---|---|
| CAPTCHA / robot page | Detect, log `blocked` with reason, backoff, retry |
| Sign-in redirect | Detect `/ap/signin` in URL, log, never interact |
| "Continue shopping" page | Click it (configurable), count it, adaptive delay |
| Empty / consent page | Check for results container, not character count |
| Page-load timeout | Retry with backoff |
| Hung JavaScript | `set_script_timeout(20)` caps it |
| Frozen ChromeDriver | `COMMAND_TIMEOUT = 60` caps every command |
| Dead Chrome session | Liveness probe + immediate rebuild on `InvalidSessionIdException` |
| Memory growth | Browser recycled every 100 pages |
| Failed keywords | Automatic retry with fresh browser at end of run |
| File locked by Excel | Rows buffered in memory, written when free |
| Crash mid-run | Per-product append to JSONL, resume by ASIN set |

### What is not handled (by design)

Real CAPTCHAs are never solved. Logins are never entered. Proxies are never
used. Fingerprints are never spoofed. Reviews behind sign-in are out of reach.

### Throttling

Amazon shows the "Continue shopping" page when it wants you to slow down. The
scraper dismisses it, counts it, and adds 1 second to the delay every 5th
occurrence, up to +5s.

If listing counts drop across a session (25 → 20 → 16), raise delays in
`config.py`:

```python
MIN_DELAY = 8.0
MAX_DELAY = 12.0
```

---

## 9. The review body issue and how it was resolved

Amazon redesigned its review markup, switching from hyphenated to camelCase
`data-hook` attributes:

| Field | Old (broken) | Current (working) |
|---|---|---|
| Title | `data-hook="review-title"` | `data-hook="reviewTitle"` |
| Body | `data-hook="review-body"` | `data-hook="reviewRichContentContainer"` |

This caused all review titles and bodies to come back null while names,
ratings and dates continued working — the review block was found, but the
text selectors were stale.

**Fixed in `scraper/selectors.py`** with the current hooks placed first and
old hooks kept as fallbacks. The `reviewRichContentContainer` selector targets
the actual review text and skips the accessibility teaser text ("Brief content
visible, double tap to read full content") that sits in the outer wrapper.

**Recovery:** `python main.py refresh-reviews` re-fetched reviews for all 859
products without re-scraping product data, images, or video. The merge backed
up `products.jsonl` first and touched only the review fields.

**If review bodies go null again in the future:**

```bash
python main.py debug-review --keyword "gaming mouse"
# or the standalone version:
python dump_review.py
```

These load a live page, test every candidate selector, and dump the raw HTML
of a review block. Add the working selector to `scraper/selectors.py`.

---

## 10. Spot-check: 5–10% of URLs (criterion 7.1)

```bash
python main.py spotcheck              # build the sheet
#   ...verify the sample in your browser...
python main.py spotcheck --summary    # pass rate + mismatch list
```

**Why part manual:** the criterion proves a *human* confirmed data matches the
live site. Automated re-scraping would only test the scraper against itself.

`data/spotcheck.html` opens in a browser: scraped values on the left, link to
the live page, tick boxes, Export button. Sample is random with a fixed seed
(reproducible). For 859 products, 8% is ~69 products.

One product wrong on one field is normal. **The same field wrong across several
products means a broken selector** — fix `scraper/selectors.py`.

---

## 11. Fixing high null rates

1. `python main.py report` → "Missing data by field"
2. Open `data/null_rates_by_keyword.csv`
   - null **everywhere** → genuinely absent, document it
   - null for **a few keywords only** → unhandled layout variant
3. Open a product page in Chrome → Inspect → Copy selector
4. Add it to the right list in `scraper/selectors.py`
5. Re-run — resume skips existing products

---

## 12. If a file is open in Excel

Windows locks it. **This no longer crashes the run.** Rows are held in memory
and written when the file is free. If still locked at the end, they go to
`logs/request_log_<timestamp>.csv`. Products go to
`data/products_overflow.jsonl`, and both files are read on resume and export.

Still — close them before a long run.

---

## 13. Constraints observed

No login. No CAPTCHA solving. No proxies. No fingerprint spoofing. Reviews
from the public product page only. Blocks logged honestly. Single-threaded,
randomised delays, escalating backoff, 10-minute cool-off after 5 consecutive
blocks. Product link checks also deliberately slow and single-threaded.

**Two decisions are recorded in the clarifications log:**

- `CLICK_CONTINUE_BUTTON = True` — dismisses Amazon's "Continue shopping"
  page. No puzzle, no login. Set `False` to treat it as a block instead.
- Sponsored listings excluded — they vary per request, making the dataset
  non-reproducible.

---

## 14. Known gaps

| Gap | Status |
|---|---|
| Excel keyword input | CSV only — convert `.xlsx` first |
| M1 raw evidence | Raw HTML not saved — keep the folder from the original run if available |
| Reviews behind sign-in | Public product-page reviews only (clarification Q9) |
| Price 8.4% missing | Explained by `price_status`: 794 available, 56 variant-only (price appears after selecting size/colour), 17 unknown (empty pages). Zero unexplained gaps |
| Parts B and C | Not started — days 3–6 |
