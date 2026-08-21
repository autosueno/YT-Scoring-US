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
    SUPPLY_METRICS, SUPPLY_METRIC_WEIGHTS,
    DEMAND_METRIC_GROUPS, DEMAND_GROUP_WEIGHTS,
    DEMAND_TAIL_WEIGHT, DEMAND_TAIL_PERCENTILE, DIRECTIONAL_LOG_SCALE,
    OPPORTUNITY_MOMENTUM_INFLUENCE, OPPORTUNITY_INDICATOR_KEYS,
    VERDICT_HIGH, VERDICT_LOW, GRADE_THRESHOLDS, MOMENTUM_GRADE_THRESHOLDS,
    MOMENTUM_LOG_SCALE, MOMENTUM_COMPONENT_WEIGHTS, MOMENTUM_INCONCLUSIVE_WEIGHT,
    TOPIC_GRADE_WINDOWS, TOPIC_GRADE_MIN_VIDEOS,
)


# ============================================================
# PUBLIC API
# ============================================================

def grade_topic(df, baseline: Baseline, topic: str,
                lookback_days=None) -> dict:
    """Evaluate a topic as a market against the niche baseline."""
    if df.empty:
        return _empty_result(topic, "topic")
    return _grade(df, baseline, topic, "topic", lookback_days)


def grade_topic_multi(topic_df, niche_df, topic: str,
                      windows=None, min_videos=None) -> dict:
    """
    Grade one topic at several time windows, each against the SAME
    window of the niche.

    Answers what a single grade cannot: is this strong recently, or
    strong across the year? Reading the columns left to right:

        90d high, 365d low   -> newly hot, no track record
        90d low,  365d high  -> was strong, cooling
        high at both         -> durable
        low at both          -> not a market

    Both sides are cut to the same window every time. Grading is
    percentile-based, so a 90-day topic scored against a 365-day
    baseline compares two different populations — the topic looks
    artificially fresh and nothing in the result would reveal it.

    Builds a fresh baseline per window rather than reusing the stored
    one, because the stored baseline describes the full collection
    period and would reintroduce exactly that mismatch.
    """
    import insights as _ins
    from baseline import from_frame as _from_frame

    windows = windows or TOPIC_GRADE_WINDOWS
    min_videos = min_videos or TOPIC_GRADE_MIN_VIDEOS
    if topic_df is None or niche_df is None or topic_df.empty or niche_df.empty:
        return {}

    t_days = _ins._days_since(topic_df)
    n_days = _ins._days_since(niche_df)
    out = {"topic": topic, "windows": []}

    for w in sorted(windows):
        t_sub = topic_df[(t_days <= w).values]
        n_sub = niche_df[(n_days <= w).values]
        entry = {"window": w,
                 "topic_videos": int(len(t_sub)),
                 "niche_videos": int(len(n_sub))}
        if len(t_sub) < min_videos or len(n_sub) < min_videos:
            entry["skipped"] = (
                f"needs {min_videos}+ videos on both sides; have "
                f"{len(t_sub)} topic / {len(n_sub)} niche")
            out["windows"].append(entry)
            continue
        try:
            bl = _from_frame(n_sub, f"{topic}@{w}d")
            res = grade_topic(t_sub, bl, topic, lookback_days=w)
            p = res.get("pillars") or {}
            entry.update({
                "skipped": None,
                "opportunity": res.get("score"),
                "grade": res.get("grade"),
                "demand": (p.get("demand") or {}).get("score"),
                "supply": (p.get("supply") or {}).get("score"),
                "momentum": (p.get("momentum") or {}).get("signed_change"),
                "verdict": res.get("verdict"),
            })
        except Exception as e:
            entry["skipped"] = f"{type(e).__name__}: {e}"
        out["windows"].append(entry)

    graded = [w for w in out["windows"] if not w.get("skipped")]
    if len(graded) >= 2:
        first, last = graded[0], graded[-1]
        d_short, d_long = first.get("demand"), last.get("demand")
        if d_short is not None and d_long is not None:
            delta = d_short - d_long
            if delta >= 10:
                out["reading"] = (
                    "newly hot — much stronger over the recent window than "
                    "across the year. You are early, but there is no track "
                    "record yet that the interest lasts.")
            elif delta <= -10:
                out["reading"] = (
                    "cooling — stronger across the year than recently. The "
                    "audience showed up already and is thinning.")
            else:
                out["reading"] = (
                    "steady — similar over the recent window and the full "
                    "year. Whatever this is, it is not a spike.")
            out["demand_delta"] = round(delta, 1)
    return out


def grade_niche(df, baseline: Baseline, niche: str,
                lookback_days=None) -> dict:
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
    result = _grade(df, baseline, niche, "niche", lookback_days)
    result["self_comparison"] = (baseline.niche_name == niche)
    return result


# ============================================================
# CORE ENGINE
# ============================================================

def _grade(df, baseline, name, kind, lookback_days=None):
    demand = _demand_pillar(df, baseline, lookback_days)
    supply = _supply_pillar(df, baseline)
    momentum = _momentum_pillar(df, baseline, lookback_days)

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
        "content_diagnostics": _content_diagnostics(df, lookback_days),
        "highlights":   [],
    }
    if kind == "topic":
        result["niche"] = baseline.niche_name
        # HONEST LABEL, NOT A FIX: grade/verdict above still come from
        # GRADE_THRESHOLDS/VERDICT_HIGH/VERDICT_LOW — fixed absolute
        # cutoffs. Niche grading got a real fix for exactly this
        # problem: crossniche.py computes the letter grade from the
        # ACTUAL distribution of niches being compared, not a fixed
        # range measured on some other run. That fix cannot honestly be
        # copied here yet — it works by pooling many niches graded
        # together in one sweep, and there is no topic-sweep equivalent;
        # topics are graded one at a time, and as of this fix, this
        # project has real grading data for exactly ONE topic ever run.
        # Inventing a "relative" topic threshold from n=1 would be
        # exactly the kind of unvalidated constant this project has
        # spent real effort removing elsewhere — worse than the
        # absolute threshold it would replace, because it would LOOK
        # calibrated without being calibrated. The honest fix right now
        # is this label, not a fake mechanism. Revisit once enough
        # topics have real grades to pool (5 are already queued) — see
        # the flagged item in the project's outstanding-items handoff.
        result["grade_calibration_warning"] = (
            "This grade and verdict use the same fixed thresholds as "
            "niche grading did BEFORE it was fixed to compare against "
            "other real niches — topic grading has no equivalent pool "
            "to compare against yet. Read the underlying demand/supply "
            "pillar scores directly; treat the letter grade and verdict "
            "text with real skepticism until this has enough real data "
            "to calibrate properly."
        )
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

def _demand_scores(df, baseline, lookback_days=None) -> dict:
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
    topic_mom = insights.momentum_summary(df, lookback_days)
    niche_mom = baseline.momentum()
    t_ev, n_ev = topic_mom.get("evergreen_score"), niche_mom.get("evergreen_score")
    ev = _directional_score(t_ev, n_ev, "higher")
    if ev is not None:
        scores["evergreen"] = ev

    return {"metrics": scores, "groups": group_scores, "evergreen": ev}


def _demand_pillar(df, baseline, lookback_days=None) -> dict:
    """Combine demand group scores by weight into the pillar score."""
    parts = _demand_scores(df, baseline, lookback_days)
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

    # channel_video_count — incumbent catalogue depth. The second-
    # strongest discriminator between niches (CV 57%) and a direct read
    # on "how established is the competition here", but it lives in the
    # per-video frame rather than the supply/competition summaries, so it
    # is scored here rather than in the SUPPLY_METRICS loop. Higher =
    # deeper catalogues = harder to break in = more supply pressure.
    if "channel_video_count" in df.columns:
        t_cvc = float(df.groupby("channel_id")["channel_video_count"]
                      .first().median())
        n_cvc = (niche_comp.get("median_channel_video_count")
                 or niche_supply.get("median_channel_video_count"))
        # No stored niche reference (older baselines): compare against the
        # pooled reference the cross-niche path supplies via the baseline
        # frame's own median, which _directional_score handles as parity
        # when equal. Fall back to skipping rather than inventing a number.
        if n_cvc:
            s = _directional_score(t_cvc, float(n_cvc), "higher")
            if s is not None:
                scores["channel_video_count"] = s

    return scores


def _supply_pillar(df, baseline) -> dict:
    """
    Supply pillar with metric weighting.

    _pillar() averages its inputs flat, which is right for demand groups
    but wrong for supply: two of supply's metrics (uploads_per_creator,
    content_freshness) are nearly constant across niches and, averaged
    equally, dilute the two that actually vary (incumbent size and
    catalogue depth). This applies SUPPLY_METRIC_WEIGHTS so the pillar
    reflects the discriminating signal instead of drowning it.
    """
    scores = _supply_scores(df, baseline)
    usable = {k: v for k, v in scores.items() if v is not None}
    if not usable:
        return {"score": 50.0, "grade": _letter_grade(50.0), "metrics": {}}

    # Re-normalise weights over whichever metrics are present, so a
    # missing metric redistributes its weight rather than silently
    # dragging the score toward an implicit zero.
    wsum = sum(SUPPLY_METRIC_WEIGHTS.get(k, 0.0) for k in usable)
    if wsum > 0:
        score = sum(v * SUPPLY_METRIC_WEIGHTS.get(k, 0.0)
                    for k, v in usable.items()) / wsum
    else:
        score = sum(usable.values()) / len(usable)
    return {
        "score":   round(score, 1),
        "grade":   _letter_grade(score),
        "metrics": {k: round(v, 1) for k, v in usable.items()},
    }


# ============================================================
# PILLAR: MOMENTUM (demand growth vs supply growth)
# ============================================================

def _momentum_pillar(df, baseline, lookback_days=None) -> dict:
    """
    Momentum pillar with an honesty guard: if the recent/prior windows don't
    hold enough videos (common when a sample is smeared across years), the
    growth ratios are computed from near-empty buckets and mean nothing — so
    the pillar ABSTAINS instead of emitting a fabricated score. An abstained
    pillar is excluded from the opportunity derivation entirely.
    """
    mom = insights.momentum_summary(df, lookback_days)
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
    signed_scores = _momentum_scores(df, baseline, lookback_days)
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
        "demand_growth_conclusive": mom.get("demand_growth_conclusive"),
        "demand_growth_confidence": mom.get("demand_growth_confidence"),
        "demand_growth_ci": mom.get("demand_growth_ci"),
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


def _momentum_scores(df, baseline, lookback_days=None) -> dict:
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
    mom = insights.momentum_summary(df, lookback_days)
    scores = {}

    dg, sg = mom.get("demand_growth"), mom.get("supply_growth")
    confidence = mom.get("demand_growth_confidence")
    if dg is not None and sg is not None:
        s = _signed_log(dg, sg)
        if s is not None:
            # Continuous confidence weighting (0..1) instead of a binary
            # conclusive/inconclusive cutoff — see
            # insights.momentum_summary's demand_growth_confidence for the
            # derivation. A hard cutoff treated a [1.09, 2.87] interval
            # and a [0.99, 2.24] interval as opposites despite nearly
            # identical evidence; this scales smoothly instead, with the
            # floor MOMENTUM_INCONCLUSIVE_WEIGHT still applied so a
            # dead-centre interval (confidence 0) contributes a small,
            # non-zero amount rather than being silenced outright.
            if confidence is not None:
                w = MOMENTUM_INCONCLUSIVE_WEIGHT + (
                    (1.0 - MOMENTUM_INCONCLUSIVE_WEIGHT) * confidence)
                s *= w
            scores["demand_vs_supply_growth"] = s
    elif dg is not None:
        s = _signed_log(dg, 1.0)
        if s is not None:
            if confidence is not None:
                w = MOMENTUM_INCONCLUSIVE_WEIGHT + (
                    (1.0 - MOMENTUM_INCONCLUSIVE_WEIGHT) * confidence)
                s *= w
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

    # evergreen_persistence is deliberately NOT scored into momentum —
    # see MOMENTUM_COMPONENT_WEIGHTS. It measures durability, not
    # direction, and penalised exactly the topics momentum should
    # reward. It is surfaced as a diagnostic instead; see
    # evergreen_diagnostic().

    return scores


def lifecycle_diagnostic(df, mom=None):
    """
    Why old videos underperform — decay, or a topic that arrived late?

    The evergreen ratio alone cannot answer this, and the two readings
    have OPPOSITE implications for whether to make the video:

        content ages fast   -> yours will fade too. Plan for a spike.
        topic arrived late  -> you are early to a wave. Plan a series.

    The discriminator is TOTAL views, not views per hour. Views per hour
    divides by age, so any old video looks slow. Total views does not
    lie about it:

        old videos with HIGH totals -> they earned, then faded. Decay.
        old videos with LOW totals  -> they never earned at all. The
                                       audience was not there yet.

    Measured on a real memory-stocks topic: videos 180-270 days old had
    a median of 675 total views against 18,568 for videos under 90 days
    — a 27x gap in TOTAL, not rate. Those videos did not decay. Nobody
    ever watched them, because nobody cared about memory stocks then.
    """
    if df is None or df.empty or "view_count" not in df.columns:
        return None
    days = insights._days_since(df)
    old_mask = days > 180
    new_mask = days <= 90
    if old_mask.sum() < 8 or new_mask.sum() < 8:
        return {"verdict": "unknown",
                "reading": ("not enough spread in video ages to tell — this "
                            "needs a 365-day collection window")}

    old_total = float(df.loc[old_mask, "view_count"].median())
    new_total = float(df.loc[new_mask, "view_count"].median())
    ratio = (old_total / new_total) if new_total else None
    ev = (mom or {}).get("evergreen_score")

    if ratio is None:
        return None
    if ratio >= 1.2:
        verdict = "evergreen"
        reading = ("old videos have MORE total views than new ones — content "
                   "here keeps earning. A video made now should still be "
                   "working in a year, so a back catalogue compounds.")
    elif ratio >= 0.5:
        verdict = "decaying"
        reading = ("old videos earned well and then faded — content has a "
                   "shelf life of months, not years. Expect a spike then a "
                   "drop, and plan to keep publishing rather than relying on "
                   "a back catalogue.")
    else:
        verdict = "emerging"
        reading = ("old videos never earned much even when they were new — "
                   "this audience arrived recently rather than the content "
                   "ageing badly. You are early to a wave, which is the good "
                   "version of this signal, but it also means there is no "
                   "proof yet that the interest lasts.")
    return {
        "verdict": verdict,
        "old_median_views": int(old_total),
        "new_median_views": int(new_total),
        "old_vs_new_total": round(ratio, 3),
        "evergreen_ratio": round(float(ev), 4) if ev is not None else None,
        "reading": reading,
    }


def evergreen_diagnostic(mom):
    """
    Content durability, reported rather than scored.

    Old videos' views/hour against new videos'. 1.0 means age makes no
    difference; below 1.0 means content has a shelf life. This is real
    and useful — for a library-building channel it may matter more than
    momentum does — but it answers "will this still earn in a year",
    not "is this heating up", so folding it into a directional score
    made both harder to read.
    """
    ev = mom.get("evergreen_score")
    if ev is None:
        return None
    signed = _signed_log(ev, 1.0)
    if ev >= 0.8:
        reading = "durable — old videos hold up about as well as new ones"
    elif ev >= 0.4:
        reading = "moderate decay — older content earns roughly half"
    elif ev >= 0.1:
        reading = "short shelf life — content fades within months"
    else:
        reading = ("very front-loaded — either content dates fast, or the "
                   "topic's audience arrived recently")
    return {"ratio": round(float(ev), 4), "signed": signed, "reading": reading}


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


def _content_diagnostics(df, lookback_days=None) -> dict:
    """
    Content signals are reported as DIAGNOSTICS, not scored — per the market
    model, content packaging is not one of the four market pillars. Kept
    because it's useful context for creators.
    """
    prof = insights.title_pattern_summary(df)
    keep = ["pct_has_tutorial", "pct_has_comparison", "pct_has_prediction",
            "pct_has_question", "avg_title_sentiment"]
    out = {k: round(prof[k], 1) for k in keep if prof.get(k) is not None}

    # Content durability lands here rather than in momentum. For a
    # library-building channel this may matter more than momentum does,
    # but it answers a different question and belongs beside the other
    # unscored context.
    mom = insights.momentum_summary(df, lookback_days)
    life = lifecycle_diagnostic(df, mom)
    if life:
        out["lifecycle"] = life.get("verdict")
        if life.get("old_vs_new_total") is not None:
            out["old_vs_new_total_views"] = life["old_vs_new_total"]
        out["lifecycle_reading"] = life["reading"]
    ev = evergreen_diagnostic(mom)
    if ev:
        out["evergreen_ratio"] = ev["ratio"]
    return out


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
    # Momentum is reported SIGNED here, like everywhere else. Printing its
    # 0-100 form in a list beside demand and supply invites exactly the
    # comparison the signed axis was introduced to prevent — and it
    # contradicted the "Momentum: ... (+30 vs. breakeven)" line a few
    # bullets above, same value shown two ways in one report.
    mom_txt = "not scored" if (mom_p.get("abstained")
                               or mom_p.get("signed_change") is None) \
        else f"{mom_p['signed_change']:+.0f} signed ({mom_p['grade']})"
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
