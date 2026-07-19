"""
metrics.py

Per-video feature engineering for YouTube videos, organized into four
pillars: Demand, Competition (ingredients), Opportunity, and Content.

This module ONLY computes derived features that are well-defined for a
SINGLE video (a single DataFrame row). It deliberately does NOT:
    - call the YouTube API,
    - grade / classify anything against a threshold,
    - compute distribution-level or niche-level aggregates
      (mean/median/percentiles, channel concentration, market share,
      "percent small creators", etc.).

Those aggregates belong downstream: per-metric distributions in
statistics.py, niche-level supply/competition summaries in insights.py,
and percentile-based classification in grader.py (via baseline.py).

Why no breakout flags here anymore:
    The old breakout_candidate / extreme_breakout_candidate used absolute
    thresholds (outlier_ratio >= 5 / >= 10). Whether a given multiple is a
    "breakout" is niche-relative — 3x is extraordinary in a slow niche and
    routine in gaming. So we keep the raw, niche-agnostic ingredient
    (outlier_ratio) and let grader.py classify it against a niche
    percentile from baseline.py. No arbitrary cutoffs live in this file.

A note on redundancy:
    Several conceptually-distinct "signals" reduce to the same arithmetic
    (e.g. views/subs, interactions/subs, X/hours). Where two ideas collapse
    onto one quantity we expose it ONCE under its clearest name and note the
    alias in a comment, rather than emitting collinear duplicate columns
    that would distort distribution stats and grading weights.

Input:
    DataFrame from collector.py (raw columns only).
Output:
    Same DataFrame with derived feature columns added.
"""

from datetime import datetime, timezone
import re

import isodate
import pandas as pd


# ============================================================
# SHARED TIME BASE
# ============================================================

def _hours_since_upload(series):
    now = datetime.now(timezone.utc)
    published = pd.to_datetime(series, utc=True)
    hours = (now - published).dt.total_seconds() / 3600
    return hours.clip(lower=1)


# ============================================================
# PILLAR 1 — DEMAND
# "Are viewers responding, and how fast?"
# ============================================================

def demand_features(df):
    df["hours_since_upload"] = _hours_since_upload(df["published_at"])
    subs = df["channel_subs"].clip(lower=1)
    views = df["view_count"].clip(lower=1)
    interactions = df["like_count"] + df["comment_count"]

    # Rate-over-time signals
    df["views_per_hour"] = df["view_count"] / df["hours_since_upload"]
    df["likes_per_hour"] = df["like_count"] / df["hours_since_upload"]
    df["comments_per_hour"] = df["comment_count"] / df["hours_since_upload"]
    df["views_per_day"] = df["view_count"] / (df["hours_since_upload"] / 24)

    # Engagement intensity
    df["engagement_rate"] = interactions / views
    df["engagement_per_hour"] = interactions / df["hours_since_upload"]
    df["like_rate"] = df["like_count"] / views
    df["comment_rate"] = df["comment_count"] / views

    # engagement_velocity: how fast *engaged* views accumulate
    # (reach speed weighted by how engaging the video is). Distinct from
    # engagement_per_hour, which counts raw interactions/hour.
    df["engagement_velocity"] = df["engagement_rate"] * df["views_per_hour"]

    # comment_to_like_ratio: discussion vs. approval. High values flag
    # debate/controversy/community rather than passive liking.
    df["comment_to_like_ratio"] = df["comment_count"] / df["like_count"].clip(lower=1)

    # "returning interest" proxy: commenters are more invested than likers,
    # so a high comment share is a proxy for durable/returning interest.
    # (Equals comment_rate; exposed under an intent-revealing name.)
    df["returning_interest_proxy"] = df["comment_rate"]

    # Per-subscriber demand — how a video performs relative to the channel's
    # existing audience. subscriber_conversion_proxy reuses views_per_subscriber
    # (views far above the sub base imply pulling in non-subscribers).
    df["views_per_subscriber"] = df["view_count"] / subs
    df["views_per_subscriber_per_hour"] = df["view_count"] / (subs * df["hours_since_upload"])
    df["likes_per_subscriber"] = df["like_count"] / subs
    df["comments_per_subscriber"] = df["comment_count"] / subs
    df["engagement_per_subscriber"] = interactions / subs
    df["subscriber_conversion_proxy"] = df["views_per_subscriber"]  # alias, kept explicit

    return df


# ============================================================
# PILLAR 2 — COMPETITION (per-video INGREDIENTS only)
# "How crowded is this space?" — the actual crowding metrics
# (channel concentration, market share, % small/large creators,
# unique-channel ratio, average/median subs) are NICHE-LEVEL and are
# computed in insights.py, which can see the whole dataset. Here we only
# expose the per-row ingredients those aggregates consume.
# ============================================================

def competition_ingredients(df):
    # channel_avg_views is computed once here and reused by opportunity
    # features and outlier_ratio rather than recomputed.
    df["channel_avg_views"] = (
        df["channel_view_count"] / df["channel_video_count"].clip(lower=1)
    )
    # views_per_video is the channel's average — same quantity, exposed
    # under the name statistics/insights will aggregate on.
    df["views_per_video"] = df["channel_avg_views"]

    df["outlier_ratio"] = df["view_count"] / df["channel_avg_views"].clip(lower=1)

    # Creator-size bands — the raw ingredients insights.py aggregates into
    # "percent small/large creators" and grader.py reads for creator fit.
    df["small_creator"] = df["channel_subs"] < 10000
    df["medium_creator"] = (df["channel_subs"] >= 10000) & (df["channel_subs"] < 100000)
    df["large_creator"] = df["channel_subs"] >= 100000

    # channel_age_proxy: no channel creation date is collected, so this is a
    # ROUGH proxy — output volume correlates with tenure. Treat as weak.
    df["channel_age_proxy"] = df["channel_video_count"]

    return df


# ============================================================
# PILLAR 3 — OPPORTUNITY
# "Can an unknown creator still win here?"
# ============================================================

def opportunity_features(df):
    subs = df["channel_subs"].clip(lower=1)

    # underdog_score: high views from a small subscriber base. Sub count is
    # damped (sqrt) so the score rewards small channels punching up without
    # exploding for micro-channels.
    df["underdog_score"] = df["view_count"] / (subs ** 0.5)

    # creator_efficiency: views each subscriber yields on an average video
    # for this channel (channel_avg_views / subs). Channel-level lens.
    df["creator_efficiency"] = df["channel_avg_views"] / subs

    # subscriber_efficiency: lifetime views per subscriber (channel scale).
    df["subscriber_efficiency"] = df["channel_view_count"] / subs

    # viral_efficiency: reach SPEED relative to (damped) audience size —
    # how fast a video spreads beyond what its sub base would predict.
    df["viral_efficiency"] = df["views_per_hour"] / (subs ** 0.5)

    # breakout_velocity: how quickly the overperformance built up. Same
    # outlier magnitude in fewer days = hotter. Uses the raw outlier_ratio
    # ingredient (NOT a breakout classification — that's grader's job).
    days = df["hours_since_upload"] / 24
    df["breakout_velocity"] = df["outlier_ratio"] / days.clip(lower=1)

    # overperformance_factor is mathematically the outlier_ratio (views vs.
    # the channel's own norm); reused under an intent-revealing name.
    df["overperformance_factor"] = df["outlier_ratio"]

    return df


# ============================================================
# PILLAR 4 — CONTENT PATTERNS
# Per-video title/format signals. These flags are the raw material
# insights.py aggregates into niche-level "what formats work" summaries.
# ============================================================

# Small hand-rolled lexicons — enough signal without an NLP dependency.
_CURIOSITY_WORDS = {
    "secret", "secrets", "truth", "hidden", "revealed", "reveal", "nobody",
    "everyone", "mistake", "wrong", "surprising", "shocking", "unknown",
}
_URGENCY_WORDS = {
    "now", "today", "before", "hurry", "last", "stop", "must", "need",
    "immediately", "deadline", "urgent", "quick", "fast",
}
_EMOTIONAL_WORDS = {
    "amazing", "shocking", "insane", "unbelievable", "incredible", "worst",
    "best", "crazy", "epic", "terrible", "love", "hate", "awesome", "awful",
}
_POSITIVE_WORDS = {
    "best", "amazing", "love", "great", "incredible", "awesome", "perfect",
    "easy", "win", "success", "top", "good", "better",
}
_NEGATIVE_WORDS = {
    "worst", "hate", "terrible", "fail", "mistake", "wrong", "scam",
    "danger", "bad", "hard", "lose", "warning", "avoid",
}


def seo_features(df):
    """
    Metadata/SEO features — the *packaging* side of a video, as opposed to
    the topic itself. Discovery tells you WHAT to make; these tell you HOW
    winning videos in the niche are packaged for search and browse.

    All are descriptive diagnostics, not scored inputs: they never touch
    demand/supply/momentum. Their job is to show up in the discovery export
    so the pattern behind breakout videos is visible.
    """
    tags = df["tags"].fillna("") if "tags" in df.columns else pd.Series([""] * len(df))
    desc = df["description"].fillna("") if "description" in df.columns else pd.Series([""] * len(df))
    titles = df["title"].fillna("")

    # --- Tags: YouTube's keyword metadata ---
    df["tag_count"] = tags.apply(lambda t: len([x for x in str(t).split("|") if x.strip()]))
    df["tag_char_length"] = tags.str.len()

    # --- Description structure ---
    df["description_length"] = desc.str.len()
    df["description_word_count"] = desc.str.split().str.len().fillna(0).astype(int)
    # Chapters (timestamps like 0:00 / 12:34) — improve retention & give
    # YouTube section metadata; a strong packaging signal when present.
    df["has_chapters"] = desc.str.contains(r"(?:^|\n)\s*\d{1,2}:\d{2}", regex=True)
    df["desc_link_count"] = desc.str.count(r"https?://")
    df["desc_hashtag_count"] = desc.str.count(r"#\w+")
    df["title_hashtag_count"] = titles.str.count(r"#\w+")

    # --- Discoverability flags ---
    if "caption" in df.columns:
        # Captions are indexed by YouTube search and aid accessibility.
        df["has_captions"] = df["caption"].astype(str).str.lower().isin(["true", "1"])
    if "topic_categories" in df.columns:
        df["topic_category_count"] = df["topic_categories"].fillna("").apply(
            lambda t: len([x for x in str(t).split("|") if x.strip()])
        )

    # Does the title lead with its keywords? Front-loaded titles are both
    # a ranking convention and survive truncation in browse/search UIs.
    df["title_truncated_risk"] = df["title_length"] > 60

    return df


def _word_set(title):
    return set(re.findall(r"[a-z']+", str(title).lower()))


def title_features(df):
    titles = df["title"].fillna("")
    df["title_length"] = titles.str.len()
    df["word_count"] = titles.str.split().str.len()
    df["has_number"] = titles.str.contains(r"\d", regex=True)
    df["has_question"] = titles.str.contains(r"\?")
    df["has_colon"] = titles.str.contains(r":")

    def caps_ratio(text):
        letters = re.findall(r"[A-Za-z]", text)
        if not letters:
            return 0
        upper = sum(1 for c in letters if c.isupper())
        return upper / len(letters)

    df["uppercase_ratio"] = titles.apply(caps_ratio)
    return df


def content_format_features(df):
    titles = df["title"].fillna("").str.lower()
    df["has_year"] = titles.str.contains(r"\b20\d{2}\b", regex=True)
    df["has_money_term"] = titles.str.contains(r"\$|money|income|salary|cost|price", regex=True)
    df["has_list_format"] = titles.str.contains(r"\b\d+\b", regex=True)
    return df


def content_signal_features(df):
    """Format-intent flags + lexicon-based tone signals (all per-video)."""
    titles = df["title"].fillna("").str.lower()

    # Format-intent flags (word-boundary matched where ambiguity matters)
    df["has_tutorial"] = titles.str.contains(
        r"how to|how i|\bguide\b|tutorial|step by step|\btips\b|explained|\blearn\b", regex=True
    )
    df["has_comparison"] = titles.str.contains(
        r"\bvs\b|versus|compared|comparison|better than", regex=True
    )
    df["has_myth"] = titles.str.contains(
        r"\bmyth\b|debunk|truth about|\bactually\b|misconception", regex=True
    )
    df["has_news"] = titles.str.contains(
        r"breaking|\bnews\b|update|announced|\bleaked\b|\breport\b", regex=True
    )
    df["has_prediction"] = titles.str.contains(
        r"prediction|forecast|\bfuture\b|\bwill\b|going to|expect|\bnext\b", regex=True
    )

    # Lexicon word counts
    word_sets = df["title"].fillna("").apply(_word_set)
    df["curiosity_word_count"] = word_sets.apply(lambda w: len(w & _CURIOSITY_WORDS))
    df["urgency_word_count"] = word_sets.apply(lambda w: len(w & _URGENCY_WORDS))
    df["emotional_word_count"] = word_sets.apply(lambda w: len(w & _EMOTIONAL_WORDS))

    # Naive lexicon sentiment in ~[-1, 1]: (pos - neg) / word_count.
    # This is a crude signal, not a trained sentiment model.
    def sentiment(text):
        words = re.findall(r"[a-z']+", str(text).lower())
        if not words:
            return 0.0
        pos = sum(1 for w in words if w in _POSITIVE_WORDS)
        neg = sum(1 for w in words if w in _NEGATIVE_WORDS)
        return (pos - neg) / len(words)

    df["title_sentiment"] = df["title"].fillna("").apply(sentiment)
    return df


def duration_features(df):
    def seconds(value):
        try:
            return isodate.parse_duration(value).total_seconds()
        except Exception:
            return 0

    df["duration_seconds"] = df["duration"].apply(seconds)
    df["long_video"] = df["duration_seconds"] > 600
    df["short_video"] = df["duration_seconds"] < 180
    return df


# ============================================================
# DATE FEATURES
# ============================================================

def upload_features(df):
    published = pd.to_datetime(df["published_at"], utc=True)
    df["upload_hour"] = published.dt.hour
    df["upload_weekday"] = published.dt.day_name()
    df["upload_month"] = published.dt.month
    return df


# ============================================================
# MASTER FUNCTION
# ============================================================

def calculate_all(df):
    # Pillar 1 — Demand (sets hours_since_upload first; others depend on it)
    df = demand_features(df)

    # Pillar 2 — Competition ingredients (sets channel_avg_views + size bands)
    df = competition_ingredients(df)

    # Pillar 3 — Opportunity (depends on channel_avg_views + outlier_ratio)
    df = opportunity_features(df)

    # Pillar 4 — Content patterns
    df = title_features(df)
    df = content_format_features(df)
    df = content_signal_features(df)
    df = duration_features(df)

    # Pillar 4b — SEO / packaging metadata (diagnostics, never scored)
    df = seo_features(df)

    # Date features
    df = upload_features(df)

    return df
