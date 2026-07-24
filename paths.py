"""
paths.py

Single source of truth for the pipeline's on-disk layout.

Every module that needs to locate a niche's or topic's files derives the
path from here, so the folder-naming rule (safe_name) and the directory
structure live in exactly one place. Change the layout once and every
module stays in sync automatically.

This module owns ONLY path construction — it never reads, writes, or
creates files. Callers do the I/O.

Layout (rooted at the directories defined in config.py):

    datasets/<safe_niche>/
        videos.csv
        statistics.csv
        metadata.json
        insights.json

    topics/<safe_topic>/
        videos.csv
        metadata.json
        report.json      (+ report.csv / report.html, written by report.py)

    history/<safe_niche>/<build-date>/
        (an archived copy of a previous datasets/<safe_niche>/ build)
"""

import os
import re

from config import DATASETS_DIR, TOPICS_DIR, HISTORY_DIR


# ============================================================
# NAME NORMALIZATION
# ============================================================

def safe_name(name: str) -> str:
    """
    Turn a display name into a filesystem-safe folder name.

    "Personal Finance!" -> "personal_finance_"  (lowercased, non-alnum
    runs collapsed to underscores). This is THE canonical rule — both the
    writer (collector) and the readers (baseline, report) call it, so a
    niche is always written to and read from the same folder.
    """
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower())


def export_dir_for(exports_root: str, niche_name: str,
                   section: str = None) -> str:
    """
    exports/<niche>/<section>/ — one folder per niche, subdivided by
    what the files are FOR.

    Bracketing by niche came first, because all four writers used to
    dump flat into exports/ and a few builds turned it into an unsorted
    pile spanning every niche. Sections came second, for the same
    reason one level down: a single niche now produces ~16 files
    (breakout x3, seo x2, two calendars, dashboard, three prompt files,
    intent gaps x2, reddit signals x2, plus thumbnail folders), and a
    flat folder of 16 mixed-purpose files is only marginally better
    than a flat folder of 60.

    Sections in use:
        market     the assessment — dashboard, grade reports
        breakouts  ranked video lists
        packaging  SEO/title/tag analysis, thumbnails, contact sheets
        timing     publishing calendars
        prompts    files meant to be pasted into an AI
        signals    external demand — autocomplete, Reddit

    section=None returns the niche root, which is still the right place
    for anything that spans sections.
    """
    d = os.path.join(exports_root, safe_name(niche_name))
    if section:
        d = os.path.join(d, section)
    os.makedirs(d, exist_ok=True)
    return d


# ============================================================
# NICHE PATHS
# ============================================================

def niche_dir(niche_name: str) -> str:
    return os.path.join(DATASETS_DIR, safe_name(niche_name))


def videos_path(niche_name: str) -> str:
    return os.path.join(niche_dir(niche_name), "videos.csv")


def statistics_path(niche_name: str) -> str:
    return os.path.join(niche_dir(niche_name), "statistics.csv")


def metadata_path(niche_name: str) -> str:
    return os.path.join(niche_dir(niche_name), "metadata.json")


def insights_path(niche_name: str) -> str:
    return os.path.join(niche_dir(niche_name), "insights.json")


# ============================================================
# HISTORY / ARCHIVE PATHS
# ============================================================

def history_dir(niche_name: str, date_str: str) -> str:
    """Archive destination for a previous build, dated by its own build date."""
    return os.path.join(HISTORY_DIR, safe_name(niche_name), date_str)


# ============================================================
# TOPIC PATHS
# ============================================================

def channel_export_dir(exports_root: str, channel_name: str,
                       section: str = None) -> str:
    """
    exports/_channels/<channel>/<section>/

    Channel teardowns live OUTSIDE the per-niche folders because they are
    a different unit of analysis. A niche folder answers "what is this
    market doing"; a channel folder answers "what is this one creator
    doing". Filing a teardown under whichever niche happened to be open
    at the time would make it impossible to find later. The underscore
    prefix keeps _channels sorted away from the niche folders.
    """
    d = os.path.join(exports_root, "_channels", safe_name(channel_name))
    if section:
        d = os.path.join(d, section)
    os.makedirs(d, exist_ok=True)
    return d


def topic_dir(topic: str) -> str:
    return os.path.join(TOPICS_DIR, safe_name(topic))


def topic_videos_path(topic: str) -> str:
    return os.path.join(topic_dir(topic), "videos.csv")


def topic_metadata_path(topic: str) -> str:
    return os.path.join(topic_dir(topic), "metadata.json")


def topic_report_path(topic: str) -> str:
    """Where a topic's grade report is saved (report.py derives .csv/.html from this)."""
    return os.path.join(topic_dir(topic), "report.json")
