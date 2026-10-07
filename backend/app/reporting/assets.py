"""Static CSS/JS embedded into every report.

Both are plain Python string constants on purpose: the PyInstaller spec's ``datas``
list is an explicit whitelist, so shipping these as data files would mean editing
the spec for all three platforms and risking a "works in dev, TemplateNotFound when
frozen" failure. Constants ship automatically via ``collect_submodules("app")``.

Neither constant may contain the substring ``</`` — they are inlined into <style>
and <script> elements respectively.
"""

from __future__ import annotations

REPORT_CSS = """
:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --border: #e4e7ec;
  --text: #1a1d23;
  --text-muted: #667085;
  --accent: #2563eb;
  --up: #0f9d58;
  --down: #d93025;
  --flat: #98a2b3;
  --radius: 10px;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.1);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a;
    --surface: #1c1f24;
    --border: #2c313a;
    --text: #e8eaed;
    --text-muted: #9aa4b2;
    --accent: #6ea8fe;
    --up: #4ade80;
    --down: #f87171;
    --flat: #6b7280;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  padding: 0 0 48px;
  background: var(--bg);
  color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.6;
}
.wrap { max-width: 1040px; margin: 0 auto; padding: 0 20px; }
header.report-head {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 28px 0 22px;
  margin-bottom: 24px;
}
header.report-head h1 { margin: 0 0 6px; font-size: 22px; line-height: 1.35; }
header.report-head .subtitle { color: var(--text-muted); margin: 0 0 10px; }
header.report-head .meta {
  color: var(--text-muted);
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
}
.block { margin: 0 0 26px; }
.block > h2 { font-size: 17px; margin: 0 0 10px; }
.block > h3 { font-size: 15px; margin: 0 0 10px; color: var(--text-muted); }
.note { color: var(--text-muted); font-size: 12px; margin: 8px 0 0; }
hr { border: 0; border-top: 1px solid var(--border); margin: 24px 0; }
p { margin: 0 0 10px; }
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
}
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 14px 16px;
}
.kpi .kpi-label { color: var(--text-muted); font-size: 12px; }
.kpi .kpi-value { font-size: 22px; font-weight: 600; margin: 2px 0 0; }
.kpi .kpi-delta { font-size: 12px; font-weight: 600; }
.kpi .kpi-hint { color: var(--text-muted); font-size: 11px; }
.tone-up { color: var(--up); }
.tone-down { color: var(--down); }
.tone-flat { color: var(--flat); }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.table-tools { display: none; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.has-js .table-tools { display: block; }
.table-tools input {
  width: 100%;
  max-width: 280px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font: inherit;
  font-size: 13px;
}
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td {
  padding: 9px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface);
  color: var(--text-muted);
  font-weight: 600;
  font-size: 12px;
  user-select: none;
}
.has-js thead th.sortable { cursor: pointer; }
.has-js thead th.sortable:hover { color: var(--accent); }
th.sort-asc::after { content: " \\25B2"; font-size: 9px; }
th.sort-desc::after { content: " \\25BC"; font-size: 9px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: color-mix(in srgb, var(--accent) 6%, transparent); }
.chart-card { padding: 14px 16px 8px; }
.chart-card svg { display: block; width: 100%; height: auto; }
.chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  padding: 6px 2px 12px;
  font-size: 12px;
  color: var(--text-muted);
}
.chart-legend .swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 6px;
  vertical-align: -1px;
}
.axis-line { stroke: var(--border); stroke-width: 1; }
.axis-label { fill: var(--text-muted); font-size: 10px; }
footer.report-foot {
  margin-top: 32px;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}
""".strip()


# Progressive enhancement only: sorting and filtering operate on the server-rendered
# <table> DOM, using data-v attributes for correct numeric ordering. With JS disabled
# the report is still fully readable, which is the point of a shareable HTML file.
REPORT_JS = """
(function () {
  'use strict';
  document.documentElement.classList.add('has-js');

  var dataEl = document.getElementById('staffdeck-report-data');
  var meta = {};
  if (dataEl) {
    try { meta = JSON.parse(dataEl.textContent || '{}') || {}; } catch (err) { meta = {}; }
  }
  var tables = meta.tables || {};

  function toNumber(raw) {
    if (raw === null || raw === undefined) { return null; }
    var text = String(raw).trim();
    if (!text) { return null; }
    var value = Number(text);
    return isNaN(value) ? null : value;
  }

  function compareRows(a, b, colIndex) {
    var ac = a.cells[colIndex];
    var bc = b.cells[colIndex];
    var av = ac ? ac.getAttribute('data-v') : null;
    var bv = bc ? bc.getAttribute('data-v') : null;
    var an = toNumber(av);
    var bn = toNumber(bv);
    if (an !== null && bn !== null) { return an - bn; }
    var as = av === null ? '' : String(av);
    var bs = bv === null ? '' : String(bv);
    return as.localeCompare(bs, 'zh-Hans-CN');
  }

  function sortTable(table, colIndex, ascending) {
    var tbody = table.tBodies[0];
    if (!tbody) { return; }
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var result = compareRows(a, b, colIndex);
      return ascending ? result : -result;
    });
    for (var i = 0; i < rows.length; i += 1) { tbody.appendChild(rows[i]); }
  }

  function clearSortState(table) {
    var heads = table.querySelectorAll('th[data-col]');
    for (var i = 0; i < heads.length; i += 1) {
      heads[i].removeAttribute('data-sort');
      heads[i].classList.remove('sort-asc', 'sort-desc');
    }
  }

  var heads = document.querySelectorAll('th[data-col]');
  for (var h = 0; h < heads.length; h += 1) {
    (function (th) {
      var table = th.closest ? th.closest('table') : null;
      if (!table) { return; }
      var tableId = table.getAttribute('data-report-table');
      var info = tables[tableId] || {};
      if (info.sortable === false) { return; }
      th.classList.add('sortable');
      th.setAttribute('role', 'button');
      th.setAttribute('tabindex', '0');
      var toggle = function () {
        var colIndex = parseInt(th.getAttribute('data-col'), 10);
        var ascending = th.getAttribute('data-sort') !== 'asc';
        clearSortState(table);
        th.setAttribute('data-sort', ascending ? 'asc' : 'desc');
        th.classList.add(ascending ? 'sort-asc' : 'sort-desc');
        sortTable(table, colIndex, ascending);
      };
      th.addEventListener('click', toggle);
      th.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      });
    })(heads[h]);
  }

  var filters = document.querySelectorAll('[data-report-filter]');
  for (var f = 0; f < filters.length; f += 1) {
    (function (input) {
      var tableId = input.getAttribute('data-report-filter');
      var table = document.querySelector('[data-report-table="' + tableId + '"]');
      if (!table) { return; }
      var tbody = table.tBodies[0];
      if (!tbody) { return; }
      input.addEventListener('input', function () {
        var needle = input.value.trim().toLowerCase();
        for (var i = 0; i < tbody.rows.length; i += 1) {
          var row = tbody.rows[i];
          var haystack = (row.textContent || '').toLowerCase();
          row.hidden = needle !== '' && haystack.indexOf(needle) === -1;
        }
      });
    })(filters[f]);
  }
})();
""".strip()
