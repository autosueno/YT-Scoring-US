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

**BUILD — uses your API quota**

| | action | cost |
|---|---|---|
| **1** | Build a niche database | ~13,000 units |
| **2** | Grade a topic idea | ~1,000 units |
| **3** | Search intent & keyword signals | free |
| **4** | Tear down a channel | ~1,000 units |
| **5** | Compare two channels | ~1,000 units |

**EXPLORE — reads what you already have**

| | action | cost |
|---|---|---|
| **6** | Discover breakout opportunities | free |
| **7** | Niche snapshot | free |
| **8** | Score a niche against your library | free |
| **9** | Thumbnail contact sheet | free |
| **10** | What's moved since last build | free |
| **11** | Compare all niches side by side | free |

**MANAGE — maintain your data**

| | action | cost |
|---|---|---|
| **12** | Rebuild from cleaned data | free |
| **13** | Slice library to a shorter window | free |
| **14** | Quit | — |

## Output layout

```
datasets/<niche>/          collected data + baseline
exports/<niche>/
    market/                dashboard
    breakouts/             ranked videos + clusters + research/discovery MDs
    packaging/             SEO analysis, thumbnails
    timing/                publishing calendars
    prompts/               files to paste into an AI
    signals/               autocomplete + Reddit
exports/_channels/<name>/  channel teardowns + diffs
exports/_compare/          niche-vs-niche verdicts
history/<niche>/<date>/    archived previous builds
```

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

- **Tell you whether *your* video will work** — It never sees your channel. Every number describes the market, not your odds in it.
- **Judge whether a video is on-topic** — There's no topical relevance filter, deliberately. Occasional off-topic leaks are the price of not silently dropping things that only look irrelevant. Run the cleaning workflow on any niche before treating its numbers as calibration data.
- **Measure current demand from one build** — A single snapshot cannot separate a video still climbing from one that stopped a year ago — views_per_hour is a lifetime average. Option 10 fixes this, but only once you have two builds of the same niche to compare.
- **Measure search demand in the scoring** — Scores are built from how existing videos performed. A topic nobody has covered looks identical to a topic nobody wants. Option 3 gets close from the outside, but it doesn't feed the scoring yet.
- **See why a video worked** — Click-through rate, average view duration, retention and traffic sources are private analytics. No public API exposes them at any price, for any channel. Everything here is inference from outside.
- **Validate the Reliability score** — Reliability measures outcome concentration — how narrow the gap between a decent and a great video is. That's a reasonable proxy for predictability, but it hasn't been checked against real channel outcomes. Treat it as a structured heuristic until you have your own data to compare against.

## Regenerating this file

`README.md`, `WORKFLOW_PROMPT.md` and `workflows.html` are all generated from the declarations in `docs.py`:

```bash
py main.py --docs
```

Edit `docs.py`, not the generated files.
