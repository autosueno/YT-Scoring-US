"""
main.py

Orchestrates the full workflow and handles user interaction.

This is the ONLY module that decides WHAT to do and WHEN. It wires
together the "dumb" building blocks:

    collector.py   → collect / load / archive data (never decides to rebuild)
    metrics.py     → per-video features (called inside collector)
    baseline.py    → percentile lookups from saved statistics
    grader.py      → scoring logic
    report.py      → formatting / export
    quota_tracker  → API budget accounting

Decision logic that lives HERE (not in collector.py):
  - Whether a niche database is fresh enough to reuse or should be rebuilt.
  - Whether to grade a topic against an existing niche baseline.
  - Where to save reports and in which formats.

Run:
    export YOUTUBE_API_KEY=...
    python main.py
"""

import os
import json
import sys
from datetime import datetime, timezone

# Load a local .env file (if present) so YOUTUBE_API_KEY and similar secrets
# are available via os.getenv without the user exporting them by hand. A .env
# file is NOT read by the OS automatically — this makes it work. Optional:
# if python-dotenv isn't installed, we fall back to real environment vars.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import collector
import discover
import seo
import dashboard
import angles
import channel as channel_mod
import trends as trends_mod
import calendars as cal
import paths as paths_mod
import config
import report
from api_keys import ApiKeyManager
from baseline import load as load_baseline
from grader import grade_topic, grade_niche
from quota_tracker import QuotaTracker


# ============================================================
# FRESHNESS POLICY  (a main.py decision, deliberately not collector's)
# ============================================================

# A niche database older than this many days is considered stale.
NICHE_MAX_AGE_DAYS = 14


def _niche_age_days(niche_name: str):
    """Return the age in days of a niche's build, or None if not built."""
    meta = collector.load_metadata(niche_name)
    if not meta or not meta.get("created"):
        return None
    try:
        created = datetime.fromisoformat(str(meta["created"]).replace("Z", "+00:00"))
    except Exception:
        return None
    delta = datetime.now(timezone.utc) - created
    return delta.total_seconds() / 86400


def _should_rebuild(niche_name: str) -> bool:
    """Decide whether to rebuild a niche database (freshness policy)."""
    if not collector.database_exists(niche_name):
        return True
    age = _niche_age_days(niche_name)
    if age is None:
        return True
    return age > NICHE_MAX_AGE_DAYS


# ============================================================
# HIGH-LEVEL WORKFLOWS
# ============================================================

def ensure_niche(api_key, niche_name, query=None, tracker=None, force=False,
                 lookback_days=None):
    """
    Ensure a fresh-enough niche database exists, rebuilding if needed.

    Returns the niche DataFrame (loaded from disk or freshly built).

    Before spending any quota on a rebuild, checks two things in order:
      1. Was a query actually given? If not —
      2. Is there a REMEMBERED query from a past real build of this
         niche (collector.load_seed_query_history)? Use it silently if
         so — that's the whole point of remembering it.
    Only if BOTH are empty does this warn and ask for confirmation
    before proceeding with just the bare niche name as the search term
    — a much narrower, weaker collection than a real multi-seed build,
    and one that would silently replace whatever good dataset already
    existed. Found and fixed after it was flagged as a real risk: a
    person rebuilding a stale niche from option 2, with no memory of
    the original seed queries, had no warning before this happened and
    no way to recover the original queries afterward either.
    """
    tracker = tracker or QuotaTracker()

    if force or _should_rebuild(niche_name):
        age = _niche_age_days(niche_name)
        reason = "forced" if force else (
            "not built yet" if age is None else f"{age:.1f} days old (max {NICHE_MAX_AGE_DAYS})"
        )

        if not query:
            remembered = collector.load_seed_query_history(niche_name)
            if remembered:
                query = remembered
                print(f"  Using remembered seed queries for {niche_name!r}: "
                      f"{query}")
            else:
                print(f"\n  ⚠️  About to rebuild {niche_name!r} ({reason}) "
                      f"with NO seed queries given, and none remembered "
                      f"from a past build.")
                fallback_term = niche_name.split('@')[0].replace('_', ' ')
                print(f"      This will search using only the bare niche "
                      f"name, {fallback_term!r} — a single, "
                      f"narrow term, not the multi-seed search a real "
                      f"build uses. It will spend quota producing a "
                      f"weaker dataset than a proper build would, and "
                      f"will overwrite whatever is there now (the old "
                      f"one is archived, not deleted, but this option "
                      f"won't use it going forward).")
                proceed = input("      Proceed anyway? [y/N]: ").strip().lower()
                if proceed != "y":
                    print("      Cancelled. Re-run with real seed queries, "
                          "or build this niche through option 1 first.")
                    return None, tracker

        print(f"Rebuilding niche {niche_name!r} — {reason}.")
        df = collector.build_niche_database(
            api_key, niche_name, query=query, tracker=tracker,
            lookback_days=lookback_days
        )
    else:
        age = _niche_age_days(niche_name)
        print(f"Reusing niche {niche_name!r} — {age:.1f} days old (fresh).")
        df = collector.load_database(niche_name)

    return df, tracker


def grade_topic_against_niche(api_key, niche_name, topic, query=None,
                              tracker=None, export_formats=("json",)):
    """
    Full topic-grading workflow:
      1. Ensure the niche baseline exists (build if stale/missing).
      2. Collect fresh topic data.
      3. Load the baseline and grade the topic.
      4. Report + export.

    export_formats: any subset of {"json", "csv", "html"}.
    """
    tracker = tracker or QuotaTracker()

    # 1. Niche baseline. Keep the frame — the multi-window view needs
    # the niche's own videos to slice, not just its stored baseline.
    niche_df, _ = ensure_niche(api_key, niche_name, query=query, tracker=tracker)
    if niche_df is None:
        return None  # rebuild was needed and the user declined it

    # 2. Topic data (always fresh — a grade should reflect right now)
    #
    # Release the reserved tail keys HERE, not at the start of option 2's
    # dispatch — step 1 above (ensure_niche) can trigger a full niche
    # rebuild if the niche was missing or stale, and that rebuild must
    # NOT have access to the keys this reservation exists to protect.
    # Releasing early was a real bug, not a theoretical one: a run that
    # hit a niche rebuild inside this function printed "12 keys — 12 for
    # the build, 0 reserved for grading" — the exact failure this
    # mechanism exists to prevent, because the release had already
    # happened one step too soon.
    if hasattr(api_key, "use_reserved_keys"):
        api_key.use_reserved_keys()
    print(f"\nCollecting topic {topic!r}...")
    # Collect the topic over the SAME window the niche used. Grading is
    # percentile-based, so a topic sampled over 45 days against a
    # baseline built over 365 compares two different populations — the
    # topic looks artificially fresh, and nothing in the output shows it.
    niche_meta = collector.load_metadata(niche_name) or {}
    niche_lookback = niche_meta.get("lookback_days")
    if niche_lookback and niche_lookback != config.COLLECTION_LOOKBACK_DAYS:
        print(f"  Matching the niche's {niche_lookback}-day collection "
              f"window.")
    topic_df = collector.collect_topic(api_key, topic, tracker=tracker,
                                       lookback_days=niche_lookback)
    if topic_df.empty:
        print(f"No data for topic {topic!r}. Aborting grade.")
        return None

    # 3. Grade
    baseline = load_baseline(niche_name)
    # Momentum windows scale to the collection period, so the grader
    # needs to know which window this data came from.
    result = grade_topic(topic_df, baseline, topic,
                         lookback_days=niche_lookback)

    # Multi-window view. Free — it slices data already in memory — and
    # answers what one grade cannot: strong recently, or strong across
    # the whole period?
    try:
        from grader import grade_topic_multi
        multi = grade_topic_multi(topic_df, niche_df, topic)
        graded = [w for w in multi.get("windows", []) if not w.get("skipped")]
        if len(graded) >= 2:
            print("\n  Across time windows (each side cut to the same window):")
            print(f"    {'window':>8}{'demand':>9}{'supply':>9}"
                  f"{'opportunity':>13}{'grade':>7}")
            for w in multi["windows"]:
                if w.get("skipped"):
                    print(f"    {w['window']:>7}d   skipped — {w['skipped'][:44]}")
                else:
                    print(f"    {w['window']:>7}d{w['demand']:>9.1f}"
                          f"{w['supply']:>9.1f}{w['opportunity']:>13.1f}"
                          f"{w['grade']:>7}")
            if multi.get("reading"):
                print(f"\n    {multi['reading']}")
    except Exception as e:
        print(f"  (multi-window view skipped: {type(e).__name__}: {e})")

    # 4. Report
    report.print_grade(result)
    report.print_quota(tracker)

    # 5. Export
    report.export(result, topic_df, topic, export_formats)

    # 6. Visual dashboard, matching the calendar/SEO dark theme.
    try:
        dpath = dashboard.export_dashboard(result, niche_name)
        print(f"Visual dashboard → {dpath}")
    except Exception as e:
        print(f"  (dashboard skipped: {type(e).__name__}: {e})")

    # 7. SEO / packaging report for THIS topic.
    #
    # At niche level the comparison is "breakout videos vs the whole
    # niche". At topic level it becomes "the videos winning at THIS topic
    # vs the ones losing at it" — a tighter, more directly usable contrast,
    # because every video in the sample is competing for the same viewer.
    # Clustering still earns its place here: within one topic it separates
    # sub-angles ("for beginners" vs "vs ETFs" vs "best of 2026").
    try:
        import paths as _paths
        topic_out = _paths.topic_export_dir(config.EXPORTS_DIR, topic, "packaging")
        scored = discover.score_breakouts(topic_df, baseline)
        clusters = discover.build_clusters(scored, max_clusters=config.CLUSTER_MAX)  # curated: SEO angle list is for reading
        # SEO report analyses what's working; tag generator hands you a
        # ready-to-use list. Different jobs — the first is diagnostic,
        # the second is the thing you paste into the upload form.
        seo_paths = seo.export_seo_report(scored, clusters, topic,
                                          out_dir_override=topic_out)
        tags_path = seo.export_tag_list(topic_df, topic, out_dir=topic_out)
        print("\nSEO & packaging report for this topic:")
        for fmt, p in seo_paths.items():
            print(f"  {fmt:12s} → {p}")
        print(f"  {'tags':12s} → {tags_path}")
        if len(topic_df) < 200:
            print(f"  ⚠️  Only {len(topic_df)} videos — lift figures from a "
                  f"small sample are noisy; treat them as hints.")
    except Exception as e:
        print(f"  (SEO report skipped: {type(e).__name__}: {e})")

    return result


def grade_existing_niche(niche_name):
    """
    Grade a niche against the OTHER niches in your built library — no
    API calls.

    PREVIOUSLY: this graded a niche against a baseline built FROM
    ITSELF, which is circular and forces demand to ~64.0 and supply to
    exactly 50.0 by arithmetic, for EVERY niche, regardless of what the
    niche actually looks like — verified directly against three
    different real niches, all three producing the identical numbers.
    crossniche.py fixed the underlying mechanism (leave-one-out pooled
    grading) as a separate module, but it was never wired into this —
    the simplest, most commonly used single-niche grading path — so
    running this specific option kept reproducing the exact bug this
    whole project exists to fix, silently, the entire time the fix
    already existed elsewhere in the codebase.

    NOW: pools against every OTHER niche built at the same window (see
    crossniche.py for why same-window only). Falls back to the OLD
    self-baseline behavior, with an explicit on-screen warning, only
    when there are fewer than crossniche.MIN_POOL_NICHES other niches to
    pool against — a self-comparison is a worse answer than a real one,
    but it is a better answer than refusing to grade at all for someone
    who has only just started building their library.

    Deliberately does NOT touch option 2's topic-vs-its-own-niche
    grading, which is a different, non-circular comparison and was never
    the bug this fixes — see grade_topic_against_niche.
    """
    if not _require_database(niche_name):
        return None

    import crossniche as _xn
    from paths import split_window as _split_window

    base, win = _split_window(niche_name)
    pool_keys = _xn.available_keys(win) if win else _xn.available_keys()
    others = [k for k in pool_keys if k != niche_name]

    result = None
    if len(others) >= _xn.MIN_POOL_NICHES:
        out = _xn.sweep_selected(pool_keys, verbose=False)
        if not out.get("error"):
            result = out.get("results", {}).get(niche_name)
        if result is not None:
            print(f"\n  Graded against the other {len(others)} niche(s) "
                  f"in your library at this window.")

    if result is None:
        df = collector.load_database(niche_name)
        baseline = load_baseline(niche_name)
        meta = collector.load_metadata(niche_name) or {}
        result = grade_niche(df, baseline, niche_name,
                             lookback_days=meta.get('lookback_days'))
        print(f"\n  ⚠️  Only {len(others)} other niche(s) built at this "
              f"window — need {_xn.MIN_POOL_NICHES} to grade against a "
              f"real pool. Falling back to self-comparison, which is "
              f"less meaningful: demand and supply will read close to "
              f"64.0/50.0 by arithmetic almost regardless of the niche. "
              f"Build more niches at this window for a real comparison.")

    report.print_grade(result)
    try:
        path = _save_grade_json(result, niche_name)
        print(f"  Grade data      → {path}")
    except Exception as e:
        print(f"  (grade json skipped: {type(e).__name__}: {e})")
    try:
        path = dashboard.export_dashboard(result, niche_name)
        print(f"\nVisual dashboard → {path}")
    except Exception as e:
        print(f"  (dashboard skipped: {type(e).__name__}: {e})")
    return result


def _save_grade_json(result, niche_name):
    """
    Write the grade result to exports/<niche>/<window>/market/.

    The pillar scores existed only in terminal output and inside rendered
    HTML — readable by a person, not by anything else. That makes the
    engine's central number, Opportunity, the one output that could not
    be checked, compared across niches, or calibrated against, without
    scraping markup.

    JSON because these are numbers meant to be re-read. Everything the
    grader computed is kept, including the pillar internals, since the
    point is to be able to ask later why a score came out as it did.
    """
    import json as _json
    from paths import export_dir_for, named
    from config import EXPORTS_DIR

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "market")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, named(niche_name, "grade.json"))

    payload = dict(result)
    payload["_saved_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _plain(o):
        """NumPy scalars and pandas types are not JSON-serialisable."""
        if hasattr(o, "item"):
            return o.item()
        return str(o)

    with open(path, "w", encoding="utf-8") as f:
        _json.dump(payload, f, indent=2, default=_plain)
    return path


def show_niche(niche_name):
    """Print a summary of an already-built niche database (no API calls)."""
    if not _require_database(niche_name):
        return
    meta = collector.load_metadata(niche_name)
    if not meta:
        print(f"Niche {niche_name!r} has no metadata.json — rebuild it (option 1).")
        return
    df = collector.load_database(niche_name)
    report.print_niche_summary(meta, df)


# ============================================================
# EXPORT HELPER
# ============================================================

# NOTE: report.export() owns output filenames. main.py deliberately does
# not build paths — storage layout is not orchestration's business.


# ============================================================
# INTERACTIVE CLI
# ============================================================

def _prompt_formats():
    raw = input("Export formats [json,csv,html] (Enter=json): ").strip().lower()
    if not raw:
        return ("json",)
    chosen = tuple(f.strip() for f in raw.split(",") if f.strip() in {"json", "csv", "html"})
    return chosen or ("json",)


def _warn_if_incomplete(niche_name):
    """Warn when a baseline came from a quota-truncated build."""
    meta = collector.load_metadata(niche_name)
    if niche_name.endswith("_incomplete") or (meta or {}).get("complete") is False:
        print()
        print("  ⚠️  This niche was built from an INCOMPLETE collection.")
        print("     Later seeds may be under-represented, so percentile")
        print("     baselines can be skewed. Fine for discovery; treat")
        print("     topic grades as indicative rather than precise.")
        print()


def _offer_slice_instead(niche_base, requested_days):
    """
    If a longer build exists, offer slicing rather than collecting again.

    Returns "slice", "build", or "cancel". Says nothing and returns
    "build" when there is no longer dataset to slice.
    """
    import paths as _p
    base, _ = _p.split_window(niche_base)
    root = os.path.join(config.DATASETS_DIR, _p.safe_name(base))
    if not os.path.isdir(root):
        return "build"
    longer = []
    for d in sorted(os.listdir(root)):
        if not (d.endswith("d") and d[:-1].isdigit()):
            continue
        days = int(d[:-1])
        if days > requested_days:
            longer.append((days, f"{base}{_p.WINDOW_SEP}{d}"))
    if not longer:
        return "build"

    days, key = max(longer)
    try:
        existing = collector.load_database(key)
    except Exception:
        return "build"
    if existing is None or existing.empty:
        return "build"

    import insights as _ins
    ages = _ins._days_since(existing)
    in_window = int((ages <= requested_days).sum())

    print(f"\n  A {days}d build of {base!r} already exists "
          f"({len(existing):,} videos).")
    print(f"  {in_window:,} of them fall within your requested "
          f"{requested_days}d window.")
    if in_window < 80:
        print("  That is thin — percentiles from under ~80 videos are "
              "noisy, so a")
        print(f"  fresh {requested_days}d build would be meaningfully "
              f"better here.")
    else:
        print("  That is probably enough to work with, and costs nothing.")
    print()
    print(f"    1) Slice the existing {days}d build (free)")
    print(f"    2) Build a fresh {requested_days}d dataset "
          f"(~13,000+ units)")
    print( "    3) Cancel")
    choice = input("  Choose [1]: ").strip() or "1"
    return {"1": "slice", "2": "build", "3": "cancel"}.get(choice, "slice")


def _slice_one_niche(niche_base, requested_days, src_key=None, verbose=True):
    """
    Do the actual slicing work for ONE niche — no interactive prompts,
    no "run discovery?" nudge. Pulled out of _build_from_slice so the
    bulk multi-niche slicer (_dispatch_bulk_slice) can call this in a
    loop without firing an interactive prompt after every single niche,
    while the existing single-niche flow keeps working exactly as
    before by calling this and then doing its own interactive tail.

    Returns a dict describing what happened — always, never raises for
    an ordinary "can't slice this one" case, so a bulk run can report
    every niche's outcome instead of dying on the first thin one:
        {"status": "sliced", "dest_key":..., "src_key":..., "videos":...}
        {"status": "no_longer_build"}   — nothing longer than the target exists
        {"status": "empty_after_slice"} — a longer build exists but has
                                           nothing inside the requested window
        {"status": "already_exists"}    — a build at this exact window
                                           already exists (caller decides
                                           whether that's a skip or an
                                           overwrite)
    """
    import pandas as pd
    import paths as _p
    import statistics as _sm
    import insights as _ins
    base, _ = _p.split_window(niche_base)
    root = os.path.join(config.DATASETS_DIR, _p.safe_name(base))
    dest_key = _p.with_window(base, requested_days)

    if src_key is None:
        if not os.path.isdir(root):
            return {"status": "no_longer_build"}
        longer = [(int(d[:-1]), f"{base}{_p.WINDOW_SEP}{d}")
                  for d in os.listdir(root)
                  if d.endswith("d") and d[:-1].isdigit()
                  and int(d[:-1]) > requested_days]
        if not longer:
            return {"status": "no_longer_build"}
        src_days, src_key = max(longer)
    else:
        src_days = int(_p.split_window(src_key)[1][:-1])

    df = collector.load_database(src_key)
    ages = _ins._days_since(df)
    sliced = df[(ages <= requested_days).values].reset_index(drop=True)
    if sliced.empty:
        return {"status": "empty_after_slice", "src_key": src_key}

    if verbose:
        print(f"  Slicing {len(sliced):,} of {len(df):,} videos from the "
              f"{src_days}d build...")
    os.makedirs(collector._niche_dir(dest_key), exist_ok=True)
    collector._save_videos(sliced, collector._videos_path(dest_key))
    _sm.save_statistics(sliced, collector._statistics_path(dest_key))
    _ins.save_insights(_ins.generate_insights(sliced),
                       collector._insights_path(dest_key))

    src_meta = collector.load_metadata(src_key) or {}
    meta = dict(src_meta)

    def _stat(col, q):
        if col not in sliced.columns:
            return None
        v = pd.to_numeric(sliced[col], errors="coerce").dropna()
        return float(v.quantile(q)) if len(v) else None

    meta.update({
        "niche": base,
        "videos": int(len(sliced)),
        "channels": int(sliced["channel_id"].nunique()),
        "lookback_days": requested_days,
        "created": datetime.now(timezone.utc).isoformat(),
        "median_vph": _stat("views_per_hour", 0.50),
        "median_views": _stat("view_count", 0.50),
        "median_engagement": _stat("engagement_rate", 0.50),
        "p90_vph": _stat("views_per_hour", 0.90),
        "p90_views": _stat("view_count", 0.90),
        "p75_views": _stat("view_count", 0.75),
        "p90_engagement": _stat("engagement_rate", 0.90),
        "oldest_video_days": float(
            pd.to_numeric(sliced["hours_since_upload"], errors="coerce").max() / 24)
            if "hours_since_upload" in sliced.columns else None,
        "newest_video_days": float(
            pd.to_numeric(sliced["hours_since_upload"], errors="coerce").min() / 24)
            if "hours_since_upload" in sliced.columns else None,
        "derived_from": src_key,
        "derived_note": (f"Sliced from the {src_days}d build rather than "
                         f"collected. Sampling density in this window is "
                         f"whatever the parent build allocated to it."),
    })
    with open(collector._metadata_path(dest_key), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return {"status": "sliced", "dest_key": dest_key, "src_key": src_key,
            "videos": len(sliced), "meta": meta, "df": sliced}


def _build_from_slice(niche_base, requested_days):
    """
    Interactive single-niche slice — the original flow, now a thin
    wrapper around _slice_one_niche() plus this flow's own "run
    discovery now?" nudge. Behaviour unchanged from before the bulk
    slicer existed.
    """
    r = _slice_one_niche(niche_base, requested_days)
    if r["status"] == "no_longer_build":
        print("  Nothing longer to slice from.")
        return
    if r["status"] == "empty_after_slice":
        print("  No videos fall inside that window.")
        return

    print(f"  Dataset written → {collector._niche_dir(r['dest_key'])}")
    print("  Statistics and baseline recomputed from the slice, so "
          "percentiles")
    print(f"  reference the {requested_days}d population rather than the "
          f"parent's.")
    try:
        report.print_niche_summary(r["meta"], r["df"])
    except Exception:
        pass
    if input("\n  Run discovery on it now? [Y/n]: ").strip().lower() != "n":
        discover_opportunities(r["dest_key"])


def _dispatch_bulk_slice():
    """
    Option 12 — slice EVERY built niche (or a chosen subset) down to
    one target window at once, all for free: this only re-filters
    already-collected videos by age, so it costs zero API quota
    regardless of how many niches it touches.

    Built for exactly the situation option 10 (compare) runs into
    constantly: a real cross-niche comparison needs every niche at the
    SAME window, and slicing one at a time through option 1 makes that
    a lot of repetitive manual work for a library of any real size.

    Two decisions made explicit rather than silent, per the design
    questions flagged when this was first put on the agenda:
      - defaults to EVERY niche in the library, but always shows the
        full list and lets you narrow it before anything runs;
      - a niche that cannot supply the requested window (nothing built
        longer than it) is REPORTED, not silently dropped — the whole
        point of a bulk tool is trusting the summary, so a niche that
        silently didn't get sliced would be a real, easy-to-miss gap.
    """
    import compare as _cmp
    import paths as _p

    all_niches = _cmp.list_all_niches()
    if not all_niches:
        print("\n  No niches built yet — nothing to slice.")
        return

    # Reduce to unique BASE names (a niche can appear at several
    # windows already) with the longest window each currently has,
    # since that longest build is what any slice will be drawn from.
    longest = {}
    for key, meta in all_niches:
        base, win = _p.split_window(key)
        days = int(win[:-1]) if win and win[:-1].isdigit() else None
        if days is None:
            continue
        if base not in longest or days > longest[base][0]:
            longest[base] = (days, meta.get("videos", 0))

    if not longest:
        print("\n  No windowed niche builds found to slice from.")
        return

    print(f"\n  {len(longest)} niche(s) in your library:")
    ordered = sorted(longest.items())
    for i, (base, (days, videos)) in enumerate(ordered, 1):
        print(f"    {i:>2}) {base:<28} longest build: {days}d ({videos:,} videos)")

    raw = input(f"\n  Slice which? (e.g. 1,3,5 — Enter for all "
               f"{len(ordered)}): ").strip()
    if raw:
        idx = [int(x) - 1 for x in raw.split(",") if x.strip().isdigit()]
        chosen = [ordered[i] for i in idx if 0 <= i < len(ordered)]
    else:
        chosen = ordered
    if not chosen:
        print("  (nothing selected — cancelled)")
        return

    pick = input("\n  Target window in days (e.g. 30 or 90): ").strip()
    if not pick.isdigit():
        print("  (not a number — cancelled)")
        return
    requested_days = int(pick)

    force = input(f"  Overwrite a niche that already has a {requested_days}d "
                  f"build? [y/N]: ").strip().lower() == "y"

    sliced, skipped_too_thin, skipped_exists, skipped_empty = [], [], [], []
    for base, (longest_days, _) in chosen:
        if longest_days <= requested_days:
            skipped_too_thin.append((base, longest_days))
            continue
        dest_key = _p.with_window(base, requested_days)
        if collector.database_exists(dest_key) and not force:
            skipped_exists.append(base)
            continue
        print(f"\n  {base}:")
        r = _slice_one_niche(base, requested_days)
        if r["status"] == "sliced":
            sliced.append((base, r["videos"]))
        elif r["status"] == "empty_after_slice":
            skipped_empty.append(base)
        else:
            skipped_too_thin.append((base, longest_days))

    print("\n" + "=" * 60)
    print(f"  Bulk slice to {requested_days}d — done")
    print("=" * 60)
    if sliced:
        print(f"\n  Sliced ({len(sliced)}):")
        for base, n in sliced:
            print(f"    {base:<28} {n:,} videos")
    if skipped_too_thin:
        print(f"\n  Skipped — no build longer than {requested_days}d exists "
              f"({len(skipped_too_thin)}):")
        for base, longest_days in skipped_too_thin:
            print(f"    {base:<28} longest available: {longest_days}d")
    if skipped_empty:
        print(f"\n  Skipped — longer build exists but has nothing inside "
              f"{requested_days}d ({len(skipped_empty)}):")
        for base in skipped_empty:
            print(f"    {base}")
    if skipped_exists:
        print(f"\n  Skipped — already has a {requested_days}d build "
              f"({len(skipped_exists)}, re-run with overwrite to redo):")
        for base in skipped_exists:
            print(f"    {base}")
    print()

    if sliced and input("  Run discovery (option 3) on all newly sliced "
                        "niches now? [y/N]: ").strip().lower() == "y":
        print(f"\n  Running discovery across {len(sliced)} niche(s) — "
              f"no API cost, but this takes real compute time per niche.")
        for base, _ in sliced:
            dest_key = _p.with_window(base, requested_days)
            print(f"\n  --- {dest_key} ---")
            try:
                discover_opportunities(dest_key)
            except Exception as e:
                print(f"    (discovery failed for {base}: "
                      f"{type(e).__name__}: {e})")




def _pick_niche(prompt="Niche display name: "):
    """
    Resolve a niche name to a specific stored WINDOW.

    A niche can exist at several collection windows at once
    (finance/365d, finance/60d). They are different datasets describing
    different populations and are not interchangeable, so when more than
    one exists the user picks rather than the tool guessing. With exactly
    one, it is chosen silently.
    """
    import paths as _p
    raw = input(prompt).strip()
    if not raw:
        return None
    base, win = _p.split_window(raw)
    if win:
        return raw
    root = os.path.join(config.DATASETS_DIR, _p.safe_name(base))
    if not os.path.isdir(root):
        return raw
    windows = sorted(d for d in os.listdir(root)
                     if os.path.isdir(os.path.join(root, d))
                     and d.endswith("d") and d[:-1].isdigit())
    if not windows:
        return raw
    if len(windows) == 1:
        return f"{base}{_p.WINDOW_SEP}{windows[0]}"
    print(f"\n  {base!r} exists at several collection windows:")
    for i, w in enumerate(windows, 1):
        print(f"    {i}) {w}")
    pick = input("  Choose [1]: ").strip() or "1"
    idx = int(pick) - 1 if pick.isdigit() and 0 < int(pick) <= len(windows) else 0
    return f"{base}{_p.WINDOW_SEP}{windows[idx]}"


def _require_database(niche_name) -> bool:
    """
    Guard for the read-only workflows (discover / summary / grade-existing).

    These read datasets/<niche>/ from disk, so a missing or never-built
    niche is an ordinary user situation — not a crash. Print actionable
    guidance and return False instead of raising a traceback.
    """
    if collector.database_exists(niche_name):
        return True

    print(f"\nNo niche database found for {niche_name!r}.")
    available = _available_niches()
    if available:
        print(f"  Available niches: {', '.join(available)}")
    else:
        print("  You haven't built any niche databases yet.")
    print("  Run option 1 (Build / refresh a niche database) first — "
          "options 2-5 all read data that option 1 creates.\n")
    return False


def _built_dataset_exists(base_dir):
    """
    Does this folder hold a usable build, at either layout and either
    naming form?

    Checks the folder itself (the flat legacy layout) and one level down
    (the windowed layout, datasets/<niche>/365d/). Uses paths.locate so
    a namespaced videos.csv is found as readily as a bare one.
    """
    import paths as _p
    if not os.path.isdir(base_dir):
        return False
    if _p.locate(base_dir, "videos.csv"):
        return True
    for sub in os.listdir(base_dir):
        d = os.path.join(base_dir, sub)
        if os.path.isdir(d) and _p.locate(d, "videos.csv"):
            return True
    return False


def _available_niches():
    """Names of niches that have a usable database on disk."""
    from config import DATASETS_DIR
    if not os.path.isdir(DATASETS_DIR):
        return []
    # Check for a real build (at the parent for the flat layout, or in any
    # window subfolder), so the list matches what can actually be loaded.
    # Listing a bare folder that only CONTAINS window folders was half the
    # confusion: the name showed as available, then "not found" on use.
    return sorted(
        d for d in os.listdir(DATASETS_DIR)
        if _built_dataset_exists(os.path.join(DATASETS_DIR, d))
    )


def discover_opportunities(niche_name):
    """
    Rank an existing niche's FULL video list by breakout score and export
    it (json/csv/md) for manual AI analysis. No API calls; pure math.
    """
    if not _require_database(niche_name):
        return None
    _warn_if_incomplete(niche_name)
    df = collector.load_database(niche_name)
    baseline = load_baseline(niche_name)
    paths = discover.export_breakouts(df, baseline, niche_name)
    print(f"\nExported full breakout ranking ({len(df)} videos):")
    for fmt, p in paths.items():
        print(f"  {fmt:12s} → {p}")

    # SEO / packaging report — how the winners are packaged, not just what
    # they are about. Separate deliverable, separate file.
    scored = discover.score_breakouts(df, baseline)
    clusters = discover.build_clusters(scored, max_clusters=config.CLUSTER_MAX)  # curated: SEO angle list is for reading
    seo_paths = seo.export_seo_report(scored, clusters, niche_name)
    print("\nExported SEO & packaging report:")
    for fmt, p in seo_paths.items():
        print(f"  {fmt:12s} → {p}")
    # Visual publishing calendars (weekly heatmap + monthly pattern)
    try:
        import timing as _timing
        _t = _timing.build_timing_report(scored)
        timing_paths = _timing.export_timing_report(scored, niche_name)
        print("\nTiming report:")
        for fmt, p in timing_paths.items():
            print(f"  {fmt:12s} → {p}")
        paths.update({f"timing_{k}": v for k, v in timing_paths.items()})
        cal_paths = cal.export_calendars(
            scored, niche_name,
            tiers=config.CREATOR_SIZE_TIERS,
            event_info=_t.get("event_driven"))
        print("\nPublishing calendars (open in a browser):")
        for k, p in cal_paths.items():
            print(f"  {k:12s} → {p}")
        paths.update({f"calendar_{k}": v for k, v in cal_paths.items()})
    except Exception as e:
        print(f"  (calendars skipped: {type(e).__name__}: {e})")

    sheet = os.path.join(
        paths_mod.export_dir_for(config.EXPORTS_DIR, niche_name, "packaging"),
        "thumbnails", "_contact_sheet.jpg")
    if os.path.exists(sheet):
        print(f"  thumbnails   → {sheet}")
        print("     ↳ upload this single image to Claude/ChatGPT to have the "
              "thumbnail pattern analysed.")
    paths.update({f"seo_{k}": v for k, v in seo_paths.items()})

    # Curation prompt for Option 6's AI-curated path. Written every
    # discovery run, so a user who later wants an AI to pick 20
    # thumbnails to inspect has the source list ready and formatted.
    try:
        import thumbnail_sheet
        cp_path = thumbnail_sheet.write_curation_prompt(scored, niche_name)
        print(f"  curation prompt → {cp_path}")
        print("     ↳ paste this into Claude/ChatGPT to get 20 curated video "
              "IDs, then feed those to Option 6.")
    except Exception as e:
        print(f"  (curation prompt skipped: {type(e).__name__}: {e})")

    # Breakouts and clusters split by era. Ranked within each era, so a
    # six-month-old video competes with its contemporaries rather than
    # with videos that had a ten-fold arithmetic advantage on age.
    try:
        era_paths = discover.export_eras(df, niche_name)
        if era_paths.get("md"):
            print(f"  by era          → {era_paths['md']}")
            print(f"     ↳ {era_paths['eras']} eras ranked separately, "
                  f"{era_paths['durable_themes']} themes appear in more "
                  f"than one.")
    except Exception as e:
        print(f"  (era breakdown skipped: {type(e).__name__}: {e})")

    # Shorts signal, if any were retained. Written here rather than as
    # its own menu option because it reads data the build already
    # collected — there is nothing to fetch and no reason to make the
    # user ask for it separately.
    try:
        import shorts as _shorts
        sres = _shorts.export_shorts_report(niche_name, long_df=df)
        if not sres.get("error"):
            print(f"  shorts signal   → {sres['md']}")
            print(f"     ↳ {sres['shorts']:,} Shorts analysed, "
                  f"{sres['gaps']} subjects busy in short form but thin "
                  f"in long-form.")
    except Exception as e:
        print(f"  (shorts report skipped: {type(e).__name__}: {e})")

    # Angle generation + transcript study. These convert FINDINGS into
    # things that can be scripted — the step percentile math cannot do.
    try:
        _meta = collector.load_metadata(niche_name) or {}
        grade_result = grade_niche(df, baseline, niche_name,
                                   lookback_days=_meta.get('lookback_days'))
        ang = angles.write_angles_prompt(scored, clusters, niche_name,
                                         grade_result=grade_result)
        tra = angles.write_transcript_study(scored, niche_name)
        print(f"  angle prompt    → {ang}")
        print("     ↳ paste into an AI for 15 scriptable angles (not topics).")
        print(f"  transcript study→ {tra}")
        print("     ↳ shortlist of videos to transcribe, plus the structural "
              "analysis prompt.")
    except Exception as e:
        print(f"  (angle prompts skipped: {type(e).__name__}: {e})")

    print("\nOpen the .md file and paste it into Claude/ChatGPT — it starts "
          "with a ready-made analysis prompt (cluster topics, explain trends, "
          "suggest adjacent opportunities).")
    return paths


def _load_keys_or_exit():
    """Build the multi-key manager from .env; exit with guidance if none."""
    try:
        keys = ApiKeyManager.from_env()
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"(Loaded {keys.total_keys} API key(s) from environment)")
    return keys


def _menu():
    print("=" * 60)
    print("  YouTube Intelligence Engine")
    print("=" * 60)
    print("  1) Build / refresh a niche database (multi-seed)")
    print("  2) Grade a topic against a niche")
    print("  3) Discover opportunities (breakout ranking + export)")
    print("  4) Show an existing niche summary")
    print("  5) Grade an existing niche (no API calls)")
    print("  6) Thumbnail contact sheet creator")
    print("  7) Search intent & Reddit signals (external demand)")
    print("  8) Tear down a single channel")
    print("  9) What changed since the last build")
    print(" 10) Compare all niches side by side")
    print(" 11) Regenerate stats/insights/metadata from an edited videos.csv")
    print(" 12) Bulk-slice your whole niche library to one window (free)")
    print(" 13) Quit")
    return input("\nChoose [1-13]: ").strip()


def _dispatch(choice):
    """Run one menu action. Isolated so a crash doesn't leave the loop."""
    if choice == "1":
        keys = _load_keys_or_exit()
        # Hard-reserve the tail keys so a large build cannot leave a later
        # topic grade with no quota. Verified failure mode: an option-1
        # build under soft reservation drained key #2, then option-2 hit
        # a bare 429 with no live keys left. This makes the reserve real.
        keys.reserve_for_topics(True)
        niche = _pick_niche()
        query = input(
            "Seed queries, comma-separated (Enter to reuse niche name)\n"
            "  e.g. finance, investing, economics, banking, stocks: "
        ).strip() or None
        print("\n  Collection window:")
        for i, (days, label) in enumerate(config.LOOKBACK_PRESETS, 1):
            mark = "  (default)" if days == config.COLLECTION_LOOKBACK_DAYS else ""
            print(f"    {i}) {days:>3} days — {label}{mark}")
        pick = input("  Choose [Enter for default]: ").strip()
        lookback = config.COLLECTION_LOOKBACK_DAYS
        if pick.isdigit() and 1 <= int(pick) <= len(config.LOOKBACK_PRESETS):
            lookback = config.LOOKBACK_PRESETS[int(pick) - 1][0]

        # Before spending quota on a short window, check whether a LONGER
        # build already exists. A 365-day dataset contains every shorter
        # window inside it, so a 60-day view can often be sliced for free.
        # Slicing is thinner — a year-build spreads its budget across 13
        # months, so only ~1/13 lands in any recent month — so the check
        # counts the actual videos rather than guessing.
        sliced = _offer_slice_instead(niche, lookback)
        if sliced == "cancel":
            return
        if sliced == "slice":
            _build_from_slice(niche, lookback)
            return
        if lookback < 90:
            print(f"  ⚠️  {lookback}-day window: expect far fewer videos, and "
                  f"percentiles from a thin\n      sample are noisy. Good for "
                  f"spotting movement, weak as a baseline.")
        force = input("Force rebuild even if fresh? [y/N]: ").strip().lower() == "y"
        # The window becomes part of the storage key, so a 60-day build
        # never overwrites a 365-day one of the same name.
        import paths as _p
        niche_key = _p.with_window(niche, lookback)
        df, tracker = ensure_niche(keys, niche_key, query=query, force=force,
                                   lookback_days=lookback)
        if df is None:
            return  # rebuild was needed and the user declined it
        niche = niche_key
        meta = collector.load_metadata(niche)
        if meta:
            report.print_niche_summary(meta, df)
        report.print_quota(tracker)
        # The build SAVES Shorts; the report on them is written by
        # discovery. Say so, because seeing shorts.csv appear with no
        # accompanying analysis reads like something failed.
        try:
            import shorts as _sh
            if _sh.load_shorts(niche) is not None:
                print("\n  Shorts were saved but not yet analysed — run "
                      "option 3 to get\n  the shorts report "
                      "(exports/.../shorts/shorts.md).")
        except Exception:
            pass

    elif choice == "2":
        keys = _load_keys_or_exit()
        # Two-part protection, both needed — found only one existed by
        # actually reproducing the bug against real data (12 keys, 0
        # reserved for grading, on a run that hit a niche rebuild):
        #
        #   1. reserve_for_topics(True), HERE, before anything else runs.
        #      This is the actual protective switch — build_key_order()
        #      only excludes the reserved tail keys when this is on.
        #      Without it (the state this was in before), a niche rebuild
        #      triggered inside step 1 of grade_topic_against_niche has
        #      every key "fair game", including the ones meant to be off
        #      limits to any build.
        #   2. use_reserved_keys(), inside grade_topic_against_niche,
        #      right before topic collection — releases the protection
        #      and switches onto the tail keys for that step specifically.
        #      (This was the only piece fixed in an earlier pass; it was
        #      necessary but not sufficient on its own.)
        keys.reserve_for_topics(True)
        niche = _pick_niche("Niche name (baseline to grade against): ")
        query = input("Niche seed queries (Enter to reuse niche name): ").strip() or None
        topic = input("Topic to grade: ").strip()
        formats = _prompt_formats()
        grade_topic_against_niche(keys, niche, topic, query=query,
                                  export_formats=formats)

    elif choice == "3":
        niche = _pick_niche("Niche name: ")
        if niche:
            discover_opportunities(niche)

    elif choice == "4":
        niche = _pick_niche("Niche name: ")
        if niche:
            show_niche(niche)

    elif choice == "5":
        niche = _pick_niche("Niche name: ")
        if niche:
            grade_existing_niche(niche)

    elif choice == "6":
        _dispatch_thumbnail_sheet()

    elif choice == "7":
        _dispatch_signals()

    elif choice == "8":
        _dispatch_channel()

    elif choice == "9":
        _dispatch_trends()

    elif choice == "10":
        _dispatch_compare()

    elif choice == "11":
        niche = _pick_niche("Niche name (the videos.csv should already be "
                            "edited/replaced at its normal path): ")
        if niche:
            result = collector.regenerate_from_edited_videos(niche)
            if result.get("error"):
                print(f"\n  ⚠️  {result['error']}")
                if result.get("missing"):
                    print(f"      missing columns: {result['missing']}")
                if result.get("hint"):
                    print(f"      {result['hint']}")

    elif choice == "12":
        _dispatch_bulk_slice()

    elif choice == "13":
        return "quit"

    else:
        print("Invalid choice.")


def _dispatch_compare():
    """
    Option 10 — pick which built niches to compare.

    Not "all niches at one window": that both over-selects (you rarely
    want every build you have ever made) and under-selects (a niche whose
    only build is 90d vanishes entirely from a 365d comparison, which is
    how two perfectly good builds can produce "no niches found").

    Two steps now, where there used to be one. If more than one window
    has anything built, ask which window first — otherwise a 90d build
    was permanently invisible the moment a 365d build of the same niche
    existed, with no way to ask for it explicitly. Then list what's
    built at the chosen window and let the specific niches be picked.
    """
    import compare as _cmp
    import pandas as pd

    # If more than one window has built niches, ask which one first.
    # Without this, preferred_keys() always picks the longest window per
    # niche silently — a 90d build becomes permanently invisible to this
    # option the moment a 365d build of the same niche exists, with no
    # way to explicitly ask "show me my 90d niches instead". Skipped
    # entirely when only one window exists, since there is nothing to
    # choose between.
    windows = _cmp.available_windows()
    chosen_window = None
    if len(windows) > 1:
        print(f"\n  You have niches built at more than one window: "
              f"{', '.join(windows)}.")
        pick = input(f"  Compare which window? ({'/'.join(windows)} — "
                     f"Enter for {windows[0]}, the longest): ").strip()
        # Accept "30" as well as "30d" — the bare-number version was
        # silently falling through to the default window with zero
        # indication that happened, which is worse than a typo: it
        # looks like it worked and quietly compares the wrong data.
        if pick and pick.isdigit():
            pick = f"{pick}d"
        chosen_window = pick if pick in windows else windows[0]
        if pick and chosen_window != pick:
            print(f"  ⚠️  {pick!r} isn't one of your built windows "
                  f"({', '.join(windows)}) — using {chosen_window} instead.")
        keys = _cmp.keys_at_window(chosen_window)
    else:
        keys = _cmp.preferred_keys()

    if not keys:
        print("\n  No built niches found in datasets/.")
        print("  Option 1 builds one. If you have built niches already,")
        print("  check that datasets/<niche>/<window>/ holds a videos.csv.")
        return
    if len(keys) < 2:
        base = _cmp.split_window(keys[0])[0]
        print(f"\n  Only one niche is built ({base}).")
        print("  A comparison needs at least two.")
        return

    all_builds = [k for k, _ in _cmp.list_all_niches()]
    print(f"\n  Built niches ({len(keys)}):\n")
    for i, k in enumerate(keys, 1):
        base, win = _cmp.split_window(k)
        others = sorted({_cmp.split_window(b)[1] for b in all_builds
                         if _cmp.split_window(b)[0] == base
                         and _cmp.split_window(b)[1] != win},
                        key=lambda w: -(int(w[:-1]) if w and w[:-1].isdigit() else 0))
        extra = f"   (also built at {', '.join(others)})" if others else ""
        print(f"    {i:>2}) {base:<28} {win or '—':>6}{extra}")
    if chosen_window is None:
        print("\n  Longest window is pre-selected for each niche — a 365d "
              "build\n  rests on more date slices than a 90d one, so its "
              "percentiles\n  are the steadier measurement.")

    # The example must use numbers that actually exist. Suggesting
    # "1,3,4" when only two niches are listed invites a selection that
    # cannot work and then blames the person for making it.
    example = ",".join(str(i) for i in range(1, min(len(keys), 3) + 1))

    # Three attempts rather than one. Dropping back to the main menu on a
    # mistyped digit means re-navigating the whole option to fix a comma.
    for attempt in range(3):
        pick = input(f"\n  Compare which? (e.g. {example} — Enter for all "
                     f"{len(keys)}): ").strip()
        if not pick:
            break   # Enter = keep all

        chosen, bad = [], []
        for part in pick.replace(" ", ",").split(","):
            if not part:
                continue
            if part.isdigit() and 1 <= int(part) <= len(keys):
                k = keys[int(part) - 1]
                if k not in chosen:
                    chosen.append(k)
            else:
                bad.append(part)

        if bad:
            print(f"  Not on the list: {', '.join(bad)} "
                  f"(pick between 1 and {len(keys)}).")
        elif len(chosen) == 1:
            only = _cmp.split_window(chosen[0])[0]
            print(f"  That is one niche ({only}). A comparison needs at "
                  f"least two —\n  it measures niches against each other, "
                  f"so there is nothing to\n  measure {only} against.")
            print(f"  Add another number, or press Enter to use all "
                  f"{len(keys)}.")
        elif len(chosen) >= 2:
            keys = chosen
            break

        if attempt == 2:
            print("  Cancelled — returning to the menu.")
            return

    mismatch = _cmp.window_mismatch(keys)
    if mismatch:
        wins = ", ".join(sorted(mismatch["windows"]))
        print(f"\n  ⚠️  Mixed windows in this selection ({wins}).")
        print("      A shorter window always looks narrower, because its")
        print("      videos have had less time to separate. The niche on")
        print("      the shorter window will understate its own spread.")
        if input("      Compare anyway? [y/N]: ").strip().lower() != "y":
            print("      Cancelled. Rebuild them onto a common window, or")
            print("      pick a set that already shares one.")
            return

    res = _cmp.export_comparison(keys=keys)
    if res.get("error"):
        print("  Nothing to compare.")
        return
    df = _cmp.build_table(keys=keys)
    print()
    print(f"  {'niche':<24}{'window':>8}{'videos':>8}{'typical':>10}"
          f"{'top 10%':>11}{'spread':>9}")
    for r in df.itertuples():
        tv = f"{r.typical_views:,.0f}" if pd.notna(r.typical_views) else "—"
        t10 = f"{r.top10_views:,.0f}" if pd.notna(r.top10_views) else "—"
        sp = f"{r.spread:.1f}x" if pd.notna(r.spread) else "—"
        v = f"{r.videos:,}" if r.videos else "—"
        print(f"  {r.niche[:23]:<24}{str(r.window):>8}{v:>8}{tv:>10}"
              f"{t10:>11}{sp:>9}")
    print()
    if res.get("calibrated"):
        print("  Calibrated thresholds written to the report.")
    else:
        print("  Need 5+ niches before thresholds can be derived from")
        print("  evidence rather than assumption.")
    print(f"  → {res['md']}")

    # The table says how the niches DIFFER. It cannot say which to enter,
    # because that needs the joint distribution (views crossed with
    # channel size and with age) which metadata does not store. The
    # verdict re-reads videos.csv to answer it.
    deep = _dispatch_verdict(keys, mismatch)
    if deep:
        print(f"  → {deep}")

    _dispatch_crossniche(keys)


def _dispatch_crossniche(keys):
    """
    Offered at the end of option 10 — market pillars (demand/supply/
    momentum/opportunity) graded against the OTHER niches selected,
    rather than against each niche's own data.

    Grading a niche against itself is arithmetic, not measurement: a
    niche's own median sits at percentile 50 of itself by construction,
    its own P90 at percentile 90, and the demand tail weight blends
    those into the same number for every niche regardless of what the
    market actually looks like. Verified on eleven real niches: all
    eleven returned identical pillar scores. crossniche.py fixes this by
    grading each niche against the others (leave-one-out).
    """
    if len(keys) < 4:
        # calibration.py's TIER2_POOL_N context: fewer than ~4 niches
        # makes "typical niche in the pool" too thin an idea to lean on,
        # and crossniche.MIN_POOL_NICHES enforces the same floor.
        return
    print("\n  Market pillars (demand/supply/momentum/opportunity) can "
          "also be graded\n  against each OTHER selected niche, rather "
          "than against itself — no API\n  calls, reads what is already "
          "on disk.")
    if input(f"  Run it for these {len(keys)} niche(s)? [y/N]: ").strip().lower() != "y":
        return
    import crossniche as _xn
    out = _xn.sweep_selected(keys)
    if out.get("error"):
        print(f"  Skipped: {out['error']}")
        return
    print()
    t = out["table"].sort_values("opportunity", ascending=False)
    print(f"  {'niche':<28}{'demand':>9}{'supply':>9}{'opp':>7}{'grade':>7}")
    for r in t.itertuples():
        print(f"  {r.niche[:27]:<28}{r.demand:>9.1f}{r.supply:>9.1f}"
              f"{r.opportunity:>7.1f}{r.grade:>7}")
    print(f"\n  Bands are relative to THIS pool of {len(keys)} niches and "
          f"will shift as more are added — see calibration.py.")


def _dispatch_verdict(keys, mismatch=None):
    """Optional deep pass at the end of option 10. Returns a path or None."""
    import verdict as _v
    usable = [k for k in keys if _v.has_dataset(k)]
    if len(usable) < 2:
        return None
    print("\n  Deep entry verdict reads videos.csv for each niche and")
    print("  answers which is worth breaking into — entry access, floor")
    print("  composition, durability, effort, and revenue at three ad")
    print("  rates. No API calls, a few seconds per niche.")
    if input(f"  Run it for {len(usable)} niche(s)? [y/N]: ").strip().lower() != "y":
        return None
    out = _v.export_verdict(usable, mismatch=mismatch)
    if out.get("error"):
        print(f"  Could not build a verdict: {out['error']}")
        return None
    if out.get("skipped"):
        print(f"  Skipped (dataset too thin): {', '.join(out['skipped'])}")
    print(f"\n  {_v.verdict_text(out['result'])}")
    print(f"  → {out['csv']}")
    return out["html"]


def _dispatch_trends():
    """
    Option 9 — compare the live dataset against the last archived build.

    No API calls: every rebuild already archives the previous dataset to
    history/, and this reads one. The payoff is the only measured (rather
    than inferred) demand figure the engine produces — actual views
    gained between two observations, which separates a video still being
    watched from one that stopped months ago.
    """
    niche = _pick_niche()
    if not niche:
        print("  (no niche given — cancelled)")
        return
    if not _require_database(niche):
        return

    snaps = trends_mod.list_snapshots(niche)
    if not snaps:
        print(f"\n  No archived builds for {niche!r} yet.")
        print("  History is written when you REBUILD a niche — the previous")
        print("  dataset is moved to history/ before the new one lands. Run")
        print("  option 1 again in a few days and this will have something")
        print("  to compare against.")
        return

    print(f"  {len(snaps)} archived build(s): "
          + ", ".join(s["date"] for s in snaps[-4:]))
    df = collector.load_database(niche)
    res = trends_mod.export_trend_report(niche, df)
    if res.get("error"):
        msgs = {"no_history": "No archived builds found.",
                "unreadable": "The most recent archive could not be read.",
                "incomparable": "Snapshots have no overlapping videos."}
        print(f"  {msgs.get(res['error'], res['error'])}")
        return

    c = res["_comparison"]
    print(f"\n  {c['matched']:,} videos present in both snapshots"
          + (f", {c['days_between']:.0f} days apart" if c.get("days_between") else ""))
    if c.get("total_gained"):
        print(f"  Niche gained {c['total_gained']:,} measured views")
    if "still_climbing" in c:
        print(f"  {c['still_climbing']} still climbing · "
              f"{c['cooled_off']} cooled off")
    if not c.get("reliable"):
        print("  ⚠️  Little separation between builds — treat as indicative")
    for k, v in res.items():
        if not k.startswith("_"):
            print(f"    {k:14s} → {v}")


def _dispatch_channel():
    """
    Option 8 — take one channel apart.

    Costs roughly 13 units for a 300-video channel, versus 13,000 for a
    niche build, because a channel's catalogue comes from its uploads
    playlist (1 unit per 50 videos) rather than from search (100 per
    call). Pasting an @handle or a /channel/UC... URL is free to
    resolve; a plain channel name needs a search first and costs 100.
    """
    keys = _load_keys_or_exit()
    raw = input("Channel @handle, URL, or ID: ").strip()
    if not raw:
        print("  (nothing given — cancelled)")
        return

    kind, _ = channel_mod.parse_channel_input(raw)
    if kind == "search":
        print("  Note: that needs a name search (100 units). Pasting the "
              "@handle or /channel/UC... URL costs 1.")

    tracker = QuotaTracker()
    tracker.attach_keys(keys)
    print("  Resolving...")
    info = channel_mod.resolve_channel(keys, raw, tracker)
    if not info:
        print("  Channel not found. Check the handle or paste the full URL.")
        return

    print(f"  {info['title']} — {info['subscribers']:,} subs, "
          f"{info['video_count']:,} videos published")
    cap = min(info["video_count"], config.CHANNEL_MAX_VIDEOS)
    est = 1 + 2 * -(-cap // 50)
    print(f"  Will analyse up to {cap:,} videos (~{est} quota units)")
    if input("  Proceed? [Y/n]: ").strip().lower() == "n":
        print("  (cancelled)")
        return

    df = channel_mod.fetch_catalog(keys, info, tracker, max_videos=cap)
    if df.empty:
        print("  No videos retrieved.")
        return

    niche_for_compare = input(
        "  Compare against a niche you've built? (Enter to skip): ").strip()
    profile = channel_mod.analyze_channel(df, info,
                                          niche_name=niche_for_compare or None)
    paths = channel_mod.export_channel_report(df, profile, tracker)
    print(f"\n  Analysed {len(df):,} videos")
    for k, v in paths.items():
        print(f"    {k:18s} → {v}")
    report.print_quota(tracker)


def _dispatch_signals():
    """
    Option 7 — demand signals from outside the video data.

    Both sources are optional and independent: autocomplete needs no
    credentials at all, Reddit needs a subreddit list. Either can fail
    (both endpoints are unofficial) without affecting the other or the
    rest of the engine.
    """
    import signals as _signals
    niche = _pick_niche()
    if not niche:
        print("  (no niche given — cancelled)")
        return
    if not _require_database(niche):
        return
    df = collector.load_database(niche)

    print("\n  a) Search intent gaps (YouTube autocomplete — free, no key)")
    print("  b) Reddit early signals (needs subreddit names)")
    print("  c) Both")
    pick = input("Choose [a/b/c]: ").strip().lower()

    if pick in ("a", "c"):
        import config as _cfg
        build_seeds, src = _signals.suggest_seeds(niche, max_clusters=0)
        clusters = _signals.cluster_seeds(niche)
        both = build_seeds + [c for c in clusters if c not in build_seeds]
        RT = _cfg.SIGNALS_RECURSE_TOP

        def est(n_seeds, full, deep):
            n = n_seeds * (41 if full else 15) * ((1 + RT) if deep else 1)
            return n, n * 0.35 / 60

        # Every preset is priced before you pick it. The cost is driven
        # by seeds x variants x recursion, all three of which multiply,
        # so an unpriced menu makes a 24-minute run look identical to a
        # 30-second one.
        opts = [
            ("1", "Quick — build seeds, no a-z", build_seeds, False, False),
            ("2", "Standard — build seeds, full a-z", build_seeds, True, False),
            ("3", "Deep — build seeds, a-z + recursion", build_seeds, True, True),
            ("4", "Everything — build + cluster seeds, a-z + recursion", both, True, True),
            ("5", "Clusters only — cluster topics, full a-z", clusters, True, False),
        ]
        print(f"\n  Build seeds ({len(build_seeds)}): {', '.join(build_seeds)}")
        if clusters:
            print(f"  Cluster topics ({len(clusters)}): {', '.join(clusters[:8])}")
        else:
            print("  Cluster topics: none — run option 3 first to get them")
        print()
        for key, label, sds, full, deep in opts:
            if not sds:
                print(f"  {key}) {label:<52} unavailable")
                continue
            n, mins = est(len(sds), full, deep)
            print(f"  {key}) {label:<52} {n:>6,} lookups  ~{mins:>4.1f} min")
        print("  6) Custom seeds")

        choice = input("\nChoose [1-6]: ").strip()
        stem, title = "intent_gaps", None
        if choice == "6":
            raw = input(f"Seeds, comma-separated (up to "
                        f"{_cfg.SIGNALS_MAX_SEEDS}): ").strip()
            seeds = [s.strip() for s in raw.split(",")
                     if s.strip()][:_cfg.SIGNALS_MAX_SEEDS]
            if not seeds:
                print("  (no seeds given — cancelled)")
                return
            full = input("Full a-z expansion? [Y/n]: ").strip().lower() != "n"
            deep = input("Recurse? [y/N]: ").strip().lower() == "y"
        else:
            match = next((o for o in opts if o[0] == choice), None)
            if not match:
                print("  (unrecognised choice — cancelled)")
                return
            _, _, seeds, full, deep = match
            if not seeds:
                # Cluster-only was chosen with no clusters on disk. Say
                # so plainly and return rather than silently substituting
                # the niche name and mislabelling the output.
                print("\n  No breakout clusters found for this niche.")
                print("  Run option 3 (discover opportunities) first — it "
                      "writes the cluster file this reads.")
                return
            if choice == "5":
                stem, title = "cluster_intent", "Cluster search intent"

        n, mins = est(len(seeds), full, deep)
        print(f"\n  {len(seeds)} seed(s), ~{n:,} lookups, ~{mins:.1f} min. "
              f"Autocomplete is free — this is wall-clock time only.")
        if mins > 5 and input("  Proceed? [Y/n]: ").strip().lower() == "n":
            print("  (cancelled)")
            return
        try:
            res = _signals.export_intent_report(seeds, df, niche,
                                                alphabet=full,
                                                depth=2 if deep else 1,
                                                stem=stem, title=title)
            if res:
                print(f"\n  {res['queries']:,} queries harvested, "
                      f"{res['unserved']} with no matching video title")
                print(f"  intent gaps  → {res['md']}")
        except Exception as e:
            print(f"  (intent harvest failed: {type(e).__name__}: {e})")

    if pick in ("b", "c"):
        raw = input("\nSubreddits, comma-separated (e.g. stocks, investing, "
                    "wallstreetbets): ").strip()
        if not raw:
            print("  (no subreddits given — skipped)")
        else:
            listing = input("Listing [hot/new/rising] (Enter for hot): "
                            ).strip().lower() or "hot"
            if listing not in ("hot", "new", "rising"):
                listing = "hot"
            print()
            try:
                res = _signals.export_reddit_report(raw, df, niche,
                                                    listing=listing)
                if res:
                    print(f"\n  {res['posts']:,} posts read, "
                          f"{res['signals']} candidate terms")
                    print(f"  reddit signals → {res['md']}")
            except Exception as e:
                print(f"  (reddit fetch failed: {type(e).__name__}: {e})")


def _dispatch_thumbnail_sheet():
    """
    Interactive Option 6 (thumbnail contact sheet creator).

    Two entry points, matching the module's two paths:
      A) From a curated list — a paste of video IDs (or a filepath).
      B) Top-N from an existing breakout export.

    Neither path calls the YouTube API for data — they read what
    option 3 already wrote to disk. A network call is still made to
    fetch the thumbnail images themselves.
    """
    import thumbnail_sheet
    niche = _pick_niche()
    if not niche:
        print("  (no niche given — cancelled)")
        return
    if not _require_database(niche):
        return

    print("\n  a) Curated list — I have video IDs to render (from an AI or manual)")
    print("  b) Top 20 from the breakout export (no curation)")
    pick = input("Choose [a/b]: ").strip().lower()

    try:
        if pick == "a":
            print("\nPaste video IDs (whitespace or comma separated),")
            print("OR give a path to a file containing IDs. End with an empty line:")
            buf = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if not line.strip():
                    break
                buf.append(line)
            ids = thumbnail_sheet.parse_id_input("\n".join(buf))
            if not ids:
                print("  (no IDs given — cancelled)")
                return
            print(f"\n  Fetching thumbnails for {len(ids)} videos...")
            result = thumbnail_sheet.build_from_ids(niche, ids)
        elif pick == "b":
            print(f"\n  Reading exports/{niche}/breakout.csv...")
            result = thumbnail_sheet.build_from_top_n(niche, n=thumbnail_sheet.TARGET_COUNT)
        else:
            print("  (unrecognised choice — cancelled)")
            return
    except FileNotFoundError as e:
        print(f"\n  {e}")
        return

    print(f"\n  Rendered {result['n_rendered']} of {result['n_requested']} requested")
    if result.get("n_shorts_excluded"):
        print(f"  Excluded {result['n_shorts_excluded']} Shorts from the source")
    if not result.get("duration_verifiable"):
        print("  ⚠️  Source lacked duration data — could not verify Shorts exclusion")
    if result.get("missing_ids"):
        print(f"  {len(result['missing_ids'])} ID(s) not found in source (rendered as blanks)")
    if result.get("sheet"):
        print(f"\n  Contact sheet → {result['sheet']}")
    else:
        print(f"\n  Individual thumbnails → {result['thumbnails_dir']}")
        print("  (install Pillow to get a stitched contact sheet)")


def interactive():
    """
    Menu loop with an OUTER safety net.

    Why a catch-all here and nowhere else: unhandled exceptions from inside
    a menu action (a fresh bug, an OS-level surprise, a corrupted file we
    haven't seen before) shouldn't drop the user back to the shell mid-
    session. This is the ONE place the "never crash" rule applies — every
    other module still fails loudly, which is how you find bugs to fix.
    Ctrl-C still exits, as expected.
    """
    # One-time rename of bare dataset filenames to the namespaced form.
    # Explicit and at startup, rather than lazily inside a path lookup:
    # moving a user's files as a side effect of what reads like a
    # read-only call is the kind of surprise that costs trust. Safe to
    # run every launch — already-renamed files are skipped and an
    # existing destination is never overwritten.
    try:
        import paths as _pth
        n = _pth.migrate_dataset_names(config.DATASETS_DIR, verbose=True)
        if n:
            print(f"  ({n} dataset file(s) renamed to include their niche)")
    except Exception:
        pass

    # Backfill summary percentiles that older builds predate (P75, added
    # for the calibrated spread reading). Metadata is a cache derived
    # from videos.csv, so every number needed is already on disk — this
    # costs no quota and no re-collection. Only missing keys are added;
    # measured values are never overwritten.
    try:
        n = collector.backfill_all_metadata(verbose=False)
        if n:
            print(f"  ({n} niche(s) updated with percentiles computed from "
                  f"existing data — no API calls)")
    except Exception:
        pass

    while True:
        try:
            choice = _menu()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        try:
            if _dispatch(choice) == "quit":
                print("Bye.")
                return
        except KeyboardInterrupt:
            print("\n(Cancelled — back to menu.)")
        except FileNotFoundError as e:
            print(f"\nMissing data: {e}")
            print("Run option 1 to build the niche database first.")
        except PermissionError as e:
            print(f"\nPermission denied: {e}")
            print("Close any programs holding the file open and try again.")
        except Exception as e:
            # Friendlier messaging for quota exhaustion, which is a
            # user-recoverable condition (wait for daily reset) rather
            # than a bug to report. Checked by TYPE NAME rather than
            # `isinstance` so we don't have to import api_keys' internal
            # exception here, and by content for the raw HttpError case
            # where the retry loop couldn't rescue us.
            err_repr = f"{type(e).__name__}: {e}"

            # A dropped connection is a "check your wifi" problem, not a
            # bug report. It used to print a 40-line traceback through
            # httplib2 and googleapiclient internals, none of which tells
            # the user anything they can act on.
            network_signals = ("ServerNotFoundError", "getaddrinfo",
                               "gaierror", "Connection aborted",
                               "ConnectionResetError", "Temporary failure",
                               "Name or service not known",
                               "SSLError", "timed out", "TimeoutError")
            if any(s in err_repr for s in network_signals):
                print()
                print("=" * 60)
                print("⚠️  Could not reach YouTube — network problem, not a bug.")
                print()
                print("  Nothing was collected and no quota was spent; the")
                print("  failure happened before any request landed.")
                print()
                print("  Check: are you online? On a VPN? Behind a firewall")
                print("  that blocks googleapis.com?")
                print()
                print("  Re-run when the connection is back — the build")
                print("  starts clean.")
                print("=" * 60)
                print()
                return

            quota_signals = ("quotaExceeded", "rateLimitExceeded",
                             "dailyLimitExceeded", "userRateLimitExceeded",
                             "AllKeysExhausted", "Quota exceeded")
            if any(s in err_repr for s in quota_signals):
                print()
                print("=" * 60)
                print("⚠️  Daily API quota exhausted across all keys.")
                print(f"    ({err_repr[:120]}{'...' if len(err_repr) > 120 else ''})")
                print()
                print("Quotas reset at midnight Pacific Time (00:00 PT).")
                print("Options while you wait:")
                print("  · Option 3 or 4 — use data you've already collected")
                print("  · Option 5 — grade a niche you already built (no API calls)")
                print("=" * 60)
                print("Returning to menu — your data is untouched.\n")
            else:
                # Last-resort catch: something we didn't anticipate.
                import traceback
                print()
                print("=" * 60)
                print(f"⚠️  Unexpected error in option {choice!r}:")
                print(f"    {err_repr}")
                print("Full traceback (please copy if reporting):")
                print("-" * 60)
                traceback.print_exc()
                print("=" * 60)
                print("Returning to menu — your data is untouched.\n")


if __name__ == "__main__":
    # Documentation is a MAINTENANCE action, not an analysis one, so it
    # lives behind a flag rather than taking a slot in a menu that is
    # already nine items long.
    if "--docs" in sys.argv:
        import docs
        print("Regenerating documentation from docs.py...")
        docs.generate_all()
        print("\nEdit docs.py, not the generated files.")
        sys.exit(0)
    interactive()
