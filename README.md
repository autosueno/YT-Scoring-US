# YouTube Market Intelligence Engine

Evaluates a YouTube topic **as a market**, not as a keyword.

Every topic is assessed on three measured pillars:

```
• Inferred Demand   how much the audience wants it
• Content Supply    how much content already exists / how crowded it is
• Momentum          whether demand is growing faster than supply
```

**Opportunity is derived from the balance between them** — it is never
measured directly:

```
opportunity = 50 + (demand − supply) / 2, adjusted by momentum
```

High demand + low supply is an underserved market. High demand + high supply
is a competitive one. The engine says which, and why.

Since YouTube exposes no search-volume data, demand is *inferred* from
observable audience behaviour (consumption rate, engagement, longevity), and
every score is **niche-relative** — measured against a statistical baseline
built from the niche itself, so "good" in a slow documentary niche and "good"
in gaming mean different things.

---

## Two modes

The engine answers two different questions, and it matters which you want.

```
TOPIC VALIDATION  (grader.py)      OPPORTUNITY DISCOVERY  (discover.py)
you already have an idea           you don't know what to make yet
        │                                  │
   one known topic                  the whole niche dataset
        │                                  │
   grade it against            find breakout videos, cluster them
   the niche baseline            into recurring topics, rank, export
        │                                  │
   score + verdict              cluster report for AI-assisted analysis
```

`grader.py` judges a topic you name. `discover.py` tells you which topics are
worth naming in the first place.

---

## Architecture

Thirteen modules in a clean dependency chain — no circular imports.

```
config.py        constants only (no internal deps)
paths.py         on-disk layout (depends on: config)
metrics.py       per-video features (depends on: —)
statistics.py    distribution stats (depends on: —)
insights.py      niche summaries: supply / momentum / competition / opportunity
quota_tracker.py API budget accounting (depends on: config)
api_keys.py      multi-key rotation + quota-exhaustion signaling (config)
collector.py     collect / save / load / archive (config, paths, metrics,
                 statistics, insights, quota_tracker, api_keys)
baseline.py      percentile lookups + niche market summaries (config, paths)
grader.py        market scoring: demand/supply/momentum → opportunity
discover.py      breakout ranking + full-list export (config, paths, baseline)
report.py        output formatting (depends on: config)
main.py          orchestration + CLI (depends on: all of the above)
```

**Collection** is multi-seed and quota-resilient: a niche is built from
several related seed queries (`finance, investing, economics, banking,
stocks`), each searched across date windows, merged and de-duplicated. Up to
three API keys rotate automatically on `quotaExceeded`, and if all keys are
exhausted mid-build the collector keeps everything gathered so far and
continues the pipeline with a partial dataset rather than crashing.

**Discovery** (`discover.py`) ranks **every collected video in the niche
dataset** by a deterministic Breakout Score, then clusters those videos into
recurring topics and ranks the clusters. Exports to `exports/` as JSON, CSV
and Markdown; the Markdown opens with a ready-made analysis prompt so it can
be pasted straight into Claude/ChatGPT. No LLM is ever called by the engine
itself — *math discovers, AI explains.*

Data flows one direction:

```
YouTube API
    │
    ▼
collector.py ──calls──► metrics.py       (raw rows → ~40 feature columns)
    │            ├─────► statistics.py    (df → statistics.csv)
    │            └─────► insights.py      (df → insights.json)
    │
    ▼
baseline.py    (reads statistics.csv → percentile lookups)
    │
    ▼
grader.py      (topic DataFrame + baseline → score + letter grade)
    │
    ▼
report.py      (result dict → terminal / JSON / CSV / HTML)

main.py orchestrates the whole chain and owns the "rebuild vs. reuse" decision.
```

collector.py is a pure pipeline: it fetches IDs, downloads videos and
channels, merges, calls `metrics.calculate_all()`, then saves videos and
metadata and delegates statistics and insights to their own modules,
archives the previous build, and logs quota. No analytics logic lives in it.

---

## Modules

### `config.py`
Central constants only — directory paths, YouTube API sample sizes, quota
costs and limits, scoring weights (`NICHE_WEIGHTS`, `TOPIC_WEIGHTS`),
letter-grade thresholds, and version strings. Everything imports from here;
it imports from nothing.

### `paths.py`
Single source of truth for the on-disk layout. Owns `safe_name()` (the
display-name → folder-name rule) and every niche/topic/history path helper.
Pure path construction — never touches disk. Both the writer (collector) and
the readers (baseline, report) call it, so files are always written to and
read from the same place.

### `metrics.py`
Per-video feature engineering. Takes the raw DataFrame and adds ~40 derived
columns: demand signals (views/likes/comments per hour, engagement rate),
creator signals (views per subscriber, small/medium/large flags), outlier and
breakout candidates, and title/duration/upload-time features. Only computes
features — no API calls, no grading, no distribution-level stats.

### `statistics.py`
Distribution-level statistics *across* videos: per-metric mean, median, std,
MAD, IQR, CV, skew, and the P1–P99 percentile columns. Exposes
`generate_statistics(df)` (pure) and `save_statistics(df, path)` (writes
`statistics.csv`). Separate from metrics.py (per-video) and baseline.py (which
reads these stats back for lookups).

### `insights.py`
Locally-derived insights from already-downloaded fields — zero extra API
calls. Owns the niche characterizations that make the market model work:
`competition_summary` (HHI, top-10 share, unique-channel ratio, creator mix),
`supply_summary` (uploads/day·week·month, upload acceleration, consistency,
content freshness, uploads-per-creator), `momentum_summary` (demand vs supply
growth, evergreen score, decay rate, trend direction, recent breakout
frequency), and `opportunity_indicators` (views/likes/comments per upload,
views per creator, small-creator success rate) — plus the title/tag/upload-time
summaries. Title text only; no thumbnail data is collected.

### `quota_tracker.py`
Tracks YouTube Data API v3 quota usage within a single run. The
`QuotaTracker` class exposes `.charge(operation, count)` and `.summary()`.
Warns as usage nears the daily limit but never hard-stops — better to finish a
build than leave a half-written dataset.

### `collector.py`
A pure data pipeline: searches the YouTube API, downloads video and channel
details, calls `metrics.calculate_all()`, then saves videos and metadata and
delegates statistics and insights to `statistics.py` and `insights.py`, and
auto-archives a previous build before overwriting. Contains no analytics logic
of its own, and never decides *whether* to rebuild — that belongs to
`main.py`.

### `baseline.py`
Loads a niche's saved `statistics.csv` (percentile lookups: `percentile_of`,
`percentile_value`, mean/median) **and** its `insights.json` market summaries
(`competition()`, `supply()`, `momentum()`, `opportunity_indicators()`,
`content_profile()`). This is what makes every pillar niche-relative. Reads
only; no API calls.

### `grader.py`
Market-intelligence scoring. Evaluates a topic as a **market**: it measures
three pillars against the niche baseline — **Inferred Demand** (consumption,
engagement, evergreen longevity), **Content Supply** (freshness, upload
acceleration, uploads-per-creator, incumbent size), and **Momentum** (is
demand growing faster than supply?) — each with its own score and letter
grade. **Opportunity is *derived*, not measured in parallel**:
`opportunity = 50 + (demand − supply)/2`, nudged by momentum, and tagged with
a 2×2 verdict (high/low demand × high/low supply → excellent / competitive /
limited / poor). Opportunity is the headline score. Content signals are
reported as diagnostics, not scored. Returns a structured result dict for
`report.py`.

### `discover.py`
Opportunity discovery. Ranks every collected video in the niche dataset by a
deterministic **Breakout Score** (a blend of niche percentiles for
`outlier_ratio`, `breakout_velocity` and `views_per_hour` — so it captures
both channel-relative overperformance *and* absolute market demand). It then
**clusters** videos into recurring topics by shared title phrases and ranks
clusters by evidence-weighted breakout strength:

```
cluster_score = avg_breakout × (1 + w × log2(unique_channels))
```

Seven videos scoring ~92 across five independent channels is a stronger
market signal than one video scoring 98 — repeated independent success is
what a real opportunity looks like. Exports JSON, CSV and Markdown (with an
embedded analysis prompt). **No LLM calls are made by the engine.**

### `report.py`
Output formatting and export. Prints terminal summaries and writes JSON, CSV,
and standalone HTML reports. Consumes grader's result dict and the video
DataFrame; decides nothing.

### `main.py`
The orchestrator and interactive CLI. The only module that decides *what* and
*when*: the freshness policy (rebuild vs. reuse a niche database), wiring
collector → baseline → grader → report, and handling user interaction.

---

## Storage layout

```
datasets/<niche>/
    videos.csv        every raw field + every metrics.py column
    statistics.csv    one row per metric: mean/median/std/MAD/IQR/CV/skew + P1–P99
    metadata.json     niche, query, counts, versions, quota, medians
    insights.json     top words/bigrams/trigrams, top channels, upload-time
                      distribution, top tags, title patterns (title text only)

topics/<topic>/
    videos.csv, metadata.json    written by collect_topic()
    report.json / .csv / .html   written by report.py after grading

history/<niche>/<build-date>/
    a full copy of datasets/<niche>/ before its most recent rebuild

cache/                cached artifacts (if used by your config)
quota_history.csv     append-only log: date, kind, query, videos, units_used, elapsed
```

---

## Usage

```bash
export YOUTUBE_API_KEY=your_key_here
python main.py
```

The CLI offers:

```
1  Build / refresh a niche database   (multi-seed, multi-key, resumable)
2  Grade a topic against a niche      (topic validation)
3  Discover opportunities             (breakout + cluster ranking, export)
4  Show an existing niche summary
5  Grade an existing niche            (no API calls)
6  Quit
```

---

## Roadmap

```
Current
  Math discovers, AI explains (manual)
  Deterministic breakout scoring + topic clustering
  Export to JSON / CSV / Markdown for manual LLM analysis

Planned
  Optional one-click AI analysis of the exported clusters
  Cross-run momentum (compare a build against its archived predecessor)
  Automatic recurring-topic tracking across refreshes
  Multi-niche comparison reports
```

The manual AI step is a deliberate design choice, not an unfinished feature:
it keeps the scoring deterministic and reproducible, costs nothing during
development, and lets prompts be refined freely before any automation.

Programmatic use:

```python
import main

# Ensure a fresh niche baseline, grade a topic, export all formats
main.grade_topic_against_niche(
    api_key,
    niche_name="Personal Finance",
    topic="day trading for beginners",
    export_formats=("json", "csv", "html"),
)
```

---

## Quota notes

YouTube Data API v3 gives 10,000 units/day free. Approximate costs:

- **Niche build** (default 2,000 videos): ~4,000–4,100 units per build
- **Topic collect** (default 500 videos): ~1,010–1,020 units per call

`search.list` costs 100 units per call; `videos.list` and `channels.list`
cost 1 each. Topic collection always pulls fresh data (a grade should reflect
*right now*), so repeat grades of the same topic cost the same each time.

---

## Requirements

```
google-api-python-client
pandas
isodate
numpy
```
