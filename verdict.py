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
    # PROVISIONAL-v2. Observed revenue-per-winner at mid RPM: $134
    # (home_espresso) to $3,260 (world_war_two) — log10 range 2.13 to
    # 3.51. The old anchor's ceiling ($1,000 / log10=3.0) was already
    # BELOW the real max, meaning ancient_history and world_war_two both
    # clamped to 100 and became indistinguishable despite a real 23%
    # difference in what they pay per winner. This is the exact clamping
    # problem flagged when this framework was first reviewed. New anchor
    # covers observed range with margin above the real ceiling.
    "log_revenue": (1.93, 3.81),
}

WEIGHTS = {
    "entry": 0.32,        # can a newcomer rank at all
    "reward": 0.32,       # what a win is worth
    "durability": 0.27,   # does the catalogue compound
    "effort": 0.09,       # cost per attempt — see note below
}
# effort's weight was cut from 0.15 to 0.09 (redistributed proportionally
# to the other three, which kept their relative ratio to each other).
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

def score_profile(prof, rpm, level="mid"):
    """
    Turn one profile into four dimension scores plus a composite, at one
    RPM level. Missing inputs propagate as None rather than defaulting to
    a middling value — a fabricated 50 would move the ranking while
    looking like evidence.
    """
    rate = float(rpm.get(f"rpm_{level}") or 0)

    # Entry as a GATE, not an average — deferred earlier for lack of
    # data, now built with 11 real niches behind it.
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

    # Revenue per successful video, not per niche: what one video that
    # reaches the top decile is worth.
    rev = (prof.get("p90_views") or 0) * rate / 1000.0
    reward = _lin(math.log10(rev) if rev > 0 else 0.0, *ANCHORS["log_revenue"])

    # Durability scored on log10 of itself — see ANCHORS["durability_log10"]
    # for why: the raw ratio spans 2.5 orders of magnitude and a linear
    # anchor could not represent that without clamping real extremes into
    # identical scores.
    dur_raw = prof.get("durability")
    dur_log = math.log10(dur_raw) if dur_raw and dur_raw > 0 else None
    durability = _lin(dur_log, *ANCHORS["durability_log10"])
    effort = _lin(prof.get("winner_duration_s"), *ANCHORS["effort_seconds"])

    dims = {"entry": entry, "reward": reward,
            "durability": durability, "effort": effort}

    # Re-normalise across whichever dimensions survived, so a niche is
    # not punished for a metric its dataset could not supply.
    usable = {k: v for k, v in dims.items() if v is not None}
    if usable:
        wsum = sum(WEIGHTS[k] for k in usable)
        composite = sum(v * WEIGHTS[k] for k, v in usable.items()) / wsum
    else:
        composite = None

    return {
        **dims,
        "composite": composite,
        "revenue_per_winner": rev,
        "rpm_used": rate,
        "dimensions_measured": len(usable),
    }


def _rank_order(scored, level):
    """Niche keys ordered best-first at one RPM level."""
    rows = [(k, s[level]["composite"]) for k, s in scored.items()
            if s[level]["composite"] is not None]
    return [k for k, _ in sorted(rows, key=lambda x: -x[1])]


def weight_sensitivity(scored, level="mid", alt_weightings=None):
    """
    How much does the composite RANKING move under different plausible
    weightings of entry/reward/durability/effort?

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

    dims = ("entry", "reward", "durability", "effort")
    rows = {k: s[level] for k, s in scored.items()
            if s[level].get("composite") is not None}
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
        "equal (25/25/25/25)": {"entry": 25, "reward": 25, "durability": 25, "effort": 25},
        "entry-heavy": {"entry": 40, "reward": 25, "durability": 20, "effort": 15},
        "durability-heavy": {"entry": 25, "reward": 25, "durability": 35, "effort": 15},
        "effort-zeroed": {"entry": 35, "reward": 35, "durability": 30, "effort": 0},
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
    Full comparison. Returns a dict carrying profiles, per-level scores,
    the ranking at each RPM level, and whether the ranking is stable.
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
        scored[key] = {lvl: score_profile(prof, rpms[key], lvl)
                       for lvl in ("low", "mid", "high")}

    if not profiles:
        return {"error": "no_usable_datasets", "skipped": skipped}

    orders = {lvl: _rank_order(scored, lvl) for lvl in ("low", "mid", "high")}
    stable = len({tuple(o) for o in orders.values()}) == 1

    # Do the dimensions disagree about the SAME niche pair? If the top
    # niche on one dimension is not the top on another by a wide margin,
    # a single ranking would hide the only fact that matters.
    conflicts = []
    if len(profiles) >= 2:
        for dim in ("entry", "reward", "durability", "effort"):
            vals = {k: scored[k]["mid"][dim] for k in profiles
                    if scored[k]["mid"][dim] is not None}
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
        "orders": orders,
        "stable_ranking": stable,
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

    Three shapes, chosen by what the data supports:
      - one niche leads everywhere -> name it
      - ranking survives the RPM band but dimensions conflict -> name it,
        then state what it costs
      - ranking flips with RPM -> refuse to rank, give the condition
    """
    if res.get("error"):
        return "Not enough data to compare."
    disp = {k: res["profiles"][k]["niche"] for k in res["profiles"]}
    mid = res["orders"]["mid"]
    if not mid:
        return "No niche had enough measurable dimensions to score."
    if len(mid) == 1:
        return f"Only {disp[mid[0]]} had a usable dataset."

    first, second = mid[0], mid[1]

    if not res["stable_ranking"]:
        lo_first = res["orders"]["low"][0]
        hi_first = res["orders"]["high"][0]
        return (
            f"The ranking depends on ad rates. At the low end of the RPM "
            f"band {disp[lo_first]} wins; at the high end {disp[hi_first]} "
            f"does. Settle your own RPM before treating either as the "
            f"answer — the measured differences are smaller than the "
            f"uncertainty in the rates."
        )

    if res["trade_off"]:
        theirs = [c for c in res["conflicts"] if c["leader"] == second]
        gained = ", ".join(_DIM_LABEL[c["dimension"]] for c in theirs[:2])
        return (
            f"{disp[first]} scores highest overall and the order holds "
            f"across the whole RPM band. But these niches suit different "
            f"operators: {disp[second]} is the better choice on "
            f"{gained}. Pick on which of those you can actually sustain."
        )

    return (f"{disp[first]} leads on every dimension measured, and the "
            f"order holds across the whole RPM band. It is the clearer "
            f"entry.")


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
    order = res["orders"]["mid"]
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
                 'main.py, so revenue is running on a flat fallback band. '
                 'Add the file to make the reward dimension meaningful.</div>')

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

    # ---- dimension scores ----
    H.append('<h2>Dimension scores</h2>')
    H.append('<p class="sub">At mid-band RPM. Bars are relative to the '
             'provisional anchors, not to each other.</p>')
    H.append('<table><tr><th>niche</th>'
             + "".join(f'<th>{_DIM_LABEL[d]}</th>' for d in
                       ("entry", "reward", "durability", "effort"))
             + '<th class="num">overall</th></tr>')
    for k in order:
        s = S[k]["mid"]
        H.append(f'<tr><td><b>{disp[k]}</b></td>')
        for d in ("entry", "reward", "durability", "effort"):
            H.append(_bar(s[d], lead=(k == lead)))
        comp = f'{s["composite"]:.0f}' if s["composite"] is not None else "—"
        H.append(f'<td class="num"><b>{comp}</b></td></tr>')
    H.append('</table>')

    # ---- RPM sensitivity ----
    H.append('<h2>Does the answer survive the ad rates?</h2>')
    H.append('<table><tr><th>niche</th><th class="num">RPM low</th>'
             '<th class="num">mid</th><th class="num">high</th>'
             '<th class="num">$ per top-decile video (mid)</th>'
             '<th>rate confidence</th></tr>')
    for k in order:
        r, s = R[k], S[k]["mid"]
        H.append(
            f'<tr><td><b>{disp[k]}</b><br>'
            f'<span class="k" style="font-size:11.5px;color:var(--ink-soft)">'
            f'matched: {r.get("label") or r.get("niche_key")}</span></td>'
            f'<td class="num">${_num(r.get("rpm_low"), 0)}</td>'
            f'<td class="num">${_num(r.get("rpm_mid"), 0)}</td>'
            f'<td class="num">${_num(r.get("rpm_high"), 0)}</td>'
            f'<td class="num">${_num(s["revenue_per_winner"], 0)}</td>'
            f'<td>{r.get("confidence") or "—"}</td></tr>')
    H.append('</table>')

    ranks = " &rarr; ".join(
        f'<b>{lvl}:</b> ' + ", ".join(disp[k] for k in res["orders"][lvl])
        for lvl in ("low", "mid", "high"))
    H.append(f'<p class="note">Ranking at each RPM level — {ranks}. '
             + ("The order does not change, so ad-rate uncertainty is not "
                "driving this result."
                if res["stable_ranking"] else
                "<b>The order changes.</b> The measured differences are "
                "smaller than the uncertainty in the rates.") + '</p>')

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
                       for k in res["orders"]["mid"][:3])
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
