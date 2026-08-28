"""
trends.py — compare a niche against its own past.

THE THING THIS UNLOCKS
----------------------
Every other module in this engine reads ONE snapshot. That imposes a
hard limit which has come up repeatedly: with a single observation you
cannot tell a video that is still climbing from one that stopped
climbing months ago. `views_per_hour` is a LIFETIME AVERAGE — total
views divided by total age — so a video that did 400,000 views in its
first week and nothing since reports the same number forever, decaying
only because the denominator grows.

Two snapshots fix that completely:

    real_views_per_day = (views_now - views_then) / days_between

That is actual current demand, measured rather than inferred. It is the
difference between "this video was popular at some point" and "this
video is being watched right now", and no amount of cleverness extracts
it from a single build.

Every rebuild has been archiving the previous dataset to
history/<niche>/<date>/ since early on, and nothing has ever read one.
This module reads them.

WHAT IT NEEDS
-------------
At least two builds of the same niche, on different days. The first
build produces nothing to compare against — that is expected, not an
error.
"""

import os
from collections import Counter

import pandas as pd

from config import EXPORTS_DIR, HISTORY_DIR
from paths import export_dir_for, safe_name, locate as _locate
from paths import named as _named
from insights import _STOPWORDS, _tokenize


# A video needs this many days between observations before its measured
# growth means anything. Below it, normal daily noise swamps the signal.
MIN_DAYS_BETWEEN = 2


# ============================================================
# READING SNAPSHOTS
# ============================================================

def list_snapshots(niche_name):
    """
    Archived builds for a niche, oldest first.

    Returns [{date, path, videos_csv}]. Folders are named by the build's
    OWN creation date, so the ordering is real chronology rather than
    the order things happened to be archived.
    """
    root = os.path.join(HISTORY_DIR, safe_name(niche_name))
    if not os.path.isdir(root):
        return []
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        csv = _locate(p, "videos.csv") or os.path.join(p, "videos.csv")
        if os.path.isdir(p) and os.path.exists(csv):
            out.append({"date": d, "path": p, "videos_csv": csv})
    return out


def load_snapshot(entry):
    """Read one archived videos.csv. Returns None if unreadable."""
    try:
        return pd.read_csv(entry["videos_csv"])
    except Exception:
        return None


def _snapshot_date(df, fallback):
    """
    Best estimate of when a snapshot was taken.

    hours_since_upload was computed AT COLLECTION TIME against the
    newest video, so the newest video's publish date plus its recorded
    age reconstructs the collection moment more precisely than the
    folder name, which only has day resolution.
    """
    try:
        pub = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
        hrs = pd.to_numeric(df["hours_since_upload"], errors="coerce")
        est = (pub + pd.to_timedelta(hrs, unit="h")).dropna()
        if len(est):
            return est.median()
    except Exception:
        pass
    return pd.to_datetime(fallback, utc=True, errors="coerce")


# ============================================================
# COMPARISON
# ============================================================

# A market-movement % change smaller than this is not reliably a real
# shift — sample composition changes between builds (a slightly
# different set of videos matching the same query) by roughly this much
# on its own, even when nothing in the market actually moved. Presenting
# a 3% shift with the same confidence as a 30% one is misleading; this
# gives the reader an honest "don't read into this" signal instead.
NOISE_FLOOR_PCT = 5.0


def _market_reading(change):
    """Plain-English reading for one market-movement metric's % change."""
    if abs(change) < NOISE_FLOOR_PCT:
        return "within noise range — not a signal either way"
    direction = "up" if change > 0 else "down"
    magnitude = "sharply" if abs(change) >= 20 else "modestly"
    return f"{direction} {magnitude} — likely a real shift, not noise"


def compare_snapshots(now_df, then_df, now_date=None, then_date=None):
    """
    Diff two snapshots of the same niche.

    The matched set (videos present in both) is where the real value is:
    those give measured growth. The unmatched sets are informative too —
    what entered the market, and what fell out of search results.
    """
    if now_df is None or then_df is None or now_df.empty or then_df.empty:
        return {}
    if "video_id" not in now_df.columns or "video_id" not in then_df.columns:
        return {}

    now_date = now_date if now_date is not None else _snapshot_date(now_df, None)
    then_date = then_date if then_date is not None else _snapshot_date(then_df, None)
    days = None
    try:
        days = float((now_date - then_date).total_seconds() / 86400.0)
    except Exception:
        pass

    a = now_df.set_index("video_id", drop=False)
    b = then_df.set_index("video_id", drop=False)
    common = a.index.intersection(b.index)
    entered = a.index.difference(b.index)
    left = b.index.difference(a.index)

    result = {
        "days_between": round(days, 1) if days else None,
        "videos_now": int(len(a)),
        "videos_then": int(len(b)),
        "matched": int(len(common)),
        "entered": int(len(entered)),
        "left": int(len(left)),
        "reliable": bool(days and days >= MIN_DAYS_BETWEEN and len(common) >= 20),
    }

    # --- measured growth on the matched set ---
    if len(common) and days and days >= MIN_DAYS_BETWEEN:
        m = pd.DataFrame({
            "video_id": common,
            "title": a.loc[common, "title"],
            "channel_title": a.loc[common, "channel_title"],
            "channel_subs": pd.to_numeric(a.loc[common, "channel_subs"],
                                          errors="coerce"),
            "views_now": pd.to_numeric(a.loc[common, "view_count"],
                                       errors="coerce"),
            "views_then": pd.to_numeric(b.loc[common, "view_count"],
                                        errors="coerce"),
        }).dropna(subset=["views_now", "views_then"])
        m["gained"] = m["views_now"] - m["views_then"]
        m["real_views_per_day"] = (m["gained"] / days).round(1)
        # Lifetime average, for the comparison that makes the point.
        if "views_per_hour" in a.columns:
            m["lifetime_vph"] = pd.to_numeric(
                a.loc[m["video_id"], "views_per_hour"], errors="coerce").values
            m["real_vph"] = (m["real_views_per_day"] / 24).round(2)
            # >1 means it is outrunning its own historical average right
            # now; <1 means it has cooled off from what it used to do.
            m["heat"] = (m["real_vph"] /
                         m["lifetime_vph"].replace(0, pd.NA)).round(2)
        result["growth"] = m.sort_values("real_views_per_day",
                                         ascending=False).reset_index(drop=True)
        result["total_gained"] = int(m["gained"].sum())
        result["median_daily_gain"] = float(m["real_views_per_day"].median())
        if "heat" in m.columns:
            hot = m[m["heat"].notna()]
            result["still_climbing"] = int((hot["heat"] >= 1.0).sum())
            result["cooled_off"] = int((hot["heat"] < 0.5).sum())

    # --- market-level movement ---
    def _med(frame, col):
        if col not in frame.columns:
            return None
        v = pd.to_numeric(frame[col], errors="coerce").dropna()
        return float(v.median()) if len(v) else None

    market = {}
    for col, label in (("views_per_hour", "median views/hour"),
                       ("engagement_rate", "median engagement rate"),
                       ("channel_subs", "median channel size")):
        n, t = _med(a, col), _med(b, col)
        if n is not None and t is not None and t != 0:
            market[col] = {"label": label, "then": t, "now": n,
                           "change": round((n / t - 1) * 100, 1)}
    result["market"] = market

    # --- vocabulary churn: what the market started and stopped saying ---
    def _terms(frame):
        c = Counter()
        for t in frame["title"].fillna(""):
            c.update(w for w in _tokenize(str(t))
                     if w not in _STOPWORDS and len(w) > 3)
        return c

    tn, tt = _terms(a), _terms(b)
    n_now, n_then = max(len(a), 1), max(len(b), 1)
    churn = []
    for term in set(list(tn) + list(tt)):
        share_now = tn.get(term, 0) / n_now
        share_then = tt.get(term, 0) / n_then
        if tn.get(term, 0) < 3 and tt.get(term, 0) < 3:
            continue
        churn.append({
            "term": term,
            "then": round(share_then, 4), "now": round(share_now, 4),
            "delta": round(share_now - share_then, 4),
        })
    churn.sort(key=lambda r: -r["delta"])
    result["rising_terms"] = churn[:15]
    result["falling_terms"] = churn[-15:][::-1]

    return result


# ============================================================
# EXPORT
# ============================================================

def export_trend_report(niche_name, current_df) -> dict:
    """Compare the live dataset against the most recent archive."""
    snaps = list_snapshots(niche_name)
    if not snaps:
        return {"error": "no_history"}
    prev = snaps[-1]
    then_df = load_snapshot(prev)
    if then_df is None:
        return {"error": "unreadable"}

    cmp = compare_snapshots(current_df, then_df,
                            then_date=pd.to_datetime(prev["date"], utc=True))
    if not cmp:
        return {"error": "incomparable"}

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "trends")
    md_path = os.path.join(out_dir, _named(niche_name, "since_last_build.md"))

    L = [f"# What changed — {niche_name}", ""]
    L.append(f"Comparing the current dataset against the build archived on "
             f"**{prev['date']}**"
             + (f", **{cmp['days_between']:.0f} days** ago."
                if cmp.get("days_between") else "."))
    L.append("")
    L.append("| | then | now |")
    L.append("|---|---|---|")
    L.append(f"| Videos in sample | {cmp['videos_then']:,} | {cmp['videos_now']:,} |")
    L.append(f"| Present in both | — | {cmp['matched']:,} |")
    L.append(f"| New to the sample | — | {cmp['entered']:,} |")
    L.append(f"| Dropped out | — | {cmp['left']:,} |")
    L.append("")

    if not cmp.get("reliable"):
        L.append("> ⚠️ **Too little separation for reliable comparison.** "
                 "Needs at least a couple of days between builds and 20+ "
                 "videos present in both. Treat everything below as "
                 "indicative.")
        L.append("")

    g = cmp.get("growth")
    if g is not None and len(g):
        L.append("## Measured growth")
        L.append("")
        L.append(f"Across the {cmp['matched']:,} videos present in both "
                 f"snapshots, the niche gained **{cmp['total_gained']:,} "
                 f"views** in {cmp['days_between']:.0f} days. Median video "
                 f"picked up **{cmp['median_daily_gain']:,.0f} views/day**.")
        L.append("")
        L.append("> This is the one number in the whole engine that is "
                 "**measured rather than inferred**. Everywhere else, "
                 "`views_per_hour` is a lifetime average — total views "
                 "over total age — so a video that did 400,000 views in "
                 "its first week and nothing since keeps reporting a high "
                 "figure forever. Two snapshots give actual current "
                 "demand.")
        L.append("")
        if "still_climbing" in cmp:
            L.append(f"**{cmp['still_climbing']}** videos are currently "
                     f"outrunning their own lifetime average; "
                     f"**{cmp['cooled_off']}** have dropped below half of "
                     f"it. The second group is the interesting one — those "
                     f"look strong in every single-snapshot report and are "
                     f"actually finished.")
            L.append("")

        L.append("### Fastest growing right now")
        L.append("")
        has_heat = "heat" in g.columns
        L.append("| views/day now | gained | total views | subs | "
                 + ("heat | " if has_heat else "") + "title |")
        L.append("|---|---|---|---|" + ("---|" if has_heat else "") + "---|")
        for r in g.head(15).itertuples():
            heat = (f" {r.heat} |" if has_heat and pd.notna(r.heat) else
                    (" — |" if has_heat else ""))
            t = str(r.title)[:58].replace("|", "\\|")
            L.append(f"| {r.real_views_per_day:,.0f} | {int(r.gained):,} | "
                     f"{int(r.views_now):,} | {int(r.channel_subs):,} |"
                     f"{heat} {t} |")
        L.append("")
        if has_heat:
            L.append("> **heat** compares current pace against the video's "
                     "own lifetime average. Above 1.0 means it is being "
                     "watched faster now than it ever was; below 0.5 means "
                     "it has cooled off.")
            L.append("")

    mk = cmp.get("market") or {}
    if mk:
        L.append("## Market movement")
        L.append("")
        L.append(f"Shifts smaller than ±{NOISE_FLOOR_PCT:.0f}% are flagged as "
                 f"**noise** rather than given a precise reading — sample "
                 f"composition changes between builds by roughly that much "
                 f"on its own, even when the market hasn't actually moved.")
        L.append("")
        L.append("| metric | then | now | change | reading |")
        L.append("|---|---|---|---|---|")
        for v in mk.values():
            reading = _market_reading(v["change"])
            if abs(v["change"]) < NOISE_FLOOR_PCT:
                change_cell = f"±{v['change']:+.1f}% (noise)"
            else:
                arrow = "↑" if v["change"] > 0 else "↓"
                change_cell = f"{arrow} {v['change']:+.1f}%"
            L.append(f"| {v['label']} | {v['then']:,.2f} | {v['now']:,.2f} | "
                     f"{change_cell} | {reading} |")
        L.append("")
        L.append("> Sample composition changes between builds, so a shift "
                 "here is partly the market moving and partly a different "
                 "set of videos being returned. Read it as directional.")
        L.append("")

    rising = cmp.get("rising_terms") or []
    falling = cmp.get("falling_terms") or []
    if rising:
        L.append("## What the market started talking about")
        L.append("")
        L.append("| term | then | now |")
        L.append("|---|---|---|")
        for r in rising[:12]:
            L.append(f"| {r['term']} | {r['then']:.1%} | {r['now']:.1%} |")
        L.append("")
    if falling:
        L.append("## What it stopped talking about")
        L.append("")
        L.append("| term | then | now |")
        L.append("|---|---|---|")
        for r in falling[:12]:
            L.append(f"| {r['term']} | {r['then']:.1%} | {r['now']:.1%} |")
        L.append("")

    L.append("## Hand this to an AI")
    L.append("")
    L.append("1. Which rising terms are a **durable shift** versus a news "
             "cycle that will be stale in a fortnight?")
    L.append("2. Among the fastest-growing videos, is there a **common "
             "angle** rather than a common subject?")
    L.append("3. The cooled-off videos looked strong in earlier reports. "
             "What did they have in common, and what does that say about "
             "which signals to distrust?")
    L.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    paths = {"report": md_path}
    if g is not None and len(g):
        csv_path = os.path.join(out_dir, _named(niche_name, "measured_growth.csv"))
        g.to_csv(csv_path, index=False)
        paths["growth_csv"] = csv_path
    paths["_comparison"] = cmp
    return paths
