"""
discover.py

Opportunity discovery: score every collected video, CLUSTER the videos into
recurring topics, and export both rankings for optional manual AI analysis.

Philosophy: math discovers, AI explains. Everything here is deterministic.
No LLM is called by the engine.

--- Why clusters, not just a video ranking -------------------------------
A single video scoring 98 is one lucky performer. Seven videos scoring ~92
across five DIFFERENT channels is a market signal: repeated, independent
success is much stronger evidence that the audience wants a topic than one
exceptional outlier. Ranking videos individually buries that pattern and
pushes the most valuable inference onto the human (or the AI). So this
module finds the clusters itself, then ranks the clusters.

--- Breakout score (0-100, deterministic, niche-relative) ----------------
Blends three views of "this outperformed", so it isn't purely
channel-relative:
    outlier_ratio percentile      did it beat its OWN channel's norm?
    breakout_velocity percentile  how FAST did that overperformance build?
    views_per_hour percentile     absolute market DEMAND (a trend-starting
                                  video on a big channel has outlier ~1 but
                                  huge demand — channel-relative signals
                                  alone are blind to it)
Weights: config.BREAKOUT_VELOCITY_WEIGHT, config.BREAKOUT_DEMAND_WEIGHT.

--- Cluster score --------------------------------------------------------
    cluster_score = (1-w) * avg_breakout + w * evidence
    evidence      = 100 * log2(channels+1) / log2(REFERENCE+1)
Independent repetition is rewarded (log-scaled so a single prolific channel
can't manufacture a cluster), and CLUSTER_MIN_CHANNELS enforces that a
cluster must span multiple creators.

Public API:
    score_breakouts(df, baseline)          -> df + 'breakout_score', sorted
    build_clusters(scored_df)              -> list[dict] ranked clusters
    export_breakouts(df, baseline, niche)  -> {json, csv, md, clusters_csv}
"""

import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

from baseline import Baseline
from config import (
    EXPORTS_DIR, EXPORT_COLUMNS,
    BREAKOUT_VELOCITY_WEIGHT, BREAKOUT_DEMAND_WEIGHT,
    CLUSTER_MIN_VIDEOS, CLUSTER_MIN_CHANNELS, CLUSTER_NGRAM_SIZES,
    CLUSTER_MAX, CLUSTER_EVIDENCE_WEIGHT, CLUSTER_EVIDENCE_REFERENCE,
    CLUSTER_WEAK_WORDS, DISCOVERY_EXCLUDE_SHORTS,
)
from insights import _STOPWORDS, _tokenize
from paths import safe_name


# ============================================================
# SCORING
# ============================================================

def score_breakouts(df: pd.DataFrame, baseline: Baseline) -> pd.DataFrame:
    """
    Add a 'breakout_score' column (0-100) and return the DataFrame sorted by
    it, descending. Pure and deterministic; does not modify the input.
    """
    if df.empty or "outlier_ratio" not in df.columns:
        out = df.copy()
        out["breakout_score"] = pd.Series(dtype=float)
        return out

    out = df.copy()

    def pct_series(col):
        if col not in out.columns or not baseline.has_metric(col):
            return None
        return out[col].apply(
            lambda v: baseline.percentile_of(col, float(v)) if pd.notna(v) else 0.0
        )

    outlier_pct = pct_series("outlier_ratio")
    velocity_pct = pct_series("breakout_velocity")
    demand_pct = pct_series("views_per_hour")

    # Renormalize weights over whatever components are actually available.
    parts, weights = [], []
    w_vel = BREAKOUT_VELOCITY_WEIGHT
    w_dem = BREAKOUT_DEMAND_WEIGHT
    w_out = max(0.0, 1.0 - w_vel - w_dem)

    for series, w in ((outlier_pct, w_out), (velocity_pct, w_vel), (demand_pct, w_dem)):
        if series is not None and w > 0:
            parts.append(series)
            weights.append(w)

    if not parts:
        out["breakout_score"] = 0.0
    else:
        total_w = sum(weights)
        blended = sum(s * (w / total_w) for s, w in zip(parts, weights))
        out["breakout_score"] = blended

    out["breakout_score"] = out["breakout_score"].round(1)
    return out.sort_values("breakout_score", ascending=False).reset_index(drop=True)


# ============================================================
# CLUSTERING (deterministic, phrase-based)
# ============================================================

def _candidate_phrases(title):
    """
    Stopword-filtered n-gram phrases used as cluster seeds.

    A phrase must contain at least one CONTENT word (not in
    CLUSTER_WEAK_WORDS). All-weak phrases like "right now", "should buy",
    "latest news", "year old" are conversational filler, not topics — on
    real data they formed large, useless clusters, while every genuine
    topic cluster ("chip stocks", "gold silver") contained a content word.
    """
    tokens = [t for t in _tokenize(title)
              if t not in _STOPWORDS and len(t) > 2 and not t.isdigit()]
    phrases = set()
    for n in CLUSTER_NGRAM_SIZES:
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if all(w in CLUSTER_WEAK_WORDS for w in gram):
                continue
            phrases.add(" ".join(gram))
    return phrases


def build_clusters(scored_df: pd.DataFrame) -> list:
    """
    Group scored videos into recurring-topic clusters by shared title
    phrases, then rank clusters by evidence-weighted breakout strength.

    A cluster must contain >= CLUSTER_MIN_VIDEOS videos from
    >= CLUSTER_MIN_CHANNELS distinct channels — this is what separates a
    real market signal from one channel repeating itself.
    """
    if scored_df.empty or "title" not in scored_df.columns:
        return []

    # phrase -> row indices
    buckets = defaultdict(set)
    for idx, title in scored_df["title"].fillna("").items():
        for phrase in _candidate_phrases(title):
            buckets[phrase].add(idx)

    raw = []
    for phrase, idxs in buckets.items():
        if len(idxs) < CLUSTER_MIN_VIDEOS:
            continue
        sub = scored_df.loc[sorted(idxs)]
        channels = sub["channel_id"].nunique() if "channel_id" in sub.columns else 1
        if channels < CLUSTER_MIN_CHANNELS:
            continue
        raw.append((phrase, set(idxs), sub, channels))

    # De-duplicate overlapping clusters. Bigger evidence wins (more videos
    # processed first); phrase length breaks ties in favour of specificity.
    # Containment is measured against the SMALLER of the two sets — the
    # earlier version measured against the candidate only, which let
    # "stocks buy" (26 vids) and "stocks buy now" (15 vids, fully contained
    # in the former) BOTH survive as separate clusters on real data.
    raw.sort(key=lambda r: (-len(r[1]), -len(r[0].split())))
    kept = []
    for phrase, idxs, sub, channels in raw:
        subsumed = False
        for k in kept:
            containment = (len(idxs & k["_idxs"])
                           / max(1, min(len(idxs), len(k["_idxs"]))))
            if containment >= 0.8:
                subsumed = True
                break
        if not subsumed:
            kept.append({"phrase": phrase, "_idxs": idxs, "_sub": sub,
                         "channels": channels})

    clusters = []
    for k in kept:
        sub, phrase = k["_sub"], k["phrase"]
        avg_breakout = float(sub["breakout_score"].mean())
        channels = int(k["channels"])

        # Evidence term: independent repetition matters, log-scaled and
        # normalized against a reference channel count so it saturates
        # gracefully at 100 instead of blowing past it.
        #
        # NOTE: this is a WEIGHTED BLEND, not a multiplier. An earlier
        # version used avg_breakout * (1 + w*log2(channels)) clamped to
        # 100 — which pushed almost every cluster past the ceiling and
        # collapsed the ranking (three clusters with avg_breakout 73, 90
        # and 97 all scored exactly 100.0). Blending keeps the score on a
        # real 0-100 scale and preserves ordering.
        evidence = min(100.0, 100.0 * math.log2(max(channels, 1) + 1)
                       / math.log2(CLUSTER_EVIDENCE_REFERENCE + 1))
        score = (1 - CLUSTER_EVIDENCE_WEIGHT) * avg_breakout \
            + CLUSTER_EVIDENCE_WEIGHT * evidence

        clusters.append({
            "topic": phrase,
            "cluster_score": round(score, 1),
            "avg_breakout": round(avg_breakout, 1),
            "videos": int(len(sub)),
            "unique_channels": channels,
            "avg_views_per_hour": round(float(sub["views_per_hour"].mean()), 1)
                if "views_per_hour" in sub.columns else None,
            "avg_outlier_ratio": round(float(sub["outlier_ratio"].mean()), 2)
                if "outlier_ratio" in sub.columns else None,
            "small_creator_share": round(float(sub["small_creator"].mean()), 3)
                if "small_creator" in sub.columns else None,
            "top_titles": sub.nlargest(3, "breakout_score")["title"].tolist(),
        })

    clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters[:CLUSTER_MAX]


# ============================================================
# EXPORT
# ============================================================

def export_breakouts(df: pd.DataFrame, baseline: Baseline, niche_name: str) -> dict:
    """
    Score, cluster, and write the FULL ranked video list plus the cluster
    ranking to exports/. Returns a dict of format -> path.
    """
    if DISCOVERY_EXCLUDE_SHORTS and "short_video" in df.columns:
        n_before = len(df)
        df = df[~df["short_video"].fillna(False)]
        print(f"  (excluding Shorts: {n_before - len(df)} removed, "
              f"{len(df)} long-form videos ranked)")

    ranked = score_breakouts(df, baseline)
    if ranked.empty:
        raise ValueError("No videos to export — dataset is empty.")

    clusters = build_clusters(ranked)

    cols = [c for c in EXPORT_COLUMNS if c in ranked.columns]
    slim = ranked[cols].copy()
    slim.insert(0, "rank", range(1, len(slim) + 1))

    os.makedirs(EXPORTS_DIR, exist_ok=True)
    stem = os.path.join(EXPORTS_DIR, f"{safe_name(niche_name)}_breakout")
    paths = {
        "json":         stem + ".json",
        "csv":          stem + ".csv",
        "md":           stem + ".md",
        "clusters_csv": stem + "_clusters.csv",
    }

    slim.to_csv(paths["csv"], index=False)
    if clusters:
        pd.DataFrame([{k: v for k, v in c.items() if k != "top_titles"}
                      for c in clusters]).to_csv(paths["clusters_csv"], index=False)
    else:
        paths.pop("clusters_csv")

    payload = {
        "niche": niche_name,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "video_count": len(slim),
        "cluster_count": len(clusters),
        "breakout_score_definition": (
            f"{max(0.0, 1-BREAKOUT_VELOCITY_WEIGHT-BREAKOUT_DEMAND_WEIGHT):.0%} outlier_ratio + "
            f"{BREAKOUT_VELOCITY_WEIGHT:.0%} breakout_velocity + "
            f"{BREAKOUT_DEMAND_WEIGHT:.0%} views_per_hour (all niche percentiles)"
        ),
        "cluster_score_definition": (
            f"{1-CLUSTER_EVIDENCE_WEIGHT:.0%} avg_breakout + "
            f"{CLUSTER_EVIDENCE_WEIGHT:.0%} evidence"
            f"(evidence = log2(channels+1)/log2({CLUSTER_EVIDENCE_REFERENCE}+1)) — "
            "rewards repeated INDEPENDENT success over single outliers"
        ),
        "clusters": clusters,
        "videos": json.loads(slim.to_json(orient="records")),
    }
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write(_markdown_report(slim, clusters, niche_name))

    return paths


def _markdown_report(slim, clusters, niche_name) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n = len(slim)

    out = [f"# {niche_name} — Opportunity Discovery\n",
           f"Generated {ts} · {n} collected videos · {len(clusters)} topic clusters\n"]

    out.append(f"""
> **Analysis prompt — paste this file into Claude or ChatGPT:**
>
> Below are topic clusters the engine detected in {niche_name}, plus the
> full ranked video list. Clusters are already grouped and scored by the
> engine — repeated success across multiple independent channels.
> 1. For each top cluster, explain *why* it is trending right now.
> 2. Suggest adjacent video opportunities each cluster implies but that
>    do not yet appear in the data (the underserved follow-up questions).
> 3. Flag clusters where small creators are winning (high
>    small_creator_share) — those prove the topic carries the video.
> 4. Recommend additional seed search queries these clusters suggest.
> 5. Note any clusters that look like noise rather than a real theme.
> 6. Using the SEO/packaging columns (tags, tag_count, title_length,
>    description_length, has_chapters, has_captions, duration_seconds,
>    topic_categories), describe how the top-ranked videos are PACKAGED
>    versus the rest, and suggest concrete title/tag/description choices
>    for a new video in the strongest cluster.
""")

    if clusters:
        out.append("\n## Topic clusters (ranked by evidence-weighted breakout)\n")
        out.append("| # | topic | cluster_score | videos | channels | avg_breakout | avg_vph | small_creator_share |")
        out.append("|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(clusters, 1):
            scs = "" if c["small_creator_share"] is None else f"{c['small_creator_share']:.0%}"
            vph = "" if c["avg_views_per_hour"] is None else f"{c['avg_views_per_hour']:,.0f}"
            out.append(
                f"| {i} | {c['topic']} | {c['cluster_score']} | {c['videos']} | "
                f"{c['unique_channels']} | {c['avg_breakout']} | {vph} | {scs} |"
            )
        out.append("\n### Example titles per cluster\n")
        for i, c in enumerate(clusters[:15], 1):
            out.append(f"**{i}. {c['topic']}**")
            for t in c["top_titles"]:
                out.append(f"  - {str(t)[:110]}")
            out.append("")

    out.append("\n## Full ranked video list\n")
    display_cols = [c for c in slim.columns if c != "video_id"]
    out.append("| " + " | ".join(display_cols) + " |")
    out.append("|" + "|".join("---" for _ in display_cols) + "|")
    for _, r in slim.iterrows():
        cells = []
        for c in display_cols:
            v = r[c]
            if isinstance(v, float):
                v = f"{v:,.1f}"
            elif isinstance(v, bool):
                v = "yes" if v else ""
            v = str(v).replace("|", "\\|").replace("\n", " ")
            if c == "title" and len(v) > 90:
                v = v[:89] + "…"
            cells.append(v)
        out.append("| " + " | ".join(cells) + " |")

    return "\n".join(out) + "\n"
