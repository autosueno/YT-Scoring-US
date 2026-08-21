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
rpm_rates.csv next to main.py, editable in Excel, and it can swing the
answer several fold. Rather than pretend to one number, every ranking is
computed three times — at the low, mid and high end of each niche's
band. A ranking that survives all three is a finding. One that flips is
reported as a flip, not resolved by picking a favourite.

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
    # PROVISIONAL-v2, and the anchor SHAPE changed, not just its values.
    # Observed durability spans 0.0059 (memory_stocks — a stock-related
    # niche where old videos are worth almost nothing) to 2.33
    # (home_espresso and personal_finance_basics, where old videos
    # outearn new ones) — two and a half orders of magnitude. The old
    # linear anchor (0.05, 0.70) could not represent that spread: it
    # clamped both niches above 0.70 to identical scores (hiding a real
    # 3x difference between them) while barely moving for anything in
    # the low range. Durability is now scored on log10 of itself,
    # matching how reward already handles its own wide range. The values
    # below are log10(durability), not durability directly:
    #     log10(0.0059) = -2.23  (memory_stocks, the real floor)
    #     log10(2.33)   =  0.37  (home/finance, the real ceiling)
    # with margin outside both.
    "durability_log10": (-2.43, 0.57),
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
    "entry": 0.32,          # can a newcomer rank at all — HIGHEST, by request
    "reliability": 0.24,    # outcome-concentration proxy — SECOND, by request
    "durability": 0.20,     # does the catalogue compound
    "reward": 0.16,         # reach of a winning video (see log_p90_views above)
    "effort": 0.08,         # cost per attempt — see note below
}
# REORDERED BY EXPLICIT REQUEST, not by a new confidence finding: entry
# stays highest (unchanged), but reliability was moved from lowest
# (0.10) to second-highest (0.24), ahead of durability. Worth being
# direct about the tension this creates: durability's construct
# validity was already established as STRONGER than reliability's (see
# the reasoning kept below — durability directly measures "do old
# videos still earn," reliability only measures a compression ratio
# that COULD mean predictability but hasn't been checked against real
# outcome data). This reordering does not resolve that gap — it means
# reliability is now weighted above a dimension with better-supported
# validity, as a deliberate choice about what matters most to act on
# right now, not as a claim that reliability became more trustworthy.
# If it's later checked against real outcomes and doesn't hold up, this
# weighting will have been wrong in a specific, correctable direction —
# worth remembering rather than re-deriving from scratch next time.
#
# reward dropped from 0.29 to 0.16 as a direct consequence of removing
# RPM from scoring (see log_p90_views above) — a raw-reach measure is a
# real, still-useful signal, but it answers a narrower question than
# "what a win is worth" did, so it no longer earns a top-two weight.
#
# ORIGINAL reliability-weight reasoning, kept for the construct-validity
# context above (the 0.20->0.10 history, not the current number):
#
# The original justification claimed reliability "shares durability's
# construct-validity profile: both are close to definitional
# operationalizations of what they claim to measure." That claim was
# checked by a reviewer and did not hold up. P90/P75 measures the gap
# between a niche's upper-middle and top performers. Reliability was
# defined here as "how predictable a good result is once a video clears
# the decent bar" — a claim about REPEATABILITY that the ratio does not
# directly establish. A niche could have a narrow P90/P75 gap because
# outcomes are genuinely consistent, or because the whole distribution
# above P75 is simply compressed for reasons unrelated to predictability.
# Nothing in this project distinguishes those cases, because nothing
# here has outcome data — repeat builds of the same niche over time,
# or actual creator results — to check which explanation is true.
# Durability's construct validity is stronger by comparison: "do old
# videos still earn" is close to a direct reading of "does the catalogue
# compound," with no comparable inferential gap.
#
# Also checked and corrected: the original note claimed weak Pearson
# correlation (max |r|=0.22 against the other four dimensions) as
# evidence reliability was "not a restatement" of anything else. With
# n=11, that is not a safe inference — Pearson correlation is unstable
# at this sample size, weak linear correlation does not rule out a
# nonlinear or threshold relationship, and the correct claim is narrower:
# no strong LINEAR correlation was found in THIS sample. That is evidence
# against the most obvious kind of redundancy, not evidence of genuine
# independence.
#
# Given both of those corrections, 0.10 treats reliability as a real,
# kept signal — the underlying ratio is real, calibrated, and reused
# rather than invented — while reflecting materially lower confidence
# than durability, reward, or entry carry. This is closer to the
# "secondary modifier, pending validation" treatment a reviewer proposed
# than to treating it as equally trustworthy. If it is later checked
# against real outcomes (repeat builds, actual channel results) and
# holds up, raising the weight would be a decision with evidence behind
# it — this is not that decision.
#
# Checked with weight_sensitivity() after adding: see that function's
# docstring for what it tests and why. Also checked: the 8% margin
# choice below barely moves anything — composite ranking held at
# Spearman >=0.991 across margins from 0% to 15%, top-3 identical at
# every margin tested. The margin is not doing the substantive work in
# this dimension; the CONSTRUCT-VALIDITY gap above is what matters and
# no margin choice fixes that.
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
            ym = float(young.median())
            prof["durability"] = float(old.median()) / ym if ym > 0 else None
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

    # Durability scored on log10 of itself — see ANCHORS["durability_log10"]
    # for why: the raw ratio spans 2.5 orders of magnitude and a linear
    # anchor could not represent that without clamping real extremes into
    # identical scores.
    dur_raw = prof.get("durability")
    dur_log = math.log10(dur_raw) if dur_raw and dur_raw > 0 else None
    durability = _lin(dur_log, *ANCHORS["durability_log10"])
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
:root{
  --paper:#F4F3EF; --ink:#242A33; --ink-soft:#5A6470; --rule:#DCD9D1;
  --dead:#CFC9D9;      /* pale lilac — the floor */
  --mid:#BCD4C6;       /* pale sage — the contested middle */
  --top:#F0C39C;       /* pale apricot — the winners */
  --flag:#D99AA0;      /* dusty rose — warnings */
  --card:#FBFAF7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:"Segoe UI",system-ui,-apple-system,sans-serif;
  font-size:15px;line-height:1.55;}
.wrap{max-width:1080px;margin:0 auto;padding:48px 28px 96px}
.eyebrow{font-family:Bahnschrift,"DIN Alternate","Segoe UI",sans-serif;
  font-weight:600;letter-spacing:.22em;text-transform:uppercase;
  font-size:11px;color:var(--ink-soft);margin:0 0 10px}
h1{font-family:Bahnschrift,"DIN Alternate","Segoe UI",sans-serif;
  font-weight:600;font-size:40px;line-height:1.1;margin:0 0 6px;
  letter-spacing:-.01em}
h2{font-family:Bahnschrift,"DIN Alternate","Segoe UI",sans-serif;
  font-weight:600;font-size:21px;margin:52px 0 4px;letter-spacing:.01em}
.sub{color:var(--ink-soft);margin:0 0 4px}
.verdict{background:var(--card);border:1px solid var(--rule);
  border-left:5px solid var(--top);border-radius:3px;
  padding:22px 26px;margin:26px 0 8px;font-size:17px;line-height:1.5}
.note{color:var(--ink-soft);font-size:13px;margin:10px 0 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}
th{font-family:Bahnschrift,"Segoe UI",sans-serif;font-weight:600;
  text-align:left;font-size:11px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--ink-soft);border-bottom:1px solid var(--rule);padding:8px 10px}
td{padding:9px 10px;border-bottom:1px solid var(--rule)}
td.num,th.num{text-align:right;
  font-family:"Cascadia Mono",Consolas,ui-monospace,monospace;
  font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.bar{height:7px;border-radius:4px;background:#E6E3DC;overflow:hidden;
  min-width:70px}
.bar>span{display:block;height:100%;background:var(--mid)}
.lead>span{background:var(--top)}
.flag{color:#8C4B54;background:#F7E7E9;border-left:4px solid var(--flag);
  padding:12px 16px;border-radius:3px;margin:14px 0;font-size:14px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;
  color:var(--ink-soft);margin:6px 0 18px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:2px;
  margin-right:6px;vertical-align:-1px}
.cards{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0}
.card{flex:1 1 300px;background:var(--card);border:1px solid var(--rule);
  border-radius:3px;padding:18px 20px}
.card h3{font-family:Bahnschrift,"Segoe UI",sans-serif;font-size:16px;
  margin:0 0 2px}
.card .k{color:var(--ink-soft);font-size:12px;margin:0 0 12px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:4px 0;
  border-bottom:1px dotted var(--rule);font-size:13px}
.kv:last-child{border-bottom:none}
.kv b{font-family:"Cascadia Mono",Consolas,monospace;font-weight:600;
  font-variant-numeric:tabular-nums}
figure{margin:18px 0}
figcaption{color:var(--ink-soft);font-size:12.5px;margin-top:10px}
@media (max-width:640px){.wrap{padding:30px 16px 64px}h1{font-size:29px}
  table{font-size:13px}td,th{padding:7px 6px}}
@media print{body{background:#fff}.card,.verdict{break-inside:avoid}}
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
           f'aria-label="Share of videos versus share of views, by stratum">']

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

        strata = [("top", top_n, top_v, "var(--top)"),
                  ("mid", mid_n, mid_v, "var(--mid)"),
                  ("dead", dead_n, dead_v, "var(--dead)")]
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
                   f'y2="{top_pad + h}" stroke="var(--ink)" '
                   f'stroke-width="1" opacity=".5"/>')
        name = p["niche"][:24]
        out.append(
            f'<text x="{spine}" y="{top_pad - 12}" text-anchor="middle" '
            f'font-family="Bahnschrift,Segoe UI,sans-serif" font-size="14" '
            f'font-weight="600" fill="var(--ink)">{name}</text>')
        out.append(
            f'<text x="{spine}" y="{top_pad + h + 20}" text-anchor="middle" '
            f'font-size="11.5" fill="var(--ink-soft)">'
            f'floor: {dead_n:.0%} of videos, {dead_v:.2%} of views</text>')
        out.append(
            f'<text x="{spine}" y="{top_pad + h + 37}" text-anchor="middle" '
            f'font-size="11.5" fill="var(--ink-soft)">'
            f'top 10%: {top_v:.0%} of views</text>')
        out.append(
            f'<text x="{spine - colw/4}" y="{top_pad + h + 58}" '
            f'text-anchor="middle" font-size="10" letter-spacing=".1em" '
            f'fill="var(--ink-soft)">VIDEOS</text>')
        out.append(
            f'<text x="{spine + colw/4}" y="{top_pad + h + 58}" '
            f'text-anchor="middle" font-size="10" letter-spacing=".1em" '
            f'fill="var(--ink-soft)">VIEWS</text>')
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
             '<span><i style="background:var(--top)"></i>top 10% of videos</span>'
             '<span><i style="background:var(--mid)"></i>the contested middle</span>'
             '<span><i style="background:var(--dead)"></i>floor '
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
            f'<span class="k" style="font-size:11.5px;color:var(--ink-soft)">'
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
            ("Old vs new views/hr", (f'{p["durability"]:.2f}'
                                     if p.get("durability") else "not enough age spread")),
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
        for lvl in ("low", "mid", "high"):
            for dim, val in res["scored"][k][lvl].items():
                row[f"{dim}_{lvl}"] = val
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
