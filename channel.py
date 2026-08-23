"""
channel.py — take one channel apart.

WHY THIS IS CHEAP
-----------------
Building a niche costs ~13,000 quota units because it leans on
search.list at 100 units a call. A single channel's entire catalogue
lives in its uploads playlist, and playlistItems.list costs 1 unit per
50 videos. So a 300-video teardown runs about 13 units — roughly a
thousandth of a niche build. You can study a channel a day for a year
without noticing.

WHAT YOU CAN AND CANNOT LEARN
-----------------------------
Available, because it is public:
    every video, with views, likes, comments, duration
    exact publish timestamps -> cadence, day, hour, consistency
    titles, descriptions, tags, thumbnails
    which of their videos beat their own median, and by how much
    whether they are improving over time

Not available at any price, because it is private analytics:
    click-through rate, average view duration, retention curves
    traffic sources, subscriber conversion, revenue

That second list is the interesting half of why a video worked, and no
public API exposes it. Everything here is inference from the outside.

Transcripts are a separate case: technically reachable but only through
an undocumented endpoint, so this module writes a shortlist of which
videos are worth transcribing and leaves the copying to you. See
angles.write_transcript_study for the same pattern applied to a niche.

WHAT TO COPY AND WHAT NOT TO
----------------------------
Format transfers. Structure, cadence, length, packaging conventions and
the shape of a title are all reusable, and studying them is ordinary
competitive analysis. Specific videos do not transfer — remaking
someone's video gets you a worse version of a thing that already
exists, competing directly against the original. The useful output of
this module is the pattern, not the list.
"""

import os
import re
from collections import Counter

import pandas as pd

from config import (
    EXPORTS_DIR, CHANNEL_MAX_VIDEOS, CHANNEL_BREAKOUT_RATIO,
    EXCLUDE_SHORTS_UNDER_SECONDS,
)
from paths import channel_export_dir
from paths import named as _named
from insights import _STOPWORDS, _tokenize


# ============================================================
# RESOLVING WHAT THE USER PASTED
# ============================================================

def parse_channel_input(raw: str) -> tuple:
    """
    Work out what the user gave us. Returns (kind, value).

    Accepts the four things people actually have to hand:
        UCxxxxxxxx...                      -> ("id", ...)
        @handle                            -> ("handle", ...)
        youtube.com/@handle                -> ("handle", ...)
        youtube.com/channel/UCxxxx         -> ("id", ...)
        youtube.com/c/Name or /user/Name   -> ("search", ...)
        anything else                      -> ("search", ...)

    The legacy /c/ and /user/ forms have no direct lookup in the current
    API, so they fall back to a search — which costs 100 units instead of
    1. Worth telling the user, since pasting the @handle instead is free.
    """
    s = (raw or "").strip()
    if not s:
        return ("none", "")
    m = re.search(r"youtube\.com/channel/(UC[\w-]{20,})", s)
    if m:
        return ("id", m.group(1))
    m = re.search(r"youtube\.com/@([\w.\-]+)", s)
    if m:
        return ("handle", "@" + m.group(1))
    m = re.search(r"youtube\.com/(?:c|user)/([\w.\-]+)", s)
    if m:
        return ("search", m.group(1))
    if s.startswith("UC") and len(s) >= 22:
        return ("id", s)
    if s.startswith("@"):
        return ("handle", s)
    return ("search", s)


def resolve_channel(keys, raw_input, tracker=None):
    """
    Resolve user input to {channel_id, title, uploads_playlist, stats}.

    Costs 1 unit for an ID or handle, 101 for a name that needs a search.
    Returns None if nothing matches.
    """
    kind, value = parse_channel_input(raw_input)
    if kind == "none":
        return None

    params = {"part": "snippet,statistics,contentDetails"}
    if kind == "id":
        params["id"] = value
    elif kind == "handle":
        params["forHandle"] = value
    else:
        # No direct lookup — search for the channel first.
        resp = keys.execute(lambda yt: yt.search().list(
            part="snippet", q=value, type="channel", maxResults=1))
        if tracker:
            tracker.charge("search.list")
        items = resp.get("items") or []
        if not items:
            return None
        params["id"] = items[0]["snippet"]["channelId"]

    resp = keys.execute(lambda yt: yt.channels().list(**params))
    if tracker:
        tracker.charge("channels.list")
    items = resp.get("items") or []
    if not items:
        return None
    c = items[0]
    stats = c.get("statistics", {})
    return {
        "channel_id": c["id"],
        "title": c["snippet"]["title"],
        "description": c["snippet"].get("description", ""),
        "published_at": c["snippet"].get("publishedAt"),
        "country": c["snippet"].get("country"),
        "uploads_playlist":
            c["contentDetails"]["relatedPlaylists"]["uploads"],
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "total_views": int(stats.get("viewCount", 0) or 0),
        "video_count": int(stats.get("videoCount", 0) or 0),
        "hidden_subs": stats.get("hiddenSubscriberCount", False),
    }


# ============================================================
# FETCHING THE CATALOGUE
# ============================================================

def fetch_video_ids(keys, uploads_playlist, tracker=None,
                    max_videos=CHANNEL_MAX_VIDEOS, verbose=True):
    """
    Page through the uploads playlist. 1 unit per 50 videos.

    This is the whole reason a teardown is cheap. Getting the same list
    via search.list would cost 100 units per 50 videos — a hundred times
    more for a strictly worse result, since search would also miss
    anything that ranks poorly.
    """
    ids, token = [], None
    while len(ids) < max_videos:
        resp = keys.execute(lambda yt: yt.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist,
            maxResults=50, pageToken=token))
        if tracker:
            tracker.charge("playlistItems.list")
        for it in resp.get("items", []):
            vid = (it.get("contentDetails") or {}).get("videoId")
            if vid:
                ids.append(vid)
        token = resp.get("nextPageToken")
        if verbose and len(ids) % 200 < 50:
            print(f"    {len(ids)} videos found...")
        if not token:
            break
    return ids[:max_videos]


def fetch_catalog(keys, channel_info, tracker=None,
                  max_videos=CHANNEL_MAX_VIDEOS, verbose=True):
    """
    Full catalogue as a DataFrame in the engine's standard schema, so
    every existing analysis module works on it unchanged.
    """
    import collector
    if verbose:
        print("  Reading uploads playlist...")
    ids = fetch_video_ids(keys, channel_info["uploads_playlist"], tracker,
                          max_videos=max_videos, verbose=verbose)
    if not ids:
        return pd.DataFrame()
    if verbose:
        print(f"  Fetching details for {len(ids)} videos...")
    # NOTE the tuple. _download_video_details returns
    # (video_rows, channel_ids); treating it as a plain list builds a
    # two-row DataFrame with integer column names, which then fails
    # deep inside metrics with an unrelated-looking error.
    rows, _channel_ids = collector._download_video_details(keys, ids, tracker)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Stamp channel-level stats onto every row. The niche pipeline gets
    # these from a separate channels.list call; here we already have them.
    df["channel_subs"] = channel_info["subscribers"]
    df["channel_view_count"] = channel_info["total_views"]
    df["channel_video_count"] = channel_info["video_count"]
    df["channel_hidden_subs"] = channel_info["hidden_subs"]
    import metrics
    return metrics.calculate_all(df)


# ============================================================
# CHANNEL-SPECIFIC ANALYSIS
# ============================================================

def _cadence(df):
    """
    Publishing rhythm: how often, and how regularly.

    Consistency matters separately from frequency. A channel posting
    every 7 days like clockwork and one averaging 7 days but swinging
    between 1 and 30 are running different operations, and the gap is
    invisible in an average.
    """
    if "published_at" not in df.columns or len(df) < 3:
        return {}
    dates = pd.to_datetime(df["published_at"], utc=True,
                           errors="coerce").dropna().sort_values()
    if len(dates) < 3:
        return {}
    gaps = dates.diff().dt.total_seconds().dropna() / 86400.0
    gaps = gaps[gaps > 0]
    if gaps.empty:
        return {}
    med = float(gaps.median())
    # Coefficient of variation — spread relative to the average, so it is
    # comparable between a daily channel and a monthly one.
    cv = float(gaps.std() / gaps.mean()) if gaps.mean() else 0.0
    if med <= 1.5:
        label = "daily"
    elif med <= 4:
        label = "every few days"
    elif med <= 9:
        label = "weekly"
    elif med <= 18:
        label = "fortnightly"
    elif med <= 40:
        label = "monthly"
    else:
        label = "sporadic"
    return {
        "median_gap_days": round(med, 1),
        "mean_gap_days": round(float(gaps.mean()), 1),
        "fastest_gap_days": round(float(gaps.min()), 1),
        "slowest_gap_days": round(float(gaps.max()), 1),
        "cadence_label": label,
        "consistency_cv": round(cv, 2),
        "consistency_label": ("metronomic" if cv < 0.5 else
                              "fairly regular" if cv < 1.0 else
                              "irregular"),
        "first_upload": str(dates.iloc[0].date()),
        "last_upload": str(dates.iloc[-1].date()),
        "active_days": int((dates.iloc[-1] - dates.iloc[0]).days),
    }


def _trajectory(df, buckets=4):
    """
    Are they getting better, or coasting?

    Splits the catalogue into equal-sized chronological buckets and
    compares median views. Uses view_count rather than views_per_hour
    on purpose: views_per_hour divides by age, so recent videos would
    look stronger automatically and every channel would appear to be
    improving.
    """
    if "published_at" not in df.columns or len(df) < buckets * 3:
        return {}
    d = df.copy()
    d["_pub"] = pd.to_datetime(d["published_at"], utc=True, errors="coerce")
    d = d.dropna(subset=["_pub"]).sort_values("_pub")
    if len(d) < buckets * 3:
        return {}
    chunks = [c for c in (d.iloc[i * len(d) // buckets:(i + 1) * len(d) // buckets]
                          for i in range(buckets)) if len(c)]
    out = []
    for i, c in enumerate(chunks, 1):
        out.append({
            "period": i,
            "from": str(c["_pub"].iloc[0].date()),
            "to": str(c["_pub"].iloc[-1].date()),
            "videos": int(len(c)),
            "median_views": int(c["view_count"].median()),
        })
    first, last = out[0]["median_views"], out[-1]["median_views"]
    ratio = (last / first) if first else None
    return {
        "periods": out,
        "change_ratio": round(ratio, 2) if ratio else None,
        "reading": (
            "growing strongly" if ratio and ratio >= 2 else
            "growing" if ratio and ratio >= 1.25 else
            "flat" if ratio and ratio >= 0.8 else
            "declining"),
    }


def _their_breakouts(df, ratio=CHANNEL_BREAKOUT_RATIO):
    """Videos that beat this channel's OWN median views by `ratio`."""
    if "view_count" not in df.columns or df.empty:
        return pd.DataFrame(), {}
    med = float(df["view_count"].median())
    if med <= 0:
        return pd.DataFrame(), {}
    d = df.copy()
    d["vs_own_median"] = (d["view_count"] / med).round(2)
    hits = d[d["vs_own_median"] >= ratio].sort_values(
        "vs_own_median", ascending=False)
    return hits, {
        "channel_median_views": int(med),
        "breakout_count": int(len(hits)),
        "breakout_rate": round(len(hits) / len(d), 3),
        "best_multiple": float(d["vs_own_median"].max()),
    }


def _title_formula(df, hits=None):
    """
    What their titles have in common, and what the winners do
    differently.

    The comparison is the point. A word appearing in 40% of all their
    titles is house style; a word appearing in 15% of all titles but 45%
    of their breakouts is a lever.
    """
    if "title" not in df.columns or df.empty:
        return {}
    titles = df["title"].fillna("").astype(str)
    lengths = titles.str.len()
    words = titles.str.split().str.len()

    def _profile(series):
        toks = Counter()
        for t in series:
            toks.update(w for w in _tokenize(str(t))
                        if w not in _STOPWORDS and len(w) > 2)
        return toks

    all_tok = _profile(titles)
    n_all = max(len(titles), 1)
    common = [{"word": w, "share_all": round(c / n_all, 3)}
              for w, c in all_tok.most_common(15)]

    lifts = []
    if hits is not None and len(hits) >= 4:
        hit_tok = _profile(hits["title"].fillna("").astype(str))
        n_hit = len(hits)
        for w, c in hit_tok.most_common(40):
            share_hit = c / n_hit
            share_all = all_tok.get(w, 0) / n_all
            if share_all > 0 and c >= 2:
                lifts.append({
                    "word": w, "in_breakouts": round(share_hit, 3),
                    "in_all": round(share_all, 3),
                    "lift": round(share_hit / share_all, 2),
                })
        lifts.sort(key=lambda r: -r["lift"])

    return {
        "median_chars": int(lengths.median()),
        "median_words": int(words.median()),
        "shortest": int(lengths.min()),
        "longest": int(lengths.max()),
        "pct_question": round(float(titles.str.contains(r"\?").mean()), 3),
        "pct_number": round(float(titles.str.contains(r"\d").mean()), 3),
        "pct_pipe_or_dash": round(float(titles.str.contains(r"[|\-–—]").mean()), 3),
        "pct_allcaps_word": round(
            float(titles.str.contains(r"\b[A-Z]{3,}\b").mean()), 3),
        "common_words": common,
        "breakout_lifts": lifts[:15],
    }


def _format_profile(df):
    """Duration, tagging and description conventions."""
    out = {}
    if "duration_seconds" in df.columns:
        secs = pd.to_numeric(df["duration_seconds"], errors="coerce").dropna()
        if len(secs):
            out["median_minutes"] = round(float(secs.median()) / 60, 1)
            out["shortest_minutes"] = round(float(secs.min()) / 60, 1)
            out["longest_minutes"] = round(float(secs.max()) / 60, 1)
            out["pct_shorts"] = round(
                float((secs < EXCLUDE_SHORTS_UNDER_SECONDS).mean()), 3)
    for col, key in (("tag_count", "median_tags"),
                     ("desc_chapter_count", "median_chapters")):
        if col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(v):
                out[key] = float(v.median())
    for col, key in (("has_chapters", "pct_with_chapters"),
                     ("desc_has_cta", "pct_with_cta"),
                     ("has_captions", "pct_with_captions")):
        if col in df.columns:
            out[key] = round(float(df[col].fillna(False).astype(bool).mean()), 3)
    return out


def _mature(df, min_age_days=30):
    """
    Drop videos too young to have finished accumulating views.

    Views arrive front-loaded but not instantly: a three-week-old video
    is still climbing. Including those in a trajectory comparison makes
    the most recent period look artificially weak and can turn a growing
    channel into a "declining" one. Same problem for breakout detection —
    a genuine hit published last week has not reached its multiple yet.

    Returns (mature_df, n_excluded).
    """
    if "hours_since_upload" not in df.columns:
        return df, 0
    age_days = pd.to_numeric(df["hours_since_upload"], errors="coerce") / 24.0
    keep = (age_days >= min_age_days).fillna(True)
    return df[keep], int((~keep).sum())


def _title_templates(df, min_uses=3, max_report=12):
    """
    Find recurring title TEMPLATES, not just recurring words.

    This is the difference between a useful teardown and a trivia list.
    Word-frequency analysis on a channel built around "The Economics of
    McDonald's / Walmart / Costco" reports the top term as "costco" —
    true, and almost worthless. The actual asset is the frame:

        The Economics of ___

    A template is the most transferable thing a channel has. It is why
    their second video is easier than their first, and it is the thing
    you can adopt without copying anyone's content.

    Detects both ends:
        prefix  "The Economics of ___"   (leading words repeat)
        suffix  "___ Explained"          (trailing words repeat)
    """
    if "title" not in df.columns or df.empty:
        return []
    titles = [str(t).strip() for t in df["title"].fillna("") if str(t).strip()]
    if len(titles) < min_uses:
        return []

    prefix, suffix = Counter(), Counter()
    for t in titles:
        w = t.split()
        # 2-5 word frames. Below 2 catches nothing but articles; above 5
        # stops being a frame and becomes the whole title.
        for n in range(2, 6):
            if len(w) > n:
                prefix[" ".join(w[:n])] += 1
                suffix[" ".join(w[-n:])] += 1

    def _collect(counter, kind):
        # Keep only the LONGEST frame for any group of videos. "The
        # Economics" and "The Economics of" will both hit exactly 60 uses
        # on the same 60 videos; they describe one template, and the
        # longer form is the more useful statement of it.
        #
        # An earlier version compared against the already-formatted
        # string ("The Economics ___"), so "The Economics of" was never
        # recognised as an extension of it and both survived.
        kept = {}
        for frame, c in counter.most_common(80):
            if c < min_uses:
                continue
            drop = False
            for other in list(kept):
                if kept[other] != c:
                    continue
                if other in frame:          # new frame extends an existing one
                    del kept[other]
                elif frame in other:        # new frame is a prefix of one we have
                    drop = True
                    break
            if not drop:
                kept[frame] = c
        return [{
            "template": (f"{f} ___" if kind == "prefix" else f"___ {f}"),
            "uses": c,
            "share": round(c / len(titles), 3),
            "kind": kind,
        } for f, c in kept.items()]

    found = _collect(prefix, "prefix") + _collect(suffix, "suffix")
    found.sort(key=lambda r: -r["uses"])
    return found[:max_report]


def _tag_profile(df, hits=None, top=20):
    """
    What they tag with, and which tags skew toward their hits.

    Tags are invisible to viewers and carry no ranking weight worth
    speaking of, so treat them as a statement of INTENT: they show what
    the creator believes the video is about and who they think should
    find it. That makes them useful for reading a channel's positioning
    even though copying them achieves nothing directly.
    """
    if "tags" not in df.columns:
        return {}

    def _count(frame):
        c = Counter()
        for raw in frame["tags"].fillna(""):
            for t in str(raw).split("|"):
                t = t.strip().lower()
                if t:
                    c[t] += 1
        return c

    all_c = _count(df)
    if not all_c:
        return {}
    n_all = max(int(df["tags"].notna().sum()), 1)

    lifts = []
    if hits is not None and len(hits) >= 4 and "tags" in hits.columns:
        hit_c = _count(hits)
        n_hit = max(int(hits["tags"].notna().sum()), 1)
        for t, c in hit_c.most_common(60):
            if c < 2:
                continue
            s_hit, s_all = c / n_hit, all_c.get(t, 0) / n_all
            if s_all > 0:
                lifts.append({"tag": t, "in_breakouts": round(s_hit, 3),
                              "in_all": round(s_all, 3),
                              "lift": round(s_hit / s_all, 2)})
        lifts.sort(key=lambda r: -r["lift"])

    return {
        "videos_with_tags": n_all,
        "pct_tagged": round(n_all / max(len(df), 1), 3),
        "distinct_tags": len(all_c),
        "common": [{"tag": t, "uses": c, "share": round(c / n_all, 3)}
                   for t, c in all_c.most_common(top)],
        "breakout_lifts": lifts[:12],
    }


def _engagement_breakouts(df, ratio=1.5, top=10):
    """
    Videos that over-performed on ENGAGEMENT rather than views.

    A different signal from a view breakout and often a more interesting
    one: modest reach but an audience that argued about it. For a small
    channel that is frequently the better template to copy, because
    engagement travels through the algorithm while raw reach mostly
    reflects who already subscribed.
    """
    if "engagement_rate" not in df.columns or df.empty:
        return pd.DataFrame()
    med = float(pd.to_numeric(df["engagement_rate"], errors="coerce").median())
    if not med or med <= 0:
        return pd.DataFrame()
    d = df.copy()
    d["vs_own_engagement"] = (
        pd.to_numeric(d["engagement_rate"], errors="coerce") / med).round(2)
    return d[d["vs_own_engagement"] >= ratio].nlargest(top, "vs_own_engagement")


def _vs_niche(df, niche_name):
    """
    Put the channel's numbers next to a niche baseline, if one exists.

    "Their median video gets 13,519 views" is unreadable on its own.
    "3.2x the niche median" is the sentence you actually wanted. Silently
    returns nothing when no matching niche has been built, since this is
    a bonus rather than a requirement.
    """
    if not niche_name:
        return {}
    try:
        from baseline import load as _load
        bl = _load(niche_name)
    except Exception:
        return {}
    out = {"niche": niche_name}
    for col, label in (("views_per_hour", "views/hour"),
                       ("engagement_rate", "engagement rate"),
                       ("view_count", "views")):
        if col not in df.columns:
            continue
        try:
            val = float(pd.to_numeric(df[col], errors="coerce").median())
            pct = bl.percentile_of(col, val)
            out[col] = {"label": label, "channel_median": val,
                        "niche_percentile": round(pct, 1)}
        except Exception:
            continue
    return out if len(out) > 1 else {}


def analyze_channel(df, channel_info, niche_name=None):
    """Assemble the full profile."""
    mature, n_young = _mature(df)
    hits, hit_stats = _their_breakouts(mature)
    return {
        "channel": channel_info,
        "videos_analysed": int(len(df)),
        "videos_too_young": n_young,
        "cadence": _cadence(df),
        "trajectory": _trajectory(mature),
        "breakout_stats": hit_stats,
        "breakouts": hits,
        "engagement_breakouts": _engagement_breakouts(mature),
        "titles": _title_formula(mature, hits),
        "templates": _title_templates(df),
        "tags": _tag_profile(df, hits),
        "format": _format_profile(df),
        "vs_niche": _vs_niche(df, niche_name),
    }


# ============================================================
# EXPORT
# ============================================================

def export_channel_report(df, profile, tracker=None) -> dict:
    """Write the teardown, and reuse the niche modules for the rest."""
    info = profile["channel"]
    name = info["title"]
    root = channel_export_dir(EXPORTS_DIR, name)
    paths = {}

    # Full catalogue as data.
    csv_path = os.path.join(root, _named(name, "catalog.csv"))
    df.to_csv(csv_path, index=False)
    paths["catalog"] = csv_path

    L = [f"# Channel teardown — {name}", ""]
    L.append(f"`{info['channel_id']}`  ·  "
             f"{info['subscribers']:,} subscribers  ·  "
             f"{info['video_count']:,} videos published  ·  "
             f"{info['total_views']:,} total views")
    if info.get("country"):
        L.append(f"Country: {info['country']}")
    L.append("")
    L.append(f"Analysed **{profile['videos_analysed']:,}** of their videos.")
    if profile.get("videos_too_young"):
        L.append("")
        L.append(f"> {profile['videos_too_young']} video(s) newer than 30 "
                 f"days are excluded from the trajectory and breakout "
                 f"sections. Views arrive front-loaded but not instantly, "
                 f"so a very recent video has not reached its final number "
                 f"— counting it would make the newest period look "
                 f"artificially weak.")
    L.append("")

    cad = profile.get("cadence") or {}
    if cad:
        L.append("## Publishing rhythm")
        L.append("")
        L.append(f"Posts **{cad['cadence_label']}** — median "
                 f"{cad['median_gap_days']} days between uploads, and the "
                 f"pattern is **{cad['consistency_label']}**.")
        L.append("")
        L.append("| | |")
        L.append("|---|---|")
        L.append(f"| Median gap | {cad['median_gap_days']} days |")
        L.append(f"| Fastest / slowest | {cad['fastest_gap_days']} / "
                 f"{cad['slowest_gap_days']} days |")
        L.append(f"| Consistency (lower = steadier) | {cad['consistency_cv']} |")
        L.append(f"| Active since | {cad['first_upload']} "
                 f"({cad['active_days']:,} days) |")
        L.append("")
        L.append("> Consistency is measured separately from frequency on "
                 "purpose. A channel posting every 7 days like clockwork and "
                 "one averaging 7 days but swinging between 1 and 30 are "
                 "running different operations, and an average hides that.")
        L.append("")

    traj = profile.get("trajectory") or {}
    if traj.get("periods"):
        L.append("## Are they improving?")
        L.append("")
        L.append(f"Median views changed by **{traj['change_ratio']}x** from "
                 f"their earliest period to their latest — _{traj['reading']}_.")
        L.append("")
        L.append("| period | dates | videos | median views |")
        L.append("|---|---|---|---|")
        for p in traj["periods"]:
            L.append(f"| {p['period']} | {p['from']} → {p['to']} | "
                     f"{p['videos']} | {p['median_views']:,} |")
        L.append("")
        L.append("> Uses raw view counts, not views-per-hour. Views-per-hour "
                 "divides by age, so recent videos would score higher "
                 "automatically and every channel on earth would look like "
                 "it was improving.")
        L.append("")

    bs = profile.get("breakout_stats") or {}
    hits = profile.get("breakouts")
    if bs:
        L.append("## Their own breakouts")
        L.append("")
        L.append(f"Median video gets **{bs['channel_median_views']:,}** "
                 f"views. **{bs['breakout_count']}** videos "
                 f"({bs['breakout_rate']:.0%}) beat that by "
                 f"{CHANNEL_BREAKOUT_RATIO}x or more; best was "
                 f"**{bs['best_multiple']:.1f}x**.")
        L.append("")
        if hits is not None and len(hits):
            L.append("| multiple | views | published | title |")
            L.append("|---|---|---|---|")
            for r in hits.head(20).itertuples():
                pub = str(r.published_at)[:10]
                t = str(r.title)[:70].replace("|", "\\|")
                L.append(f"| {r.vs_own_median}x | {int(r.view_count):,} | "
                         f"{pub} | {t} |")
            L.append("")

    tf = profile.get("titles") or {}
    if tf:
        L.append("## Title formula")
        L.append("")
        L.append(f"Median title: **{tf['median_chars']} characters, "
                 f"{tf['median_words']} words** "
                 f"(range {tf['shortest']}–{tf['longest']}).")
        L.append("")
        L.append("| convention | share of titles |")
        L.append("|---|---|")
        L.append(f"| Contains a question mark | {tf['pct_question']:.0%} |")
        L.append(f"| Contains a number | {tf['pct_number']:.0%} |")
        L.append(f"| Uses a pipe or dash separator | {tf['pct_pipe_or_dash']:.0%} |")
        L.append(f"| Has an ALL-CAPS word | {tf['pct_allcaps_word']:.0%} |")
        L.append("")
        if tf.get("breakout_lifts"):
            L.append("### Words that skew toward their breakouts")
            L.append("")
            L.append("| word | in breakouts | in all titles | lift |")
            L.append("|---|---|---|---|")
            for r in tf["breakout_lifts"]:
                L.append(f"| {r['word']} | {r['in_breakouts']:.0%} | "
                         f"{r['in_all']:.0%} | {r['lift']}x |")
            L.append("")
            L.append("> Lift compares a word's share of their breakouts "
                     "against its share of everything they publish. A word "
                     "in 40% of all their titles is house style; a word in "
                     "15% of titles but 45% of breakouts is a lever. "
                     "**Correlational** — the word may mark a subject that "
                     "performs, rather than causing the performance.")
            L.append("")

    fp = profile.get("format") or {}
    if fp:
        L.append("## Format conventions")
        L.append("")
        L.append("| | |")
        L.append("|---|---|")
        if "median_minutes" in fp:
            L.append(f"| Typical length | {fp['median_minutes']} min "
                     f"(range {fp['shortest_minutes']}–{fp['longest_minutes']}) |")
        if "pct_shorts" in fp:
            L.append(f"| Shorts | {fp['pct_shorts']:.0%} of output |")
        if "median_tags" in fp:
            L.append(f"| Tags per video | {fp['median_tags']:.0f} |")
        if "pct_with_chapters" in fp:
            L.append(f"| Uses chapters | {fp['pct_with_chapters']:.0%} |")
        if "pct_with_cta" in fp:
            L.append(f"| Description has a CTA | {fp['pct_with_cta']:.0%} |")
        if "pct_with_captions" in fp:
            L.append(f"| Captions present | {fp['pct_with_captions']:.0%} |")
        L.append("")

    tmpl = profile.get("templates") or []
    if tmpl:
        L.append("## Recurring title templates")
        L.append("")
        L.append("The most transferable thing a channel owns. A template "
                 "is why their second video is easier than their first, "
                 "and it can be adopted without copying anyone's content.")
        L.append("")
        L.append("| template | uses | share of titles |")
        L.append("|---|---|---|")
        for t in tmpl:
            L.append(f"| `{t['template']}` | {t['uses']} | {t['share']:.0%} |")
        L.append("")
        L.append("> Detected as repeated leading or trailing word frames. "
                 "Word-frequency analysis alone would report the varying "
                 "part (`costco`, `walmart`) and miss the frame, which is "
                 "the part worth having.")
        L.append("")

    tg = profile.get("tags") or {}
    if tg.get("common"):
        L.append("## Tags")
        L.append("")
        L.append(f"**{tg['pct_tagged']:.0%}** of their videos carry tags — "
                 f"{tg['distinct_tags']:,} distinct tags across "
                 f"{tg['videos_with_tags']:,} videos.")
        L.append("")
        L.append("| tag | used on | share |")
        L.append("|---|---|---|")
        for t in tg["common"]:
            L.append(f"| {t['tag']} | {t['uses']} | {t['share']:.0%} |")
        L.append("")
        if tg.get("breakout_lifts"):
            L.append("### Tags skewing toward their breakouts")
            L.append("")
            L.append("| tag | in breakouts | in all | lift |")
            L.append("|---|---|---|---|")
            for r in tg["breakout_lifts"]:
                L.append(f"| {r['tag']} | {r['in_breakouts']:.0%} | "
                         f"{r['in_all']:.0%} | {r['lift']}x |")
            L.append("")
        L.append("> Tags are invisible to viewers and carry little ranking "
                 "weight. Read them as a statement of INTENT — what the "
                 "creator thinks the video is about and who should find "
                 "it. Useful for reading positioning; copying them "
                 "directly achieves nothing.")
        L.append("")

    eng = profile.get("engagement_breakouts")
    if eng is not None and len(eng):
        L.append("## Engagement breakouts")
        L.append("")
        L.append("Videos whose audience reacted far above this channel's "
                 "norm, regardless of reach. Often a better template to "
                 "copy than a view breakout — engagement travels through "
                 "the algorithm, while raw reach mostly reflects who was "
                 "already subscribed.")
        L.append("")
        L.append("| vs own rate | engagement | views | title |")
        L.append("|---|---|---|---|")
        for r in eng.head(10).itertuples():
            t = str(r.title)[:60].replace("|", "\\|")
            L.append(f"| {r.vs_own_engagement}x | "
                     f"{float(r.engagement_rate):.3f} | "
                     f"{int(r.view_count):,} | {t} |")
        L.append("")

    vn = profile.get("vs_niche") or {}
    if len(vn) > 1:
        L.append("## Against the niche")
        L.append("")
        L.append(f"Compared with the **{vn['niche']}** baseline you built:")
        L.append("")
        L.append("| metric | their median | niche percentile |")
        L.append("|---|---|---|")
        for k, v in vn.items():
            if isinstance(v, dict):
                L.append(f"| {v['label']} | {v['channel_median']:,.1f} | "
                         f"{v['niche_percentile']:.0f}th |")
        L.append("")

    L.append("## What you cannot see from out here")
    L.append("")
    L.append("Click-through rate, average view duration, retention curves, "
             "traffic sources and subscriber conversion are private "
             "analytics. No public API exposes them at any price. That is "
             "the interesting half of why a video worked, and everything "
             "above is inference from the outside.")
    L.append("")
    L.append("## What is worth copying")
    L.append("")
    L.append("Format transfers: structure, cadence, length, packaging "
             "conventions, the shape of a title. Specific videos do not — "
             "remaking one gets you a worse version of something that "
             "already exists, competing head-on with the original. Take the "
             "pattern, not the list.")
    L.append("")

    md_path = os.path.join(root, _named(name, "teardown.md"))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    paths["teardown"] = md_path

    # --- reuse the niche modules on this channel's data ---
    try:
        import timing
        t = timing.build_timing_report(df)
        tp = os.path.join(root, _named(name, "posting_times.md"))
        with open(tp, "w", encoding="utf-8") as f:
            f.write("\n".join(timing.render_timing_markdown(t)))
        paths["posting_times"] = tp
    except Exception as e:
        print(f"  (posting times skipped: {type(e).__name__}: {e})")

    try:
        import calendars as _cal
        cp = _cal.export_week_calendar(df, name, out_dir=root)
        paths["calendar"] = cp
    except Exception as e:
        print(f"  (calendar skipped: {type(e).__name__}: {e})")

    # Thumbnail contact sheet of their biggest hits. Their packaging is
    # the first thing worth looking at and it was missing from the first
    # version of this module.
    try:
        import thumbnail_sheet as _ts
        src = hits if hits is not None and len(hits) >= 8 else df
        src = src.copy()
        if "duration_seconds" in src.columns:
            secs = pd.to_numeric(src["duration_seconds"], errors="coerce")
            src = src[(secs >= EXCLUDE_SHORTS_UNDER_SECONDS) | secs.isna()]
        picked = src.head(_ts.TARGET_COUNT)
        thumb_dir = os.path.join(root, "thumbnails")
        items = _ts._download_thumbnails(picked, thumb_dir)
        sheet = _ts._build_contact_sheet(
            items, os.path.join(thumb_dir, _named(name, "contact_sheet.jpg")),
            header_text=f"{name} — top {len(picked)} thumbnails")
        if sheet:
            paths["thumbnails"] = sheet
        else:
            paths["thumbnails"] = thumb_dir
    except Exception as e:
        print(f"  (thumbnails skipped: {type(e).__name__}: {e})")

    # Transcript shortlist — their breakouts are the ones worth studying.
    try:
        import angles
        src = hits if hits is not None and len(hits) >= 5 else df
        src = src.copy()
        if "outlier_ratio" not in src.columns and "vs_own_median" in src.columns:
            src["outlier_ratio"] = src["vs_own_median"]
        tp = angles.write_transcript_study(src, name, out_dir=root)
        paths["transcript_study"] = tp
    except Exception as e:
        print(f"  (transcript shortlist skipped: {type(e).__name__}: {e})")

    return paths


# ============================================================
# CHANNEL-VS-CHANNEL DIFF
# ============================================================

def diff_channels(profile_a, profile_b, df_a, df_b):
    """
    Side-by-side comparison of two channel profiles, highlighting the
    differences that actually matter for a creator deciding which
    channel to study or compete with.

    Returns a structured dict so callers can render it however they
    want (terminal, markdown, JSON). The terminal renderer is
    export_diff_report() below.

    Design principle: don't just print both profiles in parallel.
    Compute DELTAS and call out the ones large enough to mean something.
    A channel posting 20% more often than another is noise; one posting
    4x more often is a real strategic difference.
    """
    a, b = profile_a, profile_b

    def _num(d, key, default=None):
        return d.get(key, default)

    def _pct_delta(v_a, v_b):
        """% change from b to a, None if either is missing/zero."""
        if v_a is None or v_b is None or v_b == 0:
            return None
        return (v_a - v_b) / abs(v_b) * 100

    def _flag(delta_pct, threshold=25):
        """Arrow + magnitude label for a percentage delta."""
        if delta_pct is None:
            return "—"
        if abs(delta_pct) < threshold:
            return "similar"
        direction = "▲" if delta_pct > 0 else "▼"
        mag = abs(delta_pct)
        label = "much" if mag > 100 else "somewhat"
        return f"{direction} {label} higher" if delta_pct > 0 else f"{direction} {label} lower"

    # ---- core performance ----
    vph_a = float(df_a["views_per_hour"].median()) if "views_per_hour" in df_a.columns else None
    vph_b = float(df_b["views_per_hour"].median()) if "views_per_hour" in df_b.columns else None
    p90_a = float(df_a["view_count"].quantile(0.9)) if "view_count" in df_a.columns else None
    p90_b = float(df_b["view_count"].quantile(0.9)) if "view_count" in df_b.columns else None
    br_a = a.get("breakout_stats", {}).get("rate")
    br_b = b.get("breakout_stats", {}).get("rate")

    # ---- cadence ----
    cad_a = a.get("cadence", {})
    cad_b = b.get("cadence", {})
    gap_a = cad_a.get("median_gap_days")
    gap_b = cad_b.get("median_gap_days")

    # ---- format ----
    fmt_a = a.get("format", {})
    fmt_b = b.get("format", {})
    dur_a = fmt_a.get("median_duration_min")
    dur_b = fmt_b.get("median_duration_min")

    # ---- title overlap ----
    def _top_words(profile, n=20):
        tw = profile.get("titles", {}).get("top_words", [])
        return {w["word"] for w in tw[:n]} if tw else set()

    words_a = _top_words(a)
    words_b = _top_words(b)
    shared_words = words_a & words_b
    only_a = words_a - words_b
    only_b = words_b - words_a

    # ---- tag overlap ----
    def _top_tags(profile, n=20):
        tags = profile.get("tags", {}).get("top_tags", [])
        return {t["tag"] for t in tags[:n]} if tags else set()

    tags_a = _top_tags(a)
    tags_b = _top_tags(b)
    shared_tags = tags_a & tags_b
    only_tags_a = tags_a - tags_b
    only_tags_b = tags_b - tags_a

    # ---- trajectory ----
    traj_a = a.get("trajectory", {}).get("direction", "unknown")
    traj_b = b.get("trajectory", {}).get("direction", "unknown")

    return {
        "channels": [
            a.get("channel", {}).get("title", "Channel A"),
            b.get("channel", {}).get("title", "Channel B"),
        ],
        "performance": {
            "median_vph": [vph_a, vph_b],
            "median_vph_delta": _flag(_pct_delta(vph_a, vph_b)),
            "p90_views": [p90_a, p90_b],
            "p90_views_delta": _flag(_pct_delta(p90_a, p90_b)),
            "breakout_rate": [br_a, br_b],
            "breakout_rate_delta": _flag(_pct_delta(br_a, br_b)),
        },
        "cadence": {
            "median_gap_days": [gap_a, gap_b],
            "cadence_label": [cad_a.get("cadence_label"), cad_b.get("cadence_label")],
            "consistency": [cad_a.get("consistency_label"), cad_b.get("consistency_label")],
            "cadence_delta": _flag(_pct_delta(
                1/gap_a if gap_a else None,
                1/gap_b if gap_b else None)),
        },
        "format": {
            "median_duration_min": [dur_a, dur_b],
            "duration_delta": _flag(_pct_delta(dur_a, dur_b)),
            "shorts_share": [fmt_a.get("shorts_share"), fmt_b.get("shorts_share")],
        },
        "trajectory": {
            "direction": [traj_a, traj_b],
            "same": traj_a == traj_b,
        },
        "titles": {
            "shared_words": sorted(shared_words),
            "only_a": sorted(only_a)[:10],
            "only_b": sorted(only_b)[:10],
            "overlap_pct": round(
                len(shared_words) / max(len(words_a | words_b), 1) * 100, 1),
        },
        "tags": {
            "shared": sorted(shared_tags),
            "only_a": sorted(only_tags_a)[:10],
            "only_b": sorted(only_tags_b)[:10],
            "overlap_pct": round(
                len(shared_tags) / max(len(tags_a | tags_b), 1) * 100, 1),
        },
    }


def print_diff(diff):
    """
    Render a diff dict to the terminal in a readable, scannable format.
    Designed to surface the big differences first — not a wall of numbers,
    but a prioritised view of where the two channels actually diverge.
    """
    a_name, b_name = diff["channels"]
    w = max(len(a_name), len(b_name), 20)

    def row(label, val_a, val_b, note=""):
        la = f"{val_a}" if val_a is not None else "—"
        lb = f"{val_b}" if val_b is not None else "—"
        note_str = f"  ({note})" if note else ""
        print(f"  {label:<28} {la:>{w}}  vs  {lb:<{w}}{note_str}")

    print()
    print(f"  {'':28} {a_name:>{w}}  vs  {b_name}")
    print("  " + "─" * (28 + w*2 + 8))

    print()
    print("  PERFORMANCE")
    p = diff["performance"]
    row("Median views/hr",
        f"{p['median_vph'][0]:.2f}" if p['median_vph'][0] else None,
        f"{p['median_vph'][1]:.2f}" if p['median_vph'][1] else None,
        p["median_vph_delta"])
    row("Top-10% video views",
        f"{p['p90_views'][0]:,.0f}" if p['p90_views'][0] else None,
        f"{p['p90_views'][1]:,.0f}" if p['p90_views'][1] else None,
        p["p90_views_delta"])
    row("Breakout rate",
        f"{p['breakout_rate'][0]:.0%}" if p['breakout_rate'][0] is not None else None,
        f"{p['breakout_rate'][1]:.0%}" if p['breakout_rate'][1] is not None else None,
        p["breakout_rate_delta"])

    print()
    print("  CADENCE")
    c = diff["cadence"]
    row("Median gap between uploads",
        f"{c['median_gap_days'][0]} days" if c['median_gap_days'][0] else None,
        f"{c['median_gap_days'][1]} days" if c['median_gap_days'][1] else None,
        c["cadence_label"][0] + " vs " + (c["cadence_label"][1] or "—"))
    row("Consistency",
        c["consistency"][0], c["consistency"][1], c["cadence_delta"])

    print()
    print("  FORMAT")
    f = diff["format"]
    row("Median video length",
        f"{f['median_duration_min'][0]:.0f} min" if f['median_duration_min'][0] else None,
        f"{f['median_duration_min'][1]:.0f} min" if f['median_duration_min'][1] else None,
        f["duration_delta"])
    row("Shorts share",
        f"{f['shorts_share'][0]:.0%}" if f['shorts_share'][0] is not None else None,
        f"{f['shorts_share'][1]:.0%}" if f['shorts_share'][1] is not None else None)

    print()
    print("  TRAJECTORY")
    t = diff["trajectory"]
    row("Growth direction", t["direction"][0], t["direction"][1],
        "same" if t["same"] else "DIFFERENT — worth investigating")

    print()
    print("  TITLE LANGUAGE")
    ti = diff["titles"]
    print(f"    Shared top words ({ti['overlap_pct']:.0f}% overlap): "
          + (", ".join(ti["shared_words"][:10]) or "none"))
    print(f"    Only in {a_name[:20]}: " + (", ".join(ti["only_a"]) or "none"))
    print(f"    Only in {b_name[:20]}: " + (", ".join(ti["only_b"]) or "none"))

    print()
    print("  TAGS")
    tg = diff["tags"]
    print(f"    Shared tags ({tg['overlap_pct']:.0f}% overlap): "
          + (", ".join(tg["shared"][:8]) or "none"))
    print(f"    Only in {a_name[:20]}: " + (", ".join(tg["only_a"]) or "none"))
    print(f"    Only in {b_name[:20]}: " + (", ".join(tg["only_b"]) or "none"))
    print()


def export_diff_report(diff, niche_name=None):
    """
    Write the diff to exports/channels/<a_vs_b>/diff.md and diff.json.
    Returns the dict of paths written.
    """
    import json as _json
    from paths import safe_name
    from config import EXPORTS_DIR
    a_name, b_name = diff["channels"]
    slug = f"{safe_name(a_name)}_vs_{safe_name(b_name)}"
    out_dir = os.path.join(EXPORTS_DIR, "channels", slug)
    os.makedirs(out_dir, exist_ok=True)

    json_path = os.path.join(out_dir, "diff.json")
    md_path = os.path.join(out_dir, "diff.md")

    with open(json_path, "w", encoding="utf-8") as f:
        _json.dump(diff, f, indent=2)

    def _f(v, fmt="{:.2f}"):
        return fmt.format(v) if v is not None else "—"

    lines = [
        f"# {a_name} vs {b_name}",
        "",
        "## Performance",
        f"| | {a_name} | {b_name} | delta |",
        "|-|---|---|---|",
    ]
    p = diff["performance"]
    lines += [
        f"| Median views/hr | {_f(p['median_vph'][0])} | {_f(p['median_vph'][1])} | {p['median_vph_delta']} |",
        f"| Top-10% views | {_f(p['p90_views'][0], '{:,.0f}')} | {_f(p['p90_views'][1], '{:,.0f}')} | {p['p90_views_delta']} |",
        f"| Breakout rate | {_f(p['breakout_rate'][0], '{:.0%}') if p['breakout_rate'][0] is not None else '—'} | {_f(p['breakout_rate'][1], '{:.0%}') if p['breakout_rate'][1] is not None else '—'} | {p['breakout_rate_delta']} |",
        "",
        "## Cadence",
        f"| | {a_name} | {b_name} |",
        "|-|---|---|",
    ]
    c = diff["cadence"]
    lines += [
        f"| Median gap | {_f(c['median_gap_days'][0], '{:.1f}')} days | {_f(c['median_gap_days'][1], '{:.1f}')} days |",
        f"| Rhythm | {c['cadence_label'][0] or '—'} | {c['cadence_label'][1] or '—'} |",
        f"| Consistency | {c['consistency'][0] or '—'} | {c['consistency'][1] or '—'} |",
        "",
        "## Format",
        f"| | {a_name} | {b_name} |",
        "|-|---|---|",
    ]
    fm = diff["format"]
    lines += [
        f"| Median length | {_f(fm['median_duration_min'][0], '{:.0f}')} min | {_f(fm['median_duration_min'][1], '{:.0f}')} min |",
        "",
        "## Title overlap",
    ]
    ti = diff["titles"]
    lines += [
        f"Shared words ({ti['overlap_pct']:.0f}%): {', '.join(ti['shared_words'][:10]) or 'none'}",
        f"Only {a_name}: {', '.join(ti['only_a']) or 'none'}",
        f"Only {b_name}: {', '.join(ti['only_b']) or 'none'}",
        "",
        "## Tag overlap",
    ]
    tg = diff["tags"]
    lines += [
        f"Shared ({tg['overlap_pct']:.0f}%): {', '.join(tg['shared'][:8]) or 'none'}",
        f"Only {a_name}: {', '.join(tg['only_a']) or 'none'}",
        f"Only {b_name}: {', '.join(tg['only_b']) or 'none'}",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {"json": json_path, "md": md_path}
