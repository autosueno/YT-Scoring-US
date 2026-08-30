"""
verdict.py — which niche is actually worth breaking into.

WHY THIS IS NOT compare.py
--------------------------
compare.py answers "how do these niches differ" from stored metadata:
three medians, three P90s, no quota, instant. That is the right tool for
a glance, and it stays as it is.

It cannot answer "which should I enter", because the deciding facts are
not in the metadata. Whether a newcomer can rank, whether the bottom of
the market is amateurs or failed professionals, whether a video keeps
earning after three months — all of those need the JOINT distribution
(views crossed with channel size, views crossed with age), and metadata
stores only marginals. So this module reads videos.csv. Still no quota,
just slower.

WHY A RATIO OF PERCENTILES WAS NOT ENOUGH
-----------------------------------------
Measured on two real builds of near-identical size:

    metric                    ancient_history   memory_stocks
    P90 / median spread            1,307x            43x
    top 1% share of all views       40.1%           81.8%
    Gini                            0.917           0.976

The two measures RANK THE NICHES IN OPPOSITE ORDER. P90/median is
anchored at P90, so it is structurally blind to everything above it —
and memory_stocks keeps its concentration up there (its largest video
sits 846x above P90, against 40x for ancient_history). Meanwhile
ancient_history's enormous ratio comes from its FLOOR: half its videos
are under 226 views, uploaded by channels whose 10th percentile has four
subscribers.

Both niches were labelled "winner-take-all". They need opposite advice.
So this module never reduces a market to one ratio. It measures the top
and the bottom separately and reports when they disagree.

WHAT IS MEASURED VS WHAT IS DECLARED
------------------------------------
Measured from the data: entry access, floor composition, durability,
production effort, ceiling, concentration.

Declared, because YouTube's API cannot see it: RPM. It lives in
rpm_rates.csv next to main.py, editable in Excel. It used to drive
part of the composite score — every ranking was computed three times
(at the low, mid and high end of each niche's RPM band) and a flip
was reported as such. That mechanism was removed: RPM is now shown
alongside the result as information only, not as a scoring input.
See score_profile()'s docstring for the full reasoning.

THE SCORE ANCHORS ARE PROVISIONAL
---------------------------------
The 0-100 conversions below use absolute anchors chosen by reasoning,
exactly the sin compare.py documents about its own spread thresholds.
They are gathered in one block so they can be replaced with observed
percentiles once enough niches exist. Until then the DIMENSION RANKINGS
are trustworthy (they are ordinal, and ordering does not depend on where
the anchors sit) while the absolute scores are not.
"""

import math
import os

import numpy as np
import pandas as pd

from config import EXPORTS_DIR
from paths import split_window, videos_path, safe_name
import compare as _compare
import calibration


# ============================================================
# PROVISIONAL ANCHORS — replace with observed percentiles
# ============================================================
# Each pair maps a raw measurement onto 0-100. The LEFT value scores 0,
# the RIGHT scores 100, linearly between, clamped outside.

ANCHORS = {
    # Share of top-decile videos from channels under 10k subs. Zero means
    # the algorithm never surfaces newcomers here, whatever their quality.
    #
    # PROVISIONAL-v2, from 11 real niches. First version anchored the
    # "excellent" end at 30% underdog access — invented, and wildly
    # optimistic: measured across all 11 niches, the highest was 11.2%
    # (personal_psychology) and NONE reached even half of 30%. Every
    # niche's underdog component was scoring in a narrow, low, low-
    # information band regardless of real differences between them —
    # the exact failure mode Tier 1's spread label had. Anchor now
    # contains the observed range (1.7%-11.2%) with ~30% headroom above
    # the real max for niches not yet in the library.
    "underdog_share": (0.00, 0.145),
    # Share of the dead zone posted by channels under 100 subs. High means
    # the floor is hobbyists you outrank by being competent, not good
    # creators who tried and failed.
    #
    # PROVISIONAL-v2. Observed range 32.1%-60.1% across 11 niches; old
    # anchor's floor (10%) sat so far below the real minimum that no
    # niche ever scored near the bottom of this component either.
    "amateur_floor": (0.27, 0.65),
    # Median views/hour of videos older than 6 months divided by the same
    # for videos under 2 months. Near 1.0 means the back catalogue keeps
    # earning; near 0 means every video is dead within weeks. ABOVE 1.0
    # means old videos outperform new ones — a real, measured finding on
    # two of eleven niches, not a modelling artefact.
    #
    # DURABILITY ANCHOR — CHANGED FROM LOG-RATIO TO PLAIN LINEAR.
    #
    # The old metric (median(old vph) / median(young vph)) spanned two
    # and a half orders of magnitude and needed a log10 transform to
    # avoid clamping. It also turned out to be UNSTABLE — see the
    # durability computation in deep_profile for the full finding: a
    # split-half stability test showed nearly 6x swings between two
    # random halves of the SAME niche's data, on cohorts with 500-1200+
    # videos each, not a small-sample problem. Rebuilt as a bounded
    # percentile-rank question (what percentile of the young cohort's
    # performance does the old cohort's median represent?), confirmed
    # stable across 5 random seeds. Naturally bounded 0-100 — no log
    # transform needed, it never had the orders-of-magnitude spread
    # that made one necessary for the old formula.
    #
    # Observed range across 17 real niches: 14.7 (memory_stocks — old
    # stock picks going stale, matches independent findings from the
    # earlier Tier 4 work) to 61.7 (personal_finance_basics — evergreen
    # budgeting advice, also matches intuition). Margin added both ends.
    "durability_pct": (8.0, 68.0),
    # Median duration of top-decile videos, in seconds. Inverted: shorter
    # winners are cheaper to produce. Read as a PRODUCTION-LENGTH proxy —
    # length correlates with effort on average but is not effort itself,
    # and past ~8 minutes duration also feeds ad revenue directly (more
    # mid-rolls), so it is not a pure cost signal either.
    #
    # PROVISIONAL-v2. Observed range 871s (credit_cards, 14.5min) to
    # 2412s (world_war_two, 40min). The old anchor's "excellent" end
    # (300s / 5min) was never reached by any niche's actual winners —
    # nothing here wins short. Its "worst" end (1800s / 30min) was
    # already exceeded by 3 of 11 niches, clamping them to an identical
    # floor score despite a real 25% duration difference between them
    # (1933s vs 2412s). Anchor now contains the observed range with
    # margin.
    "effort_seconds": (2612, 771),
    # Revenue per successful video (top-decile median views x RPM/1000),
    # log10 scaled because it spans orders of magnitude.
    #
    # REWARD ANCHOR — CHANGED FROM REVENUE TO RAW REACH.
    #
    # Previously log10(revenue), where revenue = p90_views * RPM / 1000.
    # Removed by explicit decision: RPM is a declared, editable estimate
    # (rpm_rates.csv) with no ground truth behind it, and letting it
    # drive a WEIGHTED SCORE meant an uncertain, self-reported number
    # was silently deciding part of a niche's ranking. RPM is still
    # looked up and reported — see match_rpm / score_profile's
    # "probable_rpm" field — but only as information shown alongside
    # the result, never as an input the composite score depends on.
    #
    # log_p90_views replaces it: same log-scale reasoning (reach spans
    # orders of magnitude, same as revenue did), but built on the one
    # number in this pillar that is actually MEASURED — a niche's own
    # top-decile view count — rather than that measurement multiplied
    # by a guess.
    #
    # Derived the same way log_revenue was: real observed range across
    # every niche on hand (17, not 11 — this project has grown since
    # log_revenue was last set), plus margin on both ends so a future
    # niche outside today's observed range doesn't immediately clamp.
    # Observed log10(p90_views): 3.93 (small_business) to 5.89
    # (unsolved_mysteries). Anchor set to (3.7, 6.2) — roughly the same
    # proportional margin log_revenue used (about 0.2 below the low end,
    # 0.3 above the high end).
    "log_p90_views": (3.7, 6.2),
    # "reliability" is NOT listed here — it has no fixed anchor pair.
    # It reuses calibration.py's already-measured, SIZE-AWARE P90/P75
    # spread RANGE (calibration.observed_range_for — large markets
    # 8.45x-13.53x, small markets 4.31x-13.53x; large and small are NOT
    # the same scale) rather than inventing a second, parallel
    # calibration of the same underlying ratio Tier 1 already derived
    # from these same 11 niches. NOTE: this deliberately uses
    # observed_range_for, not thresholds_for — the latter returns
    # tercile LABEL boundaries, and using those as a continuous scoring
    # anchor was tried first and produced 7 of 11 niches clamped at or
    # near 0/100, because a tercile cut is designed so a third of the
    # population sits past it. See _reliability_score below.
}

WEIGHTS = {
    "entry": 0.32,          # can a newcomer rank at all — highest, well-tested
    "durability": 0.24,     # does the catalogue compound — RESTORED, see below
    "reliability": 0.20,    # outcome-concentration proxy
    "reward": 0.16,         # reach of a winning video (see log_p90_views above)
    "effort": 0.08,         # cost per attempt — weakest proxy, no fix attempted
}
# THE FULL ARC ON DURABILITY VS RELIABILITY, kept in full because the
# ranking flipped twice and each flip had a real, different reason —
# worth being able to see why, not just the current numbers.
#
# 1. Originally: durability above reliability, on construct-validity
#    grounds alone. "Do old videos still earn" is a direct, concrete
#    question. Reliability's P90/P75 compression ratio is a PROXY for
#    "are outcomes predictable" with a real inferential gap — a narrow
#    ratio could mean genuine predictability, or just a compressed
#    distribution for unrelated reasons. Nothing in this project had
#    outcome data to tell those apart. That reasoning was never wrong,
#    but it was also never TESTED — it was a confidence judgment, not a
#    measurement.
#
# 2. Reordered on request: reliability moved above durability as an
#    explicit product-priority decision, independent of the validity
#    gap above. Flagged honestly at the time as a real tension, not
#    resolved by the reorder.
#
# 3. Then durability was ACTUALLY TESTED, for the first time, with a
#    split-half stability check — score each random half of the same
#    niche's data independently, see if the two halves agree. They did
#    not: swings up to nearly 6x between halves of the same dataset,
#    on cohorts of 500-1200+ videos each. Not a small-sample problem —
#    the root cause was views_per_hour's extreme skew (CV 3-5) making
#    an unbounded ratio of two medians unstable regardless of sample
#    size. This directly contradicted step 1's confidence judgment:
#    durability LOOKED more trustworthy by reasoning, and turned out to
#    be the least stable dimension of the three once actually checked.
#
# 4. Rebuilt, not just downweighted. Root-caused to the ratio being
#    unbounded; replaced with a bounded percentile-rank formulation
#    (see deep_profile's durability computation and
#    ANCHORS["durability_pct"] for the full mechanism). Re-tested the
#    same way: stable across 5 random seeds (spread 1.6-2.7x between
#    halves), landing in the same range as reliability's own measured
#    stability (2.6x) and reward's (2.7x) — not just "less unstable,"
#    genuinely comparable to the two dimensions already trusted more.
#
# 5. This weighting. With durability's instability actually fixed and
#    confirmed rather than assumed, its original construct-validity
#    edge over reliability applies again — "do old videos still earn"
#    is a direct measurement; reliability's compression ratio remains
#    an unvalidated proxy for predictability, stability of the
#    STATISTIC notwithstanding. Restored above reliability accordingly.
#    Entry stays highest, unchanged and independently well-tested.
#    Reliability keeps a real, substantial weight (0.20, not returned
#    to its original 0.10) because its own construct-validity concern
#    was never about the ratio being unstable — it was always about
#    whether the ratio means what it claims to, which testing stability
#    cannot answer either way. That question is unchanged by anything
#    above and still needs real outcome data (repeat builds, actual
#    channel results) to settle.
#
# reward dropped from 0.29 to 0.16 as a direct consequence of removing
# RPM from scoring (see log_p90_views above) — a raw-reach measure is a
# real, still-useful signal, but it answers a narrower question than
# "what a win is worth" did, so it no longer earns a top-two weight.
#
# Checked with weight_sensitivity() after this reweighting: see that
# function's docstring for what it tests and why.
#
# effort's weight was cut from 0.15 to 0.09 when this reasoning was
# first written (redistributed proportionally to the other three, which
# kept their relative ratio to each other). It has since moved again, to
# 0.08, purely as a side effect of reliability's later reduction
# returning weight to the other dimensions — not a second, independent
# decision about effort's confidence. The reasoning below is about the
# 0.15->0.09 cut specifically and still applies to why effort is
# discounted at all; it does not need to be re-derived for every small
# arithmetic shift since.
#
# THE REASON IS DELIBERATELY NOT DISCRIMINATIVE POWER.
# ------------------------------------------------------
# A prior recalibration (see config.SUPPLY_METRIC_WEIGHTS) correctly
# reweighted metrics by how much they varied across niches — but a
# reviewer of that work flagged, correctly, that "varies a lot" and
# "matters" are different claims, and that using one to justify the
# other is a design decision, not a statistical conclusion. Re-checked
# here on purpose: post anchor-fix, all four dimensions now discriminate
# reasonably well (std 14-28 across 11 niches) — effort is NOT flat the
# way two of Tier 2's supply metrics were. So the CV-based argument that
# justified the supply reweight does not apply here, and is not the
# reason for this change.
#
# The actual reason: `effort_seconds` measures video DURATION, not
# effort. A 40-minute podcast can be cheaper to produce than an 8-minute
# edited video; the metric was flagged as a weak proxy for its own
# construct when it was first proposed, and calibrating its anchors
# carefully (which was done — see ANCHORS below) does not fix that
# problem, it just makes an admittedly-approximate measurement more
# precisely wrong. Lowering its weight reflects lower CONFIDENCE that
# the input measures what it claims, which is a different and
# independent justification from "it doesn't vary enough."
#
# Checked this wasn't purely cosmetic: a sensitivity test across 11 real
# niches found that dropping effort's weight to ZERO moved the ranking
# only mildly (Spearman 0.92 vs the current weighting, one niche shifting
# 2 places) — so this change is low-risk to the ranking either way. That
# result is supporting evidence, not the reason for the change.
#
# entry/reward/durability's RELATIVE weights to each other are untouched
# — no equivalent construct-validity concern was found for any of them,
# and no evidence justified moving them. See verdict.weight_sensitivity()
# for the robustness check this rests on, re-runnable as the library
# grows.

# A video below this many lifetime views contributed nothing. Absolute
# rather than relative on purpose: a percentile floor moves with the
# niche, which would make every niche look identically healthy.
VIABLE_VIEWS = 1_000

# Channel-size boundaries. SMALL matches metrics.small_creator so the two
# modules cannot drift apart; TINY marks hobbyist uploads.
SMALL_CHANNEL_SUBS = 10_000
TINY_CHANNEL_SUBS = 100

# Age cohorts for the durability test, in hours.
OLD_COHORT_HOURS = 182 * 24
YOUNG_COHORT_HOURS = 60 * 24

# Dimension scores this far apart count as a genuine disagreement, and
# the report leads with the trade-off instead of a ranking.
CONFLICT_THRESHOLD = 25.0

RPM_FILENAME = "rpm_rates.csv"


# ============================================================
# RPM TABLE
# ============================================================

def load_rpm_table(path=None):
    """
    Read rpm_rates.csv from beside main.py. Returns None if absent —
    the report then runs without any revenue dimension rather than
    inventing rates, and says so on its face.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                RPM_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    needed = {"niche_key", "rpm_low", "rpm_mid", "rpm_high"}
    if not needed.issubset(df.columns):
        return None
    return df


def match_rpm(niche_name, table):
    """
    Map a niche name onto an RPM row via match_keywords.

    Longest keyword wins, so "semiconductor" beats a bare "tech" when
    both appear. Falls back to the row keyed 'default', and if that is
    missing too, to a flat $2/4/8 band with the match recorded as
    "none" so the report can flag it.
    """
    fallback = {"niche_key": "none", "label": "Unmatched",
                "rpm_low": 2.0, "rpm_mid": 4.0, "rpm_high": 8.0,
                "limited_ads_risk": "unknown", "confidence": "none",
                "source_note": "No RPM row matched this niche name.",
                "matched_on": None}
    if table is None:
        return fallback

    hay = niche_name.lower().replace("_", " ")
    best, best_len = None, 0
    for row in table.to_dict("records"):
        kws = str(row.get("match_keywords") or "")
        for kw in [k.strip().lower() for k in kws.split("|") if k.strip()]:
            if kw in hay and len(kw) > best_len:
                best, best_len = dict(row), len(kw)
                best["matched_on"] = kw
    if best:
        return best

    default = table[table["niche_key"] == "default"]
    if not default.empty:
        d = default.iloc[0].to_dict()
        d["matched_on"] = None
        return d
    return fallback


# ============================================================
# DEEP PROFILE — one niche, read from videos.csv
# ============================================================

def _gini(values):
    """Gini over a non-negative array. 0 = perfectly even, 1 = one taker."""
    v = np.sort(np.asarray(values, dtype=float))
    v = v[np.isfinite(v)]
    if v.size == 0 or v.sum() <= 0:
        return None
    n = v.size
    idx = np.arange(1, n + 1)
    return float((2 * (idx * v).sum()) / (n * v.sum()) - (n + 1) / n)


def _lin(value, lo, hi):
    """Map value onto 0-100 between anchors, clamped. Handles hi < lo."""
    if value is None or not np.isfinite(value):
        return None
    if hi == lo:
        return 50.0
    t = (value - lo) / (hi - lo)
    return float(max(0.0, min(1.0, t)) * 100)


def deep_profile(niche_key):
    """
    Every measured dimension for one niche, from its videos.csv.

    Returns None when the dataset is missing or too small to say
    anything — 200 videos cannot support a decile analysis, and a
    confident number from a thin sample is worse than no number.
    """
    path = videos_path(niche_key)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return None
    if len(df) < 200 or "view_count" not in df.columns:
        return None

    views = pd.to_numeric(df["view_count"], errors="coerce")
    df = df[views.notna()].copy()
    df["view_count"] = views[views.notna()]
    if df.empty:
        return None

    n = len(df)
    total_views = float(df["view_count"].sum())
    p90 = float(df["view_count"].quantile(0.90))
    top = df[df["view_count"] >= p90]

    prof = {
        "key": niche_key,
        "niche": split_window(niche_key)[0],
        "window": split_window(niche_key)[1] or "?",
        "videos": n,
        "channels": int(df["channel_id"].nunique()) if "channel_id" in df else None,
    }

    # ---- concentration (the TOP of the market) -------------------
    prof["top10_view_share"] = (float(top["view_count"].sum()) / total_views
                               if total_views else None)
    srt = np.sort(df["view_count"].values)[::-1]
    k = max(1, int(round(n * 0.01)))
    prof["top1_view_share"] = (float(srt[:k].sum()) / total_views
                              if total_views else None)
    prof["gini"] = _gini(df["view_count"].values)
    prof["median_views"] = float(df["view_count"].median())
    prof["p75_views"] = float(df["view_count"].quantile(0.75))
    prof["p90_views"] = p90
    prof["p99_views"] = float(df["view_count"].quantile(0.99))
    prof["max_views"] = float(df["view_count"].max())
    # How far the very top sits above P90. Large means the concentration
    # lives above anything a P90-anchored ratio can see.
    prof["above_p90_reach"] = (prof["max_views"] / p90) if p90 else None

    # ---- the FLOOR, measured separately --------------------------
    dead = df[df["view_count"] < VIABLE_VIEWS]
    prof["dead_zone_pct"] = len(dead) / n
    prof["dead_zone_view_share"] = (float(dead["view_count"].sum()) / total_views
                                   if total_views else None)
    if "channel_subs" in df.columns and len(dead):
        subs = pd.to_numeric(dead["channel_subs"], errors="coerce").dropna()
        prof["amateur_floor"] = (float((subs < TINY_CHANNEL_SUBS).mean())
                                 if len(subs) else None)
        prof["dead_zone_median_subs"] = float(subs.median()) if len(subs) else None
    else:
        prof["amateur_floor"] = None
        prof["dead_zone_median_subs"] = None

    # ---- entry access: who is actually winning -------------------
    if "channel_subs" in df.columns and len(top):
        tsubs = pd.to_numeric(top["channel_subs"], errors="coerce").dropna()
        prof["underdog_share"] = (float((tsubs < SMALL_CHANNEL_SUBS).mean())
                                  if len(tsubs) else None)
        prof["winner_median_subs"] = float(tsubs.median()) if len(tsubs) else None
    else:
        prof["underdog_share"] = None
        prof["winner_median_subs"] = None

    # ---- durability: does the back catalogue keep earning? -------
    #
    # REBUILT. The original formula — median(old vph) / median(young
    # vph) — was found unstable by direct testing, not assumed fine
    # because it looked like a simple, direct measurement. A split-half
    # test (score each random half of the SAME niche's data
    # independently, see if the two halves agree) showed swings up to
    # nearly 6x between halves of the same dataset, on cohorts of
    # 500-1200+ videos — not a thin-sample problem. Root cause:
    # views_per_hour is extremely heavily skewed (CV of 3-5 measured
    # directly), and a bare ratio of two medians of a skewed metric has
    # no bound on how far one unlucky/lucky outlier can swing it.
    #
    # Tried and REJECTED first: trimmed median, P75 instead of median,
    # raw view_count instead of vph, and retuning the age cutoffs —
    # none meaningfully fixed it, and a boundary change that looked
    # promising on one random split (182/60 -> 120/90) made things
    # WORSE on other splits when re-tested across 5 different seeds —
    # overfitting to one split, not a real fix.
    #
    # What actually worked: stop taking a ratio of two point estimates
    # and ask a BOUNDED question instead — what percentile of the YOUNG
    # cohort's performance does the OLD cohort's median represent?
    # Naturally bounded 0-100, so a single extreme video cannot send it
    # to 6x or 18x the way an unbounded ratio could. Confirmed stable
    # across 5 random seeds (spread 1.6-2.7x between halves), comparable
    # to reliability's and reward's independently-measured stability —
    # not just "less bad," genuinely in the same range as the two
    # dimensions this project already trusted more.
    prof["durability"] = None
    prof["durability_n"] = 0
    if {"hours_since_upload", "views_per_hour"}.issubset(df.columns):
        age = pd.to_numeric(df["hours_since_upload"], errors="coerce")
        vph = pd.to_numeric(df["views_per_hour"], errors="coerce")
        ok = age.notna() & vph.notna()
        old = vph[ok & (age >= OLD_COHORT_HOURS)]
        young = vph[ok & (age <= YOUNG_COHORT_HOURS)]
        # Both cohorts need enough members for a median to mean anything.
        if len(old) >= 30 and len(young) >= 30:
            old_median = float(old.median())
            prof["durability"] = float((young < old_median).mean() * 100)
            prof["durability_n"] = int(min(len(old), len(young)))

    # ---- effort: what a winning video costs to make --------------
    prof["winner_duration_s"] = None
    if "duration_seconds" in df.columns and len(top):
        d = pd.to_numeric(top["duration_seconds"], errors="coerce").dropna()
        prof["winner_duration_s"] = float(d.median()) if len(d) else None

    # ---- audience stickiness -------------------------------------
    prof["winner_engagement"] = None
    if "engagement_rate" in df.columns and len(top):
        e = pd.to_numeric(top["engagement_rate"], errors="coerce")
        # Guard the known contamination: engagement_rate explodes when
        # view_count is 0, producing rates in the thousands. Anything
        # above 100% is impossible, so drop it rather than let one video
        # dictate the niche's reading.
        e = e[(e >= 0) & (e <= 1.0)].dropna()
        prof["winner_engagement"] = float(e.median()) if len(e) else None

    # ---- supply pressure -----------------------------------------
    prof["videos_per_channel"] = (n / prof["channels"]
                                  if prof.get("channels") else None)
    return prof


# ============================================================
# SCORING
# ============================================================

def _reliability_score(prof):
    """
    An outcome-concentration score, built from P90/P75 — reused from
    calibration.py's Tier 1 spread work rather than a new measurement.

    WHAT THIS ACTUALLY MEASURES, STATED CAREFULLY THIS TIME
    -----------------------------------------------------------
    This does NOT directly measure predictability or repeatability. It
    measures the gap between a niche's upper-middle performers (P75) and
    its top performers (P90). A narrow gap could mean outcomes are
    genuinely consistent — or it could mean the whole upper distribution
    is compressed for some other reason unrelated to repeatability.
    Nothing in this project can currently tell those apart, because doing
    so needs outcome data this project does not have: repeat builds of
    the same niche over time, or actual tracked creator results. Calling
    this "Reliability" outright was an unproven construct-validity leap,
    caught by review, not resolved by this docstring — it is renamed in
    spirit here (an outcome-concentration proxy) even though the field
    name in code stays `reliability` for now, to avoid a larger,
    separately-risky rename across every call site that reads it.

    WHY THIS REUSES calibration.py INSTEAD OF BUILDING SOMETHING NEW
    -------------------------------------------------------------------
    An early review of this framework proposed a dimension along these
    lines, using Gini as the example metric. Gini was tested during Tier
    1 calibration and found nearly useless for this purpose: across 11
    real niches it ranged 0.917-0.976, a band too narrow to discriminate
    anything. What DID discriminate, on the same 11 niches, was P90/P75,
    already computed, already calibrated, already SIZE-AWARE. Building a
    second metric from scratch would either rediscover the same ratio
    under a new name, or use a worse one (Gini) already tested and
    rejected. This does not, on its own, establish that the ratio means
    what a "reliability" label implies — see above.

    WHY TIER 1 DIDN'T ALREADY SCORE ON THIS, IF IT'S USEFUL
    -------------------------------------------------------------
    Tier 1 uses this ratio to DESCRIBE a niche's market shape for a
    person reading a summary — "gentle" or "brutal," a label, not a
    score competing against other niches. Tier 4 SCORES niches against
    each other for a composite ranking. The same measurement can serve
    both jobs; using it for the second does not require it to have been
    built for the second originally, but the jump from "useful label"
    to "20%-weighted ranking input" was made too casually the first
    time this was built, which is why the weight was cut afterward (see
    WEIGHTS above).

    NOT VALIDATED, STATED PLAINLY: no test in this project checks
    whether a niche scoring high here actually produces more consistent
    real-world results than one scoring low. The correlation check run
    against the other four dimensions (max |r|=0.22) shows no strong
    LINEAR relationship among 11 points — a real check, but a weak one at
    this sample size, and it rules out simple redundancy without
    establishing genuine independence. Treat this dimension as a kept,
    real, but low-confidence signal, matching its reduced weight.

    Returns None when p90/p75 aren't both available — same abstention
    pattern as every other dimension here.
    """
    p90, p75 = prof.get("p90_views"), prof.get("p75_views")
    if not p90 or not p75 or p75 <= 0:
        return None
    ratio = p90 / p75
    # FOUND, NOT ASSUMED: this was calling calibration's window-aware
    # functions with no window at all, which defaults to 365d — so
    # every niche's Reliability score was silently graded against 365d
    # size/spread calibration regardless of its real collection window,
    # the exact same bug just fixed in report.py's single-niche summary.
    # prof["window"] carries the real window as a string ("365d", "90d",
    # "30d", or "?" for a bare unwindowed key) — parse it to the int
    # calibration.py's functions expect.
    window_str = prof.get("window") or ""
    window_days = int(window_str[:-1]) if window_str[:-1].isdigit() and window_str.endswith("d") else None
    size = calibration.ceiling_band(p90, window_days)[0] or "small"
    lo, hi = calibration.observed_range_for(size, window_days)
    # Margin outside the observed extremes, same pattern used for every
    # other anchor in this file — contains the population rather than
    # clipping it, and leaves room for a niche outside the current 11 to
    # not immediately clamp. ~8% either side; the large group only has 4
    # niches behind it, so this stays modest rather than inventing false
    # precision on a thin sample.
    margin = (hi - lo) * 0.08
    worst, best = hi + margin, max(0.5, lo - margin)
    # Inverted on purpose: LOWER ratio = MORE reliable, so the anchor
    # pair is passed (worst, best) rather than (best, worst) to _lin.
    return _lin(ratio, worst, best)


def score_profile(prof, rpm, level="mid"):
    """
    Turn one profile into five dimension scores plus a composite.
    Missing inputs propagate as None rather than defaulting to a
    middling value — a fabricated 50 would move the ranking while
    looking like evidence.

    RPM NO LONGER FEEDS THE SCORE. It used to: reward was log10(revenue),
    where revenue = p90_views * RPM / 1000, so an editable, no-ground-
    truth number in rpm_rates.csv was silently deciding part of a
    niche's ranking. Removed by explicit request — RPM is still looked
    up and returned below (see "probable_rpm" / "revenue_per_winner_at_
    probable_rpm" in the output) so a reader can see the money estimate
    the tool THINKS is likely, but it is informational now, not a
    scoring input. `level` is kept as a parameter purely so the probable-
    RPM figure shown can still be picked at low/mid/high if a caller
    wants that — it has no effect on any of the five dimension scores.
    """
    rate = float(rpm.get(f"rpm_{level}") or 0)

    # Entry as a GATE, not an average — deferred earlier for lack of
    # data, now built with real niches behind it.
    #
    # A flat average of underdog_share and amateur_floor let a niche with
    # ZERO newcomer winners still score up to 50 on entry, as long as its
    # dead zone was full of hobbyists. That is backwards: if small
    # channels never win here, the niche is closed to a newcomer
    # regardless of how the floor looks — floor composition describes
    # who is easy to outrank, not whether outranking them gets you
    # anywhere. Gating makes underdog_share the precondition and
    # amateur_floor a modifier on top of it, in the 0.6-1.0 range, so a
    # generous floor can boost entry by up to 40% but can never manufacture
    # access that is not there.
    underdog = _lin(prof.get("underdog_share"), *ANCHORS["underdog_share"])
    floor = _lin(prof.get("amateur_floor"), *ANCHORS["amateur_floor"])
    if underdog is not None:
        modifier = 0.6 + 0.4 * (floor / 100.0) if floor is not None else 0.8
        entry = underdog * modifier
    else:
        entry = None

    # Reach of a winning video — see ANCHORS["log_p90_views"] for why
    # this replaced a revenue estimate. Pure measurement: a niche's own
    # top-decile view count, nothing multiplied onto it.
    p90 = prof.get("p90_views") or 0
    reward = _lin(math.log10(p90) if p90 > 0 else None, *ANCHORS["log_p90_views"])

    # Durability — see ANCHORS["durability_pct"] for the full story on
    # why this is a plain linear score now, not log10 of a ratio.
    # Passed straight to _lin(), which is None-safe on its own — no
    # truthy check needed here, which matters because the metric can
    # legitimately BE 0.0 (every young video outperforms the old
    # cohort's median — a real, valid "very poor durability" reading).
    # An `if dur_raw and ...` truthy guard would have silently discarded
    # a real 0.0 as if the niche had no durability data at all.
    durability = _lin(prof.get("durability"), *ANCHORS["durability_pct"])
    effort = _lin(prof.get("winner_duration_s"), *ANCHORS["effort_seconds"])
    reliability = _reliability_score(prof)

    dims = {"entry": entry, "reward": reward,
            "durability": durability, "reliability": reliability,
            "effort": effort}

    # Re-normalise across whichever dimensions survived, so a niche is
    # not punished for a metric its dataset could not supply.
    usable = {k: v for k, v in dims.items() if v is not None}
    if usable:
        wsum = sum(WEIGHTS[k] for k in usable)
        composite = sum(v * WEIGHTS[k] for k, v in usable.items()) / wsum
    else:
        composite = None

    # RPM: informational only, from here down. Not read by anything
    # above this comment.
    revenue_at_probable_rpm = (p90 * rate / 1000.0) if p90 and rate else None

    return {
        **dims,
        "composite": composite,
        "probable_rpm": rate or None,
        "revenue_per_winner_at_probable_rpm": revenue_at_probable_rpm,
        "dimensions_measured": len(usable),
    }


def _rank_order(scored):
    """Niche keys ordered best-first by composite score."""
    rows = [(k, s["composite"]) for k, s in scored.items()
            if s["composite"] is not None]
    return [k for k, _ in sorted(rows, key=lambda x: -x[1])]


def weight_sensitivity(scored, alt_weightings=None):
    """
    How much does the composite RANKING move under different plausible
    weightings of entry/reward/durability/reliability/effort?

    This exists because "the weights are unvalidated" is a real,
    recurring critique of this framework, and re-deriving them from
    measured variance repeats the exact mistake that critique is
    pointing at (variance is not the same claim as importance). What
    CAN be checked without inventing a new formula: is the ranking this
    framework produces fragile to the specific weight choice, or does it
    survive a range of reasonable alternatives? A ranking that only holds
    together under one particular weighting is much weaker evidence than
    one that is stable across several.

    Returns a list of dicts, one per alternative weighting: the
    weighting itself, the rank correlation against the CURRENT weights
    (WEIGHTS in this module), and which niche moved the most and by how
    many places — because "the ranking is 92% stable" is much less
    useful than knowing WHICH niche is the pivot case and why, which is
    usually a real, explainable trade-off rather than noise. Re-run this
    whenever WEIGHTS changes or the niche library grows, rather than
    trusting a robustness check performed on a different pool.
    """
    import pandas as pd

    dims = ("entry", "reward", "durability", "reliability", "effort")
    rows = {k: s for k, s in scored.items() if s.get("composite") is not None}
    if len(rows) < 3:
        return []

    df = pd.DataFrame({k: {d: v.get(d) for d in dims} for k, v in rows.items()}).T
    df = df.dropna()
    if len(df) < 3:
        return []

    def composite(w):
        wsum = sum(w.values())
        return sum(df[d] * w[d] for d in dims) / wsum

    base = composite(WEIGHTS).rank(ascending=False)

    alt_weightings = alt_weightings or {
        "equal (20/20/20/20/20)": {"entry": 20, "reward": 20, "durability": 20,
                                    "reliability": 20, "effort": 20},
        "entry-heavy": {"entry": 35, "reward": 20, "durability": 15,
                         "reliability": 15, "effort": 15},
        "durability-heavy": {"entry": 20, "reward": 20, "durability": 30,
                              "reliability": 15, "effort": 15},
        "reliability-heavy": {"entry": 20, "reward": 20, "durability": 15,
                               "reliability": 35, "effort": 10},
        "effort-zeroed": {"entry": 30, "reward": 30, "durability": 25,
                           "reliability": 15, "effort": 0},
    }

    out = []
    for name, w in alt_weightings.items():
        rank = composite(w).rank(ascending=False)
        rho = base.corr(rank, method="spearman")
        shift = (base - rank).abs()
        pivot = shift.idxmax() if len(shift) else None
        out.append({
            "weighting": name,
            "weights": w,
            "rank_correlation": round(float(rho), 3) if rho == rho else None,
            "pivot_niche": pivot,
            "pivot_niche_rank_shift": int(shift.max()) if len(shift) else 0,
        })
    return out


def analyse(niche_keys, window=None):
    """
    Full comparison. Returns a dict carrying profiles, one score set per
    niche, the ranking, and dimension-level trade-offs.

    Used to score every niche at THREE RPM levels (low/mid/high) and
    check whether the ranking held across all three — that made sense
    when RPM drove part of the composite. It no longer does (see
    score_profile's docstring), so scoring at three levels now produces
    three IDENTICAL rankings by construction — real compute spent
    confirming something guaranteed true before it ran. Scores once,
    at "mid", and keeps RPM lookups (low/mid/high) only for the
    probable-RPM figure shown alongside the result.
    """
    rpm_table = load_rpm_table()
    profiles, rpms, scored, skipped = {}, {}, {}, []

    for key in niche_keys:
        prof = deep_profile(key)
        if prof is None:
            skipped.append(key)
            continue
        profiles[key] = prof
        rpms[key] = match_rpm(prof["niche"], rpm_table)
        scored[key] = score_profile(prof, rpms[key], "mid")

    if not profiles:
        return {"error": "no_usable_datasets", "skipped": skipped}

    order = _rank_order(scored)

    # Do the dimensions disagree about the SAME niche pair? If the top
    # niche on one dimension is not the top on another by a wide margin,
    # a single ranking would hide the only fact that matters.
    conflicts = []
    if len(profiles) >= 2:
        for dim in ("entry", "reward", "durability", "reliability", "effort"):
            vals = {k: scored[k][dim] for k in profiles
                    if scored[k][dim] is not None}
            if len(vals) < 2:
                continue
            best = max(vals, key=vals.get)
            worst = min(vals, key=vals.get)
            if vals[best] - vals[worst] >= CONFLICT_THRESHOLD:
                conflicts.append({"dimension": dim, "leader": best,
                                  "laggard": worst,
                                  "gap": vals[best] - vals[worst]})

    # A real trade-off is when different niches lead different dimensions.
    leaders = {c["leader"] for c in conflicts}
    trade_off = len(leaders) > 1

    return {
        "window": window,
        "profiles": profiles,
        "rpms": rpms,
        "scored": scored,
        "order": order,
        "conflicts": conflicts,
        "trade_off": trade_off,
        "skipped": skipped,
        "rpm_table_found": rpm_table is not None,
    }


# ============================================================
# PLAIN-LANGUAGE VERDICT
# ============================================================

_DIM_LABEL = {
    "entry": "room for a new channel",
    "reward": "what a hit is worth",
    "durability": "how long a video keeps earning",
    # Hedged in its own label, not just a code comment, for the same
    # reason effort's label is hedged below — this is what a person
    # actually reads. "How predictable" implies more than the underlying
    # ratio (gap between upper-middle and top performers) has been shown
    # to prove; a review caught this and the weight was cut accordingly.
    "reliability": "outcome spread near the top (an unvalidated proxy)",
    # Marked as a proxy in its own label, not just in a code comment,
    # because this is read directly in the plain-language verdict and the
    # report table header — the two places a person actually sees it.
    # Video length correlates with production cost on average but is not
    # the same thing (a long podcast can be cheap; a short edited video
    # can be expensive), and that gap does not show up anywhere else in
    # the rendered output unless it is said here.
    "effort": "video length (a cost PROXY, not a direct measure)",
}


def verdict_text(res):
    """
    The sentence the whole report exists to produce.

    Two shapes now, not three — chosen by what the data supports:
      - one niche leads everywhere -> name it
      - dimensions conflict (different niches lead different
        dimensions) -> name the overall leader, then state what it
        costs relative to the runner-up

    A third shape used to exist here: "ranking flips with RPM -> refuse
    to rank, give the condition." That branch is gone because it
    described something that can no longer happen — RPM does not feed
    the composite anymore (see score_profile's docstring), so there is
    only one ranking to report, not three to compare.
    """
    if res.get("error"):
        return "Not enough data to compare."
    disp = {k: res["profiles"][k]["niche"] for k in res["profiles"]}
    order = res["order"]
    if not order:
        return "No niche had enough measurable dimensions to score."
    if len(order) == 1:
        return f"Only {disp[order[0]]} had a usable dataset."

    first, second = order[0], order[1]

    if res["trade_off"]:
        # FOUND BY TESTING, NOT ASSUMED: this used to only ever look at
        # whether the #2 niche BY COMPOSITE specifically led some
        # dimension. With more than 2 niches in the pool, the niche that
        # actually leads a dimension the overall winner doesn't can be
        # ranked 3rd, 4th, anywhere — not necessarily #2. When that
        # happened, "theirs" came back empty and the sentence rendered
        # with a blank list ("...is the better choice on . Pick...").
        # Confirmed live on a real 5-niche comparison. Fixed by picking
        # whichever OTHER niche actually leads a real dimension against
        # the overall winner, rather than assuming it must be the
        # runner-up.
        rivals = [c for c in res["conflicts"] if c["leader"] != first]
        if rivals:
            # the rival ranked highest overall among those with a real
            # conflict, so the sentence names the most relevant
            # contender rather than an arbitrary one
            rival = min({c["leader"] for c in rivals}, key=order.index)
            theirs = [c for c in rivals if c["leader"] == rival]
            gained = ", ".join(_DIM_LABEL[c["dimension"]] for c in theirs[:2])
            return (
                f"{disp[first]} scores highest overall. But these niches "
                f"suit different operators: {disp[rival]} is the better "
                f"choice on {gained}. Pick on which of those you can "
                f"actually sustain."
            )

    return (f"{disp[first]} leads on every dimension measured. It is the "
            f"clearer entry.")


# ============================================================
# HTML REPORT
# ============================================================

_CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;
700;800&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg-1:#0A1120; --bg-2:#101B30; --glow:rgba(76,141,255,.16);
  --ink:#EDF1F7; --ink-soft:#9AA7BC; --rule:rgba(255,255,255,.09);
  --rule-soft:rgba(255,255,255,.055);
  --card:rgba(255,255,255,.045); --card-hi:rgba(255,255,255,.07);
  --dead:#6B7A93;   /* muted slate — the floor: invisible, not alarming */
  --mid:#4C8DFF;    /* accent blue — the contested middle */
  --top:#EFA53E;    /* amber — the winners' share */
  --flag:#F0576B;   /* coral — warnings */
  --font:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --mono:'IBM Plex Mono',monospace;
}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);font-family:var(--font);font-size:15px;
  line-height:1.6;
  background:
    radial-gradient(900px 500px at 88% 6%,var(--glow),transparent 60%),
    radial-gradient(700px 420px at 6% 96%,rgba(239,165,62,.07),transparent 60%),
    linear-gradient(160deg,var(--bg-1),var(--bg-2));
  background-attachment:fixed;min-height:100vh}
.wrap{max-width:1080px;margin:0 auto;padding:clamp(24px,5vw,56px) clamp(16px,4vw,32px) 96px}
.eyebrow{font-family:var(--mono);font-weight:600;letter-spacing:.16em;
  text-transform:uppercase;font-size:11px;color:var(--ink-soft);
  margin:0 0 12px;display:flex;align-items:center;gap:10px}
.eyebrow::before{content:"";width:22px;height:1px;background:#9AA7BC}
h1{font-weight:800;font-size:clamp(26px,4vw,38px);line-height:1.12;
  margin:0 0 8px;letter-spacing:-.02em}
h2{font-weight:700;font-size:18px;margin:48px 0 6px;letter-spacing:-.01em;
  display:flex;align-items:center;gap:9px}
h2::before{content:"";width:3px;height:15px;border-radius:2px;
  background:#4C8DFF}
.sub{color:var(--ink-soft);margin:0 0 4px;font-size:13.5px}
.verdict{background:var(--card-hi);border:1px solid var(--rule);
  border-radius:18px;position:relative;overflow:hidden;
  padding:22px 26px;margin:24px 0 10px;font-size:16.5px;line-height:1.55}
.verdict::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;
  background:#EFA53E}
.note{color:var(--ink-soft);font-size:13px;margin:10px 0 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
th{font-weight:700;text-align:left;font-size:10.5px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-soft);border-bottom:1px solid var(--rule);
  padding:9px 12px}
td{padding:11px 12px;border-bottom:1px solid var(--rule-soft)}
td.num,th.num{text-align:right;font-family:var(--mono);
  font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.bar{height:7px;border-radius:5px;background:rgba(255,255,255,.08);
  overflow:hidden;min-width:70px}
.bar>span{display:block;height:100%;border-radius:5px;background:#4C8DFF}
.lead>span{background:#EFA53E}
.flag{color:var(--ink);background:rgba(240,87,107,.12);
  border-left:3px solid #F0576B;padding:14px 18px;border-radius:12px;
  margin:16px 0;font-size:13.5px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;
  color:var(--ink-soft);margin:8px 0 20px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:4px;
  margin-right:6px;vertical-align:-1px}
.cards{display:flex;gap:16px;flex-wrap:wrap;margin:14px 0}
.card{flex:1 1 300px;background:var(--card);border:1px solid var(--rule);
  border-radius:16px;padding:18px 20px}
.card h3{font-size:15.5px;font-weight:700;margin:0 0 2px;letter-spacing:-.005em}
.card .k{color:var(--ink-soft);font-size:12px;margin:0 0 14px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:6px 0;
  border-bottom:1px solid var(--rule-soft);font-size:12.5px;color:var(--ink-soft)}
.kv:last-child{border-bottom:none}
.kv b{font-family:var(--mono);font-weight:600;font-variant-numeric:tabular-nums;
  color:var(--ink)}
figure{margin:20px 0;background:var(--card);border:1px solid var(--rule);
  border-radius:18px;padding:20px}
figcaption{color:var(--ink-soft);font-size:12.5px;margin-top:14px}
footer.gen{margin-top:34px;font-family:var(--mono);font-size:10.5px;
  color:var(--ink-soft);opacity:.7}
@media (max-width:640px){.wrap{padding:24px 14px 64px}h1{font-size:24px}
  table{font-size:12.5px}td,th{padding:8px 6px}}
@media print{body{background:#fff}.card,.verdict{break-inside:avoid}}
</style>
"""


def _strata_svg(profiles):
    """
    The signature figure: each niche as a mirrored core sample.

    LEFT of the spine, how many VIDEOS sit in each stratum. RIGHT, how
    many VIEWS that stratum captured. A healthy market has the two sides
    roughly agreeing. Every YouTube niche measured so far has a floor
    that is enormous on the left and invisible on the right — which is
    the whole finding, drawn rather than argued.
    """
    keys = list(profiles)
    if not keys:
        return ""
    colw, gap, top_pad, h = 190, 46, 34, 250
    w = len(keys) * colw + (len(keys) - 1) * gap
    height = h + top_pad + 78
    out = [f'<svg viewBox="0 0 {w} {height}" width="100%" '
           f'style="max-width:{w}px" role="img" '
           f'aria-label="Share of videos versus share of views, by stratum">',
           # Explicit backdrop rect rather than relying on the parent
           # <figure>'s CSS background showing through the SVG canvas —
           # some renderers paint an inline SVG's own canvas opaque
           # regardless of the parent's background, which would wash out
           # the light-coloured labels drawn on top.
           f'<rect x="0" y="0" width="{w}" height="{height}" fill="#0F1830"/>']

    for i, k in enumerate(keys):
        p = profiles[k]
        x0 = i * (colw + gap)
        spine = x0 + colw / 2
        dead_n = p.get("dead_zone_pct") or 0
        top_n = 0.10
        mid_n = max(0.0, 1 - dead_n - top_n)
        dead_v = p.get("dead_zone_view_share") or 0
        top_v = p.get("top10_view_share") or 0
        mid_v = max(0.0, 1 - dead_v - top_v)

        strata = [("top", top_n, top_v, "#EFA53E"),
                  ("mid", mid_n, mid_v, "#4C8DFF"),
                  ("dead", dead_n, dead_v, "#6B7A93")]
        y = top_pad
        for _, n_share, v_share, fill in strata:
            hh = n_share * h
            if hh <= 0:
                continue
            # Height already encodes share of videos, so the left block
            # keeps a constant width. Letting width vary too would make
            # the drawn AREA the square of the real quantity — the eye
            # reads area, so a 56% floor would look like 31%.
            lw = colw / 2 - 2
            rw = (colw / 2 - 2) * min(1.0, v_share)
            out.append(f'<rect x="{spine - lw:.1f}" y="{y:.1f}" '
                       f'width="{lw:.1f}" height="{hh:.1f}" fill="{fill}" '
                       f'opacity=".55"/>')
            if rw > 0.4:
                out.append(f'<rect x="{spine:.1f}" y="{y:.1f}" '
                           f'width="{rw:.1f}" height="{hh:.1f}" fill="{fill}"/>')
            y += hh
        out.append(f'<line x1="{spine}" y1="{top_pad}" x2="{spine}" '
                   f'y2="{top_pad + h}" stroke="#EDF1F7" '
                   f'stroke-width="1" opacity=".5"/>')
        name = p["niche"][:24]
        out.append(
            f'<text x="{spine}" y="{top_pad - 12}" text-anchor="middle" '
            f'font-family="Bahnschrift,Segoe UI,sans-serif" font-size="14" '
            f'font-weight="600" fill="#EDF1F7">{name}</text>')
        out.append(
            f'<text x="{spine}" y="{top_pad + h + 20}" text-anchor="middle" '
            f'font-size="11.5" fill="#9AA7BC">'
            f'floor: {dead_n:.0%} of videos, {dead_v:.2%} of views</text>')
        out.append(
            f'<text x="{spine}" y="{top_pad + h + 37}" text-anchor="middle" '
            f'font-size="11.5" fill="#9AA7BC">'
            f'top 10%: {top_v:.0%} of views</text>')
        out.append(
            f'<text x="{spine - colw/4}" y="{top_pad + h + 58}" '
            f'text-anchor="middle" font-size="10" letter-spacing=".1em" '
            f'fill="#9AA7BC">VIDEOS</text>')
        out.append(
            f'<text x="{spine + colw/4}" y="{top_pad + h + 58}" '
            f'text-anchor="middle" font-size="10" letter-spacing=".1em" '
            f'fill="#9AA7BC">VIEWS</text>')
    out.append("</svg>")
    return "".join(out)


def _pct(v, dp=0):
    return f"{v:.{dp}%}" if isinstance(v, (int, float)) and v == v else "—"


def _num(v, dp=0):
    return f"{v:,.{dp}f}" if isinstance(v, (int, float)) and v == v else "—"


def _bar(score, lead=False):
    if score is None:
        return '<td class="num">—</td>'
    cls = "bar lead" if lead else "bar"
    return (f'<td><div class="{cls}"><span style="width:{score:.0f}%"></span>'
            f'</div></td>')


def render_html(res):
    """Build the standalone report. No external assets — it opens offline."""
    if res.get("error"):
        return "<p>Not enough data.</p>"
    P, S, R = res["profiles"], res["scored"], res["rpms"]
    order = res["order"]
    disp = {k: P[k]["niche"] for k in P}
    lead = order[0] if order else None

    H = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>Niche entry verdict</title>',
         f'<style>{_CSS}</style></head><body><div class="wrap">']

    H.append('<p class="eyebrow">Niche entry verdict</p>')
    H.append(f'<h1>{" &nbsp;·&nbsp; ".join(disp[k] for k in order)}</h1>')
    wins = {P[k]["window"] for k in P}
    win_label = (f'the {P[order[0]]["window"]} window' if len(wins) == 1
                 else "mixed windows — " + ", ".join(
                     f'{P[k]["niche"]} {P[k]["window"]}' for k in order))
    H.append(f'<p class="sub">{len(P)} niche(s) at {win_label} &middot; '
             f'{sum(P[k]["videos"] for k in P):,} videos read from disk</p>')
    H.append(f'<div class="verdict">{verdict_text(res)}</div>')

    if res.get("mismatch"):
        wins = ", ".join(f"{w} ({', '.join(ns)})"
                         for w, ns in sorted(res["mismatch"]["windows"].items()))
        H.append(f'<div class="flag"><b>Mixed collection windows:</b> {wins}. '
                 f'{res["mismatch"]["note"]}</div>')

    if not res["rpm_table_found"]:
        H.append('<div class="flag">rpm_rates.csv was not found beside '
                 'main.py, so the probable-revenue figure below is running '
                 'on a flat fallback band. This does not affect any '
                 'dimension score or the ranking — RPM is informational '
                 'only. Add the file to make the revenue estimate itself '
                 'meaningful.</div>')

    # ---- simple table, FIRST — the headline numbers, before anything
    # else. The deep per-dimension tables further down are real and
    # worth having, but they should not be the first thing a reader has
    # to parse to get the actual answer.
    H.append('<h2>At a glance</h2>')
    H.append('<table><tr><th>rank</th><th>niche</th>'
             '<th class="num">overall</th><th>leads on</th></tr>')
    for i, k in enumerate(order, 1):
        s = S[k]
        leads = [_DIM_LABEL[c["dimension"]] for c in res["conflicts"]
                if c["leader"] == k]
        leads_txt = ", ".join(leads[:2]) if leads else ("—" if i > 1 else "everything measured")
        comp = f'{s["composite"]:.0f}' if s["composite"] is not None else "—"
        H.append(f'<tr><td>{i}</td><td><b>{disp[k]}</b></td>'
                 f'<td class="num"><b>{comp}</b></td><td>{leads_txt}</td></tr>')
    H.append('</table>')

    # ---- signature figure ----
    H.append('<h2>Where the views actually go</h2>')
    H.append('<p class="sub">Each column is one niche. Left of the spine, '
             'the share of videos in each stratum; right, the share of '
             'views that stratum captured.</p>')
    H.append('<div class="legend">'
             '<span><i style="background:#EFA53E"></i>top 10% of videos</span>'
             '<span><i style="background:#4C8DFF"></i>the contested middle</span>'
             '<span><i style="background:#6B7A93"></i>floor '
             f'(under {VIABLE_VIEWS:,} lifetime views)</span></div>')
    H.append(f'<figure>{_strata_svg(P)}<figcaption>'
             'A floor that is wide on the left and invisible on the right '
             'is not competition — it is content that never entered the '
             'market. Whether that helps you depends on who posted it, '
             'which the entry table below answers.</figcaption></figure>')

    # ---- dimension scores (the deep table — after the simple one above) ----
    H.append('<h2>Dimension scores</h2>')
    H.append('<p class="sub">Bars are relative to the provisional anchors, '
             'not to each other.</p>')
    H.append('<table><tr><th>niche</th>'
             + "".join(f'<th>{_DIM_LABEL[d]}</th>' for d in
                       ("entry", "reward", "durability", "reliability", "effort"))
             + '<th class="num">overall</th></tr>')
    for k in order:
        s = S[k]
        H.append(f'<tr><td><b>{disp[k]}</b></td>')
        for d in ("entry", "reward", "durability", "reliability", "effort"):
            H.append(_bar(s[d], lead=(k == lead)))
        comp = f'{s["composite"]:.0f}' if s["composite"] is not None else "—"
        H.append(f'<td class="num"><b>{comp}</b></td></tr>')
    H.append('</table>')

    # ---- probable RPM (informational only — see score_profile) ----
    H.append('<h2>Probable ad revenue (not used in the score)</h2>')
    H.append('<p class="sub">RPM is a declared estimate, not a '
             'measurement — there is no ground truth behind it, so it no '
             'longer drives any dimension score or the ranking above. '
             'Shown here as information only: roughly what a top-decile '
             'video in each niche might earn, if this rate turns out to '
             'be close.</p>')
    H.append('<table><tr><th>niche</th><th class="num">RPM low</th>'
             '<th class="num">mid</th><th class="num">high</th>'
             '<th class="num">probable $ per top-decile video</th>'
             '<th>rate confidence</th></tr>')
    for k in order:
        r, s = R[k], S[k]
        H.append(
            f'<tr><td><b>{disp[k]}</b><br>'
            f'<span class="k" style="font-size:11.5px;color:#9AA7BC">'
            f'matched: {r.get("label") or r.get("niche_key")}</span></td>'
            f'<td class="num">${_num(r.get("rpm_low"), 0)}</td>'
            f'<td class="num">${_num(r.get("rpm_mid"), 0)}</td>'
            f'<td class="num">${_num(r.get("rpm_high"), 0)}</td>'
            f'<td class="num">${_num(s.get("revenue_per_winner_at_probable_rpm"), 0)}</td>'
            f'<td>{r.get("confidence") or "—"}</td></tr>')
    H.append('</table>')

    risky = [k for k in order
             if str(R[k].get("limited_ads_risk")).lower() == "high"]
    if risky:
        H.append('<div class="flag">Limited-ads risk: '
                 + ", ".join(disp[k] for k in risky)
                 + '. Mature or controversial themes are commonly '
                   'demonetised, which can remove most of the rate above. '
                   'Treat their revenue figures as a ceiling.</div>')

    # ---- the measured detail ----
    H.append('<h2>What was measured</h2>')
    H.append('<div class="cards">')
    for k in order:
        p, r = P[k], R[k]
        rows = [
            ("Winners from channels &lt;10k subs", _pct(p.get("underdog_share"))),
            ("Median subs of a winning channel", _num(p.get("winner_median_subs"))),
            ("Floor (under 1k views)", _pct(p.get("dead_zone_pct"))),
            ("&hellip;posted by channels &lt;100 subs", _pct(p.get("amateur_floor"))),
            ("Top 10% share of all views", _pct(p.get("top10_view_share"), 1)),
            ("Top 1% share of all views", _pct(p.get("top1_view_share"), 1)),
            ("Gini", _num(p.get("gini"), 3)),
            ("Biggest video vs P90", (f'{p["above_p90_reach"]:,.0f}x'
                                      if p.get("above_p90_reach") else "—")),
            ("Durability (old-cohort percentile vs young)",
             (f'{p["durability"]:.0f}th pct'
              if p.get("durability") is not None else "not enough age spread")),
            ("Median length of a winner", (f'{p["winner_duration_s"]/60:.0f} min'
                                           if p.get("winner_duration_s") else "—")),
            ("Engagement among winners", _pct(p.get("winner_engagement"), 2)),
            ("Videos per channel", _num(p.get("videos_per_channel"), 2)),
        ]
        H.append(f'<div class="card"><h3>{disp[k]}</h3>'
                 f'<p class="k">{p["videos"]:,} videos &middot; '
                 f'{p["channels"]:,} channels &middot; RPM row: '
                 f'{r.get("niche_key")}</p>')
        for label, val in rows:
            H.append(f'<div class="kv"><span>{label}</span><b>{val}</b></div>')
        H.append('</div>')
    H.append('</div>')

    H.append('<h2>What this cannot tell you</h2>')
    H.append('<p class="sub">RPM is declared, not measured — edit '
             'rpm_rates.csv to change it. Nothing here sees sponsorship '
             'rates, affiliate income, your production speed, or whether '
             'you can stand making this content for a year. The score '
             'anchors are provisional and will be replaced once enough '
             'niches have been built to set them at observed '
             'percentiles.</p>')
    H.append('</div></body></html>')
    return "".join(H)


# ============================================================
# EXPORT
# ============================================================

def has_dataset(niche_key) -> bool:
    """Is there a videos.csv for this key? Cheap check before offering."""
    return os.path.exists(videos_path(niche_key))


def export_verdict(niche_keys, window=None, exports_root=None, mismatch=None):
    """
    Write the verdict as HTML plus a flat CSV of every measured number.

    Filenames carry the compared niches, so two verdicts never collide
    in the same folder or in Excel.
    """
    res = analyse(niche_keys, window=window)
    if res.get("error"):
        return res
    res["mismatch"] = mismatch

    root = exports_root or EXPORTS_DIR
    out_dir = os.path.join(root, "_compare")
    os.makedirs(out_dir, exist_ok=True)

    stem = "_vs_".join(safe_name(res["profiles"][k]["niche"])
                       for k in res["order"][:3])
    if len(res["profiles"]) > 3:
        stem += f"_and_{len(res['profiles']) - 3}_more"
    wins = {res["profiles"][k]["window"] for k in res["profiles"]}
    suffix = f"_{list(wins)[0]}" if len(wins) == 1 else "_mixed_windows"

    html_path = os.path.join(out_dir, f"{stem}{suffix}_verdict.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(res))

    rows = []
    for k, p in res["profiles"].items():
        row = dict(p)
        row["rpm_key"] = res["rpms"][k].get("niche_key")
        # CRASH BUG FIXED: used to iterate `for lvl in ("low","mid","high")`
        # and access `res["scored"][k][lvl]` — the old three-level structure
        # from when RPM drove the composite. res["scored"][k] is now a flat
        # dict (one score per niche, at mid-RPM only), so those keys don't
        # exist and every verdict export crashed with a KeyError. Flattened
        # to the current single-level structure.
        for dim, val in res["scored"][k].items():
            row[f"{dim}"] = val
        rows.append(row)
    csv_path = os.path.join(out_dir, f"{stem}{suffix}_verdict.csv")
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    return {"html": html_path, "csv": csv_path, "result": res,
            "skipped": res["skipped"]}


def available_niches(window_filter=None):
    """Niche keys that have a videos.csv big enough to profile."""
    out = []
    for key, _meta in _compare.list_all_niches():
        _, win = split_window(key)
        if window_filter and win != window_filter:
            continue
        if os.path.exists(videos_path(key)):
            out.append(key)
    return out
