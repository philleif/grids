"""Generate the D3 interactive visualization HTML from parsed stream data.

Produces a self-contained HTML file with:
- Left panel: stats, legend, event log, analysis, report tabs
- Center: 2D grid topology / force layout with animated playback
- Right panel: agent group chat
- Bottom: timeline scrubber

The HTML template is fully self-contained (no external dependencies except D3 CDN).
"""

from __future__ import annotations

import json
from pathlib import Path

from grids.analysis.stream_parser import ParsedStream, LLMCall
from grids.analysis.workflow import WorkflowBreakdown
from grids.analysis.narrative import Narrative
from grids.analysis.retrospective import Retrospective
from grids.domain_colors import hex_color


def build_viz_data(parsed: ParsedStream, grid_snapshot: dict) -> list[dict]:
    """Build the NODES data for the D3 visualization from grid-snapshot.json."""
    nodes = []
    cells = grid_snapshot.get("cells", {})
    for pos_str, cell_data in cells.items():
        parts = pos_str.split(",")
        x, y = int(parts[0]), int(parts[1])
        nodes.append({
            "id": pos_str,
            "x": x,
            "y": y,
            "domain": cell_data.get("domain", ""),
            "agent_type": cell_data.get("agent_type", ""),
            "role": cell_data.get("role", ""),
            "state": cell_data.get("state", "idle"),
            "items_processed": cell_data.get("items_processed", 0),
            "llm_calls": cell_data.get("llm_calls", 0),
            "last_output_tick": cell_data.get("last_output_tick", 0),
            "last_output_kind": cell_data.get("last_output_kind", ""),
            "inbox_size": cell_data.get("inbox_size", 0),
        })
    return nodes


def build_timeline_data(parsed: ParsedStream) -> list[dict]:
    """Build the TIMELINE data for the D3 visualization."""
    return [
        {
            "seq": c.seq,
            "domain": c.domain,
            "agent": c.agent,
            "action": c.action,
            "tokens": c.tokens,
        }
        for c in parsed.llm_calls
    ]


def build_chat_data(parsed: ParsedStream) -> list[dict]:
    """Build the CHAT data for the group chat panel."""
    return [c.to_chat_dict() for c in parsed.llm_calls]


def build_ticks_data(parsed: ParsedStream) -> list[dict]:
    """Build the TICKS data for the timeline."""
    return [
        {
            "tick": t.tick,
            "actions": t.actions,
            "llm": t.llm_calls,
            "emitted": t.emitted,
            "elapsed": t.elapsed,
        }
        for t in parsed.ticks
    ]


def generate_html(
    parsed: ParsedStream,
    grid_snapshot: dict,
    workflow: WorkflowBreakdown | None = None,
    narrative: Narrative | None = None,
    retrospective: Retrospective | None = None,
    run_name: str = "GRIDS Run",
) -> str:
    """Generate the full self-contained HTML visualization."""
    nodes = build_viz_data(parsed, grid_snapshot)
    timeline = build_timeline_data(parsed)
    chat = build_chat_data(parsed)
    ticks = build_ticks_data(parsed)

    # Collect domains present in this run
    domains_in_run = list(dict.fromkeys(c.domain for c in parsed.llm_calls))
    domain_colors = {d: hex_color(d) for d in domains_in_run}
    # Add any domains from grid snapshot
    for node in nodes:
        d = node["domain"]
        if d and d not in domain_colors:
            domain_colors[d] = hex_color(d)

    # Grid dimensions
    grid_w = grid_snapshot.get("width", 10)
    grid_h = grid_snapshot.get("height", 8)

    # Critique stats
    critiques = [c for c in parsed.llm_calls if c.score is not None]
    passes = sum(1 for c in critiques if c.verdict == "pass")
    fails = sum(1 for c in critiques if c.verdict in ("fail", "iterate"))

    # Build report tab content if available
    report_html = _build_report_tab(workflow, narrative, retrospective)

    nodes_json = json.dumps(nodes, default=str)
    timeline_json = json.dumps(timeline, default=str)
    chat_json = json.dumps(chat, default=str)
    ticks_json = json.dumps(ticks, default=str)
    colors_json = json.dumps(domain_colors, default=str)

    return _HTML_TEMPLATE.format(
        run_name=_escape_html(run_name),
        grid_size=f"{grid_w}x{grid_h} ({len(nodes)} cells)",
        total_events=len(parsed.llm_calls),
        total_tokens=parsed.total_tokens,
        total_critiques=len(critiques),
        passes=passes,
        fails=fails,
        nodes_json=nodes_json,
        timeline_json=timeline_json,
        chat_json=chat_json,
        ticks_json=ticks_json,
        colors_json=colors_json,
        domain_options="\n".join(
            f'          <option value="{d}">{d}</option>' for d in sorted(domain_colors.keys())
        ),
        report_tab_content=report_html,
    )


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_report_tab(
    workflow: WorkflowBreakdown | None,
    narrative: Narrative | None,
    retrospective: Retrospective | None,
) -> str:
    """Build the HTML content for the Report tab in the left panel."""
    if not any([workflow, narrative, retrospective]):
        return '<div class="analysis-section"><p style="color:#555">No report data. Run grids-report to generate.</p></div>'

    parts = []

    if narrative and narrative.linear_narrative:
        parts.append(
            '<div class="analysis-section">'
            '<h3>Session Narrative</h3>'
            f'<p>{_escape_html(narrative.linear_narrative[:2000])}</p>'
            '</div>'
        )

    if narrative and narrative.highlights:
        highlights_html = "".join(
            f'<div style="margin-bottom:.4rem;padding:.3rem;background:#111;border-radius:3px;">'
            f'<span style="color:#ffd700;font-size:.6rem;">T{h.get("tick", "?")} </span>'
            f'<span style="color:#eee;font-size:.58rem;font-weight:600;">{_escape_html(h.get("title", ""))}</span>'
            f'<p style="font-size:.52rem;color:#999;margin:.15rem 0 0 0;">{_escape_html(h.get("description", "")[:200])}</p>'
            f'</div>'
            for h in narrative.highlights[:8]
        )
        parts.append(
            '<div class="analysis-section">'
            '<h3>Highlights</h3>'
            f'{highlights_html}'
            '</div>'
        )

    if retrospective:
        ca = retrospective.ca_dynamics
        parts.append(
            '<div class="analysis-section">'
            '<h3>CA Dynamics</h3>'
            f'<p><span class="analysis-metric">{ca.get("ca_class", "Unknown")}</span></p>'
            f'<p>Activity ratio: {ca.get("activity_ratio", 0):.0%} | '
            f'Concentration: {ca.get("activity_concentration", "?")}</p>'
            f'<p>{_escape_html(ca.get("assessment", "")[:300])}</p>'
            '</div>'
        )

        parts.append(
            '<div class="analysis-section">'
            f'<h3>Health Score: <span class="analysis-metric">{retrospective.health_score:.0%}</span></h3>'
            '</div>'
        )

        if retrospective.what_worked:
            items = "".join(f"<li>{_escape_html(w[:150])}</li>" for w in retrospective.what_worked[:5])
            parts.append(
                '<div class="analysis-section">'
                f'<h3>What Worked</h3><ul style="font-size:.55rem;color:#aaa;padding-left:1rem;">{items}</ul>'
                '</div>'
            )

        if retrospective.what_didnt:
            items = "".join(f"<li>{_escape_html(w[:150])}</li>" for w in retrospective.what_didnt[:5])
            parts.append(
                '<div class="analysis-section">'
                f'<h3>What Didn\'t Work</h3><ul style="font-size:.55rem;color:#aaa;padding-left:1rem;">{items}</ul>'
                '</div>'
            )

        if retrospective.what_to_try:
            items = "".join(f"<li>{_escape_html(w[:150])}</li>" for w in retrospective.what_to_try[:5])
            parts.append(
                '<div class="analysis-section">'
                f'<h3>Try Next</h3><ul style="font-size:.55rem;color:#aaa;padding-left:1rem;">{items}</ul>'
                '</div>'
            )

    return "\n".join(parts) if parts else '<div class="analysis-section"><p style="color:#555">No report data available.</p></div>'


def save_data_files(
    parsed: ParsedStream,
    grid_snapshot: dict,
    output_dir: str | Path,
) -> None:
    """Save the intermediate data files (_chat_data.json, _viz_data.json)."""
    output_dir = Path(output_dir)

    chat_data = build_chat_data(parsed)
    with open(output_dir / "_chat_data.json", "w", encoding="utf-8") as f:
        json.dump(chat_data, f, indent=2, default=str)

    viz_data = build_viz_data(parsed, grid_snapshot)
    with open(output_dir / "_viz_data.json", "w", encoding="utf-8") as f:
        json.dump(viz_data, f, indent=2, default=str)


# The full HTML template -- self-contained with D3 CDN
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GRIDS Report -- {run_name}</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0a0a; color:#ccc; font-family:'SF Mono','Fira Code',Consolas,monospace; overflow:hidden; }}
#app {{ display:flex; height:100vh; width:100vw; }}
#left-panel {{ width:300px; background:#111; border-right:1px solid #222; display:flex; flex-direction:column; overflow:hidden; flex-shrink:0; }}
#left-panel h1 {{ font-size:.8rem; color:#ffd700; padding:.7rem .8rem; border-bottom:1px solid #222; letter-spacing:.04em; }}
#stats {{ padding:.5rem .8rem; border-bottom:1px solid #222; font-size:.62rem; }}
.stat-row {{ display:flex; justify-content:space-between; margin-bottom:.25rem; }}
.stat-label {{ color:#666; }}
.stat-val {{ color:#ffd700; font-weight:700; }}
#legend {{ padding:.5rem .8rem; border-bottom:1px solid #222; }}
.legend-title {{ font-size:.58rem; color:#555; text-transform:uppercase; margin-bottom:.3rem; letter-spacing:.1em; }}
.legend-item {{ display:flex; align-items:center; gap:.35rem; font-size:.58rem; margin-bottom:.2rem; cursor:pointer; opacity:.85; }}
.legend-item:hover {{ opacity:1; color:#fff; }}
.legend-swatch {{ width:10px; height:10px; border-radius:2px; flex-shrink:0; }}
.shape-legend {{ display:flex; gap:.5rem; flex-wrap:wrap; margin-top:.4rem; }}
.shape-item {{ font-size:.52rem; color:#777; display:flex; align-items:center; gap:.25rem; }}
#tab-bar {{ display:flex; border-bottom:1px solid #222; }}
.tab-btn {{ flex:1; padding:.4rem; text-align:center; font-size:.6rem; color:#555; cursor:pointer; border:none; background:none; font-family:inherit; border-bottom:2px solid transparent; }}
.tab-btn:hover {{ color:#aaa; }}
.tab-btn.active {{ color:#ffd700; border-bottom-color:#ffd700; }}
#event-log, #analysis-panel, #report-panel {{ flex:1; overflow-y:auto; padding:.4rem; font-size:.55rem; display:none; }}
#event-log.visible, #analysis-panel.visible, #report-panel.visible {{ display:block; }}
#event-log::-webkit-scrollbar, #analysis-panel::-webkit-scrollbar, #report-panel::-webkit-scrollbar, #chat-panel::-webkit-scrollbar {{ width:4px; }}
#event-log::-webkit-scrollbar-thumb, #analysis-panel::-webkit-scrollbar-thumb, #report-panel::-webkit-scrollbar-thumb, #chat-panel::-webkit-scrollbar-thumb {{ background:#333; border-radius:2px; }}
.log-entry {{ padding:.2rem .35rem; border-radius:3px; margin-bottom:2px; border-left:2px solid transparent; cursor:pointer; }}
.log-entry:hover {{ background:#1a1a1a; }}
.log-entry.active {{ background:#1a1a1a; border-left-color:#ffd700; }}
.analysis-section {{ margin-bottom:.8rem; padding:.5rem; background:#1a1a1a; border-radius:4px; border:1px solid #222; }}
.analysis-section h3 {{ font-size:.65rem; color:#ffd700; margin-bottom:.3rem; }}
.analysis-section p {{ font-size:.55rem; color:#aaa; line-height:1.5; }}
.analysis-section ul {{ line-height:1.6; }}
.analysis-metric {{ font-size:.7rem; color:#ffd700; font-weight:700; }}
#main {{ flex:1; display:flex; flex-direction:column; min-width:0; }}
#controls {{ height:36px; background:#111; border-bottom:1px solid #222; display:flex; align-items:center; padding:0 .8rem; gap:.6rem; flex-shrink:0; }}
#controls button {{ background:#222; border:1px solid #333; color:#ccc; padding:.15rem .5rem; border-radius:3px; font-family:inherit; font-size:.6rem; cursor:pointer; }}
#controls button:hover {{ background:#333; color:#fff; }}
#controls button.active {{ background:#ffd700; color:#000; border-color:#ffd700; }}
#view-label {{ font-size:.58rem; color:#666; }}
#speed-label {{ font-size:.52rem; color:#555; }}
#graph {{ flex:1; position:relative; }}
#graph svg {{ width:100%; height:100%; }}
#timeline-bar {{ height:70px; background:#111; border-top:1px solid #222; position:relative; flex-shrink:0; }}
#timeline-bar svg {{ width:100%; height:100%; }}
#right-panel {{ width:380px; background:#0d0d0d; border-left:1px solid #222; display:flex; flex-direction:column; overflow:hidden; flex-shrink:0; }}
#chat-header {{ padding:.6rem .8rem; border-bottom:1px solid #222; display:flex; align-items:center; justify-content:space-between; }}
#chat-header h2 {{ font-size:.75rem; color:#ffd700; letter-spacing:.03em; }}
#chat-filter select {{ background:#1a1a1a; border:1px solid #333; color:#ccc; font-family:inherit; font-size:.55rem; padding:.15rem .3rem; border-radius:3px; }}
#chat-panel {{ flex:1; overflow-y:auto; padding:.6rem; }}
.chat-msg {{ margin-bottom:.8rem; padding:.6rem .7rem; border-radius:8px; border:1px solid #1a1a1a; position:relative; transition:all .3s; }}
.chat-msg:hover {{ border-color:#333; }}
.chat-msg.highlight {{ border-color:#ffd700; background:#1a1800; }}
.chat-msg-header {{ display:flex; align-items:center; gap:.4rem; margin-bottom:.35rem; }}
.chat-avatar {{ width:22px; height:22px; border-radius:4px; display:flex; align-items:center; justify-content:center; font-size:.55rem; font-weight:700; color:#000; flex-shrink:0; }}
.chat-agent-name {{ font-size:.62rem; font-weight:700; }}
.chat-domain-tag {{ font-size:.48rem; padding:.1rem .3rem; border-radius:3px; background:#1a1a1a; }}
.chat-action-tag {{ font-size:.48rem; padding:.1rem .3rem; border-radius:3px; }}
.chat-action-tag.critique {{ background:rgba(255,107,107,.15); color:#ff6b6b; }}
.chat-action-tag.process {{ background:rgba(74,158,255,.15); color:#4a9eff; }}
.chat-score-badge {{ font-size:.55rem; font-weight:700; padding:.15rem .4rem; border-radius:4px; margin-left:auto; }}
.chat-score-badge.pass {{ background:rgba(80,250,123,.2); color:#50fa7b; }}
.chat-score-badge.fail {{ background:rgba(255,107,107,.2); color:#ff6b6b; }}
.chat-body {{ font-size:.58rem; line-height:1.6; color:#bbb; max-height:200px; overflow:hidden; position:relative; }}
.chat-body.expanded {{ max-height:none; }}
.chat-body-fade {{ position:absolute; bottom:0; left:0; right:0; height:40px; background:linear-gradient(transparent, #0d0d0d); pointer-events:none; }}
.chat-expand {{ font-size:.5rem; color:#ffd700; cursor:pointer; margin-top:.3rem; text-align:center; }}
.chat-expand:hover {{ color:#fff; }}
.chat-meta {{ display:flex; gap:.5rem; margin-top:.3rem; font-size:.48rem; color:#555; }}
.tooltip {{ position:absolute; pointer-events:none; background:rgba(0,0,0,.92); border:1px solid #333; border-radius:5px; padding:.5rem .7rem; font-size:.58rem; max-width:280px; line-height:1.5; z-index:100; display:none; }}
.tooltip .tt-title {{ color:#ffd700; font-weight:700; font-size:.65rem; margin-bottom:.2rem; }}
.node-label {{ font-size:8px; fill:#666; text-anchor:middle; pointer-events:none; }}
@media (max-width:1200px) {{ #left-panel {{ width:240px; }} #right-panel {{ width:320px; }} }}
</style>
</head>
<body>
<div id="app">
  <div id="left-panel">
    <h1>GRIDS / {run_name}</h1>
    <div id="stats">
      <div class="stat-row"><span class="stat-label">Grid</span><span class="stat-val">{grid_size}</span></div>
      <div class="stat-row"><span class="stat-label">Current Tick</span><span class="stat-val" id="s-tick">--</span></div>
      <div class="stat-row"><span class="stat-label">Events Fired</span><span class="stat-val" id="s-events">0 / {total_events}</span></div>
      <div class="stat-row"><span class="stat-label">Tokens Generated</span><span class="stat-val" id="s-tokens">0</span></div>
      <div class="stat-row"><span class="stat-label">Critiques</span><span class="stat-val" id="s-critiques">0 / {total_critiques}</span></div>
      <div class="stat-row"><span class="stat-label">Pass / Fail</span><span class="stat-val" id="s-passfail">0 / 0</span></div>
    </div>
    <div id="legend"></div>
    <div id="tab-bar">
      <button class="tab-btn active" onclick="switchTab('log')">Log</button>
      <button class="tab-btn" onclick="switchTab('analysis')">Analysis</button>
      <button class="tab-btn" onclick="switchTab('report')">Report</button>
    </div>
    <div id="event-log" class="visible"></div>
    <div id="analysis-panel"></div>
    <div id="report-panel">{report_tab_content}</div>
  </div>
  <div id="main">
    <div id="controls">
      <button id="btn-play" onclick="togglePlay()">&#9654; Play</button>
      <button id="btn-reset" onclick="resetTimeline()">Reset</button>
      <span id="speed-label">1x</span>
      <button onclick="setSpeed(0.5)">0.5x</button>
      <button onclick="setSpeed(1)">1x</button>
      <button onclick="setSpeed(3)">3x</button>
      <span style="color:#333">|</span>
      <span id="view-label">Topology:</span>
      <button id="btn-grid" class="active" onclick="setView('grid')">Grid</button>
      <button id="btn-force" onclick="setView('force')">Force</button>
    </div>
    <div id="graph"></div>
    <div id="timeline-bar"></div>
  </div>
  <div id="right-panel">
    <div id="chat-header">
      <h2>Agent Group Chat</h2>
      <div id="chat-filter">
        <select id="chat-domain-filter" onchange="filterChat()">
          <option value="all">All Domains</option>
{domain_options}
        </select>
      </div>
    </div>
    <div id="chat-panel"></div>
  </div>
</div>
<div class="tooltip" id="tooltip"></div>
<script>
const NODES = {nodes_json};
const TIMELINE = {timeline_json};
const CHAT = {chat_json};
const TICKS = {ticks_json};
const DOMAIN_COLORS = {colors_json};

let playing = false, speed = 1, currentEventIdx = -1, viewMode = 'grid', animFrame = null, lastTime = 0, accumulated = 0;
const EVENT_INTERVAL = 1800;
const graphEl = document.getElementById('graph');
const svg = d3.select('#graph').append('svg');
const gMain = svg.append('g');
const tooltip = document.getElementById('tooltip');

buildLegend(); buildAnalysis(); buildChatPanel(); buildTimeline(); drawGrid();

function switchTab(tab) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('#event-log,#analysis-panel,#report-panel').forEach(p => p.classList.remove('visible'));
  event.target.classList.add('active');
  document.getElementById(tab === 'log' ? 'event-log' : tab === 'analysis' ? 'analysis-panel' : 'report-panel').classList.add('visible');
}}

function buildLegend() {{
  const leg = d3.select('#legend');
  leg.append('div').attr('class','legend-title').text('Domains');
  for (const [domain, color] of Object.entries(DOMAIN_COLORS)) {{
    const item = leg.append('div').attr('class','legend-item');
    item.append('div').attr('class','legend-swatch').style('background', color);
    item.append('span').text(domain);
  }}
  leg.append('div').attr('class','legend-title').style('margin-top','.4rem').text('Shapes');
  const shapes = leg.append('div').attr('class','shape-legend');
  [['master','diamond'],['sub','circle'],['exec','square'],['critique','triangle'],['research','ring']].forEach(([n]) => {{
    shapes.append('div').attr('class','shape-item').text(n);
  }});
}}

function buildAnalysis() {{
  const panel = document.getElementById('analysis-panel');
  const totalTokens = CHAT.reduce((s,c) => s + c.tokens, 0);
  const critiques = CHAT.filter(c => c.score !== null);
  const passes = critiques.filter(c => c.verdict === 'pass');
  const fails = critiques.filter(c => c.verdict === 'fail');
  const avgScore = critiques.length ? (critiques.reduce((s,c) => s + c.score, 0) / critiques.length).toFixed(1) : 0;
  const domainTokens = {{}};
  CHAT.forEach(c => {{ domainTokens[c.domain] = (domainTokens[c.domain] || 0) + c.tokens; }});
  let html = '<div class="analysis-section"><h3>Overview</h3>';
  html += `<p><span class="analysis-metric">${{totalTokens.toLocaleString()}}</span> tokens across <span class="analysis-metric">${{CHAT.length}}</span> events</p>`;
  html += `<p>Critiques: ${{critiques.length}} (${{passes.length}} pass, ${{fails.length}} fail) | Avg score: ${{avgScore}}</p></div>`;
  html += '<div class="analysis-section"><h3>Domain Breakdown</h3>';
  for (const [d, t] of Object.entries(domainTokens).sort((a,b) => b[1]-a[1])) {{
    const pct = (t/totalTokens*100).toFixed(0);
    html += `<p style="color:${{DOMAIN_COLORS[d] || '#888'}}">${{d}}: ${{t.toLocaleString()}} tokens (${{pct}}%)</p>`;
  }}
  html += '</div>';
  panel.innerHTML = html;
}}

function buildChatPanel() {{
  const panel = document.getElementById('chat-panel');
  CHAT.forEach((c, i) => {{
    const color = DOMAIN_COLORS[c.domain] || '#888';
    const initial = c.agent[0].toUpperCase();
    const scoreHtml = c.score !== null ? `<span class="chat-score-badge ${{c.verdict === 'pass' ? 'pass' : 'fail'}}">${{c.score}}</span>` : '';
    const actionClass = c.action === 'critique' ? 'critique' : 'process';
    const body = (c.chat_summary || '').replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>').replace(/\\n/g, '<br>');
    panel.innerHTML += `<div class="chat-msg" data-seq="${{c.seq}}" data-domain="${{c.domain}}">
      <div class="chat-msg-header">
        <div class="chat-avatar" style="background:${{color}}">${{initial}}</div>
        <span class="chat-agent-name" style="color:${{color}}">${{c.agent}}</span>
        <span class="chat-domain-tag">${{c.domain}}</span>
        <span class="chat-action-tag ${{actionClass}}">${{c.action}}</span>
        ${{scoreHtml}}
      </div>
      <div class="chat-body">${{body}}<div class="chat-body-fade"></div></div>
      <div class="chat-expand" onclick="this.previousElementSibling.classList.toggle('expanded');this.textContent=this.previousElementSibling.classList.contains('expanded')?'collapse':'expand'">expand</div>
      <div class="chat-meta"><span>${{c.tokens}} tokens</span><span>${{c.response_chars}} chars</span></div>
    </div>`;
  }});
}}

function filterChat() {{
  const domain = document.getElementById('chat-domain-filter').value;
  document.querySelectorAll('.chat-msg').forEach(m => {{
    m.style.display = (domain === 'all' || m.dataset.domain === domain) ? '' : 'none';
  }});
}}

function highlightChatMsg(seq) {{
  document.querySelectorAll('.chat-msg').forEach(m => m.classList.remove('highlight'));
  const el = document.querySelector(`.chat-msg[data-seq="${{seq}}"]`);
  if (el) {{ el.classList.add('highlight'); el.scrollIntoView({{behavior:'smooth',block:'center'}}); }}
}}

function buildTimeline() {{
  const bar = d3.select('#timeline-bar');
  const tSvg = bar.select('svg').empty() ? bar.append('svg') : bar.select('svg');
  const w = graphEl.offsetWidth || 800, h = 70;
  tSvg.attr('viewBox', `0 0 ${{w}} ${{h}}`);
  if (!TIMELINE.length) return;
  const xScale = d3.scaleLinear().domain([0, TIMELINE.length - 1]).range([30, w - 10]);
  TIMELINE.forEach((e, i) => {{
    const barH = Math.min(40, Math.max(5, e.tokens / 80));
    tSvg.append('rect').attr('x', xScale(i) - 3).attr('y', h - 10 - barH).attr('width', 6).attr('height', barH)
      .attr('fill', DOMAIN_COLORS[e.domain] || '#888').attr('opacity', 0.6).attr('rx', 1)
      .style('cursor','pointer').on('click', () => {{ currentEventIdx = i; fireEvent(i); }});
  }});
  tSvg.append('line').attr('id','playhead').attr('x1',30).attr('y1',5).attr('x2',30).attr('y2',h-5)
    .attr('stroke','#ffd700').attr('stroke-width',1.5).attr('opacity',0);
}}

function updatePlayhead(idx) {{
  const w = graphEl.offsetWidth || 800;
  const x = d3.scaleLinear().domain([0, TIMELINE.length - 1]).range([30, w - 10])(idx);
  d3.select('#playhead').attr('x1',x).attr('x2',x).attr('opacity',1);
}}

function drawGrid() {{
  gMain.selectAll('*').remove();
  const w = graphEl.offsetWidth || 600, h = graphEl.offsetHeight || 400;
  const maxX = d3.max(NODES, n => n.x) || 1, maxY = d3.max(NODES, n => n.y) || 1;
  const cellW = w / (maxX + 2), cellH = h / (maxY + 2);
  const r = Math.min(cellW, cellH) * 0.35;
  NODES.forEach(n => {{
    const cx = (n.x + 1) * cellW, cy = (n.y + 1) * cellH;
    const color = DOMAIN_COLORS[n.domain] || '#888';
    const g = gMain.append('g').attr('transform', `translate(${{cx}},${{cy}})`).attr('data-id', n.id)
      .style('cursor','pointer')
      .on('mouseover', (ev) => showTooltip(ev, n)).on('mouseout', hideTooltip);
    if (n.role === 'master') g.append('polygon').attr('points', `0,${{-r}} ${{r}},0 0,${{r}} ${{-r}},0`).attr('fill', color).attr('opacity', .8);
    else if (n.role === 'critique') g.append('polygon').attr('points', `0,${{-r}} ${{r*.9}},${{r*.7}} ${{-r*.9}},${{r*.7}}`).attr('fill', color).attr('opacity', .8);
    else if (n.role === 'execution') g.append('rect').attr('x',-r*.7).attr('y',-r*.7).attr('width',r*1.4).attr('height',r*1.4).attr('rx',3).attr('fill',color).attr('opacity',.8);
    else if (n.role === 'research') g.append('circle').attr('r', r*.7).attr('fill','none').attr('stroke',color).attr('stroke-width',2);
    else g.append('circle').attr('r', r*.7).attr('fill', color).attr('opacity', .8);
    g.append('text').attr('class','node-label').attr('y', r + 10).text(n.agent_type.length > 10 ? n.agent_type.slice(0,9)+'...' : n.agent_type);
    n._cx = cx; n._cy = cy;
  }});
}}

function showTooltip(ev, n) {{
  const t = document.getElementById('tooltip');
  t.innerHTML = `<div class="tt-title">${{n.agent_type}}</div><div class="tt-domain" style="color:${{DOMAIN_COLORS[n.domain]||'#888'}}">${{n.domain}} / ${{n.role}}</div><div class="tt-stat">LLM calls: ${{n.llm_calls}} | Processed: ${{n.items_processed}}</div><div class="tt-stat">State: ${{n.state}} | Inbox: ${{n.inbox_size}}</div>`;
  t.style.display = 'block'; t.style.left = (ev.pageX + 12) + 'px'; t.style.top = (ev.pageY - 10) + 'px';
}}
function hideTooltip() {{ document.getElementById('tooltip').style.display = 'none'; }}

function setView(mode) {{
  viewMode = mode;
  document.getElementById('btn-grid').classList.toggle('active', mode==='grid');
  document.getElementById('btn-force').classList.toggle('active', mode==='force');
  if (mode === 'grid') drawGrid();
  else drawForce();
}}

function drawForce() {{
  gMain.selectAll('*').remove();
  const w = graphEl.offsetWidth || 600, h = graphEl.offsetHeight || 400;
  const sim = d3.forceSimulation(NODES)
    .force('charge', d3.forceManyBody().strength(-50))
    .force('center', d3.forceCenter(w/2, h/2))
    .force('collision', d3.forceCollide(20));
  sim.on('tick', () => {{
    gMain.selectAll('g.fnode').data(NODES).join('g').attr('class','fnode')
      .attr('transform', d => `translate(${{d.x_||w/2}},${{d.y_||h/2}})`)
      .each(function(d) {{
        const g = d3.select(this);
        if (g.selectAll('*').empty()) {{
          const color = DOMAIN_COLORS[d.domain]||'#888';
          g.append('circle').attr('r',8).attr('fill',color).attr('opacity',.8);
          g.append('text').attr('class','node-label').attr('y',14).text(d.agent_type.slice(0,8));
        }}
      }});
    NODES.forEach(d => {{ d.x_ = d.x; d.y_ = d.y; }});
  }});
  setTimeout(() => sim.stop(), 3000);
}}

function togglePlay() {{
  playing = !playing;
  document.getElementById('btn-play').textContent = playing ? '\\u23F8 Pause' : '\\u25B6 Play';
  if (playing) {{ lastTime = performance.now(); requestAnimationFrame(animate); }}
}}
function resetTimeline() {{ playing = false; currentEventIdx = -1; accumulated = 0; document.getElementById('btn-play').textContent = '\\u25B6 Play'; d3.select('#playhead').attr('opacity',0); document.querySelectorAll('.chat-msg').forEach(m => m.classList.remove('highlight')); }}
function setSpeed(s) {{ speed = s; document.getElementById('speed-label').textContent = s + 'x'; }}

let eventsFired = 0, tokensSoFar = 0, critiquesSoFar = 0, passesSoFar = 0, failsSoFar = 0;
function fireEvent(idx) {{
  if (idx < 0 || idx >= TIMELINE.length) return;
  const e = TIMELINE[idx];
  const chatMsg = CHAT[idx];
  eventsFired = idx + 1;
  tokensSoFar += e.tokens;
  if (chatMsg && chatMsg.score !== null) {{ critiquesSoFar++; if (chatMsg.verdict==='pass') passesSoFar++; else failsSoFar++; }}
  document.getElementById('s-events').textContent = `${{eventsFired}} / ${{TIMELINE.length}}`;
  document.getElementById('s-tokens').textContent = tokensSoFar.toLocaleString();
  document.getElementById('s-critiques').textContent = `${{critiquesSoFar}} / ${{CHAT.filter(c=>c.score!==null).length}}`;
  document.getElementById('s-passfail').textContent = `${{passesSoFar}} / ${{failsSoFar}}`;
  const node = NODES.find(n => n.id === (chatMsg ? chatMsg.pos : ''));
  if (node) {{
    const color = DOMAIN_COLORS[e.domain]||'#888';
    const g = gMain.select(`g[data-id="${{node.id}}"]`);
    if (!g.empty()) {{
      g.append('circle').attr('r',5).attr('fill','none').attr('stroke',color).attr('stroke-width',3)
        .transition().duration(800).attr('r',30).attr('stroke-width',0).attr('opacity',0).remove();
    }}
  }}
  if (chatMsg) highlightChatMsg(chatMsg.seq);
  updatePlayhead(idx);
}}

function animate() {{
  if (!playing) return;
  const now = performance.now(), dt = now - lastTime; lastTime = now; accumulated += dt * speed;
  if (accumulated >= EVENT_INTERVAL) {{
    accumulated -= EVENT_INTERVAL; currentEventIdx++;
    if (currentEventIdx >= TIMELINE.length) {{ playing = false; document.getElementById('btn-play').textContent = '\\u25B6 Play'; return; }}
    fireEvent(currentEventIdx);
  }}
  requestAnimationFrame(animate);
}}

// Log panel
(function() {{
  const log = document.getElementById('event-log');
  TIMELINE.forEach((e, i) => {{
    const c = CHAT[i];
    const color = DOMAIN_COLORS[e.domain]||'#888';
    const scoreHtml = c && c.score !== null ? `<span class="score ${{c.verdict==='pass'?'score-pass':'score-fail'}}">${{c.score}}</span>` : '';
    log.innerHTML += `<div class="log-entry" onclick="currentEventIdx=${{i}};fireEvent(${{i}})"><span class="time">T${{c?c.seq:'?'}}</span> <span class="agent" style="color:${{color}}">${{e.domain}}/${{e.agent}}</span> <span class="action-${{e.action}}">${{e.action}}</span> ${{scoreHtml}} <span style="color:#555">${{e.tokens}}tk</span></div>`;
  }});
}})();

window.addEventListener('resize', () => {{ if (viewMode === 'grid') drawGrid(); }});
</script>
</body>
</html>"""
