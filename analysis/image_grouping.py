"""Unsupervised thumbnail grouping for Feature C.

Search/category labels are never used for features, tuning, or final selection.
They are used only by validate_with_categories after the grouping is fixed.
"""

from __future__ import annotations

from collections import Counter
import hashlib
from itertools import combinations
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageFile
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    normalized_mutual_info_score,
    silhouette_score,
)
from sklearn.preprocessing import normalize

ImageFile.LOAD_TRUNCATED_IMAGES = True
ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = ROOT / "data" / "images"
PRODUCTS_CSV = ROOT / "data" / "products.csv"
MODEL_DIR = ROOT / "data" / "models" / "ThumbnailGrouping"
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def build_image_index(products_csv=PRODUCTS_CSV, image_dir=IMAGE_DIR):
    """Audit images and join filenames to products by ASIN."""
    products = pd.read_csv(
        products_csv,
        usecols=lambda c: c in {"asin", "title", "brand", "search_keyword", "image_url"},
    )
    products["asin"] = products["asin"].astype(str)
    lookup = products.drop_duplicates("asin").set_index("asin")
    records, failures = [], []

    for path in sorted(Path(image_dir).iterdir()):
        if path.suffix.lower() not in VALID_EXTENSIONS:
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height, image_format = image.width, image.height, image.format
            records.append({
                "asin": path.stem,
                "image_path": str(path.resolve()),
                "filename": path.name,
                "bytes": path.stat().st_size,
                "width": width,
                "height": height,
                "aspect_ratio": width / height,
                "image_format": image_format,
            })
        except Exception as exc:
            failures.append({"filename": path.name, "error": f"{type(exc).__name__}: {exc}"})

    index = pd.DataFrame(records).join(lookup, on="asin", how="left")
    index = index.sort_values("asin").reset_index(drop=True)
    failures = pd.DataFrame(failures)
    if index["asin"].duplicated().any():
        raise ValueError("Multiple images map to the same ASIN")
    if index["title"].isna().any():
        raise ValueError("At least one image has no matching product")
    return index, failures


def audit_image_quality(index, near_duplicate_distance=3):
    """Measure visual quality and find exact/near duplicates without metadata labels."""
    rows = []
    hashes = []
    for path in index["image_path"]:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            small = np.asarray(rgb.resize((64, 64)), dtype=np.float32) / 255.0
            gray = np.asarray(rgb.convert("L").resize((9, 8)), dtype=np.int16)
            difference_hash = (gray[:, 1:] > gray[:, :-1]).ravel()
            pixel_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
        brightness = float(small.mean())
        contrast = float(small.std())
        white_ratio = float((small.min(axis=2) > 0.92).mean())
        dark_ratio = float((small.max(axis=2) < 0.08).mean())
        mean_rgb = small.mean(axis=(0, 1))
        rows.append({
            "pixel_hash": pixel_hash,
            "brightness": brightness,
            "contrast": contrast,
            "white_background_ratio": white_ratio,
            "dark_pixel_ratio": dark_ratio,
            "mean_red": float(mean_rgb[0]),
            "mean_green": float(mean_rgb[1]),
            "mean_blue": float(mean_rgb[2]),
            "low_information": bool(contrast < 0.035 or white_ratio > 0.985 or dark_ratio > 0.985),
        })
        hashes.append(difference_hash)

    quality = pd.concat([index.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    exact = (
        quality.groupby("pixel_hash").filter(lambda group: len(group) > 1)
        [["asin", "filename", "pixel_hash"]].sort_values("pixel_hash")
    )
    near_rows = []
    asins = quality["asin"].astype(str).to_numpy()
    pixel_hashes = quality["pixel_hash"].to_numpy()
    for left, right in combinations(range(len(hashes)), 2):
        distance = int(np.count_nonzero(hashes[left] != hashes[right]))
        if distance <= near_duplicate_distance and pixel_hashes[left] != pixel_hashes[right]:
            near_rows.append({
                "asin_left": asins[left],
                "asin_right": asins[right],
                "difference_hash_distance": distance,
            })
    return quality, exact.reset_index(drop=True), pd.DataFrame(near_rows)


def quality_outliers(quality, limit=24):
    """Return the most unusual images for manual visual inspection."""
    z_columns = ["bytes", "width", "height", "aspect_ratio", "brightness", "contrast", "white_background_ratio"]
    score = np.zeros(len(quality), dtype=float)
    for column in z_columns:
        values = pd.to_numeric(quality[column], errors="coerce")
        median = values.median()
        mad = np.median(np.abs(values - median))
        if mad > 0:
            score += np.abs(values - median) / (1.4826 * mad)
    result = quality.copy()
    result["quality_outlier_score"] = score
    return result.sort_values("quality_outlier_score", ascending=False).head(limit)


def extract_color_features(index, bins=16, size=(128, 128)):
    """RGB+HSV histogram baseline using image pixels only."""
    features = []
    for path in index["image_path"]:
        with Image.open(path) as image:
            rgb = image.convert("RGB").resize(size)
            hsv = rgb.convert("HSV")
            vector = []
            for array in (np.asarray(rgb), np.asarray(hsv)):
                for channel in range(3):
                    hist, _ = np.histogram(
                        array[:, :, channel], bins=bins, range=(0, 256), density=True
                    )
                    vector.extend(hist)
            features.append(vector)
    return normalize(np.asarray(features, dtype=np.float32))


def extract_cnn_embeddings(index, backbone="resnet18", batch_size=32, device=None):
    """Extract pretrained visual embeddings without product metadata."""
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, Dataset
    from torchvision.models import (
        EfficientNet_B0_Weights,
        ResNet18_Weights,
        efficientnet_b0,
        resnet18,
    )

    torch.manual_seed(42)
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    if backbone == "resnet18":
        weights, model = ResNet18_Weights.DEFAULT, resnet18(weights=ResNet18_Weights.DEFAULT)
        model.fc = nn.Identity()
    elif backbone == "efficientnet_b0":
        weights = EfficientNet_B0_Weights.DEFAULT
        model = efficientnet_b0(weights=weights)
        model.classifier = nn.Identity()
    else:
        raise ValueError("Use resnet18 or efficientnet_b0")

    transform = weights.transforms()

    class Dataset(Dataset):
        def __len__(self):
            return len(index)

        def __getitem__(self, item):
            with Image.open(index.iloc[item]["image_path"]) as image:
                return transform(image.convert("RGB"))

    loader = DataLoader(Dataset(), batch_size=batch_size, shuffle=False, num_workers=0)
    model = model.to(device).eval()
    batches = []
    with torch.inference_mode():
        for images in loader:
            batches.append(model(images.to(device)).cpu().numpy())
    return normalize(np.vstack(batches).astype(np.float32)), {
        "backbone": backbone,
        "device": device,
    }


def reduce_features(features, max_components=128, variance=0.95):
    """PCA denoising fitted without labels."""
    limit = min(max_components, features.shape[0] - 1, features.shape[1])
    pca = PCA(n_components=limit, random_state=42)
    transformed = pca.fit_transform(features)
    keep = min(
        np.searchsorted(np.cumsum(pca.explained_variance_ratio_), variance) + 1,
        transformed.shape[1],
    )
    return normalize(transformed[:, :keep]), pca, keep


def _metrics(features, labels):
    sizes = Counter(labels)
    return {
        "silhouette": silhouette_score(features, labels),
        "calinski_harabasz": calinski_harabasz_score(features, labels),
        "davies_bouldin": davies_bouldin_score(features, labels),
        "smallest_group": min(sizes.values()),
        "largest_group_share": max(sizes.values()) / len(labels),
    }


def compare_clusterings(color_features, cnn_features, combined_features=None, k_values=range(2, 31), linkages=("ward", "complete", "average")):
    """Compare multiple representations, algorithms, linkages, and cluster counts."""
    combined_features = combined_features if combined_features is not None else normalize(
        np.hstack([color_features, cnn_features])
    )
    rows, fitted = [], {}
    for k in k_values:
        candidates = {
            ("color_kmeans", k): (KMeans(n_clusters=k, n_init=30, random_state=42).fit(color_features), color_features),
            ("cnn_kmeans", k): (KMeans(n_clusters=k, n_init=30, random_state=42).fit(cnn_features), cnn_features),
            ("combined_kmeans", k): (KMeans(n_clusters=k, n_init=30, random_state=42).fit(combined_features), combined_features),
        }
        for linkage in linkages:
            name = f"cnn_agglomerative_{linkage}"
            candidates[(name, k)] = (
                AgglomerativeClustering(n_clusters=k, linkage=linkage).fit(cnn_features),
                cnn_features,
            )
        for (name, cluster_count), (model, features) in candidates.items():
            labels = np.asarray(model.labels_)
            rows.append({
                "approach": name, "k": cluster_count,
                "stability_mean_ari": np.nan, "stability_min_ari": np.nan,
                **_metrics(features, labels),
            })
            fitted[(name, cluster_count)] = {
                "model": model,
                "labels": labels,
                "features": features,
            }
    return pd.DataFrame(rows), fitted


def add_stability_scores(results, fitted, top_n=18, runs=5, noise_scale=0.01):
    """Perturb features and measure ARI agreement for the strongest candidates.

    Tests the best candidate per K value (not just overall top-N by silhouette)
    so that high-K solutions get stability scores too.
    """
    updated = results.copy()
    size_ok = updated[(updated["smallest_group"] >= 3) & (updated["largest_group_share"] <= 0.50)]
    if size_ok.empty:
        size_ok = updated

    # Select best candidate PER K value, then take top_n overall.
    # This ensures high-K solutions are tested, not just low-K ones.
    per_k_best = size_ok.sort_values("silhouette", ascending=False).drop_duplicates("k", keep="first")
    candidate_rows = per_k_best.sort_values("silhouette", ascending=False).head(top_n)

    for row_index, row in candidate_rows.iterrows():
        key = (row["approach"], int(row["k"]))
        baseline = fitted[key]["labels"]
        features = fitted[key]["features"]
        agreements = []
        scale = np.maximum(features.std(axis=0), 1e-8)
        for run in range(runs):
            rng = np.random.default_rng(1000 + run)
            perturbed = normalize(features + rng.normal(0, noise_scale * scale, features.shape))
            if "kmeans" in row["approach"]:
                model = KMeans(n_clusters=int(row["k"]), n_init=20, random_state=1000 + run).fit(perturbed)
            else:
                linkage = str(row["approach"]).rsplit("_", 1)[-1]
                model = AgglomerativeClustering(n_clusters=int(row["k"]), linkage=linkage).fit(perturbed)
            agreements.append(adjusted_rand_score(baseline, model.labels_))
        updated.loc[row_index, "stability_mean_ari"] = float(np.mean(agreements))
        updated.loc[row_index, "stability_min_ari"] = float(np.min(agreements))
    return updated


def select_best_clustering(results, fitted, min_group_size=5, max_group_share=0.40, min_stability=0.50, min_k=12):
    """Select best clustering with a minimum K floor.

    Internal metrics (silhouette, Davies-Bouldin, etc.) inherently favour
    fewer clusters.  For a catalogue with genuine visual diversity (28
    categories, 859 images), a minimum K prevents the algorithm from
    collapsing everything into 3-5 super-groups.  Within the eligible
    K range, the best silhouette is chosen.
    """
    # Apply minimum K
    pool = results[results["k"] >= min_k]
    if pool.empty:
        pool = results

    # Size constraints
    eligible = pool[
        (pool["smallest_group"] >= min_group_size)
        & (pool["largest_group_share"] <= max_group_share)
    ]
    if eligible.empty:
        eligible = pool

    # Prefer stable solutions if available
    stable = eligible[
        eligible["stability_mean_ari"].notna()
        & (eligible["stability_mean_ari"] >= min_stability)
    ]
    if not stable.empty:
        eligible = stable

    # Among eligible, pick best silhouette (now comparing within same K range)
    winner = eligible.sort_values(
        ["silhouette", "stability_mean_ari", "davies_bouldin"],
        ascending=[False, False, True],
    ).iloc[0]

    return winner, fitted[(winner["approach"], int(winner["k"]))]


def describe_groups(index, labels):
    """Create reproducible pixel-based descriptions; category labels are not used."""
    frame = index.copy()
    frame["visual_group"] = labels
    records = []
    for group, part in frame.groupby("visual_group"):
        brightness = part["brightness"].median()
        white = part["white_background_ratio"].median()
        aspect = part["aspect_ratio"].median()
        rgb = part[["mean_red", "mean_green", "mean_blue"]].median()
        tone = "bright" if brightness >= 0.78 else "dark" if brightness <= 0.60 else "mid-tone"
        background = "mostly white-background" if white >= 0.60 else "mixed/light-background" if white >= 0.35 else "non-white/dark-background"
        shape = "mostly tall" if aspect < 0.85 else "mostly wide" if aspect > 1.15 else "mostly square"
        spread = float(rgb.max() - rgb.min())
        colour = "neutral-colour" if spread < 0.035 else ("warm-colour" if rgb["mean_red"] == rgb.max() else "cool-colour")
        records.append({
            "visual_group": int(group), "group_size": int(len(part)),
            "median_brightness": float(brightness), "median_white_background_ratio": float(white),
            "median_aspect_ratio": float(aspect),
            "visual_description": f"{tone}, {background}, {shape}, {colour} thumbnails",
        })
    return pd.DataFrame(records).sort_values("visual_group").reset_index(drop=True)


def representative_indices(features, labels, samples_per_group=6):
    representatives = {}
    for group in sorted(np.unique(labels)):
        members = np.flatnonzero(labels == group)
        centroid = features[members].mean(axis=0)
        distances = np.linalg.norm(features[members] - centroid, axis=1)
        order = np.lexsort((members, distances))
        representatives[int(group)] = members[order[:samples_per_group]]
    return representatives


def plot_group_samples(index, features, labels, samples_per_group=6, output_path=None):
    """Plot representative images and group size for every group."""
    representatives = representative_indices(features, labels, samples_per_group)
    groups = sorted(representatives)
    fig, axes = plt.subplots(
        len(groups), samples_per_group,
        figsize=(2.4 * samples_per_group, 2.4 * len(groups)),
        squeeze=False,
    )
    for row, group in enumerate(groups):
        members = np.flatnonzero(labels == group)
        chosen = representatives[group]
        for column in range(samples_per_group):
            axis = axes[row, column]
            axis.axis("off")
            if column >= len(chosen):
                continue
            item = int(chosen[column])
            with Image.open(index.iloc[item]["image_path"]) as image:
                axis.imshow(image.convert("RGB"))
            axis.set_title(str(index.iloc[item]["asin"]), fontsize=8)
        axes[row, 0].set_ylabel(
            f"Group {group}\nn={len(members)}", rotation=0, labelpad=42, va="center"
        )
    fig.suptitle("Representative thumbnails from each visual group", fontsize=15)
    plt.tight_layout()
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=160, bbox_inches="tight")
    return fig


def validate_with_categories(index, labels):
    """Use site category labels only after final unsupervised selection."""
    categories = index["search_keyword"].fillna("unknown").astype(str)
    codes, _ = pd.factorize(categories, sort=True)
    table = pd.crosstab(pd.Series(labels, name="visual_group"), categories)
    return {
        "adjusted_rand_index": adjusted_rand_score(codes, labels),
        "normalized_mutual_info": normalized_mutual_info_score(codes, labels),
        "cluster_purity": table.max(axis=1).sum() / len(index),
        "cross_tab": table,
    }


def save_grouping(index, labels, features, winner, model, output_dir=MODEL_DIR):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assignments = index.copy()
    assignments["visual_group"] = labels
    assignments.to_csv(output_dir / "thumbnail_groups.csv", index=False)
    np.savez_compressed(
        output_dir / "thumbnail_features.npz",
        asins=index["asin"].to_numpy(),
        features=np.asarray(features, dtype=np.float32),
        labels=np.asarray(labels, dtype=np.int32),
    )
    joblib.dump(
        {
            "approach": winner["approach"],
            "k": int(winner["k"]),
            "metrics": winner.to_dict(),
            "model": model,
        },
        output_dir / "clustering_model.joblib",
    )
    return assignments
