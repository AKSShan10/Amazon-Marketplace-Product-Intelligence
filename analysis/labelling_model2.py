"""
analysis/labelling.py
=====================

Gemini-based sentiment labelling pipeline for Amazon reviews.

Models
------
Model 1:
    gemini-3.6-flash

Model 2:
    gemini-3.5-flash-lite

Workflow
--------
1. gemini-3.6-flash labels the main review dataset.
2. gemini-3.5-flash-lite independently labels a validation sample.
3. Model agreement is measured.
4. Failed/rate-limited requests are retried safely.
5. Progress is checkpointed so long runs can resume.

Required environment variable
-----------------------------
GEMINI_API_KEY=your_api_key_here

Required packages
-----------------
pip install google-genai python-dotenv pandas tqdm pyarrow
"""

import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from tqdm.auto import tqdm

from google import genai
from google.genai import errors


load_dotenv()


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CLEAN_DIR = DATA_DIR / "clean"

LABELS_FILE = CLEAN_DIR / "llm_labels.parquet"
GOLD_FILE = CLEAN_DIR / "gold_labels.parquet"
PROGRESS_FILE = CLEAN_DIR / "labelling_progress.json"


VALID_SENTIMENTS = {
    "positive",
    "neutral",
    "negative",
}

MODEL_2_PREFERRED = "gemini-3.5-flash-lite"
MODEL_2_FALLBACKS = ("gemini-3.1-flash-lite", "gemini-2.5-flash-lite")


def resolve_model_name(preferred=MODEL_2_PREFERRED, fallbacks=MODEL_2_FALLBACKS):
    """Return the first text-generation model visible to the current API key."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set.")
    client = genai.Client(api_key=api_key)
    candidates = [preferred, *fallbacks]
    try:
        available = {
            str(model.name).removeprefix("models/")
            for model in client.models.list()
            if getattr(model, "name", None)
        }
    except Exception as exc:
        print(f"Could not list models ({type(exc).__name__}: {exc}). Trying {preferred} directly.")
        return preferred
    for candidate in candidates:
        if candidate in available:
            if candidate != preferred:
                print(f"{preferred} is unavailable to this key; using {candidate} for Model 2.")
            return candidate
    raise RuntimeError(
        "None of the Model 2 candidates is available to this API key: "
        + ", ".join(candidates)
    )


SINGLE_PROMPT = """
You are an Amazon product review sentiment classifier.

Classify the following review into exactly ONE category:

POSITIVE
NEUTRAL
NEGATIVE

Definitions:

POSITIVE:
The reviewer is satisfied, recommends the product,
or expresses an overall positive opinion.

NEGATIVE:
The reviewer is dissatisfied, reports important problems,
warns others, or expresses an overall negative opinion.

NEUTRAL:
The review is factual, balanced, mixed, or expresses
no clearly positive or negative overall opinion.

Important rules:

- Consider negation carefully.
- "Not bad" is not automatically negative.
- Consider sarcasm.
- Consider the overall product opinion.
- Shipping complaints should not outweigh a clearly
  expressed opinion about the actual product.
- For mixed reviews, determine the reviewer's overall conclusion.

Return ONLY ONE WORD:

POSITIVE
NEUTRAL
NEGATIVE

Do not provide explanations.

Review:
"{body}"
""".strip()


BATCH_PROMPT = """
You are an Amazon product review sentiment classifier.

Classify every review below as exactly one of:

POSITIVE
NEUTRAL
NEGATIVE

Definitions:

POSITIVE:
The reviewer is satisfied, recommends the product,
or expresses an overall positive opinion.

NEGATIVE:
The reviewer is dissatisfied, reports important problems,
warns others, or expresses an overall negative opinion.

NEUTRAL:
The review is factual, balanced, mixed,
or contains no clear overall sentiment.

Important rules:

- Consider negation.
- Consider sarcasm.
- Evaluate the overall opinion.
- Focus primarily on the product.
- Mixed reviews may be neutral if neither side dominates.

Return ONLY a valid JSON array.

Do NOT use Markdown.
Do NOT use code fences.
Do NOT provide explanations.

Required format:

[
    {{"index": 0, "sentiment": "POSITIVE"}},
    {{"index": 1, "sentiment": "NEGATIVE"}}
]

Reviews:

{reviews_block}
""".strip()


TRANSLATE_PROMPT = """
Translate the following Amazon product review into English.

If it is already English, return it unchanged.

Preserve:

- sentiment
- negation
- sarcasm
- meaning
- product complaints or praise

Return ONLY the translated review.

Do not provide explanations.
Do not add quotation marks.

Review:
"{body}"
""".strip()


def _parse_single(response):
    """Parse a single Gemini sentiment response."""
    if not response:
        return None

    text = str(response).strip().lower()
    text = text.replace("**", "").replace("`", "").strip().rstrip(".")

    if text in VALID_SENTIMENTS:
        return text

    match = re.search(r"\b(positive|neutral|negative)\b", text)
    if match:
        return match.group(1)

    print(f"    WARNING: Could not parse sentiment response: {response!r}")
    return None


def _parse_batch(response, expected_count):
    """Parse Gemini's batch JSON response."""
    if not response:
        return [None] * expected_count

    text = str(response).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            print("    WARNING: Gemini batch response did not contain a JSON array.")
            return [None] * expected_count

        try:
            parsed = json.loads(match.group())
        except (ValueError, TypeError):
            print("    WARNING: Gemini returned invalid JSON.")
            return [None] * expected_count

    if not isinstance(parsed, list):
        print("    WARNING: Gemini batch response was not a JSON list.")
        return [None] * expected_count

    results = [None] * expected_count

    for item in parsed:
        if not isinstance(item, dict):
            continue

        idx = item.get("index")
        sentiment = str(item.get("sentiment", "")).strip().lower()

        if (
            isinstance(idx, int)
            and 0 <= idx < expected_count
            and sentiment in VALID_SENTIMENTS
        ):
            results[idx] = sentiment

    return results


class GeminiLabeller:
    """Wrapper around Google's Gemini API for sentiment labelling."""

    def __init__(
        self,
        model_name="gemini-3.6-flash",
        batch_size=10,
        request_delay=6.5,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self.request_delay = request_delay

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError(
                "\nGEMINI_API_KEY not found.\n\n"
                "For Google Colab, run:\n\n"
                "import os\n"
                "from getpass import getpass\n"
                "os.environ['GEMINI_API_KEY'] = getpass('Gemini API Key: ')\n\n"
                "For local execution, create .env:\n\n"
                "GEMINI_API_KEY=your_key_here"
            )

        self.client = genai.Client(api_key=api_key)
        self._request_count = 0

    def _call(self, prompt, max_retries=3, config=None):
        """
        Send one request to Gemini.

        Retries 429 and temporary 5xx errors.
        Stops immediately for permanent 4xx errors.
        """
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )

                self._request_count += 1

                if response is None:
                    print("    Gemini returned an empty response.")
                    return None

                response_text = getattr(response, "text", None)

                if not response_text:
                    print("    Gemini response contained no text.")
                    return None

                if self.request_delay > 0:
                    time.sleep(self.request_delay)

                return response_text.strip()

            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                message = getattr(exc, "message", None) or str(exc)

                print(
                    f"\n    Gemini API error"
                    f"\n    Model   : {self.model_name}"
                    f"\n    Attempt : {attempt}/{max_retries}"
                    f"\n    Code    : {code}"
                    f"\n    Message : {message}"
                )

                if code == 429:
                    if attempt < max_retries:
                        wait = 60 * attempt
                        print(f"    Rate limit reached. Retrying after {wait} seconds...")
                        time.sleep(wait)
                        continue

                    print("    Rate limit still active after all retries.")
                    return None

                if code in {500, 502, 503, 504}:
                    if attempt < max_retries:
                        wait = 20 * attempt
                        print(
                            f"    Temporary Gemini server error. "
                            f"Retrying after {wait} seconds..."
                        )
                        time.sleep(wait)
                        continue
                    return None

                if code == 400:
                    print(
                        "\n    400 = Bad request."
                        "\n    Check the request/model parameters."
                    )
                elif code == 401:
                    print(
                        "\n    401 = Authentication failed."
                        "\n    Check GEMINI_API_KEY."
                    )
                elif code == 403:
                    print(
                        "\n    403 = Permission denied."
                        "\n    The API key/project may not have access to this model or API."
                    )
                elif code == 404:
                    print(
                        "\n    404 = Model/resource not found."
                        "\n    Check the Gemini model name."
                    )

                return None

            except Exception as exc:
                print(
                    f"\n    Unexpected Gemini error"
                    f"\n    Model   : {self.model_name}"
                    f"\n    Attempt : {attempt}/{max_retries}"
                    f"\n    Type    : {type(exc).__name__}"
                    f"\n    Details : {exc}"
                )

                if attempt < max_retries:
                    wait = 10 * attempt
                    print(f"    Retrying after {wait} seconds...")
                    time.sleep(wait)
                    continue

                print(f"\n    Failed after {max_retries} attempts.")
                return None

        return None

    def test_connection(self):
        """Make one small Gemini test request."""
        print(
            f"\nTesting Gemini API:"
            f"\n  Model: {self.model_name}"
        )

        response = self._call(
            "Reply with exactly the word POSITIVE and nothing else.",
            max_retries=1,
        )

        if not response:
            print("\nGemini connection test FAILED.")
            return False

        print("\nGemini connection test successful.")
        print(f"Response: {response}")
        return True

    def label_single(self, body):
        """Label one review as positive, neutral, or negative."""
        if body is None or not str(body).strip():
            return None

        prompt = SINGLE_PROMPT.format(body=str(body)[:2000])
        response = self._call(prompt)
        return _parse_single(response)

    def translate(self, body):
        """Translate a review to English; fail-open on API failure."""
        if body is None or not str(body).strip():
            return body

        prompt = TRANSLATE_PROMPT.format(body=str(body)[:2000])
        response = self._call(prompt)

        return response.strip() if response else str(body)

    def label_batch(self, bodies):
        """Label multiple reviews using one Gemini request."""
        if bodies is None:
            return []

        bodies = list(bodies)

        if len(bodies) == 0:
            return []

        lines = []

        for i, body in enumerate(bodies):
            text = str(body)[:500] if body else "(empty)"
            text = text.replace('"', "'").replace("\n", " ")
            lines.append(f'[{i}] "{text}"')

        reviews_block = "\n".join(lines)
        prompt = BATCH_PROMPT.format(reviews_block=reviews_block)
        schema = {
            "type": "ARRAY",
            "minItems": len(bodies),
            "maxItems": len(bodies),
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "sentiment": {
                        "type": "STRING",
                        "enum": ["POSITIVE", "NEUTRAL", "NEGATIVE"],
                    },
                },
                "required": ["index", "sentiment"],
            },
        }
        response = self._call(
            prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": schema,
            },
        )

        return _parse_batch(response, len(bodies))


def label_all_reviews(
    reviews_df,
    model="gemini-3.6-flash",
    batch_size=10,
    save=True,
    resume=True,
):
    """Label every review using Gemini with checkpointing."""
    if "body_clean" not in reviews_df.columns:
        raise ValueError("reviews_df must contain a 'body_clean' column.")

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    df = reviews_df.copy()

    if (
        resume
        and "llm_sentiment" not in df.columns
        and LABELS_FILE.exists()
    ):
        try:
            previous = pd.read_parquet(LABELS_FILE)

            if (
                "llm_sentiment" in previous.columns
                and len(previous) == len(df)
            ):
                df["llm_sentiment"] = previous["llm_sentiment"].values
                already = df["llm_sentiment"].notna().sum()
                print(f"Resumed {already} labels from previous run.")
            else:
                print(
                    "Previous checkpoint does not match this DataFrame. "
                    "Starting fresh."
                )

        except Exception as exc:
            print(f"Could not load previous checkpoint: {exc}")

    if "llm_sentiment" not in df.columns:
        df["llm_sentiment"] = None

    needs_label = (
        df["llm_sentiment"].isna()
        & df["body_clean"].notna()
    )

    remaining = int(needs_label.sum())

    if remaining == 0:
        print(f"All {len(df)} reviews already labelled.")

        if save:
            df.to_parquet(LABELS_FILE, index=False)

        return df

    total = int(df["body_clean"].notna().sum())
    done = total - remaining

    print(f"\nLabelling with {model}")
    print(f"  Total reviews : {total}")
    print(f"  Already done  : {done}")
    print(f"  Remaining     : {remaining}")
    print(f"  Batch size    : {batch_size}")

    labeller = GeminiLabeller(
        model_name=model,
        batch_size=batch_size,
    )

    indices = df.index[needs_label].tolist()

    batches = [
        indices[i:i + batch_size]
        for i in range(0, len(indices), batch_size)
    ]

    labelled_count = done
    failed_count = 0

    for batch_num, batch_indices in enumerate(
        tqdm(batches, desc="Labelling"),
        start=1,
    ):
        bodies = df.loc[batch_indices, "body_clean"].tolist()
        sentiments = labeller.label_batch(bodies)

        for idx, sentiment in zip(batch_indices, sentiments):
            if sentiment:
                df.at[idx, "llm_sentiment"] = sentiment
                labelled_count += 1
            else:
                failed_count += 1

        if save:
            df.to_parquet(LABELS_FILE, index=False)

        if batch_num % 10 == 0 or batch_num == len(batches):
            print(
                f"\nBatch {batch_num}/{len(batches)}"
                f"\n  labelled : {labelled_count}"
                f"\n  failed   : {failed_count}"
                f"\n  checkpoint: {LABELS_FILE}"
            )

    if save:
        df.to_parquet(LABELS_FILE, index=False)

    print(
        f"\nDone."
        f"\n  Labelled: {labelled_count}"
        f"\n  Failed  : {failed_count}"
    )

    if save:
        print(f"  Saved   : {LABELS_FILE}")

    return df


def create_gold_set(
    reviews_df,
    model1="gemini-3.6-flash",
    model2="gemini-3.5-flash-lite",
    n_agree=250,
    n_disagree=50,
    save=True,
):
    """Build an agreement/validation set using two Gemini models."""
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    if "llm_sentiment" not in reviews_df.columns:
        raise ValueError(
            "Run label_all_reviews() first. "
            "The DataFrame must contain 'llm_sentiment'."
        )

    labelled = reviews_df[
        reviews_df["llm_sentiment"].isin(VALID_SENTIMENTS)
    ].copy()

    if labelled.empty:
        raise ValueError("No successfully labelled reviews were found.")

    if len(labelled) < (n_agree + n_disagree):
        print(
            f"Warning: only {len(labelled)} labelled reviews are available."
        )
        n_agree = int(len(labelled) * 0.8)
        n_disagree = len(labelled) - n_agree

    sample_size = min(
        n_agree + n_disagree + 100,
        len(labelled),
    )

    sample_parts = []
    labels_present = (
        labelled["llm_sentiment"]
        .dropna()
        .unique()
        .tolist()
    )

    per_class = max(
        1,
        sample_size // max(len(labels_present), 1)
    )

    for sentiment in labels_present:
        group = labelled[
            labelled["llm_sentiment"] == sentiment
        ]

        take = min(len(group), per_class)

        if take > 0:
            sample_parts.append(
                group.sample(
                    n=take,
                    random_state=42
                )
            )

    sample = pd.concat(sample_parts)

    if len(sample) < sample_size:
        remaining_pool = labelled.drop(
            index=sample.index,
            errors="ignore"
        )

        extra_n = min(
            sample_size - len(sample),
            len(remaining_pool)
        )

        if extra_n > 0:
            extra = remaining_pool.sample(
                n=extra_n,
                random_state=42
            )
            sample = pd.concat([sample, extra])

    sample = sample.head(sample_size).copy()

    print(
        f"\nBuilding gold/agreement set:"
        f"\n  Reviews : {len(sample)}"
        f"\n  Model 1 : {model1}"
        f"\n  Model 2 : {model2}"
    )

    labeller2 = GeminiLabeller(
        model_name=model2,
        batch_size=10
    )

    model2_labels = []
    indices = sample.index.tolist()

    batches = [
        indices[i:i + 10]
        for i in range(0, len(indices), 10)
    ]

    for batch_indices in tqdm(
        batches,
        desc="Gold-set labelling"
    ):
        bodies = sample.loc[
            batch_indices,
            "body_clean"
        ].tolist()

        sentiments = labeller2.label_batch(bodies)
        model2_labels.extend(sentiments)

    sample["model2_sentiment"] = model2_labels
    sample["model1_sentiment"] = sample["llm_sentiment"]

    sample["agreement"] = (
        sample["model1_sentiment"]
        == sample["model2_sentiment"]
    )

    agree = sample[sample["agreement"]]

    disagree = sample[
        ~sample["agreement"]
    ].dropna(
        subset=["model2_sentiment"]
    )

    gold_parts = []

    if len(agree) > 0:
        gold_parts.append(
            agree.sample(
                n=min(n_agree, len(agree)),
                random_state=42
            )
        )

    if len(disagree) > 0:
        gold_parts.append(
            disagree.sample(
                n=min(n_disagree, len(disagree)),
                random_state=42
            )
        )

    if not gold_parts:
        raise RuntimeError(
            "Could not create the gold set because "
            "Model 2 produced no usable predictions."
        )

    gold = pd.concat(
        gold_parts,
        ignore_index=True
    )

    gold["gold_sentiment"] = gold["model1_sentiment"]

    if save:
        gold.to_parquet(
            GOLD_FILE,
            index=False
        )

    total = len(gold)
    agreed = int(gold["agreement"].sum())

    print(
        f"\nGold/agreement set:"
        f"\n  Total              : {total}"
        f"\n  Models agree       : {agreed} ({agreed / total:.1%})"
        f"\n  Models disagree    : {total - agreed}"
    )

    if "star_sentiment" in gold.columns:
        star_vs_llm = (
            gold["gold_sentiment"]
            == gold["star_sentiment"]
        ).mean()

        print(
            f"  Stars vs LLM       : "
            f"{star_vs_llm:.1%}"
        )

    print("\nGold label distribution:")
    print(
        gold["gold_sentiment"]
        .value_counts()
        .to_string()
    )

    if save:
        print(f"\nSaved to:\n{GOLD_FILE}")

    return gold


def labelling_status(reviews_df):
    """Display current sentiment-labelling status."""
    total = len(reviews_df)

    if "body_clean" in reviews_df.columns:
        has_body = int(
            reviews_df["body_clean"]
            .notna()
            .sum()
        )
    else:
        has_body = 0

    if "llm_sentiment" in reviews_df.columns:
        labelled = int(
            reviews_df["llm_sentiment"]
            .isin(VALID_SENTIMENTS)
            .sum()
        )
    else:
        labelled = 0

    if "star_sentiment" in reviews_df.columns:
        has_star = int(
            reviews_df["star_sentiment"]
            .notna()
            .sum()
        )
    else:
        has_star = 0

    print("\nLabelling status:")
    print(f"  Reviews total  : {total}")
    print(f"  With body text : {has_body}")
    print(f"  LLM-labelled   : {labelled}")
    print(f"  Star-labelled  : {has_star}")

    if (
        labelled > 0
        and "llm_sentiment" in reviews_df.columns
    ):
        print("\n  LLM sentiment distribution:")
        print(
            reviews_df["llm_sentiment"]
            .value_counts()
            .to_string()
        )

    print()
