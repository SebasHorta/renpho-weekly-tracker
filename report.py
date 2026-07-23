"""report.py -- renders the weekly summary into a self-contained report.html.

Reads only from the weekly summary produced by analysis.py (which in turn
reads only from SQLite), so this never touches the Renpho API. Output is a
single double-clickable HTML file with an inline SVG weight-trend chart and a
table of recent weeks -- no external files, no server, no dependencies beyond
what analysis.py already uses.
"""

import json

import pandas as pd

from analysis import (
    HOME_TZ,
    dedupe_to_daily,
    load_measurements,
    weekly_summary,
)
from store import get_connection

# How your current training goal colors the weekly weight/BMI deltas. Flip this
# 2-3x/year as you switch phases:
#   "cut"     -> weight/BMI DOWN is green (good), UP is red
#   "bulk"    -> weight/BMI UP is green (good), DOWN is red
#   "neutral" -> no good/bad judgment; deltas shown in a muted neutral color
# Body fat % ignores this and always treats DOWN as good.
GOAL_MODE = "cut"

# Default number of most-recent weeks shown in the chart and table. The report
# page lets you change this at runtime (a number input, persisted in the
# browser's localStorage) -- this constant is only the fallback used the very
# first time the page is opened, before any preference has been saved.
WEEKS_SHOWN = 12
MIN_WEEKS_SELECTABLE = 2
MAX_WEEKS_SELECTABLE = 12

# Palette (from the dataviz skill's validated reference palette). Delta cues use
# the status green/red, always paired with an arrow so meaning never relies on
# color alone.
COLOR_GOOD = "#0ca30c"
COLOR_BAD = "#d03b3b"


def _is_in_progress(week_ending: pd.Timestamp) -> bool:
    """True if this week's Saturday end-date is today or later, i.e. the week hasn't finished being logged."""
    today = pd.Timestamp.now(tz=HOME_TZ).date()
    return week_ending.date() >= today


def _json_safe(value):
    """Convert a pandas/numpy scalar to something json.dumps can serialize, mapping NaN to None."""
    if pd.isna(value):
        return None
    return float(value) if isinstance(value, float) else int(value)


def _weekly_to_records(weekly: pd.DataFrame) -> list[dict]:
    """Serialize the FULL weekly history (not just a recent slice) to plain dicts for embedding as JSON.

    The report page needs every week available client-side so the "weeks
    shown" control can re-slice without re-running Python. Date formatting
    (week range for the chart, full label for the table) and the in-progress
    flag are precomputed here rather than in JS, so the browser never has to
    parse or reason about dates/timezones -- just strings and numbers.
    """
    records = []
    for week_ending, r in weekly.iterrows():
        week_start = week_ending - pd.Timedelta(days=6)
        records.append({
            "week_range_short": f"{week_start.strftime('%-m/%-d')}-{week_ending.strftime('%-m/%-d')}",
            # Used to build the chart's "Week of <year>" axis title -- kept as
            # a plain int so the JS side never has to parse it back out of a
            # formatted string.
            "year": week_ending.year,
            # Full week span for the table -- paired with the "Week of" header,
            # same reasoning as the chart's x-axis: a single end-date next to
            # "Week of" would misleadingly imply the week starts there.
            "week_label": f"{week_start.strftime('%b %-d')} – {week_ending.strftime('%b %-d, %Y')}",
            "in_progress": _is_in_progress(week_ending),
            "readings": int(r["readings"]),
            "flagged_count": int(r["flagged_count"]),
            "weight_lb": _json_safe(r["weight_lb"]),
            "weight_lb_delta": _json_safe(r["weight_lb_delta"]),
            "bodyfat": _json_safe(r["bodyfat"]),
            "bodyfat_delta": _json_safe(r["bodyfat_delta"]),
            "bmi": _json_safe(r["bmi"]),
            "bmi_delta": _json_safe(r["bmi_delta"]),
        })
    return records


def render_report(weekly: pd.DataFrame) -> str:
    """Assemble the full self-contained HTML document (styles + chart + table). Called by write_report."""
    generated = pd.Timestamp.now(tz=HOME_TZ).strftime("%b %-d, %Y %-I:%M %p")
    goal_label = {"cut": "Cutting", "bulk": "Bulking", "neutral": "Maintaining"}[GOAL_MODE]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>RENPHO Weekly Report</title>
<style>
  :root {{
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19;
      --text-primary: #fff; --text-secondary: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
      --series-1: #3987e5;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px; background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  main {{ max-width: 1040px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .sub {{ color: var(--text-secondary); font-size: 0.9rem; margin: 0 0 24px; }}
  .goal {{ font-weight: 600; color: var(--series-1); }}
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px; margin-bottom: 24px;
  }}
  .trend-svg {{ width: 100%; height: auto; display: block; }}
  .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .trend-line {{ stroke: var(--series-1); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
  .marker {{ fill: var(--series-1); }}
  .marker-open {{ fill: var(--surface); stroke: var(--series-1); stroke-width: 2; }}
  .axis-label {{ fill: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
  .axis-unit {{ fill: var(--muted); font-size: 11px; font-style: italic; }}
  .end-label {{ fill: var(--text-secondary); font-size: 13px; font-weight: 600; }}
  .caption {{ color: var(--text-secondary); font-size: 0.85rem; margin: 8px 2px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
  th, td {{ text-align: right; padding: 8px 10px; font-size: 0.9rem; }}
  th:first-child, td:first-child {{ text-align: left; }}
  thead th {{ color: var(--text-secondary); font-weight: 600; border-bottom: 1px solid var(--baseline); }}
  tbody tr {{ border-bottom: 1px solid var(--grid); }}
  tbody tr.partial {{ background: color-mix(in srgb, var(--series-1) 6%, transparent); }}
  .delta {{ font-weight: 600; }}
  .muted {{ color: var(--muted); }}
  .tag {{
    font-size: 0.7rem; font-weight: 600; color: var(--series-1);
    border: 1px solid var(--series-1); border-radius: 999px; padding: 1px 7px; margin-left: 6px;
  }}
  .flagged {{ color: {COLOR_BAD}; font-size: 0.8rem; margin-left: 4px; }}
  .chart-controls {{
    display: flex; justify-content: flex-end; align-items: center;
    gap: 18px; margin-bottom: 4px;
  }}
  .toggle {{
    display: flex; align-items: center; gap: 6px;
    font-size: 0.85rem; color: var(--text-secondary); user-select: none; cursor: pointer;
  }}
  .toggle input {{ accent-color: var(--series-1); }}
  .weeks-control {{
    display: flex; align-items: center; gap: 6px;
    font-size: 0.85rem; color: var(--text-secondary);
  }}
  .weeks-control input {{
    width: 56px; padding: 3px 6px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--page); color: var(--text-primary);
    font: inherit; font-variant-numeric: tabular-nums;
  }}
  .nav-btn {{
    width: 26px; height: 26px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--page); color: var(--text-secondary); font: inherit; font-size: 0.75rem;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
  }}
  .nav-btn:hover:not(:disabled) {{ background: color-mix(in srgb, var(--series-1) 12%, var(--page)); color: var(--series-1); }}
  .nav-btn:disabled {{ opacity: 0.35; cursor: default; }}
  th.sortable {{ cursor: pointer; user-select: none; }}
  th.sortable:hover {{ color: var(--text-primary); }}
  .sort-arrow {{ color: var(--muted); font-size: 0.7em; display: inline-block; margin-left: 2px; }}
  .trend-svg.hide-values .value-label {{ display: none; }}
</style>
</head>
<body>
<main>
  <h1>RENPHO Weekly Report</h1>
  <p class="sub">Generated {generated} · Goal: <span class="goal">{goal_label}</span></p>

  <div class="card">
    <div class="chart-controls">
      <button type="button" id="prior-weeks" class="nav-btn" aria-label="Show prior weeks" title="Show prior weeks">◀</button>
      <label class="weeks-control">
        Weeks shown
        <input type="number" id="weeks-input" min="{MIN_WEEKS_SELECTABLE}" max="{MAX_WEEKS_SELECTABLE}" value="{WEEKS_SHOWN}" />
      </label>
      <button type="button" id="next-weeks" class="nav-btn" aria-label="Show more recent weeks" title="Show more recent weeks">▶</button>
      <label class="toggle">
        <input type="checkbox" id="toggle-values" checked />
        Show values
      </label>
    </div>
    <svg viewBox="0 0 980 320" class="trend-svg" role="img" aria-label="Weekly average weight trend"></svg>
    <p class="caption" id="chart-caption"></p>
    <noscript><p class="caption">Enable JavaScript to render the chart and table -- this page draws them from embedded data client-side.</p></noscript>
  </div>

  <div class="card">
    <table id="weekly-table">
      <thead><tr>
        <th id="th-week-ending" class="sortable" title="Click to change sort order">Week of <span class="sort-arrow" id="sort-arrow">▼</span></th>
        <th>Logs</th>
        <th>Weight</th><th>Δ</th>
        <th>Body fat %</th><th>Δ</th>
        <th>BMI</th><th>Δ</th>
        <th id="th-goal">Goal (lb)</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</main>
<script id="weekly-data" type="application/json">{json.dumps(_weekly_to_records(weekly))}</script>
<script>
(() => {{
  // Full weekly history (oldest -> newest), pre-formatted by report.py so this
  // script never has to parse or reason about dates/timezones -- just numbers
  // and strings. See _weekly_to_records() in report.py for the exact shape.
  const ALL_WEEKS = JSON.parse(document.getElementById("weekly-data").textContent);
  const GOAL_MODE = {json.dumps(GOAL_MODE)};
  const COLOR_GOOD = {json.dumps(COLOR_GOOD)};
  const COLOR_BAD = {json.dumps(COLOR_BAD)};
  const DEFAULT_WEEKS = {WEEKS_SHOWN};
  const MIN_WEEKS = {MIN_WEEKS_SELECTABLE};
  const MAX_WEEKS = Math.min({MAX_WEEKS_SELECTABLE}, ALL_WEEKS.length);
  const STORAGE_KEY = "renpho-weeks-shown";
  const SORT_STORAGE_KEY = "renpho-table-oldest-first";

  // How many weeks back from "now" the current view is paged to. 0 = the
  // most recent window (what loads by default); the prior/next-week buttons
  // move this by whole window-sized pages, non-overlapping, mirroring how
  // the RENPHO app's own week view pages through history. Intentionally NOT
  // persisted -- reloading always starts back at "now" with your last-saved
  // window size, only the size itself is remembered.
  let offset = 0;

  // Table sort direction, toggled by clicking the "Week ending" header (see
  // the click listener below). Default false = newest-first (matches most
  // modern apps' default table sort); true = oldest-first, chronological.
  let oldestFirst = false;

  // Mirrors _goal_direction() in report.py: which delta sign counts as
  // "good" for a metric, given the GOAL_MODE constant set there.
  function goalDirection(metric) {{
    if (metric === "bodyfat") return -1;
    if (metric === "weight_lb" || metric === "bmi") {{
      return {{cut: -1, bulk: 1, neutral: 0}}[GOAL_MODE];
    }}
    return 0;
  }}

  // Target weight for the current in-progress week: 1% off last week's
  // average, in whichever direction GOAL_MODE calls "good" (down for a cut,
  // up for a bulk). Only one week can ever be in-progress -- always the last
  // entry in ALL_WEEKS -- so this is one target, not a per-row calculation.
  // Returns null for GOAL_MODE "neutral" (no direction to target) or if there
  // isn't yet a prior completed week to base it on.
  function computeGoalWeightLb() {{
    if (GOAL_MODE === "neutral" || ALL_WEEKS.length < 2) return null;
    const priorWeight = ALL_WEEKS[ALL_WEEKS.length - 2].weight_lb;
    if (priorWeight === null) return null;
    const factor = GOAL_MODE === "cut" ? 0.99 : 1.01;
    return priorWeight * factor;
  }}

  // Mirrors _nice_axis() in report.py: pad a [lo, hi] range and return evenly
  // spaced tick values, so the trend line never touches the chart edges.
  function niceAxis(lo, hi, ticks = 5) {{
    const span = (hi - lo) || 1.0;
    const pad = span * 0.15;
    lo -= pad; hi += pad;
    const step = (hi - lo) / (ticks - 1);
    return {{lo, hi, ticks: Array.from({{length: ticks}}, (_, i) => lo + step * i)}};
  }}

  // Mirrors build_trend_svg() in report.py: draws the weight-trend line into
  // the <svg> stub already in the page. Called whenever the shown window
  // changes. See report.py's version for why each piece (edge-anchoring,
  // peak/valley label placement, the hollow in-progress marker) works the way
  // it does -- the logic here is a direct port, not a redesign.
  function renderChart(weeks) {{
    const W = 980, H = 320;
    const mLeft = 52, mRight = 40, mTop = 44, mBottom = 56;
    const plotW = W - mLeft - mRight, plotH = H - mTop - mBottom;
    const weights = weeks.map(w => w.weight_lb);
    const {{lo, hi, ticks}} = niceAxis(Math.min(...weights), Math.max(...weights));

    const x = i => weeks.length === 1 ? mLeft + plotW / 2 : mLeft + plotW * i / (weeks.length - 1);
    const y = v => mTop + plotH * (1 - (v - lo) / (hi - lo));

    let gridlines = "", yLabels = "";
    for (const t of ticks) {{
      const yy = y(t);
      gridlines += `<line class="grid" x1="${{mLeft}}" y1="${{yy.toFixed(1)}}" x2="${{W - mRight}}" y2="${{yy.toFixed(1)}}" />`;
      yLabels += `<text class="axis-label" x="${{mLeft - 8}}" y="${{(yy + 4).toFixed(1)}}" text-anchor="end">${{t.toFixed(0)}}</text>`;
    }}
    const yUnitLabel = `<text class="axis-unit" x="${{mLeft}}" y="18" text-anchor="start">lb</text>`;

    let xLabels = "";
    weeks.forEach((w, i) => {{
      const anchor = i === 0 ? "start" : "middle";
      xLabels += `<text class="axis-label" x="${{x(i).toFixed(1)}}" y="${{(H - mBottom + 20).toFixed(1)}}" text-anchor="${{anchor}}">${{w.week_range_short}}</text>`;
    }});
    // Year(s) spanned by the currently visible window -- computed from the
    // data itself, not hardcoded, since paging back through history can land
    // on any year, and a window can straddle a Dec/Jan boundary.
    const years = [...new Set(weeks.map(w => w.year))];
    // Single year: "(2026)". Spanning years: "(2025-26)" -- full first year,
    // abbreviated second year, since a window can straddle a Dec/Jan boundary.
    const yearLabel = years.length === 1
      ? `(${{years[0]}})`
      : `(${{years[0]}}-${{String(years[years.length - 1]).slice(-2)}})`;
    const xAxisTitle = `<text class="axis-unit" x="${{(mLeft + plotW / 2).toFixed(1)}}" y="${{H - 10}}" text-anchor="middle">Week of ${{yearLabel}}</text>`;

    const linePts = weeks.map((w, i) => `${{x(i).toFixed(1)}},${{y(w.weight_lb).toFixed(1)}}`).join(" ");
    const polyline = `<polyline class="trend-line" points="${{linePts}}" fill="none" />`;

    let markers = "", valueLabels = "";
    weeks.forEach((w, i) => {{
      const last = i === weeks.length - 1;
      const cls = (last && w.in_progress) ? "marker-open" : "marker";
      markers += `<circle class="${{cls}}" cx="${{x(i).toFixed(1)}}" cy="${{y(w.weight_lb).toFixed(1)}}" r="4" />`;

      const left = i > 0 ? weeks[i - 1].weight_lb : null;
      const right = i < weeks.length - 1 ? weeks[i + 1].weight_lb : null;
      const neighbors = [left, right].filter(v => v !== null);
      const neighborAvg = neighbors.reduce((a, b) => a + b, 0) / neighbors.length;
      const labelAbove = w.weight_lb >= neighborAvg;
      const labelY = labelAbove ? y(w.weight_lb) - 10 : y(w.weight_lb) + 20;
      const anchor = i === 0 ? "start" : "middle";
      valueLabels += `<text class="end-label value-label" x="${{x(i).toFixed(1)}}" y="${{labelY.toFixed(1)}}" text-anchor="${{anchor}}">${{w.weight_lb.toFixed(1)}}</text>`;
    }});

    const svg = document.querySelector(".trend-svg");
    svg.setAttribute("aria-label", `Weekly average weight trend over the last ${{weeks.length}} weeks`);
    svg.innerHTML = gridlines + yLabels + yUnitLabel + xLabels + xAxisTitle + polyline + markers + valueLabels;
  }}

  // Mirrors _delta_cell() in report.py: a colored, arrowed table cell, with
  // the same "rounds to 0.0 -> neutral" rule so a change too small to matter
  // at display precision isn't shown as a colored +0.0/-0.0.
  function deltaCell(value, metric) {{
    if (value === null) return `<td class="delta"><span class="muted">—</span></td>`;
    if (Math.round(value * 10) / 10 === 0) return `<td class="delta"><span class="muted">0.0</span></td>`;

    const arrow = value > 0 ? "▲" : "▼";
    const goodDir = goalDirection(metric);
    let color = "var(--muted)";
    if (goodDir !== 0) {{
      const movedGood = (value < 0 && goodDir < 0) || (value > 0 && goodDir > 0);
      color = movedGood ? COLOR_GOOD : COLOR_BAD;
    }}
    const sign = value > 0 ? "+" : "";
    return `<td class="delta" style="color:${{color}}">${{arrow}} ${{sign}}${{value.toFixed(1)}}</td>`;
  }}

  // Mirrors _metric_cell() in report.py.
  function metricCell(value) {{
    if (value === null) return `<td><span class="muted">—</span></td>`;
    return `<td>${{value.toFixed(1)}}</td>`;
  }}

  // Mirrors build_table_html() in report.py: rebuilds the <tbody> for the
  // currently shown window.
  function renderTable(weeks) {{
    const goalWeight = computeGoalWeightLb(); // one target; only ever relevant to whichever row is in-progress
    let rows = "";
    for (const w of weeks) {{
      const note = w.in_progress ? ` <span class="tag">in progress</span>` : "";
      const flaggedNote = w.flagged_count
        ? ` <span class="flagged" title="${{w.flagged_count}} outlier reading(s) excluded">⚑${{w.flagged_count}}</span>`
        : "";
      const goalCell = (w.in_progress && goalWeight !== null)
        ? `<td>${{goalWeight.toFixed(1)}}</td>`
        : `<td><span class="muted">—</span></td>`;
      rows += `<tr class="${{w.in_progress ? "partial" : ""}}">`
        + `<td>${{w.week_label}}${{note}}</td>`
        + `<td>${{w.readings}}${{flaggedNote}}</td>`
        + metricCell(w.weight_lb) + deltaCell(w.weight_lb_delta, "weight_lb")
        + metricCell(w.bodyfat) + deltaCell(w.bodyfat_delta, "bodyfat")
        + metricCell(w.bmi) + deltaCell(w.bmi_delta, "bmi")
        + goalCell
        + `</tr>`;
    }}
    document.querySelector("#weekly-table tbody").innerHTML = rows;
  }}

  // Reads the current "weeks shown" value, slices ALL_WEEKS to the window
  // `offset` pages have moved it to, and re-renders the chart, table, nav
  // buttons, and caption -- the single entry point every control funnels
  // through, so the input, the arrows, and page load all stay in sync.
  function render() {{
    const n = Math.min(MAX_WEEKS, Math.max(MIN_WEEKS, parseInt(document.getElementById("weeks-input").value, 10) || DEFAULT_WEEKS));
    document.getElementById("weeks-input").value = n;

    const end = ALL_WEEKS.length - offset;
    const start = Math.max(0, end - n);
    const weeks = ALL_WEEKS.slice(start, end);

    renderChart(weeks); // always oldest -> newest, left to right: reversing a time-series chart would read backwards
    renderTable(oldestFirst ? weeks : [...weeks].reverse());
    document.getElementById("sort-arrow").textContent = oldestFirst ? "▲" : "▼";

    const paged = offset > 0 ? ` (${{start === 0 ? "earliest available" : weeks[0].week_label}} onward)` : "";
    document.getElementById("chart-caption").textContent =
      `Weekly average weight (lb), ${{n}} week${{n === 1 ? "" : "s"}}${{paged}}. A hollow final point marks a week still in progress.`;

    document.getElementById("next-weeks").disabled = offset === 0;
    document.getElementById("prior-weeks").disabled = start === 0;

    localStorage.setItem(STORAGE_KEY, String(n));
  }}

  // GOAL_MODE is a fixed build-time constant (not user-toggleable in the
  // page), so this tooltip only needs setting once, not on every render().
  document.getElementById("th-goal").title = GOAL_MODE === "cut"
    ? "1% below last week's average"
    : GOAL_MODE === "bulk"
      ? "1% above last week's average"
      : "No goal weight while GOAL_MODE is neutral";

  document.getElementById("weeks-input").addEventListener("change", () => {{
    offset = 0; // changing the window size re-anchors the view to "now"
    render();
  }});
  document.getElementById("prior-weeks").addEventListener("click", () => {{
    offset += parseInt(document.getElementById("weeks-input").value, 10) || DEFAULT_WEEKS;
    render();
  }});
  document.getElementById("next-weeks").addEventListener("click", () => {{
    offset = Math.max(0, offset - (parseInt(document.getElementById("weeks-input").value, 10) || DEFAULT_WEEKS));
    render();
  }});
  document.getElementById("toggle-values").addEventListener("change", (e) => {{
    document.querySelector(".trend-svg").classList.toggle("hide-values", !e.target.checked);
  }});
  document.getElementById("th-week-ending").addEventListener("click", () => {{
    oldestFirst = !oldestFirst;
    localStorage.setItem(SORT_STORAGE_KEY, String(oldestFirst));
    render();
  }});

  // On load: restore the saved window size and table sort order (if any and
  // still valid), otherwise fall back to the defaults -- newest-first table,
  // DEFAULT_WEEKS window. This is what makes "reload the page" and "re-run
  // report.py" both keep whatever you last picked. offset (paging position)
  // always starts back at 0 (today), regardless of past paging.
  const stored = parseInt(localStorage.getItem(STORAGE_KEY), 10);
  const initial = Number.isFinite(stored) ? Math.min(MAX_WEEKS, Math.max(MIN_WEEKS, stored)) : DEFAULT_WEEKS;
  document.getElementById("weeks-input").value = initial;
  oldestFirst = localStorage.getItem(SORT_STORAGE_KEY) === "true";
  render();
}})();
</script>
</body>
</html>"""


def write_report(path: str = "report.html") -> str:
    """Load history, compute the weekly summary, render the HTML, and write it to `path`.

    The single entry point for this module (called by run.py in Phase 4, and
    by this file's __main__). Returns the path written.
    """
    conn = get_connection()
    weekly = weekly_summary(dedupe_to_daily(load_measurements(conn)))
    with open(path, "w") as f:
        f.write(render_report(weekly))
    return path


if __name__ == "__main__":
    out = write_report()
    print(f"Wrote {out}")
