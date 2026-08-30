"""
analysis/cleaning.py
====================
Shared cleaning pipeline. All four feature notebooks import from here.

WHY THIS IS A .py FILE AND NOT JUST NOTEBOOK CELLS
---------------------------------------------------
The brief asks for a "cleaning pipeline", not just a cleaned dataset. And the
Streamlit app needs the same transformations at runtime. If the logic lives
only in a notebook, you end up copy-pasting it into the app and the two drift.

USAGE
-----
    from analysis.cleaning import load_and_clean

    products, reviews = load_and_clean()

Both DataFrames are also saved to data/clean/ so notebooks can load them
without re-running the pipeline every time.
"""

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CLEAN_DIR = DATA_DIR / "clean"

PRODUCTS_JSONL = DATA_DIR / "products.jsonl"
PRODUCTS_CLEAN = CLEAN_DIR / "products_clean.parquet"
REVIEWS_CLEAN = CLEAN_DIR / "reviews_clean.parquet"


# ===========================================================================
# 1. LOADING
# ===========================================================================
def load_raw():
    """
    Load products.jsonl into two DataFrames: products and reviews.

    Reviews are exploded into a separate table with one row per review,
    joined to the product by ASIN. This is the shape Feature B needs.
    """
    if not PRODUCTS_JSONL.exists():
        raise FileNotFoundError(
            f"{PRODUCTS_JSONL} not found. Run the scraper first."
        )

    rows = []
    with open(PRODUCTS_JSONL, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue

    products = pd.DataFrame(rows)

    # Explode reviews into a long table
    review_rows = []
    for _, product in products.iterrows():
        asin = product.get("asin")
        keyword = product.get("search_keyword")
        brand = product.get("brand")
        for review in (product.get("reviews") or []):
            review_rows.append({
                **review,
                "asin": asin,
                "search_keyword": keyword,
                "brand": brand,
            })

    reviews = pd.DataFrame(review_rows) if review_rows else pd.DataFrame()

    return products, reviews


# ===========================================================================
# 2. PRODUCT CLEANING
# ===========================================================================
_WHITESPACE = re.compile(r"\s+")
_HTML_ENTITIES = re.compile(r"&[a-zA-Z]+;|&#\d+;")


def clean_text(text):
    """Collapse whitespace, strip HTML entities. None stays None."""
    if pd.isna(text) or text is None:
        return None
    text = _HTML_ENTITIES.sub(" ", str(text))
    text = _WHITESPACE.sub(" ", text).strip()
    return text if text else None


def clean_products(df):
    """
    Clean the products table.

    Returns a new DataFrame — the original is not modified.
    """
    df = df.copy()

    # --- duplicates ---
    before = len(df)
    df = df.drop_duplicates(subset="asin", keep="first")
    dupes_dropped = before - len(df)

    # --- text fields ---
    for col in ("title", "brand", "description"):
        if col in df.columns:
            df[col] = df[col].apply(clean_text)

    # --- bullets: join list into a string ---
    if "bullets" in df.columns:
        df["bullets_text"] = df["bullets"].apply(
            lambda b: " ".join(b) if isinstance(b, list) else clean_text(b)
        )

    # --- combined text for Features A and D ---
    text_parts = []
    for col in ("title", "brand", "description", "bullets_text"):
        if col in df.columns:
            text_parts.append(df[col].fillna(""))
    if text_parts:
        df["combined_text"] = (
            pd.concat(text_parts, axis=1)
            .apply(lambda row: " ".join(row).strip(), axis=1)
        )

    # --- price: already float, just flag status ---
    if "price" in df.columns and "price_status" in df.columns:
        df["has_price"] = df["price_status"] == "available"

    # --- ensure numeric types ---
    for col in ("price", "rating", "review_count", "reviews_collected"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- log-transformed review count (for Feature D) ---
    if "review_count" in df.columns:
        df["log_review_count"] = np.log1p(df["review_count"])

    # --- title word count (for Feature D) ---
    if "title" in df.columns:
        df["title_word_count"] = df["title"].fillna("").str.split().str.len()

    print(f"Products cleaned: {len(df)} rows, {dupes_dropped} duplicates dropped")
    return df


# ===========================================================================
# 3. REVIEW CLEANING
# ===========================================================================
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_PATTERN = re.compile(r"\S+@\S+\.\S+")

# Languages to keep. Set to None to keep everything.
#
# DECISION: keep all languages. Reviews are still detected and labeled via
# `language`, but nothing is dropped on language alone — non-English
# reviews still carry real sentiment signal (Gemini's sentiment models are
# multilingual), and throwing them out would lose real data for no clear
# benefit. Only the <=3-word filter below actually removes rows.
KEEP_LANGUAGES = None


def clean_review_text(text):
    """
    Clean one review body.

    IMPORTANT: negation words (not, no, never, don't, won't, etc.) are
    deliberately KEPT. Removing them destroys sentiment — "not bad" becomes
    "bad" and a positive review flips negative.
    """
    if pd.isna(text) or text is None:
        return None

    text = str(text)
    text = _URL_PATTERN.sub(" ", text)
    text = _EMAIL_PATTERN.sub(" ", text)
    text = _HTML_ENTITIES.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    return text if text else None


def _detect_language(text):
    """Detect the language of a text. Returns 'en', 'es', etc., or 'unknown'."""
    try:
        from langdetect import detect
        return detect(str(text))
    except ImportError:
        return "missing_dependency"
    except Exception:
        return "unknown"


def _review_id(row):
    """Generate a stable review ID from content fields.

    Uses the same columns as sentiment_checkpointed.prepare_reviews so
    that checkpoint review_ids always match the cleaned parquet.
    """
    identity = "|".join(
        str(row.get(c) or "") for c in ("asin", "reviewer_name", "date", "body_clean")
    )
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20]


def clean_reviews(df, min_words=4, detect_lang=True):
    """
    Clean the reviews table.

    - Strips URLs, emails, HTML entities
    - Drops reviews with <= 3 words (no signal, pure noise for sentiment
      and text-similarity models — "Great!" or "Broke fast" can't be
      reliably classified or matched on)
    - Detects language for every review (stored in `language`); does NOT
      drop by language unless KEEP_LANGUAGES is set to a specific list
    - Drops exact duplicates (same reviewer, same ASIN, same date, same text)
    - Generates star_sentiment from rating (silver labels for Feature B)
    - Generates a stable review_id for checkpoint compatibility

    NOTE ON ORDER: language is detected on body_clean AFTER the short-review
    filter. langdetect is unreliable on very short strings anyway (a 2-word
    review gives it almost nothing to work with), so filtering short reviews
    first makes the language call more accurate and cheaper to run.
    """
    if df.empty:
        print("No reviews to clean.")
        return df

    df = df.copy()

    # --- clean text first (needed for dedup) ---
    if "body" in df.columns:
        df["body_clean"] = df["body"].apply(clean_review_text)

    if "title" in df.columns:
        df["title_clean"] = df["title"].apply(clean_review_text)

    # --- drop reviews with <= min_words words ---
    # min_words=4 means: drop 0,1,2,3-word reviews; keep 4+.
    short_dropped = 0
    if "body_clean" in df.columns:
        word_counts = df["body_clean"].fillna("").str.split().str.len()
        short = word_counts < min_words
        df = df[~short]
        short_dropped = int(short.sum())

    # --- duplicates (after text cleaning so normalized text is compared) ---
    before = len(df)
    dedup_cols = [
        c for c in ("asin", "reviewer_name", "date", "title_clean", "body_clean")
        if c in df.columns
    ]
    if dedup_cols:
        df = df.drop_duplicates(subset=dedup_cols, keep="first")
    dupes_dropped = before - len(df)

    # --- language detection: always run it if requested, on the full set ---
    # (no sampling heuristic — with min_words already applied, the dataset
    # is smaller and detection is fast enough to just do outright, and a
    # sample-based skip risked silently missing a genuinely mixed-language
    # dataset if the 100-row sample happened to be mostly English.)
    lang_dropped = 0
    if detect_lang and "body_clean" in df.columns:
        df["language"] = df["body_clean"].apply(_detect_language)

        if (df["language"] == "missing_dependency").all():
            print("  WARNING: 'langdetect' is not installed. Run `pip install langdetect`.")
            print("  Skipping language filtering — all reviews kept, language='unknown'.")
            df["language"] = "unknown"
        else:
            print("  Language distribution:")
            for lang, count in df["language"].value_counts().items():
                print(f"    - {lang}: {count} reviews")

            if KEEP_LANGUAGES:
                before_lang = len(df)
                df = df[df["language"].isin(KEEP_LANGUAGES) | (df["language"] == "unknown")]
                lang_dropped = before_lang - len(df)
    elif "body_clean" in df.columns:
        df["language"] = "unknown"

    # --- ensure rating is numeric ---
    if "rating" in df.columns:
        df["rating"] = pd.to_numeric(df["rating"], errors="coerce")

    # --- star sentiment: silver labels for Feature B ---
    if "rating" in df.columns:
        df["star_sentiment"] = pd.cut(
            df["rating"],
            bins=[0, 2, 3, 5],
            labels=["negative", "neutral", "positive"],
        ).astype("string")

    # --- stable review ID (matches sentiment_checkpointed.prepare_reviews) ---
    df["review_id"] = df.apply(_review_id, axis=1)
    df = df.drop_duplicates("review_id", keep="first").reset_index(drop=True)

    print(f"Reviews cleaned: {len(df)} rows")
    print(f"  duplicates dropped   : {dupes_dropped}")
    print(f"  <=3 word dropped     : {short_dropped}")
    print(f"  non-English dropped  : {lang_dropped}")

    return df


# ===========================================================================
# 4. MAIN ENTRY POINT
# ===========================================================================
def load_and_clean(save=True, detect_lang=True):
    """
    Load, clean, and optionally save to parquet.

    Returns (products_df, reviews_df).
    """
    print("Loading raw data...")
    products_raw, reviews_raw = load_raw()
    print(f"  {len(products_raw)} products, {len(reviews_raw)} reviews\n")

    print("Cleaning products...")
    products = clean_products(products_raw)

    print("\nCleaning reviews...")
    reviews = clean_reviews(reviews_raw, detect_lang=detect_lang)

    if save:
        CLEAN_DIR.mkdir(parents=True, exist_ok=True)
        products.to_parquet(PRODUCTS_CLEAN, index=False)
        reviews.to_parquet(REVIEWS_CLEAN, index=False)
        print(f"\nSaved to:")
        print(f"  {PRODUCTS_CLEAN}")
        print(f"  {REVIEWS_CLEAN}")

    return products, reviews


def load_clean():
    """
    Load the already-cleaned parquet files.

    Use this in notebooks after the first run, so you don't re-run the
    pipeline every time you restart the kernel.

    If the cached files are missing required columns (from an older run),
    the pipeline is re-run automatically.
    """
    if not PRODUCTS_CLEAN.exists() or not REVIEWS_CLEAN.exists():
        print("Clean files not found. Running the pipeline...")
        return load_and_clean()

    products = pd.read_parquet(PRODUCTS_CLEAN)
    reviews = pd.read_parquet(REVIEWS_CLEAN)

    required_review_cols = {"review_id", "body_clean", "star_sentiment", "language"}
    if not required_review_cols.issubset(reviews.columns):
        print("Cached clean files use an old schema; rebuilding now.")
        return load_and_clean()

    print(f"Loaded {len(products)} products, {len(reviews)} reviews from cache")
    return products, reviews
