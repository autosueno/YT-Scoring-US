"""
discover.py

Opportunity discovery: score every collected video, CLUSTER the videos into
recurring topics, and export both rankings for optional manual AI analysis.

Philosophy: math discovers, AI explains. Everything here is deterministic.
No LLM is called by the engine.

--- Why clusters, not just a video ranking -------------------------------
A single video scoring 98 is one lucky performer. Seven videos scoring ~92
across five DIFFERENT channels is a market signal: repeated, independent
success is much stronger evidence that the audience wants a topic than one
exceptional outlier. Ranking videos individually buries that pattern and
pushes the most valuable inference onto the human (or the AI). So this
module finds the clusters itself, then ranks the clusters.

--- Breakout score (0-100, deterministic, niche-relative) ----------------
Blends three views of "this outperformed", so it isn't purely
channel-relative:
    outlier_ratio percentile      did it beat its OWN channel's norm?
    breakout_velocity percentile  how FAST did that overperformance build?
    views_per_hour percentile     absolute market DEMAND (a trend-starting
                                  video on a big channel has outlier ~1 but
                                  huge demand — channel-relative signals
                                  alone are blind to it)
Weights: config.BREAKOUT_VELOCITY_WEIGHT, config.BREAKOUT_DEMAND_WEIGHT.

--- Cluster score --------------------------------------------------------
    cluster_score = (1-w) * avg_breakout + w * evidence
    evidence      = 100 * log2(channels+1) / log2(REFERENCE+1)
Independent repetition is rewarded (log-scaled so a single prolific channel
can't manufacture a cluster), and CLUSTER_MIN_CHANNELS enforces that a
cluster must span multiple creators.

Public API:
    score_breakouts(df, baseline)          -> df + 'breakout_score', sorted
    build_clusters(scored_df)              -> list[dict] ranked clusters
    export_breakouts(df, baseline, niche)  -> {json, csv, md, clusters_csv}
"""

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

from baseline import Baseline
from config import (
    EXPORTS_DIR, EXPORT_COLUMNS,
    BREAKOUT_VELOCITY_WEIGHT, BREAKOUT_DEMAND_WEIGHT,
    BREAKOUT_OUTLIER_WEIGHT, BREAKOUT_LOW_EVIDENCE_VIEWS,
    BREAKOUT_ERAS, ERA_MIN_VIDEOS,
    CLUSTER_MIN_VIDEOS, CLUSTER_MIN_CHANNELS, CLUSTER_NGRAM_SIZES,
    CLUSTER_MAX, CLUSTER_EVIDENCE_WEIGHT, CLUSTER_EVIDENCE_REFERENCE,
    CLUSTER_WEAK_WORDS, DISCOVERY_EXCLUDE_SHORTS,
    BREAKOUT_TIERS, BREAKOUT_TIER_MIN_VIDEOS, BREAKOUT_MIN_VIEWS_LADDER,
)
from insights import _STOPWORDS, _tokenize
from paths import export_dir_for
from paths import named as _named


# ============================================================
# SCORING
# ============================================================

def score_breakouts_within(df: pd.DataFrame) -> pd.DataFrame:
    """
    Score breakouts against THIS SUBSET rather than the whole niche.

    Used for era ranking. The stored niche baseline mixes every age
    together, so scoring a 200-day-old video against it means competing
    with 3-day-old videos whose views_per_hour divides by a hundredth of
    the hours. Ranking within an era removes that advantage: everyone in
    the bucket is roughly the same age, so the comparison is about the
    video rather than about the calendar.

    Percentile rank is computed directly here instead of going through
    baseline.percentile_of, because the reference population IS the
    subset.
    """
    if df is None or df.empty or "outlier_ratio" not in df.columns:
        out = (df.copy() if df is not None else pd.DataFrame())
        out["breakout_score"] = pd.Series(dtype=float)
        return out

    out = df.copy()
    parts, weights = [], []
    for col, w in (("views_per_hour", BREAKOUT_DEMAND_WEIGHT),
                   ("outlier_ratio", BREAKOUT_OUTLIER_WEIGHT),
                   ("breakout_velocity", BREAKOUT_VELOCITY_WEIGHT)):
        if col in out.columns and w > 0:
            v = pd.to_numeric(out[col], errors="coerce")
            # pct=True gives the rank as a 0-1 fraction of the subset.
            parts.append(v.rank(pct=True) * 100.0)
            weights.append(w)
    if not parts:
        out["breakout_score"] = 50.0
    else:
        total = sum(weights)
        score = sum(p * w for p, w in zip(parts, weights)) / total
        out["breakout_score"] = score.round(1)

    if "view_count" in out.columns:
        vc = pd.to_numeric(out["view_count"], errors="coerce")
        out["low_evidence"] = (vc < BREAKOUT_LOW_EVIDENCE_VIEWS).fillna(True)
    return out.sort_values("breakout_score", ascending=False).reset_index(drop=True)


def split_eras(df: pd.DataFrame) -> dict:
    """
    Split a dataset into disjoint age eras and rank each one internally.

    Returns {era_key: {"label", "range", "videos", "scored", "clusters"}}
    for every era with enough videos to be worth ranking.
    """
    from insights import _days_since
    if df is None or df.empty:
        return {}
    days = _days_since(df)
    eras = {}
    for lo, hi, key, label in BREAKOUT_ERAS:
        mask = ((days >= lo) & (days < hi)).values
        sub = df[mask]
        if len(sub) < ERA_MIN_VIDEOS:
            eras[key] = {"label": label, "range": (lo, hi),
                         "videos": int(len(sub)), "skipped": True,
                         "scored": None, "clusters": []}
            continue
        scored = score_breakouts_within(sub)
        eras[key] = {
            "label": label,
            "range": (lo, hi),
            "videos": int(len(sub)),
            "skipped": False,
            "scored": scored,
            "clusters": build_clusters(scored, max_clusters=CLUSTER_MAX),  # curated: per-era markdown reading list
        }
    return eras


def score_breakouts(df: pd.DataFrame, baseline: Baseline) -> pd.DataFrame:
    """
    Add a 'breakout_score' column (0-100) and return the DataFrame sorted by
    it, descending. Pure and deterministic; does not modify the input.
    """
    if df.empty or "outlier_ratio" not in df.columns:
        out = df.copy()
        out["breakout_score"] = pd.Series(dtype=float)
        return out

    out = df.copy()

    def pct_series(col):
        if col not in out.columns or not baseline.has_metric(col):
            return None
        return out[col].apply(
            lambda v: baseline.percentile_of(col, float(v)) if pd.notna(v) else 0.0
        )

    outlier_pct = pct_series("outlier_ratio")
    velocity_pct = pct_series("breakout_velocity")
    demand_pct = pct_series("views_per_hour")

    # Renormalize weights over whatever components are actually available,
    # so a dataset missing one column still produces a sane 0-100 score
    # rather than a silently deflated one.
    parts, weights = [], []
    for series, w in ((demand_pct, BREAKOUT_DEMAND_WEIGHT),
                      (outlier_pct, BREAKOUT_OUTLIER_WEIGHT),
                      (velocity_pct, BREAKOUT_VELOCITY_WEIGHT)):
        if series is not None and w > 0:
            parts.append(series)
            weights.append(w)

    if not parts:
        out["breakout_score"] = 0.0
    else:
        total_w = sum(weights)
        blended = sum(s * (w / total_w) for s, w in zip(parts, weights))
        out["breakout_score"] = blended

    out["breakout_score"] = out["breakout_score"].round(1)

    # Mark thin-evidence entries rather than removing them. A high score
    # built on 1,700 total views is a real early signal AND a fragile
    # one; the reader needs to see both facts, not have the row silently
    # dropped or silently trusted.
    if "view_count" in out.columns:
        vc = pd.to_numeric(out["view_count"], errors="coerce")
        out["low_evidence"] = (vc < BREAKOUT_LOW_EVIDENCE_VIEWS).fillna(True)

    return out.sort_values("breakout_score", ascending=False).reset_index(drop=True)


# ============================================================
# CLUSTERING (deterministic, phrase-based)
# ============================================================

def _is_historical_year(token, cutoff_years_back=10):
    """
    True for a 4-digit token that reads as a genuine historical date
    rather than a current-year freshness suffix.

    FOUND BY TESTING, NOT ASSUMED. Blanket-excluding every digit token
    meant "june 1944", "day 1944", "normandy 1944" could never become
    cluster candidates in a world_war_two build, however many videos or
    channels used them — the year IS the content there, not filler. On
    real data: 53 videos ("day explained"), 20 ("june 1944"), 14 ("day
    1944") sat with under 25% overlap against every real cluster in the
    niche, because the specific date that identifies the event (D-Day,
    June 6 1944) was structurally invisible to clustering.

    The blanket exclusion existed for a real reason too, not by
    accident: "excel tutorial 2026", "review 2026" are not themes, they
    are a title-freshness convention that would otherwise fragment
    across every topic in every niche. So this does not simply allow
    all digits — it allows a 4-digit token only when the year is more
    than cutoff_years_back in the past, which lets through historical
    dates (1944, 1969, 1865) while still excluding anything that could
    plausibly be a current-form freshness tag (2020-2026 as of this
    writing). Tested against the exact real cases this was built to fix
    and the exact real cases it must continue to exclude.
    """
    if not (token.isdigit() and len(token) == 4):
        return False
    year = int(token)
    if year < 1000 or year > 2099:
        return False
    from datetime import datetime
    return (datetime.now().year - year) >= cutoff_years_back


def _candidate_phrases(title):
    """
    Stopword-filtered n-gram phrases used as cluster seeds.

    A phrase must contain at least one CONTENT word (not in
    CLUSTER_WEAK_WORDS). All-weak phrases like "right now", "should buy",
    "latest news", "year old" are conversational filler, not topics — on
    real data they formed large, useless clusters, while every genuine
    topic cluster ("chip stocks", "gold silver") contained a content word.

    Digit tokens are excluded UNLESS they read as a historical year — see
    _is_historical_year for why and what real data justified the change.
    """
    tokens = [t for t in _tokenize(title)
              if t not in _STOPWORDS and len(t) > 2
              and (not t.isdigit() or _is_historical_year(t))]
    phrases = set()
    for n in CLUSTER_NGRAM_SIZES:
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if all(w in CLUSTER_WEAK_WORDS for w in gram):
                continue
            phrases.add(" ".join(gram))
    return phrases


def build_clusters(scored_df: pd.DataFrame, max_clusters=None) -> list:
    """
    Group scored videos into recurring-topic clusters by shared title
    phrases, then rank clusters by evidence-weighted breakout strength.

    A cluster must contain >= CLUSTER_MIN_VIDEOS videos from
    >= CLUSTER_MIN_CHANNELS distinct channels — this is what separates a
    real market signal from one channel repeating itself.

    max_clusters: truncate the returned list to this many, best-first.
    Defaults to None — return every cluster that survives deduplication,
    not CLUSTER_MAX.

    THIS DEFAULT CHANGED, AND HERE IS THE BUG IT FIXES.
    -----------------------------------------------------
    This used to hard-truncate to CLUSTER_MAX (40) internally, and every
    caller — including coverage.topic_coverage(), which measures what
    share of a niche's videos matched no cluster — inherited that
    truncated list with no way to ask for the full one.

    Checked on a real 2,903-video build (ancient_history): 488 clusters
    actually survive deduplication, comfortably clearing the same
    CLUSTER_MIN_VIDEOS/CLUSTER_MIN_CHANNELS bar as the top 40. Measuring
    coverage against only the top 40 reported 92.6% unmatched. Measuring
    it against all 488 gives 10.8%. The true, defensible number is 10.8%
    — the other 448 clusters were real, multi-channel-confirmed topics
    that simply never got returned to anything downstream. A warning
    built on the truncated number was overstating the problem by roughly
    8-9x, on this niche.

    Callers that want a short, curated list for a human or an AI to read
    (a markdown "ideas to paste in" file, an SEO angle summary) should
    pass max_clusters=CLUSTER_MAX explicitly — that curation is a real
    and legitimate UX need. But nothing should inherit that truncation
    silently, especially not a statistical measurement.

    ON WHY max_clusters ISN'T PERCENTILE-BASED EITHER
    ----------------------------------------------------
    Checked whether a percentage of survivors would be more principled
    than a fixed count: on real data the cluster_score distribution
    declines smoothly from rank 10 to rank 200 with no natural gap or
    elbow anywhere (81.1 -> 78.1 -> 76.3 -> ... -> 59.3). There is no
    statistically defensible stopping point in that curve — a percentile
    cutoff would be exactly as arbitrary as a fixed count, just a
    different arbitrary number. So max_clusters stays an explicit,
    named UX choice for callers that need a short list, not something
    dressed up as more principled than it is.
    """
    if scored_df.empty or "title" not in scored_df.columns:
        return []

    # phrase -> row indices
    buckets = defaultdict(set)
    for idx, title in scored_df["title"].fillna("").items():
        for phrase in _candidate_phrases(title):
            buckets[phrase].add(idx)

    raw = []
    for phrase, idxs in buckets.items():
        if len(idxs) < CLUSTER_MIN_VIDEOS:
            continue
        sub = scored_df.loc[sorted(idxs)]
        channels = sub["channel_id"].nunique() if "channel_id" in sub.columns else 1
        if channels < CLUSTER_MIN_CHANNELS:
            continue
        raw.append((phrase, set(idxs), sub, channels))

    # De-duplicate overlapping clusters. Bigger evidence wins (more videos
    # processed first); phrase length breaks ties in favour of specificity.
    # Containment is measured against the SMALLER of the two sets — the
    # earlier version measured against the candidate only, which let
    # "stocks buy" (26 vids) and "stocks buy now" (15 vids, fully contained
    # in the former) BOTH survive as separate clusters on real data.
    raw.sort(key=lambda r: (-len(r[1]), -len(r[0].split())))
    kept = []
    for phrase, idxs, sub, channels in raw:
        subsumed = False
        for k in kept:
            containment = (len(idxs & k["_idxs"])
                           / max(1, min(len(idxs), len(k["_idxs"]))))
            if containment >= 0.8:
                subsumed = True
                break
        if not subsumed:
            kept.append({"phrase": phrase, "_idxs": idxs, "_sub": sub,
                         "channels": channels})

    clusters = []
    for k in kept:
        sub, phrase = k["_sub"], k["phrase"]
        avg_breakout = float(sub["breakout_score"].mean())
        channels = int(k["channels"])

        # Evidence term: independent repetition matters, log-scaled and
        # normalized against a reference channel count so it saturates
        # gracefully at 100 instead of blowing past it.
        #
        # NOTE: this is a WEIGHTED BLEND, not a multiplier. An earlier
        # version used avg_breakout * (1 + w*log2(channels)) clamped to
        # 100 — which pushed almost every cluster past the ceiling and
        # collapsed the ranking (three clusters with avg_breakout 73, 90
        # and 97 all scored exactly 100.0). Blending keeps the score on a
        # real 0-100 scale and preserves ordering.
        evidence = min(100.0, 100.0 * math.log2(max(channels, 1) + 1)
                       / math.log2(CLUSTER_EVIDENCE_REFERENCE + 1))
        score = (1 - CLUSTER_EVIDENCE_WEIGHT) * avg_breakout \
            + CLUSTER_EVIDENCE_WEIGHT * evidence

        clusters.append({
            "topic": phrase,
            "cluster_score": round(score, 1),
            "avg_breakout": round(avg_breakout, 1),
            "videos": int(len(sub)),
            "unique_channels": channels,
            "avg_views_per_hour": round(float(sub["views_per_hour"].mean()), 1)
                if "views_per_hour" in sub.columns else None,
            "avg_outlier_ratio": round(float(sub["outlier_ratio"].mean()), 2)
                if "outlier_ratio" in sub.columns else None,
            "small_creator_share": round(float(sub["small_creator"].mean()), 3)
                if "small_creator" in sub.columns else None,
            "top_titles": sub.nlargest(3, "breakout_score")["title"].tolist(),
            # Exact row indices of this cluster's videos. Downstream
            # consumers (seo.cluster_seo_profiles) previously re-found
            # these by fuzzy regex on the title, which silently failed
            # whenever the phrase words were separated by more characters
            # than the pattern allowed — a cluster could end up with no
            # profile at all. Carrying the indices removes the guesswork.
            "video_indices": sorted(int(i) for i in k["_idxs"]),
        })

    clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters[:max_clusters] if max_clusters is not None else clusters


# ============================================================
# EXPORT
# ============================================================

def attach_search_demand(clusters, niche_name):
    """
    Annotate clusters with search volume from a previous Option 7 run.

    A SEPARATE column, never folded into cluster_score. Two reasons:

    1. The match is rough. Cluster topics are title fragments with
       stopwords stripped ("stocks buy" is what remains of "...Stocks to
       Buy Now..."), while autocomplete returns whole search phrases
       ("memory stocks to buy"). Nobody types "stocks buy". Lining the
       two up is good enough to raise a flag, not to move a number.
    2. Folding it in would make cluster_score depend on whether the user
       happened to run Option 7 — the same dataset producing different
       scores, with no visible cause.

    Returns the clusters unchanged when no intent file exists.
    """
    import csv as _csv
    from paths import search_export_dir as _sed
    path = os.path.join(_sed(EXPORTS_DIR, niche_name), "intent_gaps.csv")
    if not os.path.exists(path):
        return clusters, False
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
    except Exception:
        return clusters, False
    if not rows:
        return clusters, False

    queries = []
    for r in rows:
        q = (r.get("query") or "").lower()
        try:
            w = float(r.get("autocomplete_weight") or 0)
        except ValueError:
            w = 0.0
        if q:
            queries.append((set(q.split()), w))

    for c in clusters:
        words = set(str(c.get("topic", "")).lower().split())
        if not words:
            continue
        # A query counts if it contains EVERY word of the cluster topic.
        matched = [w for qwords, w in queries if words <= qwords]
        c["search_matches"] = len(matched)
        c["search_weight"] = round(sum(matched), 1)
        c["search_level"] = ("high" if sum(matched) >= 20 else
                             "moderate" if sum(matched) >= 5 else
                             "low" if matched else "none found")
    return clusters, True


def export_eras(df: pd.DataFrame, niche_name: str) -> dict:
    """
    Write by_era.md and clusters_by_era.csv.

    Reading down the eras answers a question the single ranked list
    cannot: which themes are DURABLE and which were a moment. A cluster
    appearing in all three eras is a format; one appearing only in the
    most recent is probably a news cycle.
    """
    eras = split_eras(df)
    if not eras:
        return {}
    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "breakouts")
    md_path = os.path.join(out_dir, _named(niche_name, "by_era.md"))

    L = [f"# Breakouts by era — {niche_name}", ""]
    L.append("Videos ranked against their own contemporaries rather than "
             "against the whole year.")
    L.append("")
    L.append("> **Why this exists.** `views_per_hour` divides by age, so a "
             "3-day-old video divides by 72 hours and a 300-day-old one by "
             "7,200. On real data that gave videos under 30 days a median "
             "breakout score of 89 and videos over 180 days a score of 26 — "
             "the main list was really \"recent videos\" with a "
             "\"breakouts\" label. Splitting into eras lets each period's "
             "actual winners surface.")
    L.append("")

    # --- theme durability, the payoff of having eras at all ---
    theme_eras = {}
    for key, e in eras.items():
        for c in e.get("clusters", []):
            theme_eras.setdefault(c["topic"], []).append(key)
    durable = {t: ks for t, ks in theme_eras.items() if len(ks) >= 2}
    if durable:
        L.append("## Durable themes")
        L.append("")
        L.append("Appearing in more than one era — a format rather than a "
                 "moment.")
        L.append("")
        L.append("| theme | eras |")
        L.append("|---|---|")
        for t, ks in sorted(durable.items(), key=lambda kv: -len(kv[1])):
            L.append(f"| {t} | {', '.join(ks)} |")
        L.append("")

    for key, e in eras.items():
        if e.get("skipped"):
            L.append(f"## {e['label']} ({key})")
            L.append("")
            L.append(f"Only {e['videos']} videos — too few to rank. A top-20 "
                     f"from a list this short is just the list, sorted.")
            L.append("")
            continue
        L.append(f"## {e['label']}  ·  {key}  ·  {e['videos']} videos")
        L.append("")
        s = e["scored"]
        L.append("| score | views | subs | title |")
        L.append("|---|---|---|---|")
        for r in s.head(15).itertuples():
            t = str(r.title)[:58].replace("|", "\\|")
            subs = int(r.channel_subs) if pd.notna(getattr(r, "channel_subs", None)) else 0
            L.append(f"| {r.breakout_score:.1f} | {int(r.view_count):,} | "
                     f"{subs:,} | {t} |")
        L.append("")
        if e["clusters"]:
            L.append("**Themes:** "
                     + ", ".join(f"`{c['topic']}`" for c in e["clusters"][:8]))
            L.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    rows = []
    for key, e in eras.items():
        for c in e.get("clusters", []):
            rows.append({"era": key, "era_label": e["label"],
                         "topic": c["topic"], "cluster_score": c["cluster_score"],
                         "videos": c["videos"],
                         "unique_channels": c["unique_channels"]})
    csv_path = os.path.join(out_dir, _named(niche_name, "clusters_by_era.csv"))
    if rows:
        pd.DataFrame(rows).to_csv(csv_path, index=False)

    return {"md": md_path, "csv": csv_path if rows else None,
            "eras": sum(1 for e in eras.values() if not e.get("skipped")),
            "durable_themes": len(durable)}


def export_breakouts(df: pd.DataFrame, baseline: Baseline, niche_name: str) -> dict:
    """
    Score, cluster, and write the FULL ranked video list plus the cluster
    ranking to exports/. Returns a dict of format -> path.
    """
    if DISCOVERY_EXCLUDE_SHORTS and "short_video" in df.columns:
        n_before = len(df)
        df = df[~df["short_video"].fillna(False)]
        print(f"  (excluding Shorts: {n_before - len(df)} removed, "
              f"{len(df)} long-form videos ranked)")

    ranked = score_breakouts(df, baseline)
    if ranked.empty:
        raise ValueError("No videos to export — dataset is empty.")

    # Full set, no truncation — this is what the coverage check and the
    # CSV/JSON exports use. Truncating before the coverage check was the
    # bug that made a niche's unmatched share look 8-9x worse than it
    # really was; see build_clusters' docstring for the measured proof.
    all_clusters = build_clusters(ranked, max_clusters=None)
    # Curated subset for the markdown reading list only — a person or an
    # AI reading 488 theme cards is not a realistic ask, and there is no
    # statistically principled place to cut a smoothly-declining score
    # curve, so this stays an explicit, named UX choice (see the
    # docstring) rather than something dressed up as more rigorous.
    clusters = all_clusters[:CLUSTER_MAX]

    # How much of this build actually shares vocabulary with itself,
    # measured against EVERY real cluster, not the markdown-curated
    # subset — a large unmatched share here means the collection is
    # probably broader than the niche (or, per coverage.py, that the
    # content is on-topic but independently worded). Warn, never filter.
    import coverage as _cov
    cov = _cov.topic_coverage(ranked, all_clusters)
    cov_note = _cov.coverage_note(cov)
    if cov_note:
        indented = cov_note.replace("\n", "\n     ")
        print(f"\n  ⚠️  {indented}")

    cols = [c for c in EXPORT_COLUMNS if c in ranked.columns]
    slim = ranked[cols].copy()
    slim.insert(0, "rank", range(1, len(slim) + 1))

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "breakouts")
    stem = os.path.join(out_dir, _named(niche_name, "breakout"))
    paths = {
        "json":         stem + ".json",
        "csv":          stem + ".csv",
        "md":           stem + ".md",
        "clusters_csv": stem + "_clusters.csv",
    }

    slim.to_csv(paths["csv"], index=False)
    # Full set, not the markdown-curated subset — a CSV is meant to be
    # filtered and sorted externally (Excel, a spreadsheet), so there is
    # no readability reason to silently drop 448 real clusters here the
    # way there is for the markdown reading list.
    if all_clusters:
        pd.DataFrame([{k: v for k, v in c.items() if k != "top_titles"}
                      for c in all_clusters]).to_csv(paths["clusters_csv"], index=False)
    else:
        paths.pop("clusters_csv")

    payload = {
        "niche": niche_name,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "video_count": len(slim),
        "cluster_count": len(all_clusters),
        "clusters_shown_in_markdown": len(clusters),
        "breakout_score_definition": (
            f"{max(0.0, 1-BREAKOUT_VELOCITY_WEIGHT-BREAKOUT_DEMAND_WEIGHT):.0%} outlier_ratio + "
            f"{BREAKOUT_VELOCITY_WEIGHT:.0%} breakout_velocity + "
            f"{BREAKOUT_DEMAND_WEIGHT:.0%} views_per_hour (all niche percentiles)"
        ),
        "cluster_score_definition": (
            f"{1-CLUSTER_EVIDENCE_WEIGHT:.0%} avg_breakout + "
            f"{CLUSTER_EVIDENCE_WEIGHT:.0%} evidence"
            f"(evidence = log2(channels+1)/log2({CLUSTER_EVIDENCE_REFERENCE}+1)) — "
            "rewards repeated INDEPENDENT success over single outliers"
        ),
        # Full set here too — the JSON is a data file, not a reading list.
        "clusters": all_clusters,
        "videos": json.loads(slim.to_json(orient="records")),
    }
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write(_markdown_report(slim, clusters, niche_name, cov_note))

    return paths


def _markdown_report(slim, clusters, niche_name, cov_note=None) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n = len(slim)

    out = [f"# {niche_name} — Opportunity Discovery\n",
           f"Generated {ts} · {n} collected videos · {len(clusters)} topic clusters\n"]

    if cov_note:
        quoted = "\n".join(f"> {line}" for line in cov_note.split("\n"))
        out.append(f"\n> ⚠️ **Topic coverage.**\n{quoted}\n")

    out.append(f"""
> **Analysis prompt — paste this file into Claude or ChatGPT:**
>
> Below are topic clusters the engine detected in {niche_name}, plus the
> full ranked video list. Clusters are already grouped and scored by the
> engine — repeated success across multiple independent channels.
> 1. For each top cluster, explain *why* it is trending right now.
> 2. Suggest adjacent video opportunities each cluster implies but that
>    do not yet appear in the data (the underserved follow-up questions).
> 3. Flag clusters where small creators are winning (high
>    small_creator_share) — those prove the topic carries the video.
> 4. Recommend additional seed search queries these clusters suggest.
> 5. Note any clusters that look like noise rather than a real theme.
> 6. This file answers WHAT to make. A companion file,
>    `<niche>_seo.md`, answers HOW to package it — it already computes
>    tag/title/hashtag lift, structural patterns and per-cluster examples,
>    so don't re-derive packaging advice by eyeballing the columns below.
>    Paste that file too if packaging matters for this question.
""")

    if clusters:
        clusters, has_search = attach_search_demand(clusters, niche_name)
        out.append("\n## Topic clusters (ranked by evidence-weighted breakout)\n")
        if has_search:
            out.append("> The **search** column comes from your last Option 7 "
                       "autocomplete run. It is reported SEPARATELY and does "
                       "not affect cluster_score — cluster topics are title "
                       "fragments (\"stocks buy\") while autocomplete returns "
                       "whole phrases (\"memory stocks to buy\"), so the match "
                       "is good enough to flag something, not to move a "
                       "number. A high-scoring cluster with **none found** is "
                       "worth a look.")
            out.append("")
        head = ("| # | topic | cluster_score | videos | channels | "
                "avg_breakout | avg_vph | small_creator_share |")
        rule = "|---|---|---|---|---|---|---|---|"
        if has_search:
            head = head[:-1] + " search |"
            rule = rule + "---|"
        out.append(head)
        out.append(rule)
        for i, c in enumerate(clusters, 1):
            scs = "" if c["small_creator_share"] is None else f"{c['small_creator_share']:.0%}"
            vph = "" if c["avg_views_per_hour"] is None else f"{c['avg_views_per_hour']:,.0f}"
            row = (f"| {i} | {c['topic']} | {c['cluster_score']} | {c['videos']} | "
                   f"{c['unique_channels']} | {c['avg_breakout']} | {vph} | {scs} |")
            if has_search:
                row += f" {c.get('search_level', '—')} |"
            out.append(row)
        out.append("\n### Example titles per cluster\n")
        for i, c in enumerate(clusters[:15], 1):
            out.append(f"**{i}. {c['topic']}**")
            for t in c["top_titles"]:
                out.append(f"  - {str(t)[:110]}")
            out.append("")

    # Tier-broken "top breakouts" summary before the full dump.
    #
    # The full ranked list below is comprehensive but noisy — 3,000 rows
    # is not a browsable format. Break the top of it into the three tiers
    # so a reader can see the actionable case studies immediately:
    #   brand-new (<2k)   -> direct case studies for someone starting out
    #   small (2k-100k)   -> peers with some traction
    #   top by reach      -> what wins in absolute terms (demand shape)
    # Same structure as report.py's HTML version, so the two feel consistent.
    if "channel_subs" in slim.columns:
        subs = pd.to_numeric(slim["channel_subs"], errors="coerce")
        tier_notes = {
            "brand_new":    ("Direct case studies for a new channel — these videos won "
                             "for channels with almost no subscribers, on topic/title/"
                             "thumbnail alone. Survivorship-biased (channels this "
                             "small that appear in search at all are already the "
                             "exception). Ranked by outlier_ratio."),
            "small":        ("Channels with some traction but no institutional "
                             "muscle. Ranked by outlier_ratio."),
            "top_by_reach": ("What is winning in absolute terms. Reveals what the "
                             "MARKET wants, independent of who can currently deliver "
                             "it. Ranked by views_per_hour."),
        }

        # A slim table for these summary sections — the full column list
        # of the ranked dump is too wide to scan at a glance.
        preview_cols = [c for c in ("title", "channel_title", "channel_subs",
                                    "view_count", "views_per_hour",
                                    "outlier_ratio", "engagement_rate")
                        if c in slim.columns]

        out.append("\n## Top breakouts by channel size\n")

        for lo, hi, key, heading in BREAKOUT_TIERS:
            mask = (subs >= lo) & (subs < hi) if hi is not None else (subs >= lo)
            tier_df = slim[mask.fillna(False)]
            tier_size = len(tier_df)
            if tier_size < BREAKOUT_TIER_MIN_VIDEOS:
                continue

            rank_col = ("views_per_hour" if key == "top_by_reach"
                        else "outlier_ratio")
            if rank_col not in tier_df.columns:
                rank_col = "views_per_hour" if "views_per_hour" in tier_df.columns else None

            # Floor absolute views on the ratio-ranked tiers. See
            # BREAKOUT_MIN_VIEWS_LADDER — outlier_ratio is unstable when
            # the channel's average is tiny, which let a 201-view video
            # outrank a 445k-view one on a real run.
            floor_used = None
            if key != "top_by_reach" and "view_count" in tier_df.columns:
                vc = pd.to_numeric(tier_df["view_count"], errors="coerce")
                for floor in BREAKOUT_MIN_VIEWS_LADDER:
                    qualified = tier_df[(vc >= floor).fillna(False)]
                    if len(qualified) >= 15 or floor == 0:
                        tier_df, floor_used = qualified, floor
                        break

            if rank_col:
                tier_df = tier_df.sort_values(rank_col, ascending=False)
            tier_df = tier_df.head(15)

            out.append(f"\n### {heading}  ·  {tier_size:,} videos in tier\n")
            out.append(f"> {tier_notes.get(key, '')}\n")
            if floor_used:
                out.append(f"> Showing only videos above **{floor_used:,} "
                           f"views** — outlier_ratio is unreliable below "
                           f"that, because a channel averaging 15 views "
                           f"produces a 13x ratio from a single 200-view "
                           f"video.\n")
            elif floor_used == 0:
                out.append("> ⚠️ Too few high-view videos in this tier to "
                           "filter — entries below a few thousand views "
                           "have unstable ratios and are not case "
                           "studies.\n")
            out.append("| " + " | ".join(preview_cols) + " |")
            out.append("|" + "|".join("---" for _ in preview_cols) + "|")
            for _, r in tier_df.iterrows():
                cells = []
                for c in preview_cols:
                    v = r[c]
                    if isinstance(v, float):
                        v = f"{v:,.1f}"
                    elif isinstance(v, bool):
                        v = "yes" if v else ""
                    v = str(v).replace("|", "\\|").replace("\n", " ")
                    if c == "title" and len(v) > 70:
                        v = v[:69] + "…"
                    cells.append(v)
                out.append("| " + " | ".join(cells) + " |")

    out.append("\n## Full ranked video list\n")
    display_cols = [c for c in slim.columns if c != "video_id"]
    out.append("| " + " | ".join(display_cols) + " |")
    out.append("|" + "|".join("---" for _ in display_cols) + "|")
    for _, r in slim.iterrows():
        cells = []
        for c in display_cols:
            v = r[c]
            if isinstance(v, float):
                v = f"{v:,.1f}"
            elif isinstance(v, bool):
                v = "yes" if v else ""
            v = str(v).replace("|", "\\|").replace("\n", " ")
            if c == "title" and len(v) > 90:
                v = v[:89] + "…"
            cells.append(v)
        out.append("| " + " | ".join(cells) + " |")

    return "\n".join(out) + "\n"
