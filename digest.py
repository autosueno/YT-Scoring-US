"""
digest.py — cross-niche cluster digest.

THE WORKFLOW THIS AUTOMATES
----------------------------
An anchor niche (say, personal_finance) plus a handful of crossover
niches (personal_psychology, investing), each built at more than one
window (30d for what's happening now, 90d for the last quarter). After
option 6 (discover breakout opportunities) has run on all of them, the
manual version of this workflow was:

    1. Open every niche's breakout_clusters.csv.
    2. Derive a video-count filter from each file's own distribution
       (NOT one global filter — see _percentile_filter below for why
       that matters), and drop thin clusters.
    3. Pool every file's survivors into one list, rank by
       avg_breakout x small_creator_share ("attention score").
    4. Paste the ranked table into an AI with a title-generation prompt.

Steps 1-3 are pure math over data that already exists on disk — no
judgement call anywhere in them — so they belong in the engine, not in
a chat window. Step 4 (turning ranked clusters into actual titles) is
editorial judgement, so this module does not attempt it; it assembles
the table and writes the prompt, exactly like angles.py does for
single-niche angle generation. The reasoning still happens on the
human/AI side of the loop.

WHERE THE DATA COMES FROM
--------------------------
This reads the FULL cluster list from each niche@window's breakout
JSON (written by discover.export_breakouts), not the CLUSTER_MAX-
curated markdown subset. See discover.build_clusters()'s docstring:
on a real build, the curated top-40 held back 90%+ of real,
multi-channel-confirmed clusters. Filtering/ranking from the curated
subset here would silently throw most of the candidate pool away
before the percentile filter even runs.

This module runs no API calls. If a niche@window has no breakout
export yet, it's reported as missing and skipped — run option 6 on it
first.
"""

import math
import os
import json
from datetime import datetime, timezone

import pandas as pd

from config import EXPORTS_DIR
from paths import export_file, split_window, safe_name


# How many rows the pooled ranking keeps. Bumped from 50 to 60 because
# the "get a topic" flow pools 6 files (3 niches × 2 windows) rather
# than the 2-3 the manual workflow used, and a flat 50 left too little
# room per file when competing against more sources.
DIGEST_TOP_N = 60

# Percentile used to derive the per-file "too thin to trust" cutoff.
DIGEST_FILTER_PERCENTILE = 0.75

# Minimum guaranteed anchor-window clusters in the final top-N.
# The prompt rule "every topic must include a cluster from the anchor
# window" is unsatisfiable when the ranking pushes the anchor below
# this count. 15 is enough for 30-50 titles without repeating the
# same anchor cluster in every one.
ANCHOR_FLOOR = 15

# Attention score weights. The blend is:
#   (1-W) * cluster_score + W * accessibility
# where accessibility = 100 * sqrt(max(small_creator_share, SHARE_FLOOR)).
# W=0.30 gives accessibility real weight without letting it override a
# strong cluster_score signal.
ATTENTION_ACCESSIBILITY_WEIGHT = 0.30

# Floor on small_creator_share BEFORE sqrt, so a cluster where only big
# creators have succeeded (share = 0) doesn't score zero — that gap
# might be the opportunity.
ATTENTION_SHARE_FLOOR = 0.05


def _load_niche_clusters(key):
    """
    Load the FULL (untruncated) cluster list for one built niche@window.

    Returns (clusters, video_count), or (None, None) if no breakout
    export exists yet for this key — the caller reports these as
    missing rather than failing the whole digest.
    """
    json_path = export_file(EXPORTS_DIR, key, "breakouts", "breakout.json")
    if not os.path.exists(json_path):
        return None, None
    with open(json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("clusters", []), payload.get("video_count", 0)


def _percentile_filter(clusters, pct=DIGEST_FILTER_PERCENTILE):
    """
    Derive the "too thin to trust" cutoff from THIS FILE's own cluster
    'videos' distribution, and drop anything below it.

    WHY PER-FILE, NOT ONE CUTOFF ACROSS EVERYTHING COMBINED.
    A 30-day collection window structurally has fewer videos to spread
    across clusters than a 365-day window of the same niche — that's a
    fact about window size, not about which topics are actually thin.
    A single percentile taken across all files combined would cull the
    30d/90d files far harder than the 365d file for exactly that reason,
    and mislabel real short-window signal as noise. Computing the
    cutoff separately per file means every window is judged against its
    own peers, not against a longer window's larger raw counts.

    Returns (cutoff, survivors).
    """
    if not clusters:
        return 0.0, []
    counts = pd.Series([c.get("videos", 0) for c in clusters])
    cutoff = float(counts.quantile(pct))
    survivors = [c for c in clusters if c.get("videos", 0) >= cutoff]
    return cutoff, survivors


def _attention_score(cluster):
    """
    Score a cluster for the digest ranking.

    Previous formula: avg_breakout × small_creator_share.
    Three failure modes that formula had:
      1. Zero-out: share=0 → score=0, so a cluster where only big
         creators have succeeded vanishes — that gap might be the
         opportunity.
      2. Breakout suppression: a weak cluster (breakout 35, share 0.70
         → 24.5) beats an explosive one (breakout 92, share 0.12 →
         11.0). Accessibility vetoed strong signals.
      3. No channel diversity: two clusters with identical breakout
         and share scored the same regardless of channel count.
         cluster_score already blends in a channel-evidence term,
         but the old formula went back to raw avg_breakout and
         discarded it.

    New formula:
        accessibility = 100 * sqrt(max(share, FLOOR))
        attention = (1-W) * cluster_score + W * accessibility

    cluster_score carries the channel-evidence term through.
    sqrt on share gives diminishing returns — going from 0 to 0.30
    matters more than 0.50 to 0.90. The floor prevents zero-out.
    """
    share = cluster.get("small_creator_share") or 0.0
    access = 100.0 * math.sqrt(max(share, ATTENTION_SHARE_FLOOR))
    cs = cluster.get("cluster_score") or cluster.get("avg_breakout", 0.0)
    W = ATTENTION_ACCESSIBILITY_WEIGHT
    return round((1 - W) * cs + W * access, 2)


def build_digest(anchor_key, other_keys, top_n=DIGEST_TOP_N,
                 pct=DIGEST_FILTER_PERCENTILE,
                 anchor_floor=ANCHOR_FLOOR):
    """
    Merge breakout clusters from an anchor niche@window plus any number
    of crossover niche@window builds into one filtered, ranked pool.

    ANCHOR FLOOR
    -------------
    The prompt rule "every generated topic must include at least one
    cluster from the anchor window" is unsatisfiable when pure
    attention-score ranking leaves fewer than anchor_floor anchor
    clusters in the top-N. This reserves that many slots: the best
    anchor_floor anchor-window survivors are guaranteed a spot, then
    the remaining (top_n - anchor_floor) slots are filled by attention
    score across everyone. When the anchor naturally wins more than
    anchor_floor slots, the floor has no effect.

    Returns:
        {
          "anchor": anchor_key,
          "keys": [anchor_key, *other_keys] (deduped, anchor first),
          "filter_stats": {key: {"total", "cutoff", "survivors"}},
          "missing": [keys with no breakout export found],
          "pool_size": int,           total survivors pooled, pre-truncation
          "top": [ranked cluster dicts, tagged with niche_key/is_anchor],
          "split_counts": {key: count in top}, ordered by count desc,
          "anchor_floor_used": bool,  True if the floor forced extra anchors in
        }
    """
    keys = [anchor_key] + [k for k in other_keys if k != anchor_key]
    filter_stats = {}
    missing = []
    pool = []

    for key in keys:
        clusters, _video_count = _load_niche_clusters(key)
        if clusters is None:
            missing.append(key)
            continue
        cutoff, survivors = _percentile_filter(clusters, pct=pct)
        filter_stats[key] = {"total": len(clusters), "cutoff": round(cutoff, 1),
                             "survivors": len(survivors)}
        for c in survivors:
            tagged = dict(c)
            tagged["niche_key"] = key
            tagged["is_anchor"] = (key == anchor_key)
            tagged["attention_score"] = _attention_score(tagged)
            pool.append(tagged)

    pool.sort(key=lambda c: c["attention_score"], reverse=True)

    # --- anchor floor: guarantee anchor_floor slots for anchor clusters ---
    anchor_pool = [c for c in pool if c["is_anchor"]]
    other_pool = [c for c in pool if not c["is_anchor"]]

    # How many anchor clusters would make it in on pure merit?
    pure_top = pool[:top_n]
    anchor_in_pure = sum(1 for c in pure_top if c["is_anchor"])
    anchor_floor_used = anchor_in_pure < anchor_floor and len(anchor_pool) > 0

    if anchor_floor_used:
        # Reserve the best anchor_floor anchor clusters, fill the rest
        # from the combined pool (which can include MORE anchor clusters
        # beyond the floor if they rank high enough).
        reserved = anchor_pool[:anchor_floor]
        reserved_set = {id(c) for c in reserved}
        remaining = [c for c in pool if id(c) not in reserved_set]
        remaining.sort(key=lambda c: c["attention_score"], reverse=True)
        top = reserved + remaining[:max(0, top_n - len(reserved))]
        # Re-sort the final list by attention score for a clean ranking
        top.sort(key=lambda c: c["attention_score"], reverse=True)
    else:
        top = pool[:top_n]

    split_counts = {}
    for c in top:
        split_counts[c["niche_key"]] = split_counts.get(c["niche_key"], 0) + 1
    split_counts = dict(sorted(split_counts.items(), key=lambda kv: -kv[1]))

    return {
        "anchor": anchor_key,
        "keys": keys,
        "filter_stats": filter_stats,
        "missing": missing,
        "pool_size": len(pool),
        "top": top,
        "split_counts": split_counts,
        "anchor_floor_used": anchor_floor_used,
    }


# ============================================================
# EXPORT
# ============================================================

def export_digest(digest, exports_root=None):
    """
    Write the digest as a CSV (the ranked pool, machine-readable) and
    an MD (the ranked table plus the paste-ready AI prompt in one
    file — open it, select all, paste into Claude/ChatGPT).

    Filenames carry every niche AND every window involved (each capped,
    "+N more" beyond three) so two digests never silently collide in
    the same folder — not just two different niche sets (same
    reasoning as verdict.export_verdict), but also two digests over the
    SAME niches at different windows. Niche name alone isn't enough:
    a trending digest (30d/90d) and an evergreen digest (durable
    365d-derived clusters) over the identical two niches produced the
    identical stem before this, and the second export silently
    overwrote the first — caught by running the full workflow
    end-to-end before shipping it.
    """
    root = exports_root or EXPORTS_DIR
    out_dir = os.path.join(root, "_digest")
    os.makedirs(out_dir, exist_ok=True)

    bases, windows = [], []
    for k in digest["keys"]:
        b, w = split_window(k)
        sb = safe_name(b)
        if sb not in bases:
            bases.append(sb)
        sw = safe_name(w) if w else "nowindow"
        if sw not in windows:
            windows.append(sw)
    niche_stem = "_".join(bases[:3])
    if len(bases) > 3:
        niche_stem += f"_and_{len(bases) - 3}_more"
    window_stem = "_".join(sorted(windows)[:3])
    if len(windows) > 3:
        window_stem += f"_and_{len(windows) - 3}_more"
    stem = f"{niche_stem}_{window_stem}"

    csv_path = os.path.join(out_dir, f"{stem}_digest.csv")
    md_path = os.path.join(out_dir, f"{stem}_digest_prompt.md")

    rows = []
    for i, c in enumerate(digest["top"], 1):
        base, win = split_window(c["niche_key"])
        rows.append({
            "rank": i,
            "topic": c["topic"],
            "niche": base,
            "window": win or "—",
            "attention_score": c["attention_score"],
            "avg_breakout": c["avg_breakout"],
            "small_creator_share": c.get("small_creator_share"),
            "videos": c["videos"],
            "unique_channels": c["unique_channels"],
            "is_anchor": c["is_anchor"],
        })
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_prompt_md(digest))

    return {"csv": csv_path, "md": md_path}


def _render_prompt_md(digest) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    anchor_base, _ = split_window(digest["anchor"])
    # Compare BASE names, not exact keys — the anchor niche can appear
    # at more than one window (finance@30d is the anchor, finance@90d
    # is also in the pool), and that second finance window is not a
    # "crossover niche"; it's the same anchor niche at a different
    # window. Comparing only digest["anchor"] (one exact key) let that
    # second window's base slip into the crossover list as a duplicate
    # of the anchor's own name.
    crossover_bases = sorted({split_window(k)[0] for k in digest["keys"]
                              if split_window(k)[0] != anchor_base})

    L = [f"# Cross-niche cluster digest — anchor: {anchor_base}", ""]
    L.append(f"Generated {ts}. Anchor niche: **{anchor_base}**. "
             f"Crossover niche(s): "
             f"{', '.join(crossover_bases) if crossover_bases else '(none)'}.")
    L.append("")

    if digest["missing"]:
        L.append(f"> ⚠️ No breakout export found for: "
                 f"{', '.join(digest['missing'])} — run option 6 "
                 f"(discover breakout opportunities) on these first. "
                 f"This digest continues without them.")
        L.append("")

    # --- Step 1: the filter, shown per file ---
    L.append("## Step 1 — filter (derived per niche/window, not globally)")
    L.append("")
    L.append("| niche | window | clusters | videos-cutoff (75th pct) | survivors |")
    L.append("|---|---|---|---|---|")
    for key, s in digest["filter_stats"].items():
        base, win = split_window(key)
        L.append(f"| {base} | {win or '—'} | {s['total']} | "
                 f"{s['cutoff']:.1f} | {s['survivors']} |")
    L.append("")
    L.append("Each cutoff is the 75th percentile of that file's OWN cluster "
             "`videos` count. A 30d build is judged against its own "
             "distribution, never against a 365d build's larger cluster "
             "sizes — see digest.py for why a single combined cutoff "
             "would over-filter the shorter windows.")
    L.append("")

    # --- Step 2: pooled ranking ---
    L.append(f"## Step 2 — pooled ranking (top {len(digest['top'])} of "
             f"{digest['pool_size']} survivors)")
    L.append("")
    if digest["split_counts"]:
        split_line = ", ".join(f"{v} from {k}"
                               for k, v in digest["split_counts"].items())
        L.append(f"Split across this table: {split_line}.")
        L.append("")
        L.append("> A longer collection window structurally produces more "
                 "surviving clusters than a shorter one even after a fair "
                 "per-file filter, so an imbalance above isn't automatically "
                 "a mistake — it isn't corrected for here on purpose. Check "
                 "it against what you'd expect before treating the ranking "
                 "as final.")
        L.append("")
    L.append("⚓ = from the anchor niche.")
    L.append("")
    L.append("| rank | topic | niche | attention | breakout | small% | videos |")
    L.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(digest["top"], 1):
        base, win = split_window(c["niche_key"])
        label = f"{base}@{win}" if win else base
        share = c.get("small_creator_share")
        share_s = f"{share:.0%}" if share is not None else "?"
        mark = " ⚓" if c["is_anchor"] else ""
        topic = str(c["topic"]).replace("|", "\\|")
        L.append(f"| {i} | {topic}{mark} | {label} | "
                 f"{c['attention_score']} | {c['avg_breakout']} | "
                 f"{share_s} | {c['videos']} |")
    L.append("")

    # --- Step 3: the AI prompt, using the table above as its data ---
    L.append("---")
    L.append("")
    L.append("## Prompt (paste everything below into Claude or ChatGPT, "
             "including the table above it)")
    L.append("")
    L.append(f"I run a faceless YouTube channel anchored in "
             f"**{anchor_base}**. Above is a pre-filtered, pre-ranked "
             f"table of breakout topic clusters pooled from my anchor "
             f"niche" +
             (f" and my crossover niche(s) "
              f"({', '.join(crossover_bases)})" if crossover_bases else "") +
             ". The filtering and ranking are already done — do not "
             "re-filter or re-rank the table; use it as given.")
    L.append("")
    # Anchor window label for the combination rule
    _, anchor_win = split_window(digest["anchor"])
    anchor_label = f"{anchor_base}@{anchor_win}" if anchor_win else anchor_base

    L.append("Using the table above, do the following:")
    L.append("")
    L.append("1. Build 30-50 video titles using cluster words drawn from "
             "across ALL niches in the table combined — not only the "
             "anchor niche's own clusters.")
    L.append(f"2. Every title must be anchored to **{anchor_base}** — it "
             "has to make sense as content for that channel, even when "
             "the vocabulary comes from a crossover niche's cluster.")
    L.append(f"3. **COMBINATION RULE:** every title must combine words "
             f"from AT LEAST TWO different clusters in the table, and "
             f"at least one of those clusters must be from the anchor "
             f"window (**{anchor_label}**, marked ⚓ in the table). "
             f"A title built from only one cluster, or from zero anchor "
             f"clusters, is not valid.")
    L.append("4. Every cluster in the top 30 rows must appear in at least "
             "one title.")
    L.append("5. The strongest clusters (top 15 rows) must appear in "
             "multiple titles.")
    L.append("6. For each title, note which cluster(s) and which niche "
             "the words were drawn from, so it's traceable back to the "
             "table.")
    L.append("")

    return "\n".join(L) + "\n"
