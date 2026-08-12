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

# Selectable lookbacks offered at build time. A niche built at 365 days
# and one built at 45 days answer DIFFERENT QUESTIONS and are not
# interchangeable:
#
#   365d  the market's shape. Evergreen content has had time to prove
#         itself, baselines are stable, and percentiles mean something.
#         Correct default, and the right basis for grading topics.
#    60d  what is moving now. Catches a trend while it is still a trend,
#         at the cost of a thinner sample and a baseline that shifts
#         between builds.
#    30d  breaking only. Very few videos, very noisy percentiles. Useful
#         for "is this happening right now", useless as a baseline.
#
# The stored lookback travels with the dataset so a topic collected over
# one span is never silently graded against a baseline built over
# another — that comparison is meaningless and looks fine.
LOOKBACK_PRESETS = [
    (30,  "Breaking — what is happening right now"),
    (45,  "Hot — recent movement, still thin"),
    (60,  "Current — a reasonable balance"),
    (90,  "Quarter — trend with some stability"),
    (180, "Half year — enough history for durability signals"),
    (365, "Full year — market shape, best for baselines"),
]

# Target number of time slices, whatever the lookback. Windows exist so
# collection is spread evenly across the period instead of returning
# whatever YouTube ranks highest (which skews recent). Momentum then
# compares a recent slice against a prior one — with only ONE slice
# there is nothing to compare, so short lookbacks need proportionally
# shorter slices. At a fixed 30-day slice, a 30-day lookback produced
# exactly one window and silently broke momentum.
TARGET_SEARCH_WINDOWS = 8
MIN_SEARCH_WINDOW_DAYS = 5


def window_days_for(lookback_days):
    """Slice size that yields roughly TARGET_SEARCH_WINDOWS windows."""
    if not lookback_days:
        return SEARCH_WINDOW_DAYS
    w = max(MIN_SEARCH_WINDOW_DAYS,
            int(round(lookback_days / TARGET_SEARCH_WINDOWS)))
    return min(w, SEARCH_WINDOW_DAYS)

# Bias search results toward a language (YouTube's relevanceLanguage
# parameter, ISO 639-1). On a real build, 21 of the top 100 breakouts were
# non-English despite English seeds. NOTE: this is a BIAS, not a strict
# filter — YouTube may still return other languages. None disables it.
RELEVANCE_LANGUAGE = "en"

# How many second-level suggestions each seed recurses into when depth=2.
#
# Autocomplete is free and unmetered — the only cost is wall-clock time
# at the polite inter-request delay. An earlier version imposed a total
# lookup budget and silently reduced recursion depth to stay inside it,
# which meant asking for a deep harvest and quietly getting a shallow
# one. Time spent is the user's decision, so the menu now shows the
# estimate and asks before starting instead of throttling behind their
# back.
#
# For reference, with full a-z expansion (41 lookups per seed):
#     5 seeds, no recursion   ->    205 lookups   ~1 min
#     5 seeds, recurse 8      ->  1,845 lookups  ~11 min
#    11 seeds, recurse 8      ->  4,059 lookups  ~24 min
SIGNALS_RECURSE_TOP = 8

# Maximum seed terms accepted for ONE autocomplete harvest.
#
# Autocomplete costs no quota, so the only limit is wall-clock time —
# each seed is 41 lookups at full a-z expansion, roughly 15 seconds.
# Ten seeds is about 2.5 minutes flat, or 25 with recursion, which the
# menu prices before you commit.
#
# Ten also unifies two numbers that were previously inconsistent for no
# reason: suggested seeds took the top 6 clusters while the
# clusters-only preset took the top 10.
SIGNALS_MAX_SEEDS = 10

# ============================================================
# COLLECTION QUALITY FILTERS
# ============================================================
# Diagnosed from a real "finance" build whose top-20 breakouts included
# Bengali, Hindi, Korean and Indonesian videos, a NATO press release, an
# LeBron James clip, a manifestation/law-of-attraction Short and a B2B
# SaaS ad. Roughly 4 of 20 were usable. Three independent causes:

# 1. SEARCH ORDER. Collection previously ordered by "date" INSIDE each
#    30-day window. But the windows already guarantee temporal spread, so
#    date-ordering within them adds nothing and discards relevance
#    entirely — it returns the newest loosely-matching videos rather than
#    the best-matching ones. "relevance" costs the same quota and is
#    strictly better here.
SEARCH_ORDER = "relevance"

# 2. SHORTS. YouTube search returns Shorts alongside long-form and the API
#    has no "exclude shorts" flag. Shorts are a different product: their
#    views come from the Shorts feed (algorithmic push), not from search
#    intent, so their views/hour is structurally far higher. Left in, they
#    dominate breakout rankings while telling a long-form creator nothing
#    actionable.
#    videoDuration is applied at the API level ("any"/"short"/"medium"/
#    "long"; medium = 4-20min, long = >20min). "any" keeps breadth and
#    lets the post-filter below do precise work.
COLLECT_VIDEO_DURATION = "any"
#    Post-filter: drop anything shorter than this. 180s is YouTube's own
#    Shorts ceiling. Set to None to keep everything.
EXCLUDE_SHORTS_UNDER_SECONDS = 180

# Collect this much MORE than the target, because the quality filters run
# after enrichment and remove a large slice. Measured across three real
# builds: 67%, 61% and 50% of collected videos survived — so roughly a
# third to a half is lost, almost all of it Shorts.
#
# The alternative was YouTube's videoDuration parameter, which would stop
# Shorts arriving at all. It was rejected because it accepts ONE value:
# "medium" is 4-20 minutes, so it excludes Shorts but also excludes every
# video over 20 minutes — in finance that means losing the documentaries
# and deep-dives, which are often the best content. There is no way to
# say "anything except Shorts". Over-collecting costs more quota but
# stays blind to nothing.
COLLECTION_OVERSHOOT = 1.6

# Shorts are excluded from the main dataset but SAVED, not discarded.
# They have already been paid for, and they answer a question the
# long-form data cannot: which topics are getting attention in short
# form. A topic busy in Shorts but thin in long-form is a candidate —
# people are interested, and nobody has made the full version.
#
# The inference is NOT automatic, and the report says so: Shorts views
# come from the Shorts feed rather than from search, so short-form
# attention does not prove that anyone would search for a long video on
# the same subject.
RETAIN_SHORTS = True

# 3. LANGUAGE. relevanceLanguage is a RANKING HINT, not a filter, and
#    regionCode only biases viewability — neither excludes foreign-language
#    content, which is why Bengali and Korean videos ranked in a US
#    English build. Declared language fields are frequently empty, so the
#    fallback is script detection on the title: a title that is mostly
#    non-Latin characters is not English content.
REQUIRE_ENGLISH = True
#    Minimum share of alphabetic characters that must be Latin. Emoji,
#    digits and punctuation are ignored entirely before measuring.
#    Set at 0.80 rather than a bare majority because BILINGUAL titles are
#    the common case in the content this is meant to exclude: a real
#    Bengali video titled "৭৪০ কোটির মালিক! | 740 crore bank balance | VOB"
#    is 76% Latin by character count and would survive a 0.60 cut, even
#    though it is plainly Bengali content. Substantial non-Latin script
#    present at all is the signal; a stray foreign word in an otherwise
#    English title still scores ~0.95 and passes.
MIN_LATIN_SCRIPT_RATIO = 0.80

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
# RATE-METRIC FLOORS
# ============================================================
# A ratio needs a denominator large enough to divide by. YouTube reports
# view_count as 0 on some videos while like_count survives; with views
# clipped to 1 that produced engagement rates in the thousands (one real
# build peaked at 28,620 — i.e. 2,862,000%). Below these floors the rate
# columns are set to NaN rather than to a fabricated number, because a
# video with three views has no measurable engagement rate.
#
# 50 views is where one like reads as 2% — roughly the observed median —
# so a single interaction no longer dominates the result.
MIN_VIEWS_FOR_RATE = 50

# comment_to_like_ratio divides by likes, so it needs its own floor.
MIN_LIKES_FOR_RATIO = 10

# ============================================================
# MULTI-KEY API ROTATION
# ============================================================
# .env may define up to MAX_API_KEYS keys: YOUTUBE_API_KEY_1,
# YOUTUBE_API_KEY_2, YOUTUBE_API_KEY_3 (plain YOUTUBE_API_KEY also works
# and is treated as key 1). When a key returns quotaExceeded the collector
# rotates to the next and retries the same request; when ALL keys are
# exhausted, collection stops gracefully and the pipeline continues with
# whatever has been gathered so far (partial dataset, never a crash).
MAX_API_KEYS = 12

# Assign each seed its OWN primary key plus its OWN backup, dealt by
# stride. With 12 keys, 2 reserved for grading (10 build keys) and 5 seeds:
#
#     seed 1 -> #1, #6        seed 4 -> #4, #9
#     seed 2 -> #2, #7        seed 5 -> #5, #10
#     seed 3 -> #3, #8        (#11, #12 untouched by builds)
#
# Seed 1 exhausting #1 moves to #6 — never to #2, which belongs to seed 2.
# Backups per seed = floor(build_keys / n_seeds) - 1, so this layout needs
# at least 2x as many build keys as seeds. At MAX_API_KEYS = 7 there were
# only 5 build keys for 5 seeds: one key each, zero backups, and every
# quota death cascaded onto the next seed's primary.
KEY_PER_SEED = True

# What a seed does when BOTH of its own keys are spent.
#
#   True   the seed STOPS and the build moves to the next seed, which
#          still has its own untouched pair. Isolation is real: an
#          expensive seed can only ever damage itself.
#
#   False  the seed takes whatever key is live — the legacy cascade,
#          where seed 1 walks #1 -> #2 -> #3 and starves everyone else.
#
# Cheap videos.list enrichment is exempt either way (see
# ApiKeyManager.unchained): 1 unit per 50 videos is not worth losing a
# search that already cost thousands.
SEED_CHAIN_STRICT = True

# Keys held back from niche builds for cheap operations: topic grading,
# channel teardown, refreshes. At MAX_API_KEYS = 12 this is keys #11 and
# #12. HARD by default during a build (see ALLOW_RESERVE_FALLBACK) — a
# build cannot touch them, so option 2 always has a full day's quota.
#
# Note that autocomplete harvesting (signals.py) does NOT draw on this:
# YouTube's suggest endpoint is unmetered and needs no key at all. The
# reserve exists for the metered Data API operations.
RESERVED_KEYS_FOR_TOPICS = 2

# What happens when a build exhausts every non-reserved key.
#
#   False  the build STOPS. Reserved keys stay untouched, so topic
#          grading is guaranteed quota afterwards. Safe, but it means a
#          long build can end early while two full keys sit unused.
#
#   True   the build spills into the reserved keys as a LAST RESORT,
#          printing a loud warning each time. Nothing is wasted, but a
#          big enough build can leave topic grading with nothing — which
#          is the exact failure this reservation was added to prevent.
#
# Set True for calibration runs, where you are only building niches and
# never grading topics, so protecting grading quota buys nothing. Set it
# back to False for normal mixed use.
ALLOW_RESERVE_FALLBACK = False

# Daily quota per key (Google default for the YouTube Data API).
DAILY_QUOTA_PER_KEY = 10_000

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
# Videos requested per seed. Raised from 700 to 1,300 because API calls
# return results in blocks of 50: across 13 monthly windows, 700 asks for
# 54 per window, which costs TWO calls but uses only 54 of the 100
# results those calls return. 1,300 fills exactly 100 per window — same
# quota, nearly double the data. Above 1,300 the cost jumps 50%.
#
# The risk of collecting deeper is that results past page 1 are, by
# YouTube own ranking, worse matches. That is no longer a guess:
# search_depth_diagnostic() measures it on every build and warns when
# deep results are materially less on-topic, or when windows ran dry.
PER_SEED_MIN_RESULTS = 1300

# Hard cap on how many result PAGES to take per time window, or None for
# no cap. One page is 50 results — YouTube's own best matches for that
# window. Page 2 is its next-best, and so on.
#
# Set this to 1 when you only want first-page quality. Collection then
# takes exactly 650 videos per seed (50 x 13 windows) and never digs
# past what YouTube considers the strongest matches.
#
# Worth knowing before you set it: paging DOES already stop when YouTube
# runs out — a window that returns fewer than requested has been
# exhausted, and nothing was padded. This cap is for the different case
# where results ARE available but you judge them not worth having.
#
# The trade is real in both directions. Page 2 of a broad niche is still
# perfectly good content, and refusing it halves the dataset for no
# gain. Page 2 of a thin niche is filler. search_depth_diagnostic()
# measures which case you are in on every build, so set this from
# evidence rather than in advance.
SEARCH_MAX_PAGES_PER_WINDOW = None

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
# --- Breakout score weights -------------------------------------------
# breakout_score is a weighted blend of PERCENTILES (each component is
# ranked against the niche baseline first, so all land on 0-100 and are
# comparable). Weights are renormalised over whatever components the
# dataset actually has, so setting one to 0 removes it cleanly.
#
# WHY ABSOLUTE DEMAND LEADS. An earlier split put outlier_ratio at 0.45,
# which ranked by "did this beat its own channel". That is the right
# question for "is this strategy transferable", but the wrong one for
# "is there real demand here": a channel averaging 15 views that hits
# 150 scores a 10x and outranks a video pulling 400,000. Genuine market
# demand is an absolute quantity, so views-per-hour now leads.
#
# The channel-relative view has NOT been discarded — it moved to where
# it belongs. The tiered breakout tables (brand-new / small / by-reach)
# rank by outlier_ratio within each size band, which answers "what can a
# channel my size actually do" without letting tiny-denominator noise
# contaminate the headline ranking.
BREAKOUT_DEMAND_WEIGHT = 0.65     # views_per_hour percentile — demand RATE
BREAKOUT_OUTLIER_WEIGHT = 0.25    # outlier_ratio percentile — vs own channel

# WHY THERE IS NO RAW view_count COMPONENT. One was briefly added, on the
# reasoning that views_per_hour over-rewards brand-new videos (small
# denominator) and an absolute reach term would balance the age bias.
# Measured against real data, view_count correlates 0.839 with
# channel_subs — it is overwhelmingly a proxy for how large the channel
# already is, not for how much demand the topic has. Adding it at 0.25
# pulled three 418k-773k-subscriber channels into a top-8 that otherwise
# had a median of ~29k subs. It was measuring exactly the thing the
# ranking is supposed to see past, so it was removed rather than
# down-weighted.
#
# The recency tilt in views_per_hour is left in deliberately. A video's
# views divided by its age favours what is moving NOW, which is the
# question this score exists to answer. The cost is that a strong
# evergreen video from ten months ago scores lower than its steady
# performance deserves — worth knowing when reading the list.

# breakout_velocity (= outlier_ratio / days) is a TIMING signal, not a
# demand one: it separates a video that exploded from one that
# accumulated the same overperformance slowly.
#
# It was briefly dropped entirely on the grounds that it correlates 0.704
# with outlier_ratio. That was measured wrong at the time. Regressing
# velocity on the two components actually kept gives R-squared 0.868 —
# so some of it IS unique information, and "correlated" is not the same
# as "redundant". Re-adding it at reduced weight changed the top 20 by
# exactly one video on the sample checked then — a real but small effect.
#
# RE-VERIFIED since, on a broader and more direct basis: across all 10
# real niches this project has full breakout exports for, correlation
# between outlier_ratio and breakout_velocity (percentile-ranked, the
# form actually blended) measures 0.77-0.89 — consistently higher than
# the 0.704 figure above, worth flagging as a real discrepancy rather
# than smoothing over it, though the two numbers may not be directly
# comparable (raw vs percentile-ranked, single sample vs ten). More
# decisively: zeroing BREAKOUT_VELOCITY_WEIGHT entirely and comparing the
# FULL resulting ranking (not just top-20 overlap) against the current
# weighting gives a mean Spearman correlation of 0.9963 across all 10
# niches (min 0.9947, max 0.9978) — velocity's current weight moves the
# full breakout ranking by well under half a percentage point, on every
# niche checked, not just some.
#
# CONCLUSION: the existing decision to keep a small, non-zero weight
# rather than remove it holds up under this more rigorous check — it is
# CONFIRMED, not newly justified. The weight is NOT changed as a result
# of this verification. Recorded here so the next person checking this
# doesn't have to re-run the same test from scratch, and so it's clear
# this was actually re-examined against real data rather than assumed
# still correct.
#
# Note that views_per_hour ALREADY encodes age (views divided by hours),
# which is why velocity adds so little on top — the exploded-vs-
# accumulated distinction is mostly visible there already.
BREAKOUT_VELOCITY_WEIGHT = 0.10

# --- Evidence flagging (NOT a hard floor on the score) ----------------
# Videos below this get an explicit low_evidence marker in exports.
#
# A hard floor was considered and rejected FOR THE SCORE. The score's job
# includes catching content while it is still emerging, and a fast-moving
# 3,000-view video a few hours old is exactly the early signal that is
# worth seeing. Filtering it out to avoid noise would also remove the
# thing the demand-rate weighting exists to find.
#
# A floor IS applied where it belongs: the tiered breakout tables (see
# BREAKOUT_MIN_VIEWS_LADDER), which exist for studying case studies. A
# 200-view video teaches nothing there no matter how clean its ratio.
# Here the answer is to LABEL thin evidence rather than hide it.
BREAKOUT_LOW_EVIDENCE_VIEWS = 5_000

# Demand RATE vs absolute REACH is a deliberate pairing: views_per_hour
# alone over-rewards brand-new videos with few total views (small
# denominator), while view_count alone over-rewards old ones that had
# longer to accumulate. Carrying both balances the age bias.

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
    # Packaging phrases observed polluting a real 40-cluster finance run:
    # "here exactly", "already here", "changes everything", "everything
    # changes", "always happens", "collapsed yet", "don understand",
    # "years here", "full guide" — 9 of 40 clusters were phrases like
    # these rather than topics. They recur across unrelated subjects, so
    # they group videos that have nothing in common but their title
    # construction.
    #
    # This IS a hand-maintained list and it will lag behind new
    # packaging fashions. A general mechanism was attempted — measuring
    # whether a cluster's videos share vocabulary beyond the phrase that
    # grouped them — and it was discarded because it did not
    # discriminate: real topics scored 0.20 and packaging phrases 0.25,
    # since a genuine topic's titles are just as varied ("Index Funds vs
    # ETFs", "Why Index Funds Beat Stock Picking"). The topic IS the
    # phrase. Until something better exists, curate this list.
    "exactly", "already", "everything", "changes", "change", "happens",
    "happen", "collapsed", "collapse", "understand", "yet", "here",
    "there", "guide", "full", "always", "never", "everyone", "nobody",
    "anyone", "something", "nothing", "anything", "actually", "really",
    "literally", "finally", "suddenly", "instantly",
    # Plurals and contraction fragments the tokeniser produces. "don"
    # is what "don't" becomes; "years here" and "every month" leaked
    # through because only the singular forms were listed.
    "don", "doesn", "isn", "won", "can", "years", "months", "weeks",
    "days", "hours", "every", "each", "any", "all", "both",
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

# ============================================================
# PUBLISH TIMING
# ============================================================
# published_at is UTC; publishing advice in UTC is useless, so timing is
# converted to the AUDIENCE timezone. Change this if you target elsewhere
# (e.g. "Europe/London", "Australia/Sydney", "Asia/Kolkata").
AUDIENCE_TIMEZONE = "America/New_York"

# Hours are bucketed into blocks. 24 separate hours x 7 weekdays = 168
# cells, far too thin to read from a 3,000-video niche.
#
# TESTED, NOT JUST ASSERTED: held-out validation (same method used for
# TIMING_SHRINKAGE_K) across 8 real niches, 3 random 50/50 splits each —
# does a block width's TRAIN-half estimate actually predict its TEST-half
# reality. Error fell steadily from 3h (17.63) through 4h (15.15) to 6h
# (11.77), a real, substantial improvement each step. Deliberately did
# NOT chase this further: it keeps falling all the way to one block for
# the whole day (6.90), because minimizing prediction error alone always
# rewards throwing away time-of-day information entirely — a single
# block "predicts" perfectly by saying nothing. That is not a config to
# reach for, it is the test's blind spot.
#
# Stopped at 6h specifically because the return on widening drops off
# sharply right after it (6h->8h is only a 6% gain, versus 14-22% for
# every step up to 6h) — the empirical elbow, not a round-number guess.
# It also happens to split the day into 4 natural quarters (night,
# morning, afternoon, evening), which stays genuinely actionable —
# "post in the afternoon window" is real guidance; 3-hour blocks were
# claiming a precision ("2pm-5pm, not 5pm-8pm") the data could not
# actually back up.
TIMING_HOUR_BLOCK = 6

# A bucket below this many videos is reported but flagged as unreliable
# rather than silently dropped.
TIMING_MIN_SAMPLES = 20

# Creator-size tiers for segmented timing. A brand-new channel behaves
# nothing like an established one: it has no subscriber base to notify, so
# the launch-hour velocity spike that drives timing advice for big channels
# simply doesn't exist for it.
#
# SAMPLE-SIZE REALITY (measured on a real 1,957-video finance build):
#   <100 subs  188 videos (9.6%)  -> ~23 per 3-hour block: usable
#                                  -> ~1 per weekday x hour cell: NOT usable
# So the tier split is reported at BLOCK level only, and the weekly
# hour-by-hour heatmap falls back to a wider population.
#
# SURVIVORSHIP WARNING: a sub-100-subscriber channel whose video appears in
# YouTube search results at all is already an extreme outlier. That segment
# is not "typical new channels" — it is "new channels that broke through".
# --- Statistical treatment of thin buckets -------------------------------
# A hard n>=TIMING_MIN_SAMPLES cutoff is crude: a 21-video bucket at index
# 240 still outranks a 700-video bucket at 180, which is the same error in
# a suit. Instead every bucket is SHRUNK toward the niche median (100):
#
#     shrunk = (n * observed + k * 100) / (n + k)
#
# k is the number of "virtual average videos" added to each bucket. With
# k=25, a 4-video bucket keeps ~14% of its deviation while a 700-video
# bucket keeps ~97%. Small samples must earn their extremity.
#
# TESTED, NOT JUST ASSERTED: held-out validation across 8 real niches (a
# 50/50 train/test split, repeated with 3 different random seeds) — does
# the shrunk index from the TRAIN half actually predict the TEST half's
# real median better than the raw index does? Confirmed shrinkage helps
# at all: average error 17.73 (raw) vs 16.87 (k=25). Then swept k itself
# from 5 to 150 on the same held-out setup — error bottoms out at
# k=40-60 (15.98), not at 25 (16.12). Modest gain, but real and
# reproduced consistently across all 3 seeds, not within noise. Moved
# to the middle of that empirically-best range.
TIMING_SHRINKAGE_K = 50

# Bootstrap resamples for the confidence interval on each bucket median.
#
# TESTED, NOT JUST ASSERTED: 400 was documented as "enough for a stable
# interval" without having actually been checked. Ran the same real
# bucket's data at 400/1000/4000/10000 resamples — 400 gave [49.4, 95.5],
# which drifted to [50.9, 95.5] by 4000 and stayed put at 10000. Real,
# not huge (about 1.5 points on a ~45-point-wide interval), but 400 was
# measurably short of converged. This computation is pure math, zero API
# cost — there is no real reason to under-resample it. Raised well past
# where it stopped moving.
TIMING_BOOTSTRAP_N = 4000
TIMING_CI_PERCENTILES = (5, 95)

# Optional recency weighting. Publishing rhythms drift; within a 365-day
# window the drift is modest, so this is OFF by default. When enabled,
# a video's weight is 0.5 ** (age_days / TIMING_HALFLIFE_DAYS).
TIMING_RECENCY_WEIGHTING = False
TIMING_HALFLIFE_DAYS = 120

CREATOR_SIZE_TIERS = [
    (0, 100, "new_creators"),          # <100 subs — closest to a fresh channel
    (100, 10_000, "small_creators"),   # 100-10k
    (10_000, None, "large_creators"),  # 10k+
]
# VALIDATED against 10 real niches' full breakout exports. Checked
# whether any tier goes empty or comes to dominate in a way that would
# make the split useless for some niches:
#
#     new_creators    16%-37% of videos, across all 10 niches
#     small_creators  27%-46%
#     large_creators  30%-49%
#
# No niche has an empty or near-empty tier; no tier swallows the whole
# distribution anywhere. Kept as-is — this is a case where checking
# confirmed the existing boundaries rather than finding a problem.

# Tiers for the breakout tables in topic + discovery reports. Different
# question from CREATOR_SIZE_TIERS above (which is mechanistic — "does the
# channel have a notification wave to catch?"). Here the question is
# competitive: "which channels are peers of mine, which are one rung above,
# and which win by absolute reach?"
#
# The 2k cutoff for "brand-new" was chosen deliberately: below that a
# channel almost always has no algorithmic history and no subscriber wave,
# so its videos win on topic/title/thumbnail alone — exactly what a
# creator at that size can control. 2k–100k covers channels that have some
# traction but no institutional muscle. 100k+ is a very different game.
#
# A tier is only rendered if it has at least this many videos, to avoid
# showing a "top 5" list that's really a top-2 list with three blanks.
BREAKOUT_TIER_MIN_VIDEOS = 8

# Minimum absolute views before a video can appear in a small-channel
# breakout table.
#
# WHY THIS IS NEEDED: those tables rank by outlier_ratio (views divided
# by the channel's own average), which is the right metric for "did this
# punch above its weight" but becomes unstable when the denominator is
# tiny. Observed on a real run: a channel averaging ~15 views had one
# video reach 201 views, scoring 13.8x — and it ranked ABOVE a 445,654-
# view video in the same table. Five of fifteen surfaced rows had under
# 10,000 views; three had under 2,000.
#
# Two independent reasons to floor it. Statistically, a ratio with a
# 15-view denominator is noise — one share in a group chat produces it.
# Practically, a 201-view video is not a case study regardless of how
# clean its ratio is; there is nothing to learn from it.
#
# Applied as a LADDER rather than a hard cut, because a thin niche may
# have no small-channel videos above the top rung. The renderer walks
# down until enough videos qualify and reports which floor it used, so
# a relaxed threshold is visible rather than silent.
BREAKOUT_MIN_VIEWS_LADDER = [10_000, 5_000, 2_000, 0]
# VALIDATED against 10 real niches. Checked how far down the ladder each
# niche actually needs to go to clear BREAKOUT_TIER_MIN_VIDEOS (8):
#
#     every one of the 10 niches clears the TOP rung (>=10,000 views)
#     with at least 27 qualifying small-channel videos (max 124) — the
#     ladder never once needed to relax to 5k, 2k, or 0 on real data.
#
# So the top floor is neither too strict (every niche clears it) nor
# doing nothing (it is the floor actually used everywhere checked). The
# lower rungs remain as an untested safety net for a thinner niche than
# any of the 10 calibrated here — kept, not verified in the sense of
# having been exercised, since no real niche so far has needed them.

# ============================================================
# BREAKOUT ERAS
# ============================================================
# Breakout ranking is age-biased and always has been. views_per_hour is
# 65% of the score and it divides by age, so a 3-day-old video divides by
# 72 hours while a 300-day-old one divides by 7,200. Measured on real
# data: videos under 30 days old had a median breakout score of 89.1 and
# took 21 of the top 50; videos over 180 days scored 26.3 and took 2. The
# single biggest video in the whole dataset — 789,008 views — did not
# appear anywhere, because it was old.
#
# So the list was really "recent videos" wearing a "breakouts" label.
#
# The fix is DISJOINT eras, each ranked against its own contemporaries.
# Nested windows (last 90 / last 180 / last 365) do NOT work: every one
# of them contains the recent videos, so all three produce an identical
# top ten. Verified.
#
# Percentiles are computed WITHIN each era rather than against the whole
# niche, which is the entire point — a video should compete with others
# of its own age, not with ones that had a ten-fold arithmetic advantage.
BREAKOUT_ERAS = [
    (0,   90,  "0-90d",     "Winning now"),
    (90,  180, "90-180d",   "Won three to six months ago"),
    (180, 365, "180-365d",  "Won six to twelve months ago"),
]

# An era below this many videos is skipped rather than ranked — a "top
# 20" drawn from 25 videos is just the list, sorted.
ERA_MIN_VIDEOS = 25

# ============================================================
# MULTI-WINDOW TOPIC GRADING
# ============================================================
# Grading a topic at several windows at once answers a question one
# grade cannot: is this strong RECENTLY, or strong ACROSS THE YEAR?
# A topic scoring well at 90d and poorly at 365d is newly hot; one
# scoring well at both is durable.
#
# Both sides are always cut to the same window. Grading is percentile-
# based, so a 90-day topic against a 365-day baseline compares two
# different populations — the topic looks artificially fresh and
# nothing in the output shows it.
#
# Windows are NESTED here, unlike breakout eras. That is correct for
# grading: "how does this topic look over the last 90 days" is a real
# question, and the comparison is against the niche over the same 90
# days, so the age bias cancels on both sides.
TOPIC_GRADE_WINDOWS = [30, 90, 180, 365]

# Below this many videos on either side, a window is skipped rather
# than graded. Percentiles from a few dozen videos swing on one lucky
# upload.
TOPIC_GRADE_MIN_VIDEOS = 80
BREAKOUT_TIERS = [
    (0,       2_000,   "brand_new",       "Brand-New Breakouts (<2k subs)"),
    (2_000,   100_000, "small",           "Small-Channel Breakouts (2k–100k subs)"),
    (100_000, None,    "top_by_reach",    "Top Videos (by views/hour)"),
]


# ============================================================
# SEO / PACKAGING REPORT
# ============================================================
# Terms are ranked by LIFT (how much more often they appear among breakout
# videos than among videos generally), never by raw frequency: the most
# common tag in a finance niche is "finance", which is vocabulary rather
# than advantage. Minimum support stops a term riding on two lucky videos.
SEO_BREAKOUT_PERCENTILE = 90   # top 10% counts as "breakout" for lift
SEO_MIN_TAG_SUPPORT = 4        # a term needs this many videos to be ranked
SEO_TOP_N = 40                 # rows kept per lift table

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
    "thumbnail_url",
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
    "search.list":        100,
    "videos.list":        1,
    "channels.list":      1,
    "playlistItems.list": 1,
}

# ============================================================
# CHANNEL TEARDOWN
# ============================================================
# Studying one channel is ~1000x cheaper than building a niche, because a
# channel's whole catalogue lives in its uploads playlist and
# playlistItems.list costs 1 unit per 50 videos while search.list costs
# 100 per call. A 300-video teardown runs about 13 units against the
# 13,000 a niche build spends.
#
# Cap on how many of a channel's videos to pull. Most channels worth
# studying have well under this; the limit exists so pointing the tool at
# a 5,000-video news channel doesn't silently spend an afternoon.
CHANNEL_MAX_VIDEOS = 600

# A channel's own breakouts: videos beating ITS OWN median by this much.
# Lower than the niche-level threshold (3.0) because a single channel's
# output is far more homogeneous than a whole niche's — 2x above your own
# median is a genuine standout when everything shares one creator, one
# format and one audience.
CHANNEL_BREAKOUT_RATIO = 2.0

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
# AUDITED against 11 real niches (cross-niche pooled baseline) — weights
# left UNCHANGED, and here is why, since "we checked" is worth recording
# the same way a fix would be.
#
# Discrimination (how much each group's score varies across niches):
#     consumption            std 4.9   range 14.9  (55.4-70.3)
#     engagement_velocity    std 8.1   range 24.5  (50.4-74.9)
#     engagement_quality     std 10.3  range 30.5  (50.2-80.7)
#
# consumption discriminates least despite carrying the second-highest
# weight — which is the same SHAPE of finding that justified reweighting
# Tier 2's supply pillar. It was deliberately NOT treated the same way
# here. Supply's flat metrics (uploads_per_creator, content_freshness)
# had no economic reason to expect flatness and were plausibly just
# under-informative. Consumption is different: it is the single most
# direct measure of raw demand this pillar has, and its lower spread
# across these particular 11 niches is at least as plausibly "these 11
# niches happen to have similar baseline viewing appetite while engagement
# style differs more" as it is "consumption matters less." Reweighting by
# variance here would repeat the exact variance-is-not-importance mistake
# already caught once this project. No construct-validity concern was
# found for any of the three groups either (unlike verdict.py's effort
# dimension) — all three are fairly direct operationalizations of what
# they claim to measure.
#
# Redundancy check: correlation between the three group scores across the
# 11 niches — all weak (max |r| = 0.52, consumption vs engagement_quality;
# engagement_velocity correlates only 0.12 and -0.20 with the two groups
# it is structurally built from). Despite being a literal product of a
# consumption member and a quality member, engagement_velocity is not
# tracking either closely in practice — real evidence it is capturing
# something the other two do not, on this data.
#
# Conclusion: no dead metric, no strong redundancy. Nothing here shows
# the pathology that justified the supply reweight. Left as-is.

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

# --- Momentum: native signed log scale ---------------------------------
# signed = MOMENTUM_LOG_SCALE * log10(observed / reference), clamped +-100.
# 100 makes one order of magnitude exactly fill the scale: 10x -> +100,
# 10x decline -> -100, doubling -> +30, halving -> -30. The old effective
# scale was 36, which meant a DOUBLING scored +10.8 and a HALVING -10.8 —
# both read as "steady", and the -100..+100 range was almost entirely
# unused because even a 10x collapse only reached -36.
MOMENTUM_LOG_SCALE = 100.0

# Weights across momentum's components. demand-vs-supply growth is the
# core signal (is attention outgrowing competition?); recent breakout
# frequency is the most direct "heating up right now" read; evergreen
# persistence measures durability, which matters but is closer to a
# quality-of-market trait than to momentum itself.
# Momentum answers ONE question: is this market heating up or cooling
# down? Every component must speak to that.
#
# evergreen_persistence was removed at weight 0.20. It measures whether
# OLD videos still pull views, which is durability, not direction — and
# on real data it actively penalised the thing momentum exists to find.
# A topic that just took off necessarily has weak old videos, because
# they were published before anyone cared. Measured on a real
# memory-stocks topic: momentum +68.2 without it, +34.6 with it. It
# halved the score of a topic that was genuinely exploding, and because
# _signed_log clamps at -100 it contributed a FIXED -20 to any topic
# that saturated it — a constant, not a signal.
#
# The metric itself is sound and still computed; it is reported as a
# content-durability diagnostic instead of being folded into a
# directional score. Weights renormalise over whatever is present, so
# these two now split 0.625 / 0.375.
MOMENTUM_COMPONENT_WEIGHTS = {
    "demand_vs_supply_growth":   0.50,
    "recent_breakout_frequency": 0.30,
}

# --- Content Supply (niche-level, scored directionally: topic vs niche) ---
# Higher score = MORE supply / saturation (worse for opportunity). Only
# sample-size-invariant supply metrics are scored (see insights.supply_summary).
#
# A NOTE ON WHAT "SUPPLY" MEANS HERE
# ------------------------------------
# Two different things are bundled under this one label: content_freshness,
# upload_acceleration, and uploads_per_creator measure literal supply — how
# much new content is entering the market. median_subscribers and
# channel_video_count (see grader._supply_pillar) measure something else —
# incumbent SIZE and catalogue depth, i.e. how hard the existing players are
# to outrank. That is a real and useful signal, but it is closer to
# "competitive barrier strength" than to "supply" in the economic sense of
# quantity being produced. Calling both "supply" was convenient but
# imprecise; flagged explicitly rather than silently relied on, since a
# reviewer asking "does median_subscribers really measure supply" deserves
# an honest "not exactly — here is what it measures instead."
SUPPLY_METRICS = {
    "content_freshness":   "higher",   # more fresh content = more active competition
    "upload_acceleration": "higher",   # supply speeding up = more pressure
    "uploads_per_creator": "higher",   # creators posting more = more pressure
    "median_subscribers":  "higher",   # bigger incumbents = harder to break in
}
# median_subscribers is read from the niche competition_summary; the rest
# from supply_summary. Grader knows where each lives.
#
# WEIGHTS — not all five discriminate equally
# --------------------------------------------
# Measured across eleven real niches (their raw quantities, before
# scoring), coefficient of variation between niches:
#
#     median_subscribers    CV 67%   <- varies enormously (86 to 1,180)
#     channel_video_count   CV 57%   <- varies enormously (52 to 241)
#     uploads_per_creator   CV 18%   <- nearly identical everywhere
#     content_freshness     CV 15%   <- nearly identical everywhere
#     upload_acceleration   CV 14%   <- nearly identical everywhere
#
# A first pass weighted upload_acceleration at 0.20 — second-highest —
# on the reasoning that it is "the one change signal in supply", without
# actually checking its CV. Measured afterward: 14%, as flat as
# content_freshness. By this file's own stated logic that metric should
# have been weighted like the other flat ones, not like the informative
# ones. Corrected below. Worth naming rather than quietly fixing: it is
# the exact mistake this weighting scheme exists to catch, just
# committed on this scheme's own second metric before anyone checked it.
#
# WHAT CV DOES AND DOES NOT JUSTIFY
# -----------------------------------
# High cross-niche variance is not the same claim as "this metric matters
# economically" — a metric could vary a lot for reasons unrelated to
# competitive pressure. What CV DOES tell you: which inputs are actually
# separating YOUR niches from each other right now, versus which ones
# read the same everywhere and can only dilute whatever the separating
# ones are saying. For the stated goal here — make the supply pillar
# differentiate niches that are, by every raw measure, genuinely
# differently supplied — that is the right question to be asking, even
# though it is a calibration heuristic and not a causal model. Averaged
# flat, the three near-constant metrics were outvoting the two that
# actually carry signal, which is why supply's range collapsed to
# 47.6-54.7 across all eleven niches while demand ranged 54-68.
#
# Both surviving high-weight metrics (median_subscribers,
# channel_video_count) also have a direct causal story independent of
# their CV — bigger incumbents and deeper catalogues are harder to
# outrank — so their weight rests on two legs, not one. The three
# downweighted metrics are not zeroed; a niche with a genuinely unusual
# upload cadence still registers, just without three flat metrics
# diluting the two that vary.
#
# PROVISIONAL: from eleven US-English niches at 365d. The CV numbers are
# the evidence; the exact weights are a reasonable reading of it, to be
# re-checked as the library grows.
SUPPLY_METRIC_WEIGHTS = {
    "median_subscribers":  0.42,
    "channel_video_count": 0.32,   # incumbent catalogue depth (see note below)
    "upload_acceleration": 0.10,
    "uploads_per_creator": 0.10,
    "content_freshness":   0.06,
}
# Note: channel_video_count is added to the supply signal here even
# though it is not in SUPPLY_METRICS above — it is read directly from the
# per-video data in _supply_scores, because it is the second-strongest
# discriminator (CV 57%) and captures "how established are the channels
# already here", which is core to contestability.

# Momentum needs enough videos in its recent/prior windows to be meaningful.
# Below this count in either window, momentum abstains rather than emitting a
# number computed from near-empty buckets.
MOMENTUM_MIN_WINDOW_VIDEOS = 15

# Momentum's demand-growth term blends the window MEDIAN with its P90.
# Median alone hides an emerging explosion (one 5000-VPH video among fifties
# doesn't move a median at all) — which is precisely what momentum should
# detect. Mirrors DEMAND_TAIL_WEIGHT in the grader.
MOMENTUM_TAIL_WEIGHT = 0.40

# How much demand_vs_supply_growth contributes at the WORST end of the
# confidence scale — i.e. when the bootstrap interval is centred exactly
# on 1.0, giving no evidence of direction either way.
#
# A first version used this as a binary switch (full weight if the CI
# cleared 1.0, this floor otherwise). That treats a [1.09, 2.87] interval
# and a [0.99, 2.24] interval as opposites, despite the underlying
# evidence being nearly identical — both barely clear or barely miss the
# same line. Replaced with a continuous confidence score (0..1, see
# insights.momentum_summary's demand_growth_confidence) that scales
# smoothly: this constant is now the floor of that scale, not a fixed
# multiplier. A dead-centre interval still contributes this much, a
# borderline one somewhat more, a tight and clearly-directional one
# scales up toward full strength.
#
# Measured across eleven real niches: SIX had a binary-inconclusive
# interval (ancient_history, excel_productivity, home_espresso,
# homelab_networking, personal_psychology, space_exploration,
# world_war_two) at 45/90-day windows — sample size was ruled out as the
# cause (220-470 videos per window, well above
# MOMENTUM_MIN_WINDOW_VIDEOS); the uncertainty is real, not thin data.
#
# The floor itself (0.15) is a judgement call, not a derived value —
# named as such rather than implied to be statistically produced. It is
# chosen to keep a faint signal (a dead-centre interval with a large
# point estimate still nudges the score slightly) without a dead-centre
# interval driving the score as hard as a confidently-directional one.
MOMENTUM_INCONCLUSIVE_WEIGHT = 0.15

# --- Opportunity derivation ---
# opportunity = 50 + (demand_score - supply_score)/2, then nudged by momentum.
# Momentum's pull on the derived opportunity score. Applied to momentum's
# SIGNED value (-100..+100), so 0.15 gives the same +-15 point maximum
# swing the old 0.30-on-(score-50) form did — (score-50) spanned only
# +-50, while signed spans +-100. Leaving it at 0.30 after the switch to
# signed input would have silently DOUBLED momentum's influence.
OPPORTUNITY_MOMENTUM_INFLUENCE = 0.15   # ±15 pts at signed momentum ±100 (theoretical max, rarely approached — see audit below)
# AUDITED against 11 real niches, post the confidence-weighting fix (see
# MOMENTUM_INCONCLUSIVE_WEIGHT) — weight left UNCHANGED.
#
# The "±15 pts at ±100" comment above describes the formula's behaviour
# at momentum's theoretical extreme, not what real data produces. Worth
# separating those: even before confidence-weighting existed, real
# momentum on these 11 niches topped out near ±44, nowhere close to the
# ±100 that would need a 10x swing in demand growth vs supply growth.
# After confidence-weighting, the real range is narrower still: -21.1 to
# +20.0, so momentum's ACTUAL contribution to the opportunity score
# currently ranges -3.2 to +3.0 points, not the ±15 the constant's
# comment implies as a design target.
#
# Checked whether that real contribution is doing meaningful, appropriately
# -sized work rather than either dominating or being negligible:
#
#     imbalance term (demand-supply)/2 spread: 11.5 points
#     momentum contribution spread:             6.2 points
#
# Momentum moves the score by roughly half as much as the demand/supply
# imbalance does — present and noticeable (it is the deciding factor
# between several close-ranked niches in the current 11-niche pool) but
# not dominant. No redundancy either: momentum correlates weakly with
# demand (r=-0.09), supply (r=+0.27), and their imbalance (r=-0.22) — it
# is measuring something the other two pillars are not.
#
# Conclusion: no evidence the 0.15 weight is currently wrong. It was
# never re-derived from real data — it remains a chosen constant, not a
# measured one — but nothing found here argues for moving it.

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
# Grade ladder for 0-100 pillar/opportunity scores. Nine bands, symmetric
# about 50 (the neutral point for demand/supply/opportunity, just as 0 is
# for momentum): offsets are +-10, +-17, +-25, +-35. Mirrors the momentum
# ladder's shape so a "B" means a comparable distance from neutral on
# either scale.
GRADE_THRESHOLDS = {
    "A+": 85,
    "A":  75,
    "B+": 67,
    "B":  60,
    "C":  40,     # neutral band: 50 +- 10
    "C-": 33,
    "D":  25,
    "D-": 15,
    # below 15 → F
}

# Momentum-specific grade bands, in SIGNED units (-100..+100, 0=breakeven).
#
# Demand/supply's "neutral" point also sits at score=50 (GRADE_THRESHOLDS'
# C cutoff), but they're computed from the FULL sample (hundreds+ videos),
# so sampling noise around that point is small. Momentum compares two
# 45/90-day WINDOWS instead — a fraction of the data — so its noise around
# breakeven is much larger. Sharing the same 15-point-wide C band as
# demand/supply meant a genuinely neutral niche (real signed change ~0)
# would land just above or just below the C/D line on pure chance almost
# half the time — exactly the "even breakeven can't get a fair grade"
# problem. This band is wider and explicitly centred on 0, so real noise
# has room to move without crossing a letter-grade line.
# --- Momentum grade ladder, in SIGNED units (-100..+100, 0 = breakeven) ---
#
# Nine bands, SYMMETRIC about 0: the thresholds are ±10, ±25, ±45, ±70, so
# a given magnitude of growth and the same magnitude of decline receive
# mirror-image grades. That symmetry is only possible because the scale is
# logarithmic — on a linear scale "doubled" and "halved" are +1.0 and -0.5,
# so any symmetric band layout would silently punish declines less than it
# rewards growth.
#
# Because the underlying value is 100*log10(ratio), EQUALLY SPACED bands
# are EQUAL MULTIPLES. Each threshold is a plain-English quantity:
#     +-10  ->  1.26x  /  0.79x     (+-26% — noise)
#     +-25  ->  1.78x  /  0.56x     (~+-2x territory begins)
#     +-45  ->  2.82x  /  0.35x
#     +-70  ->  5.01x  /  0.20x
# So a DOUBLING (signed +30) lands in B with real headroom above it, rather
# than instantly topping the scale — the previous layout put A at +30, which
# meant everything from a doubling to a 10x surge collapsed into one letter
# and the whole +30..+100 stretch did no work.
MOMENTUM_GRADE_THRESHOLDS = {
    "A+": 70,
    "A":  45,
    "B+": 25,
    "B":  10,
    "C": -10,     # breakeven band: within about +-26%
    "C-": -25,
    "D": -45,
    "D-": -70,
    # below -70 → F
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
