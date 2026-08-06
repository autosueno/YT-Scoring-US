"""
timing.py

When to publish: best hour of day and day of week, measured against the
niche itself.

WHY THIS IS THE CLEANEST SIGNAL IN THE ENGINE
---------------------------------------------
Every other diagnostic here suffers from the same confound: effort. A
creator who writes a long description also picks a better thumbnail, edits
harder and researches more — so "long descriptions correlate with success"
mostly measures effort, not descriptions.

Publish time is different. The hour a video goes live is close to
arbitrary with respect to how good the video is. Nobody makes a worse
video because they published at 3am. That near-independence is what makes
timing a genuinely causal-ish question rather than a correlational one.

THE ONE REAL CONFOUND, AND HOW WE REMOVE IT
-------------------------------------------
Big channels tend to publish at conventional times. If we ranked hours by
raw views we would mostly rediscover when large channels post, not when
audiences are receptive.

So the primary metric is OUTLIER_RATIO — a video's views divided by its
own channel's average. That asks "did this video beat its own channel's
baseline?", which cancels channel size, niche popularity and audience loyalty
in one step. A 2pm slot that consistently beats channels' own averages is
real evidence; a 2pm slot with high raw views may just be where MrBeast posts.

Raw views_per_hour is reported alongside it, because agreement between the
two is itself informative — and disagreement is a warning.

TIMEZONE
--------
published_at is UTC. Publishing advice is meaningless in UTC, so everything
is converted to the AUDIENCE timezone (config.AUDIENCE_TIMEZONE, default US
Eastern). Change it if you target a different market.

Deterministic; nothing here feeds demand/supply/momentum scoring.
"""

from datetime import timezone as _tz

import pandas as pd

import numpy as np

from config import (
    AUDIENCE_TIMEZONE, TIMING_MIN_SAMPLES, TIMING_HOUR_BLOCK,
    CREATOR_SIZE_TIERS, TIMING_SHRINKAGE_K, TIMING_BOOTSTRAP_N,
    TIMING_CI_PERCENTILES, TIMING_RECENCY_WEIGHTING, TIMING_HALFLIFE_DAYS,
)

WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]


# ============================================================
# PREP
# ============================================================

def add_local_time(df, tz_name=None):
    """
    Add audience-local publish hour / weekday columns.

    Falls back to UTC if the timezone database is unavailable, and says so
    rather than silently producing misleading advice.
    """
    if df.empty or "published_at" not in df.columns:
        # Without a publish timestamp there is nothing to derive from, but
        # callers group on these columns unconditionally. Returning the
        # frame without them turns a missing-data situation into a KeyError
        # deep inside an export. Create empty columns so every consumer
        # sees "no timing signal" (rows filtered out by min-sample checks)
        # instead of crashing. A dataset built by the collector always has
        # published_at; this path only fires on malformed or partial input.
        if not df.empty:
            if "local_hour_block" not in df.columns:
                df = df.copy()
                df["local_hour_block"] = pd.NA
                df["local_hour"] = pd.NA
                df["local_weekday"] = pd.NA
                df["_tz_used"] = "UTC"
        return df
    tz_name = tz_name or AUDIENCE_TIMEZONE
    published = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    try:
        local = published.dt.tz_convert(tz_name)
        df["_tz_used"] = tz_name
    except Exception:
        local = published.dt.tz_convert(_tz.utc)
        df["_tz_used"] = "UTC"
    df["local_hour"] = local.dt.hour
    df["local_weekday"] = local.dt.day_name()
    df["local_hour_block"] = (df["local_hour"] // TIMING_HOUR_BLOCK) * TIMING_HOUR_BLOCK
    return df


def _metric_cols(df):
    """Primary (channel-controlled) and secondary (raw) metrics."""
    primary = "outlier_ratio" if "outlier_ratio" in df.columns else None
    secondary = "views_per_hour" if "views_per_hour" in df.columns else None
    return primary, secondary


# ============================================================
# AGGREGATION
# ============================================================

def _shrink(observed_index, n, k=None):
    """
    Pull a bucket's index toward the niche median (100) in proportion to
    how little evidence supports it.

        shrunk = (n * observed + k * 100) / (n + k)

    A hard minimum-sample cutoff says "trust fully or ignore entirely",
    which is a bad model of evidence: a 21-video bucket is not equal to a
    700-video one. Shrinkage makes extremity something a bucket has to
    earn, and it degrades smoothly instead of snapping at a threshold.
    """
    if observed_index is None:
        return None
    k = TIMING_SHRINKAGE_K if k is None else k
    return round((n * observed_index + k * 100.0) / (n + k), 1)


def _bootstrap_ci(values, base, n_boot=None, pct=None):
    """
    Percentile-bootstrap interval for a bucket's INDEX.

    Reporting "136" implies a precision we do not have. Resampling the
    bucket with replacement and re-taking the median gives an honest range;
    a wide interval is itself the finding.
    """
    vals = np.asarray([v for v in values if v is not None and not pd.isna(v)],
                      dtype=float)
    if len(vals) < 3 or not base:
        return None, None
    n_boot = n_boot or TIMING_BOOTSTRAP_N
    pct = pct or TIMING_CI_PERCENTILES
    rng = np.random.default_rng(12345)   # fixed seed keeps output deterministic
    idx = rng.integers(0, len(vals), size=(n_boot, len(vals)))
    meds = np.median(vals[idx], axis=1)
    lo, hi = np.percentile(meds, pct)
    return round(100 * lo / base, 1), round(100 * hi / base, 1)


def _weights(sub):
    """Optional exponential recency weights (off by default)."""
    if not TIMING_RECENCY_WEIGHTING or "hours_since_upload" not in sub.columns:
        return None
    age_days = pd.to_numeric(sub["hours_since_upload"], errors="coerce") / 24.0
    return np.power(0.5, (age_days / TIMING_HALFLIFE_DAYS).fillna(0).values)


def _weighted_median(vals, w):
    if w is None:
        return float(np.median(vals)) if len(vals) else None
    order = np.argsort(vals)
    v, ww = np.asarray(vals)[order], np.asarray(w)[order]
    c = np.cumsum(ww)
    if c[-1] <= 0:
        return float(np.median(v))
    return float(v[np.searchsorted(c, c[-1] / 2.0)])


def _summarise(df, group_col, order=None):
    """
    Per-bucket performance with SHRUNK index, bootstrap CI, sample count
    and upload density.

    Four numbers instead of one, because one number hid too much:
      raw_index   - what the bucket actually did
      index       - the same, shrunk toward 100 by sample size (use this)
      ci_low/high - honest uncertainty
      uploads_share - how crowded the slot is (context, NOT a divisor)
    """
    primary, secondary = _metric_cols(df)
    if not primary and not secondary:
        return []

    rows = []
    base_p = df[primary].median() if primary else None
    base_s = df[secondary].median() if secondary else None
    total = max(1, len(df))
    n_buckets = max(1, df[group_col].nunique())

    for key, sub in df.groupby(group_col):
        if pd.isna(key):
            continue
        n = int(len(sub))
        entry = {
            "bucket": key,
            "videos": n,
            "reliable": n >= TIMING_MIN_SAMPLES,
            # Share of all uploads landing in this bucket, indexed so
            # 100 = an even spread. High = crowded slot.
            "upload_density": round(100.0 * (n / total) * n_buckets, 1),
        }
        if primary:
            w = _weights(sub)
            vals = pd.to_numeric(sub[primary], errors="coerce").dropna().values
            m = _weighted_median(vals, w[:len(vals)] if w is not None else None)
            raw = (round(100 * m / float(base_p), 1)
                   if m is not None and base_p else None)
            entry["raw_outlier_index"] = raw
            entry["outlier_index"] = _shrink(raw, n)
            lo, hi = _bootstrap_ci(vals, float(base_p) if base_p else None)
            entry["ci_low"], entry["ci_high"] = lo, hi
        if secondary:
            m = sub[secondary].median()
            raw_s = (round(100 * float(m) / float(base_s), 1)
                     if pd.notna(m) and base_s else None)
            entry["views_index"] = _shrink(raw_s, n)
        rows.append(entry)

    if order:
        rows.sort(key=lambda r: order.index(r["bucket"]) if r["bucket"] in order else 99)
    else:
        rows.sort(key=lambda r: r["bucket"])
    return rows


def by_hour(df):
    """Performance index per audience-local hour block."""
    return _summarise(df, "local_hour_block")


def by_weekday(df):
    """Performance index per audience-local weekday."""
    return _summarise(df, "local_weekday", order=WEEKDAY_ORDER)


def heatmap(df):
    """
    weekday x hour-block grid. 7 x (24/block) cells, so with a 3-hour block
    that is 56 cells — thin for a 3,000-video niche (~54 each), which is why
    every cell carries its own count and reliability flag.
    """
    primary, _ = _metric_cols(df)
    if not primary or df.empty:
        return []
    base = df[primary].median()
    if not base or pd.isna(base):
        return []
    out = []
    for (day, block), sub in df.groupby(["local_weekday", "local_hour_block"]):
        m = sub[primary].median()
        out.append({
            "weekday": day,
            "hour_block": int(block),
            "videos": int(len(sub)),
            "reliable": len(sub) >= TIMING_MIN_SAMPLES,
            "outlier_index": (round(100 * float(m) / float(base), 1)
                              if pd.notna(m) else None),
        })
    return out


def best_joint_slots(df, top_n=5, min_n=None):
    """
    The direct answer to "is it Sunday 3am or Thursday 5pm?"

    Best weekday and best hour, found SEPARATELY, do not have to agree on
    one actual slot — Thursday's best hour might be 9am, and 5pm's best
    day might be Tuesday, with neither implying anything about
    "Thursday 5pm" specifically. Only ranking the JOINT (weekday, hour
    block) cells answers the question that was actually asked.

    Ranked by the SHRUNK index (see _shrink) so a lucky 2-video slot can't
    outrank a well-evidenced one. min_n is a hard floor beneath which a
    slot is not even offered as a candidate, because no amount of shrinkage
    makes a 2-video reading worth acting on.

    Self-sufficient: adds local_weekday/local_hour_block columns itself if
    they are not already present, so this can be called standalone (as the
    calendar headline does) without the caller having to remember to run
    add_local_time() first — a missing call here previously raised a bare
    KeyError with no hint about what was actually wrong.
    """
    if df.empty:
        return []
    if "local_hour_block" not in df.columns or "local_weekday" not in df.columns:
        df = add_local_time(df.copy())
    primary, _ = _metric_cols(df)
    if not primary:
        return []
    min_n = min_n or max(8, TIMING_MIN_SAMPLES // 3)
    base = df[primary].median()
    if not base or pd.isna(base):
        return []

    rows = []
    for (day, block), sub in df.groupby(["local_weekday", "local_hour_block"]):
        n = int(len(sub))
        if n < min_n:
            continue
        m = sub[primary].median()
        if pd.isna(m):
            continue
        raw = round(100 * float(m) / float(base), 1)
        lo, hi = _bootstrap_ci(
            pd.to_numeric(sub[primary], errors="coerce").dropna().values,
            float(base))
        rows.append({
            "weekday": day, "hour_block": int(block), "videos": n,
            "raw_index": raw, "index": _shrink(raw, n),
            "ci_low": lo, "ci_high": hi,
        })
    rows.sort(key=lambda r: r["index"], reverse=True)
    return rows[:top_n]


def by_creator_size(df):
    """
    Timing split by creator size — the question a small channel actually
    has: "when should a channel LIKE MINE publish?"

    The niche-wide answer is dominated by whoever publishes most, which is
    usually established channels. Their optimal slot is driven by
    subscriber notifications: they publish when their audience is awake,
    early velocity spikes, and the algorithm amplifies. A channel with
    almost no subscribers has no notification wave to catch, so the same
    slot need not be optimal — its traffic arrives from search and
    suggested over days, not from a launch-hour surge.

    Splitting the analysis is the only way to see whether the two groups
    genuinely differ in this niche, rather than assuming they do.
    """
    if "channel_subs" not in df.columns or df.empty:
        return {}
    subs = pd.to_numeric(df["channel_subs"], errors="coerce")
    out = {}
    for lo, hi, label in CREATOR_SIZE_TIERS:
        mask = (subs >= lo) & (subs < hi) if hi is not None else (subs >= lo)
        sub = df[mask.fillna(False)]
        if sub.empty:
            continue
        out[label] = {
            "videos": int(len(sub)),
            "subs_range": f"<{hi:,}" if lo == 0 and hi
                          else (f"{lo:,}+" if hi is None else f"{lo:,}-{hi:,}"),
            "by_hour": _summarise(sub, "local_hour_block"),
            "by_weekday": _summarise(sub, "local_weekday", order=WEEKDAY_ORDER),
            # JOINT best slot for this tier specifically. Marginal bests
            # (best hour, best day, found separately) can point at a real
            # cell only by coincidence — a tier's "best hour: 01:00" from a
            # marginal read can come from one implausible outlier cell, not
            # a genuinely strong slot. min_n scales down for thinner tiers.
            "best_joint": best_joint_slots(sub, top_n=3,
                                           min_n=max(6, min(15, len(sub) // 40))),
        }
    return out


def by_reach(df, cuts=(20_000, 200_000)):
    """
    Segment by CHANNEL AVERAGE VIEWS rather than subscriber count.

    Subscriber count is the causally relevant variable for timing — the
    notification wave is what makes publish hour matter — but it decays as
    a proxy for real reach: 2M dormant subscribers do not produce a wave.
    Average views measure live audience instead.

    Deliberately NOT segmented by outlier_ratio or median views-per-hour:
    those are what we MEASURE with, so segmenting on them would condition
    on the outcome and manufacture the pattern we then "discover".
    channel_avg_views is a channel-level property, independent of how any
    individual video performed relative to its channel.
    """
    if "channel_avg_views" not in df.columns or df.empty:
        return {}
    av = pd.to_numeric(df["channel_avg_views"], errors="coerce")
    lo, hi = cuts
    bands = [("low_reach", av < lo, f"<{lo:,} avg views"),
             ("mid_reach", (av >= lo) & (av < hi), f"{lo:,}-{hi:,}"),
             ("high_reach", av >= hi, f"{hi:,}+")]
    out = {}
    for label, mask, pretty in bands:
        sub = df[mask.fillna(False)]
        if len(sub) < TIMING_MIN_SAMPLES:
            continue
        out[label] = {
            "videos": int(len(sub)),
            "range": pretty,
            "by_hour": _summarise(sub, "local_hour_block"),
            "by_weekday": _summarise(sub, "local_weekday", order=WEEKDAY_ORDER),
        }
    return out


def _best(rows):
    ok = [r for r in rows if r.get("reliable") and r.get("outlier_index")]
    return max(ok, key=lambda r: r["outlier_index"], default=None)


def event_driven_score(df):
    """
    Is this niche's success EVENT-DRIVEN or DURABLE?

    Scheduled economic releases (CPI, PPI, FOMC, jobs) create predictable
    demand spikes for finance content, and reaction videos published the
    same day can perform extremely well. That is a real strategy — but it
    is a different strategy from evergreen content, and the engine should
    not quietly blend them.

    The tell is DATE CONCENTRATION among top performers. If the best
    videos cluster onto a handful of calendar days, performance is being
    driven by events; if they are spread evenly, demand is durable.

    Returns the share of top-decile videos falling on the busiest 5% of
    publication days, versus what an even spread would predict.
    """
    if df.empty or "published_at" not in df.columns:
        return {}
    metric = "outlier_ratio" if "outlier_ratio" in df.columns else "views_per_hour"
    if metric not in df.columns:
        return {}
    d = df.copy()
    d["_day"] = pd.to_datetime(d["published_at"], utc=True, errors="coerce").dt.date
    d = d[d["_day"].notna()]
    if d.empty:
        return {}
    cut = d[metric].quantile(0.90)
    top = d[d[metric] >= cut]
    if len(top) < 10:
        return {}
    counts = top["_day"].value_counts()

    # --- Daily series of top-video counts over the full date range ---
    daily = counts.sort_index()
    full_range = pd.date_range(min(d["_day"]), max(d["_day"]), freq="D").date
    series = pd.Series([daily.get(dt, 0) for dt in full_range],
                       index=full_range, dtype=float)

    # --- Spike dates via rolling z-score (for LISTING, not for rhythm) ---
    roll_mean = series.rolling(28, min_periods=7, center=True).mean()
    roll_std = series.rolling(28, min_periods=7, center=True).std().replace(0, np.nan)
    z = (series - roll_mean) / roll_std
    spike_dates = [dt for dt, zz in z.items() if pd.notna(zz) and zz >= 2.0]

    # --- Rhythm via AUTOCORRELATION, not spike gaps ---
    # Consecutive-spike gaps are fragile: one missed or phantom spike
    # halves/doubles the estimate (a 45-day rhythm read as 22 in testing),
    # and for short periods the 28-day rolling mean tracks the signal
    # itself, suppressing alternate spikes (7 read as 14). The
    # autocorrelation of the whole daily series uses every day's
    # information at once: a true period P shows as an ACF peak at lag P.
    period, period_label = None, None
    x = series.values - series.values.mean()
    denom = float((x * x).sum())
    if denom > 0 and len(x) >= 60:
        max_lag = min(100, len(x) // 3)
        acf = np.array([float((x[:-lag] * x[lag:]).sum()) / denom
                        for lag in range(4, max_lag + 1)])
        lags = np.arange(4, max_lag + 1)
        # Take the FIRST strong local peak, not the global max. A true
        # period P also produces ACF peaks at 2P, 3P, ... (harmonics), and
        # the global max can land on a multiple — a 7-day rhythm was read
        # as 35 in testing. Requiring a local peak within 70% of the
        # global maximum and taking the smallest such lag recovers the
        # fundamental, the same trick pitch detectors use.
        peak_is = [i for i in range(1, len(acf) - 1)
                   if acf[i] >= acf[i - 1] and acf[i] >= acf[i + 1]]
        strong = [i for i in peak_is if acf[i] >= max(0.12, 0.70 * acf.max())]
        best_i = min(strong) if strong else int(np.argmax(acf))
        if acf[best_i] >= 0.12:
            period = float(lags[best_i])
            if 25 <= period <= 35:
                period_label = ("~monthly — consistent with CPI / PPI / "
                                "jobs reports")
            elif 40 <= period <= 52:
                period_label = ("~6-7 weeks — consistent with the FOMC "
                                "meeting schedule (8 per year)")
            elif 85 <= period <= 95:
                period_label = "~quarterly — consistent with earnings season"
            elif 5 <= period <= 9:
                period_label = "~weekly — consistent with a recurring series"
            else:
                period_label = f"recurring every ~{period:.0f} days"
    n_days = d["_day"].nunique()
    hot_n = max(1, int(round(n_days * 0.05)))
    hot_share = float(counts.head(hot_n).sum()) / len(top)

    # NULL CALIBRATION. Comparing hot_share against a uniform expectation
    # (hot_n / n_days) is statistically wrong: even purely random dates
    # cluster (Poisson), so the busiest 5% of days ALWAYS hold several
    # times their "even share" — measured at ~3.9x on genuinely uniform
    # test data, which the old threshold then misread as event-driven.
    # The honest null is: scatter the same number of videos uniformly over
    # the same days, many times, and ask how concentrated THAT looks.
    rng = np.random.default_rng(777)          # fixed seed: deterministic
    sims = []
    for _ in range(200):
        assign = rng.integers(0, n_days, size=len(top))
        c = np.bincount(assign, minlength=n_days)
        c.sort()
        sims.append(c[-hot_n:].sum() / len(top))
    null_share = float(np.mean(sims))
    ratio = hot_share / null_share if null_share else None
    return {
        "top_videos": int(len(top)),
        "distinct_days": int(n_days),
        "hot_days_counted": hot_n,
        "share_on_hot_days": round(hot_share, 3),
        "random_null_share": round(null_share, 3),
        "concentration_ratio": round(ratio, 2) if ratio else None,
        # Thresholds are vs the SIMULATED null (1.0 = pure chance), so they
        # can be far tighter than the old uniform-based ones.
        "reading": ("event-driven — winners cluster on specific dates"
                    if ratio and ratio >= 1.6
                    else "mixed" if ratio and ratio >= 1.25
                    else "durable — winners spread across the calendar"),
        "busiest_days": [{"date": str(k), "top_videos": int(v)}
                         for k, v in counts.head(5).items()],
        "spike_dates": [str(s) for s in spike_dates[:12]],
        "spike_count": len(spike_dates),
        "median_gap_days": round(period, 1) if period else None,
        "periodicity": period_label,
    }


# ============================================================
# REPORT
# ============================================================

def _fmt_block(h):
    end = (h + TIMING_HOUR_BLOCK) % 24
    return f"{h:02d}:00-{end:02d}:00"


def build_timing_report(df) -> dict:
    df = add_local_time(df.copy())
    hours, days = by_hour(df), by_weekday(df)
    reliable_hours = [h for h in hours if h["reliable"] and h.get("outlier_index")]
    reliable_days = [d for d in days if d["reliable"] and d.get("outlier_index")]
    return {
        "timezone": df["_tz_used"].iloc[0] if "_tz_used" in df.columns and len(df) else "UTC",
        "videos_analyzed": int(len(df)),
        "hour_block_size": TIMING_HOUR_BLOCK,
        "min_samples_for_reliable": TIMING_MIN_SAMPLES,
        "channel_controlled": "outlier_ratio" in df.columns,
        "by_hour": hours,
        "by_weekday": days,
        "heatmap": heatmap(df),
        "best_hour": max(reliable_hours, key=lambda r: r["outlier_index"], default=None),
        "worst_hour": min(reliable_hours, key=lambda r: r["outlier_index"], default=None),
        "best_weekday": max(reliable_days, key=lambda r: r["outlier_index"], default=None),
        "worst_weekday": min(reliable_days, key=lambda r: r["outlier_index"], default=None),
        "by_creator_size": by_creator_size(df),
        "by_reach": by_reach(df),
        "event_driven": event_driven_score(df),
    }


def render_timing_markdown(rep) -> list:
    """Markdown lines for embedding in the SEO report."""
    L = ["\n## When to publish\n"]
    if not rep.get("by_hour"):
        return L + ["_Not enough timing data._\n"]

    L.append(f"Times are **{rep['timezone']}** (audience timezone, not UTC). "
             f"{rep['videos_analyzed']:,} videos. Index 100 = niche median.\n")

    if rep.get("channel_controlled"):
        L.append("> Ranked by **outlier ratio** — each video against its OWN "
                 "channel's average. This cancels channel size, so a good "
                 "slot means 'videos beat their own baseline here', not "
                 "'big channels post here'.\n")
    else:
        L.append("> ⚠️ Channel data missing, so ranking uses raw views and "
                 "may just reflect when large channels publish.\n")

    joint = rep.get("best_joint_slots") or []
    if joint:
        top = joint[0]
        ci = (f", 90% CI {top['ci_low']:.0f}\u2013{top['ci_high']:.0f}"
              if top.get("ci_low") is not None else "")
        L.append("### The direct answer\n")
        L.append(f"**{top['weekday']} {_fmt_block(top['hour_block'])}** "
                 f"\u2014 index {top['index']} ({top['videos']} videos{ci}).\n")
        if len(joint) > 1:
            L.append("Other strong slots:\n")
            for s in joint[1:]:
                ci2 = (f" (CI {s['ci_low']:.0f}\u2013{s['ci_high']:.0f})"
                      if s.get("ci_low") is not None else "")
                L.append(f"- {s['weekday']} {_fmt_block(s['hour_block'])} "
                         f"\u2014 index {s['index']}, {s['videos']} videos{ci2}")
        L.append("\n> This is the WEEKDAY+HOUR combination, ranked "
                 "together \u2014 not \"best day\" and \"best hour\" found "
                 "separately and assumed to overlap. Those can point at a "
                 "slot that never actually occurs together in the data.\n")
    else:
        L.append("_Not enough joint weekday\u00d7hour data for a single "
                 "recommendation \u2014 see the marginal tables below "
                 "instead._\n")

    def _row(label, r):
        flag = "" if r["reliable"] else " ⚠️"
        ci = ("—" if r.get("ci_low") is None
              else f"{r['ci_low']:.0f}–{r['ci_high']:.0f}")
        dens = r.get("upload_density")
        return (f"| {label} | {r['videos']}{flag} | "
                f"{r.get('outlier_index', '—')} | {ci} | "
                f"{r.get('raw_outlier_index', '—')} | "
                f"{dens if dens is not None else '—'} |")

    hdr = ("| bucket | videos | index (shrunk) | 90% CI | raw | upload density |\n"
           "|---|---|---|---|---|---|")
    L.append("\n### By hour block\n")
    L.append(hdr)
    for h in rep["by_hour"]:
        L.append(_row(_fmt_block(h["bucket"]), h))

    L.append("\n### By weekday\n")
    L.append(hdr)
    for d in rep["by_weekday"]:
        L.append(_row(d["bucket"], d))

    L.append("\n**Reading the columns.** *index (shrunk)* is the number to "
             "trust — each bucket pulled toward 100 in proportion to how "
             "thin its sample is, so a lucky 20-video slot cannot outrank a "
             "solid 700-video one. *raw* is what it did before shrinking; a "
             "big gap between the two means the evidence is thin. *90% CI* "
             "is the bootstrap range — if it straddles 100, the bucket is "
             "not distinguishable from average. *upload density* is 100 for "
             "an evenly-used slot; higher means crowded.\n")
    L.append("> Density is shown as CONTEXT, deliberately NOT divided into "
             "performance. Uploads cluster where the audience is, so "
             "`performance / density` would crown 3am — empty because "
             "nobody is watching. Since the index already uses outlier "
             "ratio, a slot scoring well DESPITE heavy competition has "
             "already netted competition out.\n")

    L.append(f"\n⚠️ = fewer than {rep['min_samples_for_reliable']} videos; "
             f"treat as noise.\n")

    # --- Split by creator size: the question a small channel actually has ---
    cs = rep.get("by_creator_size") or {}
    if cs:
        L.append("\n### Does creator size change the answer?\n")
        L.append("The niche-wide figures above are dominated by whoever "
                 "publishes most. Established channels optimise for the "
                 "subscriber-notification wave; a channel with few subs has "
                 "no such wave and is found through search and suggested "
                 "over days. So the two groups can differ.\n")
        L.append("| group | subs | videos | best joint slot (weekday + hour) |")
        L.append("|---|---|---|---|")
        for label, key in (("Brand-new creators", "new_creators"),
                           ("Small creators", "small_creators"),
                           ("Large creators", "large_creators")):
            seg = cs.get(key)
            if not seg:
                continue
            bj = (seg.get("best_joint") or [None])[0]
            slot_txt = (f"{bj['weekday']} {_fmt_block(bj['hour_block'])} "
                       f"({bj['index']}, n={bj['videos']})") if bj else "not enough data"
            warn = "" if seg["videos"] >= TIMING_MIN_SAMPLES * 4 else " ⚠️"
            L.append(f"| {label} | {seg.get('subs_range','')} | "
                     f"{seg['videos']}{warn} | {slot_txt} |")
        new = cs.get("new_creators")
        if new:
            bj = (new.get("best_joint") or [None])[0]
            if bj:
                L.append(f"\n**For a brand-new channel (<100 subs), the data "
                         f"points to {bj['weekday']} {_fmt_block(bj['hour_block'])}** "
                         f"(index {bj['index']}, {bj['videos']} videos).")
            L.append("\n> ⚠️ **Survivorship warning on the <100 tier.** A "
                     "channel with under 100 subscribers whose video shows "
                     "up in YouTube search at all is already an extreme "
                     "outlier. This segment is not 'typical new channels' — "
                     "it is 'new channels that broke through'. Treat it as "
                     "the closest available proxy, not a representative "
                     "sample.")
        L.append("\n> Honest note: with few subscribers, publish time is one "
                 "of the WEAKEST levers you have. The mechanism that makes "
                 "timing matter — notify subscribers, spike early velocity, "
                 "trigger algorithmic amplification — barely applies without "
                 "an audience to notify. Topic, title and thumbnail matter "
                 "far more. Use this to break ties, not to plan a strategy.\n")

    # --- Reach-based lens (subs decay as a proxy for live audience) ---
    rr = rep.get("by_reach") or {}
    if rr:
        L.append("\n### Cross-check: segmented by actual reach\n")
        L.append("Subscriber count is what drives the notification wave, but "
                 "dormant subscribers inflate it. This repeats the split "
                 "using each channel's AVERAGE VIEWS — live audience rather "
                 "than nominal following. Agreement between the two lenses "
                 "is reassuring; disagreement means subscriber counts in "
                 "this niche are misleading.\n")
        L.append("| reach band | videos | best hour block | best weekday |")
        L.append("|---|---|---|---|")
        for key, label in (("low_reach", "Low reach"),
                           ("mid_reach", "Mid reach"),
                           ("high_reach", "High reach")):
            seg = rr.get(key)
            if not seg:
                continue
            bh, bd = _best(seg["by_hour"]), _best(seg["by_weekday"])
            hour_txt = (f"{_fmt_block(bh['bucket'])} ({bh['outlier_index']})"
                        if bh else "—")
            day_txt = f"{bd['bucket']} ({bd['outlier_index']})" if bd else "—"
            L.append(f"| {label} ({seg['range']}) | {seg['videos']} | "
                     f"{hour_txt} | {day_txt} |")

    # --- Event-driven vs durable ---
    ed = rep.get("event_driven") or {}
    if ed.get("concentration_ratio"):
        L.append("\n### Event-driven or durable?\n")
        L.append(f"Top videos are **{ed['concentration_ratio']}x** more "
                 f"concentrated on the busiest days than an even spread "
                 f"would predict — _{ed['reading']}_.\n")
        L.append(f"{ed['share_on_hot_days']:.0%} of top performers landed on "
                 f"the busiest {ed['hot_days_counted']} of "
                 f"{ed['distinct_days']} publication days (pure chance "
                 f"would put {ed['random_null_share']:.0%} there).\n")
        if ed.get("busiest_days"):
            L.append("Busiest days for top performers:\n")
            for b in ed["busiest_days"]:
                L.append(f"- {b['date']} — {b['top_videos']} top videos")
        if ed.get("periodicity"):
            L.append(f"\n**Rhythm detected:** {ed['spike_count']} spike days, "
                     f"median gap **{ed['median_gap_days']} days** — "
                     f"{ed['periodicity']}.\n")
            if ed.get("spike_dates"):
                L.append("Spike dates (check these against an economic "
                         "calendar): " +
                         ", ".join(f"`{s}`" for s in ed["spike_dates"][:8]) + "\n")
        L.append("\n> In finance, heavy concentration usually means "
                 "scheduled releases (CPI, PPI, FOMC, jobs). Cross-check "
                 "those dates against an economic calendar. Event content "
                 "is a legitimate strategy with PREDICTABLE demand — but it "
                 "must ship same-day and it decays fast, which is the "
                 "opposite trade-off from evergreen content.\n")
    L.append("\n> **Caveat:** this shows when videos ALREADY PUBLISHED did "
             "well. It cannot tell you what would have happened had the same "
             "video gone out at a different hour. Competition matters too — "
             "a slot may look weak simply because everyone crowds it, which "
             "is an argument for posting there, not against.\n")
    return L
