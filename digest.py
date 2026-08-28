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

import os
import json
from datetime import datetime, timezone

import pandas as pd

from config import EXPORTS_DIR
from paths import export_file, split_window, safe_name


# How many rows the pooled ranking keeps. 50 matches the manual
# workflow this replaces; the AI prompt asks for 30-50 titles from it.
DIGEST_TOP_N = 50

# Percentile used to derive the per-file "too thin to trust" cutoff.
DIGEST_FILTER_PERCENTILE = 0.75


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


def build_digest(anchor_key, other_keys, top_n=DIGEST_TOP_N,
                 pct=DIGEST_FILTER_PERCENTILE):
    """
    Merge breakout clusters from an anchor niche@window plus any number
    of crossover niche@window builds into one filtered, ranked pool.

    Deliberately does NOT constrain how many of the top_n slots any one
    niche/window can win. A window with a longer collection span will
    structurally produce more surviving clusters than a shorter one,
    even after a fair per-file filter — that's a real property of the
    data, not a bug in the ranking. Rather than silently correcting for
    it (which would hide whether it's actually happening), every digest
    reports the split explicitly via 'split_counts' so it's visible and
    judgeable on each run.

    Returns:
        {
          "anchor": anchor_key,
          "keys": [anchor_key, *other_keys] (deduped, anchor first),
          "filter_stats": {key: {"total", "cutoff", "survivors"}},
          "missing": [keys with no breakout export found],
          "pool_size": int,           total survivors pooled, pre-truncation
          "top": [ranked cluster dicts, tagged with niche_key/is_anchor],
          "split_counts": {key: count in top}, ordered by count desc,
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
            share = c.get("small_creator_share") or 0.0
            tagged = dict(c)
            tagged["niche_key"] = key
            tagged["is_anchor"] = (key == anchor_key)
            # The attention score from the manual workflow:
            # avg_breakout x small_creator_share. Both are already
            # 0-100 / 0-1 scale respectively, so this stays 0-100ish
            # and comparable across files without extra normalization.
            tagged["attention_score"] = round(
                tagged.get("avg_breakout", 0.0) * share, 2)
            pool.append(tagged)

    pool.sort(key=lambda c: c["attention_score"], reverse=True)
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
    }


# ============================================================
# EXPORT
# ============================================================

def export_digest(digest, exports_root=None):
    """
    Write the digest as a CSV (the ranked pool, machine-readable) and
    an MD (the ranked table plus the paste-ready AI prompt in one
    file — open it, select all, paste into Claude/ChatGPT).

    Filenames carry every niche involved (capped, "+N more" beyond
    three) so two digests over different niche sets never collide in
    the same folder — same reasoning as verdict.export_verdict.
    """
    root = exports_root or EXPORTS_DIR
    out_dir = os.path.join(root, "_digest")
    os.makedirs(out_dir, exist_ok=True)

    bases = []
    for k in digest["keys"]:
        b, _ = split_window(k)
        sb = safe_name(b)
        if sb not in bases:
            bases.append(sb)
    stem = "_".join(bases[:3])
    if len(bases) > 3:
        stem += f"_and_{len(bases) - 3}_more"

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
    L.append("Using the table above, do the following:")
    L.append("")
    L.append("1. Build 30-50 video titles using cluster words drawn from "
             "across ALL niches in the table combined — not only the "
             "anchor niche's own clusters.")
    L.append(f"2. Every title must be anchored to **{anchor_base}** — it "
             "has to make sense as content for that channel, even when "
             "the vocabulary comes from a crossover niche's cluster.")
    L.append("3. Every cluster in the top 30 rows must appear in at least "
             "one title.")
    L.append("4. The strongest clusters (top 15 rows) must appear in "
             "multiple titles.")
    L.append("5. For each title, note which cluster(s) and which niche "
             "the words were drawn from, so it's traceable back to the "
             "table.")
    L.append("")

    return "\n".join(L) + "\n"
