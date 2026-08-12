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
    d = _nest(os.path.join(exports_root, EXPORT_KIND_NICHE), niche_name)
    if section:
        d = os.path.join(d, section)
    os.makedirs(d, exist_ok=True)
    return d


def search_export_dir(exports_root: str, niche_name: str,
                      section: str = None) -> str:
    """
    exports/searches/<niche>/<window>/ — autocomplete and Reddit output.

    Kept out of the niche folder because it is a different KIND of
    evidence: everything under niches/ is measured from videos that
    exist, while everything here is measured from outside them — what
    people type, what they post about. Mixing the two invites reading an
    inferred signal as a measured one.
    """
    d = _nest(os.path.join(exports_root, EXPORT_KIND_SEARCH), niche_name)
    if section:
        d = os.path.join(d, section)
    os.makedirs(d, exist_ok=True)
    return d


# ============================================================
# NICHE PATHS
# ============================================================

# A niche is identified by NAME + COLLECTION WINDOW. "finance" collected
# over 365 days and "finance" collected over 60 days are different
# datasets describing different populations, and writing both to
# datasets/finance/ means the second silently destroys the first.
#
# Rather than thread a window argument through ~64 call sites in a dozen
# modules, the window rides along inside the niche string as
# "finance@365d". Every existing caller keeps passing one string; the
# path helpers split it into a folder level. Names without "@" behave
# exactly as before, so datasets built before this change still load.
WINDOW_SEP = "@"


def split_window(niche_name: str):
    """'finance@365d' -> ('finance', '365d');  'finance' -> ('finance', None)"""
    if WINDOW_SEP in str(niche_name):
        base, win = str(niche_name).rsplit(WINDOW_SEP, 1)
        return base, win
    return niche_name, None


def with_window(niche_name: str, lookback_days) -> str:
    """Compose the storage key for a niche collected over a given window."""
    base, _ = split_window(niche_name)
    if not lookback_days:
        return base
    return f"{base}{WINDOW_SEP}{int(lookback_days)}d"


def display_name(niche_name: str) -> str:
    """Human-facing label: 'finance@60d' -> 'finance (60d window)'."""
    base, win = split_window(niche_name)
    return f"{base} ({win} window)" if win else base


# ============================================================
# SUBJECT-NAMESPACED FILENAMES
# ============================================================
# Every file carries the name of what it describes.
#
# The folder tree already separates subjects, so this looks redundant on
# disk — and on disk it is. It stops mattering the moment a file LEAVES
# the tree. Two niches both produce "statistics.csv"; attach them to the
# same message and the second overwrites the first, and Excel refuses to
# open two workbooks of the same name at all. The folder that made the
# name unique does not travel with the file.
#
# So the subject goes into the name itself:
#     statistics.csv  ->  ancient_history_365d_statistics.csv
#
# The window is included because a 365d and a 90d build of one niche are
# different datasets and collide just as badly as two different niches.

def subject_slug(subject: str) -> str:
    """'ancient_history@365d' -> 'ancient_history_365d'."""
    base, win = split_window(subject)
    slug = safe_name(base)
    return f"{slug}_{safe_name(win)}" if win else slug


def named(subject: str, filename: str) -> str:
    """'shorts.csv' -> 'ancient_history_365d_shorts.csv'."""
    slug = subject_slug(subject)
    if not slug:
        return filename
    # Idempotent: never prefix a name that already carries the subject.
    if filename.startswith(slug + "_"):
        return filename
    return f"{slug}_{filename}"


def locate(directory: str, filename: str):
    """
    Find a subject file in a directory when the subject is not known.

    Callers that WALK the tree (the comparison table, the history
    snapshot reader, the built-niche picker) discover folders rather than
    being handed a niche name, so they cannot call named(). They used to
    join the bare filename directly — which silently stopped matching the
    moment files were namespaced, and made every built niche vanish from
    option 10 while the data sat untouched on disk.

    Returns the bare name if present, else the first file ending in
    "_<filename>", else None.
    """
    p = os.path.join(directory, filename)
    if os.path.exists(p):
        return p
    if os.path.isdir(directory):
        suffix = "_" + filename
        for fn in sorted(os.listdir(directory)):
            if fn.endswith(suffix):
                return os.path.join(directory, fn)
    return None


def _subject_file(directory: str, subject: str, filename: str) -> str:
    """
    Where a subject's file lives, tolerating datasets built before the
    naming change.

    Prefers the namespaced name. Falls back to the bare legacy name if
    that is what exists, so an old build keeps loading and does not have
    to be re-collected — which would cost real quota for no gain. When
    neither exists we are writing, so the new name wins.
    """
    new = os.path.join(directory, named(subject, filename))
    if os.path.exists(new):
        return new
    legacy = os.path.join(directory, filename)
    if os.path.exists(legacy):
        return legacy
    return new


def export_file(exports_root: str, subject: str, section: str,
                filename: str) -> str:
    """Full path for one export, with the subject in the filename."""
    return os.path.join(export_dir_for(exports_root, subject, section),
                        named(subject, filename))


def migrate_dataset_names(datasets_root: str, verbose: bool = True) -> int:
    """
    Rename legacy bare-named dataset files to the namespaced form.

    Explicit rather than lazy: renaming as a side effect of a path
    lookup would move a user's files during what reads like a read-only
    operation. Run once at startup, prints what it did, and is safe to
    run repeatedly — a file already renamed is skipped, and an existing
    destination is never overwritten.
    """
    targets = ("videos.csv", "statistics.csv", "metadata.json",
               "insights.json", "shorts.csv")
    renamed = 0
    if not os.path.isdir(datasets_root):
        return 0
    for root, _dirs, files in os.walk(datasets_root):
        rel = os.path.relpath(root, datasets_root)
        if rel == ".":
            continue
        parts = rel.split(os.sep)
        # datasets/<niche>/<window>/ or the older datasets/<niche>/
        if len(parts) == 2 and parts[1].endswith("d") and parts[1][:-1].isdigit():
            subject = f"{parts[0]}{WINDOW_SEP}{parts[1]}"
        elif len(parts) == 1:
            subject = parts[0]
        else:
            continue
        for fn in files:
            if fn not in targets:
                continue
            dst = os.path.join(root, named(subject, fn))
            if os.path.exists(dst):
                continue
            try:
                os.rename(os.path.join(root, fn), dst)
                renamed += 1
                if verbose:
                    print(f"  renamed {fn} -> {os.path.basename(dst)}")
            except OSError:
                pass
    return renamed


# Top-level export folders, grouped by WHAT KIND of thing is being
# analysed rather than by name. Opening exports/ and seeing "finance"
# next to "_channels" mixed one kind of object with a category; now the
# first level is always the kind, so you know what you are looking at
# before you know what it is about.
EXPORT_KIND_NICHE = "niches"
EXPORT_KIND_TOPIC = "topics"
EXPORT_KIND_CHANNEL = "channels"
EXPORT_KIND_SEARCH = "searches"


def resolve_niche(niche_name: str):
    """
    Turn a bare niche name into the key that actually has data on disk.

    A person types 'space_exploration'. The data lives in
    datasets/space_exploration/365d/. The bare name resolves to the
    PARENT folder, which holds no videos.csv — so every loader reported
    "no niche database found" while the lister happily showed the niche
    as available, because the lister walks folders and the loader walks
    files. They disagreed about where a niche lives.

    This bridges them. Given a name with an explicit window (via '@' or
    already pointing at a window folder) it is returned unchanged. Given
    a bare name it finds the built windows beneath it and returns the
    LONGEST — a 365d build is the fuller measurement, and picking it by
    default matches what option 10 already does.

    Returns the resolved key (e.g. 'space_exploration@365d'), or the
    original name untouched when nothing is found, so existing "not
    built" error paths still fire naturally.
    """
    if WINDOW_SEP in str(niche_name):
        return niche_name
    base = safe_name(split_window(niche_name)[0])
    parent = os.path.join(DATASETS_DIR, base)
    # Already a complete build at the parent level (flat legacy layout)?
    if locate(parent, "videos.csv") or locate(parent, "metadata.json"):
        return niche_name
    if not os.path.isdir(parent):
        return niche_name
    wins = []
    for sub in os.listdir(parent):
        d = os.path.join(parent, sub)
        if not os.path.isdir(d):
            continue
        if locate(d, "videos.csv") or locate(d, "metadata.json"):
            days = int(sub[:-1]) if sub.endswith("d") and sub[:-1].isdigit() else -1
            wins.append((days, sub))
    if not wins:
        return niche_name
    _, best = max(wins)
    return f"{split_window(niche_name)[0]}{WINDOW_SEP}{best}"


def _nest(root: str, niche_name: str) -> str:
    """root/<niche>/<window>/ — the window becomes its own folder level."""
    base, win = split_window(niche_name)
    d = os.path.join(root, safe_name(base))
    if win:
        d = os.path.join(d, safe_name(win))
    return d


def niche_dir(niche_name: str) -> str:
    return _nest(DATASETS_DIR, niche_name)


# Each of these resolves through _subject_file, so a dataset written
# before the naming change still loads from its bare filename while new
# builds get the namespaced one. No re-collection, no lost quota.

def shorts_path(niche_name: str) -> str:
    """Shorts live beside the main dataset, not inside it."""
    return _subject_file(niche_dir(niche_name), niche_name, "shorts.csv")


def seed_query_history_path() -> str:
    """
    One shared file, at the DATASETS root, not inside any single niche's
    folder — deliberately, so it survives that niche's folder being
    deleted (tested this session: deleting a niche's whole folder is
    otherwise harmless and recoverable by rebuilding, EXCEPT that the
    real, hand-crafted seed queries used to build it were only ever
    stored inside that same folder's metadata.json, and would be lost
    with it). This is a small standing record, independent of any one
    build, of the last real seed queries used per niche.
    """
    from config import DATASETS_DIR
    return os.path.join(DATASETS_DIR, "_seed_query_history.json")


def videos_path(niche_name: str) -> str:
    return _subject_file(niche_dir(niche_name), niche_name, "videos.csv")


def statistics_path(niche_name: str) -> str:
    return _subject_file(niche_dir(niche_name), niche_name, "statistics.csv")


def metadata_path(niche_name: str) -> str:
    return _subject_file(niche_dir(niche_name), niche_name, "metadata.json")


def insights_path(niche_name: str) -> str:
    return _subject_file(niche_dir(niche_name), niche_name, "insights.json")


# ============================================================
# HISTORY / ARCHIVE PATHS
# ============================================================

def history_dir(niche_name: str, date_str: str) -> str:
    """Archive destination for a previous build, dated by its own build date."""
    return os.path.join(_nest(HISTORY_DIR, niche_name), date_str)


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
    d = os.path.join(exports_root, EXPORT_KIND_CHANNEL, safe_name(channel_name))
    if section:
        d = os.path.join(d, section)
    os.makedirs(d, exist_ok=True)
    return d


def topic_dir(topic: str) -> str:
    return os.path.join(TOPICS_DIR, safe_name(topic))


def topic_export_dir(exports_root: str, topic: str, section: str = None) -> str:
    """
    exports/_topics/<topic>/<section>/

    Topic grades used to write to exports/topic_<name>/ — a flat sibling
    of every niche folder, prefixed to disambiguate. That put topics and
    niches in the same namespace, so a topic called "investing" sat right
    next to the "investing" niche with only the prefix telling them
    apart. Filing topics under their own _topics/ parent keeps the two
    kinds of analysis visibly separate, the same way channel teardowns
    live under _channels/. The underscore sorts them away from niches.
    """
    d = os.path.join(exports_root, EXPORT_KIND_TOPIC, safe_name(topic))
    if section:
        d = os.path.join(d, section)
    os.makedirs(d, exist_ok=True)
    return d


def topic_videos_path(topic: str) -> str:
    return _subject_file(topic_dir(topic), topic, "videos.csv")


def topic_metadata_path(topic: str) -> str:
    return _subject_file(topic_dir(topic), topic, "metadata.json")


def topic_report_path(topic: str) -> str:
    """Where a topic's grade report is saved (report.py derives .csv/.html from this)."""
    return _subject_file(topic_dir(topic), topic, "report.json")
