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
    return {"A": "#2ECF96", "B": "#7FD9A8", "C": "#9AA7BC",
            "D": "#E39A5C", "F": "#F0576B"}.get(grade, "#9AA7BC")


def _score_colour(score):
    if score is None:
        return "#67748C"
    if score >= 70:
        return "#2ECF96"
    if score >= 55:
        return "#7FD9A8"
    if score >= 45:
        return "#9AA7BC"
    if score >= 30:
        return "#E39A5C"
    return "#F0576B"


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
   --accent:#4C8DFF; --good:#2ECF96; --bad:#F0576B; --warn:#EFA53E;
   --warn-soft:rgba(239,165,62,.13);
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
 .page{max-width:980px;margin:0 auto;padding:clamp(20px,4vw,48px)}
 .topbar{display:flex;align-items:flex-start;justify-content:space-between;
         gap:20px;flex-wrap:wrap;margin:0 0 22px}
 h1{font-size:23px;margin:0 0 5px;font-weight:800;letter-spacing:-.02em}
 .sub{color:var(--ink-2);font-size:13px;margin:0;font-family:var(--mono)}
 h2{font-size:15px;margin:32px 0 13px;font-weight:700;color:var(--ink);
    letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
 h2::before{content:"";width:3px;height:15px;border-radius:2px;
            background:var(--accent)}
 .card{background:var(--card);border:1px solid var(--line);
       border-radius:18px;padding:clamp(16px,2.4vw,22px)}
 /* ---- caveat / info banner ---- */
 .caveat{margin:0 0 22px;padding:18px 22px;background:var(--warn-soft);
         border:1px solid rgba(239,165,62,.28);border-radius:16px;
         font-size:13px;color:var(--ink-2);max-width:820px}
 .caveat .head{display:flex;align-items:center;gap:8px;font-weight:700;
              color:var(--warn);font-size:13px;margin-bottom:12px}
 .caveat-rows{display:grid;gap:8px;margin-bottom:12px}
 .caveat-row{display:flex;align-items:baseline;gap:8px;font-size:12.5px}
 .caveat-row .tag{font-family:var(--mono);font-size:10.5px;
                  text-transform:uppercase;letter-spacing:.05em;
                  color:var(--ink);background:rgba(255,255,255,.08);
                  border-radius:5px;padding:2px 7px;flex-shrink:0}
 .caveat p{margin:0}
 /* ---- headline ---- */
 .headline{margin:0 0 26px;padding:24px 28px;position:relative;
           background:var(--card-hi);border:1px solid var(--line);
           border-radius:20px;overflow:hidden;
           display:flex;align-items:center;gap:28px;flex-wrap:wrap}
 .headline::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;
                   background:var(--tint,var(--accent))}
 .gauge{width:104px;height:104px;border-radius:50%;flex-shrink:0;
        display:grid;place-items:center;position:relative;
        border:6px solid var(--tint,var(--accent))}
 @supports (background:conic-gradient(red,blue)){
   .gauge{border:none;
          background:conic-gradient(var(--tint,var(--accent))
                      calc(var(--pct,50)*1%),rgba(255,255,255,.08) 0)}
   .gauge::before{content:"";position:absolute;inset:7px;border-radius:50%;
                  background:#0d1526}
 }
 .gauge .inner{position:relative;text-align:center}
 .gauge .num{font-size:26px;font-weight:800;line-height:1}
 .gauge .grade{font-size:11px;color:var(--ink-2);margin-top:2px;
              font-family:var(--mono)}
 .headline .body{flex:1;min-width:220px}
 .headline .verdict{font-size:16.5px;font-weight:700;color:var(--ink);
                    letter-spacing:-.01em;margin-bottom:12px}
 .spectrum{max-width:400px}
 .spectrum-track{position:relative;height:6px;border-radius:4px;
                 background:linear-gradient(90deg,var(--bad),var(--ink-3) 50%,var(--good));
                 opacity:.55;margin:6px 0}
 .spectrum-dot{position:absolute;top:50%;width:14px;height:14px;
              border-radius:50%;background:var(--tint,var(--accent));
              border:2.5px solid #0d1526;transform:translate(-50%,-50%)}
 .spectrum-labels{display:flex;justify-content:space-between;font-size:10.5px;
                  color:var(--ink-3);font-family:var(--mono);
                  text-transform:uppercase;letter-spacing:.04em}
 /* ---- pillars ---- */
 .pillars{display:grid;gap:14px;grid-template-columns:repeat(3,1fr)}
 @media(max-width:760px){.pillars{grid-template-columns:1fr}}
 .pillar .name{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
               color:var(--ink-3);font-weight:700;margin-bottom:8px}
 .pillar .score{font-size:27px;font-weight:800;margin:0 0 3px;
                letter-spacing:-.01em}
 .pillar .score .of100{font-size:13px;color:var(--ink-3);font-weight:600}
 .bar-track{height:7px;background:rgba(255,255,255,.07);border-radius:5px;
            overflow:hidden;margin:9px 0 13px;position:relative}
 .bar-fill{height:100%;border-radius:5px}
 .zero-line{position:absolute;left:50%;width:2px;height:100%;
           background:rgba(255,255,255,.35)}
 .zero-caption{font-size:10px;color:var(--ink-3);margin:-8px 0 12px;
              font-family:var(--mono)}
 .group{display:flex;align-items:center;gap:8px;font-size:11.5px;
        color:var(--ink-2);padding:5px 0;border-top:1px solid var(--line-soft)}
 .group .glabel{flex:0 0 auto;min-width:76px}
 .group .gbar{flex:1;height:4px;border-radius:3px;background:rgba(255,255,255,.07);
             overflow:hidden}
 .group .gbar>span{display:block;height:100%;border-radius:3px}
 .group .gval{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);
             flex:0 0 auto;width:44px;text-align:right}
 .metric-row{display:flex;justify-content:space-between;font-size:11px;
             color:var(--ink-3);padding:3px 0 3px 4px;font-family:var(--mono)}
 /* ---- indicators / highlights ---- */
 table.ind{border-collapse:collapse;width:100%}
 table.ind td{padding:9px 4px;font-size:13px;border-bottom:1px solid var(--line-soft)}
 table.ind tr:last-child td{border-bottom:none}
 table.ind td.k{color:var(--ink-2)}
 table.ind td:last-child{text-align:right;font-family:var(--mono);
                         font-weight:600;color:var(--ink)}
 ul.hi{list-style:none;margin:0;padding:0;display:grid;gap:9px}
 ul.hi li{font-size:13.5px;color:var(--ink-2);padding-left:18px;
          position:relative}
 ul.hi li::before{content:"";position:absolute;left:0;top:8px;width:6px;
                  height:6px;border-radius:50%;background:var(--accent)}
 .note{margin-top:26px;padding:18px 22px;background:var(--card);
       border:1px solid var(--line);border-left:3px solid var(--accent);
       border-radius:14px;font-size:12.5px;color:var(--ink-2);max-width:820px}
 .note b{color:var(--ink);font-weight:600}
 footer.gen{margin-top:30px;font-family:var(--mono);font-size:10.5px;
            color:var(--ink-3)}
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
        side = (f"left:50%;width:{mag}%;border-radius:0 4px 4px 0"
                if signed >= 0 else
                f"left:{50-mag}%;width:{mag}%;border-radius:4px 0 0 4px")
        bar = (f'<div class="bar-track"><div class="zero-line"></div>'
               f'<div class="bar-fill" style="position:absolute;{side};'
               f'background:{colour}"></div></div>'
               f'<div class="zero-caption">&#8592; decline &nbsp;&middot;&nbsp; '
               f'0 = breakeven &nbsp;&middot;&nbsp; growth &#8594;</div>')
        headline = (f'<div class="score" style="color:{colour}">{signed:+.1f}'
                    f'<span class="of100"> signed [{grade}]</span></div>')
    else:
        bar = (f'<div class="bar-track"><div class="bar-fill" '
               f'style="width:{min(100,max(0,score or 0))}%;'
               f'background:{colour}"></div></div>')
        headline = (f'<div class="score" style="color:{colour}">'
                    f'{score if score is not None else "—"}'
                    f'<span class="of100">/100 [{grade}]</span></div>')

    parts = [f'''<div class="card pillar">
      <div class="name">{html.escape(name)}</div>
      {headline}
      {bar}''']
    for gname, g in (pillar.get("groups") or {}).items():
        if isinstance(g, dict):
            gs, gg, gw = g.get("score"), g.get("grade", "?"), g.get("weight")
            gc = _score_colour(gs)
            wtxt = f" ({gw:.0%})" if gw else ""
            parts.append(
                f'<div class="group"><span class="glabel">{html.escape(str(gname))}'
                f'{wtxt}</span><div class="gbar"><span style="width:'
                f'{min(100,max(0,gs or 0))}%;background:{gc}"></span></div>'
                f'<span class="gval">{gs} [{gg}]</span></div>')
    for mname, mval in (pillar.get("metrics") or {}).items():
        if is_momentum:
            try:
                from report import signed_reading
                v = float(mval)
                shown = (f"{v:+.0f} <span style='color:var(--ink-3)'>"
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
  <div class="head">&#9888; Graded against its own baseline</div>
  <div class="caveat-rows">
    <div class="caveat-row"><span class="tag">Supply</span>
      <p>Median-vs-median lands at <b style="color:var(--ink)">exactly 50</b>
      here — that confirms the machinery ran, not that supply is average
      in any interesting sense.</p></div>
    <div class="caveat-row"><span class="tag">Demand</span>
      <p>Blends median and P90 percentile, which are 50 and 90 of
      themselves by definition — so demand is
      <b style="color:var(--ink)">structurally forced toward the mid-60s</b>
      on every self-grade. That's the shape of the formula, not a finding.</p></div>
    <div class="caveat-row"><span class="tag" style="background:var(--good);
      color:#06251a">Momentum</span>
      <p>The one pillar that stays genuinely informative here — it compares
      a recent window against a prior one, which can move freely.</p></div>
  </div>
  <p>This becomes a real comparison the moment you grade one niche against a
  <i>different</i> niche's baseline — nothing above is tautological there.</p>
</div>"""


def build_dashboard_html(result, niche_name=None) -> str:
    kind = result.get("kind", "topic")
    name = result.get("topic") or result.get("niche", "Unknown")
    score, grade = result.get("score", 0), result.get("grade", "?")
    n = result.get("sample_size", 0)
    colour = _score_colour(score)
    verdict = result.get("verdict", "—")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pct = max(0, min(100, score or 0))

    parts = [_CSS, "<div class='page'>",
             "<div class='topbar'><div>",
             f"<h1>{kind.title()} assessment — {html.escape(str(name))}</h1>",
             f"<p class='sub'>{n:,} videos analysed &middot; generated {ts}</p>",
             "</div></div>",
             _self_comparison_banner(result),
             f"""<div class="headline" style="--tint:{colour}">
               <div class="gauge" style="--tint:{colour};--pct:{pct}">
                 <div class="inner"><div class="num" style="color:{colour}">
                 {score:.0f}</div><div class="grade">grade {grade}</div></div>
               </div>
               <div class="body">
                 <div class="verdict">{html.escape(verdict)}</div>
                 <div class="spectrum">
                   <div class="spectrum-track">
                     <div class="spectrum-dot" style="--tint:{colour};left:{pct}%"></div>
                   </div>
                   <div class="spectrum-labels"><span>Oversupplied</span>
                   <span>Underserved</span></div>
                 </div>
               </div>
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
        parts.append("<h2>Opportunity indicators</h2>"
                     "<div class='card'><table class='ind'>")
        for k, v in ind.items():
            parts.append(f"<tr><td class='k'>{html.escape(str(k))}</td><td>{v}</td></tr>")
        parts.append("</table></div>")

    hi = result.get("highlights") or []
    if hi:
        parts.append("<h2>Highlights</h2><div class='card'><ul class='hi'>")
        parts += [f"<li>{html.escape(str(h))}</li>" for h in hi]
        parts.append("</ul></div>")

    parts.append(
        "<div class='note'><b>Reading this dashboard.</b> Opportunity is "
        "derived, not independently measured — it always equals the "
        "demand/supply imbalance, nudged by momentum. The dot on the "
        "spectrum above is that same score, positioned between oversupplied "
        "and underserved. If momentum shows no reliable score, it abstained "
        "rather than guessed from a recent window too thin to trust.</div>")
    parts.append(f"<footer class='gen'>Generated {ts}</footer>")
    parts.append("</div>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
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
