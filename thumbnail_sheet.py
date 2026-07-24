"""
thumbnail_sheet.py — Option 7: Thumbnail Contact Sheet Creator

TWO INPUT PATHS, ONE OUTPUT DISCIPLINE
--------------------------------------
Both paths produce the same shape of output: 20 long-form thumbnails
tiled into one file, saved into the niche's exports folder.

Path A - CURATED LIST
    The user (or an AI they consulted) supplies a list of video IDs.
    Every ID gets rendered, in the order provided. Blanks are shown
    with the ID visible when a thumbnail cannot be fetched, so the
    correspondence between the input list and the output grid is never
    silently broken.

Path B - TOP-N FROM AN EXISTING BREAKOUT EXPORT
    Read `exports/<niche>/breakout.csv` and take the first 20 rows.
    Zero AI, zero re-ranking. This is the "quick look at what the
    breakout export already surfaced, in picture form" path.

WHY SHORTS ARE FILTERED AT RENDER TIME
--------------------------------------
Shorts are excluded unconditionally, both paths, even though they were
already filtered upstream. That upstream filter is about "should this
data enter the dataset at all"; this filter is about "does a Short's
thumbnail teach us anything about thumbnail effectiveness". A Short's
views come from the Shorts feed autoplay, not from a click on the
thumbnail, so a Short's success tells you next to nothing about visual
packaging. Including any Short here would pollute the pattern the
whole exercise exists to expose. A visible note reports how many were
dropped, so the exclusion is not hidden — just not asked about every
time.

WHY THE AI CURATION PROMPT LIVES IN THE BREAKOUT EXPORT
--------------------------------------------------------
Every discovery run writes `curation_prompt.md` alongside the other
breakout files. It contains the breakout list in a clean format plus a
ready-to-paste instruction block for the user's chat model of choice.
The user pastes the prompt, gets back a list of video IDs, comes here
with the list. The engine never calls an AI itself — the loop is
closed by the human, on purpose, so the runtime stays deterministic.
"""

import os
from typing import Iterable, List, Optional

import pandas as pd

from config import EXPORTS_DIR, EXCLUDE_SHORTS_UNDER_SECONDS
from paths import export_dir_for


TARGET_COUNT = 20


# ============================================================
# DATA LOADERS
# ============================================================

def _read_breakout_csv(niche_name: str) -> pd.DataFrame:
    """
    Load `exports/<niche>/breakout.csv` written by discover.export_breakouts.

    Kept lenient about missing columns: some legacy exports predate the
    `duration_seconds` column, and we would rather render a slightly
    weaker sheet than crash a user's inspection because their old build
    lacks one field. Missing duration means we cannot detect Shorts —
    we report that as a caveat rather than refuse to run.
    """
    path = os.path.join(export_dir_for(EXPORTS_DIR, niche_name, "breakouts"),
                        "breakout.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No breakout export found for {niche_name!r}. Run option 3 "
            f"(discover opportunities) first — that produces the "
            f"breakout export this option reads from."
        )
    return pd.read_csv(path)


def _read_topic_videos(niche_name: str, topic: str) -> pd.DataFrame:
    """
    Load a topic sample from `topics/<topic>/videos.csv` for a topic
    graded under this niche. Used when the user wants a contact sheet
    of a graded topic rather than the niche's breakout discovery.
    """
    import collector as _collector
    path = _collector._topic_videos_path(topic)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No videos found for topic {topic!r}. Run option 2 first."
        )
    return pd.read_csv(path)


# ============================================================
# FILTERING
# ============================================================

def _drop_shorts(df: pd.DataFrame) -> tuple[pd.DataFrame, int, bool]:
    """
    Remove Shorts. Returns (filtered_df, n_dropped, could_check).

    could_check is False when duration_seconds is missing from the
    export — the caller uses that to tell the user "we could not
    verify these are all long-form" rather than silently claiming
    a clean sheet.
    """
    if "duration_seconds" not in df.columns:
        return df, 0, False
    secs = pd.to_numeric(df["duration_seconds"], errors="coerce")
    # Keep rows with unknown duration: dropping them would silently
    # bias against videos whose contentDetails failed to fetch.
    mask = (secs >= EXCLUDE_SHORTS_UNDER_SECONDS) | secs.isna()
    dropped = int((~mask).sum())
    return df[mask].reset_index(drop=True), dropped, True


# ============================================================
# SELECTION
# ============================================================

def _select_top(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """
    Take the first n rows. Deliberately trivial — the breakout export
    is already ranked by breakout_score, so "top-N from the file"
    means the first N. Doing anything cleverer here would silently
    re-rank on assumptions the user cannot see.
    """
    return df.head(n).reset_index(drop=True)


def _select_by_ids(df: pd.DataFrame, ids: Iterable[str]) -> pd.DataFrame:
    """
    Pick rows matching the user's IDs, IN THE ORDER THEY PROVIDED THEM.

    A DataFrame filter with .isin() would reorder to the DataFrame's
    order, which loses the caller's ranking intent. The whole point of
    a curated list is that the order carries meaning.
    """
    if "video_id" not in df.columns:
        raise ValueError("Source data has no video_id column.")
    by_id = df.set_index("video_id", drop=False)
    rows = []
    for vid in ids:
        vid = vid.strip()
        if not vid:
            continue
        if vid in by_id.index:
            rows.append(by_id.loc[vid].to_dict())
        else:
            # Placeholder row so the grid position is preserved and the
            # user can see which requested ID was not found.
            rows.append({"video_id": vid, "title": "(not found in source)",
                         "channel_title": "", "thumbnail_url": ""})
    return pd.DataFrame(rows)


# ============================================================
# RENDERING
# ============================================================

def _download_thumbnails(df: pd.DataFrame, out_dir: str) -> List[dict]:
    """
    Fetch each thumbnail to disk. Failures leave a placeholder entry
    with local_file=None, so the sheet can render a labelled blank in
    that grid slot instead of silently collapsing.
    """
    import urllib.request
    os.makedirs(out_dir, exist_ok=True)
    items = []
    for i, r in df.iterrows():
        entry = {
            "position": i + 1,
            "video_id": str(r.get("video_id", "")),
            "title": str(r.get("title", "")),
            "channel_title": str(r.get("channel_title", "")),
            "local_file": None,
        }
        url = r.get("thumbnail_url")
        if isinstance(url, str) and url.strip():
            path = os.path.join(out_dir, f"{i+1:02d}.jpg")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp, open(path, "wb") as f:
                    f.write(resp.read())
                entry["local_file"] = path
            except Exception as e:
                entry["error"] = f"{type(e).__name__}"
        items.append(entry)
    return items


def _build_contact_sheet(items: List[dict], sheet_path: str,
                         header_text: Optional[str] = None) -> Optional[str]:
    """
    Tile the fetched thumbnails into one image. Missing thumbnails
    render as a dark labelled placeholder showing the video_id, so
    the grid stays aligned with the input list.

    Requires Pillow. If Pillow is missing, we return None and the
    caller falls back to the raw individual thumbnails on disk.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not items:
        return None

    cols = 4
    rows_n = -(-len(items) // cols)
    tw, th, pad, label_h = 320, 180, 8, 18
    header_h = 42 if header_text else 0

    sheet = Image.new(
        "RGB",
        (cols * (tw + pad) + pad,
         header_h + rows_n * (th + label_h + pad) + pad),
        (18, 18, 20),
    )
    draw = ImageDraw.Draw(sheet)

    if header_text:
        draw.text((pad + 4, 12), header_text, fill=(220, 220, 225))

    for i, item in enumerate(items):
        x = pad + (i % cols) * (tw + pad)
        y = header_h + pad + (i // cols) * (th + label_h + pad)
        fp = item.get("local_file")
        if fp and os.path.exists(fp):
            try:
                im = Image.open(fp).convert("RGB").resize((tw, th))
                sheet.paste(im, (x, y))
            except Exception:
                # Corrupted download — treat as if it failed
                fp = None
        if not fp or not os.path.exists(fp):
            # Draw a placeholder tile so the caller can see WHICH slot
            # is empty and cross-reference to their input list.
            draw.rectangle((x, y, x + tw, y + th), fill=(32, 32, 38),
                           outline=(80, 80, 90))
            msg = f"#{item['position']}  no thumbnail"
            draw.text((x + 10, y + 10), msg, fill=(160, 160, 168))
            vid = item.get("video_id", "")
            if vid:
                draw.text((x + 10, y + 30), vid[:36], fill=(120, 120, 128))
        draw.text((x + 3, y + th + 3), f"#{item['position']}", fill=(200, 200, 205))

    sheet.save(sheet_path, quality=88)
    return sheet_path


# ============================================================
# PUBLIC ENTRY POINTS
# ============================================================

def build_from_top_n(niche_name: str, n: int = TARGET_COUNT,
                     source: str = "breakout") -> dict:
    """
    Path B: take the top N from an existing export, drop Shorts, render.

    source="breakout"  -> exports/<niche>/breakout.csv  (from option 3)
    source="topic:<t>" -> topics/<t>/videos.csv         (from option 2)
    """
    if source.startswith("topic:"):
        topic = source.split(":", 1)[1]
        df = _read_topic_videos(niche_name, topic)
        label = f"topic {topic!r}"
    else:
        df = _read_breakout_csv(niche_name)
        label = "breakouts"

    total_in = len(df)
    filtered, dropped, could_check = _drop_shorts(df)
    picked = _select_top(filtered, n)

    out_dir = os.path.join(export_dir_for(EXPORTS_DIR, niche_name, "packaging"),
                           f"contact_sheet_{source.replace(':', '_')}")
    items = _download_thumbnails(picked, out_dir)
    header = f"{niche_name} — {label} — top {len(picked)} thumbnails"
    if dropped:
        header += f"  (excluded {dropped} Shorts)"
    elif not could_check:
        header += "  (could not verify all long-form: duration missing)"
    sheet_path = _build_contact_sheet(items, os.path.join(out_dir, "_contact_sheet.jpg"),
                                      header_text=header)
    return {
        "sheet": sheet_path,
        "thumbnails_dir": out_dir,
        "n_rendered": sum(1 for i in items if i.get("local_file")),
        "n_requested": len(picked),
        "n_shorts_excluded": dropped,
        "duration_verifiable": could_check,
        "total_in_source": total_in,
    }


def build_from_ids(niche_name: str, ids: List[str],
                   source: str = "breakout") -> dict:
    """
    Path A: user supplies a curated list of video IDs, we render in order.

    Missing IDs get placeholder tiles rather than being silently skipped,
    so a paste from an AI curation step stays traceable to its output.
    """
    if source.startswith("topic:"):
        topic = source.split(":", 1)[1]
        df = _read_topic_videos(niche_name, topic)
    else:
        df = _read_breakout_csv(niche_name)

    filtered, dropped, could_check = _drop_shorts(df)
    picked = _select_by_ids(filtered, ids)

    out_dir = os.path.join(export_dir_for(EXPORTS_DIR, niche_name, "packaging"),
                           "contact_sheet_curated")
    items = _download_thumbnails(picked, out_dir)
    header = f"{niche_name} — curated ({len(picked)} thumbnails)"
    if dropped:
        header += f"  (Shorts in source dropped before matching: {dropped})"
    sheet_path = _build_contact_sheet(items, os.path.join(out_dir, "_contact_sheet.jpg"),
                                      header_text=header)
    return {
        "sheet": sheet_path,
        "thumbnails_dir": out_dir,
        "n_rendered": sum(1 for i in items if i.get("local_file")),
        "n_requested": len(picked),
        "n_shorts_excluded": dropped,
        "duration_verifiable": could_check,
        "missing_ids": [i["video_id"] for i in items if not i.get("local_file")],
    }


# ============================================================
# AI CURATION PROMPT (written alongside breakout export)
# ============================================================

def write_curation_prompt(df: pd.DataFrame, niche_name: str,
                          n_candidates: int = 50) -> str:
    """
    Write curation_prompt.md into the niche's export dir.

    Contains the top N breakout candidates as a clean table and a
    ready-to-paste instruction block. The user takes this to their
    chat model, gets back a list of video IDs, then feeds those IDs
    into build_from_ids().

    We deliberately hand the AI text, never call it from here. The
    engine's runtime stays deterministic; the AI runs on the human's
    side of the loop.
    """
    filtered, _, _ = _drop_shorts(df)
    candidates = filtered.head(n_candidates)

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "prompts")
    path = os.path.join(out_dir, "curation_prompt.md")

    lines = [
        f"# Thumbnail curation prompt — {niche_name}",
        "",
        "## What to do",
        "",
        f"Paste everything below into your chat model. It will return "
        f"video IDs, one per line. Bring those IDs to Option 6 → "
        f"\"from curated list\" and the engine will render exactly those "
        f"{TARGET_COUNT} thumbnails as a contact sheet you can then hand "
        f"back to the same (or a multimodal) model for visual analysis.",
        "",
        "---",
        "",
        "## Prompt (paste from here)",
        "",
        f"I am building a thumbnail contact sheet for the niche "
        f"**{niche_name}**. Below are the top {len(candidates)} breakout "
        f"videos from a recent scan (Shorts already excluded).",
        "",
        f"Pick exactly **{TARGET_COUNT} videos** whose thumbnails are most "
        "worth studying for visual/packaging patterns. Prefer:",
        "",
        "- Small-channel breakouts (low sub count, high outlier_ratio) — "
        "these punched above their weight, so their packaging likely "
        "carried them.",
        "- Visual variety across your picks. If ten videos look like "
        "the same template, pick one representative, not five.",
        "- Thumbnails where the packaging plausibly drove the click, not "
        "the topic or the channel's existing audience.",
        "",
        f"Return exactly {TARGET_COUNT} video IDs, one per line, no "
        "extra text. Do not include Shorts even if any slipped in.",
        "",
        "### Candidate videos",
        "",
        "| video_id | title | channel | subs | outlier_ratio |",
        "|---|---|---|---|---|",
    ]
    for _, r in candidates.iterrows():
        title = str(r.get("title", ""))[:70].replace("|", "\\|")
        ch = str(r.get("channel_title", ""))[:22].replace("|", "\\|")
        subs = int(r["channel_subs"]) if pd.notna(r.get("channel_subs")) else "?"
        outlier = f"{float(r['outlier_ratio']):.2f}" if pd.notna(r.get("outlier_ratio")) else "?"
        lines.append(f"| {r.get('video_id', '')} | {title} | {ch} | {subs} | {outlier} |")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def parse_id_input(raw: str) -> List[str]:
    """
    Accept either a filesystem path (one ID per line) or a paste of IDs
    directly. Comma-separated, whitespace-separated, or newline-separated
    all work. Extracting IDs from URLs is intentionally NOT done —
    keeping the input format explicit avoids surprises where a
    paste-and-hope produces different behaviour than the user expected.
    """
    if raw and os.path.isfile(raw):
        with open(raw, "r", encoding="utf-8") as f:
            raw = f.read()
    # Split on any run of whitespace or commas
    import re
    parts = re.split(r"[\s,]+", raw.strip())
    return [p for p in parts if p]
