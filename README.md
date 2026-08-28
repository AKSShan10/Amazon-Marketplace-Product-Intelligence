# Amazon Product & Review Intelligence

Scrapes product and review data from **amazon.com** for a list of search
keywords read from a CSV file.

**Part A (data collection) is complete and verified.** Parts B and C have
folders waiting.

---

## 1. The keyword file — start here

The scraper reads your keywords from:

```
data/keywords.csv          ← PUT YOUR KEYWORDS HERE
```

It must have a column named exactly **`Search Keywords`**. An `SN` column is
optional and ignored.

```csv
SN,Search Keywords
1,wireless earbuds
2,gaming mouse
3,USB-C hub
4,mechanical keyboard
5,smartwatch
```

A file with all 28 keywords is already in place. To use your own, either edit
that file or replace it — keep the same name and column header.

**If your list is an Excel file**, open it in Excel and use
*File → Save As → CSV (Comma delimited)*, saving as `data/keywords.csv`.

To change the filename or column name, edit `config.py`:

```python
KEYWORDS_CSV   = DATA_DIR / "keywords.csv"
KEYWORD_COLUMN = "Search Keywords"
```

The loader is slightly forgiving: if the exact column is missing it will use
any column with "keyword" in its name. Blank rows and duplicates are removed
automatically.

---

## 2. Project structure

```
amazon-intelligence/
├── main.py                  run everything from here
├── config.py                every setting, one file
├── requirements.txt
│
├── scraper/                 ── PART A ── complete
│   ├── browser.py           Chrome: create, health-check, auto-restart
│   ├── fetch.py             load pages, handle blocks and retries
│   ├── selectors.py         every CSS selector (fix null rates HERE)
│   ├── parser.py            HTML → clean dictionaries
│   ├── storage.py           save, resume, export, M1 report
│   ├── images.py            download images for Feature C
│   ├── spotcheck.py         the 5–10% URL check (criterion 7.1)
│   ├── collect.py           the conductor
│   └── logs.py              request_log.csv + run.txt
│
├── analysis/                ── PART B ── days 3–5
├── notebooks/               ── PART B ── days 3–5
├── app/                     ── PART C ── day 6
│
├── data/
│   ├── keywords.csv         INPUT — your search keywords
│   ├── products.jsonl       master file + resume state
│   ├── products.csv         THE M1 DELIVERABLE
│   ├── images/              downloaded images + manifest.csv
│   └── clean/               Part B outputs
│
├── logs/
│   ├── request_log.csv      audit trail, one row per attempt
│   └── run.txt              human-readable run log
│
├── models/                  saved models the app will load
└── docs/data_flow.mermaid   pipeline diagram
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

---

## 4. Running the scrape

```bash
python main.py test            # 1 min  — are the selectors still valid?
python main.py collect         # hours  — reads data/keywords.csv
python main.py export          # ~10 min — link validation is deliberately slow
python main.py report          # instant — coverage and null rates
python main.py images          # ~10 min — needed by Feature C
python main.py spotcheck       # then check the sample in your browser
```

`collect` reads every row of `data/keywords.csv`, searches Amazon for each
keyword, opens every unique product on page 1, and saves the product plus its
publicly visible reviews.

**It is resumable.** Interrupt it any time; running it again skips products
already collected. A crash loses at most one product.

### All commands

| Command | What it does |
|---|---|
| `python main.py test` | Check selectors against live pages |
| `python main.py collect` | Collect everything in `data/keywords.csv` |
| `python main.py collect --retry` | Re-run only what failed last time |
| `python main.py export` | Validate links, write `products.csv` |
| `python main.py report` | M1 completeness report |
| `python main.py images` | Download product images |
| `python main.py images-check` | Verify the image folder |
| `python main.py spotcheck` | Build the 5–10% URL check sheet |
| `python main.py spotcheck --summary` | Read results back, print pass rate |
| `python main.py media-check` | Print image/video URLs so you can click them |

### Useful flags

```bash
python main.py collect --keyword "gaming mouse"       # one keyword, ignores the CSV
python main.py collect --first 3                      # first 3 rows of the CSV
python main.py collect --limit 5                      # max 5 NEW products per keyword
python main.py export --skip-links                    # faster, not for delivery
python main.py media-check --count 5 --no-fetch
```

Note on `--limit`: it caps **new** products per run, not listings examined.
Running `--limit 3` twice gives you 6 different products, not 3 — the second
run skips what you already have and moves down the list.

---

## 5. Quick trial before a long run

About 8 minutes. Worth doing after any change.

```bash
python main.py test --keyword "gaming mouse"
python main.py collect --keyword "gaming mouse" --limit 3
python main.py media-check --count 3
python main.py report
python main.py images
python main.py images-check
python main.py export --skip-links
```

Check for duplicates:

```bash
python -c "import json,collections; a=[json.loads(l)['asin'] for l in open('data/products.jsonl',encoding='utf-8') if l.strip()]; print('total:',len(a),'unique:',len(set(a)))"
```

| Symptom | Meaning |
|---|---|
| Any traceback | Real bug |
| A field 100% missing in `report` | Broken selector in `selectors.py` |
| Video URL longer than one line | Parsing bug |
| `[not_an_image (text/html)]` | Image URL returning a page |
| Corrupt files > 0 | Download saved something that isn't an image |
| unique ≠ total | Dedupe broken |

---

## 6. Output files

| File | Contents |
|---|---|
| `data/products.csv` | **The deliverable.** One row per product, reviews as a JSON array in one field |
| `data/products.jsonl` | Master copy and resume state |
| `data/images/` | Downloaded images named by ASIN, plus `manifest.csv` |
| `logs/request_log.csv` | One row per request attempt, with a plain-English reason |
| `logs/run.txt` | The run log |
| `data/null_rates_by_keyword.csv` | Which fields are missing, per keyword |

CSV is the deliverable because that is what the brief asks for. JSONL is kept
alongside because an Excel cell caps at ~32,767 characters and a reviews array
can exceed that.

---

## 7. Throttling

Amazon shows a "Continue shopping" page when it wants you to slow down. The
scraper dismisses it, counts it, and **adds a second to the delay every fifth
one**, up to +5s. The run summary reports the total.

If you see an interstitial on nearly every request, or listing counts dropping
across a session (25 → 20 → 16), you are being throttled. Raise the delays in
`config.py` and run when your connection is quiet:

```python
MIN_DELAY = 8.0
MAX_DELAY = 12.0
```

Listing counts also vary legitimately by category: Amazon uses a list layout
for electronics (~16–25 results) and a denser grid for fashion and home
(~48–65). Both are page one.

---

## 8. Images and video

### Images

`image_url` is the **original** file. Amazon encodes resize instructions in the
filename, and those are stripped:

```
.../images/I/61Xk9Z8vLqL._AC_SL1500_.jpg    1500px processed copy
.../images/I/61Xk9Z8vLqL.jpg                THE ORIGINAL  ← stored
```

| Column | Purpose |
|---|---|
| `image_url` | The original. Click it, you see the picture at full size |
| `image_url_sized` | Amazon's resized copy, used automatically if the original fails |

Guarded: base64 lazy-load placeholders rejected, only real image extensions
accepted, and the link check verifies `Content-Type: image/*` so a 200 that
returns HTML is recorded as `not_an_image`.

### Video — three columns, not one

Amazon's video links are signed, expire, and are often encoder artefacts or
HLS playlists that were never clickable.

| Column | Reliability |
|---|---|
| `has_video` | From page structure — always correct |
| `video_url` | A direct, playable `.mp4` if one exists, else None |
| `video_page_url` | The product page — **always works**, video plays there |

`.mp4` is preferred over `.m3u8`, because a playlist downloads a text file
instead of playing. If only a playlist exists, `video_url` is left null —
honest, and `video_page_url` still satisfies criterion 7.1.
`ACCEPT_HLS_VIDEO` in `config.py` changes this if your reviewer disagrees.

### Downloading

```bash
python main.py images
python main.py images-check
```

Files land as `data/images/B08HMWZBXC.jpg` with a `manifest.csv` recording
asin, filename, which URL was used, size and status. Feature C joins it on
`asin`. Resumable, falls back to the sized URL, and verifies magic bytes so an
HTML error page saved as `.jpg` is rejected. Expect 50–150 MB for 850 products.

---

## 9. Spot-check: 5–10% of URLs (criterion 7.1)

```bash
python main.py spotcheck              # build the sheet
#   ...check the sample in your browser...
python main.py spotcheck --summary    # pass rate + mismatch list
```

**Why part manual:** the criterion exists to prove a *human* confirmed the data
matches the live site. Verifying by scraping again would only test the scraper
against itself.

`data/spotcheck.html` opens in a browser: scraped values on the left, a link to
the live page, tick boxes, Export button. Sample is random with a **fixed
seed**, so it is reproducible.

One product wrong on one field is normal. **The same field wrong across several
products means a broken selector.**

---

## 10. Fixing high null rates

1. `python main.py report` → look at "Missing data by field"
2. Open `data/null_rates_by_keyword.csv`
   - null **everywhere** → genuinely absent, document it
   - null for **a few keywords only** → an unhandled layout
3. Open one of those product pages in Chrome → Inspect → Copy selector
4. Add it to the end of the right list in `scraper/selectors.py`
5. Re-run — resume means only missing products are fetched

---

## 11. If a file is open in Excel

Windows locks it, so Python cannot append. **This no longer crashes the run.**
Rows are held in memory and written when the file is free; you get one warning.
If still locked at the end, they go to `logs/request_log_<timestamp>.csv`.
Products go to `data/products_overflow.jsonl`, and both files are read on
resume and export.

Still — close them before a long run.

---

## 12. Constraints observed

No login. No CAPTCHA solving. No proxies. No fingerprint spoofing. Reviews come
from the public product page only. Blocks are logged honestly. Requests are
single-threaded with randomised delays, escalating backoff, and a 10-minute
cool-off after 5 consecutive blocks.

**Two decisions are yours to record** in the clarifications log:

- `CLICK_CONTINUE_BUTTON = True` — dismisses Amazon's plain "Continue shopping"
  page. No puzzle, no login; closer to a cookie banner. Set `False` to treat it
  as a block.
- Sponsored listings are excluded, because they vary per request and would make
  the dataset irreproducible.

---

## 13. Known gaps

| Gap | Status |
|---|---|
| Excel keyword input | Loader is CSV-only; convert your `.xlsx` first |
| M1 "raw evidence" | Raw HTML no longer saved — keep the folder from your first run |
| Reviews behind sign-in | Public product-page reviews only; clarification Q9 |
| Parts B and C | Not started; days 3–6 |
