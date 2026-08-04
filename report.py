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

import html
import pandas as pd

from config import (SUMMARY_COLUMNS, TERMINAL_MAX_ROWS, BREAKOUT_TIERS,
                    BREAKOUT_TIER_MIN_VIDEOS, BREAKOUT_MIN_VIEWS_LADDER)


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
            # Momentum is rendered on a SIGNED axis, the others on 0-100.
            # They are not the same kind of number: demand 50 means "at
            # the niche median", momentum 50 means "nothing has changed".
            # Sharing a bar style invites the eye to compare them, which
            # is exactly the wrong reading.
            signed = p.get("signed_change")
            if pname == "momentum" and signed is not None:
                bar = _signed_bar(signed)
                print(f"    {pname:<10} {signed:+5.1f}     [{p['grade']}]  "
                      f"{bar}   0 = breakeven")
            else:
                bar = _score_bar(p["score"])
                print(f"    {pname:<10} {p['score']:5.1f}/100  [{p['grade']}]  {bar}")
            # Signal groups first (graded sub-scores), then raw metrics.
            for gname, g in p.get("groups", {}).items():
                print(f"        {gname:<24} {g['score']:5.1f}  [{g['grade']}]"
                      f"  (weight {g['weight']:.0%})")
            for mname, mscore in p.get("metrics", {}).items():
                # Signed components get an explicit sign and a plain-word
                # reading. A bare "-74.2" beside demand's percentiles
                # looks like a catastrophic score; it actually means the
                # quantity roughly halved.
                if pname == "momentum" and signed is not None:
                    print(f"          · {mname:<26} {mscore:+6.1f}   "
                          f"{signed_reading(mscore)}")
                else:
                    print(f"          · {mname:<24} {mscore:5.1f}")

    # Opportunity LAST — it is derived from the pillars above, so the
    # report reads in computation order: measured inputs, then the result.
    opp = result.get("opportunity", {})
    if opp:
        print()
        print("  " + "-" * 56)
        print("  Opportunity is DERIVED from the pillars above:")
        # Signed, to match the pillar bar directly above. The 0-100 form
        # sat in a row with demand and supply and read as a third
        # comparable score, which it is not.
        mom = opp.get("momentum_signed")
        mom_txt = ("abstained" if (opp.get("momentum_abstained") or mom is None)
                   else f"{mom:+.0f} signed")
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

    # Median AND top decile, because one number cannot describe a
    # distribution this skewed. The GAP between them is the useful part:
    # it says whether this is a market where a few videos take
    # everything, or one where solid work reliably lands somewhere.
    #
    # The mean is deliberately NOT shown. On real data, removing a single
    # video from a 291-video sample moved the mean view count by 69%
    # while the median moved 0.9% — one viral upload makes the mean say
    # something entirely different about a market that has not changed.
    # P90 carries the same "what does winning look like" information and
    # is stable.
    rows = [
        ("views/hr",   meta.get("median_vph"),        meta.get("p90_vph")),
        ("views",      meta.get("median_views"),      meta.get("p90_views")),
        ("engagement", meta.get("median_engagement"), meta.get("p90_engagement")),
    ]
    have_p90 = any(p is not None for _, _, p in rows)
    print("\n  Niche profile:")
    if have_p90:
        print(f"    {'':<12}  {'typical':>12}  {'top 10%':>12}   spread")
    for label, med, p90 in rows:
        if med is None:
            continue
        fmt = (lambda v: f"{v:.3%}") if label == "engagement" else (lambda v: f"{v:,.1f}")
        if p90 is None:
            print(f"    {label:<12}: {fmt(med)}")
            continue
        ratio = (p90 / med) if med else 0
        print(f"    {label:<12}: {fmt(med):>12}  {fmt(p90):>12}   {ratio:>5.1f}x")

    if have_p90 and meta.get("median_views") and meta.get("p90_views"):
        r = meta["p90_views"] / meta["median_views"]
        if r >= 15:
            read = ("winner-take-all — a few videos take almost everything, "
                    "so being average earns close to nothing")
        elif r >= 6:
            read = ("top-heavy, which is normal for YouTube — breakouts "
                    "clearly happen here")
        else:
            read = ("unusually flat — steady returns, but little evidence "
                    "of big breakouts to aim at")
        print(f"\n    Spread reading: {read}.")

    # Top-video table
    if not df.empty:
        print(f"\n  Top {min(TERMINAL_MAX_ROWS, len(df))} videos by views/hour:")
        _print_video_table(df)

    print()


def print_quota(tracker):
    """Print quota usage as units AND percent, with per-key detail."""
    s = tracker.summary()
    used, cap = s["used"], s.get("capacity") or s["limit"]
    pct = s.get("pct_used", 100.0 * used / cap if cap else 0)
    print()
    print(f"  Quota used : {used:,} / {cap:,} units  ({pct:.1f}%)")
    bar_len = int(pct / 5)
    print(f"               [{'█' * bar_len}{'·' * (20 - bar_len)}]")
    if s.get("per_key"):
        print(f"  Per key    : {s['per_key']}")
    if s.get("breakdown"):
        bits = ", ".join(f"{k} {v:,}" for k, v in s["breakdown"].items())
        print(f"  Breakdown  : {bits}")
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
        signed = p.get("signed_change")
        is_momentum = (pname == "momentum" and signed is not None)

        if is_momentum:
            # Signed components read as plain multiples. "-74.2" beside a
            # column of percentiles looks like a failing score when it
            # actually means the quantity fell to about a fifth.
            bits = []
            for m, s in p.get("metrics", {}).items():
                bits.append(f"<div class='metric-row'><span>{m}</span>"
                            f"<span>{s:+.0f} &nbsp;<em>{signed_reading(s)}</em>"
                            f"</span></div>")
            metric_bits = "".join(bits)
            # Centred bar: growth fills right of the zero line, decline
            # fills left. A 0-100 bar has no zero point, so momentum 50
            # ("nothing changed") looked identical to a mediocre score.
            half = 90
            mag = int(min(abs(signed), 100) / 100 * half)
            if signed >= 0:
                fill = (f"<div style='position:absolute;left:{half}px;width:{mag}px;"
                        f"height:12px;background:{color};border-radius:0 4px 4px 0'></div>")
            else:
                fill = (f"<div style='position:absolute;left:{half-mag}px;width:{mag}px;"
                        f"height:12px;background:{color};border-radius:4px 0 0 4px'></div>")
            bar = (f"<div style='position:relative;width:{half*2}px;height:12px;"
                   f"background:#e5e7eb;border-radius:4px'>{fill}"
                   f"<div style='position:absolute;left:{half}px;width:2px;"
                   f"height:12px;background:#6b7280'></div></div>"
                   f"<div class='muted' style='font-size:.7rem;margin-top:2px'>"
                   f"&#8592; decline &nbsp;|&nbsp; 0 = breakeven &nbsp;|&nbsp; growth &#8594;</div>")
            score_cell = f"{signed:+.1f}"
        else:
            metric_bits = "".join(
                f"<div class='metric-row'><span>{m}</span><span>{s:.0f}</span></div>"
                for m, s in p.get("metrics", {}).items()
            )
            bar = (f"<div class='bar-bg'><div class='bar-fg' style='background:{color};"
                   f"width:{int(p['score'] * 1.8)}px'></div></div>")
            score_cell = f"{p['score']:.1f}"

        rows.append(
            f"<tr><td><strong>{pname}</strong></td>"
            f"<td><span class='badge' style='background:{color}'>{p['grade']}</span></td>"
            f"<td>{score_cell}</td>"
            f"<td>{bar}"
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
    mom = opp.get("momentum_signed")
    mom_txt = ("abstained (insufficient recent data)"
               if (opp.get("momentum_abstained") or mom is None)
               else f"{mom:+.0f} signed")
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
    """
    Raw evidence — deliberately last; the market read comes first.

    Renders three separate tables, one per BREAKOUT_TIER:
      - Brand-new (<2k subs): direct case studies for creators just starting
      - Small (2k-100k): "peers with some traction"
      - Top overall (100k+, ranked by views/hour): what wins in absolute terms

    Different questions, different answers. A brand-new creator gets more
    from the first table (survivorship-biased but relevant). The last
    table still matters because it reveals demand shape — what the market
    wants, regardless of who can currently deliver it.
    """
    if df is None or df.empty:
        return ""
    cols = [c for c in SUMMARY_COLUMNS if c in df.columns]
    if not cols:
        return ""

    def _render_table(subset, heading, note=""):
        if subset.empty:
            return ""
        header = "".join(f"<th>{c}</th>" for c in cols)
        body = ""
        for _, row in subset.iterrows():
            cells = ""
            for c in cols:
                v = row.get(c, "")
                if isinstance(v, float):
                    v = f"{v:,.3f}" if abs(v) < 10 else f"{v:,.1f}"
                elif isinstance(v, bool):
                    v = "✓" if v else "—"
                cells += f"<td>{v}</td>"
            body += f"<tr>{cells}</tr>"
        note_html = f'<p class="meta">{note}</p>' if note else ""
        # HTML-escape the heading — tier names in config use raw <, > (e.g.
        # "<2k subs") which would otherwise be parsed as tags and swallow
        # the rest of the section.
        return (f"  <h2>{html.escape(heading)}</h2>\n{note_html}"
                f'  <div style="overflow-x:auto"><table><thead><tr>{header}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>\n")

    out = ["  <div class='section-label'>Raw evidence</div>\n"]

    if "channel_subs" not in df.columns:
        # Fall back to the simple overall list if we somehow lack sub data.
        top = (df.sort_values("views_per_hour", ascending=False).head(TERMINAL_MAX_ROWS)
               if "views_per_hour" in df.columns else df.head(TERMINAL_MAX_ROWS))
        out.append(_render_table(top, "Top Videos (by views/hour)"))
        return "".join(out)

    subs = pd.to_numeric(df["channel_subs"], errors="coerce")

    tier_notes = {
        "brand_new": ("<b>Direct case studies for a new channel.</b> These videos "
                      "won for channels with almost no subscribers — meaning they "
                      "won on topic/title/thumbnail alone, which is exactly what "
                      "you can control at that size. Survivorship-biased: channels "
                      "this small that appear in search results at all are "
                      "already the exception. Ranked by outlier ratio (how much "
                      "each video beat its own channel's average)."),
        "small":      ("Channels with some traction but no institutional muscle. "
                      "Ranked by outlier ratio."),
        "top_by_reach": ("What's winning in absolute terms. Different signal from "
                        "the tables above — this reveals what the MARKET wants, "
                        "not what small channels can currently deliver. Ranked "
                        "by views/hour."),
    }

    for lo, hi, key, heading in BREAKOUT_TIERS:
        mask = (subs >= lo) & (subs < hi) if hi is not None else (subs >= lo)
        tier_df = df[mask.fillna(False)]
        if len(tier_df) < BREAKOUT_TIER_MIN_VIDEOS:
            continue

        # Small-channel tiers ranked by outlier_ratio (self-beating);
        # top-by-reach ranked by absolute views/hour. That's the whole
        # reason for the split.
        floor_used = None
        if key == "top_by_reach":
            rank_col = "views_per_hour" if "views_per_hour" in tier_df.columns else None
        else:
            rank_col = "outlier_ratio" if "outlier_ratio" in tier_df.columns else "views_per_hour"
            # Floor absolute views before ranking by ratio — see
            # BREAKOUT_MIN_VIEWS_LADDER. Without this a 201-view video
            # on a 15-view-average channel outranks a 445k-view video.
            if "view_count" in tier_df.columns:
                vc = pd.to_numeric(tier_df["view_count"], errors="coerce")
                for floor in BREAKOUT_MIN_VIEWS_LADDER:
                    qualified = tier_df[(vc >= floor).fillna(False)]
                    if len(qualified) >= TERMINAL_MAX_ROWS or floor == 0:
                        tier_df, floor_used = qualified, floor
                        break

        if rank_col:
            tier_df = tier_df.sort_values(rank_col, ascending=False)
        tier_df = tier_df.head(TERMINAL_MAX_ROWS)

        note = tier_notes.get(key, "")
        n_note = f" &nbsp;·&nbsp; {len(df[mask.fillna(False)]):,} videos in this tier"
        if floor_used:
            n_note += (f", showing only those above {floor_used:,} views "
                       f"(ratio is unreliable below that)")
        elif floor_used == 0:
            n_note += (" &nbsp;·&nbsp; ⚠️ too few high-view videos to filter; "
                       "low-view entries may have unstable ratios")
        out.append(_render_table(tier_df, heading, note=note + n_note))

    return "".join(out)


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


def signed_reading(value: float) -> str:
    """
    Plain-language reading of a signed momentum component.

    Marks the CLAMP explicitly. _signed_log saturates at +/-100, so a
    component reported as -100 could be a 10x fall or a 500x one — on
    real data an evergreen ratio of 0.0048 computes to -231 before
    clamping. Printing a bare "10.0x down" claims a precision the number
    does not have, so the clamp reads ">=".
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    at_clamp = abs(v) >= 99.5
    ratio = 10 ** (abs(v) / 100.0)
    if abs(v) < 10:
        return "flat"
    direction = "up" if v > 0 else "down"
    prefix = "\u2265" if at_clamp else ""
    return f"{prefix}{ratio:.1f}x {direction}"


def _signed_bar(signed: float, width: int = 20) -> str:
    """
    Centred bar for a value that runs -100..+100 with a MEANINGFUL ZERO.

    A 0-100 bar cannot express momentum. Momentum 50 means "nothing is
    changing", which is the normal, healthy, expected state — but a
    half-filled bar reads as mediocre, and the eye compares it directly
    against demand 50 and supply 50, which mean something completely
    different. The bar needs a zero point, so it gets one: growth fills
    rightward from the centre, decline fills leftward.

        -50 ........  ████│        ........
          0 ........      │        ........
        +50 ........      │████    ........
    """
    half = width // 2
    signed = max(-100.0, min(100.0, float(signed)))
    cells = round(abs(signed) / 100 * half)
    if signed >= 0:
        left = "░" * half
        right = "█" * cells + "░" * (half - cells)
    else:
        left = "░" * (half - cells) + "█" * cells
        right = "░" * half
    return f"{left}│{right}"


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
