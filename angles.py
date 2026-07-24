"""
angles.py — turn findings into things you can actually script.

THE GAP THIS FILLS
------------------
Everything else in this engine produces FINDINGS:

    "memory stocks — opportunity 53.6, demand 52, supply 46,
     strongest cluster 'crash sell-off', high-lift tag 'DRAM cycle'"

You cannot write a script from that. It tells you a market is
under-served; it does not tell you what to say. The missing step is
the ANGLE — a specific, structured take with a hook already implied:

    "Micron nearly went bankrupt in 2018. The exact conditions that
     caused it are back. Here's the 6-month window that decides
     whether it repeats."

Same topic, completely different artifact. The angle has a hook, a
stake, a timeframe and an implied structure. That transformation is
editorial judgement — no amount of percentile math produces it — so
this module does NOT attempt it. It assembles the evidence an AI needs
and writes the prompt, exactly like the SEO report's handoff section.
The reasoning happens on the human's side of the loop.

TWO OUTPUTS
-----------
angles_prompt.md      Everything needed to generate angles: the
                      clusters, the breakout titles that define each,
                      the supply/demand context that says which angles
                      are under-served, and the request itself.

transcript_study.md   A shortlist of videos worth transcribing, with
                      clickable URLs, plus the prompt for analysing
                      several transcripts together. Transcripts are
                      fetched MANUALLY by the user — see the note in
                      write_transcript_study for why this module does
                      not download them.
"""

import os

import pandas as pd

from config import EXPORTS_DIR, EXCLUDE_SHORTS_UNDER_SECONDS
from paths import export_dir_for


# How many videos to shortlist for transcript study. Five is the number
# the workflow is built around: enough for a pattern to repeat, few
# enough to paste into one chat without truncation (~50k tokens).
TRANSCRIPT_SHORTLIST = 8

# How many angles to request. Enough that weak ones can be discarded
# and something still remains.
ANGLE_COUNT = 15


def _drop_shorts(df):
    """Shorts are excluded from both outputs.

    For angle generation a Short's structure teaches nothing about a
    long-form script. For transcript study the reason is starker: a
    45-second video has no structure to extract.
    """
    if "duration_seconds" not in df.columns:
        return df
    secs = pd.to_numeric(df["duration_seconds"], errors="coerce")
    return df[(secs >= EXCLUDE_SHORTS_UNDER_SECONDS) | secs.isna()]


def _fmt_pillars(grade_result):
    """One line of market context, or nothing if we weren't given a grade."""
    if not grade_result:
        return None
    p = grade_result.get("pillars") or {}
    d = (p.get("demand") or {}).get("score")
    s = (p.get("supply") or {}).get("score")
    m = (p.get("momentum") or {}).get("signed_change")
    if d is None or s is None:
        return None
    bits = [f"demand {d:.0f}", f"supply {s:.0f}"]
    if m is not None:
        bits.append(f"momentum {m:+.0f} (0 = breakeven)")
    verdict = grade_result.get("verdict", "")
    return " · ".join(bits) + (f" — {verdict}" if verdict else "")


# ============================================================
# ANGLE GENERATION
# ============================================================

def write_angles_prompt(scored_df, clusters, niche_name,
                        grade_result=None, seo_summary=None) -> str:
    """
    Write angles_prompt.md into the niche's export folder.

    Deliberately includes the SUPPLY side as well as the winners. An
    angle is only worth anything if it is under-served — a great hook
    on a saturated theme is still a saturated theme — so the prompt
    gives the model both what is working and how crowded each cluster
    already is, and asks it to weigh them against each other.
    """
    df = _drop_shorts(scored_df)
    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "prompts")
    path = os.path.join(out_dir, "angles_prompt.md")

    L = [f"# Angle generation — {niche_name}", ""]
    L.append("## How to use this")
    L.append("")
    L.append("Paste everything below the divider into Claude or ChatGPT. "
             "It returns specific angles you can script from, not topics. "
             "The data it needs is already embedded — you don't add "
             "anything.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Prompt (paste from here)")
    L.append("")
    L.append(f"I run a faceless YouTube channel in the **{niche_name}** "
             f"niche. Below is real performance data from "
             f"{len(df):,} videos.")
    L.append("")

    ctx = _fmt_pillars(grade_result)
    if ctx:
        L.append(f"**Market context:** {ctx}")
        L.append("")

    # --- Clusters: the recurring themes, with crowding signal ---
    if clusters:
        L.append("### Recurring themes (clusters)")
        L.append("")
        L.append("Each theme below appears across multiple channels, so it "
                 "is a market signal rather than one creator repeating "
                 "themselves. `small_creator_share` is the fraction of "
                 "videos in that theme from channels under 10k subs — a "
                 "high share means small channels can win there.")
        L.append("")
        L.append("| theme | videos | channels | avg outlier | small-creator share |")
        L.append("|---|---|---|---|---|")
        for c in clusters[:10]:
            share = c.get("small_creator_share")
            share_s = f"{share:.0%}" if share is not None else "?"
            L.append(f"| {c['topic']} | {c['videos']} | "
                     f"{c['unique_channels']} | "
                     f"{c.get('avg_outlier_ratio', '?')} | {share_s} |")
        L.append("")
        L.append("Example titles from the strongest themes:")
        L.append("")
        for c in clusters[:5]:
            L.append(f"- **{c['topic']}**")
            for t in c.get("top_titles", [])[:3]:
                L.append(f"    - {t}")
        L.append("")

    # --- Small-channel winners: what worked WITHOUT an audience ---
    if "channel_subs" in df.columns and "outlier_ratio" in df.columns:
        subs = pd.to_numeric(df["channel_subs"], errors="coerce")
        small = df[(subs < 100_000).fillna(False)]
        if len(small) >= 5:
            top_small = small.nlargest(12, "outlier_ratio")
            L.append("### Videos that won WITHOUT a large audience")
            L.append("")
            L.append("These are from channels under 100k subs, ranked by "
                     "how far each video beat its own channel's average. "
                     "They won on topic, angle and packaging rather than "
                     "on an existing subscriber base — so their choices "
                     "are the most transferable.")
            L.append("")
            L.append("| title | subs | beat own average by |")
            L.append("|---|---|---|")
            for _, r in top_small.iterrows():
                title = str(r.get("title", ""))[:78].replace("|", "\\|")
                s = int(r["channel_subs"]) if pd.notna(r.get("channel_subs")) else "?"
                o = r.get("outlier_ratio")
                o_s = f"{float(o):.1f}x" if pd.notna(o) else "?"
                L.append(f"| {title} | {s:,} | {o_s} |" if isinstance(s, int)
                         else f"| {title} | {s} | {o_s} |")
            L.append("")

    if seo_summary:
        L.append("### Packaging signals")
        L.append("")
        L.append(seo_summary)
        L.append("")

    # --- The request ---
    L.append("### What I want")
    L.append("")
    L.append(f"Generate **{ANGLE_COUNT} angles**, not topics.")
    L.append("")
    L.append("The difference matters. A topic is *\"the history of the "
             "cold war\"*. An angle is *\"the 7 minutes that almost ended "
             "the world, and why the person who stopped it was a "
             "44-year-old submarine officer who disobeyed a direct "
             "order\"*. The topic is generic; the angle is already a "
             "script.")
    L.append("")
    L.append("For each angle give me:")
    L.append("")
    L.append("1. **The angle** — one sentence, specific enough that I "
             "could start writing.")
    L.append("2. **The hook** — the actual first line, or the question "
             "the opening frames pose.")
    L.append("3. **Entry point** — where the story starts. Not the "
             "beginning; the moment that earns attention.")
    L.append("4. **Why it's under-served** — reference the cluster and "
             "supply data above. If a theme is crowded, say what makes "
             "this take different rather than pretending it isn't.")
    L.append("5. **Format match** — explainer, case study, "
             "countdown, contrarian take, timeline, teardown, etc.")
    L.append("")
    L.append("Constraints:")
    L.append("")
    L.append("- Prefer angles a channel with no audience could win with. "
             "Look at the small-channel winners above for what that "
             "looks like in this niche.")
    L.append("- Spread across themes. Don't give me 10 variations of the "
             "strongest cluster.")
    L.append("- Be concrete. \"Explain the DRAM cycle\" is a topic. "
             "\"The 1998 DRAM crash playbook, and the three signals that "
             "say it's repeating\" is an angle.")
    L.append("- If any angle depends on a fact you're unsure about, flag "
             "it so I verify before scripting.")
    L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path


# ============================================================
# TRANSCRIPT STUDY
# ============================================================

def write_transcript_study(scored_df, niche_name,
                           n=TRANSCRIPT_SHORTLIST, out_dir=None) -> str:
    """
    Write transcript_study.md — a shortlist of videos worth transcribing
    plus the prompt for analysing them together.

    WHY THIS DOESN'T FETCH TRANSCRIPTS
    ----------------------------------
    YouTube's official captions.download endpoint only works for videos
    you own — it authenticates as the channel owner, so it is useless
    for studying competitors. Every library that appears to solve this
    (youtube-transcript-api and friends) works by calling an internal,
    undocumented endpoint the web player uses. It works today and many
    projects rely on it, but it is not sanctioned, it can break without
    notice, and it arguably conflicts with the terms of service.

    Everything else in this engine runs on officially supported APIs.
    Adding a dependency in a different risk class — one that could
    silently stop working and take a feature down with it — is not
    worth it when the manual path costs a few minutes: open the URL,
    click the transcript button, copy.

    So this writes the shortlist and the URLs; you fetch the text.

    SELECTION: ranked by outlier_ratio among videos from channels
    under 100k subs where possible. A video that beat its own channel's
    average by 8x had something structural going for it. A video from a
    3M-subscriber channel might just have had 3M subscribers.
    """
    df = _drop_shorts(scored_df)
    out_dir = out_dir or export_dir_for(EXPORTS_DIR, niche_name, "prompts")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "transcript_study.md")

    rank_col = "outlier_ratio" if "outlier_ratio" in df.columns else "breakout_score"
    pool = df
    if "channel_subs" in df.columns:
        subs = pd.to_numeric(df["channel_subs"], errors="coerce")
        small = df[(subs < 100_000).fillna(False)]
        # Only prefer the small-channel pool if it can actually fill the
        # shortlist; otherwise fall back to everything rather than
        # returning a 2-item list.
        if len(small) >= n:
            pool = small

    picks = pool.nlargest(n, rank_col) if rank_col in pool.columns else pool.head(n)

    L = [f"# Transcript study — {niche_name}", ""]
    L.append("## Step 1 — collect the transcripts")
    L.append("")
    L.append(f"These {len(picks)} videos outperformed their own channels "
             f"by the widest margin. Open each, click the **⋯ → Show "
             f"transcript** button under the video, and copy the text.")
    L.append("")
    L.append("> **Read the titles before you start copying.** This engine "
             "has no topical relevance filter — it matches on search "
             "results, not meaning — so an off-topic video can occasionally "
             "reach this list. With only 8 videos, one wrong entry is 12% "
             "of your sample, which is enough to distort the pattern. If a "
             "title clearly is not about this niche, skip it and use the "
             "next one down from the breakout report.")
    L.append("")
    L.append("Skip any that have no transcript available — not every "
             "video does, and auto-generated ones are imperfect but "
             "usable for structure.")
    L.append("")
    for i, (_, r) in enumerate(picks.iterrows(), 1):
        vid = str(r.get("video_id", ""))
        title = str(r.get("title", ""))[:80]
        ch = str(r.get("channel_title", ""))
        o = r.get("outlier_ratio")
        o_s = f"{float(o):.1f}x own average" if pd.notna(o) else ""
        subs = r.get("channel_subs")
        subs_s = f"{int(subs):,} subs" if pd.notna(subs) else ""
        meta = " · ".join(x for x in (ch, subs_s, o_s) if x)
        L.append(f"{i}. **{title}**")
        L.append(f"   https://www.youtube.com/watch?v={vid}")
        L.append(f"   _{meta}_")
        L.append("")

    L.append("---")
    L.append("")
    L.append("## Step 2 — the analysis prompt")
    L.append("")
    L.append("Paste the transcripts into one chat, then this:")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"Above are transcripts from {len(picks)} videos in the "
             f"**{niche_name}** niche. Each one significantly "
             f"outperformed its own channel's average, so whatever they "
             f"share is more likely to be structural than incidental.")
    L.append("")
    L.append("Find the patterns they have in common:")
    L.append("")
    L.append("1. **Hook type** — how does each open? Question, bold "
             "claim, story cold-open, statistic, contradiction? Which "
             "type appears across more than one winner?")
    L.append("2. **Entry point** — where in the story does each start? "
             "Do they begin at the beginning, or drop in at a moment of "
             "tension and backfill?")
    L.append("3. **Pacing** — how long before the first substantive "
             "payoff? Where do they place the biggest reveal? Is there "
             "a consistent rhythm to how they break up sections?")
    L.append("4. **Structure** — chronological, problem-solution, "
             "list, argument-counterargument, case study? Which "
             "recurs?")
    L.append("5. **What they DON'T do** — omissions are as informative "
             "as habits. Do none of them open with a channel intro? "
             "Does none of them explain background before hooking?")
    L.append("")
    L.append("Then tell me:")
    L.append("")
    L.append("- Which patterns appear in **most** of them (likely "
             "transferable) versus only one (likely idiosyncratic).")
    L.append("- Which patterns are plausibly **causal** versus which "
             "just mark a creator who puts in more effort generally.")
    L.append("- A **skeleton** for a script in this niche, built from "
             "the patterns that showed up repeatedly.")
    L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path
