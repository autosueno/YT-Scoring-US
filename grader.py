"""
grader.py

Market-intelligence scoring. A topic is evaluated as a MARKET: the engine
measures three pillars against the niche baseline, then DERIVES opportunity
from the balance between demand and supply.

    Inferred Demand   how much the audience wants this (consumption,
                      engagement, longevity) — niche-relative percentile.
    Content Supply    how much content the market produces / how saturated
                      it is — niche-relative, higher = more supply.
    Momentum          is demand growing faster than supply (market
                      strengthening)?

    Opportunity       DERIVED, not measured in parallel:
                        opportunity = 50 + (demand - supply)/2
                        then nudged by momentum.
                      This encodes "opportunity exists where inferred
                      demand exceeds content supply." The 2×2 verdict
                      (high/low demand × high/low supply) is attached.

Opportunity is the headline score. Demand, Supply, and Momentum each keep
their own score + letter grade so the report can explain WHY.

All niche-level inputs (supply/momentum/competition/opportunity summaries)
come from the baseline, which loads them from the niche's insights.json.
The same summaries are recomputed for the topic via insights.py so the
comparison is apples-to-apples.
"""

import math

import insights
from baseline import Baseline
from config import (
    SUPPLY_METRICS,
    DEMAND_METRIC_GROUPS, DEMAND_GROUP_WEIGHTS,
    DEMAND_TAIL_WEIGHT, DEMAND_TAIL_PERCENTILE, DIRECTIONAL_LOG_SCALE,
    OPPORTUNITY_MOMENTUM_INFLUENCE, OPPORTUNITY_INDICATOR_KEYS,
    VERDICT_HIGH, VERDICT_LOW, GRADE_THRESHOLDS, MOMENTUM_GRADE_THRESHOLDS,
    MOMENTUM_LOG_SCALE, MOMENTUM_COMPONENT_WEIGHTS,
)


# ============================================================
# PUBLIC API
# ============================================================

def grade_topic(df, baseline: Baseline, topic: str) -> dict:
    """Evaluate a topic as a market against the niche baseline."""
    if df.empty:
        return _empty_result(topic, "topic")
    return _grade(df, baseline, topic, "topic")


def grade_niche(df, baseline: Baseline, niche: str) -> dict:
    """
    Evaluate a niche against a baseline.

    WHAT THIS IS GRADING AGAINST: when the baseline was built FROM this
    same niche (the normal case — option 5), you are comparing the niche
    to itself. That is not perfectly neutral, and the asymmetry between
    pillars is worth knowing rather than discovering by staring at
    unexplained numbers:

    - SUPPLY compares median-vs-median, so a true self-comparison lands
      at EXACTLY 50 — confirming nothing except that the comparison ran.
    - DEMAND blends each metric's median percentile with its P90 percentile
      (DEMAND_TAIL_WEIGHT=0.35, favouring "who's currently raving" over
      pure typicality). A median sits at the 50th percentile of its own
      distribution by definition; P90 sits at the 90th by definition. So a
      true self-comparison is STRUCTURALLY forced to
      0.65*50 + 0.35*90 = 64 — not because the niche is unusually strong,
      but because of how the formula is built. Expect demand ~64 on every
      niche self-grade; it is not a finding.
    - MOMENTUM is the only pillar that is genuinely informative here: it
      compares a RECENT window against a PRIOR one within the same data,
      so a self-grade can and does move away from a fixed constant.

    This function is actually informative when you pass a DIFFERENT
    niche's baseline — comparing "finance" videos against a "gaming"
    baseline, say — where none of demand/supply is tautological.
    """
    if df.empty:
        return _empty_result(niche, "niche")
    result = _grade(df, baseline, niche, "niche")
    result["self_comparison"] = (baseline.niche_name == niche)
    return result


# ============================================================
# CORE ENGINE
# ============================================================

def _grade(df, baseline, name, kind):
    demand = _demand_pillar(df, baseline)
    supply = _pillar(_supply_scores(df, baseline))
    momentum = _momentum_pillar(df, baseline)

    opportunity = _derive_opportunity(demand["score"], supply["score"], momentum)

    result = {
        "kind":         kind,
        kind:           name,
        "score":        opportunity["score"],        # headline = opportunity
        "grade":        _letter_grade(opportunity["score"]),
        "verdict":      opportunity["verdict"],
        "sample_size":  len(df),
        "pillars": {
            "demand":   demand,
            "supply":   supply,
            "momentum": momentum,
        },
        "opportunity":  opportunity,
        "indicators":   _opportunity_indicators(df),
        "content_diagnostics": _content_diagnostics(df),
        "highlights":   [],
    }
    if kind == "topic":
        result["niche"] = baseline.niche_name
    result["highlights"] = _highlights(df, baseline, result)
    return result


def _pillar(metric_scores: dict) -> dict:
    """Bundle per-metric scores into {score, grade, metrics}. Empty → 50."""
    usable = {k: v for k, v in metric_scores.items() if v is not None}
    score = sum(usable.values()) / len(usable) if usable else 50.0
    return {
        "score":   round(score, 1),
        "grade":   _letter_grade(score),
        "metrics": {k: round(v, 1) for k, v in usable.items()},
    }


# ============================================================
# PILLAR: INFERRED DEMAND (per-video percentile rank + evergreen)
# ============================================================

def _demand_scores(df, baseline) -> dict:
    """
    Score demand by GROUP so correlated metrics can't vote twice.

    Each group's members are averaged into one group score; groups then
    combine using DEMAND_GROUP_WEIGHTS. Individual metric scores are still
    returned (for transparency in the report) alongside the group scores.
    """
    scores = {}
    group_scores = {}

    for group, members in DEMAND_METRIC_GROUPS.items():
        member_scores = []
        for m in members:
            if m in df.columns and baseline.has_metric(m):
                s = _demand_metric_score(df[m], baseline, m)
                scores[m] = s          # transparency: report per-metric too
                member_scores.append(s)
        if member_scores:
            group_scores[group] = sum(member_scores) / len(member_scores)

    # Longevity: topic evergreen vs niche evergreen (higher = more durable).
    topic_mom = insights.momentum_summary(df)
    niche_mom = baseline.momentum()
    t_ev, n_ev = topic_mom.get("evergreen_score"), niche_mom.get("evergreen_score")
    ev = _directional_score(t_ev, n_ev, "higher")
    if ev is not None:
        scores["evergreen"] = ev

    return {"metrics": scores, "groups": group_scores, "evergreen": ev}


def _demand_pillar(df, baseline) -> dict:
    """Combine demand group scores by weight into the pillar score."""
    parts = _demand_scores(df, baseline)
    groups, metrics_seen, ev = parts["groups"], parts["metrics"], parts["evergreen"]

    if groups:
        total_w = sum(DEMAND_GROUP_WEIGHTS.get(g, 0) for g in groups) or 1.0
        score = sum(s * DEMAND_GROUP_WEIGHTS.get(g, 0) for g, s in groups.items()) / total_w
        # Evergreen is a modest longevity adjustment, not a co-equal group.
        if ev is not None:
            score = 0.85 * score + 0.15 * ev
    else:
        score = 50.0

    return {
        "score": round(score, 1),
        "grade": _letter_grade(score),
        "metrics": {k: round(v, 1) for k, v in metrics_seen.items()},
        # Each signal group carries its own score AND grade, so the demand
        # pillar can be read as a breakdown rather than one opaque number.
        "groups": {
            k: {"score": round(v, 1),
                "grade": _letter_grade(v),
                "weight": DEMAND_GROUP_WEIGHTS.get(k, 0)}
            for k, v in groups.items()
        },
    }


def _demand_metric_score(series, baseline, metric) -> float:
    """
    Blend the niche percentile of the topic's MEDIAN (typical performance)
    with the percentile of its P90 (upside / breakout ceiling), so a fat
    upper tail is rewarded instead of averaged away.

        score = (1-w) * pct(median) + w * pct(P90),   w = DEMAND_TAIL_WEIGHT
    """
    s = series.dropna()
    if s.empty:
        return 50.0
    median_pct = baseline.percentile_of(metric, float(s.median()))
    if DEMAND_TAIL_WEIGHT <= 0:
        return median_pct
    tail_val = float(s.quantile(DEMAND_TAIL_PERCENTILE / 100.0))
    tail_pct = baseline.percentile_of(metric, tail_val)
    return (1.0 - DEMAND_TAIL_WEIGHT) * median_pct + DEMAND_TAIL_WEIGHT * tail_pct


# ============================================================
# PILLAR: CONTENT SUPPLY (niche-level, directional; higher = saturated)
# ============================================================

def _supply_scores(df, baseline) -> dict:
    topic_supply = insights.supply_summary(df)
    niche_supply = baseline.supply()
    niche_comp = baseline.competition()
    topic_comp = insights.competition_summary(df)
    if not niche_supply and not niche_comp:
        return {}

    scores = {}
    for metric, direction in SUPPLY_METRICS.items():
        # median_subscribers lives in the competition summary; others in supply.
        if metric == "median_subscribers":
            t, n = topic_comp.get(metric), niche_comp.get(metric)
        else:
            t, n = topic_supply.get(metric), niche_supply.get(metric)
        s = _directional_score(t, n, direction)
        if s is not None:
            scores[metric] = s
    return scores


# ============================================================
# PILLAR: MOMENTUM (demand growth vs supply growth)
# ============================================================

def _momentum_pillar(df, baseline) -> dict:
    """
    Momentum pillar with an honesty guard: if the recent/prior windows don't
    hold enough videos (common when a sample is smeared across years), the
    growth ratios are computed from near-empty buckets and mean nothing — so
    the pillar ABSTAINS instead of emitting a fabricated score. An abstained
    pillar is excluded from the opportunity derivation entirely.
    """
    mom = insights.momentum_summary(df)
    if not mom.get("sufficient_data", False):
        recent_n = mom.get("recent_window_videos", 0)
        prior_n = mom.get("prior_window_videos", 0)
        return {
            "score":     None,
            "grade":     "—",
            "abstained": True,
            "reason":    (f"insufficient recent data "
                          f"(recent window: {recent_n}, prior: {prior_n} videos)"),
            "metrics":   {},
        }
    signed_scores = _momentum_scores(df, baseline)
    if not signed_scores:
        return {"score": 50.0, "grade": "C", "abstained": False,
                "signed_change": 0.0, "metrics": {}}

    # Weighted average IN SIGNED SPACE — the components are commensurable
    # there (all "recent vs reference", same units, same zero point), so
    # weighting them is meaningful. Weighting 0-100 values that had
    # different zero-points, as an earlier version effectively did, is not.
    total_w = sum(MOMENTUM_COMPONENT_WEIGHTS.get(k, 0.0) for k in signed_scores)
    if total_w <= 0:
        signed = sum(signed_scores.values()) / len(signed_scores)
    else:
        signed = sum(v * MOMENTUM_COMPONENT_WEIGHTS.get(k, 0.0)
                     for k, v in signed_scores.items()) / total_w
    signed = max(-100.0, min(100.0, signed))

    p = {
        "signed_change": round(signed, 1),
        # 0-100 view DERIVED from the signed value (not the reverse), kept
        # so the opportunity formula and older consumers keep working.
        "score": round(50.0 + signed / 2.0, 1),
        "grade": _momentum_letter_grade(signed),
        "abstained": False,
        "metrics": {k: round(v, 1) for k, v in signed_scores.items()},
        "weights": {k: MOMENTUM_COMPONENT_WEIGHTS.get(k, 0.0)
                    for k in signed_scores},
    }
    return p


def _signed_log(observed, reference):
    """
    The engine's momentum primitive: a signed, symmetric, log-scaled
    comparison of an observed quantity against its reference.

        signed = MOMENTUM_LOG_SCALE * log10(observed / reference)

    clamped to +-100. Properties that make this the right shape:
      - 0 means observed == reference (true breakeven, no rescaling needed
        to land there),
      - doubling and halving give exactly +30 and -30 — equal magnitudes,
        which a LINEAR scale cannot do (there, +1.0 vs -0.5),
      - one order of magnitude fills the scale exactly (10x -> +100),
      - extremes compress, so a 50x fluke does not run away from a 10x
        genuine surge.
    """
    if observed is None or reference is None:
        return None
    o, r = float(observed), float(reference)
    if r <= 0 or o <= 0:
        if o == r:
            return 0.0
        return 60.0 if o > r else -60.0     # directional but not precise
    return max(-100.0, min(100.0, MOMENTUM_LOG_SCALE * math.log10(o / r)))


def _momentum_scores(df, baseline) -> dict:
    """
    Momentum components, each on the SIGNED -100..+100 log scale where
    0 = breakeven. The signed scale IS the pipeline here; the 0-100 view
    is derived from it afterwards, not the other way round.

    Every component answers the same shaped question — "how does the
    recent period compare to its reference?" — so they are directly
    commensurable and can be weighted against each other meaningfully:

      demand_vs_supply_growth   demand growth vs supply growth
      recent_breakout_frequency recent breakout rate vs this niche's own
                                prior-window rate
      evergreen_persistence     old videos' views/hour vs new videos'
                                (reference 1.0 = no decay)
    """
    mom = insights.momentum_summary(df)
    scores = {}

    dg, sg = mom.get("demand_growth"), mom.get("supply_growth")
    if dg is not None and sg is not None:
        s = _signed_log(dg, sg)
        if s is not None:
            scores["demand_vs_supply_growth"] = s
    elif dg is not None:
        s = _signed_log(dg, 1.0)
        if s is not None:
            scores["demand_vs_supply_growth"] = s

    rbf, pbf = mom.get("recent_breakout_frequency"), mom.get("prior_breakout_frequency")
    if rbf is not None and pbf is not None and pbf > 0:
        s = _signed_log(rbf, pbf)
        if s is not None:
            scores["recent_breakout_frequency"] = s
    elif rbf is not None and pbf == 0:
        # No breakouts at all in the prior window: any recent breakout is
        # real acceleration, but there is no ratio to take. Stay modest.
        scores["recent_breakout_frequency"] = 20.0 if rbf > 0 else 0.0

    ev = mom.get("evergreen_score")
    if ev is not None:
        s = _signed_log(ev, 1.0)     # 1.0 = old videos hold up as well as new
        if s is not None:
            scores["evergreen_persistence"] = s

    return scores


# ============================================================
# DERIVED OPPORTUNITY (+ 2×2 verdict)
# ============================================================

def _derive_opportunity(demand: float, supply: float, momentum_pillar: dict) -> dict:
    """
    Opportunity from market imbalance:
        base = 50 + (demand - supply)/2      # demand above supply → > 50
        score = base + (momentum-50)*INFLUENCE

    If momentum abstained (insufficient recent data), it does NOT nudge the
    score — opportunity rests on the demand-vs-supply imbalance alone rather
    than being pushed around by a momentum number we don't trust.
    Clamped to 0–100. Verdict from the demand×supply quadrant.
    """
    base = 50.0 + (demand - supply) / 2.0

    signed_momentum = momentum_pillar.get("signed_change")
    abstained = momentum_pillar.get("abstained", False) or signed_momentum is None
    if abstained:
        score = base
    else:
        # Read the SIGNED value directly. Momentum's natural neutral is 0,
        # so a breakeven niche contributes exactly nothing — no nudge in
        # either direction. The previous form, (score-50)*INFLUENCE, was
        # arithmetically equivalent ONLY once momentum's zero-point was
        # correct; while two of its components were mis-centred, every
        # niche got a silent downward nudge regardless of what was
        # actually happening in it.
        score = base + signed_momentum * OPPORTUNITY_MOMENTUM_INFLUENCE
    score = max(0.0, min(100.0, score))

    d_high, d_low = demand >= VERDICT_HIGH, demand < VERDICT_LOW
    s_high, s_low = supply >= VERDICT_HIGH, supply < VERDICT_LOW
    imbalance = demand - supply

    # Verdict considers the IMBALANCE first, then the quadrant for colour.
    #
    # Quadrant-only logic had a dead zone: a topic with demand 85 and supply
    # 48 hit neither "supply high" (>=55) nor "supply low" (<45), so it fell
    # through to "Balanced market" despite a +37 imbalance — plainly wrong.
    # Imbalance is the actual signal; the quadrant just describes the shape.
    if d_high and s_low:
        verdict = "Excellent opportunity — high demand, low supply (underserved)"
    elif d_low and s_high:
        verdict = "Poor opportunity — low demand, high supply (saturated)"
    elif imbalance >= 20:
        shape = "high demand" if d_high else "demand"
        verdict = f"Strong opportunity — {shape} clearly outpaces supply"
    elif imbalance >= 8:
        verdict = "Moderate opportunity — demand runs ahead of supply"
    elif imbalance <= -20:
        verdict = "Weak opportunity — supply clearly outpaces demand"
    elif imbalance <= -8:
        verdict = "Cautious — supply runs ahead of demand"
    elif d_high and s_high:
        verdict = "Competitive market — high demand, but high supply too"
    elif d_low and s_low:
        verdict = "Quiet market — low demand, low supply"
    else:
        verdict = "Balanced market — demand and supply are near niche parity"

    return {
        "score":         round(score, 1),
        "verdict":       verdict,
        "demand_score":  round(demand, 1),
        "supply_score":  round(supply, 1),
        "momentum_signed": (round(signed_momentum, 1)
                            if signed_momentum is not None else None),
        "momentum_score": (round(50.0 + signed_momentum / 2.0, 1)
                           if signed_momentum is not None else None),
        "momentum_abstained": abstained,
        "imbalance":     round(demand - supply, 1),
    }


def _opportunity_indicators(df) -> dict:
    ind = insights.opportunity_indicators(df)
    return {k: ind.get(k) for k in OPPORTUNITY_INDICATOR_KEYS if ind.get(k) is not None}


def _content_diagnostics(df) -> dict:
    """
    Content signals are reported as DIAGNOSTICS, not scored — per the market
    model, content packaging is not one of the four market pillars. Kept
    because it's useful context for creators.
    """
    prof = insights.title_pattern_summary(df)
    keep = ["pct_has_tutorial", "pct_has_comparison", "pct_has_prediction",
            "pct_has_question", "avg_title_sentiment"]
    return {k: round(prof[k], 1) for k in keep if prof.get(k) is not None}


# ============================================================
# DIRECTIONAL COMPARISON + LETTER GRADE
# ============================================================

def _directional_score(topic_val, niche_val, direction):
    """
    Bounded 0–100 comparison of a topic scalar vs the niche scalar, using a
    LOG-RATIO so it preserves resolution across orders of magnitude instead
    of saturating near 99 (the old topic/(topic+niche) form pinned a 10x and
    a 100x market to nearly the same score).

        r      = log10(topic / niche)          # 0 at parity
        score  = 50 + DIRECTIONAL_LOG_SCALE * r  (clamped 0–100)

    "higher" means higher-topic → higher score; "lower" flips the sign so
    lower-topic → higher score. 50 = niche parity. Safe on zeros/None.
    """
    if topic_val is None or niche_val is None:
        return None
    t, n = float(topic_val), float(niche_val)
    if n <= 0 or t <= 0:
        # Can't take a log ratio; fall back to a coarse parity/edge read.
        if t == n:
            return 50.0
        better = (t > n) if direction == "higher" else (t < n)
        return 75.0 if better else 25.0
    r = math.log10(t / n)
    if direction == "lower":
        r = -r
    return max(0.0, min(100.0, 50.0 + DIRECTIONAL_LOG_SCALE * r))


def _letter_grade(score: float) -> str:
    for letter, threshold in sorted(GRADE_THRESHOLDS.items(), key=lambda x: -x[1]):
        if score >= threshold:
            return letter
    return "F"


def _momentum_letter_grade(signed_change: float) -> str:
    """Letter grade from momentum's SIGNED (-100..+100, 0=breakeven) value,
    using MOMENTUM_GRADE_THRESHOLDS' wider neutral band instead of the
    generic 0-100 GRADE_THRESHOLDS."""
    for letter, threshold in sorted(MOMENTUM_GRADE_THRESHOLDS.items(),
                                    key=lambda x: -x[1]):
        if signed_change >= threshold:
            return letter
    return "F"


# ============================================================
# HIGHLIGHTS
# ============================================================

def _highlights(df, baseline, result) -> list:
    hi = []
    opp = result["opportunity"]

    hi.append(opp["verdict"] + ".")

    if baseline.has_metric("views_per_hour") and "views_per_hour" in df.columns:
        pct = baseline.percentile_of("views_per_hour", _safe_median(df["views_per_hour"]))
        hi.append(f"Demand: views/hour at the niche's {pct:.0f}th percentile.")

    supply_metrics = result["pillars"]["supply"]["metrics"]
    if "content_freshness" in supply_metrics:
        s = supply_metrics["content_freshness"]
        word = "above" if s >= 55 else ("below" if s < 45 else "near")
        hi.append(f"Supply: content freshness is {word} the niche norm.")

    mom_pillar = result["pillars"]["momentum"]
    if not mom_pillar.get("abstained") and mom_pillar.get("score") is not None:
        # Derived from the SAME score that produced the grade — previously
        # this used insights.momentum_summary()'s raw trend_direction
        # (demand_growth alone), which could say "rising" while the
        # composite grade read D/F, because trend_direction never saw the
        # evergreen/breakout components dragging the grade down. One
        # number now drives both the label and the grade, so they cannot
        # disagree.
        signed = mom_pillar.get("signed_change", 0.0)
        label = ("heating up" if signed >= 15 else
                 "cooling off" if signed <= -15 else "steady")
        hi.append(f"Momentum: {label} ({signed:+.0f} vs. breakeven, "
                  f"{mom_pillar['grade']}).")
    elif mom_pillar.get("abstained"):
        hi.append("Momentum: not scored — sample lacks enough recent videos "
                  "for a reliable trend (widen collection recency or refresh more often).")

    ind = result["indicators"]
    if ind.get("views_per_upload") is not None:
        hi.append(f"Views per upload: {ind['views_per_upload']:,.0f}.")
    if ind.get("small_creator_success_rate") is not None:
        hi.append(f"Small-creator success rate: {ind['small_creator_success_rate']*100:.0f}% of overperformers.")

    # Pillar summary line.
    p = result["pillars"]
    mom_p = p["momentum"]
    mom_txt = "not scored" if (mom_p.get("abstained") or mom_p.get("score") is None) \
        else f"{mom_p['score']:.0f} ({mom_p['grade']})"
    hi.append(
        f"Pillars — demand {p['demand']['score']:.0f} ({p['demand']['grade']}), "
        f"supply {p['supply']['score']:.0f} ({p['supply']['grade']}), "
        f"momentum {mom_txt}."
    )
    return hi


# ============================================================
# HELPERS
# ============================================================

def _safe_median(series) -> float:
    s = series.dropna()
    return float(s.median()) if not s.empty else 0.0


def _empty_result(name: str, kind: str) -> dict:
    return {
        "kind":         kind,
        kind:           name,
        "score":        0.0,
        "grade":        "F",
        "verdict":      "No videos found — cannot assess market.",
        "sample_size":  0,
        "pillars":      {},
        "opportunity":  {},
        "indicators":   {},
        "content_diagnostics": {},
        "highlights":   ["No videos found — cannot grade."],
    }
