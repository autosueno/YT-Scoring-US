"""
calibration.py — thresholds derived from measured niches, not assumption.

WHY THIS MODULE EXISTS
----------------------
The spread label lived in report.py as `>= 15x = winner-take-all`, and a
second, different set of thresholds lived in compare.py. Both were
invented. Neither knew about the other, so they could disagree about the
same niche — and did.

Worse, the report.py cutoff did not discriminate at all. Measured across
eleven real niches:

    niches labelled "winner-take-all" : 11 of 11
    niches labelled "top-heavy"       :  0
    niches labelled "flat"            :  0

A label with one possible output carries no information.

WHAT REPLACED IT
----------------
The old ratio was P90 / P50 — a hit measured against the MEDIAN video.
In every niche measured, the median video is effectively dead:

    world_war_two   median =  143 views
    excel           median =  122 views
    finance_basics  median =  119 views

Those are not videos that competed and lost; they are uploads that never
entered the market. So P90/P50 mostly counts how much abandoned content
a niche contains, which is why every niche tripped the same label.

The replacement is P90 / P75 — a hit measured against a DECENT video,
one better than three quarters of the field. That answers the question a
creator actually has: if I do solid work, how far am I from a real win?

The two metrics rank niches almost inversely at the extremes.
ancient_history reads as the harshest niche in the set under P90/P50
(1,308x) and lands mid-pack under P90/P75 (8.5x) — its decent videos
already pull ~35,000 views; it simply has a large dead floor beneath
them. excel_productivity looks mild at 281x and is among the most
brutal at 13.5x.

ROBUSTNESS, TESTED NOT ASSUMED
------------------------------
Removing the top 5% of videos entirely — every viral outlier — moves
P90/P75 by under 5% (4.4x -> 4.6x on home_espresso). The same edit moves
P90/P50 by 36%. Percentile ratios anchored above the floor are immune to
outliers by construction: a video with millions of views sits at P99+,
above both measuring points, so it cannot drag either.

Off-topic contamination is the real weakness. Injecting 20% of videos
drawn from other niches inflates P90/P75 by a median of 38%. But it
inflates every niche roughly equally, so the RANKING survives: rank
correlation between clean and 20%-contaminated sets is 0.955. Because
the label is a tercile — which third of markets is this — a uniform
inflation does not change the answer. This is also why the reading is
reported as a BAND rather than a point: the precision is not there.

PROVISIONAL - v2
----------------
These come from eleven niches, all US-targeted English content, all
collected by this engine at a 365-day window. They describe that
sampling as much as they describe YouTube. Firm them at ~20 niches, and
re-derive if the collector's sampling changes.
"""

# ============================================================
# SPREAD — graded SEPARATELY for large and small markets
# ============================================================
# A single shared scale does not work, and the calibration set shows why:
#
#   LARGE niches   ratio 8.45 - 13.53   (median 10.9)
#   small niches   ratio 4.31 - 13.53   (median  6.2)
#
# No large niche in the set falls below 8.45. Under one shared boundary
# at 6.9x, "gentle" is literally unreachable for a large market — the
# label exists but no large niche can ever earn it, while 4 of 7 small
# niches do. That is not a measurement, it is a scale mismatch.
#
# The cause is structural rather than accidental. Large markets carry
# more amateur uploads AND bigger breakouts, so the distance from decent
# to hit is wider everywhere in them. Judging a large niche against
# small-niche numbers just reports that it is large, which the ceiling
# already said.
#
# So each size class gets its own terciles, derived from its own group.
# "Gentle" then means gentle FOR A MARKET THIS SIZE — which is the only
# comparison a creator can act on, because they are choosing between
# niches of a given size, not against YouTube in the abstract.

SPREAD_METRIC = "P90/P75"

# Terciles within the large group (4 niches, 8.45-13.53).
LARGE_GENTLE_MAX = 8.7
LARGE_BRUTAL_MIN = 13.1

# Terciles within the small group (7 niches, 4.31-13.53).
SMALL_GENTLE_MAX = 5.0
SMALL_BRUTAL_MIN = 12.7

# The four constants above are LABEL boundaries — tercile cut points,
# meant so roughly a third of niches fall on each side, for the
# gentle/normal/brutal three-way classification. A tercile cut point is
# NOT the same thing as the population's own min/max, and using one as a
# continuous 0-100 scoring anchor is a mistake with a real empirical
# consequence, not a merely theoretical one: on the calibration
# population itself, roughly a third of observations sit past a tercile
# boundary BY CONSTRUCTION, so a scoring function anchored there tends
# strongly toward clamping them — whether a given point actually clamps
# still depends on the scoring direction and the exact implementation,
# so "tends strongly toward" is the accurate claim, not "guarantees."
# What is not in question is the measured outcome: found this exact bug
# while building verdict.py's reliability dimension, which originally
# reused LARGE_GENTLE_MAX/BRUTAL_MIN as _lin anchors directly — 7 of 11
# niches came out clamped at or within a point of 0 or 100, checked
# directly, not inferred. These two constants are the actual observed
# range instead, for anything that needs a
# continuous score rather than a three-way label.
LARGE_RATIO_MIN = 8.45
LARGE_RATIO_MAX = 13.53
SMALL_RATIO_MIN = 4.31
SMALL_RATIO_MAX = 13.53

SPREAD_CALIBRATION_N = 11
SPREAD_CALIBRATION_LARGE_N = 4
SPREAD_CALIBRATION_SMALL_N = 7

# The large group rests on FOUR niches. Terciles from four points are
# barely terciles — the p33 and p67 are essentially the 2nd and 3rd
# values. Treat large-market readings as provisional in a way the small
# ones are not, and re-derive once more large niches exist.
LARGE_THRESHOLDS_ARE_THIN = True

# ============================================================
# CEILING — is this a large market or a small one?
# ============================================================
# The eleven ceilings (P90 views) form two clusters with a clear gap:
#
#   32k 34k 34k 42k 48k 51k 95k  |  211k 248k 296k 362k
#                                 ^
#              largest jump in the ladder: 2.21x
#
# Nothing lands between 95k and 211k, so the cut goes in that gap.
# 100k is inside it, round, and easy to state.
CEILING_LARGE_MIN = 100_000

# ============================================================
# CONTAMINATION — when to distrust the reading
# ============================================================
# Search pulls in adjacent content; that is expected and usually fine.
# But a niche where a large share of videos match none of its own topic
# clusters is probably measuring something broader than the niche, and
# its spread will read high for a reason that has nothing to do with the
# market. Warn rather than silently report a number.
CONTAMINATION_WARN_SHARE = 0.25


def thresholds_for(size_band):
    """The (gentle_max, brutal_min) pair for a market size."""
    if size_band == "large":
        return LARGE_GENTLE_MAX, LARGE_BRUTAL_MIN
    return SMALL_GENTLE_MAX, SMALL_BRUTAL_MIN


def observed_range_for(size_band):
    """
    The (min, max) OBSERVED ratio for a market size — the population's
    own extremes, with no margin applied here (callers scoring
    continuously should add their own, the way every other anchor in
    this project does; this function only reports what was measured).

    Use this for continuous scoring. Use thresholds_for() for the
    three-way gentle/normal/brutal label. They answer different
    questions and are not interchangeable — see the comment above
    LARGE_RATIO_MIN for the bug that happens when they are swapped.
    """
    if size_band == "large":
        return LARGE_RATIO_MIN, LARGE_RATIO_MAX
    return SMALL_RATIO_MIN, SMALL_RATIO_MAX


def spread_band(ratio, size_band="small"):
    """
    ('gentle'|'normal'|'brutal', plain-English reading) for a P90/P75
    ratio, JUDGED AGAINST OTHER MARKETS OF THE SAME SIZE.

    size_band matters: 8.5x is the gentlest reading in the large group
    and firmly brutal in the small one. Grading both on one scale would
    call the same number two different things — or, worse, the same
    thing when it means two different things.
    """
    if ratio is None or ratio != ratio or ratio <= 0:
        return None, None
    gentle_max, brutal_min = thresholds_for(size_band)
    peers = "large markets" if size_band == "large" else "markets this size"

    if ratio < gentle_max:
        return "gentle", (
            f"gentle for {peers} — solid work lands a solid result. A "
            f"decent video already earns a meaningful share of what a hit "
            f"earns, so you can build without needing a breakout")
    if ratio < brutal_min:
        return "normal", (
            f"normal for {peers} — hits clearly outrun decent videos, but "
            f"competent work still earns real views")
    return "brutal", (
        f"brutal even for {peers} — a decent video earns a small fraction "
        f"of a hit. Being good is not enough here; almost everything goes "
        f"to breakouts")


def ceiling_band(p90_views):
    """('large'|'small', reading) from a niche's top-10% view count."""
    if not p90_views or p90_views != p90_views:
        return None, None
    if p90_views >= CEILING_LARGE_MIN:
        return "large", (
            f"large market — a hit here reaches ~{p90_views:,.0f} views, "
            f"so the prize is big, but so is the crowd")
    return "small", (
        f"small market — a hit here reaches ~{p90_views:,.0f} views. "
        f"Lower ceiling, but usually a thinner field to climb through")


def spread_range_text(ratio):
    """
    Report the ratio as a BAND, not a point.

    38% median distortion under contamination means the second decimal
    is fiction. '≈8-9x' is honest where '8.53x' is not.
    """
    if ratio is None or ratio != ratio or ratio <= 0:
        return "—"
    lo = max(1.0, ratio * 0.85)
    hi = ratio * 1.15
    return f"~{lo:.0f}-{hi:.0f}x"


def describe(p90_views, p75_views, contamination_share=None):
    """
    The two-axis reading for one niche, graded on its own size class.

    ORDER MATTERS: size is determined FIRST, then the spread is judged
    against markets of that size. The alternative — one scale for
    everything — makes "gentle" unreachable for large markets and
    reports scale mismatch as if it were market difficulty.

    Ceiling and spread are still reported separately and never blended.
    They are independent: personal_psychology is small with a brutal
    spread; ancient_history is large with a normal one. A single combined
    score would hide exactly the distinction a creator is choosing
    between — a big prize on a lottery, or a modest one on a steady
    climb.
    """
    out = {"ratio": None, "spread_band": None, "spread_text": None,
           "ceiling_band": None, "ceiling_text": None,
           "decent_pay": None, "warning": None, "thin_calibration": False}

    # 1. How big is this market?
    out["ceiling_band"], out["ceiling_text"] = ceiling_band(p90_views)
    size = out["ceiling_band"] or "small"

    # 2. Judge the climb against markets of THAT size.
    if p90_views and p75_views and p75_views > 0:
        out["ratio"] = p90_views / p75_views
        out["spread_band"], out["spread_text"] = spread_band(out["ratio"], size)
        out["decent_pay"] = p75_views

    if size == "large" and LARGE_THRESHOLDS_ARE_THIN:
        out["thin_calibration"] = True

    if (contamination_share is not None
            and contamination_share >= CONTAMINATION_WARN_SHARE):
        out["warning"] = (
            f"{contamination_share:.0%} of videos matched none of this "
            f"niche's own topics, so the collection is probably broader "
            f"than the niche. Spread reads high when that happens — "
            f"treat it as an upper bound.")
    return out


# ============================================================
# TIER 2 — DEMAND / SUPPLY / OPPORTUNITY, RELATIVE TO YOUR LIBRARY
# ============================================================
# WHY RELATIVE AND NOT ABSOLUTE
# ------------------------------
# Option 5 originally graded a niche against a baseline built from that
# SAME niche. That is arithmetic, not measurement: a niche's own median
# sits at percentile 50 of itself by definition, its own P90 at
# percentile 90, and DEMAND_TAIL_WEIGHT=0.35 blends those into exactly
# 0.65*50 + 0.35*90 = 64.0 — for every metric, in every niche. Supply
# came out to exactly 50.0 the same way. Verified on eleven real,
# structurally different niches: all eleven returned identical pillar
# scores. The self-comparison told you about the FORMULA, not the
# market.
#
# crossniche.py fixes the mechanism: grade a niche against the OTHER
# niches in the library (leave-one-out, so a niche is never in its own
# reference). That makes the scores vary — but the config.py thresholds
# (VERDICT_HIGH=55, VERDICT_LOW=45, and the 9-band GRADE_THRESHOLDS
# ladder) were tuned for the old self-comparison scale, where demand
# naturally centred near 64. Run against the real 11-niche pool:
#
#     demand   ranges 54.2 to 67.9   (10 of 11 clear the old HIGH=55)
#     supply   ranges 47.6 to 54.7   (NONE clear HIGH=55, none reach LOW=45)
#
# The old thresholds could only ever land in two of the four verdict
# quadrants with this pool, which is why 10 of 11 niches came back
# "Moderate opportunity" — a label problem identical in shape to
# Tier 1's "winner-take-all on 11 of 11 niches", fixed the same way:
# derive the boundary from what was actually measured.
#
# WHAT "RELATIVE" MEANS HERE, AND ITS ONE REAL COST
# --------------------------------------------------
# "High demand" means top third of the CURRENT LIBRARY, not top third of
# YouTube. That is honest given eleven niches — there is no absolute
# anchor for "abundant supply" that eleven points could support — but it
# has a real consequence: these thresholds MOVE as more niches are
# added. A niche graded "high demand" today could read "average" once a
# handful of exceptional niches join the pool. That is the metric doing
# its job, not a bug, but the report says so plainly rather than
# presenting a relative number as if it were absolute.
TIER2_POOL_N = 11

DEMAND_LOW_MAX = 59.0     # p33 of the 11-niche pool
DEMAND_HIGH_MIN = 61.8    # p67

SUPPLY_LOW_MAX = 49.0     # p33
SUPPLY_HIGH_MIN = 50.6    # p67

# Opportunity terciles for a 5-band grade ladder collapse almost on top
# of each other at n=11 (p50=54.8, p67=55.0 — a 0.2-point gap), which
# would make two letter grades differ by noise. Nine bands need more
# points than eleven niches can support. So relative_grade() below uses
# a min-max STRETCH across the pool's observed range instead of
# percentile bands — coarser, but it does not manufacture false
# precision from a gap this small.
OPPORTUNITY_POOL_MIN = 47.0
OPPORTUNITY_POOL_MAX = 60.0


def relative_verdict_bands(demand, supply, pool_demand=None, pool_supply=None):
    """
    ('high'|'mid'|'low', 'high'|'mid'|'low') for demand and supply,
    relative to the ACTUAL pool being graded.

    pool_demand / pool_supply: the demand/supply scores of every niche
    in the current sweep (including this one). Terciles are computed
    from them directly. Without a pool, falls back to the fixed
    DEMAND_LOW_MAX etc below — those came from ONE specific 11-niche
    run and are only a reasonable stand-in when no live pool is
    available; passing the real pool is always preferred and is what
    crossniche.py does.
    """
    import pandas as pd

    def band_from_pool(v, pool, fallback_lo, fallback_hi):
        if v is None or v != v:
            return None
        vals = pd.Series([p for p in (pool or []) if p is not None and p == p])
        if len(vals) >= 3:
            lo, hi = vals.quantile(0.33), vals.quantile(0.67)
        else:
            lo, hi = fallback_lo, fallback_hi
        if v <= lo:
            return "low"
        if v >= hi:
            return "high"
        return "mid"

    return (band_from_pool(demand, pool_demand, DEMAND_LOW_MAX, DEMAND_HIGH_MIN),
            band_from_pool(supply, pool_supply, SUPPLY_LOW_MAX, SUPPLY_HIGH_MIN))


def relative_grade(opportunity_score, pool_scores=None):
    """
    A letter grade stretched across the pool's OBSERVED range.

    pool_scores: opportunity scores of every niche in the current sweep.
    Preferred over the fixed OPPORTUNITY_POOL_MIN/MAX below, which came
    from one specific run — grading against a range that does not match
    the data being graded reproduces the exact collapse this module
    exists to fix. A run whose scores sit outside that fixed 47-60
    window clamps everything to the same letter, silently.

    Five bands (A/B/C/D/F) rather than the absolute ladder's nine,
    matched to what a small pool can actually support without two
    letters differing by noise.
    """
    if opportunity_score is None or opportunity_score != opportunity_score:
        return None
    if pool_scores:
        import pandas as pd
        vals = pd.Series([s for s in pool_scores if s is not None and s == s])
        if len(vals) >= 3:
            lo, hi = float(vals.min()), float(vals.max())
        else:
            lo, hi = OPPORTUNITY_POOL_MIN, OPPORTUNITY_POOL_MAX
    else:
        lo, hi = OPPORTUNITY_POOL_MIN, OPPORTUNITY_POOL_MAX
    if hi <= lo:
        return "C"
    pct = (opportunity_score - lo) / (hi - lo) * 100
    pct = max(0.0, min(100.0, pct))
    if pct >= 80:
        return "A"
    if pct >= 60:
        return "B"
    if pct >= 40:
        return "C"
    if pct >= 20:
        return "D"
    return "F"
