# YouTube Market Intelligence Engine

Evaluates a YouTube topic **as a market**, not as a keyword.

Guiding principle: **math discovers, AI explains**. Every number is deterministic and reproducible — the engine never calls an AI internally. It produces structured findings and prompt files; you take those to a chat model for the judgement calls.

## The model

Three measured pillars:

| pillar | measures |
|---|---|
| **Demand** | engagement velocity, consumption, engagement quality |
| **Supply** | content freshness, upload acceleration, uploads per creator |
| **Momentum** | recent window vs prior, signed -100..+100, 0 = breakeven |

**Opportunity is derived, not measured**: `50 + (demand - supply)/2`, nudged by momentum (max ±15). Opportunity exists where inferred demand exceeds content supply.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # add YOUTUBE_API_KEY_1..12
py main.py
```

## Menu

| | action | cost |
|---|---|---|
| **1** | Build / refresh a niche database | ~13,000 units |
| **2** | Grade a topic against a niche | ~1,000 units |
| **3** | Discover opportunities | free |
| **4** | Show an existing niche summary | free |
| **5** | Grade an existing niche | free |
| **6** | Thumbnail contact sheet creator | free |
| **7** | Search intent & Reddit signals | free |
| **8** | Tear down a single channel | free |
| **9** | What changed since the last build | free |
| **10** | Compare all niches side by side | free |
| **11** | Regenerate stats/insights/metadata from an edited videos.csv | free |
| **12** | Bulk-slice your whole niche library to one window | free |
| **13** | Compare two channels side by side | — |
| **14** | Quit | — |

## Output layout

```
datasets/<niche>/          collected data + baseline
exports/<niche>/
    market/                dashboard
    breakouts/             ranked videos + clusters
    packaging/             SEO analysis, thumbnails
    timing/                publishing calendars
    prompts/               files to paste into an AI
    signals/               autocomplete + Reddit
exports/_channels/<name>/  channel teardowns
exports/_compare/          niche-vs-niche verdicts
history/<niche>/<date>/    archived previous builds
```

Every file carries the name of what it describes — `ancient_history_365d_statistics.csv`, not `statistics.csv`. The folder tree already separates subjects, but the folder does not travel with the file: attach two niches' exports to one message and the second overwrites the first, and Excel refuses to open two workbooks of the same name at all. The window is included because a 365d and a 90d build of one niche collide just as badly as two different niches. Datasets built before this change keep loading under their old names and are renamed once at startup.

## Modules (29)

```
  angles              api_keys            baseline            calendars
  calibration         channel             collector           compare
  config              coverage            crossniche          dashboard
  discover            grader              insights            main
  metrics             paths               quota_state         quota_tracker
  report              seo                 shorts              signals
  statistics          thumbnail_sheet     timing              trends
  verdict
```

## Known limits

- **Compare two niches head to head** — Option 5 grades a niche against its own baseline, which forces demand to about 64 and supply to exactly 50. That's arithmetic, not information. Cross-niche grading exists in the code but isn't wired into the menu — for now, run option 3 on each and compare cluster strength by hand.
- **Tell you whether *your* video will work** — It never sees your channel. Every number describes the market, not your odds in it.
- **Judge whether a video is on-topic** — There's no topical relevance filter, deliberately. Occasional off-topic leaks are the price of not silently dropping things that only look irrelevant.
- **Measure current demand from one build** — A single snapshot cannot separate a video still climbing from one that stopped a year ago — views_per_hour is a lifetime average. Option 9 fixes this, but only once you have two builds of the same niche to compare.
- **Measure search demand in the scoring** — Scores are built from how existing videos performed. A topic nobody has covered looks identical to a topic nobody wants. Option 7 gets close from the outside, but it doesn't feed the scoring yet.
- **See why a video worked** — Click-through rate, average view duration, retention and traffic sources are private analytics. No public API exposes them at any price, for any channel. Everything here is inference from outside.

## Regenerating this file

`README.md`, `WORKFLOW_PROMPT.md` and `workflows.html` are all generated from the declarations in `docs.py`:

```bash
py main.py --docs
```

Edit `docs.py`, not the generated files. They went stale three times when hand-maintained — the workflow prompt was still telling a chat model the menu ended at option 8 after a ninth was added.
