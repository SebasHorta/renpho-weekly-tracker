(() => {
  // Full weekly history (oldest -> newest), pre-formatted by report.py so this
  // script never has to parse or reason about dates/timezones -- just numbers
  // and strings. See _weekly_to_records() in report.py for the exact shape.
  const ALL_WEEKS = JSON.parse(document.getElementById("weekly-data").textContent);
  // Every deduped daily reading (oldest -> newest), used only when "Weeks
  // shown" is 1 -- see _daily_to_records() in report.py.
  const ALL_DAYS = JSON.parse(document.getElementById("daily-data").textContent);
  // Per-run config report.py injects as a plain global before this script
  // loads (see template.html) -- the only bridge between Python and this
  // static file, so nothing here needs string interpolation of its own.
  const CONFIG = window.__RENPHO_CONFIG__;
  const GOAL_MODE = CONFIG.goalMode;
  const COLOR_GOOD = CONFIG.colorGood;
  const COLOR_BAD = CONFIG.colorBad;
  const DEFAULT_WEEKS = CONFIG.defaultWeeks;
  const MIN_WEEKS = CONFIG.minWeeks;
  const MAX_WEEKS = Math.min(CONFIG.maxWeeks, ALL_WEEKS.length);
  const STORAGE_KEY = "renpho-weeks-shown";
  const SORT_STORAGE_KEY = "renpho-table-oldest-first";

  // Baseline for the target-trend line/column, i.e. "when did the current
  // cut/bulk actually start." report.py's GOAL_ANCHOR_WEEK_ENDING (or its
  // placeholder fallback) is only the *default* -- used the very first time
  // the page loads, before you've picked a real start date. Once you pick
  // one via the "Start date" control, that choice lives in localStorage and
  // wins from then on (same pattern as "weeks shown"), so this is `let`, not
  // `const`: resolveAnchor() reassigns it whenever the date input changes.
  const DEFAULT_ANCHOR_ISO = CONFIG.defaultAnchorIso;
  const ANCHOR_STORAGE_KEY = "renpho-goal-anchor-week-ending";
  // Weekly compounding factor toward the goal: 1% down for a cut, 1% up for
  // a bulk. Same rate as the single-week Goal column, just applied
  // repeatedly from the anchor instead of once from last week.
  const GOAL_FACTOR = GOAL_MODE === "cut" ? 0.99 : GOAL_MODE === "bulk" ? 1.01 : null;

  let anchorIndex = -1;
  let anchorWeight = null;

  // Point the target trend at a specific week (by its week_ending_iso), or
  // null to disable it entirely. Called on load (with the saved or default
  // anchor) and whenever the "Start date" input changes.
  function resolveAnchor(weekEndingIso) {
    if (weekEndingIso === null) { anchorIndex = -1; anchorWeight = null; return; }
    anchorIndex = ALL_WEEKS.findIndex(w => w.week_ending_iso === weekEndingIso);
    anchorWeight = anchorIndex >= 0 ? ALL_WEEKS[anchorIndex].weight_lb : null;
  }

  // Expected weight at absolute index i into ALL_WEEKS, per the target
  // trend -- null before the anchor week or if the anchor/goal isn't set.
  function expectedWeightAt(i) {
    if (anchorIndex < 0 || anchorWeight === null || GOAL_FACTOR === null || i < anchorIndex) return null;
    return anchorWeight * Math.pow(GOAL_FACTOR, i - anchorIndex);
  }

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
  function goalDirection(metric) {
    if (metric === "bodyfat") return -1;
    if (metric === "weight_lb" || metric === "bmi") {
      return {cut: -1, bulk: 1, neutral: 0}[GOAL_MODE];
    }
    return 0;
  }

  // Target weight for the current in-progress week: 1% off last week's
  // average, in whichever direction GOAL_MODE calls "good" (down for a cut,
  // up for a bulk). Only one week can ever be in-progress -- always the last
  // entry in ALL_WEEKS -- so this is one target, not a per-row calculation.
  // Returns null for GOAL_MODE "neutral" (no direction to target) or if there
  // isn't yet a prior completed week to base it on.
  function computeGoalWeightLb() {
    if (GOAL_FACTOR === null || ALL_WEEKS.length < 2) return null;
    const priorWeight = ALL_WEEKS[ALL_WEEKS.length - 2].weight_lb;
    if (priorWeight === null) return null;
    return priorWeight * GOAL_FACTOR;
  }

  // Mirrors _nice_axis() in report.py: pad a [lo, hi] range and return evenly
  // spaced tick values, so the trend line never touches the chart edges.
  function niceAxis(lo, hi, ticks = 5) {
    const span = (hi - lo) || 1.0;
    const pad = span * 0.15;
    lo -= pad; hi += pad;
    const step = (hi - lo) / (ticks - 1);
    return {lo, hi, ticks: Array.from({length: ticks}, (_, i) => lo + step * i)};
  }

  // Mirrors build_trend_svg() in report.py: draws the weight-trend line into
  // the <svg> stub already in the page. Called whenever the shown window
  // changes. See report.py's version for why each piece (edge-anchoring,
  // peak/valley label placement, the hollow in-progress marker) works the way
  // it does -- the logic here is a direct port, not a redesign.
  function renderChart(weeks, startIndex, showTrend) {
    const W = 980, H = 320;
    const mLeft = 52, mRight = 40, mTop = 44, mBottom = 56;
    const plotW = W - mLeft - mRight, plotH = H - mTop - mBottom;
    const weights = weeks.map(w => w.weight_lb);
    // Expected (target-trend) value per point, aligned index-for-index with
    // `weeks` -- null wherever expectedWeightAt() has nothing to show (before
    // the anchor week, or the feature's off). Included in the axis range
    // whenever shown, so the dashed line never clips off the top/bottom.
    const expected = showTrend ? weeks.map((w, i) => expectedWeightAt(startIndex + i)) : weeks.map(() => null);
    const rangeValues = weights.concat(expected.filter(v => v !== null));
    const {lo, hi, ticks} = niceAxis(Math.min(...rangeValues), Math.max(...rangeValues));

    const x = i => weeks.length === 1 ? mLeft + plotW / 2 : mLeft + plotW * i / (weeks.length - 1);
    const y = v => mTop + plotH * (1 - (v - lo) / (hi - lo));

    let gridlines = "", yLabels = "";
    for (const t of ticks) {
      const yy = y(t);
      gridlines += `<line class="grid" x1="${mLeft}" y1="${yy.toFixed(1)}" x2="${W - mRight}" y2="${yy.toFixed(1)}" />`;
      yLabels += `<text class="axis-label" x="${mLeft - 8}" y="${(yy + 4).toFixed(1)}" text-anchor="end">${t.toFixed(0)}</text>`;
    }
    const yUnitLabel = `<text class="axis-unit" x="${mLeft}" y="18" text-anchor="start">lb</text>`;

    let xLabels = "";
    weeks.forEach((w, i) => {
      const anchor = i === 0 ? "start" : "middle";
      xLabels += `<text class="axis-label" x="${x(i).toFixed(1)}" y="${(H - mBottom + 20).toFixed(1)}" text-anchor="${anchor}">${w.week_range_short}</text>`;
    });
    // Year(s) spanned by the currently visible window -- computed from the
    // data itself, not hardcoded, since paging back through history can land
    // on any year, and a window can straddle a Dec/Jan boundary.
    const years = [...new Set(weeks.map(w => w.year))];
    // Single year: "(2026)". Spanning years: "(2025-26)" -- full first year,
    // abbreviated second year, since a window can straddle a Dec/Jan boundary.
    const yearLabel = years.length === 1
      ? `(${years[0]})`
      : `(${years[0]}-${String(years[years.length - 1]).slice(-2)})`;
    const xAxisTitle = `<text class="axis-unit" x="${(mLeft + plotW / 2).toFixed(1)}" y="${H - 10}" text-anchor="middle">Week of ${yearLabel}</text>`;

    const linePts = weeks.map((w, i) => `${x(i).toFixed(1)},${y(w.weight_lb).toFixed(1)}`).join(" ");
    const polyline = `<polyline class="trend-line" points="${linePts}" fill="none" />`;

    // Target-trend line: dashed (not just a different color) so it reads
    // distinctly even without relying on hue alone, and drawn as separate
    // contiguous segments rather than one polyline, since `expected` can
    // start partway through the window (null before the anchor week).
    let targetPolylines = "";
    if (showTrend) {
      let segment = [];
      const flushSegment = () => {
        if (segment.length > 1) targetPolylines += `<polyline class="target-line" points="${segment.join(" ")}" fill="none" />`;
        segment = [];
      };
      weeks.forEach((w, i) => {
        if (expected[i] === null) { flushSegment(); return; }
        segment.push(`${x(i).toFixed(1)},${y(expected[i]).toFixed(1)}`);
      });
      flushSegment();
    }

    document.getElementById("chart-legend").hidden = !showTrend;

    let markers = "", valueLabels = "";
    weeks.forEach((w, i) => {
      const last = i === weeks.length - 1;
      const cls = (last && w.in_progress) ? "marker-open" : "marker";
      markers += `<circle class="${cls}" cx="${x(i).toFixed(1)}" cy="${y(w.weight_lb).toFixed(1)}" r="4" />`;

      const left = i > 0 ? weeks[i - 1].weight_lb : null;
      const right = i < weeks.length - 1 ? weeks[i + 1].weight_lb : null;
      const neighbors = [left, right].filter(v => v !== null);
      const neighborAvg = neighbors.reduce((a, b) => a + b, 0) / neighbors.length;
      const labelAbove = w.weight_lb >= neighborAvg;
      const labelY = labelAbove ? y(w.weight_lb) - 10 : y(w.weight_lb) + 20;
      const anchor = i === 0 ? "start" : "middle";
      valueLabels += `<text class="end-label value-label" x="${x(i).toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="${anchor}">${w.weight_lb.toFixed(1)}</text>`;
    });

    const svg = document.querySelector(".trend-svg");
    svg.setAttribute("aria-label", `Weekly average weight trend over the last ${weeks.length} weeks`);
    svg.innerHTML = gridlines + yLabels + yUnitLabel + xLabels + xAxisTitle + targetPolylines + polyline + markers + valueLabels;
  }

  // Used instead of renderChart() when "Weeks shown" is 1: a single weekly
  // average is just one flat dot, not a trend, so this plots that week's
  // individual daily readings instead. Structurally a twin of renderChart --
  // same margins/axis/label logic -- just with days as the x-axis instead of
  // weeks, and one flat target value (that week's expected average) instead
  // of a per-point target line, since a weekly target has nothing finer to
  // compare each day against.
  function renderDailyChart(days, weekRecord, showTrend) {
    const W = 980, H = 320;
    const mLeft = 52, mRight = 40, mTop = 44, mBottom = 56;
    const plotW = W - mLeft - mRight, plotH = H - mTop - mBottom;
    const weights = days.map(d => d.weight_lb);

    const weekIndex = ALL_WEEKS.findIndex(w => w.week_ending_iso === weekRecord.week_ending_iso);
    const target = showTrend ? expectedWeightAt(weekIndex) : null;
    const rangeValues = target !== null ? weights.concat([target]) : weights;
    const {lo, hi, ticks} = niceAxis(Math.min(...rangeValues), Math.max(...rangeValues));

    const x = i => days.length === 1 ? mLeft + plotW / 2 : mLeft + plotW * i / (days.length - 1);
    const y = v => mTop + plotH * (1 - (v - lo) / (hi - lo));

    let gridlines = "", yLabels = "";
    for (const t of ticks) {
      const yy = y(t);
      gridlines += `<line class="grid" x1="${mLeft}" y1="${yy.toFixed(1)}" x2="${W - mRight}" y2="${yy.toFixed(1)}" />`;
      yLabels += `<text class="axis-label" x="${mLeft - 8}" y="${(yy + 4).toFixed(1)}" text-anchor="end">${t.toFixed(0)}</text>`;
    }
    const yUnitLabel = `<text class="axis-unit" x="${mLeft}" y="18" text-anchor="start">lb</text>`;

    let xLabels = "";
    days.forEach((d, i) => {
      const anchor = i === 0 ? "start" : "middle";
      xLabels += `<text class="axis-label" x="${x(i).toFixed(1)}" y="${(H - mBottom + 20).toFixed(1)}" text-anchor="${anchor}">${d.day_label}</text>`;
    });
    const xAxisTitle = `<text class="axis-unit" x="${(mLeft + plotW / 2).toFixed(1)}" y="${H - 10}" text-anchor="middle">${weekRecord.week_label}</text>`;

    const linePts = days.map((d, i) => `${x(i).toFixed(1)},${y(d.weight_lb).toFixed(1)}`).join(" ");
    const polyline = `<polyline class="trend-line" points="${linePts}" fill="none" />`;

    // One flat dashed line at the week's expected average, spanning the full
    // width -- shows what your daily readings should be wobbling around.
    const targetLine = target !== null
      ? `<polyline class="target-line" points="${mLeft},${y(target).toFixed(1)} ${W - mRight},${y(target).toFixed(1)}" fill="none" />`
      : "";
    document.getElementById("chart-legend").hidden = !(target !== null);

    let markers = "", valueLabels = "";
    days.forEach((d, i) => {
      markers += `<circle class="marker" cx="${x(i).toFixed(1)}" cy="${y(d.weight_lb).toFixed(1)}" r="4" />`;

      const left = i > 0 ? days[i - 1].weight_lb : null;
      const right = i < days.length - 1 ? days[i + 1].weight_lb : null;
      const neighbors = [left, right].filter(v => v !== null);
      const neighborAvg = neighbors.length ? neighbors.reduce((a, b) => a + b, 0) / neighbors.length : d.weight_lb;
      const labelAbove = d.weight_lb >= neighborAvg;
      const labelY = labelAbove ? y(d.weight_lb) - 10 : y(d.weight_lb) + 20;
      const anchor = i === 0 ? "start" : "middle";
      valueLabels += `<text class="end-label value-label" x="${x(i).toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="${anchor}">${d.weight_lb.toFixed(1)}</text>`;
    });

    const svg = document.querySelector(".trend-svg");
    svg.setAttribute("aria-label", `Daily weight readings for ${weekRecord.week_label}`);
    svg.innerHTML = gridlines + yLabels + yUnitLabel + xLabels + xAxisTitle + targetLine + polyline + markers + valueLabels;
  }

  // Mirrors _delta_cell() in report.py: a colored, arrowed table cell, with
  // the same "rounds to 0.0 -> neutral" rule so a change too small to matter
  // at display precision isn't shown as a colored +0.0/-0.0.
  function deltaCell(value, metric) {
    if (value === null) return `<td class="delta"><span class="muted">—</span></td>`;
    if (Math.round(value * 10) / 10 === 0) return `<td class="delta"><span class="muted">0.0</span></td>`;

    const arrow = value > 0 ? "▲" : "▼";
    const goodDir = goalDirection(metric);
    let color = "var(--muted)";
    if (goodDir !== 0) {
      const movedGood = (value < 0 && goodDir < 0) || (value > 0 && goodDir > 0);
      color = movedGood ? COLOR_GOOD : COLOR_BAD;
    }
    const sign = value > 0 ? "+" : "";
    return `<td class="delta" style="color:${color}">${arrow} ${sign}${value.toFixed(1)}</td>`;
  }

  // Mirrors _metric_cell() in report.py.
  function metricCell(value) {
    if (value === null) return `<td><span class="muted">—</span></td>`;
    return `<td>${value.toFixed(1)}</td>`;
  }

  // A small ✓/✗ next to the Goal (lb) value, direction-aware: for a cut,
  // "met" means the actual average is at or below the target; for a bulk,
  // at or above. Empty string for GOAL_MODE "neutral" (no direction to judge).
  function goalMetBadge(actual, target) {
    const goodDir = goalDirection("weight_lb");
    if (goodDir === 0) return "";
    const met = goodDir < 0 ? actual <= target : actual >= target;
    return met ? ` <span style="color:${COLOR_GOOD}">✓</span>` : ` <span style="color:${COLOR_BAD}">✗</span>`;
  }

  // Actual minus expected (from the target trend, see expectedWeightAt), for
  // the "vs Target" column. Same coloring rule as deltaCell (direction-aware,
  // rounds-to-zero is neutral) but no arrow -- this is a distance from a
  // target, not a direction of movement.
  function deviationCell(actual, expected) {
    if (actual === null || expected === null) return `<td><span class="muted">—</span></td>`;
    const diff = actual - expected;
    if (Math.round(diff * 10) / 10 === 0) return `<td><span class="muted">0.0</span></td>`;

    const goodDir = goalDirection("weight_lb");
    let color = "var(--muted)";
    if (goodDir !== 0) {
      const isGood = (diff < 0 && goodDir < 0) || (diff > 0 && goodDir > 0);
      color = isGood ? COLOR_GOOD : COLOR_BAD;
    }
    const sign = diff > 0 ? "+" : "";
    return `<td style="color:${color}">${sign}${diff.toFixed(1)}</td>`;
  }

  // Mirrors build_table_html() in report.py: rebuilds the <tbody> for the
  // currently shown window. startIndex is weeks[0]'s absolute position in
  // ALL_WEEKS, needed so the "vs Target" column can look up each row's
  // expected value regardless of which slice is currently shown. Rows are
  // always built chronologically first (so that index math is correct), then
  // reversed as a last step if the table's sorted newest-first -- reversing
  // the *rendered rows* rather than the input `weeks` array, since reversing
  // the input first would break the startIndex + i lookup.
  function renderTable(weeks, startIndex, oldestFirstFlag) {
    const goalWeight = computeGoalWeightLb(); // one target; only ever relevant to whichever row is in-progress
    const rowsHtml = weeks.map((w, i) => {
      const note = w.in_progress ? ` <span class="tag">in progress</span>` : "";
      const flaggedNote = w.flagged_count
        ? ` <span class="flagged" title="${w.flagged_count} outlier reading(s) excluded">⚑${w.flagged_count}</span>`
        : "";
      const goalCell = (w.in_progress && goalWeight !== null)
        ? `<td>${goalWeight.toFixed(1)}${goalMetBadge(w.weight_lb, goalWeight)}</td>`
        : `<td><span class="muted">—</span></td>`;
      const deviation = deviationCell(w.weight_lb, expectedWeightAt(startIndex + i));
      return `<tr class="${w.in_progress ? "partial" : ""}">`
        + `<td>${w.week_label}${note}</td>`
        + `<td>${w.readings}${flaggedNote}</td>`
        + metricCell(w.weight_lb) + deltaCell(w.weight_lb_delta, "weight_lb")
        + metricCell(w.bodyfat) + deltaCell(w.bodyfat_delta, "bodyfat")
        + metricCell(w.bmi) + deltaCell(w.bmi_delta, "bmi")
        + goalCell
        + deviation
        + `</tr>`;
    });
    const ordered = oldestFirstFlag ? rowsHtml : [...rowsHtml].reverse();
    document.querySelector("#weekly-table tbody").innerHTML = ordered.join("");
  }

  // Reads the current "weeks shown" value, slices ALL_WEEKS to the window
  // `offset` pages have moved it to, and re-renders the chart, table, nav
  // buttons, and caption -- the single entry point every control funnels
  // through, so the input, the arrows, and page load all stay in sync.
  function render() {
    const n = Math.min(MAX_WEEKS, Math.max(MIN_WEEKS, parseInt(document.getElementById("weeks-input").value, 10) || DEFAULT_WEEKS));
    document.getElementById("weeks-input").value = n;

    const end = ALL_WEEKS.length - offset;
    const start = Math.max(0, end - n);
    const weeks = ALL_WEEKS.slice(start, end);
    const showTrend = document.getElementById("toggle-trend").checked;

    if (n === 1) {
      // One week selected: a single weekly-average dot isn't a "trend," so
      // plot that week's individual daily readings instead. The table still
      // shows the normal one-row weekly summary -- Goal/Target/deltas are
      // inherently weekly concepts, nothing daily to switch them to.
      const weekRecord = weeks[0];
      const days = ALL_DAYS.filter(d => d.week_ending_iso === weekRecord.week_ending_iso && d.weight_lb !== null);
      renderDailyChart(days, weekRecord, showTrend);
      document.getElementById("chart-caption").textContent =
        `Daily weight (lb) for the week of ${weekRecord.week_label}.`;
    } else {
      renderChart(weeks, start, showTrend); // always oldest -> newest, left to right: reversing a time-series chart would read backwards
      const paged = offset > 0 ? ` (${start === 0 ? "earliest available" : weeks[0].week_label} onward)` : "";
      document.getElementById("chart-caption").textContent =
        `Weekly average weight (lb), ${n} weeks${paged}. A hollow final point marks a week still in progress.`;
    }

    renderTable(weeks, start, oldestFirst);
    document.getElementById("sort-arrow").textContent = oldestFirst ? "▲" : "▼";

    document.getElementById("next-weeks").disabled = offset === 0;
    document.getElementById("prior-weeks").disabled = start === 0;

    localStorage.setItem(STORAGE_KEY, String(n));
  }

  // GOAL_MODE is a fixed build-time constant (not user-toggleable in the
  // page), so this tooltip only needs setting once, not on every render().
  document.getElementById("th-goal").title = GOAL_MODE === "cut"
    ? "1% below last week's average"
    : GOAL_MODE === "bulk"
      ? "1% above last week's average"
      : "No goal weight while GOAL_MODE is neutral";
  // Reflects the current anchor into both tooltips and the date input's
  // displayed value. Called on load and whenever "Start date" changes.
  function updateAnchorUI() {
    const tooltip = anchorIndex >= 0
      ? `1%/week from ${ALL_WEEKS[anchorIndex].week_label} (${anchorWeight.toFixed(1)} lb) -- pick a different "Start date" to change`
      : `Pick a "Start date" above to enable`;
    document.getElementById("th-deviation").title = tooltip;
    document.getElementById("toggle-trend").closest(".toggle").title = tooltip;
    document.getElementById("anchor-date-input").value = anchorIndex >= 0 ? ALL_WEEKS[anchorIndex].week_ending_iso : "";
  }

  document.getElementById("weeks-input").addEventListener("change", () => {
    offset = 0; // changing the window size re-anchors the view to "now"
    render();
  });
  document.getElementById("prior-weeks").addEventListener("click", () => {
    offset += parseInt(document.getElementById("weeks-input").value, 10) || DEFAULT_WEEKS;
    render();
  });
  document.getElementById("next-weeks").addEventListener("click", () => {
    offset = Math.max(0, offset - (parseInt(document.getElementById("weeks-input").value, 10) || DEFAULT_WEEKS));
    render();
  });
  document.getElementById("toggle-values").addEventListener("change", (e) => {
    document.querySelector(".trend-svg").classList.toggle("hide-values", !e.target.checked);
  });
  document.getElementById("toggle-trend").addEventListener("change", render);
  document.getElementById("th-week-ending").addEventListener("click", () => {
    oldestFirst = !oldestFirst;
    localStorage.setItem(SORT_STORAGE_KEY, String(oldestFirst));
    render();
  });
  // Weeks run Sun-Sat, so a picked date almost never lands exactly on a
  // week-ending Saturday -- snap it to the week that CONTAINS that date
  // (the first week whose end is on/after it), then reflect the actual
  // week-ending date back into the input so it's clear what got selected.
  document.getElementById("anchor-date-input").addEventListener("change", (e) => {
    const match = ALL_WEEKS.find(w => w.week_ending_iso >= e.target.value);
    if (!match) return;
    localStorage.setItem(ANCHOR_STORAGE_KEY, match.week_ending_iso);
    resolveAnchor(match.week_ending_iso);
    updateAnchorUI();
    render();
  });

  // On load: restore the saved window size, table sort order, and goal
  // anchor (if any and still valid), otherwise fall back to the defaults --
  // newest-first table, DEFAULT_WEEKS window, DEFAULT_ANCHOR_ISO. This is
  // what makes "reload the page" and "re-run report.py" both keep whatever
  // you last picked. offset (paging position) always starts back at 0
  // (today), regardless of past paging.
  const stored = parseInt(localStorage.getItem(STORAGE_KEY), 10);
  const initial = Number.isFinite(stored) ? Math.min(MAX_WEEKS, Math.max(MIN_WEEKS, stored)) : DEFAULT_WEEKS;
  document.getElementById("weeks-input").value = initial;
  oldestFirst = localStorage.getItem(SORT_STORAGE_KEY) === "true";

  const savedAnchor = localStorage.getItem(ANCHOR_STORAGE_KEY);
  const anchorToUse = (savedAnchor && ALL_WEEKS.some(w => w.week_ending_iso === savedAnchor)) ? savedAnchor : DEFAULT_ANCHOR_ISO;
  if (GOAL_FACTOR === null) {
    document.getElementById("anchor-date-input").disabled = true;
    document.getElementById("toggle-trend").disabled = true;
  } else {
    resolveAnchor(anchorToUse);
  }
  updateAnchorUI();
  render();
})();
