"""
calendars.py

Two visual publishing calendars, exported as standalone HTML.

1. WEEKLY HEATMAP  — 7 days x 24 hours, green = good slot, red = poor.
2. MONTHLY PATTERN — day-of-month performance, which in finance tends to
   surface the scheduled-release rhythm (CPI, PPI, FOMC, jobs).

Both are coloured by OUTLIER INDEX (each video against its own channel's
average, indexed so 100 = niche median). That controls for channel size, so
a green cell means "videos published here beat their own baseline", not
"big channels publish here".

THE SAMPLE-SIZE PROBLEM, HANDLED HONESTLY
-----------------------------------------
7 x 24 = 168 cells. A 2,000-video niche gives ~12 videos per cell, which is
thin, and a single tier (e.g. <100 subs, ~190 videos) gives barely one.
Rather than hide that, every cell carries its count and cells below the
confidence threshold are drawn faded and hatched. A pale, striped cell means
"we do not really know", which is different information from "this is a bad
time" — and the legend says so.

No dependencies; plain HTML + inline CSS, opens in any browser.
"""

import html
import os
from datetime import datetime, timezone

import pandas as pd

from config import EXPORTS_DIR, TIMING_MIN_SAMPLES, AUDIENCE_TIMEZONE
from paths import export_dir_for
from paths import named as _named
import timing as timing_mod

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]

# A cell needs at least this many videos to be drawn at full confidence.
# Lower than TIMING_MIN_SAMPLES because 168 cells is inherently thin; the
# faded styling carries the uncertainty rather than a hard cutoff.
CELL_MIN_SAMPLES = max(4, TIMING_MIN_SAMPLES // 4)


# ============================================================
# COLOUR
# ============================================================

def _colour(index, n):
    """
    Diverging coral -> glass-neutral -> emerald around 100 (niche median).

    Saturation scales with distance from 100, capped at +/-60 so one wild
    cell cannot flatten the rest of the grid. Low-n cells are returned
    faded so uncertainty is visible rather than implied.
    """
    if index is None or n == 0:
        return "rgb(40,49,68)", "#5c6a83", True
    delta = max(-60.0, min(60.0, index - 100.0)) / 60.0
    # Endpoints tuned for the dark-glass palette: neutral sits close to the
    # card surface tone so an "average" cell recedes rather than reading
    # as a third hard color; strong ends lean toward true emerald/coral
    # rather than the desaturated red/green a linear RGB blend would give.
    neutral = (34, 42, 58)
    if delta >= 0:
        end = (33, 178, 122)   # emerald
    else:
        end = (214, 66, 84)    # coral
        delta = -delta
    r = int(neutral[0] + (end[0] - neutral[0]) * delta)
    g = int(neutral[1] + (end[1] - neutral[1]) * delta)
    b = int(neutral[2] + (end[2] - neutral[2]) * delta)
    weak = n < CELL_MIN_SAMPLES
    fg = "#EDF1F7" if not weak else "#6B7A93"
    return f"rgb({r},{g},{b})", fg, weak


def _cell_style(index, n):
    bg, fg, weak = _colour(index, n)
    style = f"background:{bg};color:{fg};"
    if weak:
        # Hatching + transparency reads as "not enough data" at a glance.
        style += ("background-image:repeating-linear-gradient(45deg,"
                  "rgba(255,255,255,.16) 0 3px,transparent 3px 6px);"
                  "opacity:.75;")
    return style


# ============================================================
# GRID DATA
# ============================================================

def _hour_day_grid(df):
    """(weekday, hour) -> {index, raw, n}, colored by the SHRUNK index."""
    metric = "outlier_ratio" if "outlier_ratio" in df.columns else "views_per_hour"
    if metric not in df.columns or df.empty:
        return {}, metric
    base = df[metric].median()
    if not base or pd.isna(base):
        return {}, metric
    grid = {}
    for (day, hour), sub in df.groupby(["local_weekday", "local_hour"]):
        m = sub[metric].median()
        raw = round(100 * float(m) / float(base), 1) if pd.notna(m) else None
        n = int(len(sub))
        grid[(day, int(hour))] = {
            "index": timing_mod._shrink(raw, n),   # colour + headline use this
            "raw": raw,
            "n": n,
        }
    return grid, metric


def _day_of_month_grid(df):
    """
    Day-of-month -> {index, raw, n}.

    COLOURED BY THE SHRUNK INDEX. Raw index on a day-of-month grid is
    especially treacherous: 31 buckets over a 12-month window average
    ~8 videos each, so a single viral fluke can paint a day neon green.
    Measured case: day 26 showed index 253 from 2 videos and was rendered
    as the strongest colour on the grid, while day 22's rock-solid 222-video
    sample at index 101 looked identical to "nothing happening". Shrinking
    toward 100 by sample size fixes the colour; `raw` is kept for the
    tooltip so the underlying number is never hidden, only not mistaken for
    certainty.
    """
    metric = "outlier_ratio" if "outlier_ratio" in df.columns else "views_per_hour"
    if metric not in df.columns or df.empty:
        return {}
    published = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    try:
        local = published.dt.tz_convert(AUDIENCE_TIMEZONE)
    except Exception:
        local = published
    d = df.copy()
    d["_dom"] = local.dt.day
    base = d[metric].median()
    if not base or pd.isna(base):
        return {}
    out = {}
    for dom, sub in d.groupby("_dom"):
        if pd.isna(dom):
            continue
        m = sub[metric].median()
        n = int(len(sub))
        raw = round(100 * float(m) / float(base), 1) if pd.notna(m) else None
        out[int(dom)] = {"index": timing_mod._shrink(raw, n), "raw": raw, "n": n}
    return out


# ============================================================
# HTML
# ============================================================

_CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;
700;800&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
 :root{
   --bg-1:#0A1120; --bg-2:#101B30; --glow:rgba(76,141,255,.16);
   --card:rgba(255,255,255,.045); --card-hi:rgba(255,255,255,.07);
   --line:rgba(255,255,255,.09); --line-soft:rgba(255,255,255,.055);
   --ink:#EDF1F7; --ink-2:#9AA7BC; --ink-3:#67748C;
   --accent:#4C8DFF; --accent-soft:rgba(76,141,255,.14);
   --good:#2ECF96; --good-soft:rgba(46,207,150,.14);
   --bad:#F0576B; --bad-soft:rgba(240,87,107,.14);
   --font:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
   --mono:'IBM Plex Mono',monospace;
 }
 *{box-sizing:border-box}
 body{margin:0;padding:0;color:var(--ink);font-family:var(--font);
      line-height:1.55;
      background:
        radial-gradient(900px 500px at 88% 8%,var(--glow),transparent 60%),
        radial-gradient(700px 420px at 6% 96%,rgba(46,207,150,.08),transparent 60%),
        linear-gradient(160deg,var(--bg-1),var(--bg-2));
      background-attachment:fixed;min-height:100vh}
 .page{max-width:1180px;margin:0 auto;padding:clamp(20px,4vw,48px)}
 .topbar{display:flex;align-items:flex-start;justify-content:space-between;
         gap:20px;flex-wrap:wrap;margin:0 0 26px}
 h1{font-size:24px;margin:0 0 6px;font-weight:800;letter-spacing:-.02em}
 .sub{color:var(--ink-2);font-size:13.5px;margin:0;max-width:640px}
 .stats{display:flex;gap:8px;flex-wrap:wrap}
 .stat-pill{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);
            background:var(--card);border:1px solid var(--line);
            border-radius:20px;padding:7px 14px;white-space:nowrap}
 .stat-pill b{color:var(--ink);font-weight:600}
 h2{font-size:15px;margin:34px 0 13px;font-weight:700;color:var(--ink);
    letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
 h2::before{content:"";width:3px;height:15px;border-radius:2px;
            background:var(--accent)}
 .card{background:var(--card);border:1px solid var(--line);
       border-radius:18px;padding:clamp(16px,2.4vw,22px);margin:0 0 4px}
 .headline{margin:18px 0 30px;padding:22px 26px;position:relative;
           background:var(--card-hi);border:1px solid var(--line);
           border-radius:18px;max-width:760px;overflow:hidden}
 .headline::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;
                   background:var(--good)}
 .headline .label{font-size:11px;text-transform:uppercase;letter-spacing:.1em;
                  color:var(--good);font-weight:700;margin-bottom:8px}
 .headline .slot{font-size:27px;font-weight:800;color:var(--ink);
                 letter-spacing:-.02em}
 .headline .stats-line{font-size:13px;color:var(--ink-2);margin-top:7px;
                       font-family:var(--mono)}
 .headline .also{margin-top:15px;padding-top:15px;
                 border-top:1px solid var(--line-soft);
                 font-size:12.5px;color:var(--ink-2)}
 .headline .also b{color:var(--ink);font-weight:600}
 table{border-collapse:separate;border-spacing:4px}
 th{font-size:10.5px;color:var(--ink-3);font-weight:700;padding:3px;
    text-align:center;text-transform:uppercase;letter-spacing:.04em}
 th.day{text-align:right;padding-right:12px;min-width:82px;font-size:12px;
        color:var(--ink-2);text-transform:none;letter-spacing:0}
 td{width:34px;height:30px;text-align:center;font-size:11px;border-radius:8px;
    font-variant-numeric:tabular-nums;font-weight:700;cursor:default;
    font-family:var(--mono);box-shadow:inset 0 0 0 1px rgba(255,255,255,.05)}
 td.dom{width:76px;height:64px;font-size:12px;vertical-align:top;
        padding:8px 9px;text-align:left;border-radius:12px}
 .dnum{font-size:15px;font-weight:800;display:block;margin-bottom:3px;
       font-family:var(--font)}
 .legend{margin-top:28px;padding:16px 20px;background:var(--card);
         border:1px solid var(--line);border-radius:14px;font-size:12.5px;
         color:var(--ink-2);display:flex;align-items:center;flex-wrap:wrap;
         gap:6px 5px}
 .sw{display:inline-block;width:14px;height:14px;border-radius:5px;
     vertical-align:-2px;margin:0 6px 0 16px;
     box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)}
 .sw:first-of-type{margin-left:0}
 .note{margin-top:22px;padding:18px 22px;background:var(--card);
       border:1px solid var(--line);border-left:3px solid var(--accent);
       border-radius:14px;font-size:13px;color:var(--ink-2);max-width:760px}
 .note b{color:var(--ink);font-weight:600}
 .note div+div{margin-top:12px}
 code{background:rgba(255,255,255,.08);padding:2px 7px;border-radius:5px;
      font-size:12px;font-family:var(--mono)}
 .rank-table{border-collapse:collapse;max-width:640px;margin-top:6px}
 .rank-table th{text-align:left;font-size:11px;padding:9px 16px 9px 0;
                text-transform:none;letter-spacing:0}
 .rank-table td{padding:11px 16px 11px 0;font-size:13.5px;
                border-bottom:1px solid var(--line-soft);font-weight:500;
                white-space:nowrap}
 .rank-table tr:last-child td{border-bottom:none}
 .rank-table td.n{color:var(--ink-3);font-family:var(--mono);font-size:12.5px}
 footer.gen{margin-top:34px;font-family:var(--mono);font-size:10.5px;
            color:var(--ink-3)}
</style>
"""


def _legend():
    return f"""
<div class="legend">
  <span class="sw" style="background:rgb(33,178,122)"></span>strong
  <span class="sw" style="background:rgb(34,113,94)"></span>above average
  <span class="sw" style="background:rgb(34,42,58)"></span>average
  <span class="sw" style="background:rgb(140,58,66)"></span>below average
  <span class="sw" style="background:rgb(214,66,84)"></span>weak
  <span class="sw" style="background:rgb(40,49,68);background-image:repeating-linear-gradient(45deg,
        rgba(255,255,255,.16) 0 3px,transparent 3px 6px);opacity:.75"></span>
  fewer than {CELL_MIN_SAMPLES} videos — not enough data
</div>
<div class="sub" style="margin-top:10px">Colour is the SHRUNK index — a cell
with few videos is pulled toward neutral (100) rather than shown at its raw
extreme, so a 2-video fluke can't paint the strongest colour on the grid.
Hover any cell to see both the shrunk and raw numbers.</div>"""


def _headline(joint, tier_label=""):
    """The direct answer, rendered as a prominent callout — this is the
    fix for 'is it Sunday 3am or Thursday 5pm': one joint cell, not two
    marginal bests that may never co-occur."""
    if not joint:
        return "<div class='sub'>Not enough joint weekday×hour data for a single recommendation.</div>"
    top = joint[0]
    ci = (f" &nbsp;·&nbsp; 90% CI {top['ci_low']:.0f}–{top['ci_high']:.0f}"
          if top.get("ci_low") is not None else "")
    prefix = f"Best time to publish{f' for {tier_label}' if tier_label else ''}"
    html_out = [f"<div class='headline'><div class='label'>{html.escape(prefix)}</div>",
               f"<div class='slot'>{top['weekday']} "
               f"{timing_mod._fmt_block(top['hour_block'])}</div>",
               f"<div class='stats-line'>Index {top['index']} "
               f"({top['videos']} videos){ci}</div>"]
    if len(joint) > 1:
        others = " · ".join(f"<b>{s['weekday']} {timing_mod._fmt_block(s['hour_block'])}</b>"
                            f" ({s['index']})" for s in joint[1:4])
        html_out.append(f"<div class='also'>Also strong: {others}</div>")
    html_out.append("</div>")
    return "\n".join(html_out)


def _rank_table(joint):
    if not joint:
        return ""
    rows = ["<table class='rank-table'><tr><th>Slot</th><th>Index</th>"
            "<th>90% CI</th><th>Videos</th></tr>"]
    for s in joint:
        ci = (f"{s['ci_low']:.0f}–{s['ci_high']:.0f}" if s.get("ci_low") is not None else "—")
        rows.append(f"<tr><td>{s['weekday']} {timing_mod._fmt_block(s['hour_block'])}</td>"
                   f"<td>{s['index']}</td><td class='n'>{ci}</td>"
                   f"<td class='n'>{s['videos']}</td></tr>")
    rows.append("</table>")
    return "\n".join(rows)


def _week_table(df, label):
    grid, metric = _hour_day_grid(df)
    if not grid:
        return f"<p style='color:#8b8b96'>No data for {html.escape(label)}.</p>"
    rows = ["<table>", "<tr><th class='day'></th>" +
            "".join(f"<th>{h:02d}</th>" for h in range(24)) + "</tr>"]
    for day in WEEKDAYS:
        cells = [f"<th class='day'>{day[:3]}</th>"]
        for h in range(24):
            c = grid.get((day, h), {"index": None, "raw": None, "n": 0})
            idx, raw, n = c["index"], c["raw"], c["n"]
            txt = "" if idx is None else f"{idx:.0f}"
            tip = (f"{day} {h:02d}:00 — {n} videos"
                  + (f", index {idx:.0f} (raw {raw:.0f})" if idx is not None else ""))
            cells.append(f"<td style=\"{_cell_style(idx, n)}\" "
                         f"title=\"{html.escape(tip)}\">{txt}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def export_week_calendar(df, niche_name, tiers=None, out_dir=None) -> str:
    """
    7x24 heatmap. Rendered for the whole niche AND for each creator tier
    that has enough videos, so a new channel can see whether its own
    segment behaves differently from the niche average.
    """
    df = timing_mod.add_local_time(df.copy())
    tz = df["_tz_used"].iloc[0] if "_tz_used" in df.columns and len(df) else "UTC"
    joint = timing_mod.best_joint_slots(df, top_n=5)

    parts = [_CSS, "<div class='page'>",
             "<div class='topbar'><div>",
             f"<h1>{html.escape(niche_name)}</h1>",
             f"<p class='sub'>Weekly publishing calendar — when videos in "
             f"this niche actually beat their own baseline.</p></div>",
             "<div class='stats'>"
             f"<span class='stat-pill'>tz <b>{html.escape(str(tz))}</b></span>"
             f"<span class='stat-pill'><b>{len(df):,}</b> videos</span>"
             f"<span class='stat-pill'>index <b>100</b> = median</span>"
             "</div></div>",
             _headline(joint),
             "<h2>Top 5 slots, ranked</h2>",
             f"<div class='card'>{_rank_table(joint)}</div>",
             "<h2>Full grid — all creators</h2>", _week_table(df, "all creators")]

    if tiers and "channel_subs" in df.columns:
        subs = pd.to_numeric(df["channel_subs"], errors="coerce")
        for lo, hi, label in tiers:
            mask = (subs >= lo) & (subs < hi) if hi is not None else (subs >= lo)
            sub = df[mask.fillna(False)]
            if len(sub) < 60:      # below this, 168 cells is meaningless
                continue
            pretty = label.replace("_", " ").title()
            rng = f"&lt;{hi:,}" if lo == 0 and hi else (
                f"{lo:,}+" if hi is None else f"{lo:,}–{hi:,}")
            tier_joint = timing_mod.best_joint_slots(
                sub, top_n=3, min_n=max(6, min(15, len(sub) // 40)))
            parts.append(f"<h2>{pretty} ({rng} subs) — {len(sub):,} videos</h2>")
            parts.append(_headline(tier_joint, tier_label=pretty.lower()))
            parts.append(_week_table(sub, label))

    parts.append(_legend())
    parts.append(
        "<div class='note'><div><b>How to read this.</b> Colour comes from "
        "<code>outlier_ratio</code> — each video measured against its own "
        "channel's average — so green means videos published in that slot "
        "beat their own baseline, not that big channels post there.</div>"
        "<div><b>A pale striped cell is not a bad time.</b> It means too few "
        "videos landed there to judge. With 168 cells, thin coverage is "
        "normal; trust the broad bands, not individual squares.</div>"
        "<div><b>Biggest caveat:</b> this shows how already-published videos "
        "did. It cannot tell you what the same video would have done an "
        "hour later. And if you have few subscribers, publish time is one "
        "of the weakest levers you have — topic, title and thumbnail matter "
        "far more.</div></div>")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"<footer class='gen'>Generated {ts}</footer>")
    parts.append("</div>")

    out_dir = out_dir or export_dir_for(EXPORTS_DIR, niche_name, "timing")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, _named(niche_name, "calendar_week.html"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>{html.escape(niche_name)} — weekly calendar</title>"
                "</head><body>" + "\n".join(parts) + "</body></html>")
    return path


def export_month_calendar(df, niche_name, event_info=None) -> str:
    """
    Day-of-month performance calendar.

    WHAT THIS IS: how videos published on each calendar day have historically
    performed in this niche, aggregated across every month in the dataset.

    WHY IT IS USEFUL FOR FINANCE: scheduled releases land on a monthly
    rhythm — CPI mid-month, jobs on the first Friday, FOMC eight times a
    year — so recurring demand spikes show up as high-index days.

    WHAT IT IS NOT: an economic calendar. The engine has no feed of release
    dates; this is inferred purely from observed performance. Cross-check
    the highlighted days against a real calendar before planning around them.
    """
    grid = _day_of_month_grid(df)
    parts = [_CSS, "<div class='page'>",
             "<div class='topbar'><div>",
             f"<h1>{html.escape(niche_name)}</h1>",
             "<p class='sub'>Monthly pattern calendar — day-of-month "
             "performance, pooled across every month in the dataset.</p>"
             "</div>",
             "<div class='stats'>"
             f"<span class='stat-pill'><b>{len(df):,}</b> videos</span>"
             f"<span class='stat-pill'>index <b>100</b> = median</span>"
             "</div></div>"]

    if not grid:
        parts.append("<p class='sub'>Not enough data.</p>")
    else:
        rows = ["<table>"]
        for week_start in range(1, 32, 7):
            cells = []
            for d in range(week_start, min(week_start + 7, 32)):
                c = grid.get(d, {"index": None, "raw": None, "n": 0})
                idx, raw, n = c["index"], c["raw"], c["n"]
                val = "" if idx is None else f"{idx:.0f}"
                tip = (f"Day {d} — {n} videos"
                      + (f", index {idx:.0f} (raw {raw:.0f})" if idx is not None else ""))
                cells.append(
                    f"<td class='dom' style=\"{_cell_style(idx, n)}\" "
                    f"title=\"{html.escape(tip)}\">"
                    f"<span class='dnum'>{d}</span>{val}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        rows.append("</table>")
        parts.append("\n".join(rows))

        ranked = sorted((d for d in grid if grid[d]["index"] is not None
                         and grid[d]["n"] >= CELL_MIN_SAMPLES),
                        key=lambda d: grid[d]["index"], reverse=True)
        if ranked:
            best = ", ".join(f"<b>{d}</b> ({grid[d]['index']:.0f})" for d in ranked[:5])
            worst = ", ".join(f"{d} ({grid[d]['index']:.0f})" for d in ranked[-5:])
            parts.append(f"<h2>Strongest days of the month</h2>"
                         f"<div class='card sub'>{best}</div>"
                         f"<h2>Weakest days</h2>"
                         f"<div class='card sub'>{worst}</div>")

    if event_info and event_info.get("concentration_ratio"):
        parts.append("<h2>Event concentration</h2>")
        parts.append(
            f"<div class='card sub'>Top videos are "
            f"<b style='color:var(--ink)'>{event_info['concentration_ratio']}x</b> "
            f"more concentrated on the busiest dates than an even spread "
            f"predicts — <i>{html.escape(event_info['reading'])}</i>.</div>")
        if event_info.get("busiest_days"):
            items = "".join(f"<li>{html.escape(str(b['date']))} — "
                            f"{b['top_videos']} top videos</li>"
                            for b in event_info["busiest_days"])
            parts.append(f"<ul class='card sub'>{items}</ul>")

    parts.append(_legend())
    parts.append(
        "<div class='note'><div><b>This is not an economic calendar.</b> The "
        "engine has no feed of CPI, PPI, FOMC or jobs-report dates. What "
        "you are seeing is how videos published on each calendar day "
        "actually performed, pooled across every month in the dataset. In "
        "finance those peaks often line up with scheduled releases — but "
        "confirm against a real calendar before planning around them.</div>"
        "<div><b>Two different strategies.</b> A high-index day usually "
        "means event-driven demand: publish same-day, expect a spike, "
        "expect fast decay. Evergreen content ignores the calendar and "
        "compounds instead. Both work; they are not the same game.</div>"
        "</div>")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"<footer class='gen'>Generated {ts}</footer>")
    parts.append("</div>")

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "timing")
    path = os.path.join(out_dir, _named(niche_name, "calendar_month.html"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>{html.escape(niche_name)} — monthly calendar</title>"
                "</head><body>" + "\n".join(parts) + "</body></html>")
    return path


def export_calendars(df, niche_name, tiers=None, event_info=None) -> dict:
    """Write both calendars; returns {'week': path, 'month': path}."""
    return {
        "week": export_week_calendar(df, niche_name, tiers=tiers),
        "month": export_month_calendar(df, niche_name, event_info=event_info),
    }
