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
    df = collector.load_database(niche_name)
    baseline = load_baseline(niche_name)
    paths = discover.export_breakouts(df, baseline, niche_name)
    print(f"\nExported full breakout ranking ({len(df)} videos):")
    for fmt, p in paths.items():
        print(f"  {fmt:4s} → {p}")
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


def interactive():
    print("=" * 60)
    print("  YouTube Intelligence Engine")
    print("=" * 60)
    print("  1) Build / refresh a niche database (multi-seed)")
    print("  2) Grade a topic against a niche")
    print("  3) Discover opportunities (breakout ranking + export)")
    print("  4) Show an existing niche summary")
    print("  5) Grade an existing niche (no API calls)")
    print("  6) Quit")

    choice = input("\nChoose [1-6]: ").strip()

    if choice == "1":
        keys = _load_keys_or_exit()
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
        print("Bye.")
        return

    else:
        print("Invalid choice.")


if __name__ == "__main__":
    try:
        interactive()
    except FileNotFoundError as e:
        print(f"\nMissing data: {e}")
        print("Run option 1 to build the niche database first.")
    except KeyboardInterrupt:
        print("\nInterrupted.")
