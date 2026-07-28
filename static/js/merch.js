
var DATA = [], sortKey = "margin", sortAsc = false, CAP = null, FLOOR = null;
var ES = null, STATUS = null, LOADING = false;
var VISIBLE_ROWS = [];
var SEL_INDEX = -1;   // index into VISIBLE_ROWS of the selected row, -1 = none

var ALL_COLS = [
  { key: "limit", label: "Buy limit" },
  { key: "vol_day", label: "Vol/day" }, { key: "spark", label: "30d chart" },
  { key: "risk_level", label: "Risk" }
];
var WATCH = (function(){
  try { return JSON.parse(localStorage.getItem("merchdesk.watch") || "[]"); } catch (e) { return []; }
})();
function saveWatch(){ try { localStorage.setItem("merchdesk.watch", JSON.stringify(WATCH)); } catch(e){} }
var ANALYSIS_NOTE = "";

var FILTERS = loadFilters();

function $(id) { return document.getElementById(id); }
function isMobile() {
  return window.ClanShell ? ClanShell.isMobile() : window.matchMedia("(max-width: 860px)").matches;
}


function gp(n) {
  if (n === null || n === undefined) return "-";
  var a = Math.abs(n), s = n < 0 ? "-" : "";
  if (a >= 1e9) return s + (a/1e9).toFixed(2) + "b";
  if (a >= 1e6) return s + (a/1e6).toFixed(2) + "m";
  if (a >= 1e3) return s + (a/1e3).toFixed(1) + "k";
  return s + Math.round(a);
}
function esc(t) {
  return (t == null ? "" : String(t)).replace(/[&<>"]/g, function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];
  });
}
function fmtInt(n) { return Math.round(n||0).toLocaleString(); }
function scoreGrade(sc) {
  if (sc >= 80) return { cls: "grade-high", label: "S" };
  if (sc >= 60) return { cls: "grade-mid", label: "A" };
  return { cls: "grade-low", label: "B" };
}
function ago(secs) {
  if (secs === null || secs === undefined) return "never";
  if (secs < 60) return Math.round(secs) + "s ago";
  if (secs < 3600) return Math.round(secs/60) + " min ago";
  return (secs/3600).toFixed(1) + "h ago";
}

function chartColor(up) {
  var cs = getComputedStyle(document.documentElement);
  return (up ? cs.getPropertyValue("--gain") : cs.getPropertyValue("--loss")).trim() || (up ? "#2FBF71" : "#E06A6A");
}

/* compact line spark for the 30d table column */
function sparkline(arr, w, h) {
  w = w || 70; h = h || 20;
  if (!arr || arr.length < 2) return '<span class="muted">&mdash;</span>';
  var pad = 1.5, n = arr.length;
  var min = Math.min.apply(null, arr), max = Math.max.apply(null, arr);
  var rng = (max - min) || 1;
  function xy(v, i) {
    var x = pad + (i / (n - 1)) * (w - 2 * pad);
    var y = pad + (1 - (v - min) / rng) * (h - 2 * pad);
    return [x, y];
  }
  var pts = arr.map(function(v, i){ var p = xy(v, i); return p[0].toFixed(1) + "," + p[1].toFixed(1); });
  var up = arr[n - 1] >= arr[0];
  var col = chartColor(up);
  var last = xy(arr[n - 1], n - 1);
  var area = "0," + h + " " + pts.join(" ") + " " + w + "," + h;
  return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">'
    + '<polyline points="' + area + '" fill="' + col + '" opacity="0.12" stroke="none"/>'
    + '<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + col
      + '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
    + '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="1.6" fill="' + col + '"/>'
    + '</svg>';
}

/* line + dot chart with labeled X/Y axes, used in the drill-down panel */
function lineChart(vals, xlabels, w, h, yfmt) {
  w = w || 280; h = h || 72;
  if (!vals || vals.length < 2) return '<span class="ax-nodata">not enough data</span>';
  yfmt = yfmt || function(v){ return String(Math.round(v)); };
  var n = vals.length;
  var lm = 40, bm = 16, tm = 8, rm = 8;
  var pw = Math.max(1, w - lm - rm), ph = Math.max(1, h - tm - bm);
  var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  if (min === max) { min -= 1; max += 1; }
  var rng = max - min;
  function xAt(i) { return lm + (n === 1 ? 0 : (i / (n - 1)) * pw); }
  function yAt(v) { return tm + (1 - (v - min) / rng) * ph; }
  var up = vals[n - 1] >= vals[0];
  var col = chartColor(up);
  var pts = vals.map(function(v, i){ return xAt(i).toFixed(1) + "," + yAt(v).toFixed(1); }).join(" ");
  var dots = vals.map(function(v, i){
    return '<circle cx="' + xAt(i).toFixed(1) + '" cy="' + yAt(v).toFixed(1) + '" r="1.7" style="fill:' + col + '"/>';
  }).join("");
  var axis = '<line x1="' + lm + '" y1="' + tm + '" x2="' + lm + '" y2="' + (tm + ph) + '" style="stroke:var(--line2);stroke-width:1"/>'
    + '<line x1="' + lm + '" y1="' + (tm + ph) + '" x2="' + (lm + pw) + '" y2="' + (tm + ph) + '" style="stroke:var(--line2);stroke-width:1"/>';
  var ylabels = '<text x="' + (lm - 4) + '" y="' + (tm + 3) + '" text-anchor="end" font-size="8" style="fill:var(--faint)">' + esc(yfmt(max)) + '</text>'
    + '<text x="' + (lm - 4) + '" y="' + (tm + ph) + '" text-anchor="end" font-size="8" style="fill:var(--faint)">' + esc(yfmt(min)) + '</text>';
  var idxs = n < 3 ? [0, n - 1] : [0, Math.round((n - 1) / 2), n - 1];
  var xTicks = idxs.map(function(i){
    var anchor = i === 0 ? "start" : (i === n - 1 ? "end" : "middle");
    return '<text x="' + xAt(i).toFixed(1) + '" y="' + (h - 2) + '" text-anchor="' + anchor
      + '" font-size="8" style="fill:var(--faint)">' + esc((xlabels && xlabels[i]) || "") + '</text>';
  }).join("");
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="xMidYMid meet">'
    + axis + ylabels + xTicks
    + '<polyline points="' + pts + '" fill="none" style="stroke:' + col + ';stroke-width:1.5"/>'
    + dots
    + '</svg>';
}

function sparkDayLabels(arr) {
  var n = (arr || []).length;
  var out = [];
  for (var i = 0; i < n; i++) {
    var daysAgo = n - 1 - i;
    out.push(daysAgo === 0 ? "today" : "-" + daysAgo + "d");
  }
  return out;
}

/* -------------------------------------------------------------- prefs */

function loadFilters() {
  var d = { q: "", mode: "any", trends: [], minScore: 0, watchOnly: false,
            cols: { limit: true, vol_day: true, spark: true,
                    risk_level: true } };
  try {
    var raw = localStorage.getItem("merchdesk.filters");
    if (raw) { var saved = JSON.parse(raw); for (var k in saved) d[k] = saved[k]; }
  } catch (e) {}
  return d;
}
function saveFilters() {
  try { localStorage.setItem("merchdesk.filters", JSON.stringify(FILTERS)); } catch (e) {}
}
function loadPrefs() {
  try {
    var cap = localStorage.getItem("merch.capital");
    var flr = localStorage.getItem("merch.floor");
    if (cap) { $("capital").value = cap; if ($("mobile-capital")) $("mobile-capital").value = cap; }
    if (flr) { $("floor").value = flr; if ($("mobile-floor")) $("mobile-floor").value = flr; }
  } catch (e) {}
}
function syncCapFloorInputs(fromMobile) {
  if (fromMobile) {
    if ($("mobile-capital")) $("capital").value = $("mobile-capital").value;
    if ($("mobile-floor")) $("floor").value = $("mobile-floor").value;
  } else {
    if ($("mobile-capital")) $("mobile-capital").value = $("capital").value;
    if ($("mobile-floor")) $("mobile-floor").value = $("floor").value;
  }
}
function savePrefs() {
  try {
    localStorage.setItem("merch.capital", $("capital").value.trim());
    localStorage.setItem("merch.floor", $("floor").value.trim());
  } catch (e) {}
}

/* -------------------------------------------------------- column resize */

function colKey(th) {
  return th.getAttribute("data-k") || th.getAttribute("data-col")
    || ("i" + Array.prototype.indexOf.call(th.parentNode.children, th));
}
function loadColWidths() {
  try { return JSON.parse(localStorage.getItem("merchdesk.colWidths") || "{}"); } catch (e) { return {}; }
}
function saveColWidths() {
  try { localStorage.setItem("merchdesk.colWidths", JSON.stringify(COL_WIDTHS)); } catch (e) {}
}
var COL_WIDTHS = loadColWidths();

function applyColWidths() {
  document.querySelectorAll("#thead-row th").forEach(function(th){
    var w = COL_WIDTHS[colKey(th)];
    if (w) th.style.width = w + "px";
  });
}

function initColResize() {
  document.querySelectorAll("#thead-row th").forEach(function(th){
    if (th.querySelector(".col-resizer")) return;
    var handle = document.createElement("span");
    handle.className = "col-resizer";
    handle.title = "Drag to resize";
    handle.addEventListener("click", function(e){ e.stopPropagation(); });
    handle.addEventListener("mousedown", function(e){
      e.preventDefault();
      e.stopPropagation();
      var startX = e.pageX;
      var startW = th.offsetWidth;
      var k = colKey(th);
      handle.classList.add("active");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      function onMove(ev) {
        var w = Math.max(36, startW + (ev.pageX - startX));
        th.style.width = w + "px";
        COL_WIDTHS[k] = Math.round(w);
      }
      function onUp() {
        handle.classList.remove("active");
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        saveColWidths();
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    th.appendChild(handle);
  });
  applyColWidths();
}

/* ------------------------------------------------------------- filtering */

function applyFilters(rows) {
  var f = FILTERS;
  var q = f.q.trim().toLowerCase();
  return rows.filter(function(r) {
    if (q && r.name.toLowerCase().indexOf(q) === -1) return false;
    if (f.mode === "buy" && !(r.rank_all <= 0.5)) return false;
    if (f.mode === "avoid" && !(r.rank_all >= 0.5)) return false;
    if (f.trends.length && f.trends.indexOf(r.trend) === -1) return false;
    if ((r.merch_score || 0) < f.minScore) return false;
    if (f.watchOnly && WATCH.indexOf(r.id) === -1) return false;
    return true;
  });
}
function activeFilterChips() {
  var f = FILTERS, chips = [];
  if (f.mode !== "any") chips.push({ k: "mode", label: "mode:" + (f.mode === "buy" ? "buy dips" : "avoid") });
  if (f.trends.length) chips.push({ k: "trends", label: "trend:" + f.trends.join(",") });
  if (f.minScore > 0) chips.push({ k: "minScore", label: "score>=" + f.minScore });
  if (f.q.trim()) chips.push({ k: "q", label: '"' + f.q.trim() + '"' });
  return chips;
}
function clearFilterChip(k) {
  if (k === "mode") FILTERS.mode = "any";
  else if (k === "trends") FILTERS.trends = [];
  else if (k === "minScore") FILTERS.minScore = 0;
  else if (k === "q") { FILTERS.q = ""; $("cmd").value = ""; }
  syncFilterUI(); saveFilters(); render();
}
function resetFilters() {
  FILTERS.q = ""; FILTERS.mode = "any"; FILTERS.trends = []; FILTERS.minScore = 0;
  FILTERS.watchOnly = false;
  $("cmd").value = "";
  syncFilterUI(); saveFilters(); render();
}
function syncFilterUI() {
  document.querySelectorAll("#fb-mode button").forEach(function(b){
    b.classList.toggle("on", b.getAttribute("data-mode") === FILTERS.mode);
  });
  document.querySelectorAll(".vchip[data-trend]").forEach(function(c){
    c.classList.toggle("on", FILTERS.trends.indexOf(c.getAttribute("data-trend")) !== -1);
  });
  $("fb-score").value = FILTERS.minScore; $("fb-score-v").textContent = FILTERS.minScore;
  if ($("fb-watch")) $("fb-watch").checked = !!FILTERS.watchOnly;
  document.querySelectorAll("[data-col]").forEach(function(el){
    var col = el.getAttribute("data-col");
    el.style.display = FILTERS.cols[col] === false ? "none" : "";
  });
}

/* ----------------------------------------------------------------- render */

function render() {
  var selId = (SEL_INDEX >= 0 && VISIBLE_ROWS[SEL_INDEX]) ? VISIBLE_ROWS[SEL_INDEX].id : null;
  var rows = applyFilters(DATA.slice());
  if (sortKey === "rank") {
    if (sortAsc) rows.reverse();
  } else {
    rows.sort(function(a, b){
      var x = a[sortKey], y = b[sortKey];
      if (typeof x === "string" || typeof y === "string") {
        x = (x||"").toString().toLowerCase(); y = (y||"").toString().toLowerCase();
        return sortAsc ? (x<y?-1:x>y?1:0) : (x<y?1:x>y?-1:0);
      }
      x = x||0; y = y||0; return sortAsc ? x-y : y-x;
    });
  }
  rows.forEach(function(r, i){ r.rank = i + 1; });
  VISIBLE_ROWS = rows;
  SEL_INDEX = selId == null ? -1 : rows.findIndex(function(r){ return r.id === selId; });

  renderSummary(rows);
  renderTrendCounts();

  var tb = $("rows");
  if (!DATA.length) {
    tb.innerHTML = '<tr><td colspan="13" class="empty">nothing scored above the price floor. try a lower floor.</td></tr>';
    renderCards([]);
    return;
  }
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="13" class="empty">0 of ' + DATA.length
      + ' items match your filters. <span id="empty-reset" style="text-decoration:underline;cursor:pointer;color:var(--accent);">reset filters</span></td></tr>';
    var er = document.getElementById("empty-reset");
    if (er) er.addEventListener("click", resetFilters);
    renderCards([]);
    return;
  }
  var html = "";
  rows.forEach(function(r, idx){
    var tcls = "pill t-" + esc(r.trend);
    var sc = Math.round(r.merch_score||0);
    var g = scoreGrade(sc);
    var selCls = idx === SEL_INDEX ? " sel" : "";
    html += '<tr data-idx="' + idx + '" class="' + selCls + '">'
      + '<td class="num rank">' + r.rank + "</td>"
      + '<td class="name">' + esc(r.name) + "</td>"
      + '<td class="num buy">' + gp(r.buy_price) + "</td>"
      + '<td class="num sell">' + gp(r.sell_price) + "</td>"
      + '<td class="num">' + gp(r.margin) + "</td>"
      + '<td class="num">' + (r.roi||0).toFixed(1) + "%</td>"
      + '<td class="num muted" data-col="limit">' + fmtInt(r.limit) + "</td>"
      + '<td class="num sell">' + gp(r.gp_24h) + "</td>"
      + '<td class="num muted" data-col="vol_day">' + fmtInt(r.vol_day) + "</td>"
      + '<td><span class="' + tcls + '">' + esc(r.trend) + "</span></td>"
      + '<td data-col="spark" title="30-day price trend">' + sparkline(r.spark) + "</td>"
      + '<td class="num score-col"><span class="grade ' + g.cls + '" title="' + g.label + ' ' + sc + '">' + sc + "</span></td>"
      + '<td data-col="risk_level"><span class="risk risk-' + esc((r.risk_level||"LOW").toLowerCase()) + '">' + esc(r.risk_level || "—") + "</span></td>"
      + "</tr>";
  });
  tb.innerHTML = html;
  document.querySelectorAll("[data-col]").forEach(function(el){
    var col = el.getAttribute("data-col");
    el.style.display = FILTERS.cols[col] === false ? "none" : "";
  });
  tb.querySelectorAll("tr[data-idx]").forEach(function(tr){
    tr.addEventListener("click", function(){ selectRow(parseInt(tr.getAttribute("data-idx"), 10)); });
  });
  paintHeaders();
  renderCards(rows);
}

function renderCards(rows) {
  var wrap = $("cardwrap");
  if (!wrap) return;
  if (!DATA.length) {
    wrap.innerHTML = '<div class="cards-empty">nothing scored above the price floor. try a lower floor.</div>';
    return;
  }
  if (!rows.length) {
    wrap.innerHTML = '<div class="cards-empty">0 of ' + DATA.length
      + ' items match your filters. <span id="cards-empty-reset" style="text-decoration:underline;cursor:pointer;color:var(--accent);">reset filters</span></div>';
    var er = document.getElementById("cards-empty-reset");
    if (er) er.addEventListener("click", resetFilters);
    return;
  }
  var html = "";
  rows.forEach(function(r, idx){
    var sc = Math.round(r.merch_score||0);
    var g = scoreGrade(sc);
    var risk = (r.risk_level||"LOW").toLowerCase();
    var selCls = idx === SEL_INDEX ? " sel" : "";
    html += '<div class="pick-card' + selCls + '" data-idx="' + idx + '" role="button" tabindex="0">'
      + '<div class="pc-top"><div class="pc-name">' + esc(r.name) + '</div>'
      + '<div class="pc-badges"><span class="grade ' + g.cls + '">' + sc + '</span>'
      + '<span class="risk risk-' + esc(risk) + '">' + esc(r.risk_level || "—") + '</span></div></div>'
      + '<div class="pc-meta">'
      + '<span class="buy">buy ' + gp(r.buy_price) + '</span>'
      + '<span class="sell">sell ' + gp(r.sell_price) + '</span>'
      + '<span class="margin">' + gp(r.margin) + ' · ' + (r.roi||0).toFixed(1) + '%</span>'
      + '<span class="pill t-' + esc(r.trend) + '">' + esc(r.trend) + '</span>'
      + '</div></div>';
  });
  wrap.innerHTML = html;
  wrap.querySelectorAll(".pick-card").forEach(function(card){
    function go(){ selectRow(parseInt(card.getAttribute("data-idx"), 10)); }
    card.addEventListener("click", go);
    card.addEventListener("keydown", function(e){
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
    });
  });
}

function renderSummary(rows) {
  var chips = activeFilterChips();
  var row = $("summary-row");
  if (!DATA.length || !chips.length) { row.style.display = "none"; return; }
  row.style.display = "flex";
  var html = "showing <b>" + rows.length + " of " + DATA.length + "</b>";
  chips.forEach(function(c){
    html += ' &middot; <span class="clearchip" data-clear="' + c.k + '">' + esc(c.label) + " &times;</span>";
  });
  row.innerHTML = html;
  row.querySelectorAll("[data-clear]").forEach(function(el){
    el.addEventListener("click", function(){ clearFilterChip(el.getAttribute("data-clear")); });
  });
}

function renderTrendCounts() {
  var counts = { rising: 0, bounce: 0, flat: 0, falling: 0 };
  DATA.forEach(function(r){ if (counts[r.trend] !== undefined) counts[r.trend]++; });
  document.querySelectorAll(".vchip[data-trend]").forEach(function(c){
    var t = c.getAttribute("data-trend");
    c.querySelector(".n").textContent = counts[t] || 0;
  });
}

function renderTicker() {
  var el = $("ticker");
  if (!DATA.length) { el.innerHTML = '<span class="tk-k">awaiting snapshot&hellip;</span>'; return; }
  var topGp = DATA.reduce(function(m,r){ return Math.max(m, r.gp_24h||0); }, 0);
  var avg = DATA.reduce(function(s,r){ return s + (r.merch_score||0); }, 0) / DATA.length;
  el.innerHTML =
      '<span><span class="tk-k">BEST GP/24H</span> <span class="tk-v accent">' + gp(topGp) + '</span></span>'
    + '<span><span class="tk-k">AVG SCORE</span> <span class="tk-v">' + avg.toFixed(0) + '</span></span>'
    + '<span><span class="tk-k">TOP PICK</span> <span class="tk-v accent">' + esc(DATA[0].name) + '</span></span>';
}

function paintHeaders() {
  document.querySelectorAll("thead th").forEach(function(th){
    var old = th.querySelector(".arrow"); if (old) old.remove();
    if (th.getAttribute("data-k") === sortKey) {
      var a = document.createElement("span");
      a.className = "arrow"; a.textContent = sortAsc ? "^" : "v";
      th.appendChild(a);
    }
  });
}

function setSort(k) {
  if (sortKey === k) sortAsc = !sortAsc;
  else { sortKey = k; sortAsc = (k === "name"); }
  render();
}
document.querySelectorAll("thead th").forEach(function(th){
  var k = th.getAttribute("data-k");
  if (!k || th.classList.contains("nosort")) return;
  th.addEventListener("click", function(e){
    if (e.target && e.target.classList && e.target.classList.contains("col-resizer")) return;
    setSort(k);
  });
});

/* ---------------------------------------------------------- filter bar UI */

document.querySelectorAll("#fb-mode button").forEach(function(b){
  b.addEventListener("click", function(){
    FILTERS.mode = b.getAttribute("data-mode"); syncFilterUI(); saveFilters(); render();
  });
});
document.querySelectorAll(".vchip[data-trend]").forEach(function(c){
  c.addEventListener("click", function(){
    var t = c.getAttribute("data-trend");
    var i = FILTERS.trends.indexOf(t);
    if (i === -1) FILTERS.trends.push(t); else FILTERS.trends.splice(i, 1);
    syncFilterUI(); saveFilters(); render();
  });
});
$("fb-score").addEventListener("input", function(){
  FILTERS.minScore = parseInt($("fb-score").value, 10); $("fb-score-v").textContent = FILTERS.minScore;
  saveFilters(); render();
});
if ($("fb-watch")) $("fb-watch").addEventListener("change", function(){
  FILTERS.watchOnly = $("fb-watch").checked; saveFilters(); render();
});
$("fb-reset").addEventListener("click", resetFilters);

(function buildColsMenu(){
  var pop = $("cols-pop");
  pop.innerHTML = ALL_COLS.map(function(c){
    return '<label><input type="checkbox" data-col-toggle="' + c.key + '" '
      + (FILTERS.cols[c.key] !== false ? "checked" : "") + '> ' + c.label + '</label>';
  }).join("");
  pop.querySelectorAll("[data-col-toggle]").forEach(function(cb){
    cb.addEventListener("change", function(){
      FILTERS.cols[cb.getAttribute("data-col-toggle")] = cb.checked;
      saveFilters(); syncFilterUI();
    });
  });
})();

/* -------------------------------------------------------- command bar */

function runCommand(raw) {
  var t = raw.trim();
  if (!t.startsWith("/")) { FILTERS.q = raw; saveFilters(); render(); return; }
  var parts = t.slice(1).split(/\s+/);
  var cmd = (parts[0] || "").toLowerCase();
  var arg = parts.slice(1).join(" ");
  if (cmd === "cap" || cmd === "capital") { $("capital").value = arg; loadPicks(); }
  else if (cmd === "floor") { $("floor").value = arg; loadPicks(); }
  else if (cmd === "refresh" || cmd === "scan") { refresh(); }
  else if (cmd === "reset") { resetFilters(); }
  else { FILTERS.q = raw; saveFilters(); render(); }
}
$("cmd").addEventListener("input", function(){
  if ($("cmd").value.startsWith("/")) return;   // wait for Enter on commands
  FILTERS.q = $("cmd").value; saveFilters(); render();
});
$("cmd").addEventListener("keydown", function(e){
  if (e.key === "Enter") { runCommand($("cmd").value); if ($("cmd").value.startsWith("/")) $("cmd").value = ""; }
});

/* ------------------------------------------------------- reads (instant) */

function statusLine() {
  var st = $("status"), lamp = $("lamp");
  if (LOADING) { st.innerHTML = "personalising&hellip;"; lamp.className = "lamp busy"; return; }
  if (!STATUS) { st.innerHTML = "&hellip;"; lamp.className = "lamp"; return; }
  if (STATUS.scanning) {
    st.innerHTML = "scanning live GE&hellip;"; lamp.className = "lamp busy";
    return;
  }
  var stale = STATUS.age_seconds !== null && STATUS.age_seconds > 25 * 60;
  lamp.className = "lamp" + (stale ? "" : " live");
  var when = '<span class="' + (stale ? "stale" : "") + '">' + ago(STATUS.age_seconds) + "</span>";
  st.innerHTML = "<b>" + DATA.length + "</b> picks &middot; " + when + " &middot; every " + STATUS.interval_minutes + "m";
}

function loadPicks() {
  var cap = $("capital").value.trim(), floor = $("floor").value.trim();
  var q = ["top=200"];
  if (cap) q.push("capital=" + encodeURIComponent(cap));
  if (floor) q.push("floor=" + encodeURIComponent(floor));
  LOADING = true; statusLine();
  return fetch("/api/picks?" + q.join("&"))
    .then(function(r){ return r.json().then(function(d){ d._code = r.status; return d; }); })
    .then(function(d){
      LOADING = false;
      if (d._code === 503) {
        DATA = []; renderTicker();
        $("rows").innerHTML = '<tr><td colspan="13" class="empty">'
          + esc(d.error || "no data yet") + "</td></tr>";
        if ($("cardwrap")) $("cardwrap").innerHTML = '<div class="cards-empty">'
          + esc(d.error || "no data yet") + "</div>";
        if (d.scanning) attachStream(true);
        return;
      }
      DATA = d.rows || [];
      ANALYSIS_NOTE = d.analysis_note || "";
      CAP = d.capital; FLOOR = d.floor;
      $("capital").value = gp(CAP);
      $("floor").value = gp(FLOOR);
      syncCapFloorInputs(false);
      savePrefs();
      sortKey = "margin"; sortAsc = false;
      render(); renderTicker(); statusLine();
    })
    .catch(function(e){
      LOADING = false;
      $("rows").innerHTML = '<tr><td colspan="13" class="err">could not reach '
        + 'the server: ' + esc(e.message) + "</td></tr>";
      if ($("cardwrap")) $("cardwrap").innerHTML = '<div class="cards-empty">'
        + 'could not reach the server: ' + esc(e.message) + "</div>";
    });
}

function pollStatus() {
  return fetch("/api/status").then(function(r){ return r.json(); })
    .then(function(d){
      var wasScanning = STATUS && STATUS.scanning;
      var newSnapshot = STATUS && d.updated_at !== STATUS.updated_at;
      STATUS = d;
      $("refresh").disabled = d.scanning;
      statusLine();
      if (d.scanning && !ES) attachStream(true);
      if (!d.scanning && wasScanning && newSnapshot) loadPicks();
    })
    .catch(function(e){
      var st = $("status");
      if (st) st.innerHTML = '<span class="stale">status unreachable</span>';
      var lamp = $("lamp");
      if (lamp) lamp.className = "lamp";
    });
}

/* ------------------------------------------------------ writes (a scan) */

function setBusy(on) {
  $("refresh").disabled = on;
  $("overlay").classList.toggle("on", on);
}
function conReset() {
  $("con-log").innerHTML = "";
  $("con-phase").textContent = "starting…";
  $("con-pct").textContent = "";
  $("con-bar").style.width = "0";
  $("con-progress").classList.add("indet");
}
function conLog(msg, level) {
  var log = $("con-log");
  var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  document.querySelectorAll("#con-log .con-cursor").forEach(function(el){
    el.classList.remove("con-cursor");
  });
  var ln = document.createElement("div");
  ln.className = "ln con-cursor" + (level && level !== "info" ? " " + level : "");
  ln.textContent = msg;
  log.appendChild(ln);
  while (log.childNodes.length > 400) log.removeChild(log.firstChild);
  if (atBottom) log.scrollTop = log.scrollHeight;
}
function conProgress(done, total) {
  var pr = $("con-progress");
  pr.classList.remove("indet");
  var pct = total ? Math.round((done / total) * 100) : 0;
  $("con-bar").style.width = pct + "%";
  $("con-pct").textContent = pct + "%";
}

function attachStream(quiet) {
  if (ES) return;
  if (!quiet) conReset();
  setBusy(true);
  var es = new EventSource("/api/refresh/stream");
  ES = es;
  es.onmessage = function(ev) {
    var d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.kind === "phase") {
      $("con-phase").textContent = d.label;
      conLog(d.label, "phase");
    } else if (d.kind === "log") {
      conLog(d.msg, d.level);
    } else if (d.kind === "progress") {
      conProgress(d.done, d.total);
    } else if (d.kind === "idle") {
      es.close(); ES = null; setBusy(false);
    } else if (d.kind === "result") {
      es.close(); ES = null;
      conProgress(1, 1);
      conLog("snapshot stored — " + d.n_items + " items", "ok");
      loadPicks().then(pollStatus);
      setTimeout(function(){ setBusy(false); }, 600);
    } else if (d.kind === "error") {
      es.close(); ES = null;
      conLog("scan failed: " + d.error, "err");
      setTimeout(function(){ setBusy(false); }, 1500);
      pollStatus();
    }
  };
  es.onerror = function() {
    if (!ES) return;
    es.close(); ES = null;
    var stillBusy = STATUS && STATUS.scanning;
    if (stillBusy) {
      conLog("stream interrupted — reconnecting…", "dim");
      setTimeout(function(){ if (!ES && STATUS && STATUS.scanning) attachStream(true); }, 1500);
    } else {
      conLog("stream closed", "dim");
      setTimeout(function(){ setBusy(false); }, 400);
    }
  };
}

function refresh() {
  conReset();
  setBusy(true);
  fetch("/api/refresh", { method: "POST" })
    .then(function(r){ return r.json().then(function(d){ d._code = r.status; return d; }); })
    .then(function(d){
      if (d._code === 429 || (!d.started && !d.scanning)) {
        conLog(d.reason || "no scan started", "dim");
        setTimeout(function(){ setBusy(false); }, 1200);
        return;
      }
      if (!d.started) conLog(d.reason, "dim");
      attachStream(true);
    })
    .catch(function(e){
      conLog("could not start a scan: " + e.message, "err");
      setTimeout(function(){ setBusy(false); }, 1200);
    });
}

$("refresh").addEventListener("click", refresh);
$("con-close").addEventListener("click", function(){
  $("overlay").classList.remove("on");
});
["capital", "floor"].forEach(function(id){
  $(id).addEventListener("keydown", function(e){
    if (e.key === "Enter") loadPicks();
  });
});

/* ----------------------------------------------------- action / execution */

function rangeLabel(rankAll) {
  var pct = Math.round((rankAll || 0) * 100);
  if (pct <= 15) return pct + "th pct — near its cheapest ever";
  if (pct >= 85) return pct + "th pct — near its priciest ever";
  return pct + "th pct of its own all-time range";
}
function trendWord(t) {
  return { bounce: "just turned up after a dip", rising: "trending up",
           falling: "still sliding — knife risk", flat: "flat" }[t] || t;
}
function axBar(label, val) {
  var v = Math.round(val || 0);
  return '<div class="ax-bar-row"><span class="lbl">' + esc(label) + '</span>'
    + '<span class="ax-bar-track"><i style="width:' + Math.max(0,Math.min(100,v)) + '%"></i></span>'
    + '<span class="val">' + v + '</span></div>';
}
function axStat(k, v, cls) {
  return '<div class="ax-stat"><div class="k">' + k + '</div><div class="v' + (cls ? " " + cls : "")
    + '">' + v + '</div></div>';
}

function selectRow(idx) {
  SEL_INDEX = idx;
  render();
  renderAction();
  if (isMobile() && idx >= 0) openDetailSheet();
}
function clearSelection() {
  SEL_INDEX = -1;
  render();
  renderAction();
  closeDetailSheet();
}
function stepSelection(delta) {
  if (!VISIBLE_ROWS.length) return;
  SEL_INDEX = SEL_INDEX < 0 ? 0 : (SEL_INDEX + delta + VISIBLE_ROWS.length) % VISIBLE_ROWS.length;
  render();
  renderAction();
  if (isMobile()) {
    openDetailSheet();
    return;
  }
  var tr = document.querySelector('tr[data-idx="' + SEL_INDEX + '"]');
  if (tr) tr.scrollIntoView({ block: "nearest" });
}

function execRecalc(r) {
  var qtyInput = $("ax-qty");
  if (!qtyInput) return;
  var maxQty = Math.max(1, Math.floor(r.limit || 1));
  var qty = Math.max(0, Math.min(maxQty, parseInt(qtyInput.value, 10) || 0));
  var cost = qty * (r.buy_price || 0);
  var proceeds = qty * (r.sell_price || 0);
  var profit = qty * (r.margin || 0);
  $("ax-exec-cost").textContent = gp(cost);
  $("ax-exec-proceeds").textContent = gp(proceeds);
  $("ax-exec-profit").textContent = (profit >= 0 ? "+" : "") + gp(profit);
  $("ax-exec-profit").style.color = profit >= 0 ? "var(--gain)" : "var(--loss)";
}

function renderAction() {
  var r = VISIBLE_ROWS[SEL_INDEX];
  $("ax-empty").style.display = r ? "none" : "flex";
  var content = $("ax-content");
  if (!r) { content.style.display = "none"; return; }
  content.style.display = "flex"; content.style.flexDirection = "column"; content.style.flex = "1 1 auto";

  var sc = Math.round(r.merch_score || 0);
  var g = scoreGrade(sc);
  var maxQty = Math.max(1, Math.floor(r.limit || 1));

  var watched = WATCH.indexOf(r.id) !== -1;
  var wiki = "https://oldschool.runescape.wiki/w/Special:Lookup?type=item&id=" + r.id;
  var watchTitle = "Add to Watchlist";
  var eyeSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
    + '<circle cx="12" cy="12" r="3"/>'
    + '</svg>';
  var html = ''
    + '<div class="ax-head">'
    +   '<div class="ax-head-top">'
    +     '<div class="ax-title">' + esc(r.name) + '</div>'
    +     '<button type="button" class="ax-watch' + (watched ? ' on' : '') + '" id="ax-watch"'
    +       ' title="' + watchTitle + '" aria-label="' + watchTitle + '"'
    +       ' aria-pressed="' + (watched ? 'true' : 'false') + '">' + eyeSvg + '</button>'
    +   '</div>'
    +   '<div class="ax-sub"><span class="trend t-' + esc(r.trend) + '">' + esc(r.trend) + '</span>'
    +   ' · ' + fmtInt(r.units_24h) + ' units/24h · ROI ' + (r.roi||0).toFixed(1) + '%</div>'
    +   '<div class="ax-actions">'
    +     '<button type="button" class="ghost" id="ax-copy-buy">Copy buy</button>'
    +     '<button type="button" class="ghost" id="ax-copy-sell">Copy sell</button>'
    +     '<a class="ghost" href="' + wiki + '" target="_blank" rel="noopener">Wiki</a>'
    +   '</div>'
    +   '<div class="ax-score"><span class="num">' + sc + '</span>'
    +     '<span class="grade ' + g.cls + '">' + g.label + '</span>'
    +     '<span class="lbl">Merch score</span></div>'
    + '</div>'
    + '<div class="ax-body">'
    +   '<div><div class="ax-section-title">Execution</div>'
    +     '<div class="ax-exec">'
    +       '<div class="row"><label for="ax-qty">quantity (max ' + fmtInt(maxQty) + ') / 4h</label>'
    +         '<input id="ax-qty" type="number" min="0" max="' + maxQty + '" value="' + maxQty + '"></div>'
    +       '<div class="out"><span class="muted">cost</span><span class="v buy" id="ax-exec-cost">-</span></div>'
    +       '<div class="out"><span class="muted">proceeds</span><span class="v sell" id="ax-exec-proceeds">-</span></div>'
    +       '<div class="out"><span class="muted">net profit</span><span class="v" id="ax-exec-profit">-</span></div>'
    +     '</div>'
    +   '</div>'
    +   '<div class="ax-tax"><span>GE tax (2%, cap 5m)</span><span class="v">&minus;' + gp(r.tax) + '</span></div>'
    +   '<div><div class="ax-section-title">Signals (heuristic)</div>'
    +     '<div class="ax-grid3">'
    +       axStat("Dip", r.dip_confidence != null ? r.dip_confidence.toFixed(0) : "—", "")
    +       axStat("Flip", r.flip_score != null ? r.flip_score.toFixed(0) : "—", "")
    +       axStat("Pred.", esc(r.predicted_trend || "—"), "")
    +     '</div>'
    +     (ANALYSIS_NOTE ? '<div class="ax-note">' + esc(ANALYSIS_NOTE) + '</div>' : '')
    +   '</div>'
    +   '<div><div class="ax-section-title">30-day price history</div>'
    +     '<div class="ax-chart">' + lineChart(r.spark, sparkDayLabels(r.spark), 280, 72, gp) + '</div>'
    +   '</div>'
    +   '<div><div class="ax-section-title">Last few scans</div>'
    +     '<div class="ax-chart" id="ax-hist"><div class="ax-nodata">loading recent scan history&hellip;</div></div>'
    +   '</div>'
    +   '<div><div class="ax-section-title">Score breakdown'
    +     ' <span class="ax-subtoggle" id="ax-score-toggle" role="button" tabindex="0">show</span></div>'
    +     '<div class="ax-collapse" id="ax-score-detail">'
    +       '<div class="ax-bars">'
    +         axBar("Merch", r.merch_score) + axBar("Flip", r.flip) + axBar("Swing", r.swing)
    +       '</div>'
    +       '<span class="ax-subtoggle" id="ax-subtoggle" role="button" tabindex="0">show what drives the merch score</span>'
    +       '<div class="ax-bars ax-subbars" id="ax-subbars">'
    +         axBar("Throughput", r.sc_throughput) + axBar("Liquidity", r.sc_liquidity)
    +         axBar("Volatility", r.sc_volatility_rank) + axBar("Value (cheap)", r.sc_value)
    +       '</div>'
    +       '<div class="ax-range">cheapest'
    +         '<span class="ax-range-track"><span class="ax-range-dot" style="left:' + Math.round((r.rank_all||0)*100) + '%;"></span></span>'
    +         'priciest</div>'
    +       '<div style="font-size:10px;color:var(--faint);margin-top:4px;">' + rangeLabel(r.rank_all)
    +         ' &middot; z30 ' + (r.z30 != null ? r.z30.toFixed(2) : "&mdash;") + '</div>'
    +     '</div>'
    +   '</div>'
    +   (r.catalyst
        ? '<div class="ax-catalyst"><div class="lbl">Content catalyst</div>' + esc(r.catalyst) + '</div>'
        : '')
    +   (r.reason
        ? '<div><div class="ax-section-title">Reasoning</div><div class="ax-reason">' + esc(r.reason) + '</div></div>'
        : '')
    + '</div>'
    + '<div class="ax-foot">'
    +   '<span class="navbtn" id="ax-prev" role="button" tabindex="0">&larr; k prev</span>'
    +   '<span>' + (SEL_INDEX + 1) + ' of ' + VISIBLE_ROWS.length + '</span>'
    +   '<span class="navbtn" id="ax-next" role="button" tabindex="0">j next &rarr;</span>'
    + '</div>';

  content.innerHTML = html;
  $("ax-prev").addEventListener("click", function(){ stepSelection(-1); });
  $("ax-next").addEventListener("click", function(){ stepSelection(1); });
  function copyTxt(btn, t){
    function flash(){ btn.classList.add("ok"); btn.textContent = "Copied"; setTimeout(function(){ btn.classList.remove("ok"); btn.textContent = btn.id === "ax-copy-buy" ? "Copy buy" : "Copy sell"; }, 900); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(String(t)).then(flash).catch(function(){});
    }
  }
  $("ax-copy-buy").addEventListener("click", function(){ copyTxt($("ax-copy-buy"), r.buy_price); });
  $("ax-copy-sell").addEventListener("click", function(){ copyTxt($("ax-copy-sell"), r.sell_price); });
  $("ax-watch").addEventListener("click", function(){
    var i = WATCH.indexOf(r.id);
    if (i === -1) WATCH.push(r.id); else WATCH.splice(i, 1);
    saveWatch(); renderAction();
  });
  $("ax-score-toggle").addEventListener("click", function(){
    var on = $("ax-score-detail").classList.toggle("on");
    $("ax-score-toggle").textContent = on ? "hide" : "show";
  });
  $("ax-subtoggle").addEventListener("click", function(){
    var on = $("ax-subbars").classList.toggle("on");
    $("ax-subtoggle").textContent = (on ? "hide" : "show") + " what drives the merch score";
  });
  $("ax-qty").addEventListener("input", function(){ execRecalc(r); });
  execRecalc(r);
  loadItemHistory(r.id);
}

function loadItemHistory(itemId) {
  fetch("/api/item/" + itemId + "/history?limit=30")
    .then(function(r){ return r.json(); })
    .then(function(d){
      var el = $("ax-hist");
      if (!el) return;
      var hist = d.history || [];
      if (hist.length < 2) {
        el.innerHTML = '<span class="ax-nodata">not enough scan history yet — builds up every '
          + (STATUS ? STATUS.interval_minutes : 10) + ' min.</span>';
        return;
      }
      var margins = hist.map(function(h){ return h.margin || 0; });
      var times = hist.map(function(h){
        return h.finished_at
          ? new Date(h.finished_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
          : "";
      });
      el.innerHTML = lineChart(margins, times, 280, 72, gp)
        + '<div style="font-size:10px;color:var(--faint);margin-top:6px;">net margin, last '
        + hist.length + ' scans (~' + Math.round(hist.length * (STATUS ? STATUS.interval_minutes : 10) / 60 * 10) / 10 + 'h)</div>';
    })
    .catch(function(){
      var el = $("ax-hist");
      if (el) el.innerHTML = '<span class="ax-nodata">could not load scan history.</span>';
    });
}


/* --------------------------------------------------------- mobile sheets */

function moveChildren(from, to) {
  if (!from || !to || from === to) return;
  while (from.firstChild) to.appendChild(from.firstChild);
}
function parkFiltersInSheet() {
  var body = $("filter-sheet-body");
  var rail = $("rail");
  if (body && rail && rail.childElementCount) moveChildren(rail, body);
}
function restoreFiltersToRail() {
  var body = $("filter-sheet-body");
  var rail = $("rail");
  if (body && rail && body.childElementCount) moveChildren(body, rail);
}
function parkDetailInSheet() {
  var host = $("detail-sheet-body");
  var action = $("action");
  if (host && action && action.childElementCount) moveChildren(action, host);
}
function restoreDetailToAction() {
  var host = $("detail-sheet-body");
  var action = $("action");
  if (host && action && host.childElementCount) moveChildren(host, action);
}
function openFilterSheet() {
  var sheet = $("filter-sheet");
  var bd = $("sheet-backdrop");
  if (!sheet) return;
  parkFiltersInSheet();
  sheet.classList.add("open");
  if (bd) bd.classList.add("on");
  document.body.classList.add("sheet-open");
}
function closeFilterSheet() {
  var sheet = $("filter-sheet");
  var bd = $("sheet-backdrop");
  if (sheet) sheet.classList.remove("open");
  if (!isMobile()) restoreFiltersToRail();
  if (bd && !($("detail-sheet") && $("detail-sheet").classList.contains("open"))) {
    bd.classList.remove("on");
  }
  if (!($("detail-sheet") && $("detail-sheet").classList.contains("open"))) {
    document.body.classList.remove("sheet-open");
  }
}
function openDetailSheet() {
  var sheet = $("detail-sheet");
  var bd = $("sheet-backdrop");
  if (!sheet) return;
  parkDetailInSheet();
  sheet.classList.add("open");
  if (bd) bd.classList.add("on");
  document.body.classList.add("sheet-open");
}
function closeDetailSheet() {
  var sheet = $("detail-sheet");
  var bd = $("sheet-backdrop");
  if (sheet) sheet.classList.remove("open");
  if (!isMobile()) restoreDetailToAction();
  if (bd && !($("filter-sheet") && $("filter-sheet").classList.contains("open"))) {
    bd.classList.remove("on");
  }
  if (!($("filter-sheet") && $("filter-sheet").classList.contains("open"))) {
    document.body.classList.remove("sheet-open");
  }
}
function closeAllSheets() {
  var f = $("filter-sheet");
  var d = $("detail-sheet");
  if (f) f.classList.remove("open");
  if (d) d.classList.remove("open");
  if (!isMobile()) {
    restoreFiltersToRail();
    restoreDetailToAction();
  }
  var bd = $("sheet-backdrop");
  if (bd) bd.classList.remove("on");
  document.body.classList.remove("sheet-open");
}
function syncMobileStatus() {
  var el = $("mobile-status");
  var src = $("status");
  if (el && src) el.innerHTML = src.innerHTML;
}
function onLayoutChange() {
  if (isMobile()) {
    // keep parked nodes in sheets while mobile; nothing to do until open
  } else {
    closeAllSheets();
    restoreFiltersToRail();
    restoreDetailToAction();
  }
}

/* -------------------------------------------------------------- keyboard */

document.addEventListener("keydown", function(e){
  var typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (e.key === "/" && !typing) {
    e.preventDefault(); $("cmd").focus();
    return;
  }
  if (e.key === "Escape") {
    if ($("detail-sheet") && $("detail-sheet").classList.contains("open")) {
      e.preventDefault(); closeDetailSheet(); return;
    }
    if ($("filter-sheet") && $("filter-sheet").classList.contains("open")) {
      e.preventDefault(); closeFilterSheet(); return;
    }
    if (typing) { document.activeElement.blur(); return; }
  }
  if (!typing && (e.key === "j" || e.key === "ArrowDown")) { e.preventDefault(); stepSelection(1); return; }
  if (!typing && (e.key === "k" || e.key === "ArrowUp")) { e.preventDefault(); stepSelection(-1); return; }
});

document.addEventListener("shell:theme", function(){
  if (VISIBLE_ROWS.length) render();
  if (SEL_INDEX >= 0) renderAction();
});

window.addEventListener("load", function(){
  loadPrefs();
  initColResize();
  syncFilterUI();
  $("cmd").value = FILTERS.q || "";
  var mf = $("mobile-filters");
  if (mf) mf.addEventListener("click", openFilterSheet);
  var mscan = $("mobile-scan");
  if (mscan) mscan.addEventListener("click", refresh);
  ["mobile-capital", "mobile-floor"].forEach(function(id){
    var el = $(id);
    if (!el) return;
    el.addEventListener("keydown", function(e){
      if (e.key === "Enter") { syncCapFloorInputs(true); loadPicks(); }
    });
    el.addEventListener("change", function(){ syncCapFloorInputs(true); loadPicks(); });
  });
  syncCapFloorInputs(false);
  var fclose = $("filter-sheet-close");
  if (fclose) fclose.addEventListener("click", closeFilterSheet);
  var dclose = $("detail-sheet-close");
  if (dclose) dclose.addEventListener("click", function(){ closeDetailSheet(); });
  var sbd = $("sheet-backdrop");
  if (sbd) sbd.addEventListener("click", closeAllSheets);
  var origStatus = statusLine;
  statusLine = function(){ origStatus(); syncMobileStatus(); };
  pollStatus().then(loadPicks);
  setInterval(pollStatus, 15000);
  window.addEventListener("resize", onLayoutChange);
});
