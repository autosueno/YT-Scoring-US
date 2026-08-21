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

# (key, label, cost, blurb). cost is "dear" | "cheap" | "free" | None
MENU = [
    ("1", "Build / refresh a niche database", "dear",
     "Collects the dataset everything else reads. ~13,000 units."),
    ("2", "Grade a topic against a niche", "cheap",
     "Is there a market for this specific idea? ~1,000 units."),
    ("3", "Discover opportunities", "free",
     "Breakouts, clusters, SEO, calendars, dashboard, AI prompts."),
    ("4", "Show an existing niche summary", "free",
     "Quick readout of what you have collected."),
    ("5", "Grade an existing niche", "free",
     "Mostly arithmetic against itself — see the limits section."),
    ("6", "Thumbnail contact sheet creator", "free",
     "20 thumbnails tiled for visual study."),
    ("7", "Search intent & Reddit signals", "free",
     "Demand from outside the video data."),
    ("8", "Tear down a single channel", "free",
     "One creator's whole catalogue for ~13 units."),
    ("9", "What changed since the last build", "free",
     "Measured growth between two snapshots. Needs 2+ builds."),
    ("10", "Compare all niches side by side", "free",
     "Every build in one table, plus calibrated spread thresholds."),
    ("11", "Regenerate stats/insights/metadata from an edited videos.csv", "free",
     "Re-derive everything from a cleaned videos.csv — no API calls."),
    ("12", "Bulk-slice your whole niche library to one window", "free",
     "Slice every 365d niche down to 30d/90d/etc at once, from data you already have."),
    ("13", "Quit", None, ""),
]

WORKFLOWS = [
    {
        "name": "Find what to make",
        "aim": "You have a niche. You need ideas that are genuinely "
               "under-served, not just ideas.",
        "chain": [("key", "1", "build"), ("arrow",), ("key", "3", "discover"),
                  ("arrow",), ("file", "angles_prompt.md"), ("arrow",),
                  ("step", "paste into an AI"), ("arrow",),
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
        "name": "Clone a channel you admire",
        "aim": "One creator is doing exactly what you want to do. What "
               "exactly are they doing?",
        "chain": [("key", "8", "teardown"), ("arrow",),
                  ("file", "teardown.md"), ("arrow",),
                  ("file", "thumbnails/"), ("arrow",),
                  ("file", "transcript_study.md")],
        "note": "Cadence, growth trajectory, their own breakouts, title "
                "**templates**, tags and thumbnails. Costs ~13 units "
                "because a catalogue comes from the uploads playlist "
                "(1 unit per 50) rather than search (100 per call). "
                "**Format transfers; specific videos don't.**",
        "cost": "~13 units for a 300-video channel",
        "tint": "free",
    },
    {
        "name": "Learn how to package it",
        "aim": "Topic decided. Now: how do the winners title and "
               "thumbnail it?",
        "chain": [("key", "3", "discover"), ("arrow",),
                  ("file", "packaging/seo.md"), ("arrow",),
                  ("key", "6", "contact sheet")],
        "note": "Twenty thumbnails to study, plus title patterns and tag "
                "lift. **Lift is correlational** — it shows what winners "
                "did, not what caused the winning.",
        "cost": "Free after the build",
        "tint": "free",
    },
    {
        "name": "Learn how to structure it",
        "aim": "Why do the winning videos in this niche actually work?",
        "chain": [("key", "3", "discover"), ("arrow",),
                  ("file", "prompts/transcript_study.md"), ("arrow",),
                  ("step", "copy 8 transcripts"), ("arrow",),
                  ("step", "paste with the included prompt")],
        "note": "Hook type, entry point, pacing, and what none of them do. "
                "**Almost nobody does this**, which is part of why it's "
                "worth the twenty minutes of copying.",
        "cost": "Free · ~20 min of manual work",
        "tint": "free",
    },
    {
        "name": "Find what's coming",
        "aim": "Cover something before the wave reaches YouTube.",
        "chain": [("key", "7", "→ b"), ("arrow",),
                  ("file", "signals/reddit_signals.md")],
        "note": "Terms climbing fast on Reddit but thin on YouTube. "
                "**Treat these as leads to check, not forecasts** — the "
                "idea that social chatter reliably precedes YouTube is "
                "widely repeated and, as far as I know, never actually "
                "demonstrated.",
        "cost": "Free · no YouTube quota",
        "tint": "free",
    },
    {
        "name": "Research keywords",
        "aim": "What do people actually type into the search box?",
        "chain": [("key", "7", "→ a → preset 2"), ("step", "then"),
                  ("step", "preset 5")],
        "note": "Preset 2 covers the niche broadly, about a minute. "
                "Preset 5 is sharper and takes about two: **what people "
                "search around the topics already winning**. Separate "
                "files, so run both.",
        "cost": "Free · 1–3 min depending on preset",
        "tint": "free",
    },
    {
        "name": "Compare niches against each other",
        "aim": "How do these markets differ?",
        "chain": [("key", "10", "compare"), ("arrow",),
                  ("file", "_compare/compare_365d.md")],
        "note": "Every built niche in one table, sorted by spread — the "
                "climb from ordinary to good. With five or more niches it "
                "derives spread thresholds from **your measured data** "
                "instead of the assumed ones. Reads stored metadata, so "
                "no quota and no recompute.",
        "cost": "Free · reads what is already on disk",
        "tint": "free",
    },
    {
        "name": "Decide which niche to break into",
        "aim": "Of these markets, which one should I actually enter?",
        "chain": [("key", "10", "compare"), ("arrow",),
                  ("file", "_compare/<a>_vs_<b>_365d_verdict.html")],
        "note": "Offered at the end of option 10. The table above says how "
                "niches DIFFER; this says which to enter, which needs facts "
                "metadata cannot hold — so it re-reads `videos.csv`. "
                "Measures **whether a small channel ever wins here**, "
                "whether the floor is hobbyists or failed professionals, "
                "whether videos keep earning after six months, how long a "
                "winning video runs, and what a hit is worth. Ad rates "
                "come from `rpm_rates.csv` (edit it in Excel); the ranking "
                "runs at the low, mid and high end of each band and tells "
                "you if the answer flips. Still no quota — seconds per "
                "niche instead of instant.",
        "cost": "Free · reads videos.csv from disk",
        "tint": "free",
    },
    {
        "name": "See what actually moved",
        "aim": "Is this niche going anywhere since you last looked?",
        "chain": [("key", "9", "compare"), ("arrow",),
                  ("file", "trends/since_last_build.md")],
        "note": "The only **measured** demand figure the engine produces. "
                "Everywhere else `views_per_hour` is a lifetime average, "
                "so a video that did 400k views in its first week and "
                "nothing since reports a healthy number forever. Two "
                "snapshots give actual views gained — which separates "
                "**still being watched** from **finished months ago**. "
                "Needs two builds of the same niche, days apart.",
        "cost": "Free · reads the history/ folder",
        "tint": "free",
    },
    {
        "name": "See which themes actually last",
        "aim": "Is this format durable, or was it a moment?",
        "chain": [("key", "3", "discover"), ("arrow",),
                  ("file", "breakouts/by_era.md")],
        "note": "Breakouts split into 0-90d / 90-180d / 180-365d, each "
                "ranked against its own contemporaries. A theme appearing "
                "in all three eras is a **format**; one appearing only in "
                "the newest is a news cycle. Fixes an age bias where "
                "recent videos took 43 of the top 50 and the single "
                "biggest video in the dataset was invisible.",
        "cost": "Free · slices the build you already have",
        "tint": "free",
    },
    {
        "name": "Mine the Shorts you filtered out",
        "aim": "A third of every build is Shorts. What were they about?",
        "chain": [("key", "3", "discover"), ("arrow",),
                  ("file", "shorts/shorts.md")],
        "note": "Subjects busy in short form but thin in long-form — "
                "attention exists, nobody made the full version. "
                "**Leads, not findings**: a Short's views come from the "
                "feed, not from search, so short-form attention doesn't "
                "prove anyone would search for a ten-minute version. "
                "Written automatically; costs nothing extra.",
        "cost": "Free · data was already collected",
        "tint": "free",
    },
    {
        "name": "Find a repeatable format",
        "aim": "A series, not a one-off. At a small channel size this "
               "matters more than any single video decision here.",
        "chain": [("key", "3", "discover"), ("arrow",),
                  ("file", "breakouts/breakout_clusters.csv"), ("arrow",),
                  ("step", "sort by unique_channels")],
        "note": "A cluster where seven or more separate channels succeeded "
                "is a **format**, not a fluke. That's the difference "
                "between one good video and a content engine — your second "
                "video benefits from the first in a way one-off topics "
                "never allow.",
        "cost": "Free after the build",
        "tint": "free",
        "wide": True,
    },
]

LIMITS = [
    ("Compare two niches head to head",
     "Option 5 grades a niche against its own baseline, which forces "
     "demand to about 64 and supply to exactly 50. That's arithmetic, not "
     "information. Cross-niche grading exists in the code but isn't wired "
     "into the menu — for now, run option 3 on each and compare cluster "
     "strength by hand."),
    ("Tell you whether <em>your</em> video will work",
     "It never sees your channel. Every number describes the market, not "
     "your odds in it."),
    ("Judge whether a video is on-topic",
     "There's no topical relevance filter, deliberately. Occasional "
     "off-topic leaks are the price of not silently dropping things that "
     "only look irrelevant."),
    ("Measure current demand from one build",
     "A single snapshot cannot separate a video still climbing from one "
     "that stopped a year ago — views_per_hour is a lifetime average. "
     "Option 9 fixes this, but only once you have two builds of the same "
     "niche to compare."),
    ("Measure search demand in the scoring",
     "Scores are built from how existing videos performed. A topic nobody "
     "has covered looks identical to a topic nobody wants. Option 7 gets "
     "close from the outside, but it doesn't feed the scoring yet."),
    ("See why a video worked",
     "Click-through rate, average view duration, retention and traffic "
     "sources are private analytics. No public API exposes them at any "
     "price, for any channel. Everything here is inference from outside."),
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
    ("Constants are reasonable but not validated",
     "Weights and thresholds were chosen sensibly, not derived from "
     "evidence that high-scoring topics actually outperform. Treat scores "
     "as structured opinion, not measurement."),
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
        ("angles_prompt.md",
         "Follow the prompt embedded in it. Generate **15 angles, not "
         "topics** — each with the angle, the actual hook line, the entry "
         "point, why it's under-served, and the format match."),
        ("transcript_study.md + pasted transcripts",
         "Analyse **structure**: hook type, entry point, pacing, what "
         "recurs, and critically **what none of them do**. Say which "
         "patterns are transferable versus idiosyncratic."),
        ("curation_prompt.md",
         "Pick exactly **20 video IDs** whose thumbnails are worth "
         "studying. Prefer small-channel breakouts and visual variety. "
         "Return IDs only, one per line, no commentary."),
        ("_contact_sheet.jpg",
         "Identify visual patterns, focusing on what **small channels** "
         "did differently from incumbents. If they all look identical, "
         "say so — that's a real finding about the niche."),
        ("intent_gaps.md / cluster_intent.md",
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
  --lilac:#DCD3EC; --sky:#C6DCEB;
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
h1{font-family:var(--display);font-weight:700;font-size:clamp(30px,6vw,52px);
  line-height:1.06;letter-spacing:-.025em;margin:0 0 16px;max-width:16ch}
.standfirst{font-size:clamp(15px,2.2vw,18px);color:var(--ink-2);max-width:56ch;margin:0}
.menu{background:var(--card);border:1px solid var(--rule);border-radius:14px;
  padding:clamp(18px,3vw,26px);margin:clamp(28px,5vw,44px) 0}
.menu-title{font-family:var(--mono);font-size:11.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 16px}
.menu-grid{display:grid;gap:9px;grid-template-columns:repeat(auto-fill,minmax(232px,1fr))}
.menu-row{display:flex;align-items:center;gap:11px;font-size:14.5px}
.menu-row span{color:var(--ink-2)}
.key{flex:none;display:grid;place-items:center;width:29px;height:29px;border-radius:8px;
  background:var(--sky);border:1px solid rgba(46,52,64,.13);
  box-shadow:0 1.5px 0 rgba(46,52,64,.16);font-family:var(--mono);
  font-weight:600;font-size:14px;color:var(--ink)}
.key.free{background:var(--mint)} .key.cheap{background:var(--butter)}
.key.dear{background:var(--apricot)} .key.ghost{background:var(--card);color:var(--ink-3)}
.legend{display:flex;flex-wrap:wrap;gap:8px 22px;align-items:center;
  margin:0 0 clamp(28px,5vw,40px);font-size:13.5px;color:var(--ink-2)}
.legend b{display:inline-block;width:15px;height:15px;border-radius:5px;
  vertical-align:-2px;margin-right:7px;border:1px solid rgba(46,52,64,.12)}
.jobs{display:grid;gap:16px}
@media(min-width:820px){.jobs{grid-template-columns:1fr 1fr}}
.job{background:var(--card);border:1px solid var(--rule);border-radius:14px;
  padding:clamp(20px,3vw,26px);position:relative;overflow:hidden;
  transition:transform .18s ease,box-shadow .18s ease}
.job::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--tint,var(--sky))}
.job:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(46,52,64,.07)}
.job.wide{grid-column:1/-1}
.job h2{font-family:var(--display);font-weight:700;font-size:20px;
  letter-spacing:-.015em;margin:0 0 4px}
.aim{color:var(--ink-2);font-size:14.5px;margin:0 0 18px;max-width:52ch}
.chain{display:flex;flex-wrap:wrap;align-items:center;gap:9px;padding:14px;
  border-radius:10px;background:var(--paper);margin:0 0 16px}
.chain .arrow{color:var(--ink-3);font-size:13px;flex:none}
.chain .step{font-family:var(--mono);font-size:12.5px;color:var(--ink-2);
  display:flex;align-items:center;gap:7px}
.chain .file{font-family:var(--mono);font-size:12px;color:var(--ink-2);
  background:var(--card);border:1px solid var(--rule);padding:4px 9px;border-radius:7px}
.note{font-size:14px;color:var(--ink-2);margin:0 0 16px;max-width:54ch}
.note strong{color:var(--ink);font-weight:600}
.cost{font-family:var(--mono);font-size:12px;color:var(--ink-3);
  border-top:1px solid var(--rule);padding-top:13px;margin:0}
.limits{background:var(--lilac);border-radius:14px;padding:clamp(22px,3.5vw,30px);
  margin-top:clamp(28px,5vw,44px)}
.limits h2{font-family:var(--display);font-weight:700;font-size:20px;
  margin:0 0 6px;letter-spacing:-.015em}
.limits p.lead{margin:0 0 18px;color:#4B4460;font-size:14.5px;max-width:58ch}
.limits dl{margin:0;display:grid;gap:15px}
.limits dt{font-weight:600;font-size:14.5px;color:#332E45}
.limits dd{margin:3px 0 0;font-size:14px;color:#54506B;max-width:62ch}
footer{margin-top:clamp(30px,5vw,48px);padding-top:20px;border-top:1px solid var(--rule);
  font-family:var(--mono);font-size:11.5px;color:var(--ink-3);
  display:flex;flex-wrap:wrap;gap:6px 18px}
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
                "free": "var(--mint)"}
    H = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width, initial-scale=1'>",
         "<title>Workflows — YouTube Market Intelligence Engine</title>",
         "<link rel='preconnect' href='https://fonts.googleapis.com'>",
         "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>",
         "<link href='https://fonts.googleapis.com/css2?family=Space+Grotesk:"
         "wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&"
         "family=Inter:wght@400;500;600&display=swap' rel='stylesheet'>",
         f"<style>{_HTML_CSS}</style></head><body><div class='wrap'>"]

    H.append("<header><p class='eyebrow'>YouTube Market Intelligence Engine"
             "</p>")
    H.append(f"<h1>{len(WORKFLOWS)} jobs, and how to run each one.</h1>")
    H.append("<p class='standfirst'>The menu has "
             f"{len(MENU)} options. On their own they don't do much — the "
             "value is in chaining them. Each workflow below is a chain, "
             "priced so you can see what it costs before you start.</p>"
             "</header>")

    H.append("<section class='menu'><p class='menu-title'>py main.py</p>"
             "<div class='menu-grid'>")
    for key, label, cost, _ in MENU:
        cls = cost or "ghost"
        H.append(f"<div class='menu-row'><span class='key {cls}'>{key}</span>"
                 f"<span>{label}</span></div>")
    H.append("</div></section>")

    H.append("<p class='legend'>"
             "<span><b style='background:var(--apricot)'></b>13,000 units</span>"
             "<span><b style='background:var(--butter)'></b>~1,000 units</span>"
             "<span><b style='background:var(--mint)'></b>No quota — reads "
             "what you already have</span></p>")

    H.append("<div class='jobs'>")
    for w in WORKFLOWS:
        wide = " wide" if w.get("wide") else ""
        H.append(f"<article class='job{wide}' "
                 f"style=\"--tint:{tint_map.get(w['tint'], 'var(--sky)')}\">")
        H.append(f"<h2>{w['name']}</h2><p class='aim'>{w['aim']}</p>")
        H.append("<div class='chain'>")
        for part in w["chain"]:
            if part[0] == "arrow":
                H.append("<span class='arrow'>→</span>")
            elif part[0] == "key":
                cost = next((m[2] for m in MENU if m[0] == part[1]), "free")
                H.append(f"<span class='step'><span class='key {cost}'>"
                         f"{part[1]}</span>{part[2]}</span>")
            elif part[0] == "file":
                H.append(f"<span class='file'>{part[1]}</span>")
            else:
                H.append(f"<span class='step'>{part[1]}</span>")
        H.append("</div>")
        H.append(f"<p class='note'>{_md_bold_to_html(w['note'])}</p>")
        H.append(f"<p class='cost'>{w['cost']}</p></article>")
    H.append("</div>")

    H.append("<section class='limits'><h2>What it can't do</h2>"
             "<p class='lead'>Worth knowing before you go looking for "
             "these.</p><dl>")
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
    L.append("| | action | cost |")
    L.append("|---|---|---|")
    cost_label = {"dear": "~13,000 units", "cheap": "~1,000 units",
                  "free": "free", None: "—"}
    for key, label, cost, blurb in MENU:
        L.append(f"| **{key}** | {label} | {cost_label.get(cost, '—')} |")
    L.append("")
    L.append("## Output layout")
    L.append("")
    L.append("```")
    L.append("datasets/<niche>/          collected data + baseline")
    L.append("exports/<niche>/")
    L.append("    market/                dashboard")
    L.append("    breakouts/             ranked videos + clusters")
    L.append("    packaging/             SEO analysis, thumbnails")
    L.append("    timing/                publishing calendars")
    L.append("    prompts/               files to paste into an AI")
    L.append("    signals/               autocomplete + Reddit")
    L.append("exports/_channels/<name>/  channel teardowns")
    L.append("exports/_compare/          niche-vs-niche verdicts")
    L.append("history/<niche>/<date>/    archived previous builds")
    L.append("```")
    L.append("")
    L.append("Every file carries the name of what it describes — "
             "`ancient_history_365d_statistics.csv`, not `statistics.csv`. "
             "The folder tree already separates subjects, but the folder "
             "does not travel with the file: attach two niches' exports to "
             "one message and the second overwrites the first, and Excel "
             "refuses to open two workbooks of the same name at all. The "
             "window is included because a 365d and a 90d build of one "
             "niche collide just as badly as two different niches. "
             "Datasets built before this change keep loading under their "
             "old names and are renamed once at startup.")
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
    L.append("Edit `docs.py`, not the generated files. They went stale three "
             "times when hand-maintained — the workflow prompt was still "
             "telling a chat model the menu ended at option 8 after a ninth "
             "was added.")
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
