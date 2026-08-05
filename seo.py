"""
seo.py

Packaging intelligence: which TAGS, HASHTAGS and format choices distinguish
the videos that overperform from the ones that don't.

Design principle — LIFT, NOT FREQUENCY
--------------------------------------
The most common tag in a finance niche is "finance". That is vocabulary,
not advantage: it appears everywhere, including on videos that flopped.
What matters is the tag that appears disproportionately among BREAKOUTS:

    lift = P(tag | breakout video) / P(tag | any video)

A tag on 8% of all videos but 40% of breakouts has lift 5.0 — that is a
signal. A tag on 80% of everything has lift ~1.0 — that is noise. Minimum
support guards against a tag appearing on two lucky videos scoring lift 20.

HONEST CAVEAT ON TAGS
---------------------
YouTube has stated publicly that tags play a minimal role in search
ranking; titles, thumbnails and descriptions carry far more weight. So the
value here is NOT "copy these tags to rank higher". Tags are a semantic
FINGERPRINT — they reveal how successful videos position themselves, what
adjacent terms they claim, and which sub-topic they target. Use them to
understand positioning, not to game search.

Everything here is deterministic and diagnostic: nothing feeds the
demand / supply / momentum scoring.

Public API:
    tag_lift(df)                 -> tags ranked by breakout lift
    hashtag_lift(df)             -> hashtags ranked by breakout lift
    cluster_seo_profiles(df, cl) -> per-cluster tag/format profile
    build_seo_report(df, clusters) -> dict
    export_seo_report(df, clusters, niche) -> {md, json}
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

from config import (
    EXPORTS_DIR, SEO_BREAKOUT_PERCENTILE, SEO_MIN_TAG_SUPPORT,
    SEO_TOP_N, CLUSTER_WEAK_WORDS,
)
from insights import _STOPWORDS
from paths import export_dir_for
from paths import named as _named


HASHTAG_RE = re.compile(r"#(\w{2,40})")


# ============================================================
# EXTRACTION
# ============================================================

def _split_tags(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [t.strip().lower() for t in str(value).split("|") if t.strip()]


def extract_hashtags(text):
    """Hashtags from a title or description, lowercased, '#' stripped."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return []
    return [h.lower() for h in HASHTAG_RE.findall(str(text))]


def _breakout_mask(df):
    """
    Which rows count as 'breakout' for lift purposes.

    Uses breakout_score when discovery has already scored the frame,
    otherwise falls back to views_per_hour. Either way the cut is the
    SEO_BREAKOUT_PERCENTILE of this niche — always niche-relative.
    """
    col = "breakout_score" if "breakout_score" in df.columns else "views_per_hour"
    if col not in df.columns:
        return None, None
    cut = df[col].quantile(SEO_BREAKOUT_PERCENTILE / 100.0)
    return df[col] >= cut, col


# ============================================================
# LIFT ANALYSIS
# ============================================================

def _lift_table(df, extract_fn, min_support=None):
    """
    Generic lift computation over a per-row list of terms.

    Returns rows of {term, videos, breakout_videos, share_all,
    share_breakout, lift, avg_views_per_hour}, ranked by lift.
    """
    mask, _ = _breakout_mask(df)
    if mask is None or df.empty:
        return []

    min_support = min_support or SEO_MIN_TAG_SUPPORT
    n_all = len(df)
    n_break = int(mask.sum())
    if n_break == 0:
        return []

    all_counts = defaultdict(int)
    break_counts = defaultdict(int)
    vph_sums = defaultdict(float)
    has_vph = "views_per_hour" in df.columns

    for idx, row in df.iterrows():
        terms = set(extract_fn(row))
        is_break = bool(mask.loc[idx])
        for t in terms:
            all_counts[t] += 1
            if has_vph:
                v = row.get("views_per_hour")
                vph_sums[t] += 0.0 if (v is None or pd.isna(v)) else float(v)
            if is_break:
                break_counts[t] += 1

    rows = []
    for t, c in all_counts.items():
        if c < min_support:
            continue
        share_all = c / n_all
        share_break = break_counts[t] / n_break
        rows.append({
            "term": t,
            "videos": c,
            "breakout_videos": break_counts[t],
            "share_all": round(share_all, 4),
            "share_breakout": round(share_break, 4),
            "lift": round(share_break / share_all, 2) if share_all else None,
            "avg_views_per_hour": round(vph_sums[t] / c, 1) if has_vph else None,
        })

    rows.sort(key=lambda r: (r["lift"] or 0, r["breakout_videos"]), reverse=True)
    return rows


def tag_lift(df, top_n=None):
    """Tags ranked by how much they over-index among breakout videos."""
    rows = _lift_table(df, lambda r: _split_tags(r.get("tags")))
    return rows[: (top_n or SEO_TOP_N)]


def hashtag_lift(df, top_n=None):
    """
    Hashtags ranked by breakout lift.

    Hashtags live mostly in DESCRIPTIONS but also appear in titles; both
    are pooled, since a viewer/algorithm sees them the same way.
    """
    def extract(r):
        return extract_hashtags(r.get("title")) + extract_hashtags(r.get("description"))
    rows = _lift_table(df, extract)
    return rows[: (top_n or SEO_TOP_N)]


def generate_tags(df, target_count=15):
    """
    Produce a ready-to-paste tag list for a video you are about to make.

    tag_lift ANALYSES which existing tags correlate with breakouts. That
    is diagnostic — it tells you what is working — but it does not hand
    you a list to use. This does. It blends three sources so the result
    is neither all-generic nor all-obscure:

      1. HIGH-LIFT tags — tags that over-index on breakouts (from
         tag_lift). These are the differentiators, the reason to prefer
         one tag over another. Weighted most heavily.
      2. HIGH-COVERAGE tags — tags that simply appear on a large share of
         videos in this topic. These are table stakes: the algorithm
         expects them, and omitting them reads as off-topic. Included
         even at low lift.
      3. TITLE / NGRAM tags — the most common meaningful phrases in the
         topic's video titles, which are frequently strong tags that
         nobody has formally tagged yet.

    Returns {"tags": [...], "reasons": {tag: why}} so the export can
    explain each choice rather than emitting an unaccountable list.

    IMPORTANT and stated in the output: tags are a weak ranking signal on
    modern YouTube. This list helps a video be found for what it IS; it
    is not a growth lever. Title, thumbnail and topic choice dominate.
    """
    from collections import Counter
    from insights import _STOPWORDS, _tokenize

    chosen, reasons = [], {}

    # --- source 1: high-lift tags ---
    lifts = tag_lift(df, top_n=40)
    for row in lifts:
        term = row.get("term") or row.get("tag")
        if not term or term in chosen:
            continue
        if row.get("lift", 0) >= 1.3 and row.get("breakout_count", 0) >= 3:
            chosen.append(term)
            reasons[term] = (f"over-indexes on breakouts "
                             f"({row['lift']:.1f}x lift)")
        if len(chosen) >= target_count // 2:
            break

    # --- source 2: high-coverage tags (table stakes) ---
    tag_counts = Counter()
    for raw in df.get("tags", pd.Series(dtype=str)).fillna(""):
        for t in _split_tags(raw):
            tag_counts[t.strip().lower()] += 1
    n = max(len(df), 1)
    for term, c in tag_counts.most_common(30):
        if term and term not in chosen and c / n >= 0.15:
            chosen.append(term)
            reasons[term] = f"used on {c/n:.0%} of videos in this topic"
        if len(chosen) >= target_count - 3:
            break

    # --- source 3: common title phrases not yet tagged ---
    if len(chosen) < target_count and "title" in df.columns:
        phrase = Counter()
        for t in df["title"].fillna(""):
            words = [w for w in _tokenize(str(t))
                     if w not in _STOPWORDS and len(w) > 3]
            for i in range(len(words) - 1):
                phrase[f"{words[i]} {words[i+1]}"] += 1
        for term, c in phrase.most_common(20):
            if term not in chosen and c >= 3:
                chosen.append(term)
                reasons[term] = f"recurring title phrase ({c} videos)"
            if len(chosen) >= target_count:
                break

    return {"tags": chosen[:target_count], "reasons": reasons}


def export_tag_list(df, niche_name, out_dir=None) -> str:
    """Write tags.md — the generated list plus why each tag is there."""
    result = generate_tags(df)
    tags, reasons = result["tags"], result["reasons"]
    out_dir = out_dir or export_dir_for(EXPORTS_DIR, niche_name, "packaging")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, _named(niche_name, "tags.md"))

    L = [f"# Suggested tags — {niche_name}", ""]
    L.append("A ready-to-use tag set for a video on this topic, blended "
             "from three sources: tags that over-index on breakouts, tags "
             "that most videos here carry anyway, and common title phrases "
             "that make strong tags.")
    L.append("")
    L.append("> **Tags are a weak ranking signal on modern YouTube.** This "
             "helps a video be found for what it *is* — it is not a growth "
             "lever. Title, thumbnail and topic choice matter far more. "
             "Copy these to be correctly categorised, then stop thinking "
             "about tags.")
    L.append("")
    L.append("## Copy-paste")
    L.append("")
    L.append("```")
    L.append(", ".join(tags))
    L.append("```")
    L.append("")
    L.append("## Why each")
    L.append("")
    L.append("| tag | reason |")
    L.append("|---|---|")
    for t in tags:
        L.append(f"| {t} | {reasons.get(t, '')} |")
    L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path


# ============================================================
# PER-CLUSTER PROFILES
# ============================================================

def _pick_examples(sub, n, rank_col):
    """
    Choose example videos that actually teach something.

    Two rules, both learned from bad output:
      1. CHANNEL DIVERSITY — taking the top N by score routinely returned
         N videos from the same prolific channel (3 of 3 from one channel
         out of 30 in testing). That shows one creator's house style, not
         the cluster's pattern. We take each channel's best video first,
         then backfill.
      2. DEDUPLICATION — near-identical descriptions repeated verbatim
         waste space and teach nothing. Identical openings are skipped.
    """
    if sub.empty:
        return []
    ordered = sub.sort_values(rank_col, ascending=False) if rank_col in sub.columns else sub
    picked, seen_channels, seen_desc = [], set(), set()

    def _key(row):
        dv = row.get("desc_visible")
        if dv is None or (isinstance(dv, float) and pd.isna(dv)):
            dv = str(row.get("description", ""))[:150]
        return str(dv).strip().lower()[:80]

    # Pass 1: best video from each distinct channel.
    for _, r in ordered.iterrows():
        if len(picked) >= n:
            break
        ch = r.get("channel_id") or r.get("channel_title")
        if ch in seen_channels:
            continue
        k = _key(r)
        if k in seen_desc:
            continue
        seen_channels.add(ch); seen_desc.add(k); picked.append(r)

    # Pass 2: backfill if the cluster has few channels, still deduping.
    if len(picked) < n:
        for _, r in ordered.iterrows():
            if len(picked) >= n:
                break
            k = _key(r)
            if k in seen_desc:
                continue
            seen_desc.add(k); picked.append(r)
    return picked


def cluster_seo_profiles(df, clusters, per_cluster_tags=8, examples_per_cluster=6):
    """
    For each discovered topic cluster, the packaging profile of ITS videos:
    the tags/hashtags that cluster actually uses, format medians, and real
    worked examples.

    This is the directly actionable output — "if you make a video in this
    cluster, here is how the videos that worked were packaged."
    """
    if df.empty or not clusters:
        return []

    profiles = []
    for c in clusters:
        phrase = c.get("topic", "")
        if not phrase:
            continue
        # Use the cluster's OWN member indices when available. Re-finding
        # them by regex on the title was fragile and silently dropped
        # whole clusters from this report.
        idxs = c.get("video_indices")
        if idxs:
            sub = df.loc[[i for i in idxs if i in df.index]]
        else:
            pattern = phrase.replace(" ", r".{0,6}")
            sub = df[df["title"].fillna("").str.lower().str.contains(pattern, regex=True, na=False)]
        if sub.empty:
            continue

        tag_counts = defaultdict(int)
        hash_counts = defaultdict(int)
        for _, r in sub.iterrows():
            for t in set(_split_tags(r.get("tags"))):
                tag_counts[t] += 1
            for h in set(extract_hashtags(r.get("title")) + extract_hashtags(r.get("description"))):
                hash_counts[h] += 1

        def _top(counts):
            return [{"term": k, "videos": v}
                    for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
                    [:per_cluster_tags]]

        def _med(col):
            if col not in sub.columns:
                return None
            m = sub[col].median()
            return None if pd.isna(m) else float(m)

        # Real EXAMPLES, not just statistics. Asking an AI to "write a
        # description template" while handing it only median character
        # counts forces it to invent from nothing — it has never seen what
        # actually works in this niche. Titles were already exemplified via
        # the cluster's top_titles; descriptions were not. We include the
        # VISIBLE opening (~150 chars, the part shown before "Show more"),
        # which is both the SEO-critical portion and a short excerpt.
        rank_col = "breakout_score" if "breakout_score" in sub.columns else "views_per_hour"
        best = _pick_examples(sub, examples_per_cluster, rank_col)
        examples = []
        for br in best:
            dv = br.get("desc_visible")
            if dv is None or (isinstance(dv, float) and pd.isna(dv)):
                dv = str(br.get("description", ""))[:150]
            examples.append({
                "title": str(br.get("title", ""))[:110],
                "channel": str(br.get("channel_title", "")),
                "description_opening": str(dv).replace("\n", " ⏎ ")[:150],
                "chapters": int(br.get("desc_chapter_count", 0) or 0),
                "tags": _split_tags(br.get("tags"))[:10],
                "views_per_hour": (None if pd.isna(br.get("views_per_hour"))
                                   else round(float(br.get("views_per_hour")), 1)),
            })

        profiles.append({
            "topic": phrase,
            "cluster_score": c.get("cluster_score"),
            "videos": int(len(sub)),
            "unique_channels": c.get("unique_channels"),
            "small_creator_share": c.get("small_creator_share"),
            "top_tags": _top(tag_counts),
            "top_hashtags": _top(hash_counts),
            "median_tag_count": _med("tag_count"),
            "median_title_length": _med("title_length"),
            "median_description_length": _med("description_length"),
            "median_duration_seconds": _med("duration_seconds"),
            "median_chapter_count": _med("desc_chapter_count"),
            "chapters_rate": (float(sub["has_chapters"].mean())
                              if "has_chapters" in sub.columns else None),
            "captions_rate": (float(sub["has_captions"].mean())
                              if "has_captions" in sub.columns else None),
            "examples": examples,
        })
    return profiles


# ============================================================
# TITLE PATTERNS AMONG BREAKOUTS
# ============================================================

def title_word_lift(df, top_n=None):
    """
    Title WORDS that over-index among breakouts, weak words removed.

    Titles are the strongest controllable ranking/CTR lever, so this is
    usually more actionable than the tag table.
    """
    def extract(r):
        words = re.findall(r"[a-z][a-z']+", str(r.get("title", "")).lower())
        # Filter BOTH basic stopwords ("the", "are", "and" — these ride
        # along with any phrase and score meaningless lift) and the weak
        # filler words ("now", "best", "truth") used by clustering.
        return [w for w in words
                if w not in _STOPWORDS and w not in CLUSTER_WEAK_WORDS and len(w) > 2]
    return _lift_table(df, extract)[: (top_n or SEO_TOP_N)]


def structure_lift(df):
    """
    Which TITLE STRUCTURES and DESCRIPTION habits over-index among
    breakouts. Titles carry far more ranking/CTR weight than tags, so a
    structural read ("questions outperform here 2.4x") is usually the most
    actionable thing in this report.
    """
    mask, _ = _breakout_mask(df)
    if mask is None or df.empty or mask.sum() == 0:
        return []
    flags = [
        ("question titles", "title_is_question"),
        ("how-to / explainer titles", "title_is_howto"),
        ("listicle titles (numbered)", "title_is_listicle"),
        ("comparison titles (X vs Y)", "title_is_comparison"),
        ("colon-split titles", "title_has_colon"),
        ("bracketed titles [2026]", "title_has_bracket"),
        ("year in title", "title_has_year"),
        ("chapters in description", "has_chapters"),
        ("4+ chapters", "desc_timestamp_dense"),
        ("call-to-action in description", "desc_has_cta"),
        ("captions enabled", "has_captions"),
    ]
    rows = []
    for label, col in flags:
        if col not in df.columns:
            continue
        base = float(df[col].fillna(False).mean())
        top = float(df.loc[mask, col].fillna(False).mean())
        if base <= 0:
            continue
        rows.append({
            "pattern": label,
            "niche_rate": round(base, 4),
            "breakout_rate": round(top, 4),
            "lift": round(top / base, 2),
        })
    rows.sort(key=lambda r: r["lift"], reverse=True)
    return rows


def top_thumbnails(df, n=12):
    """
    Thumbnail URLs of the highest-scoring videos.

    THE ENGINE CANNOT ANALYSE THUMBNAILS — it has no vision. Thumbnails are
    plausibly the single biggest CTR lever, and this is the one major
    packaging factor we are structurally blind to. Exporting the URLs at
    least lets a human open them side by side, or lets a multimodal model
    look if the images are uploaded.
    """
    if "thumbnail_url" not in df.columns:
        return []
    col = "breakout_score" if "breakout_score" in df.columns else "views_per_hour"
    if col not in df.columns:
        return []
    top = df.nlargest(n, col)
    return [{"title": str(r.get("title", ""))[:90],
             "score": r.get(col),
             "channel": str(r.get("channel_title", "")),
             "thumbnail_url": r.get("thumbnail_url", "")}
            for _, r in top.iterrows() if r.get("thumbnail_url")]


def download_thumbnails(df, niche_name, n=12, make_contact_sheet=True,
                        out_dir_override=None):
    """
    Download the top-N thumbnails and (optionally) tile them into ONE
    contact-sheet image.

    Why this exists: an AI reading the exported Markdown sees only URL
    *text* — chat models cannot reliably fetch arbitrary links, so a list
    of thumbnail URLs is useless to them. What DOES work is handing a
    multimodal model actual image files. Downloading them locally turns an
    impossible ask into a drag-and-drop, and the contact sheet reduces
    twelve uploads to one.

    Requires Pillow for the contact sheet; degrades to plain downloads
    without it. Network failures are non-fatal — thumbnails are a bonus,
    never a reason to lose a report.
    """
    import urllib.request

    items = top_thumbnails(df, n)
    if not items:
        return {"files": [], "contact_sheet": None, "dir": None}

    base = out_dir_override or export_dir_for(EXPORTS_DIR, niche_name, "packaging")
    out_dir = os.path.join(base, "thumbnails")
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for i, it in enumerate(items, 1):
        url = it.get("thumbnail_url")
        if not url:
            continue
        path = os.path.join(out_dir, f"{i:02d}.jpg")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r, open(path, "wb") as f:
                f.write(r.read())
            files.append(path)
            it["local_file"] = path
        except Exception as e:
            print(f"    (thumbnail {i} failed: {type(e).__name__})")

    sheet = None
    if make_contact_sheet and files:
        try:
            from PIL import Image, ImageDraw
            cols = 4
            rows_n = -(-len(files) // cols)
            tw, th, pad, label_h = 320, 180, 8, 18
            sheet_img = Image.new(
                "RGB",
                (cols * (tw + pad) + pad, rows_n * (th + label_h + pad) + pad),
                (18, 18, 20))
            draw = ImageDraw.Draw(sheet_img)
            for i, fp in enumerate(files):
                try:
                    im = Image.open(fp).convert("RGB").resize((tw, th))
                except Exception:
                    continue
                x = pad + (i % cols) * (tw + pad)
                y = pad + (i // cols) * (th + label_h + pad)
                sheet_img.paste(im, (x, y))
                draw.text((x + 3, y + th + 3), f"#{i+1}", fill=(200, 200, 205))
            sheet = os.path.join(out_dir, _named(niche_name, "contact_sheet.jpg"))
            sheet_img.save(sheet, quality=88)
        except ImportError:
            print("    (Pillow not installed — skipping contact sheet)")
        except Exception as e:
            print(f"    (contact sheet failed: {type(e).__name__})")

    return {"files": files, "contact_sheet": sheet, "dir": out_dir}


# ============================================================
# REPORT
# ============================================================

def build_seo_report(df, clusters=None) -> dict:
    """Assemble the full SEO analysis as a plain dict."""
    import insights
    mask, score_col = _breakout_mask(df)
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "videos_analyzed": int(len(df)),
        "breakout_definition": (
            f"top {100 - SEO_BREAKOUT_PERCENTILE}% by "
            f"{score_col or 'views_per_hour'}"),
        "breakout_videos": int(mask.sum()) if mask is not None else 0,
        "packaging_contrast": insights.seo_summary(df),
        "tag_lift": tag_lift(df),
        "hashtag_lift": hashtag_lift(df),
        "title_word_lift": title_word_lift(df),
        "structure_lift": structure_lift(df),
        "top_thumbnails": top_thumbnails(df),
        "cluster_profiles": cluster_seo_profiles(df, clusters or []),
        "timing": (lambda: __import__("timing").build_timing_report(df))(),
    }


def _recommendations(rep) -> list:
    """
    Turn the contrast table into concrete, checkable statements.

    Generated by CODE, not AI: each line is a threshold comparison of the
    top decile against the niche median, emitted only where the gap is
    real. No generic "use good tags" filler.

    IMPORTANT — these are CORRELATIONS, not causal advice. "Top videos
    carry 18 tags" is an observation; it does not follow that adding tags
    causes success. The likelier explanation is common cause: creators who
    invest real effort write fuller descriptions AND add chapters AND tag
    carefully AND make better videos. Code cannot separate those. The
    report says so explicitly so the numbers are not mistaken for a recipe.
    """
    recs = []
    pc = rep.get("packaging_contrast") or {}

    def med(field, key="top_decile_median"):
        v = pc.get(field)
        return v.get(key) if isinstance(v, dict) else None

    def rate(field, key="top_decile_rate"):
        v = pc.get(field)
        return v.get(key) if isinstance(v, dict) else None

    t_top, t_all = med("tag_count"), med("tag_count", "niche_median")
    if t_top and t_all and t_top > t_all * 1.2:
        recs.append(f"Use about **{t_top:.0f} tags** — overperformers carry "
                    f"{t_top/t_all:.1f}x the niche median of {t_all:.0f}.")

    d_top, d_all = med("description_length"), med("description_length", "niche_median")
    if d_top and d_all and d_top > d_all * 1.2:
        recs.append(f"Write a **~{d_top:.0f}-character description** "
                    f"({d_top/d_all:.1f}x the niche median).")

    ch_top, ch_all = rate("has_chapters"), rate("has_chapters", "niche_rate")
    if ch_top and ch_all is not None and ch_top > max(ch_all * 1.3, 0.15):
        recs.append(f"**Add chapters/timestamps** — {ch_top:.0%} of top videos "
                    f"have them vs {ch_all:.0%} of the niche.")

    cap_top, cap_all = rate("has_captions"), rate("has_captions", "niche_rate")
    if cap_top and cap_all is not None and cap_top > max(cap_all * 1.3, 0.15):
        recs.append(f"**Enable captions** — {cap_top:.0%} of top videos have "
                    f"them vs {cap_all:.0%} of the niche.")

    du_top, du_all = med("duration_seconds"), med("duration_seconds", "niche_median")
    if du_top and du_all and abs(du_top - du_all) / du_all > 0.25:
        direction = "longer" if du_top > du_all else "shorter"
        recs.append(f"Target **~{du_top/60:.0f} minutes** — top videos run "
                    f"{direction} than the niche median of {du_all/60:.0f} min.")

    ti_top = med("title_length")
    if ti_top:
        note = " (stay under ~60 chars to avoid truncation in browse)" if ti_top > 60 else ""
        recs.append(f"Title length around **{ti_top:.0f} characters**{note}.")

    words = [r["term"] for r in rep.get("title_word_lift", [])[:8]]
    if words:
        recs.append("Title words that over-index among breakouts: "
                    + ", ".join(f"`{w}`" for w in words) + ".")

    tags = [r["term"] for r in rep.get("tag_lift", [])[:10]]
    if tags:
        recs.append("Highest-lift tags (positioning signal, not a ranking "
                    "hack): " + ", ".join(f"`{t}`" for t in tags) + ".")

    hashes = [r["term"] for r in rep.get("hashtag_lift", [])[:6]]
    if hashes:
        recs.append("Hashtags over-indexing among breakouts: "
                    + ", ".join(f"`#{h}`" for h in hashes) + ".")
    return recs


def _lift_table_md(rows, label, limit=15):
    if not rows:
        return f"\n_No {label} met the minimum support threshold._\n"
    out = [f"\n| {label} | lift | videos | in breakouts | share of breakouts |",
           "|---|---|---|---|---|"]
    for r in rows[:limit]:
        out.append(f"| `{r['term']}` | **{r['lift']}x** | {r['videos']} | "
                   f"{r['breakout_videos']} | {r['share_breakout']:.0%} |")
    return "\n".join(out) + "\n"


def export_seo_report(df, clusters, niche_name, fetch_thumbnails=True,
                      out_dir_override=None) -> dict:
    """Write the SEO report to exports/ as Markdown + JSON."""
    rep = build_seo_report(df, clusters)
    thumbs_info = {"files": [], "contact_sheet": None, "dir": None}
    if fetch_thumbnails:
        try:
            thumbs_info = download_thumbnails(df, niche_name,
                                              out_dir_override=out_dir_override)
        except Exception as e:
            print(f"  (thumbnail download skipped: {type(e).__name__})")
    out_dir = out_dir_override or export_dir_for(EXPORTS_DIR, niche_name,
                                                 "packaging")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, _named(niche_name, "seo"))
    paths = {"md": stem + ".md", "json": stem + ".json"}

    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, default=str)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# {niche_name} — SEO & Packaging Report\n",
         f"Generated {ts} · {rep['videos_analyzed']:,} videos · "
         f"{rep['breakout_videos']:,} breakouts ({rep['breakout_definition']})\n",
         "\n> **How to read this:** every figure compares BREAKOUT videos "
         "against the rest of this niche. `lift` = how much more often a "
         "term appears among breakouts than among videos generally. "
         "Lift ~1.0 means the term is just niche vocabulary.\n",
         "\n> **On tags:** YouTube says tags play a minimal role in search "
         "ranking. Treat high-lift tags as a *positioning fingerprint* — "
         "what winning videos claim to be about — not as a ranking hack. "
         "Titles and descriptions carry far more weight.\n",
         "\n> **Correlation, not causation.** Every line below is computed "
         "by code from observed differences between breakout videos and "
         "the rest — it is a description of what successful videos look "
         "like, NOT proof that copying it causes success. Most of these "
         "signals likely share a common cause: creators who invest effort "
         "write fuller descriptions *and* add chapters *and* tag carefully "
         "*and* make better videos. Match the packaging because it is "
         "cheap and it is what the winners do — but the video still has to "
         "earn the view.\n",
         "\n## Observed patterns among breakout videos\n",
         "_Generated deterministically from the data. No AI involved._\n"]

    recs = _recommendations(rep)
    L += [f"{i}. {r}" for i, r in enumerate(recs, 1)] if recs else \
         ["_No packaging gaps large enough to call out._"]

    L.append("\n### Questions this report cannot answer\n")
    L.append("Code can measure *what* differs; it cannot judge *why*, or "
             "whether a pattern transfers to your video. Worth asking an "
             "AI (paste this file alongside the breakout report):\n")
    L.append("- Which of these patterns are plausibly causal, and which are "
             "just markers of a creator who tries harder?")
    L.append("- Do the high-lift tags reveal a sub-topic or audience the "
             "niche is under-serving?")
    L.append("- Given the strongest cluster, write 3 title options and a "
             "description outline that fit these observed patterns.")

    L.append("\n## Packaging contrast — top performers vs the niche\n")
    pc = rep.get("packaging_contrast") or {}
    L.append("| metric | niche | top decile | ratio |")
    L.append("|---|---|---|---|")
    for k, v in pc.items():
        if not isinstance(v, dict):
            continue
        if "niche_median" in v:
            r = v.get("ratio")
            L.append(f"| {k} | {v['niche_median']:,.0f} | "
                     f"{v['top_decile_median']:,.0f} | "
                     f"{('%.2fx' % r) if r else '—'} |")
        elif "niche_rate" in v:
            lf = v.get("lift")
            L.append(f"| {k} | {v['niche_rate']:.0%} | "
                     f"{v['top_decile_rate']:.0%} | "
                     f"{('%.1fx' % lf) if lf else '—'} |")

    st = rep.get("structure_lift") or []
    if st:
        L.append("\n## Title & description STRUCTURE (weighted far above tags)\n")
        L.append("| pattern | niche | breakouts | lift |")
        L.append("|---|---|---|---|")
        for r in st:
            L.append(f"| {r['pattern']} | {r['niche_rate']:.0%} | "
                     f"{r['breakout_rate']:.0%} | **{r['lift']}x** |")

    L.append("\n## Title words that over-index among breakouts")
    L.append(_lift_table_md(rep["title_word_lift"], "title word"))
    L.append("\n## Tags that over-index among breakouts")
    L.append(_lift_table_md(rep["tag_lift"], "tag"))
    L.append("\n## Hashtags that over-index among breakouts")
    L.append(_lift_table_md(rep["hashtag_lift"], "hashtag"))

    if rep["cluster_profiles"]:
        L.append("\n## Per-cluster packaging profiles\n")
        L.append("How the videos in each discovered topic cluster are packaged.\n")
        for p in rep["cluster_profiles"][:15]:
            L.append(f"\n### {p['topic']}  ·  {p['videos']} videos"
                     f"  ·  {p.get('unique_channels', '?')} channels")
            bits = []
            if p["median_tag_count"] is not None:
                bits.append(f"{p['median_tag_count']:.0f} tags")
            if p["median_title_length"] is not None:
                bits.append(f"{p['median_title_length']:.0f}-char titles")
            if p["median_duration_seconds"]:
                bits.append(f"{p['median_duration_seconds']/60:.0f} min")
            if p["chapters_rate"] is not None:
                bits.append(f"{p['chapters_rate']:.0%} use chapters")
            if bits:
                L.append("- Format: " + " · ".join(bits))
            if p["top_tags"]:
                L.append("- Tags: " + ", ".join(f"`{t['term']}`" for t in p["top_tags"]))
            if p["top_hashtags"]:
                L.append("- Hashtags: " + ", ".join(f"`#{h['term']}`" for h in p["top_hashtags"]))
            if p.get("examples"):
                L.append("\n  **What the top videos in this cluster actually did:**\n")
                for ex in p["examples"]:
                    L.append(f"  - **{ex['title']}** — {ex.get('channel','')}")
                    L.append(f"    - Description opens: _\"{ex['description_opening']}\"_")
                    L.append(f"    - {ex['chapters']} chapters · tags: "
                             + ", ".join(f"`{t}`" for t in ex["tags"][:6]))

    # Publish timing — appended to the SEO report because it is the same
    # question ("how do I package/ship this?") and keeps the deliverable
    # count down.
    try:
        import timing
        L += timing.render_timing_markdown(timing.build_timing_report(df))
    except Exception as e:
        L.append(f"\n_(timing analysis unavailable: {type(e).__name__})_\n")

    thumbs = rep.get("top_thumbnails") or []
    L.append("\n## Thumbnails — the blind spot\n")
    if thumbs:
        L.append("**The engine cannot analyse thumbnails.** It has no vision, "
                 "and thumbnail is plausibly the single largest CTR lever in "
                 "the whole system. This is the one major packaging factor "
                 "measured nowhere in this report.\n")
        if thumbs_info.get("contact_sheet"):
            L.append(f"**A contact sheet of these has been saved to "
                     f"`{thumbs_info['contact_sheet']}`.** Upload that ONE "
                     f"image to a multimodal AI (Claude/ChatGPT) and ask it "
                     f"to describe the pattern — that works, whereas pasting "
                     f"the URLs below does not, because a chat model sees "
                     f"only the link text.\n")
        elif thumbs_info.get("dir"):
            L.append(f"**Images saved to `{thumbs_info['dir']}`** — upload "
                     f"them to a multimodal AI to analyse.\n")
        L.append("Or open these side by side yourself and look for the "
                 "pattern (face/no face, text density, colour, expression):\n")
        for t in thumbs:
            L.append(f"- [{t['title']}]({t['thumbnail_url']}) — "
                     f"{t['channel']} (score {t['score']})")
    else:
        L.append("_No thumbnail URLs in this dataset — rebuild the niche to "
                 "capture them._")

    L.append("\n## Hand this to an AI\n")
    L.append("Everything above is measured. These are the jobs that need "
             "judgement or generation — paste this file (and the breakout "
             "report) into Claude or ChatGPT and ask:\n")
    L.append("1. Which of these patterns are plausibly **causal**, and which "
             "are just markers of a creator who invests more effort?")
    L.append("2. For the strongest cluster, write **5 title options** that "
             "follow the winning structure above, and say what each is "
             "doing psychologically.")
    L.append("3. Write a **description template** for that cluster. Use the "
             "*What the top videos actually did* examples above as the "
             "model — they show the real opening lines, chapter counts and "
             "tags used by the winners in this niche. Give me the first 150 "
             "visible characters, then a chapter skeleton, then the rest.")
    L.append("4. Do the high-lift tags reveal a **sub-topic or audience** "
             "the niche is under-serving?")
    L.append("5. I'll open the thumbnail links above — ask me what I see, "
             "then suggest a thumbnail concept that fits the pattern but "
             "differentiates.")
    L.append("6. Which observed patterns should I **ignore** because they "
             "reflect this niche's quirks rather than something transferable?")

    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return paths
