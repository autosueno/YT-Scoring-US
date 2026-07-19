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

def topic_dir(topic: str) -> str:
    return os.path.join(TOPICS_DIR, safe_name(topic))


def topic_videos_path(topic: str) -> str:
    return os.path.join(topic_dir(topic), "videos.csv")


def topic_metadata_path(topic: str) -> str:
    return os.path.join(topic_dir(topic), "metadata.json")


def topic_report_path(topic: str) -> str:
    """Where a topic's grade report is saved (report.py derives .csv/.html from this)."""
    return os.path.join(topic_dir(topic), "report.json")
