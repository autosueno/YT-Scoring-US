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
from paths import split_window, WINDOW_SEP, locate, safe_name
import calibration


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
        if locate(base_dir, "metadata.json"):
            candidates.append((base_dir, name))
        for path, key in candidates:
            # locate() rather than a bare join: metadata is written as
            # <niche>_<window>_metadata.json, and joining the plain name
            # made every built niche invisible here while the files sat
            # on disk untouched.
            mp = locate(path, "metadata.json")
            if not mp:
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
    p75_v = meta.get("p75_views")
    # The legacy P90/median column is kept for continuity with older
    # exports, but the RATIO used for judgement is P90/P75 — see
    # calibration.py for why the median version fired the same label on
    # 11 of 11 measured niches.
    spread = (p90_v / med_v) if (med_v and p90_v) else None
    ratio = (p90_v / p75_v) if (p75_v and p90_v) else None
    return {
        "niche": base,
        "window": win or "?",
        "videos": meta.get("videos"),
        "channels": meta.get("channels"),
        "typical_views": med_v,
        "top10_views": p90_v,
        "spread": round(spread, 1) if spread else None,
        "ratio": round(ratio, 2) if ratio else None,
        "p75_views": p75_v,
        "typical_vph": meta.get("median_vph"),
        "top10_vph": meta.get("p90_vph"),
        "typical_engagement": meta.get("median_engagement"),
        "top10_engagement": meta.get("p90_engagement"),
        "derived": bool(meta.get("derived_from")),
        "seeds": meta.get("query"),
    }


def _window_days(key):
    """'finance@365d' -> 365. Unwindowed builds sort last."""
    _, win = split_window(key)
    if win and win.endswith("d") and win[:-1].isdigit():
        return int(win[:-1])
    return -1


def preferred_keys(niche_filter=None):
    """
    One key per niche NAME, choosing the longest window available.

    A 365-day build contains every shorter window inside it and rests on
    thirteen date slices rather than three, so its percentiles are the
    steadier ones. Given both a 90d and a 365d build of the same niche,
    comparing the 90d would be throwing away the better measurement —
    and doing it invisibly, since nothing in the output says which window
    was picked.

    niche_filter: optional collection of base names to keep.
    """
    best = {}
    for key, _meta in list_all_niches():
        base, _ = split_window(key)
        if niche_filter and base not in niche_filter:
            continue
        if base not in best or _window_days(key) > _window_days(best[base]):
            best[base] = key
    return [best[b] for b in sorted(best)]


def available_windows():
    """
    Every distinct window with at least one built niche, longest first.

    This exists because preferred_keys() always picks the longest window
    per niche, silently. That is the right DEFAULT, but it also makes a
    90d build permanently invisible to option 11 the moment a 365d build
    of the same niche exists — there was no way to explicitly ask "show
    me my 90d niches" at all. This is what lets the caller find out
    whether that question is even worth asking (more than one window
    actually exists) before asking it.
    """
    wins = set()
    for key, _meta in list_all_niches():
        _, win = split_window(key)
        if win:
            wins.add(win)
    return sorted(wins, key=lambda w: -_window_days(f"x{WINDOW_SEP}{w}"))


def keys_at_window(window, niche_filter=None):
    """
    One key per niche NAME that has a build at EXACTLY this window —
    the explicit counterpart to preferred_keys()'s "always longest".

    A niche with no build at this window is simply absent from the
    result, not silently substituted with a different window the way
    preferred_keys() would. Sorted for a stable, repeatable listing.
    """
    out = []
    for key, _meta in list_all_niches():
        base, win = split_window(key)
        if win != window:
            continue
        if niche_filter and base not in niche_filter:
            continue
        out.append(key)
    return sorted(out)


def window_mismatch(keys):
    """
    Describe a comparison that spans different collection windows.

    Returns None when every key shares a window. Otherwise returns a
    dict naming the windows involved, because the comparison is still
    worth running but the spread figures are NOT like-for-like: a 90-day
    sample looks narrower than a 365-day one no matter what the market
    is doing, since recent videos have not finished separating from each
    other. Silently mixing them turns a sampling artifact into a verdict.
    """
    wins = {}
    for k in keys:
        _, w = split_window(k)
        wins.setdefault(w or "none", []).append(split_window(k)[0])
    if len(wins) <= 1:
        return None
    widest = max(keys, key=_window_days)
    return {
        "windows": wins,
        "widest": split_window(widest)[1] or "none",
        "note": ("Spread is not comparable across windows. A shorter "
                 "window always looks narrower, because its videos have "
                 "had less time to separate from each other — so the "
                 "niche with the shorter window will understate its own "
                 "spread. Read the ordering with that in mind, or "
                 "rebuild them onto a common window."),
    }


def build_table(window_filter="365d", keys=None):
    """
    One row per niche.

    keys: explicit list of niche keys to include. When given it wins over
    window_filter entirely, so a deliberate mixed-window comparison is
    possible — window_mismatch() reports it rather than the table
    refusing to run.
    """
    rows = []
    if keys is not None:
        wanted = set(keys)
        for key, meta in list_all_niches():
            if key in wanted:
                rows.append(_row(key, meta))
    else:
        for key, meta in list_all_niches():
            _, win = split_window(key)
            if window_filter and win != window_filter:
                continue
            rows.append(_row(key, meta))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Sort by the ratio that drives the labels, not the legacy column —
    # ordering a table by one metric while labelling it with another is
    # how a reader ends up trusting the wrong number.
    sort_col = "ratio" if df["ratio"].notna().any() else "spread"
    return df.sort_values(sort_col, na_position="last").reset_index(drop=True)


def calibrate(df):
    """
    Derive spread thresholds from the user's OWN niches.

    Runs on the calibrated P90/P75 ratio, not the legacy P90/median.
    The median version was found to fire the same label on every niche
    measured, because in every one of them the median video is
    effectively dead content — see calibration.py.

    Returns None below five niches: thresholds from four points are a
    different guess wearing a number, which is no improvement on the
    packaged defaults.

    Boundaries land at the 33rd and 67th percentiles of what was
    measured here, so a third of these niches read gentle, a third
    normal, a third brutal. That is a statement about THIS collection
    and only as representative as the niches chosen — which is why the
    packaged defaults exist as a fallback rather than the other way
    round.
    """
    import calibration as _cal
    out = {"metric": "P90/P75", "groups": {}}
    ceil = pd.to_numeric(df.get("top10_views"), errors="coerce")
    ratios_all = pd.to_numeric(df.get("ratio"), errors="coerce")

    # Derived PER SIZE CLASS, because that is how the thresholds are
    # applied. Pooling large and small together would produce boundaries
    # that fit neither — which is exactly the mistake being corrected.
    for label, mask in (("large", ceil >= _cal.CEILING_LARGE_MIN),
                        ("small", ceil < _cal.CEILING_LARGE_MIN)):
        r = ratios_all[mask].dropna()
        if len(r) < 4:
            continue
        out["groups"][label] = {
            "n": int(len(r)), "min": float(r.min()),
            "p33": float(r.quantile(0.33)), "median": float(r.median()),
            "p67": float(r.quantile(0.67)), "max": float(r.max()),
        }
    return out if out["groups"] else None


def export_comparison(window_filter="365d", keys=None) -> dict:
    """Write the comparison table into exports/_compare/."""
    df = build_table(window_filter, keys=keys)
    if df.empty:
        return {"error": "no_niches"}

    out_dir = os.path.join(EXPORTS_DIR, "_compare")
    os.makedirs(out_dir, exist_ok=True)

    # Name the file after what it compares, not after a window that may
    # not even apply — two comparisons of different niche sets must not
    # overwrite each other, on disk or once attached to a message.
    #
    # BUG, FOUND AND FIXED: this used split_window(k)[0] — the bare niche
    # name only, with the window stripped out. So comparing the SAME
    # niches at 90d produced the IDENTICAL filename to comparing them at
    # 365d, and the second comparison silently overwrote the first on
    # disk. Invisible before option 11 could explicitly select a window;
    # became trivial to trigger the moment it could. Fixed by folding the
    # window into the stem — a single shared window gets a clean suffix,
    # a mixed-window selection is named explicitly as such rather than
    # picking one window's name and hiding the other.
    if keys:
        stem = "_vs_".join(safe_name(split_window(k)[0]) for k in keys[:3])
        if len(keys) > 3:
            stem += f"_and_{len(keys) - 3}_more"
        win_set = {split_window(k)[1] or "nowindow" for k in keys}
        stem += f"_{sorted(win_set)[0]}" if len(win_set) == 1 else "_mixedwindow"
    else:
        stem = f"compare_{window_filter or 'all'}"
    csv_path = os.path.join(out_dir, f"{stem}.csv")
    df.to_csv(csv_path, index=False)
    md_path = os.path.join(out_dir, f"{stem}.md")

    mismatch = window_mismatch(keys) if keys else None
    windows_used = sorted({str(r.window) for r in df.itertuples()})

    L = ["# Niche comparison", ""]
    if mismatch:
        L.append(f"{len(df)} niche(s) across **mixed windows** "
                 f"({', '.join(windows_used)}). Sorted by spread, "
                 f"narrowest first.")
        L.append("")
        L.append(f"> ⚠️ **Mixed windows.** {mismatch['note']}")
    else:
        L.append(f"{len(df)} niche(s) at the **{windows_used[0]}** window. "
                 f"Sorted by spread, narrowest first.")
        L.append("")
        L.append("> Spread is not comparable across windows — a 90-day "
                 "slice always looks narrower, because recent videos have "
                 "not finished separating from each other yet. Only "
                 "compare like with like.")
    L.append("")
    L.append("| niche | window | videos | top 10% | market | decent→hit | reading |")
    L.append("|---|---|---|---|---|---|---|")
    for r in df.itertuples():
        t10 = f"{r.top10_views:,.0f}" if pd.notna(r.top10_views) else "—"
        ratio = getattr(r, "ratio", None)
        size_band, _ = calibration.ceiling_band(
            r.top10_views if pd.notna(r.top10_views) else None)
        # Graded against peers of the same size — see calibration.py.
        band, _ = calibration.spread_band(
            ratio if pd.notna(ratio) else None, size_band or "small")
        rng = calibration.spread_range_text(ratio if pd.notna(ratio) else None)
        name = r.niche + (" *(sliced)*" if r.derived else "")
        L.append(f"| {name} | {r.window} | {r.videos:,} | {t10} | "
                 f"{size_band or '—'} | {rng} | {band or '—'} |")
    L.append("")
    L.append("> **market** is the size of the prize (top-10% views). "
             "**decent→hit** is how far a solid video sits from a "
             "breakout. They are independent — a small market can be "
             "brutal and a large one gentle — so they are never combined "
             "into a single score.")
    L.append("")

    L.append("## Thresholds in use")
    L.append("")
    L.append(f"Markets at or above {calibration.CEILING_LARGE_MIN:,} "
             f"top-10% views are graded as **large**, the rest as "
             f"**small** — and each size is judged on its OWN scale.")
    L.append("")
    L.append("| reading | large markets | small markets |")
    L.append("|---|---|---|")
    L.append(f"| gentle | below {calibration.LARGE_GENTLE_MAX:.1f}x "
             f"| below {calibration.SMALL_GENTLE_MAX:.1f}x |")
    L.append(f"| normal | {calibration.LARGE_GENTLE_MAX:.1f}x – "
             f"{calibration.LARGE_BRUTAL_MIN:.1f}x "
             f"| {calibration.SMALL_GENTLE_MAX:.1f}x – "
             f"{calibration.SMALL_BRUTAL_MIN:.1f}x |")
    L.append(f"| brutal | above {calibration.LARGE_BRUTAL_MIN:.1f}x "
             f"| above {calibration.SMALL_BRUTAL_MIN:.1f}x |")
    L.append("")
    L.append(f"> Two scales, not one. Across the calibration set no large "
             f"niche fell below 8.5x, so a single shared boundary made "
             f"'gentle' unreachable for large markets while 4 of 7 small "
             f"ones earned it. Large markets carry both more amateur "
             f"uploads and bigger breakouts, so the decent-to-hit "
             f"distance is wider throughout. Grading each size against "
             f"its own peers is the only comparison a creator can act on. "
             f"Large thresholds rest on just "
             f"{calibration.SPREAD_CALIBRATION_LARGE_N} niches — "
             f"provisional.")
    L.append("")

    if "top10_views" in df.columns:
        big = df[pd.to_numeric(df.top10_views, errors="coerce")
                 >= calibration.CEILING_LARGE_MIN]
        sml = df[pd.to_numeric(df.top10_views, errors="coerce")
                 < calibration.CEILING_LARGE_MIN]
        L.append(f"Of these, {len(big)} large and {len(sml)} small.")
        L.append("")

    cal = calibrate(df)
    if cal:
        L.append("### Your own niches say")
        L.append("")
        L.append("| size | n | range | your p33 | your p67 | packaged |")
        L.append("|---|---|---|---|---|---|")
        packaged = {"large": (calibration.LARGE_GENTLE_MAX,
                              calibration.LARGE_BRUTAL_MIN),
                    "small": (calibration.SMALL_GENTLE_MAX,
                              calibration.SMALL_BRUTAL_MIN)}
        drifted = False
        for label, g in cal["groups"].items():
            pg, pb = packaged[label]
            if abs(g["p33"] - pg) > 2 or abs(g["p67"] - pb) > 2:
                drifted = True
            L.append(f"| {label} | {g['n']} | {g['min']:.1f}x–{g['max']:.1f}x "
                     f"| {g['p33']:.1f}x | {g['p67']:.1f}x "
                     f"| {pg:.1f}x / {pb:.1f}x |")
        L.append("")
        if drifted:
            L.append("> Your niches sit well away from the packaged "
                     "thresholds. Either this selection is unusual, or "
                     "the defaults need re-deriving — worth checking "
                     "before trusting the labels above.")
        else:
            L.append("> Close to the packaged thresholds, which is mild "
                     "evidence that both describe the same underlying "
                     "population.")
        L.append("")
    else:
        L.append("### Your own niches")
        L.append("")
        L.append("Needs 4+ niches within a size class to check the "
                 "defaults against your own data. The packaged "
                 "thresholds above are in use.")
        L.append("")

    L.append("## How to read this")
    L.append("")
    L.append("**decent→hit** is the climb from a solid video to a "
             "breakout. At 4x, a decent video already earns a quarter of "
             "what a hit earns and steady work compounds. At 13x it earns "
             "under a tenth — being good is not enough, and almost "
             "everything goes to breakouts.")
    L.append("")
    L.append("**Gentle is not automatically better.** A gentle market is "
             "predictable but its ceiling is usually lower. A brutal one "
             "is punishing but proves large outcomes are reachable. Read "
             "it against the **market** column: a large brutal market is "
             "a lottery with a real prize, a small gentle one is a steady "
             "climb with a modest one.")
    L.append("")
    L.append("**The ratio is a band, not a point.** Contamination from "
             "off-topic videos inflates it by ~38% at 20% contamination, "
             "though it inflates every niche about equally, so the "
             "ORDERING holds (rank correlation 0.96) even when the "
             "absolute number drifts.")
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
            "mismatch": window_mismatch(keys) if keys else None,
            "calibrated": bool(cal)}
