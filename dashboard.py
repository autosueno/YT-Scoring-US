"""
dashboard.py

A visual HTML dashboard for a grade result (topic or niche), matching the
dark visual language of calendars.py so every export from this engine looks
like it belongs to the same tool.

WHY THIS EXISTS SEPARATELY FROM report.py's save_html
------------------------------------------------------
report.py already has an HTML exporter, but it predates the calendar work
and renders light/default-styled tables — visually disconnected from
everything else the engine now produces. Rather than reskin that exporter
in place (grader/report output shape is stable and other code depends on
it), this module is a second, purely presentational view of the same
result dict. Nothing here computes anything; it only renders.

THE SELF-COMPARISON CAVEAT IS RENDERED, NOT JUST DOCUMENTED
-------------------------------------------------------------
Grading a niche against its OWN baseline is not neutral: SUPPLY compares
median-vs-median (a true self-comparison lands at exactly 50), but DEMAND
blends median percentile with P90 percentile (DEMAND_TAIL_WEIGHT=0.35), and
a median/P90 pair is 50/90 by definition — so demand is STRUCTURALLY forced
to 0.65*50 + 0.35*90 = 64 on every niche self-grade, regardless of the
niche. That is not a finding; it is the shape of the formula. A banner
saying so appears on-page whenever result["self_comparison"] is True,
because burying that in a docstring means it only reaches someone who
reads the source.
"""

import html
import os
from datetime import datetime, timezone

from config import EXPORTS_DIR
from paths import export_dir_for


def _grade_colour(grade):
    return {"A": "#22c55e", "B": "#65b96b", "C": "#8b8b96",
            "D": "#c97b4a", "F": "#dc4a45"}.get(grade, "#8b8b96")


def _score_colour(score):
    if score is None:
        return "#55555e"
    if score >= 70:
        return "#22c55e"
    if score >= 55:
        return "#65b96b"
    if score >= 45:
        return "#8b8b96"
    if score >= 30:
        return "#c97b4a"
    return "#dc4a45"


_CSS = """
<style>
 body{background:#0f0f12;color:#e8e8ee;font-family:-apple-system,BlinkMacSystemFont,
      'Segoe UI',Roboto,sans-serif;margin:0;padding:32px 36px;line-height:1.55}
 h1{font-size:23px;margin:0 0 4px;font-weight:700;letter-spacing:-.2px}
 h2{font-size:15.5px;margin:28px 0 12px;font-weight:650;color:#d4d4de}
 .sub{color:#96969f;font-size:13.5px;margin-bottom:6px}
 .caveat{margin:20px 0;padding:16px 20px;background:#241a12;border:1px solid #4a3420;
         border-radius:8px;font-size:13px;color:#d9b98a;max-width:820px}
 .caveat b{color:#f0d4a8}
 .headline{margin:22px 0 26px;padding:22px 26px;border-radius:12px;max-width:820px;
           background:linear-gradient(135deg,#17171d,#101014);border:1px solid #2a2a34;
           display:flex;align-items:center;gap:28px}
 .gauge{width:96px;height:96px;border-radius:50%;display:flex;align-items:center;
        justify-content:center;flex-direction:column;flex-shrink:0;border:5px solid}
 .gauge .num{font-size:26px;font-weight:750}
 .gauge .grade{font-size:12px;opacity:.8;margin-top:-2px}
 .headline .verdict{font-size:16px;font-weight:650;color:#f0f0f4}
 .headline .meta{font-size:13px;color:#96969f;margin-top:5px}
 .pillars{display:flex;gap:16px;flex-wrap:wrap;max-width:900px}
 .pillar{flex:1;min-width:230px;background:#17171c;border:1px solid #26262e;
         border-radius:10px;padding:16px 18px}
 .pillar .name{font-size:12px;text-transform:uppercase;letter-spacing:.5px;
               color:#96969f;font-weight:650}
 .pillar .score{font-size:28px;font-weight:750;margin:4px 0 2px}
 .bar-track{height:7px;background:#26262e;border-radius:4px;overflow:hidden;margin:8px 0 12px}
 .bar-fill{height:100%;border-radius:4px}
 .group{display:flex;justify-content:space-between;font-size:12.5px;color:#b4b4be;
        padding:4px 0;border-top:1px solid #1f1f26}
 .group .w{color:#6e6e78;font-size:11px}
 .metric-row{display:flex;justify-content:space-between;font-size:11.5px;
             color:#7a7a84;padding:2px 0 2px 12px}
 table.ind{border-collapse:collapse;max-width:640px;margin-top:8px}
 table.ind td{padding:7px 16px 7px 0;font-size:13.5px;border-bottom:1px solid #1e1e25}
 table.ind td.k{color:#96969f}
 ul.hi{font-size:13.5px;color:#c4c4cc;padding-left:20px;max-width:700px}
 ul.hi li{margin-bottom:5px}
 .note{margin-top:26px;padding:16px 19px;background:#17171c;border-left:3px solid #3a3a44;
       border-radius:7px;font-size:12.5px;color:#9a9aa4;max-width:820px}
 .note b{color:#d4d4de}
</style>
"""


def _pillar_card(name, pillar):
    score = pillar.get("score")
    grade = pillar.get("grade", "?")
    colour = _score_colour(score)
    signed = pillar.get("signed_change")
    is_momentum = (name == "momentum" and signed is not None)

    if is_momentum:
        # Momentum needs a ZERO POINT. A 0-100 fill bar makes momentum 50
        # — which means "nothing has changed", the normal state — look
        # like a mediocre score, and invites comparison against demand 50
        # and supply 50, which mean something entirely different. Growth
        # fills right of centre, decline fills left.
        mag = min(abs(float(signed)), 100) / 2.0
        side = (f"left:50%;width:{mag}%;border-radius:0 3px 3px 0"
                if signed >= 0 else
                f"left:{50-mag}%;width:{mag}%;border-radius:3px 0 0 3px")
        bar = (f'<div class="bar-track" style="position:relative">'
               f'<div class="bar-fill" style="position:absolute;{side};'
               f'background:{colour}"></div>'
               f'<div style="position:absolute;left:50%;width:2px;height:100%;'
               f'background:#9a9aa4"></div></div>'
               f'<div style="font-size:10px;color:#8a8a94;margin-top:3px">'
               f'&#8592; decline &nbsp;|&nbsp; 0 = breakeven &nbsp;|&nbsp; growth &#8594;</div>')
        headline = (f'<div class="score" style="color:{colour}">{signed:+.1f}'
                    f'<span style="font-size:14px;color:#6e6e78;font-weight:600">'
                    f' signed [{grade}]</span></div>')
    else:
        bar = (f'<div class="bar-track"><div class="bar-fill" '
               f'style="width:{min(100,max(0,score or 0))}%;'
               f'background:{colour}"></div></div>')
        headline = (f'<div class="score" style="color:{colour}">'
                    f'{score if score is not None else "—"}'
                    f'<span style="font-size:14px;color:#6e6e78;font-weight:600">'
                    f'/100 [{grade}]</span></div>')

    parts = [f'''<div class="pillar">
      <div class="name">{html.escape(name)}</div>
      {headline}
      {bar}''']
    for gname, g in (pillar.get("groups") or {}).items():
        if isinstance(g, dict):
            gs, gg, gw = g.get("score"), g.get("grade", "?"), g.get("weight")
            wtxt = f" ({gw:.0%})" if gw else ""
            parts.append(f'<div class="group"><span>{html.escape(str(gname))}</span>'
                        f'<span class="w">{gs} [{gg}]{wtxt}</span></div>')
    for mname, mval in (pillar.get("metrics") or {}).items():
        if is_momentum:
            try:
                from report import signed_reading
                v = float(mval)
                shown = (f"{v:+.0f} <span style='color:#8a8a94'>"
                         f"{signed_reading(v)}</span>")
            except (TypeError, ValueError, ImportError):
                shown = html.escape(str(mval))
        else:
            shown = html.escape(str(mval))
        parts.append(f'<div class="metric-row"><span>{html.escape(str(mname))}</span>'
                    f'<span>{shown}</span></div>')
    parts.append("</div>")
    return "\n".join(parts)


def _self_comparison_banner(result):
    if not result.get("self_comparison"):
        return ""
    return """
<div class="caveat">
  <b>⚠️ This niche is graded against its OWN baseline</b> — every video in
  the comparison also built the comparison. Two pillars behave very
  differently under that condition:<br><br>
  <b>Supply</b> compares median-vs-median, so a true self-comparison lands
  at <b>exactly 50</b> — that confirms the machinery ran, not that supply
  is average in any interesting sense.<br><br>
  <b>Demand</b> blends each metric's median percentile with its 90th-percentile
  reading (to weight "who's raving right now" over pure typicality). A
  median sits at the 50th percentile of itself and P90 at the 90th, by
  definition — so demand is <b>structurally forced toward the mid-60s</b>
  on every niche self-grade. That is the shape of the formula, not a
  finding about this niche.<br><br>
  <b>Momentum</b> is the one pillar that stays genuinely informative here —
  it compares a recent window against a prior one, which can move freely.<br><br>
  This becomes a real comparison the moment you grade one niche against a
  <i>different</i> niche's baseline — e.g. "finance" videos against a
  "gaming" baseline — where nothing above is tautological.
</div>"""


def build_dashboard_html(result, niche_name=None) -> str:
    kind = result.get("kind", "topic")
    name = result.get("topic") or result.get("niche", "Unknown")
    score, grade = result.get("score", 0), result.get("grade", "?")
    n = result.get("sample_size", 0)
    colour = _score_colour(score)
    verdict = result.get("verdict", "—")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts = [_CSS,
             f"<h1>{kind.title()} Market Assessment — {html.escape(str(name))}</h1>",
             f"<div class='sub'>{n:,} videos analysed · generated {ts}</div>",
             _self_comparison_banner(result),
             f"""<div class="headline">
               <div class="gauge" style="border-color:{colour};color:{colour}">
                 <div class="num">{score:.0f}</div><div class="grade">[{grade}]</div>
               </div>
               <div><div class="verdict">{html.escape(verdict)}</div>
               <div class="meta">Opportunity = 50 + (demand − supply)/2, nudged by momentum</div></div>
             </div>""",
             "<h2>Market pillars</h2>",
             "<div class='pillars'>"]
    for pname in ("demand", "supply", "momentum"):
        p = (result.get("pillars") or {}).get(pname)
        if p:
            parts.append(_pillar_card(pname, p))
    parts.append("</div>")

    ind = result.get("indicators") or {}
    if ind:
        parts.append("<h2>Opportunity indicators</h2><table class='ind'>")
        for k, v in ind.items():
            parts.append(f"<tr><td class='k'>{html.escape(str(k))}</td><td>{v}</td></tr>")
        parts.append("</table>")

    hi = result.get("highlights") or []
    if hi:
        parts.append("<h2>Highlights</h2><ul class='hi'>")
        parts += [f"<li>{html.escape(str(h))}</li>" for h in hi]
        parts.append("</ul>")

    parts.append(
        "<div class='note'><b>Reading this dashboard.</b> Opportunity is "
        "DERIVED, not independently measured — it always equals the "
        "demand/supply imbalance, nudged by momentum. If momentum shows "
        "no reliable score, it abstained rather than guessed from a "
        "recent window too thin to trust.</div>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{kind.title()} assessment — {html.escape(str(name))}</title></head>"
            "<body>" + "\n".join(parts) + "</body></html>")


def export_dashboard(result, niche_name) -> str:
    """Write the dashboard HTML to exports/<niche>/dashboard.html (topic
    grades write to exports/<niche>/topic_<topic>_dashboard.html so a
    niche's several topic checks don't overwrite each other)."""
    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "market")
    kind = result.get("kind", "topic")
    if kind == "topic":
        from paths import safe_name
        fname = f"topic_{safe_name(result.get('topic', 'unknown'))}_dashboard.html"
    else:
        fname = "dashboard.html"
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_dashboard_html(result, niche_name))
    return path
