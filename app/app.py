"""
Amazon Product & Review Intelligence — Streamlit Application
=============================================================
Part C of the project: a single application with four analytical features.

Run:  streamlit run app/app.py
"""

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from PIL import Image

# ---------------------------------------------------------------------------
# PATHS — all relative to project root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CLEAN_DIR = DATA_DIR / "clean"
IMAGES_DIR = DATA_DIR / "images"
MODELS_DIR = DATA_DIR / "models"

PRODUCTS_CSV = DATA_DIR / "products.csv"
PRODUCTS_PARQUET = CLEAN_DIR / "products_clean.parquet"
REVIEWS_PARQUET = CLEAN_DIR / "reviews_clean.parquet"

# Feature model directories
REC_DIR = MODELS_DIR / "SimilarProductRecommendation"
SENT_DIR = MODELS_DIR / "ReviewSentiment"
THUMB_DIR = MODELS_DIR / "ThumbnailGrouping"
PRICE_DIR = MODELS_DIR / "PriceTierClassification"

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Amazon Product Intelligence",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# DATA LOADING (cached)
# ---------------------------------------------------------------------------
@st.cache_data
def load_products():
    """Load the product catalogue."""
    if PRODUCTS_PARQUET.exists():
        df = pd.read_parquet(PRODUCTS_PARQUET)
    elif PRODUCTS_CSV.exists():
        df = pd.read_csv(PRODUCTS_CSV)
    else:
        st.error("No product data found. Run the data pipeline first.")
        st.stop()
    return df


@st.cache_data
def load_reviews():
    """Load cleaned reviews."""
    if REVIEWS_PARQUET.exists():
        return pd.read_parquet(REVIEWS_PARQUET)
    st.warning("No review data found.")
    return pd.DataFrame()


@st.cache_data
def load_sentiment_by_product():
    path = SENT_DIR / "sentiment_by_product.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()


@st.cache_data
def load_sentiment_by_category():
    path = SENT_DIR / "sentiment_by_category.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()


@st.cache_data
def load_reviews_with_sentiment():
    path = SENT_DIR / "reviews_with_sentiment.parquet"
    if path.exists():
        return pd.read_parquet(path)
    csv_path = SENT_DIR / "reviews_with_sentiment.csv"
    if csv_path.exists():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


@st.cache_data
def load_thumbnail_groups():
    path = THUMB_DIR / "thumbnail_groups.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()


@st.cache_data
def load_group_descriptions():
    path = THUMB_DIR / "visual_group_descriptions.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()


@st.cache_data
def load_price_predictions():
    path = PRICE_DIR / "price_tier_predictions.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()


@st.cache_data
def load_price_metrics():
    path = PRICE_DIR / "price_tier_metrics.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


# Feature A model loading
@st.cache_resource
def load_semantic_model():
    path = REC_DIR / "semantic_embeddings.pkl"
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


@st.cache_resource
def load_tfidf_model():
    path = REC_DIR / "tfidf_model.pkl"
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


@st.cache_resource
def load_bm25_model():
    path = REC_DIR / "bm25_model.pkl"
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


def get_product_image(asin):
    """Return a PIL Image for the given ASIN, or None."""
    for ext in (".jpg", ".png", ".jpeg"):
        img_path = IMAGES_DIR / f"{asin}{ext}"
        if img_path.exists():
            try:
                return Image.open(img_path)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# SIDEBAR NAVIGATION
# ---------------------------------------------------------------------------
st.sidebar.title("🛒 Product Intelligence")
page = st.sidebar.radio(
    "Navigate",
    [
        "📦 Product List",
        "🔍 A: Similar Products",
        "💬 B: Review Sentiment",
        "🖼️ C: Thumbnail Groups",
        "💰 D: Price Tiers",
    ],
)


# ===================================================================
# PAGE: PRODUCT LIST
# ===================================================================
def page_product_list():
    st.title("📦 Product Catalogue")
    products = load_products()

    # --- filters ---
    col1, col2 = st.columns(2)
    with col1:
        categories = ["All"] + sorted(products["search_keyword"].dropna().unique().tolist())
        selected_cat = st.selectbox("Category", categories)
    with col2:
        search_text = st.text_input("Search by title or ASIN")

    filtered = products.copy()
    if selected_cat != "All":
        filtered = filtered[filtered["search_keyword"] == selected_cat]
    if search_text:
        mask = (
            filtered["asin"].str.contains(search_text, case=False, na=False)
            | filtered["title"].str.contains(search_text, case=False, na=False)
        )
        filtered = filtered[mask]

    st.write(f"**{len(filtered)}** products found")

    # --- product grid ---
    cols_per_row = 4
    rows = [filtered.iloc[i : i + cols_per_row] for i in range(0, len(filtered), cols_per_row)]

    for row_df in rows[:25]:  # show up to 100 products
        cols = st.columns(cols_per_row)
        for idx, (_, product) in enumerate(row_df.iterrows()):
            with cols[idx]:
                img = get_product_image(product["asin"])
                if img:
                    st.image(img, use_container_width=True)
                else:
                    st.image("https://via.placeholder.com/200x200?text=No+Image", use_container_width=True)

                st.markdown(f"**{str(product.get('title', ''))[:60]}...**")
                st.caption(f"ASIN: {product['asin']}")

                price_val = product.get("price")
                if pd.notna(price_val):
                    st.write(f"💲 {price_val}")

                rating_val = product.get("rating")
                if pd.notna(rating_val):
                    st.write(f"⭐ {rating_val}")

                if st.button("View Details", key=f"detail_{product['asin']}"):
                    st.session_state["selected_asin"] = product["asin"]
                    st.session_state["show_detail"] = True
                    st.rerun()

    # --- product detail modal ---
    if st.session_state.get("show_detail"):
        show_product_detail(st.session_state["selected_asin"])


def show_product_detail(asin):
    """Display full details for a single product."""
    products = load_products()
    product = products[products["asin"] == asin]
    if product.empty:
        st.error(f"Product {asin} not found.")
        return

    product = product.iloc[0]

    st.markdown("---")
    st.subheader(f"📋 Product Details: {asin}")

    if st.button("← Back to list"):
        st.session_state["show_detail"] = False
        st.rerun()

    col1, col2 = st.columns([1, 2])

    with col1:
        img = get_product_image(asin)
        if img:
            st.image(img, use_container_width=True)

    with col2:
        st.markdown(f"### {product.get('title', 'N/A')}")
        st.write(f"**Brand:** {product.get('brand', 'N/A')}")
        st.write(f"**Category:** {product.get('search_keyword', 'N/A')}")

        price_val = product.get("price")
        if pd.notna(price_val):
            st.write(f"**Price:** ${price_val}")

        rating_val = product.get("rating")
        if pd.notna(rating_val):
            st.write(f"**Rating:** ⭐ {rating_val}")

        review_count = product.get("review_count")
        if pd.notna(review_count):
            st.write(f"**Reviews:** {int(review_count)}")

        # Price tier
        price_preds = load_price_predictions()
        if not price_preds.empty and asin in price_preds["asin"].values:
            tier = price_preds[price_preds["asin"] == asin].iloc[0].get("predicted_tier", "N/A")
            tier_colors = {"budget": "🟢", "mid_range": "🟡", "premium": "🔴"}
            st.write(f"**Price Tier:** {tier_colors.get(tier, '')} {tier}")

    # Reviews section
    reviews_sent = load_reviews_with_sentiment()
    if not reviews_sent.empty:
        product_reviews = reviews_sent[reviews_sent["asin"] == asin]
        if not product_reviews.empty:
            st.markdown("#### 💬 Reviews")

            # Sentiment distribution
            sent_col = None
            for col_name in ["final_sentiment", "sentiment", "llm_sentiment", "star_sentiment"]:
                if col_name in product_reviews.columns:
                    sent_col = col_name
                    break

            if sent_col:
                sent_counts = product_reviews[sent_col].value_counts()
                fig = px.pie(
                    values=sent_counts.values,
                    names=sent_counts.index,
                    title="Sentiment Distribution",
                    color=sent_counts.index,
                    color_discrete_map={
                        "positive": "#22C55E",
                        "neutral": "#F59E0B",
                        "negative": "#EF4444",
                    },
                )
                st.plotly_chart(fig, use_container_width=True)

            # Show some reviews
            body_col = "body_clean" if "body_clean" in product_reviews.columns else "body"
            if body_col in product_reviews.columns:
                for _, rev in product_reviews.head(5).iterrows():
                    sentiment = rev.get(sent_col, "") if sent_col else ""
                    rating_r = rev.get("rating", "")
                    emoji = {"positive": "👍", "neutral": "😐", "negative": "👎"}.get(sentiment, "")
                    st.markdown(
                        f"> {emoji} **{'⭐' * int(rating_r) if pd.notna(rating_r) else ''}** "
                        f"{str(rev.get(body_col, ''))[:300]}..."
                    )


# ===================================================================
# PAGE: FEATURE A — SIMILAR PRODUCT RECOMMENDATION
# ===================================================================
def page_feature_a():
    st.title("🔍 Feature A: Similar Product Recommendation")
    st.markdown(
        "Enter a **product ASIN** or a **search term** to find similar products. "
        "Three algorithms are compared: Semantic AI, TF-IDF, and BM25."
    )

    products = load_products()

    query = st.text_input("Enter ASIN or search term", placeholder="e.g. B0H9LHWBDK or noise cancelling headphones")

    if not query:
        st.info("Enter a query above to get recommendations.")
        return

    top_k = st.slider("Number of results", 3, 20, 5)

    # Determine if ASIN or text search
    is_asin = query.upper() in products["asin"].values
    if is_asin:
        query = query.upper()

    # Load models
    embeddings = load_semantic_model()
    tfidf_data = load_tfidf_model()
    bm25 = load_bm25_model()

    if embeddings is None or tfidf_data is None or bm25 is None:
        st.error("Recommendation models not found. Please run the Feature A notebook first.")
        return

    # Prepare data
    df_a = products.dropna(subset=["combined_text"]).copy().reset_index(drop=True)
    corpus = df_a["combined_text"].tolist()

    if is_asin:
        match = df_a[df_a["asin"] == query]
        if match.empty:
            st.warning(
                f"Product {query} exists but has no text data for recommendations. "
                "Try a text search instead."
            )
            return
        idx = match.index[0]
        target = df_a.loc[idx]
        st.success(f"Found product: **{str(target.get('title', ''))[:80]}**")
        query_text = target["combined_text"]
    else:
        idx = -1
        target = None
        query_text = query

    results = {}

    # --- Semantic Search ---
    try:
        from sentence_transformers import SentenceTransformer, util

        if is_asin:
            query_emb = embeddings[idx]
        else:
            model_st = SentenceTransformer("all-MiniLM-L6-v2")
            query_emb = model_st.encode(query, convert_to_tensor=True)

        cosine_scores = util.cos_sim(query_emb, embeddings)[0]
        num = top_k + 1 if is_asin else top_k
        top_sem = np.argpartition(-cosine_scores.cpu().numpy(), range(num))[:num]
        top_sem = top_sem[np.argsort(-cosine_scores[top_sem].cpu().numpy())]

        sem_rows = []
        count = 0
        for i in top_sem:
            if is_asin and i == idx:
                continue
            score = cosine_scores[i].item()
            sem_rows.append({
                "asin": df_a.loc[i, "asin"],
                "title": df_a.loc[i, "title"],
                "score": round(score, 3),
                "brand": df_a.loc[i].get("brand", ""),
                "category": df_a.loc[i].get("search_keyword", ""),
            })
            count += 1
            if count == top_k:
                break
        results["Semantic AI"] = pd.DataFrame(sem_rows)
    except Exception as e:
        st.warning(f"Semantic search unavailable: {e}")

    # --- TF-IDF Search ---
    try:
        from sklearn.metrics.pairwise import cosine_similarity

        vectorizer = tfidf_data["vectorizer"]
        tfidf_matrix = tfidf_data["matrix"]

        query_tfidf = vectorizer.transform([query_text])
        tfidf_scores = cosine_similarity(query_tfidf, tfidf_matrix).flatten()
        num = top_k + 1 if is_asin else top_k
        top_t = np.argpartition(-tfidf_scores, range(num))[:num]
        top_t = top_t[np.argsort(-tfidf_scores[top_t])]

        tfidf_rows = []
        count = 0
        for i in top_t:
            if is_asin and i == idx:
                continue
            score = tfidf_scores[i]
            tfidf_rows.append({
                "asin": df_a.loc[i, "asin"],
                "title": df_a.loc[i, "title"],
                "score": round(score, 3),
                "brand": df_a.loc[i].get("brand", ""),
                "category": df_a.loc[i].get("search_keyword", ""),
            })
            count += 1
            if count == top_k:
                break
        results["TF-IDF"] = pd.DataFrame(tfidf_rows)
    except Exception as e:
        st.warning(f"TF-IDF search unavailable: {e}")

    # --- BM25 Search ---
    try:
        tokenized_query = query_text.lower().split()
        bm25_scores = bm25.get_scores(tokenized_query)
        num = top_k + 1 if is_asin else top_k
        top_b = np.argpartition(-bm25_scores, range(num))[:num]
        top_b = top_b[np.argsort(-bm25_scores[top_b])]

        bm25_rows = []
        count = 0
        for i in top_b:
            if is_asin and i == idx:
                continue
            score = bm25_scores[i]
            bm25_rows.append({
                "asin": df_a.loc[i, "asin"],
                "title": df_a.loc[i, "title"],
                "score": round(score, 2),
                "brand": df_a.loc[i].get("brand", ""),
                "category": df_a.loc[i].get("search_keyword", ""),
            })
            count += 1
            if count == top_k:
                break
        results["BM25"] = pd.DataFrame(bm25_rows)
    except Exception as e:
        st.warning(f"BM25 search unavailable: {e}")

    # Display results
    if results:
        tabs = st.tabs(list(results.keys()))
        for tab, (algo_name, df_results) in zip(tabs, results.items()):
            with tab:
                st.subheader(f"{algo_name} Results")
                for rank, (_, row) in enumerate(df_results.iterrows(), 1):
                    col1, col2 = st.columns([1, 4])
                    with col1:
                        img = get_product_image(row["asin"])
                        if img:
                            st.image(img, width=100)
                    with col2:
                        st.markdown(f"**#{rank}** — {row['title']}")
                        st.caption(
                            f"ASIN: {row['asin']} | Score: {row['score']} | "
                            f"Brand: {row.get('brand', 'N/A')} | Category: {row.get('category', 'N/A')}"
                        )
    else:
        st.error("No results could be generated. Check that all recommendation models are available.")


# ===================================================================
# PAGE: FEATURE B — REVIEW SENTIMENT
# ===================================================================
def page_feature_b():
    st.title("💬 Feature B: Review Sentiment Analysis")
    st.markdown("Sentiment summaries by **product** and **category**.")

    tab1, tab2 = st.tabs(["By Category", "By Product"])

    with tab1:
        cat_summary = load_sentiment_by_category()
        if cat_summary.empty:
            st.warning("No category sentiment data found.")
        else:
            st.subheader("Sentiment Distribution by Category")

            # Identify percentage columns
            pct_cols = [c for c in cat_summary.columns if "percent" in c.lower() or "share" in c.lower()]
            cat_col = cat_summary.columns[0]  # first column is usually the category

            if pct_cols:
                fig = go.Figure()
                colors = {"positive": "#22C55E", "neutral": "#F59E0B", "negative": "#EF4444"}
                for col in pct_cols:
                    label = col.replace("_percent", "").replace("_share", "")
                    fig.add_trace(go.Bar(
                        y=cat_summary[cat_col],
                        x=cat_summary[col],
                        name=label.capitalize(),
                        orientation="h",
                        marker_color=colors.get(label, "#6366F1"),
                    ))
                fig.update_layout(barmode="stack", title="Sentiment by Category", height=600)
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(cat_summary, use_container_width=True)

    with tab2:
        prod_summary = load_sentiment_by_product()
        if prod_summary.empty:
            st.warning("No product sentiment data found.")
        else:
            st.subheader("Sentiment by Product")

            # Search
            search = st.text_input("Filter by ASIN", key="sent_search")
            display = prod_summary.copy()
            if search:
                display = display[display.iloc[:, 0].str.contains(search, case=False, na=False)]

            st.dataframe(display.head(50), use_container_width=True)

    # Show model info
    with st.expander("Model Details"):
        selected_model_path = SENT_DIR / "selected_model.txt"
        if selected_model_path.exists():
            st.write(f"**Selected model:** {selected_model_path.read_text().strip()}")

        comparison_path = SENT_DIR / "sentiment_model_comparison.csv"
        if comparison_path.exists():
            st.subheader("Model Comparison")
            st.dataframe(pd.read_csv(comparison_path), use_container_width=True)

        metrics_path = SENT_DIR / "final_sentiment_metrics.csv"
        if metrics_path.exists():
            st.subheader("Final Metrics")
            st.dataframe(pd.read_csv(metrics_path), use_container_width=True)


# ===================================================================
# PAGE: FEATURE C — THUMBNAIL GROUPING
# ===================================================================
def page_feature_c():
    st.title("🖼️ Feature C: Thumbnail Grouping")
    st.markdown(
        "Product images grouped into **visual clusters** without using category labels. "
        "Groups are based on visual features: color, shape, brightness, and CNN embeddings."
    )

    groups = load_thumbnail_groups()
    descriptions = load_group_descriptions()

    if groups.empty:
        st.warning("No thumbnail grouping data found. Run the Feature C notebook first.")
        return

    # Determine group column
    group_col = None
    for candidate in ["group", "cluster", "visual_group", "group_label"]:
        if candidate in groups.columns:
            group_col = candidate
            break
    if group_col is None:
        # fallback: use the last column that looks like a group
        for c in groups.columns:
            if "group" in c.lower() or "cluster" in c.lower():
                group_col = c
        if group_col is None:
            group_col = groups.columns[-1]

    unique_groups = sorted(groups[group_col].dropna().unique())
    n_groups = len(unique_groups)

    st.write(f"**{n_groups} visual groups** found across **{len(groups)} products**")

    # Group size chart
    group_sizes = groups[group_col].value_counts().sort_index()
    fig = px.bar(
        x=group_sizes.index.astype(str),
        y=group_sizes.values,
        labels={"x": "Group", "y": "Products"},
        title="Group Sizes",
        color=group_sizes.values,
        color_continuous_scale="viridis",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Group descriptions
    if not descriptions.empty:
        st.subheader("Group Descriptions")
        st.dataframe(descriptions, use_container_width=True)

    # Browse groups with sample images
    st.subheader("Browse Groups")
    selected_group = st.selectbox("Select a group", unique_groups)

    group_members = groups[groups[group_col] == selected_group]
    st.write(f"**{len(group_members)} products** in group {selected_group}")

    # Show sample images
    asin_col = "asin" if "asin" in group_members.columns else group_members.columns[0]
    sample_asins = group_members[asin_col].head(12).tolist()

    cols = st.columns(4)
    for i, asin in enumerate(sample_asins):
        with cols[i % 4]:
            img = get_product_image(asin)
            if img:
                st.image(img, caption=asin, use_container_width=True)
            else:
                st.caption(f"{asin}\n(no image)")

    # Show saved visualizations
    with st.expander("Clustering Analysis"):
        for img_name in [
            "clustering_metric_comparison.png",
            "final_group_sizes_and_projection.png",
            "category_validation_heatmap.png",
        ]:
            img_path = THUMB_DIR / img_name
            if img_path.exists():
                st.image(str(img_path), caption=img_name.replace("_", " ").replace(".png", "").title())


# ===================================================================
# PAGE: FEATURE D — PRICE TIER CLASSIFICATION
# ===================================================================
def page_feature_d():
    st.title("💰 Feature D: Price Tier Classification")
    st.markdown(
        "Each product classified as **budget**, **mid-range**, or **premium** "
        "using product attributes (title, description, brand, reviews) — **not the price itself**."
    )

    predictions = load_price_predictions()
    products = load_products()

    if predictions.empty:
        st.warning("No price tier predictions found. Run the Feature D notebook first.")
        return

    # Determine tier column
    tier_col = None
    for candidate in ["predicted_tier", "price_tier", "tier"]:
        if candidate in predictions.columns:
            tier_col = candidate
            break
    if tier_col is None:
        tier_col = predictions.columns[-1]

    # Merge with products for display
    display_df = predictions.copy()
    if "title" not in display_df.columns and "asin" in display_df.columns:
        prod_cols = ["asin", "title", "brand", "search_keyword", "price"]
        available_cols = [c for c in prod_cols if c in products.columns]
        display_df = display_df.merge(products[available_cols], on="asin", how="left", suffixes=("", "_prod"))

    # --- Overview ---
    tier_counts = display_df[tier_col].value_counts()
    tier_order = ["budget", "mid_range", "premium"]
    tier_counts = tier_counts.reindex([t for t in tier_order if t in tier_counts.index])

    col1, col2, col3 = st.columns(3)
    for i, (tier, count) in enumerate(tier_counts.items()):
        emoji = {"budget": "🟢", "mid_range": "🟡", "premium": "🔴"}.get(tier, "⚪")
        with [col1, col2, col3][i]:
            st.metric(f"{emoji} {tier.replace('_', ' ').title()}", count)

    # Tier distribution chart
    fig = px.pie(
        values=tier_counts.values,
        names=tier_counts.index,
        title="Price Tier Distribution",
        color=tier_counts.index,
        color_discrete_map={"budget": "#22C55E", "mid_range": "#F59E0B", "premium": "#EF4444"},
    )
    st.plotly_chart(fig, use_container_width=True)

    # By category
    if "search_keyword" in display_df.columns:
        st.subheader("Tier Distribution by Category")
        cat_tier = pd.crosstab(display_df["search_keyword"], display_df[tier_col])
        cat_tier = cat_tier.reindex(columns=[t for t in tier_order if t in cat_tier.columns], fill_value=0)

        fig2 = px.bar(
            cat_tier,
            barmode="stack",
            title="Price Tiers by Category",
            color_discrete_map={"budget": "#22C55E", "mid_range": "#F59E0B", "premium": "#EF4444"},
        )
        fig2.update_layout(height=600)
        st.plotly_chart(fig2, use_container_width=True)

    # --- Browse products by tier ---
    st.subheader("Browse by Tier")
    selected_tier = st.selectbox("Select tier", tier_order)
    tier_products = display_df[display_df[tier_col] == selected_tier]

    # Filter by category
    if "search_keyword" in tier_products.columns:
        cats = ["All"] + sorted(tier_products["search_keyword"].dropna().unique().tolist())
        cat_filter = st.selectbox("Filter by category", cats, key="tier_cat")
        if cat_filter != "All":
            tier_products = tier_products[tier_products["search_keyword"] == cat_filter]

    st.write(f"**{len(tier_products)} products** in {selected_tier}")
    display_cols = [c for c in ["asin", "title", "brand", "search_keyword"] if c in tier_products.columns]
    st.dataframe(
        tier_products[display_cols].head(30) if display_cols else tier_products.head(30),
        use_container_width=True,
    )

    # Model performance
    with st.expander("Model Performance"):
        metrics = load_price_metrics()
        if metrics:
            st.json(metrics)

        for img_name in ["model_comparison.png", "test_confusion_matrix.png", "top_model_features.png"]:
            img_path = PRICE_DIR / img_name
            if img_path.exists():
                st.image(str(img_path), caption=img_name.replace("_", " ").replace(".png", "").title())


# ===================================================================
# ROUTING
# ===================================================================
if page == "📦 Product List":
    page_product_list()
elif page == "🔍 A: Similar Products":
    page_feature_a()
elif page == "💬 B: Review Sentiment":
    page_feature_b()
elif page == "🖼️ C: Thumbnail Groups":
    page_feature_c()
elif page == "💰 D: Price Tiers":
    page_feature_d()
