"""
insights.py

Derives niche insights purely from already-downloaded fields (titles,
tags, upload times) — zero additional API calls. This is what makes an
expensive niche build worth more than just a table of videos.

Scope note: this covers TITLE text only. No thumbnail image data is
collected anywhere in this pipeline (that would require OCR or a vision
model — a genuinely different system). "Top words" from titles is the
closest real signal available; it is not a thumbnail analysis.

Public API:
    generate_insights(df) -> dict
    save_insights(insights, path)

Owns: stopwords, tokenization, n-gram generation, top tags, top
channels, upload-time distribution, and the title-pattern summary.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

from config import (MOMENTUM_MIN_WINDOW_VIDEOS, MOMENTUM_TAIL_WEIGHT,
                    COLLECTION_LOOKBACK_DAYS)


# Minimal hand-rolled stopword list — enough to keep title word/n-gram
# frequencies meaningful without pulling in an NLP dependency for
# something this simple.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "to", "of", "in", "on", "for", "with", "this", "that", "it",
    "you", "your", "i", "my", "we", "our", "how", "why", "what", "when",
    "where", "which", "who", "will", "can", "do", "does", "did", "if",
    "as", "at", "by", "from", "not", "no", "so", "up", "out", "all",
    "just", "into", "than", "then", "them", "his", "her", "he", "she",
    "they", "its", "us", "get", "got", "video", "new",
}


def _tokenize(text):
    text = str(text).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return text.split()


def _num(value, default=0.0):
    """
    Coerce a cell to float, treating NA/None/non-numeric as `default`.

    The idiom `value or 0` looks equivalent but raises
    "TypeError: boolean value of NA is ambiguous" on pandas NA — which is
    exactly what a quota-truncated build produces (channel metadata
    missing -> outlier_ratio is NA). That crashed the very
    graceful-degradation path meant to salvage a partial build.
    """
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ngram_stats(df, n):
    """
    Aggregate per-phrase stats across titles. Returns a list of dicts with
    count, avg_views, avg_vph and avg_outlier so callers can rank the SAME
    phrase pool by different criteria (see _ngram_leaderboards).
    """
    counts = defaultdict(int)
    view_sums = defaultdict(float)
    vph_sums = defaultdict(float)
    outlier_sums = defaultdict(float)

    has_vph = "views_per_hour" in df.columns
    has_out = "outlier_ratio" in df.columns

    for _, row in df.iterrows():
        tokens = [t for t in _tokenize(row.get("title", ""))
                  if t not in _STOPWORDS and len(t) > 2]
        if n == 1:
            grams = [t for t in tokens if not t.isdigit()]
        else:
            grams = [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]

        for g in set(grams):  # count each phrase once per title
            counts[g] += 1
            view_sums[g] += _num(row.get("view_count"))
            if has_vph:
                vph_sums[g] += _num(row.get("views_per_hour"))
            if has_out:
                outlier_sums[g] += _num(row.get("outlier_ratio"))

    rows = []
    for g, c in counts.items():
        rows.append({
            "phrase": g,
            "count": c,
            "avg_views": view_sums[g] / c,
            "avg_views_per_hour": (vph_sums[g] / c) if has_vph else None,
            "avg_outlier_ratio": (outlier_sums[g] / c) if has_out else None,
        })
    return rows


def _ngram_leaderboards(df, n, top_k, min_count=3):
    """
    Four leaderboards over the same phrase pool.

    Ranking phrases by COUNT alone is misleading for opportunity hunting:
    "interest rates" may appear 120 times averaging 300k views while
    "tariffs" appears 12 times averaging 4M. Frequency says the first is
    the story; the market says the second is. So we publish:

        most_common   — what the niche talks about (supply/saturation)
        most_viewed   — what actually earns views (demand)
        fastest       — highest avg views/hour (demand velocity)
        highest_outlier — phrases whose videos most beat their channels

    min_count guards the value-ranked boards against one-hit flukes.
    """
    rows = _ngram_stats(df, n)
    if not rows:
        return {"most_common": [], "most_viewed": [], "fastest": [],
                "highest_outlier": []}

    eligible = [r for r in rows if r["count"] >= min_count] or rows

    def top(source, key):
        vals = [r for r in source if r.get(key) is not None]
        return sorted(vals, key=lambda r: r[key], reverse=True)[:top_k]

    return {
        "most_common":     sorted(rows, key=lambda r: r["count"], reverse=True)[:top_k],
        "most_viewed":     top(eligible, "avg_views"),
        "fastest":         top(eligible, "avg_views_per_hour"),
        "highest_outlier": top(eligible, "avg_outlier_ratio"),
    }


def _top_ngrams(df, n, top_k):
    """Backwards-compatible count-ranked list (used by legacy callers)."""
    return _ngram_leaderboards(df, n, top_k)["most_common"]


def _top_channels(df, top_k=50):
    if "channel_title" not in df.columns or df.empty:
        return []
    g = (
        df.groupby("channel_title")
        .agg(video_count=("video_id", "count"), total_views=("view_count", "sum"), avg_views=("view_count", "mean"))
        .reset_index()
        .sort_values("total_views", ascending=False)
        .head(top_k)
    )
    return g.to_dict("records")


def _upload_time_distribution(df):
    by_hour = df["upload_hour"].value_counts().sort_index().to_dict() if "upload_hour" in df.columns else {}
    by_weekday = df["upload_weekday"].value_counts().to_dict() if "upload_weekday" in df.columns else {}
    return {"by_hour": by_hour, "by_weekday": by_weekday}


def _top_tags(df, top_k=200):
    counts = defaultdict(int)
    view_sums = defaultdict(int)

    for _, row in df.iterrows():
        tags = [t.strip().lower() for t in str(row.get("tags", "")).split("|") if t.strip()]
        for t in set(tags):
            counts[t] += 1
            view_sums[t] += row.get("view_count", 0)

    rows = [
        {"tag": t, "count": c, "avg_views": view_sums[t] / c}
        for t, c in counts.items()
    ]
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows[:top_k]


def title_pattern_summary(df):
    """
    Niche-level content-format profile. Public because grader.py computes
    the same summary for a topic and compares it against the niche's
    (loaded from insights.json via baseline.py) to score the Content pillar.
    """
    if df.empty:
        return {}

    def _pct(col):
        return float(df[col].mean() * 100) if col in df.columns else None

    def _avg(col):
        return float(df[col].mean()) if col in df.columns else None

    return {
        # Existing title-structure signals
        "pct_has_number": _pct("has_number"),
        "pct_has_question": _pct("has_question"),
        "pct_has_colon": _pct("has_colon"),
        "avg_title_length": _avg("title_length"),
        "avg_word_count": _avg("word_count"),
        # Format-intent flag rates (used by grader's Content pillar)
        "pct_has_tutorial": _pct("has_tutorial"),
        "pct_has_comparison": _pct("has_comparison"),
        "pct_has_myth": _pct("has_myth"),
        "pct_has_news": _pct("has_news"),
        "pct_has_prediction": _pct("has_prediction"),
        # Packaging-intensity averages
        "avg_curiosity_words": _avg("curiosity_word_count"),
        "avg_emotional_words": _avg("emotional_word_count"),
        "avg_urgency_words": _avg("urgency_word_count"),
        "avg_title_sentiment": _avg("title_sentiment"),
    }


# Backwards-compatible private alias (kept so any existing internal
# reference keeps working; new code should call the public name).
_title_pattern_summary = title_pattern_summary


def competition_summary(df):
    """
    NICHE-LEVEL supply / competition metrics — how crowded is this space?

    These are aggregates over the whole dataset (not per-video), which is
    why they live here rather than in metrics.py. They consume the per-video
    ingredients metrics.py exposes (channel_id, channel_subs, small/large
    creator flags, view_count).

    Public because grader.py computes the same summary for a topic and
    compares it directionally against the niche's (loaded from
    insights.json via baseline.py) to score the Competition pillar.

    Returns concentration and creator-mix signals:
        video_count, unique_channels, unique_channel_ratio,
        avg_subscribers, median_subscribers,
        pct_small_creators, pct_medium_creators, pct_large_creators,
        top10_channel_view_share  (market share of the 10 biggest channels),
        creator_dominance          (Herfindahl index of view share, 0–1;
                                     higher = more concentrated / dominated).
    """
    if df.empty or "channel_id" not in df.columns:
        return {}

    n_videos = len(df)
    unique_channels = int(df["channel_id"].nunique())

    # View share per channel → concentration measures.
    by_channel_views = df.groupby("channel_id")["view_count"].sum()
    total_views = float(by_channel_views.sum())
    if total_views > 0:
        shares = by_channel_views / total_views
        top10_share = float(shares.sort_values(ascending=False).head(10).sum())
        hhi = float((shares ** 2).sum())  # Herfindahl-Hirschman Index, 0–1
    else:
        top10_share = None
        hhi = None

    def _pct(colname):
        return float(df[colname].mean() * 100) if colname in df.columns else None

    subs = df["channel_subs"] if "channel_subs" in df.columns else None

    # Median catalogue depth of the channels present — how established the
    # incumbents are. One of the two supply signals that actually varies
    # between niches (CV 57% across eleven), so the supply pillar reads it
    # directly. Per-channel (first value per channel_id), not per-video,
    # so a prolific channel's many rows don't inflate it.
    cat_depth = None
    if "channel_video_count" in df.columns and "channel_id" in df.columns:
        cat_depth = float(
            df.groupby("channel_id")["channel_video_count"].first().median())

    return {
        "video_count": n_videos,
        "unique_channels": unique_channels,
        "unique_channel_ratio": unique_channels / n_videos if n_videos else None,
        "avg_subscribers": float(subs.mean()) if subs is not None else None,
        "median_subscribers": float(subs.median()) if subs is not None else None,
        "median_channel_video_count": cat_depth,
        "pct_small_creators": _pct("small_creator"),
        "pct_medium_creators": _pct("medium_creator"),
        "pct_large_creators": _pct("large_creator"),
        "top10_channel_view_share": top10_share,
        "creator_dominance": hhi,
    }


# Backwards-compatible private alias.
_competition_summary = competition_summary


# ============================================================
# MARKET-INTELLIGENCE SUMMARIES (niche-level)
# Supply = how much content is produced; Momentum = whether the market
# is strengthening; Opportunity indicators = demand-per-unit-supply.
# All are aggregates over the whole dataset (topic OR niche), computed
# the SAME way for both so grader.py can compare them niche-relative.
# ============================================================

# Windows (days) used for freshness / growth / evergreen calculations.
_RECENT_DAYS = 45
_PRIOR_DAYS = 90        # prior window is (_RECENT_DAYS, _PRIOR_DAYS]
_FRESH_DAYS = 90        # "fresh" = published within this many days
_EVERGREEN_OLD_DAYS = 180


def momentum_windows(lookback_days=None):
    """
    Recent and prior window sizes, scaled to the collection period.

    These were fixed at 45 and 90 days, which was correct while every
    build collected a full year. Once the collection window became
    selectable they broke silently: a 45-day build has NOTHING in the
    45-90 day prior window, so demand growth had no denominator, and a
    60-day build filled only 15 days of a 45-day window — a ratio
    computed from a third of the intended sample, reported with the same
    confidence as a full one.

    Above 90 days the original constants are kept, because momentum
    should describe RECENT change rather than year-long drift. Below
    that, the period is split in half: recent vs prior, both real.
    """
    lb = lookback_days or COLLECTION_LOOKBACK_DAYS
    if not lb or lb >= _PRIOR_DAYS:
        return _RECENT_DAYS, _PRIOR_DAYS
    return max(3, lb // 2), lb


def _days_since(df):
    """Series of whole days since each video's publish time (>= 0)."""
    now = datetime.now(timezone.utc)
    if "published_at" not in df.columns:
        # A collector-built dataset always has published_at; a partial or
        # externally-supplied frame may not. Return NaN so age-based
        # diagnostics abstain (their callers already treat NaN as "no
        # signal") rather than raising from inside a grade.
        return pd.Series([float("nan")] * len(df), index=df.index)
    published = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    days = (now - published).dt.total_seconds() / 86400
    return days.clip(lower=0)


def supply_summary(df):
    """
    Content-SUPPLY metrics — how much content the market is producing.

    Sample-size-invariant where it matters for grading (freshness is a
    proportion, acceleration and uploads_per_creator are ratios), so a
    topic sample can be compared against the niche baseline fairly. Raw
    counts (uploads_per_day/week/month) are sample-relative and included
    for reporting, not for niche-relative scoring.
    """
    if df.empty or "published_at" not in df.columns:
        return {}

    days = _days_since(df)
    n = len(df)
    unique_channels = int(df["channel_id"].nunique()) if "channel_id" in df.columns else 0

    recent = int((days <= _RECENT_DAYS).sum())
    prior = int(((days > _RECENT_DAYS) & (days <= _PRIOR_DAYS)).sum())
    fresh = int((days <= _FRESH_DAYS).sum())

    # Ratios (sample-invariant)
    content_freshness = fresh / n if n else None
    uploads_per_creator = n / unique_channels if unique_channels else None
    # Acceleration: recent daily rate vs prior daily rate.
    recent_rate = recent / _RECENT_DAYS
    prior_rate = prior / (_PRIOR_DAYS - _RECENT_DAYS)
    upload_acceleration = (recent_rate / prior_rate) if prior_rate > 0 else None

    # Consistency: 1 - coefficient of variation of monthly upload counts
    # (1 = perfectly steady output; lower = lumpy).
    upload_consistency = None
    if n >= 6:
        published = pd.to_datetime(df["published_at"], utc=True, errors="coerce").dt.tz_localize(None)
        monthly = published.dt.to_period("M").value_counts()
        if len(monthly) >= 2 and monthly.mean() > 0:
            cv = monthly.std() / monthly.mean()
            upload_consistency = float(max(0.0, 1.0 - cv))

    # Absolute rates (sample-relative — for reporting only)
    span_days = max(float(days.max()), 1.0)
    uploads_per_day = n / span_days
    active_creators = int(df.loc[days <= _FRESH_DAYS, "channel_id"].nunique()) if "channel_id" in df.columns else 0

    # Creator arrival rate: are NEW creators entering the niche? Channels
    # whose first appearance (earliest upload in this dataset) falls in the
    # recent window are "new arrivals". Rising arrivals = competition is
    # actively intensifying, which uploads/day alone doesn't reveal.
    new_creators_recent = new_creators_prior = creator_arrival_ratio = None
    if "channel_id" in df.columns:
        first_seen = df.assign(_days=days).groupby("channel_id")["_days"].max()
        new_creators_recent = int((first_seen <= _RECENT_DAYS).sum())
        new_creators_prior = int(((first_seen > _RECENT_DAYS)
                                  & (first_seen <= _PRIOR_DAYS)).sum())
        r_rate = new_creators_recent / _RECENT_DAYS
        p_rate = new_creators_prior / (_PRIOR_DAYS - _RECENT_DAYS)
        creator_arrival_ratio = (r_rate / p_rate) if p_rate > 0 else None

    return {
        "video_count": n,
        "uploads_per_day": uploads_per_day,
        "uploads_per_week": uploads_per_day * 7,
        "uploads_per_month": uploads_per_day * 30,
        "uploads_per_creator": uploads_per_creator,
        "active_creators": active_creators,
        "new_creators_recent": new_creators_recent,
        "new_creators_prior": new_creators_prior,
        "creator_arrival_ratio": creator_arrival_ratio,   # >1 = influx
        "content_freshness": content_freshness,          # 0–1, share published <= 90d
        "upload_acceleration": upload_acceleration,        # >1 = supply speeding up
        "upload_consistency": upload_consistency,          # ~1 = steady output
        "publishing_intensity": uploads_per_day,           # alias, reporting
    }


def momentum_summary(df, lookback_days=None):
    """
    MOMENTUM — is the market strengthening? Compares a RECENT window of
    uploads against the PRIOR window (within this single collection) for
    both demand and supply, plus evergreen persistence.

        demand_growth  = recent median views/hour  / prior median views/hour
        supply_growth  = recent upload rate         / prior upload rate
        (demand_growth > supply_growth  ->  demand outpacing supply)

    Also: evergreen_score (do old videos still pull views?), decay_rate,
    trend_direction, recent_breakout_frequency.
    """
    if df.empty or "published_at" not in df.columns:
        return {}

    # Windows scale with the collection period — a 45-day build has
    # nothing in a fixed 45-90 day prior window.
    recent_days, prior_days = momentum_windows(lookback_days)
    days = _days_since(df)
    recent_mask = days <= recent_days
    prior_mask = (days > recent_days) & (days <= prior_days)
    recent_n = int(recent_mask.sum())
    prior_n = int(prior_mask.sum())

    def _safe_ratio(a, b):
        return float(a / b) if (b and b > 0) else None

    # Demand growth via views_per_hour (already age-normalized).
    # Blend MEDIAN with P90: the median alone hides exactly the thing this
    # pillar exists to catch. A recent window of [50,50,50,50,50,5000] has
    # median 50 vs a prior median of 40 — "growth 1.25, boring" — when in
    # fact a monster trend just appeared. The P90 term sees it.
    demand_growth = None
    if "views_per_hour" in df.columns:
        r_med = df.loc[recent_mask, "views_per_hour"].median()
        p_med = df.loc[prior_mask, "views_per_hour"].median()
        r_p90 = df.loc[recent_mask, "views_per_hour"].quantile(0.90)
        p_p90 = df.loc[prior_mask, "views_per_hour"].quantile(0.90)

        growths = []
        if pd.notna(r_med) and pd.notna(p_med) and p_med > 0:
            growths.append(((1 - MOMENTUM_TAIL_WEIGHT), float(r_med / p_med)))
        if pd.notna(r_p90) and pd.notna(p_p90) and p_p90 > 0:
            growths.append((MOMENTUM_TAIL_WEIGHT, float(r_p90 / p_p90)))
        if growths:
            tw = sum(w for w, _ in growths)
            demand_growth = sum(w * g for w, g in growths) / tw

    # Supply growth via upload counts (normalized per-day per window length).
    recent_rate = int(recent_mask.sum()) / recent_days
    prior_rate = int(prior_mask.sum()) / (prior_days - recent_days)
    supply_growth = _safe_ratio(recent_rate, prior_rate)

    # Confidence on demand_growth — is the direction real, or could
    # resampling flip it?
    #
    # Measured across eleven real niches, the 45-day demand-growth ratio's
    # 95% bootstrap interval crosses 1.0 for SIX of them: the point
    # estimate says "rising" or "falling" but the data cannot actually
    # tell which. Momentum already abstains on sample SIZE (below
    # MOMENTUM_MIN_WINDOW_VIDEOS); this is the same honesty applied to
    # sample NOISE. A niche whose interval straddles 1.0 should contribute
    # ~nothing to opportunity, not because momentum is being damped, but
    # because there is no defensible direction to contribute. A niche
    # whose interval sits clearly above 1.0 contributes its FULL signal.
    #
    # 300 resamples is plenty for a 2.5/97.5 interval and cheap at these
    # window sizes. Deterministic seed so a given dataset always reports
    # the same interval.
    demand_growth_ci = None
    demand_growth_conclusive = None
    demand_growth_confidence = None
    if ("views_per_hour" in df.columns
            and recent_n >= MOMENTUM_MIN_WINDOW_VIDEOS
            and prior_n >= MOMENTUM_MIN_WINDOW_VIDEOS):
        import numpy as _np
        r_vals = df.loc[recent_mask, "views_per_hour"].dropna().values
        p_vals = df.loc[prior_mask, "views_per_hour"].dropna().values
        if len(r_vals) >= MOMENTUM_MIN_WINDOW_VIDEOS and len(p_vals) >= MOMENTUM_MIN_WINDOW_VIDEOS:
            _rng = _np.random.default_rng(len(r_vals) * 1000 + len(p_vals))
            boot = []
            tw = MOMENTUM_TAIL_WEIGHT
            for _ in range(300):
                rs = _rng.choice(r_vals, len(r_vals), replace=True)
                ps = _rng.choice(p_vals, len(p_vals), replace=True)
                rm, pm = _np.median(rs), _np.median(ps)
                rp, pp = _np.quantile(rs, 0.90), _np.quantile(ps, 0.90)
                if pm > 0 and pp > 0:
                    boot.append((1 - tw) * (rm / pm) + tw * (rp / pp))
            if boot:
                lo, hi = float(_np.percentile(boot, 2.5)), float(_np.percentile(boot, 97.5))
                demand_growth_ci = [lo, hi]
                demand_growth_conclusive = (lo > 1.0) or (hi < 1.0)

                # CONTINUOUS confidence, 0..1, replacing a hard cutoff.
                #
                # FIRST VERSION (superseded): confidence = distance / (distance
                # + width), dividing the near edge's distance from 1.0 by the
                # interval's raw width. This punished WIDE-but-unambiguous
                # intervals: credit_cards_explained's [2.62, 6.02] — entirely
                # above 1.0, a clearly rising niche — scored confidence 0.32
                # under that formula purely because the interval happened to
                # be wide, which cut its momentum contribution from 26.4 to
                # 10.6 despite the direction never being in doubt. Width and
                # directional certainty are different questions; that formula
                # conflated them.
                #
                # THIS VERSION measures confidence relative to the POINT
                # ESTIMATE instead of the interval's own width, in log space
                # (matching the log scale momentum already uses elsewhere):
                #
                #   near_edge = whichever bound sits closer to 1.0
                #   confidence = |log10(near_edge)| / |log10(point_estimate)|
                #
                # This asks "how much of the point estimate's apparent
                # strength survives at the conservative edge of the
                # interval?" A tight interval where the edge sits nearly as
                # far from 1.0 as the point estimate itself scores near 1
                # (little uncertainty ate into the signal). A wide interval
                # whose conservative edge still clears 1.0 by a healthy
                # margin — like credit_cards' [2.62, 6.02] — scores high too,
                # because 2.62 itself is already far from 1.0; width alone no
                # longer punishes it. An interval whose edge sits barely past
                # 1.0 relative to how large the point estimate claims to be
                # — technically "conclusive" but only just — correctly scores
                # low, which is what happened to ancient_history's boundary
                # case: [1.09, 2.87] scores confidence ~0.15 here, versus
                # being treated identically to credit_cards' confident rise
                # under the old binary test.
                point = float(_np.median(boot))
                if point > 0 and point != 1.0:
                    signed_point = abs(_np.log10(point))
                    near_edge = lo if lo > 1.0 else (hi if hi < 1.0 else None)
                    if near_edge is None:
                        # interval straddles 1.0 — direction genuinely unknown
                        demand_growth_confidence = 0.0
                    elif signed_point <= 0:
                        demand_growth_confidence = 0.0
                    else:
                        signed_edge = abs(_np.log10(near_edge))
                        demand_growth_confidence = max(
                            0.0, min(1.0, signed_edge / signed_point))
                else:
                    demand_growth_confidence = 0.0

    # Evergreen: do old videos still earn views/hour comparable to new ones?
    evergreen_score = None
    if "views_per_hour" in df.columns:
        # Evergreen needs genuinely OLD videos. On a short collection
        # window none exist, so it reports nothing rather than comparing
        # two slices of the same recent period and calling it durability.
        old_cut = _EVERGREEN_OLD_DAYS
        fresh_cut = _FRESH_DAYS
        if lookback_days and lookback_days < _EVERGREEN_OLD_DAYS * 1.5:
            old_cut = fresh_cut = None
        if old_cut is not None:
            old = df.loc[days > old_cut, "views_per_hour"].median()
            new = df.loc[days <= fresh_cut, "views_per_hour"].median()
        else:
            old = new = None
        if pd.notna(old) and pd.notna(new) and new > 0:
            evergreen_score = float(min(old / new, 2.0))  # cap at 2x
    decay_rate = (1.0 - evergreen_score) if evergreen_score is not None else None

    # Recent breakout frequency (uses raw outlier_ratio ingredient), AND
    # the same rate in the PRIOR window — the grader needs both to know
    # whether "recent" is elevated or depressed relative to what this
    # niche's own data normally looks like, rather than judging it against
    # an arbitrary fixed constant.
    recent_breakout_frequency = None
    prior_breakout_frequency = None
    if "outlier_ratio" in df.columns:
        if recent_mask.sum() > 0:
            recent_breakout_frequency = float((df.loc[recent_mask, "outlier_ratio"] >= 3).mean())
        if prior_mask.sum() > 0:
            prior_breakout_frequency = float((df.loc[prior_mask, "outlier_ratio"] >= 3).mean())

    trend_direction = None
    if demand_growth is not None:
        trend_direction = "rising" if demand_growth > 1.05 else ("falling" if demand_growth < 0.95 else "flat")

    return {
        "recent_window_videos": recent_n,
        "prior_window_videos": prior_n,
        "sufficient_data": (recent_n >= MOMENTUM_MIN_WINDOW_VIDEOS
                            and prior_n >= MOMENTUM_MIN_WINDOW_VIDEOS),
        "demand_growth": demand_growth,
        "demand_growth_ci": demand_growth_ci,
        "demand_growth_conclusive": demand_growth_conclusive,
        "demand_growth_confidence": demand_growth_confidence,
        "supply_growth": supply_growth,
        "view_acceleration": demand_growth,     # alias per doc vocabulary
        "upload_acceleration": supply_growth,   # alias per doc vocabulary
        "trend_direction": trend_direction,
        "evergreen_score": evergreen_score,     # ~1 = evergreen, <1 = decaying
        "decay_rate": decay_rate,
        "recent_breakout_frequency": recent_breakout_frequency,
        "prior_breakout_frequency": prior_breakout_frequency,
    }


def seo_summary(df):
    """
    Packaging profile of the niche — and, crucially, how the TOP performers
    differ from the rest.

    Reporting "median tag count = 12" is nearly useless on its own. What a
    creator needs is "breakout videos carry 18 tags, everyone else carries
    9" — a contrast that suggests an actionable packaging change. So every
    field is reported for the whole niche AND for the top decile by
    views_per_hour, with the ratio between them.

    Diagnostics only; nothing here feeds demand/supply/momentum scoring.
    """
    if df.empty or "views_per_hour" not in df.columns:
        return {}

    fields = ["tag_count", "title_length", "description_length",
              "description_word_count", "desc_link_count",
              "duration_seconds", "topic_category_count"]
    bool_fields = ["has_chapters", "has_captions", "has_number",
                   "has_question", "has_colon", "title_truncated_risk"]

    cutoff = df["views_per_hour"].quantile(0.90)
    top = df[df["views_per_hour"] >= cutoff]
    if top.empty:
        return {}

    out = {"top_decile_videos": int(len(top))}

    for f in fields:
        if f not in df.columns:
            continue
        all_med = float(df[f].median()) if pd.notna(df[f].median()) else None
        top_med = float(top[f].median()) if pd.notna(top[f].median()) else None
        out[f] = {
            "niche_median": all_med,
            "top_decile_median": top_med,
            # >1 means top performers use MORE of this than the niche norm
            "ratio": (top_med / all_med) if (all_med and top_med is not None) else None,
        }

    for f in bool_fields:
        if f not in df.columns:
            continue
        all_rate = float(df[f].mean())
        top_rate = float(top[f].mean())
        out[f] = {
            "niche_rate": all_rate,
            "top_decile_rate": top_rate,
            "lift": (top_rate / all_rate) if all_rate else None,
        }

    return out


def opportunity_indicators(df):
    """
    Demand-per-unit-SUPPLY indicators (the raw ingredients of opportunity).
    All are per-upload / per-creator averages -> sample-size invariant.
    """
    if df.empty:
        return {}

    unique_channels = int(df["channel_id"].nunique()) if "channel_id" in df.columns else 0
    total_views = float(df["view_count"].sum()) if "view_count" in df.columns else 0.0

    def _mean(col):
        return float(df[col].mean()) if col in df.columns else None

    interactions = None
    if "like_count" in df.columns and "comment_count" in df.columns:
        interactions = float((df["like_count"] + df["comment_count"]).mean())

    small_success = None
    if "small_creator" in df.columns and "outlier_ratio" in df.columns:
        # Of the videos that overperform (outlier_ratio >= 2), what share are small creators?
        over = df[df["outlier_ratio"] >= 2]
        if len(over) > 0:
            small_success = float(over["small_creator"].mean())

    return {
        "views_per_upload": _mean("view_count"),
        "likes_per_upload": _mean("like_count"),
        "comments_per_upload": _mean("comment_count"),
        "engagement_per_upload": interactions,
        "views_per_creator": (total_views / unique_channels) if unique_channels else None,
        "small_creator_success_rate": small_success,
        "large_creator_dominance": _mean("large_creator"),
    }


def generate_insights(df):
    """
    NOTE on scope: this covers TITLE text only — there is no thumbnail
    image data collected anywhere in this pipeline (that would require
    OCR or a vision model, a genuinely different system). "Top words"
    from titles is the closest real signal available; it is not a
    thumbnail analysis.
    """
    return {
        # Four leaderboards over the same phrase pool — ranking by count
        # alone hides high-value/low-frequency topics (see
        # _ngram_leaderboards). "most_viewed" / "fastest" /
        # "highest_outlier" are the opportunity-relevant boards.
        "word_leaderboards": _ngram_leaderboards(df, 1, 200),
        "bigram_leaderboards": _ngram_leaderboards(df, 2, 200),
        "trigram_leaderboards": _ngram_leaderboards(df, 3, 100),
        # Back-compat count-ranked lists
        "top_words": _top_ngrams(df, 1, 500),
        "top_bigrams": _top_ngrams(df, 2, 500),
        "top_trigrams": _top_ngrams(df, 3, 200),
        "top_channels": _top_channels(df, 50),
        "upload_time_distribution": _upload_time_distribution(df),
        "top_tags": _top_tags(df, 200),
        "title_pattern_summary": title_pattern_summary(df),
        "seo_summary": seo_summary(df),
        "competition_summary": competition_summary(df),
        "supply_summary": supply_summary(df),
        "momentum_summary": momentum_summary(df),
        "opportunity_indicators": opportunity_indicators(df),
        "note": "Derived entirely from title/tag/upload-time fields already collected — "
                "no additional API calls. Does not include thumbnail image analysis "
                "(no thumbnail data is downloaded by this pipeline).",
    }


def save_insights(insights, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(insights, f, indent=2, default=str)
