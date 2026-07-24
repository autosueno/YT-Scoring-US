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
from paths import safe_name, export_dir_for
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
    Diverging red -> neutral -> green around 100 (the niche median).

    Saturation scales with distance from 100, capped at +/-60 so one wild
    cell cannot flatten the rest of the grid. Low-n cells are returned
    faded so uncertainty is visible rather than implied.
    """
    if index is None or n == 0:
        return "#1b1b1f", "#55555e", True
    delta = max(-60.0, min(60.0, index - 100.0)) / 60.0
    if delta >= 0:
        r, g, b = int(24 + 20 * (1 - delta)), int(70 + 130 * delta), int(50 + 30 * (1 - delta))
    else:
        d = -delta
        r, g, b = int(70 + 150 * d), int(40 + 20 * (1 - d)), int(45 + 25 * (1 - d))
    weak = n < CELL_MIN_SAMPLES
    return f"rgb({r},{g},{b})", "#e8e8ee" if not weak else "#9a9aa4", weak


def _cell_style(index, n):
    bg, fg, weak = _colour(index, n)
    style = f"background:{bg};color:{fg};"
    if weak:
        # Hatching + transparency reads as "not enough data" at a glance.
        style += ("background-image:repeating-linear-gradient(45deg,"
                  "rgba(255,255,255,.14) 0 3px,transparent 3px 6px);"
                  "opacity:.55;")
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
<style>
 body{background:#0f0f12;color:#e8e8ee;font-family:-apple-system,BlinkMacSystemFont,
      'Segoe UI',Roboto,sans-serif;margin:0;padding:32px 36px;line-height:1.55}
 h1{font-size:23px;margin:0 0 6px;font-weight:700;letter-spacing:-.2px}
 h2{font-size:16px;margin:32px 0 12px;font-weight:650;color:#d4d4de}
 .sub{color:#96969f;font-size:13.5px;margin-bottom:20px;max-width:760px}
 .headline{margin:20px 0 28px;padding:20px 24px;background:linear-gradient(135deg,#173322,#0f2a1c);
           border:1px solid #2a5c3c;border-radius:10px;max-width:760px}
 .headline .label{font-size:11.5px;text-transform:uppercase;letter-spacing:.6px;
                   color:#7fd99a;font-weight:650;margin-bottom:6px}
 .headline .slot{font-size:26px;font-weight:750;color:#f2fff5;letter-spacing:-.3px}
 .headline .stats{font-size:13px;color:#9fcfae;margin-top:6px}
 .headline .also{margin-top:14px;padding-top:14px;border-top:1px solid #2a5c3c22;
                  font-size:12.5px;color:#8fae98}
 .headline .also b{color:#c8e6d0}
 table{border-collapse:separate;border-spacing:3px}
 th{font-size:11px;color:#96969f;font-weight:650;padding:4px;text-align:center}
 th.day{text-align:right;padding-right:11px;min-width:80px;font-size:11.5px}
 td{width:34px;height:30px;text-align:center;font-size:11px;border-radius:5px;
    font-variant-numeric:tabular-nums;font-weight:600;cursor:default}
 td.dom{width:66px;height:56px;font-size:12px;vertical-align:top;padding:5px 6px;
        text-align:left;border-radius:7px}
 .dnum{font-size:14px;font-weight:750;display:block;margin-bottom:1px}
 .legend{margin-top:26px;font-size:12.5px;color:#96969f;display:flex;
          align-items:center;flex-wrap:wrap;gap:2px 4px}
 .sw{display:inline-block;width:16px;height:16px;border-radius:4px;
     vertical-align:-3px;margin:0 6px 0 16px}
 .note{margin-top:26px;padding:16px 19px;background:#17171c;border-left:3px solid #3a3a44;
       border-radius:7px;font-size:13px;color:#aeaeb8;max-width:760px}
 .note b{color:#dcdce4}
 .note div+div{margin-top:12px}
 code{background:#1e1e25;padding:2px 6px;border-radius:4px;font-size:12px}
 .rank-table{border-collapse:collapse;max-width:640px;margin-top:10px}
 .rank-table th{text-align:left;font-size:11.5px;padding:6px 14px 6px 0;color:#96969f;
                 border-bottom:1px solid #2a2a32}
 .rank-table td{padding:8px 14px 8px 0;font-size:13.5px;border-bottom:1px solid #1e1e25}
 .rank-table td.n{color:#8a8a94}
</style>
"""


def _legend():
    return f"""
<div class="legend">
  <span class="sw" style="background:rgb(24,200,50)"></span>strong
  <span class="sw" style="background:rgb(34,110,68)"></span>above average
  <span class="sw" style="background:rgb(44,60,70)"></span>average
  <span class="sw" style="background:rgb(160,48,58)"></span>below average
  <span class="sw" style="background:rgb(220,40,45)"></span>weak
  <span class="sw" style="background:#1b1b1f;background-image:repeating-linear-gradient(45deg,
        rgba(255,255,255,.14) 0 3px,transparent 3px 6px);opacity:.55"></span>
  fewer than {CELL_MIN_SAMPLES} videos — not enough data
</div>
<div class="sub" style="margin-top:6px">Colour is the SHRUNK index — a cell
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
               f"<div class='stats'>Index {top['index']} "
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


def export_week_calendar(df, niche_name, tiers=None) -> str:
    """
    7x24 heatmap. Rendered for the whole niche AND for each creator tier
    that has enough videos, so a new channel can see whether its own
    segment behaves differently from the niche average.
    """
    df = timing_mod.add_local_time(df.copy())
    tz = df["_tz_used"].iloc[0] if "_tz_used" in df.columns and len(df) else "UTC"
    joint = timing_mod.best_joint_slots(df, top_n=5)

    parts = [_CSS,
             f"<h1>{html.escape(niche_name)} — weekly publishing calendar</h1>",
             f"<div class='sub'>Times in <b>{tz}</b> (audience timezone). "
             f"{len(df):,} videos. Index 100 = niche median.</div>",
             _headline(joint),
             "<h2>Top 5 slots, ranked</h2>", _rank_table(joint),
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
        "<div class='note'><b>How to read this.</b> Colour comes from "
        "<code>outlier_ratio</code> — each video measured against its own "
        "channel's average — so green means videos published in that slot "
        "beat their own baseline, not that big channels post there.<br><br>"
        "<b>A pale striped cell is not a bad time.</b> It means too few "
        "videos landed there to judge. With 168 cells, thin coverage is "
        "normal; trust the broad bands, not individual squares.<br><br>"
        "<b>Biggest caveat:</b> this shows how already-published videos "
        "did. It cannot tell you what the same video would have done an "
        "hour later. And if you have few subscribers, publish time is one "
        "of the weakest levers you have — topic, title and thumbnail matter "
        "far more.</div>")

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "timing")
    path = os.path.join(out_dir, "calendar_week.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><head><meta charset='utf-8'>"
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
    parts = [_CSS,
             f"<h1>{html.escape(niche_name)} — monthly pattern calendar</h1>",
             f"<div class='sub'>Median performance by day of month across "
             f"{len(df):,} videos. Index 100 = niche median.</div>"]

    if not grid:
        parts.append("<p style='color:#8b8b96'>Not enough data.</p>")
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
                         f"<div class='sub'>{best}</div>"
                         f"<h2>Weakest days</h2><div class='sub'>{worst}</div>")

    if event_info and event_info.get("concentration_ratio"):
        parts.append("<h2>Event concentration</h2>")
        parts.append(
            f"<div class='sub'>Top videos are "
            f"<b>{event_info['concentration_ratio']}x</b> more concentrated "
            f"on the busiest dates than an even spread predicts — "
            f"<i>{html.escape(event_info['reading'])}</i>.</div>")
        if event_info.get("busiest_days"):
            items = "".join(f"<li>{html.escape(str(b['date']))} — "
                            f"{b['top_videos']} top videos</li>"
                            for b in event_info["busiest_days"])
            parts.append(f"<ul class='sub'>{items}</ul>")

    parts.append(_legend())
    parts.append(
        "<div class='note'><b>This is not an economic calendar.</b> The "
        "engine has no feed of CPI, PPI, FOMC or jobs-report dates. What "
        "you are seeing is how videos published on each calendar day "
        "actually performed, pooled across every month in the dataset. In "
        "finance those peaks often line up with scheduled releases — but "
        "confirm against a real calendar before planning around them."
        "<br><br><b>Two different strategies.</b> A high-index day usually "
        "means event-driven demand: publish same-day, expect a spike, "
        "expect fast decay. Evergreen content ignores the calendar and "
        "compounds instead. Both work; they are not the same game."
        "<br><br>Generated "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.</div>")

    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "timing")
    path = os.path.join(out_dir, "calendar_month.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{html.escape(niche_name)} — monthly calendar</title>"
                "</head><body>" + "\n".join(parts) + "</body></html>")
    return path


def export_calendars(df, niche_name, tiers=None, event_info=None) -> dict:
    """Write both calendars; returns {'week': path, 'month': path}."""
    return {
        "week": export_week_calendar(df, niche_name, tiers=tiers),
        "month": export_month_calendar(df, niche_name, event_info=event_info),
    }
