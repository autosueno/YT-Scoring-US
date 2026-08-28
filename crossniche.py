"""
crossniche.py — grade a niche against OTHER niches, not against itself.

THE PROBLEM THIS EXISTS TO FIX
------------------------------
Option 8 grades a niche against a baseline built from that same niche.
The result is arithmetic, not measurement:

    a niche's own median sits, by definition, at the 50th percentile
    of its own distribution                                 -> 50
    a niche's own P90 sits at the 90th percentile of itself  -> 90
    blended with DEMAND_TAIL_WEIGHT = 0.35:
        0.65 * 50 + 0.35 * 90                               -> 64.0

Which is why every metric, in every niche, returned exactly 64.0, and
supply returned exactly 50.0 (median-only, no tail blend). Observed
across eleven separate niches with wildly different markets — from
home_espresso to world_war_two — all producing identical pillar scores.

The number was never wrong. It was never a number ABOUT the niche.

WHAT CHANGES HERE
-----------------
The reference population becomes the OTHER niches. "Demand 72" then
means this niche's demand sits well above the typical niche in the
library, which is the comparison a person actually has in mind when they
read a demand score.

LEAVE-ONE-OUT, AND WHY IT MATTERS
---------------------------------
A niche is never included in the baseline it is graded against. Pooling
all eleven and grading one of them against the pool would drag the
reference toward the thing being measured — a milder version of the same
self-comparison bug, and harder to spot because the numbers would no
longer be suspiciously round. With eleven niches a single one carries
about 9% of the pool; that is enough to matter and cheap to exclude.

WHAT THIS CANNOT DO
-------------------
Momentum is untouched. It already compares recent videos against earlier
ones — genuinely different populations — so it was the only pillar
producing real variation before this, and it needs no fixing.

Scores here are relative to YOUR library. A niche graded against eleven
mostly-small niches will read differently once large ones are added.
That is a real property of the method, not a defect, but it means the
numbers move as the library grows and should not be treated as absolute.
"""

import os

import pandas as pd

import baseline as _bl


MIN_POOL_NICHES = 3   # below this, "typical niche" is not a meaningful idea


def _load_videos(key):
    from paths import videos_path
    p = videos_path(key)
    if not os.path.exists(p):
        return None
    try:
        return pd.read_csv(p, low_memory=False)
    except Exception:
        return None


def available_keys(window_filter="365d"):
    """Niche keys with a readable videos.csv at the given window."""
    import compare as _cmp
    from paths import split_window
    out = []
    for key, _meta in _cmp.list_all_niches():
        _, win = split_window(key)
        if window_filter and win != window_filter:
            continue
        out.append(key)
    return out


def pooled_baseline(exclude_key, keys, window_filter="365d", cache=None):
    """
    A Baseline built from every niche EXCEPT exclude_key.

    cache: optional dict of key -> DataFrame, so a full library sweep
    reads each videos.csv once rather than once per niche graded.
    """
    frames = []
    for k in keys:
        if k == exclude_key:
            continue
        df = cache.get(k) if cache is not None else None
        if df is None:
            df = _load_videos(k)
            if cache is not None and df is not None:
                cache[k] = df
        if df is not None and not df.empty:
            frames.append(df)
    if len(frames) < MIN_POOL_NICHES:
        return None, len(frames)
    pooled = pd.concat(frames, ignore_index=True)
    label = f"pool of {len(frames)} niches (excluding {exclude_key})"
    return _bl.from_frame(pooled, label=label), len(frames)


def grade_against_library(key, keys=None, window_filter="365d", cache=None):
    """
    Grade one niche against the rest of the library.

    Returns the usual grade result dict with cross-niche context added,
    or a dict carrying 'error' when the library is too small.
    """
    from grader import grade_niche
    import collector

    keys = keys or available_keys(window_filter)
    bl, n_pool = pooled_baseline(key, keys, window_filter, cache)
    if bl is None:
        return {"error": "pool_too_small", "pool_niches": n_pool,
                "needed": MIN_POOL_NICHES}

    df = _load_videos(key)
    if df is None or df.empty:
        return {"error": "no_dataset", "key": key}

    meta = collector.load_metadata(key) or {}
    result = grade_niche(df, bl, key, lookback_days=meta.get("lookback_days"))

    # Bands and grade are NOT applied here. They need the demand/supply/
    # opportunity scores of every OTHER niche in the pool, which are not
    # all known until the whole sweep has run — see _apply_relative_bands.
    # Applying a fixed window at this point reproduces exactly the bug
    # this module exists to fix, one call site later: a niche graded
    # alone would need to borrow SOME range from somewhere, and any fixed
    # range is only correct for the data it was measured on.
    result["_absolute_grade"] = result.get("grade")   # kept for comparison
    result["reference"] = {
        "kind": "cross_niche",
        "pool_niches": n_pool,
        "pool_videos": int(sum(len(cache[k]) for k in cache) if cache else 0),
        "excluded_self": True,
        "note": ("Scored against other niches in this library, not against "
                 "itself. Self-comparison forces demand to 64.0 and supply "
                 "to 50.0 by arithmetic and says nothing about the market. "
                 "Verdict and grade are RELATIVE to this library and will "
                 "shift as more niches are added — see calibration.py."),
    }
    return result


def _apply_relative_bands(rows_by_key):
    """
    Second pass over an already-graded pool: compute each niche's
    demand/supply band and letter grade from the ACTUAL distribution of
    scores in this sweep, not from a fixed range measured on some other
    run. This is what makes the grading genuinely relative rather than
    relative-in-name-with-a-hardcoded-window.
    """
    import calibration
    demands = [r["demand"] for r in rows_by_key.values()]
    supplies = [r["supply"] for r in rows_by_key.values()]
    opps = [r["opportunity"] for r in rows_by_key.values()]
    for row in rows_by_key.values():
        d_band, s_band = calibration.relative_verdict_bands(
            row["demand"], row["supply"], pool_demand=demands, pool_supply=supplies)
        row["demand_band"] = d_band
        row["supply_band"] = s_band
        row["grade"] = calibration.relative_grade(row["opportunity"], pool_scores=opps)


def sweep_selected(keys, verbose=False):
    """
    Like sweep(), but grades exactly the given keys against each other
    rather than discovering every niche at a window. Used by the option
    10 menu flow, where the person has already picked which niches to
    compare — grading against a DIFFERENT, wider pool than what they
    selected would silently answer a different question than the one
    they asked.
    """
    if len(keys) <= MIN_POOL_NICHES:
        return {"error": f"needs at least {MIN_POOL_NICHES + 1} niches, "
                         f"got {len(keys)}"}
    cache = {}
    rows_by_key, results = {}, {}
    for key in keys:
        res = grade_against_library(key, keys, window_filter=None, cache=cache)
        if res.get("error"):
            if verbose:
                print(f"  {key}: skipped ({res['error']})")
            continue
        results[key] = res
        p = res.get("pillars") or {}
        rows_by_key[key] = {
            "niche": key,
            "demand": (p.get("demand") or {}).get("score"),
            "supply": (p.get("supply") or {}).get("score"),
            "momentum": (p.get("momentum") or {}).get("signed_change"),
            "opportunity": res.get("score"),
            "verdict": res.get("verdict"),
        }
    if not rows_by_key:
        return {"error": "no_gradeable_niches"}
    _apply_relative_bands(rows_by_key)
    return {"table": pd.DataFrame(list(rows_by_key.values())),
            "results": results, "niches": len(rows_by_key)}


def sweep(window_filter="365d", verbose=True):
    """
    Grade every niche in the library against the others.

    This is the operation that makes the pillar scores comparable, and it
    is entirely offline — every input is already on disk.
    """
    keys = available_keys(window_filter)
    if len(keys) <= MIN_POOL_NICHES:
        return {"error": "library_too_small", "niches": len(keys),
                "needed": MIN_POOL_NICHES + 1}

    cache = {}
    rows_by_key, results = {}, {}
    for key in keys:
        res = grade_against_library(key, keys, window_filter, cache)
        if res.get("error"):
            if verbose:
                print(f"  {key}: skipped ({res['error']})")
            continue
        results[key] = res
        p = res.get("pillars") or {}
        rows_by_key[key] = {
            "niche": key,
            "demand": (p.get("demand") or {}).get("score"),
            "supply": (p.get("supply") or {}).get("score"),
            "momentum": (p.get("momentum") or {}).get("signed_change"),
            "opportunity": res.get("score"),
            "verdict": res.get("verdict"),
        }

    if not rows_by_key:
        return {"error": "no_gradeable_niches"}

    # Bands and grade need every niche's score, so they are computed once
    # the whole pool is in — see _apply_relative_bands.
    _apply_relative_bands(rows_by_key)
    if verbose:
        for key, row in rows_by_key.items():
            print(f"  {key:<30} demand={row['demand']} ({row['demand_band']})  "
                  f"supply={row['supply']} ({row['supply_band']})  "
                  f"opp={row['opportunity']} [{row['grade']}]")

    return {"table": pd.DataFrame(list(rows_by_key.values())),
            "results": results, "niches": len(rows_by_key)}
