"""Leakage-safe utilities for Feature D: product price-tier classification.

Price is used only to create evaluation labels. It is never included in the
model feature matrix. Tiers are relative to product category and detected
currency, which keeps unlike categories and currencies comparable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.svm import LinearSVC
from sklearn.feature_extraction.text import TfidfVectorizer


RANDOM_STATE = 42
TIER_ORDER = ["budget", "mid_range", "premium"]
TEXT_COLUMNS = ["title", "description", "bullets_text"]
CATEGORICAL_COLUMNS = ["brand", "search_keyword", "category_breadcrumb", "availability"]
NUMERIC_COLUMNS = [
    "rating",
    "log_review_count",
    "search_rank",
    "sponsored_numeric",
    "title_word_count",
    "description_word_count",
    "bullets_word_count",
    "brand_missing",
    "description_missing",
    "review_count_missing",
]

_CURRENCY_PREFIX = re.compile(r"^\s*([A-Za-z]{3})")
_EXPLICIT_PRICE = re.compile(
    r"(?:[$£€¥]\s*\d[\d,.]*|\b(?:usd|sek|gbp|eur|bdt|cad|aud)\s*\d[\d,.]*)",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")


def load_products(path: str | Path) -> pd.DataFrame:
    """Load a product CSV or Parquet file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, low_memory=False)


def _text(value) -> str:
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return ""
    if isinstance(value, list):
        value = " ".join(map(str, value))
    value = _EXPLICIT_PRICE.sub(" ", str(value))
    return _SPACE.sub(" ", value).strip()


def _detected_currency(frame: pd.DataFrame) -> pd.Series:
    currency = frame.get("currency", pd.Series(index=frame.index, dtype="object"))
    currency = currency.astype("string").str.strip().str.upper()
    raw = frame.get("price_raw", pd.Series(index=frame.index, dtype="object"))
    raw_currency = raw.astype("string").str.extract(_CURRENCY_PREFIX, expand=False).str.upper()
    return currency.fillna(raw_currency)


def prepare_products(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Clean Feature D fields and create model features without price leakage."""
    df = frame.copy()
    if "asin" not in df:
        raise ValueError("Product data must include an 'asin' column")

    original_rows = len(df)
    df["asin"] = df["asin"].astype("string").str.strip().str.upper()
    df = df[df["asin"].notna() & df["asin"].ne("")].copy()
    before_dedup = len(df)
    df = df.drop_duplicates("asin", keep="last").reset_index(drop=True)

    for column in TEXT_COLUMNS + CATEGORICAL_COLUMNS:
        if column not in df:
            df[column] = np.nan

    # Raw CSV bullets are JSON-like strings; retaining their text is sufficient for TF-IDF.
    df["bullets_text"] = df["bullets_text"].where(df["bullets_text"].notna(), df.get("bullets"))
    for column in TEXT_COLUMNS:
        df[column] = df[column].map(_text)

    df["model_text"] = (
        df[TEXT_COLUMNS].fillna("").agg(" ".join, axis=1).map(_text)
    )

    df["price_numeric"] = pd.to_numeric(df.get("price"), errors="coerce")
    df.loc[df["price_numeric"] <= 0, "price_numeric"] = np.nan
    df["label_currency"] = _detected_currency(df)

    df["rating"] = pd.to_numeric(df.get("rating"), errors="coerce")
    df.loc[~df["rating"].between(1, 5), "rating"] = np.nan
    review_count = pd.to_numeric(df.get("review_count"), errors="coerce")
    review_count = review_count.where(review_count >= 0)
    df["review_count_missing"] = review_count.isna().astype(int)
    df["log_review_count"] = np.log1p(review_count)

    search_rank = pd.to_numeric(df.get("search_rank"), errors="coerce")
    df["search_rank"] = search_rank.where(search_rank > 0)
    sponsored = df.get("sponsored", pd.Series(False, index=df.index))
    df["sponsored_numeric"] = sponsored.map(
        lambda value: 1.0 if str(value).strip().lower() in {"true", "1", "yes"} else 0.0
    )

    df["title_word_count"] = df["title"].str.split().str.len().astype(float)
    df["description_word_count"] = df["description"].str.split().str.len().astype(float)
    df["bullets_word_count"] = df["bullets_text"].str.split().str.len().astype(float)
    df["brand_missing"] = df["brand"].isna().astype(int)
    df["description_missing"] = df["description"].eq("").astype(int)

    # Explicit missing category values are handled inside the fitted preprocessing pipeline.
    for column in CATEGORICAL_COLUMNS:
        df[column] = df[column].astype("object").where(df[column].notna(), np.nan)

    df["label_eligible"] = (
        df["price_numeric"].notna()
        & df["label_currency"].notna()
        & df["search_keyword"].notna()
    )

    audit = {
        "original_rows": int(original_rows),
        "prepared_rows": int(len(df)),
        "duplicate_asins_removed": int(before_dedup - len(df)),
        "missing_price_rows": int(df["price_numeric"].isna().sum()),
        "unknown_currency_rows": int(df["label_currency"].isna().sum()),
        "label_eligible_rows": int(df["label_eligible"].sum()),
    }
    return df, audit


def missingness_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "title", "description", "bullets_text", "brand", "search_keyword",
        "category_breadcrumb", "availability", "rating", "review_count",
        "price", "currency",
    ]
    columns = [column for column in columns if column in frame]
    return (
        frame[columns].isna().sum().rename("missing_count").to_frame()
        .assign(missing_percent=lambda x: 100 * x["missing_count"] / len(frame))
        .sort_values("missing_percent", ascending=False)
    )


def _group_key(frame: pd.DataFrame) -> pd.Series:
    return frame["search_keyword"].astype(str) + " || " + frame["label_currency"].astype(str)


def split_for_evaluation(
    prepared: pd.DataFrame,
    test_size: float = 0.20,
    random_state: int = RANDOM_STATE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split before learning tier thresholds; preserve every category/currency group."""
    eligible = prepared[prepared["label_eligible"]].copy()
    counts = _group_key(eligible).value_counts()
    too_small = counts[counts < 3]
    if len(too_small):
        raise ValueError(f"Groups too small for a stratified split: {too_small.to_dict()}")
    train_index, test_index = train_test_split(
        eligible.index,
        test_size=test_size,
        random_state=random_state,
        stratify=_group_key(eligible),
    )
    return eligible.loc[train_index].copy(), eligible.loc[test_index].copy()


def fit_tier_rules(label_training_data: pd.DataFrame) -> pd.DataFrame:
    """Learn tie-safe 33rd/67th percentile cutoffs from training price points only."""
    records = []
    grouped = label_training_data.groupby(["search_keyword", "label_currency"], dropna=False)
    for (category, currency), group in grouped:
        unique_prices = np.sort(group["price_numeric"].dropna().unique())
        if len(unique_prices) < 3:
            raise ValueError(f"Need at least three distinct prices for {category!r}, {currency!r}")
        lower, upper = np.quantile(unique_prices, [1 / 3, 2 / 3])
        if not lower < upper:
            raise ValueError(f"Tier boundaries collapsed for {category!r}, {currency!r}")
        records.append({
            "search_keyword": category,
            "label_currency": currency,
            "budget_upper": float(lower),
            "mid_range_upper": float(upper),
            "training_rows": int(len(group)),
            "distinct_prices": int(len(unique_prices)),
        })
    return pd.DataFrame(records).sort_values(["search_keyword", "label_currency"]).reset_index(drop=True)


def apply_tier_rules(frame: pd.DataFrame, rules: pd.DataFrame) -> pd.Series:
    """Apply learned price cutoffs. Price is used here only to create ground truth."""
    lookup = rules.set_index(["search_keyword", "label_currency"])
    labels = []
    for row in frame.itertuples(index=False):
        key = (row.search_keyword, row.label_currency)
        if key not in lookup.index or pd.isna(row.price_numeric):
            labels.append(pd.NA)
            continue
        rule = lookup.loc[key]
        if row.price_numeric <= rule["budget_upper"]:
            labels.append("budget")
        elif row.price_numeric <= rule["mid_range_upper"]:
            labels.append("mid_range")
        else:
            labels.append("premium")
    return pd.Series(labels, index=frame.index, dtype="string")


def model_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only permitted non-price predictors."""
    permitted = ["model_text"] + CATEGORICAL_COLUMNS + NUMERIC_COLUMNS
    missing = [column for column in permitted if column not in frame]
    if missing:
        raise ValueError(f"Missing prepared predictors: {missing}")
    return frame[permitted].copy()


def make_pipeline(classifier, max_text_features: int = 20_000) -> Pipeline:
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="constant", fill_value="__MISSING__")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=2)),
    ])
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler(with_mean=False)),
    ])
    preprocess = ColumnTransformer([
        ("text", TfidfVectorizer(
            ngram_range=(1, 2), min_df=2, max_df=0.98,
            max_features=max_text_features, sublinear_tf=True,
        ), "model_text"),
        ("categorical", categorical, CATEGORICAL_COLUMNS),
        ("numeric", numeric, NUMERIC_COLUMNS),
    ])
    return Pipeline([("preprocess", preprocess), ("classifier", classifier)])


def candidate_models() -> dict[str, Pipeline]:
    return {
        "dummy_baseline": make_pipeline(DummyClassifier(strategy="most_frequent")),
        "logistic_regression": make_pipeline(LogisticRegression(
            C=1.0, class_weight="balanced", max_iter=3000, random_state=RANDOM_STATE,
        )),
        "linear_svm": make_pipeline(LinearSVC(
            C=1.0, class_weight="balanced", max_iter=20_000,
            random_state=RANDOM_STATE,
        )),
        "sgd_logistic": make_pipeline(SGDClassifier(
            loss="log_loss", alpha=1e-4, class_weight="balanced",
            max_iter=3000, random_state=RANDOM_STATE,
        )),
    }


def compare_models(X: pd.DataFrame, y: pd.Series, folds: int = 5) -> pd.DataFrame:
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=RANDOM_STATE)
    scoring = {"macro_f1": "f1_macro", "accuracy": "accuracy", "balanced_accuracy": "balanced_accuracy"}
    rows = []
    for name, pipeline in candidate_models().items():
        scores = cross_validate(pipeline, X, y, cv=cv, scoring=scoring, n_jobs=-1)
        rows.append({
            "model": name,
            "cv_macro_f1_mean": scores["test_macro_f1"].mean(),
            "cv_macro_f1_std": scores["test_macro_f1"].std(),
            "cv_accuracy_mean": scores["test_accuracy"].mean(),
            "cv_balanced_accuracy_mean": scores["test_balanced_accuracy"].mean(),
        })
    return pd.DataFrame(rows).sort_values("cv_macro_f1_mean", ascending=False).reset_index(drop=True)


def tune_linear_candidates(X: pd.DataFrame, y: pd.Series, folds: int = 5) -> dict[str, GridSearchCV]:
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=RANDOM_STATE)
    searches = {
        "logistic_regression": GridSearchCV(
            make_pipeline(LogisticRegression(
                class_weight="balanced", max_iter=3000, random_state=RANDOM_STATE,
            )),
            {"classifier__C": [0.25, 0.5, 1.0, 2.0, 4.0]},
            scoring="f1_macro", cv=cv, n_jobs=-1, refit=True,
        ),
        "linear_svm": GridSearchCV(
            make_pipeline(LinearSVC(
                class_weight="balanced", max_iter=20_000,
                random_state=RANDOM_STATE,
            )),
            {"classifier__C": [0.1, 0.25, 0.5, 1.0, 2.0]},
            scoring="f1_macro", cv=cv, n_jobs=-1, refit=True,
        ),
    }
    for search in searches.values():
        search.fit(X, y)
    return searches


def choose_search(searches: dict[str, GridSearchCV]) -> tuple[str, GridSearchCV]:
    name = max(searches, key=lambda key: searches[key].best_score_)
    return name, searches[name]


def evaluate(model: Pipeline, X_test: pd.DataFrame, y_test: pd.Series) -> tuple[dict, pd.DataFrame, np.ndarray]:
    prediction = model.predict(X_test)
    order = {name: number for number, name in enumerate(TIER_ORDER)}
    true_number = pd.Series(y_test).map(order).to_numpy()
    pred_number = pd.Series(prediction).map(order).to_numpy()
    metrics = {
        "accuracy": float(accuracy_score(y_test, prediction)),
        "macro_f1": float(f1_score(y_test, prediction, average="macro")),
        "mean_absolute_tier_error": float(np.abs(true_number - pred_number).mean()),
        "severe_error_rate": float((np.abs(true_number - pred_number) == 2).mean()),
    }
    report = pd.DataFrame(classification_report(
        y_test, prediction, labels=TIER_ORDER, output_dict=True, zero_division=0,
    )).T
    matrix = confusion_matrix(y_test, prediction, labels=TIER_ORDER)
    return metrics, report, matrix


def top_linear_features(model: Pipeline, top_n: int = 15) -> pd.DataFrame:
    classifier = model.named_steps["classifier"]
    if not hasattr(classifier, "coef_"):
        return pd.DataFrame()
    names = model.named_steps["preprocess"].get_feature_names_out()
    rows = []
    for class_name, coefficients in zip(classifier.classes_, classifier.coef_):
        for index in np.argsort(coefficients)[-top_n:][::-1]:
            rows.append({"tier": class_name, "feature": names[index], "coefficient": coefficients[index]})
    return pd.DataFrame(rows)


def save_artifacts(
    output_dir: str | Path,
    model: Pipeline,
    rules: pd.DataFrame,
    predictions: pd.DataFrame,
    metrics: dict,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_dir / "price_tier_model.joblib")
    rules.to_csv(output_dir / "price_tier_rules.csv", index=False)
    predictions.to_csv(output_dir / "price_tier_predictions.csv", index=False)
    (output_dir / "price_tier_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )


def refit_for_deployment(best_estimator: Pipeline, prepared: pd.DataFrame) -> tuple[Pipeline, pd.DataFrame, pd.DataFrame]:
    """Relearn label rules on all priced rows, refit, then predict every product."""
    labelled = prepared[prepared["label_eligible"]].copy()
    rules = fit_tier_rules(labelled)
    labelled["price_tier"] = apply_tier_rules(labelled, rules)
    final_model = clone(best_estimator).fit(model_frame(labelled), labelled["price_tier"])
    predicted = prepared[["asin", "title", "search_keyword"]].copy()
    predicted["observed_price"] = prepared["price_numeric"]
    predicted["observed_currency"] = prepared["label_currency"]
    predicted["price_was_missing"] = prepared["price_numeric"].isna()
    predicted["predicted_price_tier"] = final_model.predict(model_frame(prepared))
    predicted["known_price_tier"] = apply_tier_rules(prepared, rules)
    return final_model, rules, predicted
