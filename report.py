"""
report.py

Formats and exports grading results and niche insights.

Responsibilities:
  - Print a human-readable summary to the terminal.
  - Serialize a grade result to JSON.
  - Export a video DataFrame to CSV.
  - Generate a standalone HTML report.

Does NOT:
  - Call the YouTube API.
  - Compute metrics or scores (receives already-computed dicts/DataFrames).
  - Decide what to export — that's main.py's job.

Public functions:
    print_grade(result)             → terminal output
    print_niche_summary(meta, df)   → terminal output
    save_json(result, path)         → write result dict as JSON
    save_csv(df, path)              → write DataFrame to CSV
    save_html(result, df, path)     → write standalone HTML report
    print_quota(tracker)            → terminal quota summary
"""

import json
import os
from datetime import datetime, timezone

import pandas as pd

from config import SUMMARY_COLUMNS, TERMINAL_MAX_ROWS


# ============================================================
# TERMINAL OUTPUT
# ============================================================

def print_grade(result: dict):
    """Print a market-intelligence result dict (from grader.py) to terminal."""
    kind  = result.get("kind", "topic")
    name  = result.get("topic") or result.get("niche", "Unknown")
    score = result.get("score", 0)
    grade = result.get("grade", "?")
    n     = result.get("sample_size", 0)

    _header(f"{kind.upper()} MARKET ASSESSMENT — {name}")
    print(f"  Sample      : {n:,} videos")
    niche = result.get("niche")
    if niche:
        print(f"  Niche       : {niche}")

    # Demand / Supply / Momentum pillars, each with its own grade
    pillars = result.get("pillars", {})
    if pillars:
        print()
        print("  Market pillars:")
        for pname, p in pillars.items():
            if p.get("abstained") or p.get("score") is None:
                reason = p.get("reason", "insufficient data")
                print(f"    {pname:<10}   —   [not scored]  ({reason})")
                continue
            bar = _score_bar(p["score"])
            print(f"    {pname:<10} {p['score']:5.1f}/100  [{p['grade']}]  {bar}")
            # Signal groups first (graded sub-scores), then raw metrics.
            for gname, g in p.get("groups", {}).items():
                print(f"        {gname:<24} {g['score']:5.1f}  [{g['grade']}]"
                      f"  (weight {g['weight']:.0%})")
            for mname, mscore in p.get("metrics", {}).items():
                print(f"          · {mname:<24} {mscore:5.1f}")

    # Opportunity LAST — it is derived from the pillars above, so the
    # report reads in computation order: measured inputs, then the result.
    opp = result.get("opportunity", {})
    if opp:
        print()
        print("  " + "-" * 56)
        print("  Opportunity is DERIVED from the pillars above:")
        mom = opp.get("momentum_score")
        mom_txt = "abstained" if (opp.get("momentum_abstained") or mom is None) else f"{mom:.0f}"
        print(f"    demand {opp.get('demand_score', 0):.0f}  −  supply {opp.get('supply_score', 0):.0f}"
              f"  (imbalance {opp.get('imbalance', 0):+.0f}),  momentum {mom_txt}")
        print()
        print(f"  ►  OPPORTUNITY : {score:.1f} / 100   [{grade}]  {_grade_emoji(grade)}")
        print(f"     Verdict     : {result.get('verdict', '—')}")

    # Opportunity indicators
    ind = result.get("indicators", {})
    if ind:
        print()
        print("  Opportunity indicators:")
        for k, v in ind.items():
            print(f"    {k:<28} {format_indicator(k, v):>8}")

    # Content diagnostics (not scored)
    diag = result.get("content_diagnostics", {})
    if diag:
        print()
        print("  Content diagnostics (not scored):")
        for k, v in diag.items():
            print(f"    {k:<28} {v}")

    # Highlights
    highlights = result.get("highlights", [])
    if highlights:
        print()
        print("  Highlights:")
        for h in highlights:
            print(f"    • {h}")
    print()


def print_niche_summary(meta: dict, df: pd.DataFrame):
    """Print a niche metadata summary and a sample of top videos."""
    name = meta.get("niche", "Unknown")
    _header(f"NICHE DATABASE — {name}")

    print(f"  Query    : {meta.get('query', '—')}")
    print(f"  Videos   : {meta.get('videos', 0):,}")
    print(f"  Channels : {meta.get('channels', 0):,}")
    print(f"  Built    : {_fmt_date(meta.get('created'))}")
    print(f"  Schema v : {meta.get('database_schema_version', '—')}")

    if meta.get("span_warning"):
        print(f"\n  ⚠️  {meta['span_warning']}")

    medians = {
        "views/hr":   meta.get("median_vph"),
        "views":      meta.get("median_views"),
        "engagement": meta.get("median_engagement"),
    }
    print("\n  Niche medians:")
    for label, val in medians.items():
        if val is not None:
            if label == "engagement":
                print(f"    {label:<12}: {val:.3%}")
            else:
                print(f"    {label:<12}: {val:,.1f}")

    # Top-video table
    if not df.empty:
        print(f"\n  Top {min(TERMINAL_MAX_ROWS, len(df))} videos by views/hour:")
        _print_video_table(df)

    print()


def print_quota(tracker):
    """Print the quota tracker summary."""
    print()
    print(str(tracker))
    print()


# ============================================================
# FILE EXPORT
# ============================================================

def save_json(result: dict, path: str):
    """Write a grade result (or any dict) to a JSON file."""
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Saved JSON → {path}")


def save_csv(df: pd.DataFrame, path: str):
    """Write a DataFrame to CSV."""
    _ensure_dir(path)
    df.to_csv(path, index=False)
    print(f"Saved CSV  → {path}")


def save_html(result: dict, df: pd.DataFrame, path: str):
    """
    Generate a standalone HTML report combining the grade result
    and a video table.
    """
    _ensure_dir(path)
    html = _build_html(result, df)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved HTML → {path}")


def export(result, df, topic, formats=("json",)):
    """
    Write a graded result in the requested formats and return
    {format: path}. report owns the OUTPUT filenames; callers just name
    the topic and pick formats — main.py no longer manipulates paths.
    """
    import collector  # local import: avoids a module-level cycle
    base = collector.topic_report_path(topic)
    stem = os.path.splitext(base)[0]

    written = {}
    if "json" in formats:
        save_json(result, base); written["json"] = base
    if "csv" in formats:
        p = stem + ".csv"; save_csv(df, p); written["csv"] = p
    if "html" in formats:
        p = stem + ".html"; save_html(result, df, p); written["html"] = p
    return written


# ============================================================
# HTML BUILDER
# ============================================================

def _build_html(result: dict, df: pd.DataFrame) -> str:
    """
    Assemble the HTML report from focused section builders.

    Previously one ~180-line function doing six jobs; now each section is
    independently readable and testable. Output is unchanged.
    """
    ctx = {
        "kind":  result.get("kind", "topic"),
        "name":  result.get("topic") or result.get("niche", "Unknown"),
        "score": result.get("score", 0),
        "grade": result.get("grade", "?"),
        "n":     result.get("sample_size", 0),
        "niche": result.get("niche", ""),
        "ts":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    return (
        _html_head(ctx)
        + _html_summary(ctx, result)
        + _html_pillars(result.get("pillars", {}))
        + _html_opportunity(result)
        + _html_indicators(result.get("indicators", {}))
        + _html_highlights(result.get("highlights", []))
        + _html_diagnostics(result.get("content_diagnostics", {}))
        + _html_video_table(df)
        + "</body>\n</html>"
    )


def _html_head(ctx) -> str:
    """Document head + CSS. Classes are defined here so section builders
    can use semantic class names instead of repeating inline styles."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{ctx['kind'].title()} Market Assessment — {ctx['name']}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 960px; margin: 40px auto; padding: 0 20px; color: #111; }}
    h1 {{ font-size: 1.6rem; border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }}
    h2 {{ font-size: 1.2rem; margin-top: 32px; color: #374151; }}
    table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; }}
    th {{ background: #f3f4f6; font-weight: 600; }}
    tr:nth-child(even) {{ background: #f9fafb; }}
    ul {{ padding-left: 20px; }} li {{ margin: 4px 0; }}
    .meta {{ color: #6b7280; font-size: .85rem; margin-bottom: 24px; }}
    .muted {{ color: #6b7280; }}
    .verdict {{ font-size: 1.05rem; font-weight: 600; }}
    /* Executive summary is what most readers need; analyst detail follows. */
    .exec {{ background: #f9fafb; border-left: 4px solid #d1d5db;
             padding: 12px 18px; margin: 16px 0 28px; }}
    .section-label {{ text-transform: uppercase; letter-spacing: .05em;
                      font-size: .72rem; color: #9ca3af; margin-top: 34px; }}
    .badge {{ display: inline-block; min-width: 24px; text-align: center;
              font-weight: 700; color: #fff; border-radius: 4px; padding: 1px 6px; }}
    .bar-bg {{ background: #e5e7eb; border-radius: 4px; height: 12px; width: 180px; }}
    .bar-fg {{ border-radius: 4px; height: 12px; }}
    .metric-row {{ display: flex; justify-content: space-between;
                   font-size: .78rem; color: #6b7280; }}
    .score-badge {{ display: inline-block; font-size: 3rem; font-weight: 700;
                    background: #f9fafb; border-radius: 12px; padding: 8px 24px;
                    margin: 8px 0; }}
  </style>
</head>
<body>
"""


def _html_summary(ctx, result) -> str:
    """Executive summary — the part most readers actually act on."""
    color = _grade_color(ctx["grade"])
    niche_line = f"<p><strong>Niche:</strong> {ctx['niche']}</p>" if ctx["niche"] else ""
    return f"""  <h1>{ctx['kind'].title()} Market Assessment — {ctx['name']}</h1>
  <p class="meta">Generated {ctx['ts']} · {ctx['n']:,} videos sampled</p>
  {niche_line}
  <div class="exec">
    <div class="score-badge" style="color:{color};border:2px solid {color}">
      {ctx['grade']} &nbsp; {ctx['score']:.1f}/100
    </div>
    <p class="verdict">{result.get('verdict', '')}</p>
  </div>
"""


def _html_pillars(pillars) -> str:
    if not pillars:
        return ""
    rows = []
    for pname, p in pillars.items():
        if p.get("abstained") or p.get("score") is None:
            reason = p.get("reason", "insufficient data")
            rows.append(
                f"<tr><td><strong>{pname}</strong></td>"
                f"<td class='muted'>—</td><td class='muted'>not scored</td>"
                f"<td class='muted' style='font-size:.8rem'>{reason}</td></tr>"
            )
            continue
        color = _grade_color(p["grade"])
        metric_bits = "".join(
            f"<div class='metric-row'><span>{m}</span><span>{s:.0f}</span></div>"
            for m, s in p.get("metrics", {}).items()
        )
        rows.append(
            f"<tr><td><strong>{pname}</strong></td>"
            f"<td><span class='badge' style='background:{color}'>{p['grade']}</span></td>"
            f"<td>{p['score']:.1f}</td>"
            f"<td><div class='bar-bg'><div class='bar-fg' style='background:{color};"
            f"width:{int(p['score'] * 1.8)}px'></div></div>"
            f"<div style='margin-top:4px'>{metric_bits}</div></td></tr>"
        )
    return ("  <div class='section-label'>Analyst detail</div>\n"
            "  <h2>Market Pillars</h2>\n  <table>\n"
            "    <thead><tr><th>Pillar</th><th>Grade</th><th>Score</th>"
            "<th>Breakdown</th></tr></thead>\n    <tbody>"
            + "".join(rows) + "</tbody>\n  </table>\n")


def _html_opportunity(result) -> str:
    opp = result.get("opportunity", {})
    if not opp:
        return ""
    mom = opp.get("momentum_score")
    mom_txt = ("abstained (insufficient recent data)"
               if (opp.get("momentum_abstained") or mom is None) else f"{mom:.0f}")
    return f"""  <h2>Opportunity (derived)</h2>
  <p class="meta">opportunity = 50 + (demand − supply)/2, nudged by momentum</p>
  <p><strong>Demand {opp.get('demand_score', 0):.0f}</strong> −
     <strong>Supply {opp.get('supply_score', 0):.0f}</strong>
     = imbalance {opp.get('imbalance', 0):+.0f}; momentum {mom_txt}
     → <strong>{opp.get('score', 0):.1f}/100</strong></p>
"""


def _html_indicators(ind) -> str:
    if not ind:
        return ""
    rows = "".join(
        f"<tr><td>{k}</td><td>{format_indicator(k, v)}</td></tr>" for k, v in ind.items()
    )
    return f"  <h2>Opportunity Indicators</h2>\n  <table><tbody>{rows}</tbody></table>\n"


def _html_highlights(highlights) -> str:
    if not highlights:
        return ""
    items = "".join(f"<li>{h}</li>" for h in highlights)
    return f"  <h2>Highlights</h2>\n  <ul>{items}</ul>\n"


def _html_diagnostics(diag) -> str:
    if not diag:
        return ""
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in diag.items())
    return ('  <h2>Content Diagnostics <span class="muted" '
            'style="font-weight:400;font-size:.8rem">(not scored)</span></h2>\n'
            f"  <table><tbody>{rows}</tbody></table>\n")


def _html_video_table(df) -> str:
    """Raw evidence — deliberately last; the market read comes first."""
    if df is None or df.empty:
        return ""
    cols = [c for c in SUMMARY_COLUMNS if c in df.columns]
    if not cols:
        return ""
    top = (df.sort_values("views_per_hour", ascending=False).head(TERMINAL_MAX_ROWS)
           if "views_per_hour" in df.columns else df.head(TERMINAL_MAX_ROWS))
    header = "".join(f"<th>{c}</th>" for c in cols)
    body = ""
    for _, row in top.iterrows():
        cells = ""
        for c in cols:
            v = row.get(c, "")
            if isinstance(v, float):
                v = f"{v:,.3f}" if abs(v) < 10 else f"{v:,.1f}"
            elif isinstance(v, bool):
                v = "✓" if v else "—"
            cells += f"<td>{v}</td>"
        body += f"<tr>{cells}</tr>"
    return ("  <div class='section-label'>Raw evidence</div>\n"
            "  <h2>Top Videos (by views/hour)</h2>\n"
            f'  <div style="overflow-x:auto"><table><thead><tr>{header}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>\n")


# ============================================================
# HELPERS
# ============================================================

def _header(title: str):
    line = "═" * (len(title) + 4)
    print(f"\n{line}")
    print(f"  {title}")
    print(f"{line}")


# Single source of truth for grade -> colour (was duplicated between the
# badge and the pillar rows).
GRADE_COLORS = {"A": "#16a34a", "B": "#2563eb", "C": "#d97706",
                "D": "#ea580c", "F": "#dc2626"}
_NEUTRAL = "#6b7280"


def _grade_color(grade: str) -> str:
    return GRADE_COLORS.get(grade, _NEUTRAL)


def _grade_emoji(grade: str) -> str:
    return {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"}.get(grade, "⚪")


def _score_bar(score: float, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _fmt_date(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(iso_str)


def _print_video_table(df: pd.DataFrame):
    cols = [c for c in SUMMARY_COLUMNS if c in df.columns]
    if not cols:
        return
    sort_col = "views_per_hour" if "views_per_hour" in df.columns else cols[0]
    top = df.sort_values(sort_col, ascending=False).head(TERMINAL_MAX_ROWS)

    # Build header
    col_widths = {c: max(len(c), 10) for c in cols}
    header = "  " + "  ".join(c.ljust(col_widths[c]) for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))

    for _, row in top.iterrows():
        cells = []
        for c in cols:
            val = row.get(c, "")
            if isinstance(val, float):
                fmt = f"{val:,.2f}" if abs(val) < 100 else f"{val:,.0f}"
            elif isinstance(val, bool):
                fmt = "Yes" if val else "No"
            else:
                fmt = str(val)
            # Truncate long strings (e.g. title)
            if len(fmt) > col_widths[c]:
                fmt = fmt[:col_widths[c] - 1] + "…"
            cells.append(fmt.ljust(col_widths[c]))
        print("  " + "  ".join(cells))


def format_indicator(key, value):
    """
    Shared display formatting for opportunity indicators so the terminal
    and HTML renderers can never drift apart (they used to each decide
    independently whether a value was a rate).
    """
    if value is None:
        return "—"
    if "rate" in key or "dominance" in key or "share" in key:
        return f"{value * 100:.1f}%"
    return f"{value:,.1f}"


def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
