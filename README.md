# Amazon Product & Review Intelligence — Part A (Data Collection)

Collects product and review data from amazon.com for an assigned list of search
keywords, using a purpose-built Selenium pipeline. No pre-existing datasets.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Google Chrome must be installed. ChromeDriver is **not** required — Selenium
Manager (bundled with Selenium 4.6+) resolves it automatically.

The keyword list lives at `data/input/keywords.csv` with columns
`SN` and `Search Keywords`.

---

## Running

```bash
# 1. Verify selectors before committing to a long run  (do this first)
python scripts/smoke_test.py

# 2. Full collection - safe to interrupt and re-run, it resumes
python scripts/run_collection.py

# 3. Validate links and write the deliverable dataset
python scripts/run_export.py

# 4. Print the M1 completeness report
python scripts/run_report.py
```

Useful flags during development:

```bash
python scripts/run_collection.py --first 2 --limit 3     # tiny trial run
python scripts/run_collection.py --keyword "gaming mouse"
python scripts/run_export.py --skip-link-check
```

---

## Outputs

| Path | Contents |
|---|---|
| `data/final/products.csv` | **Deliverable.** One row per product; reviews as a JSON array in one field |
| `data/final/products.jsonl` | Lossless master (CSV is primary because Excel truncates long cells) |
| `data/raw/products.jsonl` | Incremental write target; also the resume state |
| `data/raw_html/` | Raw evidence — every page fetched, saved verbatim |
| `logs/request_log.csv` | Audit log: one row per request attempt, including retries |
| `logs/run.log` | Human-readable run log |
| `reports/m1_null_rates*.csv` | Completeness diagnostics |

---

## Architecture

```
keywords.csv
   │
   ▼
runner.py ──► fetch.py ──► raw_html/          (retries, backoff, block detection)
   │             └──────► request_log.csv     (every attempt logged)
   │
   ├──► search_parser.py    search page  → unique ASINs
   ├──► product_parser.py   product page → schema row
   │       ├── media_parser.py    hi-res image + video
   │       └── review_parser.py   public reviews
   │
   ▼
store.py  (append per product, resume by ASIN)
   │
   ▼
export.py  (link validation → CSV + JSONL)
   │
   ▼
report.py  (M1 completeness report)
```

**Every CSS selector lives in `src/scraper/selectors.py`** as an ordered fallback
chain. Amazon serves several page layouts; a single selector silently nulls a
large share of rows. When a null rate is high, fix it there — never inside a
parser.

---

## Design decisions

| Decision | Reason |
|---|---|
| Selenium loads, BeautifulSoup parses | Avoids `StaleElementReferenceException`; lets you re-parse saved HTML without re-scraping |
| Raw HTML saved for every page | A wrong selector becomes a 2-minute re-parse, not a 4-hour re-scrape |
| Append to JSONL per product | A crash at product 900 loses one row, not the run |
| Logger written before the scraper | The log is a graded deliverable; one reconstructed afterwards is fiction |
| `fetch_page` never raises | Returns `None`, logs the outcome, run continues |
| Driver recycled every 150 pages | Chrome memory growth degrades sessions into false "empty" pages |
| Visible (non-headless) browser | Blocked noticeably less often |
| CSV primary, JSONL master | Excel silently truncates cells beyond ~32,767 chars, destroying the reviews field |
| Three video columns | CDN media URLs are signed and expire; `has_video` + `video_page_url` always work |

---

## Constraints observed

- Public pages only. No authentication, no CAPTCHA solving, no proxy rotation,
  no fingerprint spoofing.
- Blocks are detected, logged as `blocked`, backed off, and reported honestly —
  never circumvented.
- Reviews are taken from the public product detail page. Amazon gates full
  review pages behind sign-in; `reviews_collected` vs `review_count` makes the
  gap measurable.
- No personal data beyond publicly displayed reviewer names.
- Request rate: single-threaded, 4–6 s randomised delay, escalating backoff,
  10-minute cool-off after 5 consecutive blocks.

---

## Before the full run

Run `scripts/smoke_test.py` and confirm every check passes — in particular
**review star ratings**. They are the entire label basis for Feature B, and
discovering they fail to parse on day 3 leaves no recovery time.

Also confirm the scroll reaches the media block: video markup that never
rendered cannot be recovered by re-parsing, only by re-scraping.
