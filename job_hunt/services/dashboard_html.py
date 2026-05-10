"""Generate a self-contained HTML dashboard from `data/applications.md`.

The output is a single HTML file that loads Chart.js from a CDN and renders KPIs,
three charts, and a filterable, sortable table. No build step, no server.
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from pathlib import Path

from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Hunt — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 24px;
  }
  h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
  .subtitle { color: #64748b; font-size: 0.85rem; margin-bottom: 24px; }
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .kpi { background: #1e2130; border-radius: 10px; padding: 16px; border: 1px solid #2d3148; }
  .kpi-label { font-size: 0.72rem; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
  .kpi-value { font-size: 2rem; font-weight: 700; line-height: 1; }
  .kpi-value.applied { color: #38bdf8; }
  .kpi-value.evaluated { color: #fbbf24; }
  .kpi-value.discarded { color: #6b7280; }
  .kpi-value.avg { color: #a78bfa; }
  .charts-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 24px; }
  .chart-card { background: #1e2130; border-radius: 10px; padding: 20px; border: 1px solid #2d3148; }
  .chart-card h3 { font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 200px; }
  .table-card { background: #1e2130; border-radius: 10px; padding: 20px; border: 1px solid #2d3148; }
  .table-card h3 { font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 12px; }
  .filter-row { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
  .filter-row input, .filter-row select { background: #0f1117; border: 1px solid #2d3148; color: #e2e8f0; border-radius: 6px; padding: 6px 10px; font-size: 0.82rem; }
  .filter-row input { flex: 1; min-width: 200px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; color: #64748b; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #2d3148; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .04em; cursor: pointer; user-select: none; }
  th:hover { color: #94a3b8; }
  td { padding: 8px 10px; border-bottom: 1px solid #1a1d2e; vertical-align: middle; }
  tr:hover td { background: #252840; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 600; }
  .badge-applied { background: #0c4a6e; color: #38bdf8; }
  .badge-evaluated { background: #451a03; color: #fbbf24; }
  .badge-discarded { background: #1f2937; color: #9ca3af; }
  .badge-other { background: #2d1b69; color: #a78bfa; }
  .score-bar { display: flex; align-items: center; gap: 6px; }
  .score-bg { flex: 1; height: 4px; background: #2d3148; border-radius: 2px; max-width: 60px; }
  .score-fill { height: 4px; border-radius: 2px; }
  .score-text { font-weight: 600; min-width: 24px; }
  @media (max-width: 900px) { .charts-row { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<h1>Job Hunt — Dashboard</h1>
<p class="subtitle">Updated __TODAY__ · __COUNT__ applications</p>

<div class="kpi-grid" id="kpis"></div>

<div class="charts-row">
  <div class="chart-card">
    <h3>Status distribution</h3>
    <div class="chart-wrap"><canvas id="statusChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>Score histogram</h3>
    <div class="chart-wrap"><canvas id="scoreChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>By date</h3>
    <div class="chart-wrap"><canvas id="timelineChart"></canvas></div>
  </div>
</div>

<div class="table-card">
  <h3>All applications</h3>
  <div class="filter-row">
    <input type="text" id="search" placeholder="Search company, role…">
    <select id="filterStatus">
      <option value="">All statuses</option>
      <option value="Applied">Applied</option>
      <option value="Evaluated">Evaluated</option>
      <option value="Responded">Responded</option>
      <option value="Interview">Interview</option>
      <option value="Offer">Offer</option>
      <option value="Rejected">Rejected</option>
      <option value="Discarded">Discarded</option>
      <option value="SKIP">SKIP</option>
    </select>
    <select id="filterScore">
      <option value="">All scores</option>
      <option value="4">≥ 4.0</option>
      <option value="3">3.0–3.9</option>
      <option value="0">&lt; 3.0</option>
    </select>
  </div>
  <table id="appTable">
    <thead>
      <tr>
        <th onclick="sortTable('num')">#</th>
        <th onclick="sortTable('date')">Date</th>
        <th onclick="sortTable('company')">Company</th>
        <th>Role</th>
        <th onclick="sortTable('score')">Score</th>
        <th onclick="sortTable('status')">Status</th>
        <th>PDF</th>
      </tr>
    </thead>
    <tbody id="tableBody"></tbody>
  </table>
</div>

<script>
const raw = __DATA_JSON__;

const applied = raw.filter(r => r.status === "Applied");
const evaluated = raw.filter(r => r.status === "Evaluated");
const discarded = raw.filter(r => r.status === "Discarded" || r.status === "SKIP");
const avgApplied = applied.length ? (applied.reduce((s,r)=>s+r.score,0)/applied.length).toFixed(2) : "—";

document.getElementById("kpis").innerHTML = `
  <div class="kpi"><div class="kpi-label">Total</div><div class="kpi-value" style="color:#e2e8f0">${raw.length}</div></div>
  <div class="kpi"><div class="kpi-label">Applied</div><div class="kpi-value applied">${applied.length}</div></div>
  <div class="kpi"><div class="kpi-label">Pending decision</div><div class="kpi-value evaluated">${evaluated.length}</div></div>
  <div class="kpi"><div class="kpi-label">Discarded / SKIP</div><div class="kpi-value discarded">${discarded.length}</div></div>
  <div class="kpi"><div class="kpi-label">Avg score (applied)</div><div class="kpi-value avg">${avgApplied}</div></div>
`;

const statusCounts = {};
for (const r of raw) statusCounts[r.status] = (statusCounts[r.status] || 0) + 1;
const statusLabels = Object.keys(statusCounts);
const statusData = statusLabels.map(s => statusCounts[s]);
const statusColors = { Applied:"#0369a1", Evaluated:"#92400e", Discarded:"#374151", SKIP:"#374151", Interview:"#065f46", Offer:"#4c1d95", Rejected:"#7f1d1d", Responded:"#155e75" };
const statusBorders = { Applied:"#38bdf8", Evaluated:"#fbbf24", Discarded:"#6b7280", SKIP:"#6b7280", Interview:"#34d399", Offer:"#a78bfa", Rejected:"#f87171", Responded:"#22d3ee" };

if (raw.length > 0) {
  new Chart(document.getElementById("statusChart"), {
    type: "doughnut",
    data: {
      labels: statusLabels,
      datasets: [{
        data: statusData,
        backgroundColor: statusLabels.map(s => statusColors[s] || "#1f2937"),
        borderColor: statusLabels.map(s => statusBorders[s] || "#6b7280"),
        borderWidth: 2,
      }]
    },
    options: { plugins: { legend: { labels: { color: "#94a3b8", boxWidth: 12 } } }, cutout: "65%" }
  });

  const bins = [[1,2],[2,3],[3,3.5],[3.5,4],[4,4.5],[4.5,5.1]];
  const binLabels = ["1–2","2–3","3–3.5","3.5–4","4–4.5","4.5+"];
  const binCounts = bins.map(([lo,hi]) => raw.filter(r=>r.score>=lo&&r.score<hi).length);
  const binColors = ["#374151","#374151","#78350f","#92400e","#0c4a6e","#0369a1"];
  const binBorders = ["#6b7280","#6b7280","#fbbf24","#fbbf24","#38bdf8","#38bdf8"];

  new Chart(document.getElementById("scoreChart"), {
    type: "bar",
    data: { labels: binLabels, datasets: [{ label:"Count", data:binCounts, backgroundColor:binColors, borderColor:binBorders, borderWidth:1, borderRadius:4 }] },
    options: { plugins:{ legend:{ display:false } }, scales:{ x:{ticks:{color:"#64748b"},grid:{color:"#1f2937"}}, y:{ticks:{color:"#64748b",stepSize:1},grid:{color:"#1f2937"},beginAtZero:true} } }
  });

  const dateMap = {};
  for (const r of raw) {
    dateMap[r.date] = dateMap[r.date] || {total:0,applied:0};
    dateMap[r.date].total++;
    if (r.status==="Applied") dateMap[r.date].applied++;
  }
  const dates = Object.keys(dateMap).sort();
  new Chart(document.getElementById("timelineChart"), {
    type: "bar",
    data: {
      labels: dates.map(d=>d.slice(5)),
      datasets: [
        { label:"All", data:dates.map(d=>dateMap[d].total), backgroundColor:"#1e3a5f", borderColor:"#38bdf8", borderWidth:1, borderRadius:3 },
        { label:"Applied", data:dates.map(d=>dateMap[d].applied), backgroundColor:"#0369a1", borderColor:"#38bdf8", borderWidth:1, borderRadius:3 },
      ]
    },
    options: { plugins:{ legend:{ labels:{ color:"#94a3b8", boxWidth:12 } } }, scales:{ x:{ticks:{color:"#64748b"},grid:{color:"#1f2937"}}, y:{ticks:{color:"#64748b",stepSize:2},grid:{color:"#1f2937"},beginAtZero:true} } }
  });
}

let sortKey = "num", sortDir = -1;

function scoreColor(s) {
  if (s >= 4.0) return "#38bdf8";
  if (s >= 3.5) return "#fbbf24";
  if (s >= 3.0) return "#f97316";
  return "#6b7280";
}

function badgeClass(s) {
  if (s === "Applied") return "badge-applied";
  if (s === "Evaluated") return "badge-evaluated";
  if (s === "Discarded" || s === "SKIP") return "badge-discarded";
  return "badge-other";
}

function renderTable() {
  const q = document.getElementById("search").value.toLowerCase();
  const st = document.getElementById("filterStatus").value;
  const sc = document.getElementById("filterScore").value;

  let filtered = raw.filter(r => {
    const matchQ = !q || r.company.toLowerCase().includes(q) || r.role.toLowerCase().includes(q);
    const matchSt = !st || r.status === st;
    const matchSc = !sc || (sc==="4"&&r.score>=4) || (sc==="3"&&r.score>=3&&r.score<4) || (sc==="0"&&r.score<3);
    return matchQ && matchSt && matchSc;
  });

  filtered.sort((a,b) => {
    let va = a[sortKey], vb = b[sortKey];
    if (typeof va === "string") { va = va.toLowerCase(); vb = vb.toLowerCase(); }
    return va < vb ? sortDir : va > vb ? -sortDir : 0;
  });

  document.getElementById("tableBody").innerHTML = filtered.map(r => {
    const col = scoreColor(r.score);
    const fillW = Math.round((r.score/5)*100);
    return `<tr>
      <td style="color:#64748b;font-size:0.75rem">${String(r.num).padStart(3,"0")}</td>
      <td style="color:#64748b;font-size:0.75rem;white-space:nowrap">${r.date.slice(5)}</td>
      <td style="font-weight:600">${r.company}</td>
      <td style="color:#94a3b8;max-width:260px">${r.role}</td>
      <td><div class="score-bar"><div class="score-bg"><div class="score-fill" style="width:${fillW}%;background:${col}"></div></div><span class="score-text" style="color:${col}">${r.score}</span></div></td>
      <td><span class="badge ${badgeClass(r.status)}">${r.status}</span></td>
      <td style="text-align:center">${r.pdf?"✅":"❌"}</td>
    </tr>`;
  }).join("");
}

function sortTable(key) {
  if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = -1; }
  renderTable();
}

document.getElementById("search").addEventListener("input", renderTable);
document.getElementById("filterStatus").addEventListener("change", renderTable);
document.getElementById("filterScore").addEventListener("change", renderTable);
renderTable();
</script>
</body>
</html>
"""


def _entry_to_dict(entry: TrackerEntry) -> dict:
    """Reduce TrackerEntry to the JSON shape the dashboard expects."""
    score_match = re.search(r"([\d.]+)", entry.score.replace("**", ""))
    score = float(score_match.group(1)) if score_match else 0.0
    status = entry.status.replace("**", "").strip()
    return {
        "num": entry.number,
        "date": entry.date,
        "company": entry.company,
        "role": entry.role,
        "score": score,
        "status": status,
        "pdf": "✅" in entry.pdf,
        "report": entry.report,
    }


def generate(
    apps_path: Path | None = None,
    output_path: Path | None = None,
) -> int:
    """Render the dashboard HTML to ``output_path``. Returns the entry count."""
    apps = apps_path or Path("data/applications.md")
    out = output_path or Path("data/dashboard.html")

    repo = TrackerRepository(apps)
    entries = repo.parse()
    payload = [_entry_to_dict(e) for e in entries]
    payload.sort(key=lambda r: r["num"], reverse=True)

    html = _HTML_TEMPLATE.replace(
        "__DATA_JSON__", json.dumps(payload, ensure_ascii=False)
    ).replace(
        "__TODAY__", date_cls.today().isoformat()
    ).replace(
        "__COUNT__", str(len(payload))
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return len(payload)
