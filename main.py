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

def ensure_niche(api_key, niche_name, query=None, tracker=None, force=False):
    """
    Ensure a fresh-enough niche database exists, rebuilding if needed.

    Returns the niche DataFrame (loaded from disk or freshly built).
    """
    tracker = tracker or QuotaTracker()

    if force or _should_rebuild(niche_name):
        age = _niche_age_days(niche_name)
        reason = "forced" if force else (
            "not built yet" if age is None else f"{age:.1f} days old (max {NICHE_MAX_AGE_DAYS})"
        )
        print(f"Rebuilding niche {niche_name!r} — {reason}.")
        df = collector.build_niche_database(
            api_key, niche_name, query=query, tracker=tracker
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

    # 1. Niche baseline
    ensure_niche(api_key, niche_name, query=query, tracker=tracker)

    # 2. Topic data (always fresh — a grade should reflect right now)
    print(f"\nCollecting topic {topic!r}...")
    topic_df = collector.collect_topic(api_key, topic, tracker=tracker)
    if topic_df.empty:
        print(f"No data for topic {topic!r}. Aborting grade.")
        return None

    # 3. Grade
    baseline = load_baseline(niche_name)
    result = grade_topic(topic_df, baseline, topic)

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
        clusters = discover.build_clusters(scored)
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
    Grade a niche against its own baseline (no API calls) — useful for
    comparing multiple niches you've already built.
    """
    if not _require_database(niche_name):
        return None
    df = collector.load_database(niche_name)
    baseline = load_baseline(niche_name)
    result = grade_niche(df, baseline, niche_name)
    report.print_grade(result)
    try:
        path = dashboard.export_dashboard(result, niche_name)
        print(f"\nVisual dashboard → {path}")
    except Exception as e:
        print(f"  (dashboard skipped: {type(e).__name__}: {e})")
    return result


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


def _available_niches():
    """Names of niches that have a usable database on disk."""
    from config import DATASETS_DIR
    if not os.path.isdir(DATASETS_DIR):
        return []
    return sorted(
        d for d in os.listdir(DATASETS_DIR)
        if os.path.exists(os.path.join(DATASETS_DIR, d, "videos.csv"))
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
    clusters = discover.build_clusters(scored)
    seo_paths = seo.export_seo_report(scored, clusters, niche_name)
    print("\nExported SEO & packaging report:")
    for fmt, p in seo_paths.items():
        print(f"  {fmt:12s} → {p}")
    # Visual publishing calendars (weekly heatmap + monthly pattern)
    try:
        import timing as _timing
        _t = _timing.build_timing_report(scored)
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

    # Angle generation + transcript study. These convert FINDINGS into
    # things that can be scripted — the step percentile math cannot do.
    try:
        grade_result = grade_niche(df, baseline, niche_name)
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
    print(" 10) Quit")
    return input("\nChoose [1-10]: ").strip()


def _dispatch(choice):
    """Run one menu action. Isolated so a crash doesn't leave the loop."""
    if choice == "1":
        keys = _load_keys_or_exit()
        # Hard-reserve the tail keys so a large build cannot leave a later
        # topic grade with no quota. Verified failure mode: an option-1
        # build under soft reservation drained key #2, then option-2 hit
        # a bare 429 with no live keys left. This makes the reserve real.
        keys.reserve_for_topics(True)
        niche = input("Niche display name: ").strip()
        query = input(
            "Seed queries, comma-separated (Enter to reuse niche name)\n"
            "  e.g. finance, investing, economics, banking, stocks: "
        ).strip() or None
        force = input("Force rebuild even if fresh? [y/N]: ").strip().lower() == "y"
        df, tracker = ensure_niche(keys, niche, query=query, force=force)
        meta = collector.load_metadata(niche)
        if meta:
            report.print_niche_summary(meta, df)
        report.print_quota(tracker)

    elif choice == "2":
        keys = _load_keys_or_exit()
        # Topic grading starts on the reserved tail keys so a big niche
        # build can never leave grading without quota.
        keys.use_reserved_keys()
        niche = input("Niche name (baseline to grade against): ").strip()
        query = input("Niche seed queries (Enter to reuse niche name): ").strip() or None
        topic = input("Topic to grade: ").strip()
        formats = _prompt_formats()
        grade_topic_against_niche(keys, niche, topic, query=query,
                                  export_formats=formats)

    elif choice == "3":
        niche = input("Niche name: ").strip()
        discover_opportunities(niche)

    elif choice == "4":
        niche = input("Niche name: ").strip()
        show_niche(niche)

    elif choice == "5":
        niche = input("Niche name: ").strip()
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
        return "quit"

    else:
        print("Invalid choice.")


def _dispatch_trends():
    """
    Option 9 — compare the live dataset against the last archived build.

    No API calls: every rebuild already archives the previous dataset to
    history/, and this reads one. The payoff is the only measured (rather
    than inferred) demand figure the engine produces — actual views
    gained between two observations, which separates a video still being
    watched from one that stopped months ago.
    """
    niche = input("Niche display name: ").strip()
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
    niche = input("Niche display name: ").strip()
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
            raw = input("Seeds, comma-separated: ").strip()
            seeds = [s.strip() for s in raw.split(",") if s.strip()]
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
    niche = input("Niche display name: ").strip()
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
