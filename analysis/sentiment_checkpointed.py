"""Durable Feature B automatic-label checkpoints and sentiment summaries."""
from __future__ import annotations
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd

VALID = {"positive", "neutral", "negative"}

def prepare_reviews(frame):
    df=frame.copy()
    if "body_clean" not in df: raise ValueError("body_clean is required")
    if "rating" not in df: raise ValueError("rating is required for automatic silver-label validation")
    df=df[df.body_clean.notna() & df.body_clean.astype(str).str.strip().ne("")].copy()
    if "review_id" not in df:
        identity=df.fillna("").astype(str)[[c for c in ["asin","reviewer_name","date","body_clean"] if c in df]].agg("|".join,axis=1)
        df["review_id"]=identity.map(lambda x:hashlib.sha1(x.encode()).hexdigest()[:20])
    df["body_hash"]=df.body_clean.astype(str).map(lambda x:hashlib.sha256(x.encode()).hexdigest())
    df=df.drop_duplicates("review_id",keep="first").reset_index(drop=True)
    rating=pd.to_numeric(df.get("rating"),errors="coerce")
    df["star_sentiment"]=pd.cut(rating,bins=[0,2,3,5],labels=["negative","neutral","positive"]).astype("string")
    df["language_group"]=np.where(df.get("language",pd.Series("unknown",index=df.index)).eq("en"),"en","non_en")
    return df

def _atomic_parquet(frame,path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix(".tmp.parquet")
    frame.to_parquet(temp,index=False); temp.replace(path)

def label_with_checkpoint(reviews,labeller,checkpoint_dir,prompt_version="sentiment_v2",batch_size=10):
    """Resume safely by review ID/body hash/model/prompt; retry failures next run."""
    model=labeller.model_name
    safe=model.replace("/","_")
    path=Path(checkpoint_dir)/f"labels__{safe}__{prompt_version}.parquet"
    base=reviews.copy(); key=["review_id","body_hash"]
    if path.exists():
        old=pd.read_parquet(path)
        old=old[(old.model_name==model)&(old.prompt_version==prompt_version)]
        old=old[key+["sentiment"]].drop_duplicates(key,keep="last")
        base=base.merge(old,on=key,how="left")
    else: base["sentiment"]=pd.NA
    pending=base.index[~base.sentiment.isin(VALID)].tolist()
    print(f"{model}: reused {len(base)-len(pending)}, remaining {len(pending)}")
    for start in range(0,len(pending),batch_size):
        indices=pending[start:start+batch_size]
        predictions=labeller.label_batch(base.loc[indices,"body_clean"].tolist())
        successful_batch=0
        for idx,pred in zip(indices,predictions):
            if pred in VALID:
                base.at[idx,"sentiment"]=pred
                successful_batch+=1
        saved=base[key+["sentiment"]].copy(); saved["model_name"]=model; saved["prompt_version"]=prompt_version
        _atomic_parquet(saved,path)
        print(f"checkpoint {min(start+batch_size,len(pending))}/{len(pending)} -> {path.name}")
        if successful_batch == 0:
            print("No labels were returned for this batch. Stopping safely; rerun later after checking quota/API messages.")
            break
    return base,path

def sentiment_summaries(labelled,label_col="sentiment"):
    def one(index_column):
        counts=pd.crosstab(labelled[index_column],labelled[label_col]).reindex(columns=["positive","neutral","negative"],fill_value=0)
        counts["total_reviews"]=counts.sum(axis=1)
        for c in ["positive","neutral","negative"]: counts[f"{c}_percent"]=100*counts[c]/counts.total_reviews
        counts["dominant_sentiment"]=counts[["positive","neutral","negative"]].idxmax(axis=1)
        return counts.reset_index()
    return one("asin"),one("search_keyword")
