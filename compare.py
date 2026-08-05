"""
compare.py — put niches side by side.

WHY THIS IS SEPARATE FROM GRADING
---------------------------------
grade_niche() compares a niche against a baseline built from ITSELF,
which forces demand to about 64 and supply to exactly 50 by arithmetic.
Those numbers say nothing about the niche; they are a property of the
formula meeting its own reference population.

Comparing niches to EACH OTHER is a different operation and needs no
grading at all. Every build already stores its own medians, P90s and
counts, so the comparison is a table lookup — no quota, no recompute,
and it works on however many niches happen to be on disk.

WHAT THE COLUMNS MEAN, AND WHY THEY ARE NOT ALL THE SAME KIND OF THING
----------------------------------------------------------------------
typical      median views. What an ordinary video earns here.
top 10%      the 90th percentile. What a good video earns.
spread       top10 / typical. How far you have to climb from ordinary
             to good — the single most useful number for deciding
             whether a market is enterable.
depth        whether collection ran past the supply of relevant videos.
             A niche flagged here has an INFLATED spread, because
             padding sits at the bottom of the distribution. Read its
             spread as an upper bound, not a measurement.

THE SPREAD THRESHOLDS ARE UNVALIDATED
-------------------------------------
The labels attached to spread (winner-take-all / top-heavy / flat) come
from reasoning, not evidence. This module exists partly so they can be
replaced: build a spread of niches, look at where real markets actually
fall, and set the boundaries at observed percentiles instead of round
numbers. calibrate() does that arithmetic once there is something to
calibrate against.
"""

import json
import os

import pandas as pd

from config import DATASETS_DIR, EXPORTS_DIR
from paths import split_window, WINDOW_SEP


def list_all_niches():
    """
    Every built niche on disk, as (key, metadata) pairs.

    Walks datasets/<niche>/<window>/metadata.json. Windows are separate
    entries because a niche at 365d and the same niche at 90d describe
    different populations and should never be silently merged.
    """
    out = []
    if not os.path.isdir(DATASETS_DIR):
        return out
    for name in sorted(os.listdir(DATASETS_DIR)):
        base_dir = os.path.join(DATASETS_DIR, name)
        if not os.path.isdir(base_dir):
            continue
        # Window subfolders, plus the legacy flat layout.
        candidates = [(os.path.join(base_dir, d), f"{name}{WINDOW_SEP}{d}")
                      for d in sorted(os.listdir(base_dir))
                      if os.path.isdir(os.path.join(base_dir, d))
                      and d.endswith("d") and d[:-1].isdigit()]
        if os.path.exists(os.path.join(base_dir, "metadata.json")):
            candidates.append((base_dir, name))
        for path, key in candidates:
            mp = os.path.join(path, "metadata.json")
            if not os.path.exists(mp):
                continue
            try:
                with open(mp, encoding="utf-8") as f:
                    out.append((key, json.load(f)))
            except Exception:
                continue
    return out


def _row(key, meta):
    base, win = split_window(key)
    med_v = meta.get("median_views")
    p90_v = meta.get("p90_views")
    spread = (p90_v / med_v) if (med_v and p90_v) else None
    return {
        "niche": base,
        "window": win or "?",
        "videos": meta.get("videos"),
        "channels": meta.get("channels"),
        "typical_views": med_v,
        "top10_views": p90_v,
        "spread": round(spread, 1) if spread else None,
        "typical_vph": meta.get("median_vph"),
        "top10_vph": meta.get("p90_vph"),
        "typical_engagement": meta.get("median_engagement"),
        "top10_engagement": meta.get("p90_engagement"),
        "derived": bool(meta.get("derived_from")),
        "seeds": meta.get("query"),
    }


def build_table(window_filter="365d"):
    """
    One row per niche. Restricted to a single window by default, because
    spread is not comparable across windows — a 90-day slice shows a
    much narrower spread simply because recent videos have not finished
    separating yet.
    """
    rows = []
    for key, meta in list_all_niches():
        _, win = split_window(key)
        if window_filter and win != window_filter:
            continue
        rows.append(_row(key, meta))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("spread", na_position="last").reset_index(drop=True)


def calibrate(df):
    """
    Derive spread thresholds from observed niches instead of assumption.

    Returns None below five niches — thresholds from four points would
    be a different guess wearing a number, which is no improvement on
    the guess already in place.

    Boundaries land at the 33rd and 67th percentiles of what was
    actually measured, so a third of real niches read as flat, a third
    normal, a third winner-take-all. That is a statement about THIS
    collection of niches, and it is only as representative as the
    niches chosen.
    """
    spreads = pd.to_numeric(df.get("spread"), errors="coerce").dropna()
    if len(spreads) < 5:
        return None
    return {
        "n": int(len(spreads)),
        "min": float(spreads.min()),
        "p33": float(spreads.quantile(0.33)),
        "median": float(spreads.median()),
        "p67": float(spreads.quantile(0.67)),
        "max": float(spreads.max()),
    }


def export_comparison(window_filter="365d") -> dict:
    """Write compare.md and compare.csv into exports/."""
    df = build_table(window_filter)
    if df.empty:
        return {"error": "no_niches"}

    out_dir = os.path.join(EXPORTS_DIR, "_compare")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"compare_{window_filter or 'all'}.csv")
    df.to_csv(csv_path, index=False)
    md_path = os.path.join(out_dir, f"compare_{window_filter or 'all'}.md")

    L = ["# Niche comparison", ""]
    L.append(f"{len(df)} niche(s) at the **{window_filter}** window. "
             f"Sorted by spread, narrowest first.")
    L.append("")
    L.append("> Spread is not comparable across windows — a 90-day slice "
             "always looks narrower, because recent videos have not "
             "finished separating from each other yet. Only compare like "
             "with like.")
    L.append("")
    L.append("| niche | videos | typical views | top 10% | spread | typical eng |")
    L.append("|---|---|---|---|---|---|")
    for r in df.itertuples():
        tv = f"{r.typical_views:,.0f}" if pd.notna(r.typical_views) else "—"
        t10 = f"{r.top10_views:,.0f}" if pd.notna(r.top10_views) else "—"
        sp = f"{r.spread:.1f}x" if pd.notna(r.spread) else "—"
        eng = (f"{r.typical_engagement:.2%}"
               if pd.notna(r.typical_engagement) else "—")
        name = r.niche + (" *(sliced)*" if r.derived else "")
        L.append(f"| {name} | {r.videos:,} | {tv} | {t10} | {sp} | {eng} |")
    L.append("")

    cal = calibrate(df)
    if cal:
        L.append("## Calibrated spread thresholds")
        L.append("")
        L.append(f"From {cal['n']} measured niches, ranging "
                 f"{cal['min']:.1f}x to {cal['max']:.1f}x, median "
                 f"{cal['median']:.1f}x.")
        L.append("")
        L.append("| reading | spread |")
        L.append("|---|---|")
        L.append(f"| flat — steady returns, few breakouts | below {cal['p33']:.0f}x |")
        L.append(f"| normal for YouTube | {cal['p33']:.0f}x to {cal['p67']:.0f}x |")
        L.append(f"| winner-take-all | above {cal['p67']:.0f}x |")
        L.append("")
        L.append("> These come from the niches YOU built, so they describe "
                 "that selection rather than YouTube at large. Boundaries "
                 "sit at the 33rd and 67th percentiles of what was "
                 "observed. To adopt them, replace the thresholds in "
                 "report.print_niche_summary.")
        L.append("")
    else:
        L.append("## Calibration")
        L.append("")
        L.append(f"Needs at least 5 niches at this window; {len(df)} "
                 f"available. Thresholds from fewer would be a different "
                 f"guess rather than a measurement, so none are offered.")
        L.append("")

    L.append("## How to read this")
    L.append("")
    L.append("**Spread** is the climb from ordinary to good. A niche at "
             "8x means a solid video earns eight times the median; at "
             "120x, being ordinary earns almost nothing and only "
             "breakouts matter.")
    L.append("")
    L.append("**Low spread is not automatically better.** A flat niche is "
             "predictable but has no ceiling — nothing there ever takes "
             "off. A high spread is brutal but proves large outcomes are "
             "possible.")
    L.append("")
    L.append("**Check the build output for depth warnings** before "
             "trusting any spread here. A niche collected past its supply "
             "of relevant videos carries padding at the bottom of its "
             "distribution, which inflates spread. That is a measurement "
             "artefact, not a market.")
    L.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"md": md_path, "csv": csv_path, "niches": len(df),
            "calibrated": bool(cal)}
