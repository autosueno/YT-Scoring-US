"""
docs.py — one source of truth for what this engine does.

WHY THIS EXISTS
---------------
Three documents describe this project to a reader: the README, a
workflow prompt meant to be pasted into a chat model, and a pastel HTML
reference page. All three were hand-written, and all three went stale
the moment a menu option was added — the prompt still claimed the menu
ended at "8) Quit" after a ninth was added, so a model reading it would
confidently deny that channel teardown exists. Wrong documentation is
worse than none, because it is believed.

The fix is not discipline. It is removing the opportunity to drift: the
menu and the workflows are declared ONCE here, as data, and every
document is rendered from that declaration. Adding a menu option means
editing one list, then running:

    py main.py --docs

MODULE INVENTORY is read off the filesystem rather than listed, for the
same reason — a hand-kept list of 21 modules is a hand-kept list that
will say 13 in a month.
"""

import os
from datetime import datetime, timezone

# ============================================================
# THE DECLARATION — edit here, nowhere else
# ============================================================

# Menu groups: (group_label, cost_colour, [(key, label, cost, blurb), ...])
# cost: "dear" | "cheap" | "free" | "api" | None
MENU_GROUPS = [
    ("BUILD — uses your API quota", "dear", [
        ("1",  "Build a niche database",          "dear",
         "Collects the dataset everything else reads. ~13,000 units."),
        ("2",  "Grade a topic idea",               "cheap",
         "Is there a market for this specific idea? ~1,000 units."),
        ("3",  "Search intent & keyword signals",  "free",
         "What people type, what Reddit is talking about. No YouTube quota."),
        ("4",  "Tear down a channel",              "cheap",
         "One creator's whole catalogue. ~13 units for 300 videos."),
        ("5", "Compare two channels",             "cheap",
         "Fetch two channels and diff them side by side."),
    ]),
    ("EXPLORE — reads what you already have", "free", [
        ("6",  "Discover breakout opportunities",  "free",
         "Breakouts, clusters, SEO, calendars, AI prompts."),
        ("7",  "Niche snapshot",                   "free",
         "Quick readout of what you have collected."),
        ("8",  "Score a niche against your library","free",
         "Where does this niche rank among all your builds?"),
        ("9",  "Thumbnail contact sheet",          "free",
         "20 thumbnails tiled for visual study."),
        ("10",  "What's moved since last build",    "free",
         "Measured growth between two snapshots. Needs 2+ builds."),
        ("11", "Compare all niches side by side",  "free",
         "Every build in one table, plus a scored verdict."),
    ]),
    ("MANAGE — maintain your data", "free", [
        ("12", "Rebuild from cleaned data",        "free",
         "Re-derive everything from a cleaned videos.csv — no API calls."),
        ("13", "Slice library to a shorter window","free",
         "Turn 365d builds into 30d/90d slices, free, from data you have."),
        ("14", "Quit",                             None, ""),
    ]),
]

# Flat list for backwards-compat with code that iterates MENU
MENU = [item for _, _, items in MENU_GROUPS for item in items]


# ============================================================
# MODULE DESCRIPTIONS
# What each file does, in plain English, + example use-cases.
# ============================================================

MODULE_DESCRIPTIONS = {
    "api_keys": {
        "what": "Manages your 12 YouTube API keys — loads them from .env, "
                "chains them so you don't burn one out, and reserves the last "
                "two for topic grading.",
        "example": "You don't call this directly. It runs in the background "
                   "every time the engine hits YouTube.",
    },
    "angles": {
        "what": "Generates the AI prompt file you paste into Claude to get "
                "video angle ideas. Takes the breakout clusters and formats "
                "them as a research brief.",
        "example": "After running option 6, open angles_prompt.md and paste "
                   "it into Claude to get 15 specific angle ideas for the niche.",
    },
    "baseline": {
        "what": "Computes the niche's own percentile anchors — what's a "
                "typical view count, what's a typical upload rate. Everything "
                "else compares against these numbers.",
        "example": "You never call this directly. It runs automatically when "
                   "you build or load a niche.",
    },
    "calendars": {
        "what": "Produces a publishing calendar based on when the winning "
                "videos in a niche were uploaded. Tells you which days and "
                "times have historically produced the most breakouts.",
        "example": "After running option 6, open the timing/ folder to see "
                   "the best days and hour-blocks to publish.",
    },
    "calibration": {
        "what": "Holds the calibrated thresholds for market-size and spread "
                "labels — what counts as a 'large' market, what makes a "
                "spread 'gentle' vs 'brutal'. Separately calibrated at 30d, "
                "90d, and 365d from real niche data.",
        "example": "When you see 'gentle for large markets' on a niche "
                   "summary, that label came from this module.",
    },
    "channel": {
        "what": "Fetches and analyses a single channel's full video catalogue "
                "— cadence, trajectory, breakout rate, title patterns, tags. "
                "Also computes the diff between two channels.",
        "example": "Option 4: tear down a channel you admire to see exactly "
                   "how they operate. Option 5: compare two channels side "
                   "by side to see where they diverge.",
    },
    "collector": {
        "what": "The part that actually talks to YouTube. Runs the searches, "
                "downloads video metadata, handles key rotation, retries "
                "failed requests, and saves everything to disk.",
        "example": "Option 1. Everything you can explore for free was "
                   "collected by this module at some point.",
    },
    "compare": {
        "what": "Loads all your built niches and lines them up in one table "
                "so you can compare market size, spread, and scores at a "
                "glance.",
        "example": "Option 11: 'I have 10 niches built. Which one has the "
                   "best spread for a newcomer?' — one command, one table.",
    },
    "config": {
        "what": "All the tunable numbers in one place — minimum videos per "
                "cluster, breakout score weights, spread calibration "
                "thresholds, bootstrap resamples. Change a number here and "
                "it flows everywhere that uses it.",
        "example": "If you want to see more clusters in a breakout report, "
                   "raise CLUSTER_MAX here.",
    },
    "coverage": {
        "what": "Checks how much of the niche's content actually got picked "
                "up by the cluster analysis. Warns you when a large share of "
                "videos don't cluster — which usually means contamination or "
                "a very fragmented niche.",
        "example": "When a breakout report says '32% unmatched — possible "
                   "contamination', that warning came from this module.",
    },
    "crossniche": {
        "what": "Grades a niche against all your other niches rather than "
                "against itself. This is what makes option 8 useful — "
                "without it, every niche would score identically on demand "
                "and supply.",
        "example": "Option 8: 'Is ancient_history a strong niche or a weak "
                   "one compared to the 9 others I've built?' — this module "
                   "does the pooled comparison.",
    },
    "dashboard": {
        "what": "Produces the market dashboard export — a one-page summary "
                "of a niche's key numbers formatted for quick review.",
        "example": "After running option 6, open the market/ folder for the "
                   "dashboard.",
    },
    "discover": {
        "what": "The main analysis engine. Scores every video as a breakout "
                "candidate, groups them into topic clusters, and writes "
                "everything out — the research MD, the discovery MD, the "
                "clusters CSV, the JSON, the video table.",
        "example": "Option 6. This is the module you interact with most, "
                   "even if you never open it.",
    },
    "docs": {
        "what": "Generates README.md, WORKFLOW_PROMPT.md, and "
                "workflows.html from the declarations in this file. "
                "Nothing in those three files is hand-written.",
        "example": "Run 'py main.py --docs' after changing the menu or "
                   "adding a workflow to keep all three documents current.",
    },
    "grader": {
        "what": "Scores a single topic or niche across the three pillars — "
                "demand, supply, momentum — and combines them into an "
                "opportunity score.",
        "example": "Option 2: 'Is there a market for a video about "
                   "Excel automation?' — grader runs and returns a score "
                   "with pillar breakdown.",
    },
    "insights": {
        "what": "Derives summary statistics from a video dataset — "
                "lifecycle, evergreen ratio, engagement patterns, "
                "creator-size distribution. Written to insights.json "
                "alongside every build.",
        "example": "When a niche summary tells you '72% of content is "
                   "evergreen', that reading came from this module.",
    },
    "main": {
        "what": "The entry point — the numbered menu you interact with. "
                "Reads your input, calls the right module, and handles "
                "the interactive flows like picking a niche or a window.",
        "example": "This is what runs when you type 'py main.py'.",
    },
    "metrics": {
        "what": "Computes per-video derived metrics from the raw API "
                "data — views_per_hour, outlier_ratio, breakout_velocity, "
                "engagement_rate. These are the numbers every other module "
                "reasons about.",
        "example": "You never call this directly. It runs as part of "
                   "option 1 when building a niche.",
    },
    "paths": {
        "what": "Handles all the file path logic — where datasets live, "
                "where exports go, how to name files with niche and window "
                "so nothing overwrites anything else.",
        "example": "If you ever wonder why a file is named "
                   "'ancient_history_365d_breakout.csv', the naming "
                   "convention is enforced here.",
    },
    "quota_tracker": {
        "what": "Tracks how many YouTube API units you've spent this "
                "session, per key. Shows the bar at the end of every run "
                "so you can see what you have left.",
        "example": "The 'Quota used: 22,497 / 120,000 units' line printed "
                   "after every build comes from this module.",
    },
    "report": {
        "what": "Formats and prints results to the terminal — niche "
                "summaries, grade reports, quota bars. Also the module "
                "that adds calibration warnings when a number shouldn't "
                "be fully trusted.",
        "example": "The formatted output you see in the terminal after "
                   "running option 7 or 8 is this module rendering the "
                   "numbers.",
    },
    "seo": {
        "what": "Analyses which title words, hashtags, and tags are "
                "most associated with breakout videos. Uses a "
                "Wilson-score-corrected lift ranking so small-sample "
                "noise doesn't dominate.",
        "example": "After option 6, open packaging/seo.md to see which "
                   "title words and tags the winners used more than average.",
    },
    "shorts": {
        "what": "Analyses the Shorts that were filtered out of the main "
                "dataset. Finds which topics are busy in short form but "
                "thin in long-form.",
        "example": "After option 6, open shorts/shorts.md to see if any "
                   "Shorts-heavy topics are underserved in full-length "
                   "format.",
    },
    "signals": {
        "what": "Gathers demand signals from outside YouTube — Google "
                "autocomplete (what people are searching) and Reddit "
                "(what communities are discussing). Produces the intent "
                "gap report.",
        "example": "Option 3: 'What are people searching that nobody has "
                   "covered yet in this niche?' — this module finds those "
                   "gaps.",
    },
    "statistics": {
        "what": "Computes and saves the summary statistics for a niche "
                "dataset — percentiles, medians, channel-size breakdown. "
                "The numbers that appear in every niche summary.",
        "example": "The 'median views: 4,200' line in a niche snapshot "
                   "comes from the statistics.csv this module writes.",
    },
    "thumbnail_sheet": {
        "what": "Downloads thumbnails for the top breakout videos and "
                "tiles them into a contact sheet image for visual study.",
        "example": "Option 9: 'Show me what the winning thumbnails in this "
                   "niche actually look like' — contact sheet of 20 images.",
    },
    "timing": {
        "what": "Analyses publish-time patterns — which hour blocks and "
                "day-of-week combinations produced the most breakouts. "
                "Uses shrinkage and bootstrap CIs so thin time slots "
                "don't look falsely confident.",
        "example": "After option 6, open timing/ to see the best hour "
                   "blocks to publish in this niche.",
    },
    "trends": {
        "what": "Compares two builds of the same niche taken at different "
                "times to measure actual change — views gained, new "
                "creators, rising and falling topics.",
        "example": "Option 10: 'Has this niche actually grown since I last "
                   "built it?' — trends does the before/after comparison.",
    },
    "verdict": {
        "what": "The composite scoring system — combines Entry, Durability, "
                "Reliability, Reward, and Effort into one score per niche, "
                "then ranks and compares them. Produces the verdict HTML "
                "report.",
        "example": "Option 11 → verdict HTML: 'Of my 10 niches, which "
                   "should I actually enter?' — verdict scores and ranks "
                   "them on the five dimensions.",
    },
}


# ============================================================
# WORKFLOWS
# ============================================================

WORKFLOWS = [
    {
        "name": "Find what to make",
        "aim": "You have a niche. You need ideas that are genuinely "
               "under-served, not just ideas.",
        "chain": [("key", "1", "build"), ("arrow",), ("key", "6", "discover"),
                  ("arrow",), ("file", "breakout_research.md"), ("arrow",),
                  ("step", "paste into Claude"), ("arrow",),
                  ("key", "2", "grade the best 2-3")],
        "note": "The main loop — everything else supports it. Don't skip "
                "the grading step. **An angle that reads beautifully can "
                "still sit in a saturated corner**, and 1,000 units is "
                "cheap insurance against a week of wasted production.",
        "cost": "13,000 units to build · ~1,000 per topic graded",
        "tint": "dear",
    },
    {
        "name": "Validate one idea",
        "aim": "You already know what you want to make. Is the market "
               "actually there?",
        "chain": [("key", "2", "grade topic against niche")],
        "note": "Read the **demand-minus-supply imbalance**, not the "
                "headline score. Demand well above supply is the signal. "
                "Momentum tells you whether you're early or late.",
        "cost": "~1,000 units · needs a niche built first",
        "tint": "cheap",
    },
    {
        "name": "Decide which niche to enter",
        "aim": "You have several niches built. Which one should you "
               "actually commit to?",
        "chain": [("key", "11", "compare"), ("arrow",),
                  ("file", "_verdict.html")],
        "note": "Option 11 shows the table. At the end it offers a "
                "scored verdict — say yes. It measures **whether a small "
                "channel ever wins here**, whether the catalogue compounds, "
                "how concentrated the outcomes are, and what a hit is "
                "worth. No quota — reads videos.csv from disk.",
        "cost": "Free · reads videos.csv from disk",
        "tint": "free",
    },
    {
        "name": "Cross-niche topic research",
        "aim": "You want title ideas that borrow proven vocabulary from "
               "adjacent niches, not just your main one.",
        "chain": [("key", "6", "discover main niche"), ("arrow",),
                  ("file", "breakout_discovery.md"), ("arrow",),
                  ("step", "repeat for 1-2 crossover niches"), ("arrow",),
                  ("step", "paste all 3 + your prompt into Claude")],
        "note": "The discovery MD is lean on purpose — **6-8KB per file** "
                "so pasting three together doesn't blow the context window. "
                "Your standalone prompt does the ranking and title "
                "generation; the files just carry the cluster data.",
        "cost": "Free · all three reads are from disk",
        "tint": "free",
    },
    {
        "name": "Clone a channel you admire",
        "aim": "One creator is doing exactly what you want to do. What "
               "exactly are they doing?",
        "chain": [("key", "4", "teardown"), ("arrow",),
                  ("file", "teardown.md"), ("arrow",),
                  ("file", "thumbnails/")],
        "note": "Cadence, growth trajectory, their own breakouts, title "
                "**templates**, tags and thumbnails. Costs ~13 units "
                "because a catalogue comes from the uploads playlist "
                "(1 unit per 50) rather than search (100 per call). "
                "**Format transfers; specific videos don't.**",
        "cost": "~13 units for a 300-video channel",
        "tint": "cheap",
    },
    {
        "name": "Compare two channels directly",
        "aim": "How do two creators in the same space actually differ — "
               "who's performing better, and why?",
        "chain": [("key", "5", "compare two channels"), ("arrow",),
                  ("file", "diff.md"), ("arrow",),
                  ("file", "diff.json")],
        "note": "Performance, cadence, format, trajectory, title language "
                "overlap and tag overlap — all in one diff. "
                "**Costs about 26 units total** (13 per channel catalogue).",
        "cost": "~26 units total",
        "tint": "cheap",
    },
    {
        "name": "Learn how to package it",
        "aim": "Topic decided. Now: how do the winners title and "
               "thumbnail it?",
        "chain": [("key", "6", "discover"), ("arrow",),
                  ("file", "packaging/seo.md"), ("arrow",),
                  ("key", "9", "contact sheet")],
        "note": "Tag lift, title word lift, hashtag patterns. "
                "**Lift is correlational** — it shows what winners "
                "did, not what caused the winning.",
        "cost": "Free after the build",
        "tint": "free",
    },
    {
        "name": "Research keywords",
        "aim": "What do people actually type into the search box?",
        "chain": [("key", "3", "autocomplete"), ("arrow",),
                  ("file", "intent_gaps.md"), ("arrow",),
                  ("step", "paste into Claude for triage")],
        "note": "Google autocomplete harvest — what gets suggested tells "
                "you what's being searched. The intent gap report crosses "
                "this against your niche to find **what's searched but "
                "not covered**. Matching is lexical, so triage with AI "
                "before acting.",
        "cost": "Free · no YouTube quota",
        "tint": "free",
    },
    {
        "name": "Find what's coming",
        "aim": "Cover something before the wave reaches YouTube.",
        "chain": [("key", "3", "Reddit signals"), ("arrow",),
                  ("file", "signals/reddit_signals.md")],
        "note": "Terms climbing on Reddit but thin on YouTube. "
                "**Treat these as leads to check, not forecasts** — the "
                "idea that social chatter reliably precedes YouTube is "
                "widely repeated and never actually demonstrated.",
        "cost": "Free · no YouTube quota",
        "tint": "free",
    },
    {
        "name": "Compare at a shorter window",
        "aim": "You have 365d builds. You want to see what the last "
               "90 days looks like across all of them at once.",
        "chain": [("key", "13", "slice library to 90d"), ("arrow",),
                  ("key", "11", "compare at 90d")],
        "note": "Slicing is free — it re-filters the data you already "
                "paid for. Option 13 does all your niches in one go. "
                "Then option 11 will ask which window you want — "
                "**type 90** (not 90d) and it compares the slices.",
        "cost": "Free · zero API calls",
        "tint": "free",
    },
    {
        "name": "See what actually moved",
        "aim": "Is this niche going anywhere since you last looked?",
        "chain": [("key", "10", "trends"), ("arrow",),
                  ("file", "trends/since_last_build.md")],
        "note": "The only **measured** demand figure the engine produces. "
                "Everywhere else views_per_hour is a lifetime average — "
                "two snapshots give actual views gained, separating "
                "**still being watched** from **finished months ago**. "
                "Needs two builds of the same niche, days apart.",
        "cost": "Free · reads the history/ folder",
        "tint": "free",
    },
    {
        "name": "See which themes actually last",
        "aim": "Is this format durable, or was it a moment?",
        "chain": [("key", "6", "discover"), ("arrow",),
                  ("file", "breakouts/by_era.md")],
        "note": "Breakouts split into 0-90d / 90-180d / 180-365d, each "
                "ranked against its own contemporaries. A theme appearing "
                "in all three eras is a **format**; one appearing only in "
                "the newest is a news cycle.",
        "cost": "Free · slices the build you already have",
        "tint": "free",
    },
    {
        "name": "Find a repeatable format",
        "aim": "A series, not a one-off.",
        "chain": [("key", "6", "discover"), ("arrow",),
                  ("file", "breakouts/breakout_clusters.csv"), ("arrow",),
                  ("step", "sort by unique_channels")],
        "note": "A cluster where seven or more separate channels succeeded "
                "is a **format**, not a fluke. That's the difference "
                "between one good video and a content engine.",
        "cost": "Free after the build",
        "tint": "free",
        "wide": True,
    },
]


LIMITS = [
    ("Tell you whether <em>your</em> video will work",
     "It never sees your channel. Every number describes the market, not "
     "your odds in it."),
    ("Judge whether a video is on-topic",
     "There's no topical relevance filter, deliberately. Occasional "
     "off-topic leaks are the price of not silently dropping things that "
     "only look irrelevant. Run the cleaning workflow on any niche before "
     "treating its numbers as calibration data."),
    ("Measure current demand from one build",
     "A single snapshot cannot separate a video still climbing from one "
     "that stopped a year ago — views_per_hour is a lifetime average. "
     "Option 10 fixes this, but only once you have two builds of the same "
     "niche to compare."),
    ("Measure search demand in the scoring",
     "Scores are built from how existing videos performed. A topic nobody "
     "has covered looks identical to a topic nobody wants. Option 3 gets "
     "close from the outside, but it doesn't feed the scoring yet."),
    ("See why a video worked",
     "Click-through rate, average view duration, retention and traffic "
     "sources are private analytics. No public API exposes them at any "
     "price, for any channel. Everything here is inference from outside."),
    ("Validate the Reliability score",
     "Reliability measures outcome concentration — how narrow the gap "
     "between a decent and a great video is. That's a reasonable proxy "
     "for predictability, but it hasn't been checked against real channel "
     "outcomes. Treat it as a structured heuristic until you have your "
     "own data to compare against."),
]

CAVEATS = [
    ("Niche self-grades are structurally meaningless for demand and supply",
     "Graded against its own baseline, demand is forced to ~64 by the "
     "formula (it blends median percentile with P90 percentile at 65/35, "
     "and a median sits at the 50th percentile of itself by definition). "
     "Supply lands at exactly 50. Only momentum is informative."),
    ("Momentum is signed, 0 = breakeven",
     "+30 means doubled, -30 means halved. A \"C\" means nothing is "
     "changing, which is normal, not bad."),
    ("Opportunity is derived, not measured",
     "It is arithmetic on demand and supply. It doesn't independently "
     "validate anything."),
    ("SEO and tag lift are correlational",
     "High-lift terms don't cause breakouts. Effort drives both good "
     "packaging and good content — common cause, not mechanism."),
    ("The sub-2k tier is survivorship-biased",
     "A channel that small appearing in YouTube search at all is already "
     "an extreme outlier. Case studies of channels that broke through, "
     "not a representative sample."),
    ("outlier_ratio is unstable at low view counts",
     "A channel averaging 15 views that hits 201 scores 13.8x. Breakout "
     "tables apply a minimum-view floor for this reason; entries below "
     "5,000 views are flagged low_evidence elsewhere."),
    ("Timing is one of the weakest levers for a small channel",
     "The mechanism that makes publish time matter — notify subscribers, "
     "spike early velocity, trigger amplification — barely applies "
     "without an audience. Use it to break ties, not to plan strategy."),
]


# ============================================================
# INVENTORY — read, never declared
# ============================================================

def module_inventory(src_dir=None):
    """Actual .py modules on disk. A hand-kept list would lie."""
    src_dir = src_dir or os.path.dirname(os.path.abspath(__file__))
    mods = sorted(
        f[:-3] for f in os.listdir(src_dir)
        if f.endswith(".py") and not f.startswith("_") and f != "docs.py")
    return mods


# ============================================================
# RENDERERS
# ============================================================

def _menu_block():
    lines = []
    for key, label, _cost, _blurb in MENU:
        lines.append(f"{key}) {label}")
    return "\n".join(lines)


def _chain_text(chain):
    """Flatten a chain declaration into plain text for markdown."""
    out = []
    for part in chain:
        if part[0] == "arrow":
            out.append("→")
        elif part[0] == "key":
            out.append(f"[{part[1]}] {part[2]}")
        elif part[0] == "file":
            out.append(f"`{part[1]}`")
        else:
            out.append(part[1])
    return " ".join(out)


def render_workflow_prompt():
    """The paste-into-a-chat-model briefing."""
    L = ["# Paste everything below this line into a new Claude conversation",
         "", "---", ""]
    L.append("You are my analyst and workflow guide for a YouTube market "
             "research engine I built. I'll run the engine on my machine "
             "and upload its output files to you. Your job is twofold: "
             "tell me what to run next, and do the reasoning the engine "
             "deliberately doesn't do.")
    L.append("")
    L.append("## My situation")
    L.append("")
    L.append("- Brand-new faceless YouTube channel, **under 100 "
             "subscribers**. No audience, no algorithmic history.")
    L.append("- Targeting a **US audience** for RPM.")
    L.append("- Current niche: **finance**.")
    L.append("")
    L.append("Filter everything through \"can a channel with no audience "
             "actually execute this?\" Advice that only works with an "
             "existing subscriber base wastes the one thing I can't get "
             "back, which is production time.")
    L.append("")
    L.append("## What the engine is")
    L.append("")
    L.append("A deterministic Python tool that evaluates YouTube topics "
             "**as markets**. Core philosophy: **math discovers, AI "
             "explains**. It never calls an AI internally — it hands me "
             "structured findings, I hand them to you.")
    L.append("")
    L.append("Three pillars: **Demand** (inferred from engagement "
             "velocity and consumption), **Supply** (freshness, upload "
             "acceleration, uploads per creator), **Momentum** (signed "
             "-100 to +100 on a log scale, **0 = breakeven**).")
    L.append("")
    L.append("**Opportunity is derived**: `50 + (demand - supply)/2`, "
             "nudged by momentum, max ±15 points.")
    L.append("")
    L.append("## The menu")
    L.append("")
    L.append("```")
    L.append(_menu_block())
    L.append("```")
    L.append("")
    L.append("## Workflows")
    L.append("")
    for w in WORKFLOWS:
        L.append(f"**{w['name']}** — {w['aim']}")
        L.append("")
        L.append(f"    {_chain_text(w['chain'])}")
        L.append("")
        L.append(f"{w['note']}  _{w['cost']}_")
        L.append("")
    L.append("## Files I'll upload, and what I want from each")
    L.append("")
    for name, want in [
        ("breakout_research.md",
         "Follow the prompt embedded in it. Generate **15 angles, not "
         "topics** — each with the angle, the actual hook line, the entry "
         "point, why it's under-served, and the format match."),
        ("breakout_discovery.md (one or more niches)",
         "These are data-only files for cross-niche work. Apply the "
         "filter step in each file first, then use my standalone prompt "
         "for ranking and title generation across all of them."),
        ("transcript_study.md + pasted transcripts",
         "Analyse **structure**: hook type, entry point, pacing, what "
         "recurs, and critically **what none of them do**. Say which "
         "patterns are transferable versus idiosyncratic."),
        ("_contact_sheet.jpg",
         "Identify visual patterns, focusing on what **small channels** "
         "did differently from incumbents. If they all look identical, "
         "say so — that's a real finding about the niche."),
        ("intent_gaps.md",
         "**Triage.** The matching is lexical, not semantic. Use your own "
         "knowledge to separate genuine gaps from wording mismatches, then "
         "group the real ones into intent clusters."),
        ("reddit_signals.md",
         "Separate real emerging topics from tickers and subreddit noise. "
         "For genuine ones, say whether it's a news spike or something "
         "durable, and how fast I'd need to ship."),
        ("teardown.md (channel)",
         "Tell me whether their success is a **format I could adopt** or a "
         "**personality I can't**. Focus on the title templates and "
         "cadence. Remember you cannot see CTR or retention."),
        ("diff.md (channel comparison)",
         "Tell me what the meaningful differences are between these two "
         "channels, and what a newcomer could learn from each."),
        ("breakout.md, seo.md, grade reports",
         "General analysis. Lead with what's actionable for a sub-100-"
         "subscriber channel."),
    ]:
        L.append(f"### `{name}`")
        L.append("")
        L.append(want)
        L.append("")
    L.append("## Caveats you must respect — do not let me overread the data")
    L.append("")
    for title, body in CAVEATS:
        L.append(f"**{title}.** {body}")
        L.append("")
    L.append("## How I want you to behave")
    L.append("")
    L.append("- **Be direct.** If my angle is weak, say so and why.")
    L.append("- **Push back on thin data.** If a conclusion rests on 3 "
             "videos, say it's directional, not conclusive.")
    L.append("- **Distinguish finding from speculation.** Mark inference.")
    L.append("- **Don't cheerlead.** I want to know whether this works.")
    L.append("- **Ask where I am** before assuming which step I'm on.")
    L.append("")
    L.append("## Start here")
    L.append("")
    L.append("Ask me which stage I'm at and what I've got. If I've already "
             "run something, tell me what to upload.")
    L.append("")
    return "\n".join(L)


_HTML_CSS = """
:root{
  --mint:#C9E4D4; --butter:#F7E5B7; --apricot:#F8D3C0;
  --lilac:#DCD3EC; --sky:#C6DCEB; --lavender:#EBE8F4;
  --paper:#F2F4F7; --card:#FFF; --ink:#2E3440; --ink-2:#5A6474;
  --ink-3:#8B94A3; --rule:#E1E6ED;
  --display:'Space Grotesk',system-ui,sans-serif;
  --body:'Inter',system-ui,sans-serif; --mono:'IBM Plex Mono',monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);
  font-size:16px;line-height:1.6;padding:clamp(24px,5vw,64px) clamp(16px,5vw,48px)}
.wrap{max-width:1040px;margin:0 auto}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 14px}
h1{font-family:var(--display);font-weight:700;font-size:clamp(28px,5vw,48px);
  line-height:1.06;letter-spacing:-.025em;margin:0 0 16px;max-width:18ch}
h2.section-head{font-family:var(--display);font-weight:700;font-size:22px;
  letter-spacing:-.015em;margin:clamp(40px,6vw,64px) 0 20px;color:var(--ink)}
.standfirst{font-size:clamp(15px,2.2vw,18px);color:var(--ink-2);max-width:56ch;margin:0}
/* ---- menu ---- */
.menu{background:var(--card);border:1px solid var(--rule);border-radius:14px;
  padding:clamp(18px,3vw,26px);margin:clamp(28px,5vw,44px) 0}
.menu-title{font-family:var(--mono);font-size:11.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 20px}
.menu-group{margin:0 0 22px}
.menu-group:last-child{margin:0}
.menu-group-label{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 10px;
  padding-bottom:6px;border-bottom:1px solid var(--rule)}
.menu-grid{display:grid;gap:7px;grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.menu-row{display:flex;align-items:center;gap:11px;font-size:14px}
.menu-row span.label{color:var(--ink-2)}
.key{flex:none;display:grid;place-items:center;width:28px;height:28px;border-radius:7px;
  background:var(--sky);border:1px solid rgba(46,52,64,.13);
  box-shadow:0 1.5px 0 rgba(46,52,64,.16);font-family:var(--mono);
  font-weight:600;font-size:13px;color:var(--ink)}
.key.free{background:var(--mint)} .key.cheap{background:var(--butter)}
.key.dear{background:var(--apricot)} .key.api{background:var(--butter)}
.key.ghost{background:var(--card);color:var(--ink-3)}
.legend{display:flex;flex-wrap:wrap;gap:8px 22px;align-items:center;
  margin:0 0 clamp(28px,5vw,40px);font-size:13px;color:var(--ink-2)}
.legend b{display:inline-block;width:14px;height:14px;border-radius:4px;
  vertical-align:-2px;margin-right:6px;border:1px solid rgba(46,52,64,.12)}
/* ---- modules ---- */
.modules{display:grid;gap:12px;margin:0 0 clamp(28px,5vw,44px)}
@media(min-width:640px){.modules{grid-template-columns:1fr 1fr}}
@media(min-width:900px){.modules{grid-template-columns:1fr 1fr 1fr}}
.mod{background:var(--card);border:1px solid var(--rule);border-radius:12px;
  padding:16px 18px}
.mod-name{font-family:var(--mono);font-weight:600;font-size:13px;
  color:var(--ink);margin:0 0 6px}
.mod-what{font-size:13.5px;color:var(--ink-2);margin:0 0 8px;line-height:1.5}
.mod-eg{font-size:12.5px;color:var(--ink-3);font-style:italic;
  border-top:1px solid var(--rule);padding-top:8px;margin:0}
/* ---- workflows ---- */
.jobs{display:grid;gap:16px}
@media(min-width:820px){.jobs{grid-template-columns:1fr 1fr}}
.job{background:var(--card);border:1px solid var(--rule);border-radius:14px;
  padding:clamp(20px,3vw,26px);position:relative;overflow:hidden;
  transition:transform .18s ease,box-shadow .18s ease}
.job::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;
  background:var(--tint,var(--sky))}
.job:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(46,52,64,.07)}
.job.wide{grid-column:1/-1}
.job h2{font-family:var(--display);font-weight:700;font-size:19px;
  letter-spacing:-.015em;margin:0 0 4px}
.aim{color:var(--ink-2);font-size:14px;margin:0 0 16px;max-width:52ch}
.chain{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:12px;
  border-radius:10px;background:var(--paper);margin:0 0 14px}
.chain .arrow{color:var(--ink-3);font-size:13px;flex:none}
.chain .step{font-family:var(--mono);font-size:12px;color:var(--ink-2);
  display:flex;align-items:center;gap:6px}
.chain .file{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);
  background:var(--card);border:1px solid var(--rule);padding:3px 8px;border-radius:6px}
.note{font-size:13.5px;color:var(--ink-2);margin:0 0 14px;max-width:54ch}
.note strong{color:var(--ink);font-weight:600}
.cost{font-family:var(--mono);font-size:11.5px;color:var(--ink-3);
  border-top:1px solid var(--rule);padding-top:12px;margin:0}
/* ---- limits ---- */
.limits{background:var(--lilac);border-radius:14px;padding:clamp(22px,3.5vw,30px);
  margin-top:clamp(28px,5vw,44px)}
.limits h2{font-family:var(--display);font-weight:700;font-size:20px;
  margin:0 0 6px;letter-spacing:-.015em}
.limits p.lead{margin:0 0 18px;color:#4B4460;font-size:14px;max-width:58ch}
.limits dl{margin:0;display:grid;gap:14px}
.limits dt{font-weight:600;font-size:14px;color:#332E45}
.limits dd{margin:3px 0 0;font-size:13.5px;color:#54506B;max-width:62ch}
footer{margin-top:clamp(30px,5vw,48px);padding-top:20px;
  border-top:1px solid var(--rule);font-family:var(--mono);font-size:11px;
  color:var(--ink-3);display:flex;flex-wrap:wrap;gap:6px 18px}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def _md_bold_to_html(text):
    """Minimal **bold** conversion so notes can be written once."""
    parts, out, strong = text.split("**"), [], False
    for p in parts:
        out.append(f"<strong>{p}</strong>" if strong else p)
        strong = not strong
    return "".join(out)


def render_workflows_html():
    """The pastel reference page."""
    tint_map = {"dear": "var(--apricot)", "cheap": "var(--butter)",
                "free": "var(--mint)", "api": "var(--butter)"}
    H = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width, initial-scale=1'>",
         "<title>YouTube Market Intelligence Engine</title>",
         "<link rel='preconnect' href='https://fonts.googleapis.com'>",
         "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>",
         "<link href='https://fonts.googleapis.com/css2?family=Space+Grotesk:"
         "wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&"
         "family=Inter:wght@400;500;600&display=swap' rel='stylesheet'>",
         f"<style>{_HTML_CSS}</style></head><body><div class='wrap'>"]

    # ---- header ----
    H.append("<header><p class='eyebrow'>YouTube Market Intelligence Engine</p>")
    H.append("<h1>What each piece does, and how to chain them.</h1>")
    H.append("<p class='standfirst'>30 modules. 13 menu options. "
             "On their own they don't do much — the value is in chaining "
             "them. Everything below is a chain, priced so you can see "
             "what it costs before you start.</p></header>")

    # ---- menu (grouped) ----
    H.append("<section class='menu'><p class='menu-title'>py main.py</p>")
    for group_label, _tint, items in MENU_GROUPS:
        H.append(f"<div class='menu-group'>"
                 f"<p class='menu-group-label'>{group_label}</p>"
                 f"<div class='menu-grid'>")
        for key, label, cost, _ in items:
            cls = cost or "ghost"
            H.append(f"<div class='menu-row'>"
                     f"<span class='key {cls}'>{key}</span>"
                     f"<span class='label'>{label}</span></div>")
        H.append("</div></div>")
    H.append("</section>")

    H.append("<p class='legend'>"
             "<span><b style='background:var(--apricot)'></b>"
             "~13,000 units (search)</span>"
             "<span><b style='background:var(--butter)'></b>"
             "~1,000 units (metadata)</span>"
             "<span><b style='background:var(--mint)'></b>"
             "Free — reads what you already have</span></p>")

    # ---- modules ----
    H.append("<h2 class='section-head'>What each module does</h2>")
    H.append("<div class='modules'>")
    mods = module_inventory()
    for m in mods:
        desc = MODULE_DESCRIPTIONS.get(m)
        if not desc:
            continue
        H.append(f"<div class='mod'>"
                 f"<p class='mod-name'>{m}.py</p>"
                 f"<p class='mod-what'>{desc['what']}</p>"
                 f"<p class='mod-eg'>{desc['example']}</p>"
                 f"</div>")
    H.append("</div>")

    # ---- workflows ----
    H.append("<h2 class='section-head'>End-to-end workflows</h2>")
    H.append("<div class='jobs'>")
    for w in WORKFLOWS:
        wide = " wide" if w.get("wide") else ""
        tint = tint_map.get(w["tint"], "var(--sky)")
        H.append(f"<article class='job{wide}' style='--tint:{tint}'>")
        H.append(f"<h2>{w['name']}</h2>"
                 f"<p class='aim'>{w['aim']}</p>")
        H.append("<div class='chain'>")
        for part in w["chain"]:
            if part[0] == "arrow":
                H.append("<span class='arrow'>→</span>")
            elif part[0] == "key":
                cost = next((m[2] for m in MENU if m[0] == part[1]), "free")
                H.append(f"<span class='step'>"
                         f"<span class='key {cost}'>{part[1]}</span>"
                         f"{part[2]}</span>")
            elif part[0] == "file":
                H.append(f"<span class='file'>{part[1]}</span>")
            else:
                H.append(f"<span class='step'>{part[1]}</span>")
        H.append("</div>")
        H.append(f"<p class='note'>{_md_bold_to_html(w['note'])}</p>")
        H.append(f"<p class='cost'>{w['cost']}</p></article>")
    H.append("</div>")

    # ---- limits ----
    H.append("<section class='limits'><h2>What it can't do</h2>"
             "<p class='lead'>Worth knowing before you go looking "
             "for these.</p><dl>")
    for title, body in LIMITS:
        H.append(f"<dt>{title}</dt><dd>{body}</dd>")
    H.append("</dl></section>")

    n_mods = len(module_inventory())
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    H.append(f"<footer><span>{n_mods} modules</span>"
             "<span>Deterministic — same data in, same answer out</span>"
             "<span>Math discovers, AI explains</span>"
             f"<span>Generated {ts}</span></footer>")
    H.append("</div></body></html>")
    return "\n".join(H)


def render_readme():
    """Project README, with the module list read off disk."""
    mods = module_inventory()
    L = ["# YouTube Market Intelligence Engine", ""]
    L.append("Evaluates a YouTube topic **as a market**, not as a keyword.")
    L.append("")
    L.append("Guiding principle: **math discovers, AI explains**. Every "
             "number is deterministic and reproducible — the engine never "
             "calls an AI internally. It produces structured findings and "
             "prompt files; you take those to a chat model for the "
             "judgement calls.")
    L.append("")
    L.append("## The model")
    L.append("")
    L.append("Three measured pillars:")
    L.append("")
    L.append("| pillar | measures |")
    L.append("|---|---|")
    L.append("| **Demand** | engagement velocity, consumption, engagement quality |")
    L.append("| **Supply** | content freshness, upload acceleration, uploads per creator |")
    L.append("| **Momentum** | recent window vs prior, signed -100..+100, 0 = breakeven |")
    L.append("")
    L.append("**Opportunity is derived, not measured**: "
             "`50 + (demand - supply)/2`, nudged by momentum (max ±15). "
             "Opportunity exists where inferred demand exceeds content "
             "supply.")
    L.append("")
    L.append("## Setup")
    L.append("")
    L.append("```bash")
    L.append("pip install -r requirements.txt")
    L.append("cp .env.example .env      # add YOUTUBE_API_KEY_1..12")
    L.append("py main.py")
    L.append("```")
    L.append("")
    L.append("## Menu")
    L.append("")
    cost_label = {"dear": "~13,000 units", "cheap": "~1,000 units",
                  "free": "free", "api": "~1,000 units", None: "—"}
    for group_label, _, items in MENU_GROUPS:
        L.append(f"**{group_label}**")
        L.append("")
        L.append("| | action | cost |")
        L.append("|---|---|---|")
        for key, label, cost, blurb in items:
            L.append(f"| **{key}** | {label} | {cost_label.get(cost, '—')} |")
        L.append("")
    L.append("## Output layout")
    L.append("")
    L.append("```")
    L.append("datasets/<niche>/          collected data + baseline")
    L.append("exports/<niche>/")
    L.append("    market/                dashboard")
    L.append("    breakouts/             ranked videos + clusters + research/discovery MDs")
    L.append("    packaging/             SEO analysis, thumbnails")
    L.append("    timing/                publishing calendars")
    L.append("    prompts/               files to paste into an AI")
    L.append("    signals/               autocomplete + Reddit")
    L.append("exports/_channels/<name>/  channel teardowns + diffs")
    L.append("exports/_compare/          niche-vs-niche verdicts")
    L.append("history/<niche>/<date>/    archived previous builds")
    L.append("```")
    L.append("")
    L.append(f"## Modules ({len(mods)})")
    L.append("")
    L.append("```")
    for i in range(0, len(mods), 4):
        L.append("  " + "  ".join(f"{m:<18}" for m in mods[i:i + 4]).rstrip())
    L.append("```")
    L.append("")
    L.append("## Known limits")
    L.append("")
    for title, body in LIMITS:
        clean = body.replace("<em>", "*").replace("</em>", "*")
        L.append(f"- **{title.replace('<em>', '*').replace('</em>', '*')}** "
                 f"— {clean}")
    L.append("")
    L.append("## Regenerating this file")
    L.append("")
    L.append("`README.md`, `WORKFLOW_PROMPT.md` and `workflows.html` are all "
             "generated from the declarations in `docs.py`:")
    L.append("")
    L.append("```bash")
    L.append("py main.py --docs")
    L.append("```")
    L.append("")
    L.append("Edit `docs.py`, not the generated files.")
    L.append("")
    return "\n".join(L)


# ============================================================
# WRITE
# ============================================================

def generate_all(out_dir=None, verbose=True):
    """Write all three documents. Returns {name: path}."""
    out_dir = out_dir or os.path.dirname(os.path.abspath(__file__))
    written = {}
    for fname, content in (
        ("README.md", render_readme()),
        ("WORKFLOW_PROMPT.md", render_workflow_prompt()),
        ("workflows.html", render_workflows_html()),
    ):
        path = os.path.join(out_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written[fname] = path
        if verbose:
            print(f"  {fname:<22} → {path}")
    return written
