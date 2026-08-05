"""
shorts.py — what the Shorts we filtered out are telling us.

WHY KEEP THEM AT ALL
--------------------
Every build discards a third to a half of what it collected, almost all
of it Shorts. That data was already paid for, and it answers a question
the long-form dataset cannot: which subjects are getting attention in
short form right now.

The payoff is the comparison. A subject that is busy in Shorts and thin
in long-form is a candidate — attention exists, and nobody has made the
full version.

THE INFERENCE IS NOT AUTOMATIC, AND THIS SAYS SO
------------------------------------------------
A Short's views come from the Shorts feed, which is algorithmic push,
not from search. So short-form attention does NOT establish that anyone
would search for a ten-minute video on the same subject. Plenty of
things are fun for forty seconds and tedious for ten minutes.

What it does establish is that the subject is legible and interesting
enough to stop a scroll. That is a real signal, weaker than search
demand and stronger than nothing, and the report is written to be read
that way — as a list of things to check, not things to make.

Shorts also cannot be compared to long-form on views: a Short pulling
200,000 views from the feed and a ten-minute video pulling 200,000 from
search are not the same achievement. Nothing here ranks the two
together.
"""

import os
from collections import Counter

import pandas as pd

from config import EXPORTS_DIR
from paths import export_dir_for, shorts_path
from paths import named as _named
from insights import _STOPWORDS, _tokenize


# A term needs this many Shorts behind it before it is worth reporting.
MIN_TERM_SUPPORT = 3


def load_shorts(niche_name):
    """Read the retained Shorts for a niche, or None if there are none."""
    p = shorts_path(niche_name)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p)
        return df if len(df) else None
    except Exception:
        return None


def _terms(df, col="title"):
    c = Counter()
    if col not in df.columns:
        return c
    for t in df[col].fillna(""):
        c.update(w for w in _tokenize(str(t))
                 if w not in _STOPWORDS and len(w) > 3)
    return c


def find_underserved(shorts_df, long_df, top=25):
    """
    Subjects busy in Shorts, thin in long-form.

    Both sides are measured as a SHARE of their own population, not as
    raw counts — the two datasets are different sizes and one is not a
    subset of the other, so counts would be meaningless.
    """
    if shorts_df is None or shorts_df.empty:
        return []
    s_terms = _terms(shorts_df)
    l_terms = _terms(long_df) if long_df is not None else Counter()
    n_s = max(len(shorts_df), 1)
    n_l = max(len(long_df) if long_df is not None else 0, 1)

    rows = []
    for term, count in s_terms.most_common(200):
        if count < MIN_TERM_SUPPORT:
            continue
        share_s = count / n_s
        share_l = l_terms.get(term, 0) / n_l
        # +1 in the denominator keeps a term that appears zero times in
        # long-form from producing an infinite ratio, and damps the
        # advantage of very rare terms.
        gap = share_s / (share_l + 0.001)
        rows.append({
            "term": term,
            "shorts_count": count,
            "shorts_share": round(share_s, 4),
            "longform_count": int(l_terms.get(term, 0)),
            "longform_share": round(share_l, 4),
            "gap": round(gap, 2),
        })
    rows.sort(key=lambda r: -r["gap"])
    return rows[:top]


def export_shorts_report(niche_name, long_df=None) -> dict:
    """Write shorts.md + shorts.csv into the niche's exports."""
    shorts_df = load_shorts(niche_name)
    if shorts_df is None:
        return {"error": "no_shorts"}

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "shorts")
    csv_path = os.path.join(out_dir, _named(niche_name, "shorts.csv"))
    shorts_df.to_csv(csv_path, index=False)

    gaps = find_underserved(shorts_df, long_df)
    md_path = os.path.join(out_dir, _named(niche_name, "shorts.md"))

    L = [f"# Shorts signal — {niche_name}", ""]
    L.append(f"**{len(shorts_df):,} Shorts** were collected and filtered out "
             f"of the main dataset. They are analysed here rather than "
             f"discarded — the data was already paid for.")
    L.append("")
    L.append("> **Read this as leads, not findings.** A Short's views come "
             "from the Shorts feed — algorithmic push — not from search. So "
             "short-form attention does not establish that anyone would "
             "search for a ten-minute video on the same subject. Plenty of "
             "things are fun for forty seconds and tedious for ten minutes. "
             "What it does show is that a subject is interesting enough to "
             "stop a scroll.")
    L.append("")
    L.append("> Shorts and long-form are also **not comparable on views**. "
             "200,000 views from the feed and 200,000 from search are not "
             "the same achievement, so nothing here ranks them together.")
    L.append("")

    if gaps:
        L.append("## Busy in Shorts, thin in long-form")
        L.append("")
        L.append("The interesting quadrant: attention exists in short form, "
                 "and the full version is missing.")
        L.append("")
        L.append("| term | shorts | share | long-form | share | gap |")
        L.append("|---|---|---|---|---|---|")
        for r in gaps:
            L.append(f"| {r['term']} | {r['shorts_count']} | "
                     f"{r['shorts_share']:.1%} | {r['longform_count']} | "
                     f"{r['longform_share']:.1%} | {r['gap']}x |")
        L.append("")

    if "views_per_hour" in shorts_df.columns:
        top = shorts_df.nlargest(15, "views_per_hour")
        L.append("## Fastest-moving Shorts")
        L.append("")
        L.append("| views | views/hr | channel | title |")
        L.append("|---|---|---|---|")
        for r in top.itertuples():
            t = str(r.title)[:56].replace("|", "\\|")
            ch = str(r.channel_title)[:20].replace("|", "\\|")
            L.append(f"| {int(r.view_count):,} | {r.views_per_hour:,.0f} | "
                     f"{ch} | {t} |")
        L.append("")

    L.append("## Hand this to an AI")
    L.append("")
    L.append("1. Which of these subjects would sustain a **ten-minute** "
             "video, and which only work as forty seconds?")
    L.append("2. For the ones that would, what is the long-form angle — "
             "what does the Short leave unanswered?")
    L.append("3. Which are novelty that will be stale in a month?")
    L.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"md": md_path, "csv": csv_path, "shorts": len(shorts_df),
            "gaps": len(gaps)}
