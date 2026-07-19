"""
config.py

Central configuration for the YouTube niche analysis pipeline.

All paths, constants, version strings, scoring weights, and limits live
here. Import from this module rather than hard-coding values elsewhere.
"""

import os

# ============================================================
# VERSION STRINGS
# ============================================================

DATABASE_SCHEMA_VERSION = "4.0"
COLLECTOR_VERSION       = "4.0"
METRICS_VERSION         = "2.0"
GRADER_VERSION          = "1.0"
YOUTUBE_API_VERSION     = "v3"

# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATASETS_DIR  = os.path.join(BASE_DIR, "datasets")
TOPICS_DIR    = os.path.join(BASE_DIR, "topics")
HISTORY_DIR   = os.path.join(BASE_DIR, "history")
CACHE_DIR     = os.path.join(BASE_DIR, "cache")

# ============================================================
# FILES
# ============================================================

QUOTA_HISTORY_PATH = os.path.join(BASE_DIR, "quota_history.csv")

# ============================================================
# YOUTUBE API
# ============================================================

SEARCH_RESULTS_PER_CALL = 50   # YouTube max per search.list call

# YouTube caps a single search.list QUERY at ~500 total results, no matter
# how many pages you request. To exceed that (and to bound recency), the
# collector slices the search into consecutive date windows.
SEARCH_MAX_PER_QUERY = 500     # hard ceiling per single query/window

# Collection is confined to the last COLLECTION_LOOKBACK_DAYS, sliced into
# SEARCH_WINDOW_DAYS-wide windows. A bounded, recent span keeps the
# momentum/"trending" logic meaningful (a decade-wide scatter makes
# recent-vs-prior comparisons noise). Set COLLECTION_LOOKBACK_DAYS = None
# to disable windowing and fall back to a single unbounded query.
COLLECTION_LOOKBACK_DAYS = 365   # only collect videos from the last ~year
SEARCH_WINDOW_DAYS = 30          # window size per search slice

# Bias search results toward a language (YouTube's relevanceLanguage
# parameter, ISO 639-1). On a real build, 21 of the top 100 breakouts were
# non-English despite English seeds. NOTE: this is a BIAS, not a strict
# filter — YouTube may still return other languages. None disables it.
RELEVANCE_LANGUAGE = "en"

# Target markets (YouTube regionCode, ISO 3166-1 alpha-2). Advertiser rates
# vary sharply by VIEWER country — US/CA/GB/AU are consistently among the
# highest-paying anglophone markets — so biasing collection toward them
# surfaces opportunities whose audiences monetize better.
#
# IMPORTANT semantics: regionCode filters by where a video can be VIEWED,
# not where its creator is based. Most videos are viewable worldwide, so
# this is a relevance BIAS, not a hard geographic filter. Paired with
# RELEVANCE_LANGUAGE it is far more effective than either alone.
# Default: US only. The US is the largest anglophone market and among the
# highest-paying, and a SINGLE-market baseline stays interpretable — a
# pooled US+CA+GB+AU distribution ranks UK ISA content against US 401(k)
# content, so "77th percentile demand" would describe no real market.
#
# This costs nothing in coverage: universal topics ("how to save money")
# are viewable in the US and still appear. What a US-only baseline drops is
# CA/GB/AU-SPECIFIC content (ISAs, TFSAs, superannuation) — build those as
# SEPARATE niches (finance_uk, finance_au) rather than blending them here.
REGION_CODES = ["US"]

# How REGION_CODES are applied:
#   "all"    — search EVERY seed in EVERY region, then de-duplicate.
#              Full market coverage: guarantees no target market is skipped,
#              so region-specific opportunities (a GB-only topic, say) can't
#              be structurally missed. Costs roughly 3x the search quota,
#              because English finance content is viewable in all four
#              markets and most results dedupe away. Measured on a 5-seed
#              build: ~21,000 units for ~3,000 unique videos.
#   "rotate" — cycle regions across seeds (seed 1 -> US, seed 2 -> CA, ...).
#              Each seed sees ONE market. Quota-efficient (~6,500 units for
#              ~2,700 videos) but cannot find a topic that only surfaces in
#              a market its seed wasn't assigned.
#   "first"  — REGION_CODES[0] only (single-market baseline).
#
# NOTE on "all" + quota: at ~21,000 units it consumes most of a 3-key day.
# To reduce: shorten COLLECTION_LOOKBACK_DAYS (fewer date windows) or lower
# PER_SEED_MIN_RESULTS.
REGION_STRATEGY = "first"

# ============================================================
# MULTI-KEY API ROTATION
# ============================================================
# .env may define up to MAX_API_KEYS keys: YOUTUBE_API_KEY_1,
# YOUTUBE_API_KEY_2, YOUTUBE_API_KEY_3 (plain YOUTUBE_API_KEY also works
# and is treated as key 1). When a key returns quotaExceeded the collector
# rotates to the next and retries the same request; when ALL keys are
# exhausted, collection stops gracefully and the pipeline continues with
# whatever has been gathered so far (partial dataset, never a crash).
MAX_API_KEYS = 3

# ============================================================
# MULTI-SEED NICHE COLLECTION
# ============================================================
# A niche can be built from several related seed queries (e.g. finance,
# investing, economics, banking, stocks). Each seed is searched
# independently (with its own per-query ceiling and date windows) and the
# results are merged + de-duplicated into one dataset.
MAX_SEEDS_PER_NICHE = 8

# Each seed is allowed to pull AT LEAST this many results before the next
# seed starts, regardless of how many seeds there are.
#
# Why a floor instead of target//len(seeds): dividing the target starves
# each seed. A single seed can routinely return 600+ videos, so splitting a
# 2000 target across 5 seeds capped every one of them at 400 and threw away
# data that was free to collect. With a floor, each seed pulls what it can
# and collection stops when the overall NICHE_DATABASE_SIZE target is met
# (or the seeds run dry, or quota is exhausted).
PER_SEED_MIN_RESULTS = 700

# ============================================================
# STATISTICS
# ============================================================
# Percentile points stored per metric in statistics.csv. SHARED so
# statistics.py (writer) and baseline.py (reader) can never drift apart.
STATISTICS_PERCENTILE_POINTS = [1, 5, 10, 20, 25, 30, 40, 50,
                                60, 70, 75, 80, 90, 95, 99]

# ============================================================
# OPPORTUNITY DISCOVERY / EXPORT
# ============================================================
# The discovery workflow ranks the ENTIRE collected niche dataset by
# breakout score, then CLUSTERS videos into recurring topics — because
# repeated independent success (7 videos, 5 channels, all scoring ~92) is
# stronger evidence of a real opportunity than one exceptional performer
# scoring 98. Clustering is deterministic (phrase-based), not AI.
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
BREAKOUT_VELOCITY_WEIGHT = 0.30   # 0 = pure outlier percentile

# Demand context: how much the video's absolute demand percentile (views
# per hour vs the niche) contributes to breakout score, alongside the
# channel-relative signals. Without this, a video that starts a whole new
# trend on a big channel is invisible (its channel-relative outlier is ~1).
BREAKOUT_DEMAND_WEIGHT = 0.25

# Clustering
CLUSTER_MIN_VIDEOS = 3        # a phrase needs this many videos to be a cluster
CLUSTER_MIN_CHANNELS = 2      # ...from at least this many distinct channels
CLUSTER_NGRAM_SIZES = (2, 3)  # phrase lengths considered as cluster seeds
CLUSTER_MAX = 40              # max clusters reported

# A phrase can only seed a cluster if it contains at least one CONTENT word.
# Phrases built entirely from the weak words below ("right now", "should
# buy", "latest news", "year old") are conversational filler, not topics.
# NOTE: this filter was chosen from evidence, not theory — on a real 1,957-
# video finance build, document frequency FAILED to separate junk from
# signal ("right now" 0.72% vs "chip stocks" 0.56%), while the junk was
# perfectly characterized by being all-weak-word phrases.
CLUSTER_WEAK_WORDS = {
    # temporal / deictic
    "now", "today", "latest", "right", "new", "current", "recent", "next",
    "day", "week", "month", "year", "time", "old", "ago", "still",
    # generic verbs + modals
    "buy", "sell", "watch", "know", "need", "make", "get", "want", "use",
    "see", "look", "going", "explained", "revealed",
    "should", "could", "would", "must", "might", "about", "truth",
    # vague nouns / qualifiers
    "news", "video", "best", "top", "big", "huge", "thing", "things",
    "people", "guy", "man", "woman", "way", "ways", "everyone", "nobody",
    "life", "world", "full", "live", "update", "updates", "real", "really",
    "here", "ever", "never", "always",
}
# Cluster score blends breakout strength with an EVIDENCE term:
#   score = (1-w)*avg_breakout + w*evidence
#   evidence = 100 * log2(channels+1) / log2(REFERENCE+1)   (capped at 100)
# so repeated INDEPENDENT success is rewarded on a real 0-100 scale.
# (A multiply-then-clamp form was tried first and saturated: clusters with
# avg_breakout 73, 90 and 97 all pinned to exactly 100, destroying the rank.)
CLUSTER_EVIDENCE_WEIGHT = 0.25
CLUSTER_EVIDENCE_REFERENCE = 10   # channel count that earns a full evidence score

# Discovery can exclude Shorts (< 3 min) from the breakout ranking. On a
# real finance build, hashtag-spam Shorts with enormous views/hour crowded
# the top of the list while carrying HALF the median engagement of the
# dataset — a different content economy than long-form video ideas. Default
# False (no silent behavior change); flip to True for long-form hunting.
DISCOVERY_EXCLUDE_SHORTS = False

# Columns included in the per-video discovery export (order preserved).
EXPORT_COLUMNS = [
    "breakout_score",
    "title",
    "channel_title",
    "published_at",
    "view_count",
    "views_per_hour",
    "outlier_ratio",
    "breakout_velocity",
    "engagement_rate",
    "channel_subs",
    "small_creator",
    "short_video",
    # --- SEO / packaging metadata ---
    # Discovery says WHAT to make; these show HOW the winners are packaged.
    "tags",
    "tag_count",
    "topic_categories",
    "title_length",
    "description_length",
    "has_chapters",
    "has_captions",
    "duration_seconds",
    "video_id",
]

# Default collection sizes — override per-call via max_results kwarg.
# Overall target for a niche build. This is a CEILING/stop-condition, not a
# per-seed divisor — see PER_SEED_MIN_RESULTS below.
NICHE_DATABASE_SIZE = 3000
TOPIC_SAMPLE_SIZE   = 500      # ~1 010-1 020 quota units per collect

# ============================================================
# DAILY QUOTA LIMIT
# ============================================================

# YouTube Data API v3 free tier: 10 000 units/day.
# Set slightly below the hard ceiling so warnings fire before we hit it.
QUOTA_DAILY_LIMIT   = 10_000
QUOTA_WARN_THRESHOLD = 8_000   # emit a warning once used exceeds this

# Per-operation quota costs (units charged by YouTube, not by us).
# https://developers.google.com/youtube/v3/determine_quota_cost
QUOTA_COSTS = {
    "search.list":   100,
    "videos.list":   1,
    "channels.list": 1,
}

# ============================================================
# GRADING — MARKET-INTELLIGENCE MODEL
# ============================================================
# The engine evaluates a topic as a MARKET. Three measured pillars —
# Inferred Demand, Content Supply, Momentum — each get their own score and
# letter grade. Opportunity is NOT a parallel pillar: it is DERIVED from
# the relationship between demand and supply (modulated by momentum), per
# the guiding principle "opportunity exists where inferred demand exceeds
# content supply." The Opportunity score is the headline result.

# --- Inferred Demand (per-video, scored by baseline percentile rank) ---
#
# Metrics are GROUPED by the signal they measure, because several are
# near-perfectly correlated and averaging them flat lets one signal vote
# many times. Measured on a real 1,957-video finance build (Spearman):
#     views_per_hour  <-> views_per_day    1.000   (same number x 24)
#     engagement_rate <-> like_rate        1.000   (likes dominate)
#     comment_rate    <-> engagement_rate  0.929
# Flat-averaging those six gave engagement ~3 votes and view-velocity ~2.8,
# so like-ratio quietly outweighed views/hour — the reverse of their real
# information content. Each GROUP now scores once (mean of its members),
# then groups combine by DEMAND_GROUP_WEIGHTS.
DEMAND_METRIC_GROUPS = {
    # How fast the audience consumes it — the primary demand signal.
    "consumption": ["views_per_hour", "views_per_day"],
    # How fast engagement accrues (mixes volume and rate).
    "engagement_velocity": ["engagement_velocity"],
    # How strongly viewers react per view — a quality/affinity signal.
    "engagement_quality": ["engagement_rate", "like_rate", "comment_rate"],
}

# Weights across groups.
#
# engagement_velocity LEADS because it is the interaction term:
#     engagement_velocity = engagement_rate x views_per_hour
# i.e. "engaged views per hour". It only scores high when a topic pulls
# volume AND provokes reaction, which is the sharpest available proxy for
# "this is catching fire right now" — precisely what an opportunity hunter
# wants. Volume alone can be passive/clickbait traffic; reaction alone can
# be a tiny devoted audience. The product demands both.
#
# HONEST CAVEAT: because it is literally the product of the other two
# groups, it is not an independent signal — raising its weight partly
# re-counts consumption and quality. It is weighted as an interaction
# term, not as a third dimension. Consumption keeps meaningful weight
# because raw reach still matters on its own; quality keeps the least
# because like-ratio is the weakest standalone demand evidence.
DEMAND_GROUP_WEIGHTS = {
    "engagement_velocity": 0.40,   # "raving right now" (volume x reaction)
    "consumption": 0.35,           # raw audience appetite
    "engagement_quality": 0.25,    # per-view affinity
}

# Flat list retained for collection/reporting code that just needs to know
# which columns are demand-related.
DEMAND_METRICS = [
    "views_per_hour",
    "views_per_day",
    "engagement_velocity",
    "engagement_rate",
    "like_rate",
    "comment_rate",
]

# Demand is scored on BOTH the median (typical performance) and the P90
# (upside / breakout ceiling), then blended. Median alone hides a fat upper
# tail — 10 viral videos among 50 mediocre ones can otherwise look identical
# to 60 uniformly mediocre ones, which is exactly the signal this engine
# should catch. Weight is on the tail; set to 0.0 to score on median only.
DEMAND_TAIL_WEIGHT = 0.35          # 0 = median-only, 1 = P90-only
DEMAND_TAIL_PERCENTILE = 90        # which upper percentile represents "upside"

# Directional (niche-relative scalar) scoring uses a bounded LOG-RATIO rather
# than topic/(topic+niche), which saturates: a 10x market and a 100x market
# both pin near 99. Log-ratio keeps 50 at parity but preserves resolution
# across orders of magnitude. DIRECTIONAL_LOG_SCALE sets points per 10x:
# at 18, a 10x deviation ≈ 68, 100x ≈ 86, ~215x reaches the 0/100 rails —
# so realistic niche/topic gaps stay off the ceiling and keep resolution.
DIRECTIONAL_LOG_SCALE = 18.0

# --- Content Supply (niche-level, scored directionally: topic vs niche) ---
# Higher score = MORE supply / saturation (worse for opportunity). Only
# sample-size-invariant supply metrics are scored (see insights.supply_summary).
SUPPLY_METRICS = {
    "content_freshness":   "higher",   # more fresh content = more active competition
    "upload_acceleration": "higher",   # supply speeding up = more pressure
    "uploads_per_creator": "higher",   # creators posting more = more pressure
    "median_subscribers":  "higher",   # bigger incumbents = harder to break in
}
# median_subscribers is read from the niche competition_summary; the rest
# from supply_summary. Grader knows where each lives.

# Momentum needs enough videos in its recent/prior windows to be meaningful.
# Below this count in either window, momentum abstains rather than emitting a
# number computed from near-empty buckets.
MOMENTUM_MIN_WINDOW_VIDEOS = 15

# Momentum's demand-growth term blends the window MEDIAN with its P90.
# Median alone hides an emerging explosion (one 5000-VPH video among fifties
# doesn't move a median at all) — which is precisely what momentum should
# detect. Mirrors DEMAND_TAIL_WEIGHT in the grader.
MOMENTUM_TAIL_WEIGHT = 0.40

# --- Opportunity derivation ---
# opportunity = 50 + (demand_score - supply_score)/2, then nudged by momentum.
OPPORTUNITY_MOMENTUM_INFLUENCE = 0.30   # ±15 pts at momentum extremes (0/100)

# 2×2 market verdict thresholds (on the 0–100 niche-relative pillar scores).
VERDICT_HIGH = 55.0
VERDICT_LOW = 45.0

# Opportunity indicators surfaced in the report (from opportunity_indicators).
OPPORTUNITY_INDICATOR_KEYS = [
    "views_per_upload",
    "engagement_per_upload",
    "views_per_creator",
    "small_creator_success_rate",
    "large_creator_dominance",
]

# ============================================================
# GRADING — THRESHOLDS
# ============================================================

# Percentile cutoffs (0–100).  A metric at P90 of its niche distribution
# earns a near-perfect score; at P10 it earns a very poor score.
GRADE_EXCELLENT_PERCENTILE = 90
GRADE_GOOD_PERCENTILE      = 70
GRADE_FAIR_PERCENTILE      = 50
GRADE_POOR_PERCENTILE      = 30

# Letter-grade cutoffs (score is 0–100).
GRADE_THRESHOLDS = {
    "A": 80,
    "B": 65,
    "C": 50,
    "D": 35,
    # below 35 → F
}

# ============================================================
# INSIGHTS
# ============================================================

TOP_WORDS_K    = 500
TOP_BIGRAMS_K  = 500
TOP_TRIGRAMS_K = 200
TOP_TAGS_K     = 200
TOP_CHANNELS_K = 50

# ============================================================
# REPORT
# ============================================================

# Columns to include in the terminal summary table.
SUMMARY_COLUMNS = [
    "title",
    "channel_title",
    "view_count",
    "views_per_hour",
    "engagement_rate",
    "outlier_ratio",
    "underdog_score",
]

# Max rows shown in terminal tables by default.
TERMINAL_MAX_ROWS = 20
