#!/usr/bin/env python3
"""
osrs_merch_scan.py — scan the whole Grand Exchange, filter to a ranked
top-N merch shortlist using trend + margin logic.

    python -m venv .venv
    source .venv/bin/activate.fish
    pip install requests
    python osrs_merch_scan.py --bankroll 700000000

Two-stage by design. Stage 1 uses three bulk API calls to score every
tradeable item cheaply, then keeps a shortlist. Stage 2 pulls a year of
daily candles for the shortlist only — one call each, which is why the
shortlist exists at all.

Set UA below. The wiki blocks the default python-requests agent.
Docs: https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices
"""


import argparse
import csv
import html
import json
import statistics
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The scanning core now lives in scanner.py so the hosted clan app and this
# local tool share one implementation. Everything imported below behaves
# exactly as it did when it was defined inline here.
from scanner import (  # noqa: F401  (re-exported for the CLI/GUI below)
    BASE,
    GE_WINDOWS_24H,
    NEWS_RSS,
    SESSION,
    UA,
    ScanConfig,
    catalyst_for,
    enrich,
    fetch_news,
    fillable_units_24h,
    fmt_gp,
    get,
    merch_score,
    net_margin,
    parse_gp,
    prefilter,
    rank_for,
    rank_within,
    reason_for,
    scan_market,
    score,
    slope_pct_per_day,
    volatility_pct,
)


def prompt_capital(default):
    """Ask for liquid capital (buying power) before launching. Falls back to
    `default` on empty input or a non-interactive stdin."""
    if not sys.stdin or not sys.stdin.isatty():
        return default
    while True:
        try:
            raw = input(f"Liquid capital / buying power [{fmt_gp(default)}]: ")
        except EOFError:
            return default
        if not raw.strip():
            return default
        try:
            val = parse_gp(raw)
            if val <= 0:
                raise ValueError("must be positive")
            return val
        except ValueError:
            print("  couldn't read that — try e.g. 700m, 1.5b, 500k, or 700000000")


# ------------------------------------------------------- scan orchestration

def run_scan(params, progress=None, on_event=None):
    """Full live pipeline -> ranked top-N dicts. Every call hits the API
    fresh; there is no cache.

    Now a thin composition of the two halves in scanner.py: the expensive
    capital-agnostic `scan_market`, followed by `rank_for` applying this
    run's bankroll and floor. Output is identical to the old monolithic
    version for the same parameters — that equivalence is what
    test_parity.py pins down.

    `progress(done, total)` — optional legacy per-item callback (CLI).
    `on_event(kind, **data)` — optional structured feed for the live GUI
    console. Kinds: "log" (msg, level), "phase" (label), "progress"
    (done, total, name)."""
    bankroll = params["bankroll"]
    floor = params.get("min_price", 500_000)

    cfg = ScanConfig(
        reference_bankroll=bankroll,
        global_floor=floor,
        shortlist=params["shortlist"],
        sleep=params["sleep"],
        min_deploy_frac=params["min_deploy_frac"],
        max_spread_pct=params["max_spread_pct"],
        stale_hours=params["stale_hours"],
    )

    # Bridge the legacy per-item progress callback onto the event feed.
    def relay(kind, **data):
        if kind == "progress" and progress:
            progress(data["done"], data["total"])
        if on_event:
            on_event(kind, **data)

    _meta, market_rows = scan_market(cfg, on_event=relay)

    if not market_rows:
        if on_event:
            on_event("log", msg="no items had enough history to score",
                     level="err")
        return []

    if on_event:
        on_event("phase", label="Scoring, tax + capital economics, ranking")
        on_event("log", msg=f"Applying {fmt_gp(bankroll)} of capital to "
                            f"{len(market_rows)} items...")

    top = rank_for(market_rows, bankroll, floor,
                   top=params["top"], mode=params.get("mode", "any"))

    if on_event:
        on_event("log", msg=f"ranked — returning top {len(top)} picks",
                 level="ok")
    return top



def check_item(name):
    """Dump one item's raw numbers for side-by-side comparison with the GE."""
    mapping = {i["name"].lower(): i for i in get("/mapping")}
    meta = mapping.get(name.lower())
    if not meta:
        near = [n for n in mapping if name.lower() in n][:8]
        print(f"no exact match for {name!r}.")
        if near:
            print("did you mean: " + ", ".join(sorted(near)))
        return

    iid = meta["id"]
    live = (get("/latest", id=iid).get("data", {}) or {}).get(str(iid), {})
    high, low = live.get("high"), live.get("low")
    now = time.time()

    print(f"\n{meta['name']}  (id {iid})")
    print("-" * 46)
    if not high or not low:
        print("no live trade data — item is effectively untraded right now.")
        return

    ha = (now - live.get("highTime", now)) / 60
    la = (now - live.get("lowTime", now)) / 60
    tax = min(high * 0.02, 5_000_000)

    print(f"{'insta-buy (high)':<26}{high:>16,}  {ha:>5.0f}m ago")
    print(f"{'insta-sell (low)':<26}{low:>16,}  {la:>5.0f}m ago")
    print(f"{'raw spread':<26}{high - low:>16,}")
    print(f"{'GE tax on sale':<26}{-tax:>16,.0f}")
    print(f"{'net margin':<26}{net_margin(high, low):>16,.0f}")
    print(f"{'buy limit / 4h':<26}{meta.get('limit') or 0:>16,}")

    data = get("/timeseries", id=iid, timestep="24h").get("data", [])
    series = []
    for p in data:
        hi, lo = p.get("avgHighPrice"), p.get("avgLowPrice")
        if hi is None and lo is None:
            continue
        series.append((hi + lo) / 2 if (hi and lo) else (hi or lo))

    if len(series) >= 30:
        mid = (high + low) / 2
        print(f"\n{'30d mean':<26}{statistics.mean(series[-30:]):>16,.0f}")
        print(f"{'90d low / high':<26}"
              f"{min(series[-90:]):>16,.0f} / {max(series[-90:]):,.0f}")
        print(f"{'percentile (all time)':<26}"
              f"{rank_within(series, mid) * 100:>15.0f}%   "
              f"over {len(series)} days")
        print(f"{'30d slope':<26}"
              f"{slope_pct_per_day(series[-30:]):>+15.2f}%/day")

    print("\nlast 10 daily midpoints (oldest first):")
    print("  " + "  ".join(fmt_gp(p) for p in series[-10:]))
    print("\nNote: these are real RuneLite-observed trades. The GE's own")
    print("displayed guide price lags by days and will NOT match.")


# ------------------------------------------------------------------ gui

GUI_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grand Exchange Merch Scanner</title>
<style>
  :root {
    --bg: #0f1115; --bg2: #151922; --card: #171b24; --card2: #1c212c;
    --line: #262c39; --line2: #313947;
    --text: #e7ebf2; --muted: #9aa4b6; --faint: #6b7488;
    --gold: #f4c430; --gold2: #d9a521;
    --green: #3fbf7f; --red: #ef5b5b; --amber: #f0a340; --blue: #5b9bd5;
    --shadow: 0 10px 30px rgba(0,0,0,.35);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, "Noto Sans", sans-serif;
    font-size: 14px; line-height: 1.45;
    background:
      radial-gradient(1200px 600px at 15% -10%, #1b2130 0, rgba(27,33,48,0) 60%),
      radial-gradient(1000px 500px at 100% 0, #201a10 0, rgba(32,26,16,0) 55%),
      var(--bg);
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1400px; margin: 0 auto; padding: 28px 24px 80px; }
  header { display: flex; align-items: flex-end; justify-content: space-between;
           flex-wrap: wrap; gap: 16px; margin-bottom: 22px; }
  .brand { display: flex; align-items: center; gap: 14px; }
  .logo {
    width: 44px; height: 44px; flex: 0 0 44px; border-radius: 12px;
    display: grid; place-items: center; font-size: 22px;
    background: linear-gradient(145deg, #2a2f3c, #1a1e28);
    border: 1px solid var(--line2);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.05);
  }
  h1 { margin: 0; font-size: 20px; font-weight: 650; letter-spacing: .2px; }
  h1 .accent { color: var(--gold); }
  .tag { color: var(--muted); font-size: 12.5px; margin-top: 2px; }
  .controls { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  #status { color: var(--muted); font-size: 12.5px; }
  #status b { color: var(--text); font-weight: 600; }
  button#refresh {
    font: inherit; font-weight: 600; font-size: 13.5px; cursor: pointer;
    color: #241b04; padding: 10px 18px; border: 0; border-radius: 10px;
    background: linear-gradient(180deg, #ffd75a, var(--gold) 55%, var(--gold2));
    box-shadow: 0 4px 14px rgba(244,196,48,.28), inset 0 1px 0 rgba(255,255,255,.4);
    transition: transform .08s ease, filter .15s ease;
  }
  button#refresh:hover { filter: brightness(1.05); }
  button#refresh:active { transform: translateY(1px); }
  button#refresh:disabled { filter: grayscale(.5) brightness(.8); cursor: wait; }
  .capbox { display: flex; align-items: center; gap: 10px;
            background: var(--card); border: 1px solid var(--line2);
            border-radius: 10px; padding: 6px 8px 6px 12px; }
  .capbox .field { display: flex; align-items: center; gap: 8px; }
  .capbox .divider { width: 1px; align-self: stretch; background: var(--line2);
                     margin: 2px 0; }
  .capbox label { color: var(--faint); font-size: 11px; text-transform: uppercase;
                  letter-spacing: .5px; white-space: nowrap; }
  .capbox input {
    width: 82px; font: inherit; font-size: 13.5px; font-weight: 600;
    color: var(--gold); background: var(--bg2); border: 1px solid var(--line2);
    border-radius: 7px; padding: 6px 9px; outline: none;
  }
  .capbox input:focus { border-color: var(--gold2); }
  button#confirm {
    font: inherit; font-weight: 600; font-size: 13px; cursor: pointer;
    color: var(--text); background: var(--card2); border: 1px solid var(--line2);
    border-radius: 7px; padding: 7px 13px;
  }
  button#confirm:hover { border-color: var(--gold2); color: var(--gold); }
  button#confirm:disabled { opacity: .5; cursor: wait; }

  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
  .stat {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 12px 16px; min-width: 130px;
  }
  .stat .k { color: var(--faint); font-size: 11px; text-transform: uppercase;
             letter-spacing: .6px; }
  .stat .v { font-size: 18px; font-weight: 650; margin-top: 3px; }
  .stat .v.gold { color: var(--gold); }

  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    box-shadow: var(--shadow); overflow: hidden;
  }
  .tablescroll { overflow-x: auto; }
  table { width: 100%; border-collapse: separate; border-spacing: 0;
          font-variant-numeric: tabular-nums; }
  thead th {
    position: sticky; top: 0; z-index: 3; text-align: left; white-space: nowrap;
    background: linear-gradient(180deg, #1b202b, #161a23);
    color: var(--muted); font-weight: 600; font-size: 11.5px;
    letter-spacing: .3px; text-transform: uppercase;
    padding: 12px 14px; border-bottom: 1px solid var(--line2);
    cursor: pointer; user-select: none;
  }
  thead th.num { text-align: right; }
  thead th:hover { color: var(--text); }
  thead th.nosort { cursor: default; }
  thead th.nosort:hover { color: var(--muted); }
  thead th .arrow { color: var(--gold); font-size: 10px; margin-left: 4px; }
  svg.spark { display: block; }
  td.sparkcell { padding-top: 6px; padding-bottom: 6px; }
  tbody td { padding: 11px 14px; border-bottom: 1px solid var(--line);
             white-space: nowrap; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover { background: var(--card2); }
  td.num { text-align: right; }
  td.rank { color: var(--faint); font-weight: 600; }
  td.name { font-weight: 600; }
  td.reason { white-space: normal; color: var(--muted); font-size: 12.5px;
              min-width: 300px; max-width: 460px; line-height: 1.4; }
  .buy { color: var(--blue); font-weight: 600; }
  .sell { color: var(--green); font-weight: 600; }
  .muted { color: var(--faint); }

  .pill {
    display: inline-block; padding: 2px 9px; border-radius: 999px;
    font-size: 11.5px; font-weight: 600; border: 1px solid transparent;
  }
  .t-rising  { color: var(--green); background: rgba(63,191,127,.12);
               border-color: rgba(63,191,127,.3); }
  .t-falling { color: var(--red);   background: rgba(239,91,91,.12);
               border-color: rgba(239,91,91,.3); }
  .t-bounce  { color: var(--amber); background: rgba(240,163,64,.12);
               border-color: rgba(240,163,64,.3); }
  .t-flat    { color: var(--muted); background: rgba(154,164,182,.1);
               border-color: rgba(154,164,182,.25); }

  .scorewrap { display: flex; align-items: center; gap: 8px;
               justify-content: flex-end; }
  .scorebar { width: 46px; height: 6px; border-radius: 4px; overflow: hidden;
              background: rgba(255,255,255,.08); }
  .scorebar > i { display: block; height: 100%; }
  .scoreval { font-weight: 650; min-width: 24px; text-align: right; }

  .news {
    display: inline-flex; align-items: center; gap: 5px; cursor: help;
    color: var(--gold); font-size: 12px; font-weight: 600;
    background: rgba(244,196,48,.1); border: 1px solid rgba(244,196,48,.28);
    padding: 2px 8px; border-radius: 999px;
  }

  .foot { color: var(--faint); font-size: 12px; margin-top: 16px;
          line-height: 1.6; }

  #overlay {
    position: fixed; inset: 0; z-index: 50; display: none;
    background: rgba(8,10,14,.78); backdrop-filter: blur(3px);
    align-items: center; justify-content: center; padding: 24px;
  }
  #overlay.on { display: flex; }
  .console {
    width: min(760px, 96vw); background: #0d1017;
    border: 1px solid var(--line2); border-radius: 14px;
    box-shadow: 0 24px 70px rgba(0,0,0,.6); overflow: hidden;
    display: flex; flex-direction: column;
  }
  .con-head {
    display: flex; align-items: center; gap: 8px; padding: 11px 16px;
    background: linear-gradient(180deg, #191e28, #12161e);
    border-bottom: 1px solid var(--line2);
  }
  .con-head .dot { width: 11px; height: 11px; border-radius: 50%;
                   background: #ef5b5b; box-shadow: inset 0 0 0 1px rgba(0,0,0,.2); }
  .con-head .dot.amber { background: #f0a340; }
  .con-head .dot.grn { background: #3fbf7f; }
  .con-title { margin-left: 8px; font-weight: 600; font-size: 13px; color: var(--text); }
  .con-title #con-phase { color: var(--gold); }
  .con-pct { margin-left: auto; font-weight: 700; font-size: 13px;
             color: var(--gold); font-variant-numeric: tabular-nums; }
  .con-progress { height: 6px; background: rgba(255,255,255,.06); position: relative; }
  .con-progress > i {
    display: block; height: 100%; width: 0;
    background: linear-gradient(90deg, var(--gold2), var(--gold));
    box-shadow: 0 0 12px rgba(244,196,48,.5);
    transition: width .25s ease;
  }
  .con-progress.indet > i {
    width: 32% !important; border-radius: 4px;
    animation: slide 1.1s ease-in-out infinite;
  }
  @keyframes slide { 0% { margin-left: -32%; } 100% { margin-left: 100%; } }
  .con-log {
    font-family: ui-monospace, "SFMono-Regular", "JetBrains Mono", Menlo,
                 Consolas, monospace;
    font-size: 12px; line-height: 1.55; color: var(--muted);
    padding: 12px 16px; height: 320px; max-height: 52vh; overflow-y: auto;
    background:
      repeating-linear-gradient(180deg, transparent 0 21px, rgba(255,255,255,.015) 21px 42px);
  }
  .con-log .ln { white-space: pre-wrap; word-break: break-word; }
  .con-log .ln::before { content: "› "; color: var(--faint); }
  .con-log .ln.ok { color: #cfe9d8; }
  .con-log .ln.ok::before { content: "✓ "; color: var(--green); }
  .con-log .ln.err { color: #f3b6b6; }
  .con-log .ln.err::before { content: "✗ "; color: var(--red); }
  .con-log .ln.dim { color: var(--faint); }
  .con-log .ln.phase {
    color: var(--gold); margin-top: 8px; font-weight: 600;
    border-top: 1px solid var(--line); padding-top: 8px;
  }
  .con-log .ln.phase::before { content: "» "; color: var(--gold2); }
  .con-log .ln.phase:first-child { margin-top: 0; border-top: 0; padding-top: 0; }
  .con-cursor::after {
    content: "▌"; color: var(--gold); animation: blink 1s steps(1) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
  .empty { text-align: center; color: var(--muted); padding: 40px 16px; }
  .err { text-align: center; color: var(--red); padding: 28px 16px; }
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="brand">
        <div class="logo">&#9876;</div>
        <div>
          <h1>Grand Exchange <span class="accent">Merch Scanner</span></h1>
          <div class="tag">Live RuneLite prices &middot; user-set item floor &middot; margin net of GE tax &middot; gp/24h capital-aware</div>
        </div>
      </div>
      <div class="controls">
        <span id="status">Ready &mdash; loading live data&hellip;</span>
        <div class="capbox">
          <div class="field">
            <label for="capital">Liquid capital</label>
            <input id="capital" type="text" value="700m" spellcheck="false"
                   autocomplete="off" title="how much gp you can deploy — e.g. 700m, 1.5b">
          </div>
          <div class="divider"></div>
          <div class="field">
            <label for="floor">Item floor</label>
            <input id="floor" type="text" value="500k" spellcheck="false"
                   autocomplete="off" title="ignore items trading below this — e.g. 500k, 1m">
          </div>
          <button id="confirm">Apply</button>
        </div>
        <button id="refresh">&#10227; Refresh</button>
      </div>
    </header>

    <div class="stats" id="stats"></div>

    <div class="card">
      <div class="tablescroll">
        <table>
          <thead>
            <tr>
              <th data-k="rank" class="num">#</th>
              <th data-k="name">Item</th>
              <th data-k="buy_price" class="num">Buy @</th>
              <th data-k="sell_price" class="num">Sell @ <span class="muted">(safe)</span></th>
              <th data-k="margin" class="num">Margin</th>
              <th data-k="roi" class="num">ROI</th>
              <th data-k="limit" class="num">Buy limit <span class="muted">/4h</span></th>
              <th data-k="units_24h" class="num">Units/24h</th>
              <th data-k="gp_24h" class="num">gp / 24h</th>
              <th data-k="vol_day" class="num">Vol/day</th>
              <th data-k="trend">Trend</th>
              <th class="nosort">30d</th>
              <th data-k="merch_score" class="num">Score</th>
              <th data-k="catalyst">News</th>
              <th data-k="reason">Reasoning</th>
            </tr>
          </thead>
          <tbody id="rows">
            <tr><td colspan="15" class="empty">Loading the live Grand Exchange&hellip; a full scan takes ~1&ndash;2 minutes.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="foot">
      <b>Buy @</b> is the current insta-sell price (place your buy offer here); <b>Sell @ (safe)</b>
      is the current insta-buy price &mdash; selling into it fills immediately and nets the margin shown after the 2% GE tax.<br>
      Prices are real trades observed by RuneLite and will not match the GE's lagged guide price.
      &ldquo;News&rdquo; badges are a best-effort keyword match against recent OSRS updates &mdash; a hint, not authoritative release data.
    </div>
  </div>

  <div id="overlay">
    <div class="console">
      <div class="con-head">
        <span class="dot"></span><span class="dot amber"></span><span class="dot grn"></span>
        <span class="con-title">live scan &mdash; <span id="con-phase">starting&hellip;</span></span>
        <span class="con-pct" id="con-pct"></span>
      </div>
      <div class="con-progress indet" id="con-progress"><i id="con-bar"></i></div>
      <div class="con-log" id="con-log"></div>
    </div>
  </div>

<script>
var DATA = [], sortKey = "margin", sortAsc = false, CAP = null, FLOOR = null;

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
function scoreColor(v) {
  var h = Math.round(8 + (Math.max(0,Math.min(100,v))/100)*130);  // red->green
  return "hsl(" + h + ",68%,52%)";
}
function fmtInt(n) { return Math.round(n||0).toLocaleString(); }
function sparkline(arr) {
  if (!arr || arr.length < 2) return '<span class="muted">&mdash;</span>';
  var w = 76, h = 24, pad = 2, n = arr.length;
  var min = Math.min.apply(null, arr), max = Math.max.apply(null, arr);
  var rng = (max - min) || 1;
  var xy = function(v, i) {
    var x = pad + (i / (n - 1)) * (w - 2 * pad);
    var y = pad + (1 - (v - min) / rng) * (h - 2 * pad);
    return [x, y];
  };
  var pts = arr.map(function(v, i){ var p = xy(v, i); return p[0].toFixed(1) + "," + p[1].toFixed(1); });
  var up = arr[n-1] >= arr[0];
  var col = up ? "#3fbf7f" : "#ef5b5b";     // matches --green / --red
  var last = xy(arr[n-1], n-1);
  var area = "0," + h + " " + pts.join(" ") + " " + (w) + "," + h;
  return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">'
    + '<polyline points="' + area + '" fill="' + col + '" opacity="0.10" stroke="none"/>'
    + '<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + col
      + '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
    + '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="1.7" fill="' + col + '"/>'
    + '</svg>';
}

function render() {
  var rows = DATA.slice();
  rows.forEach(function(r, i){ r.rank = i + 1; });   // rank from server order
  if (sortKey !== "rank") {
    rows.sort(function(a, b){
      var x = a[sortKey], y = b[sortKey];
      if (typeof x === "string" || typeof y === "string") {
        x = (x||"").toString().toLowerCase(); y = (y||"").toString().toLowerCase();
        return sortAsc ? (x<y?-1:x>y?1:0) : (x<y?1:x>y?-1:0);
      }
      x = x||0; y = y||0; return sortAsc ? x-y : y-x;
    });
  } else if (sortAsc) { rows.reverse(); }

  var tb = document.getElementById("rows");
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="15" class="empty">Nothing scored above the price floor. Try again later.</td></tr>';
    return;
  }
  var html = "";
  rows.forEach(function(r){
    var tcls = "pill t-" + esc(r.trend);
    var cat = r.catalyst
      ? '<span class="news" title="' + esc(r.catalyst) + '">&#128227; news</span>'
      : '<span class="muted">&mdash;</span>';
    var sc = Math.round(r.merch_score||0);
    html += "<tr>"
      + '<td class="num rank">' + r.rank + "</td>"
      + '<td class="name">' + esc(r.name) + "</td>"
      + '<td class="num buy">' + gp(r.buy_price) + "</td>"
      + '<td class="num sell">' + gp(r.sell_price) + "</td>"
      + '<td class="num">' + gp(r.margin) + "</td>"
      + '<td class="num">' + (r.roi||0).toFixed(1) + "%</td>"
      + '<td class="num muted">' + fmtInt(r.limit) + "</td>"
      + '<td class="num muted">' + fmtInt(r.units_24h) + "</td>"
      + '<td class="num sell">' + gp(r.gp_24h) + "</td>"
      + '<td class="num muted">' + fmtInt(r.vol_day) + "</td>"
      + '<td><span class="' + tcls + '">' + esc(r.trend) + "</span></td>"
      + '<td class="sparkcell" title="30-day price trend">' + sparkline(r.spark) + "</td>"
      + '<td class="num"><div class="scorewrap">'
        + '<span class="scorebar"><i style="width:' + sc + '%;background:'
        + scoreColor(sc) + '"></i></span>'
        + '<span class="scoreval">' + sc + "</span></div></td>"
      + "<td>" + cat + "</td>"
      + '<td class="reason">' + esc(r.reason) + "</td>"
      + "</tr>";
  });
  tb.innerHTML = html;
  paintHeaders();
}

function paintHeaders() {
  document.querySelectorAll("thead th").forEach(function(th){
    var old = th.querySelector(".arrow"); if (old) old.remove();
    if (th.getAttribute("data-k") === sortKey) {
      var a = document.createElement("span");
      a.className = "arrow"; a.textContent = sortAsc ? "▲" : "▼";
      th.appendChild(a);
    }
  });
}
function renderStats() {
  var el = document.getElementById("stats");
  if (!DATA.length) { el.innerHTML = ""; return; }
  var best = DATA[0];
  var avg = DATA.reduce(function(s,r){ return s + (r.merch_score||0); }, 0) / DATA.length;
  var withNews = DATA.filter(function(r){ return r.catalyst; }).length;
  var topGp = DATA.reduce(function(m,r){ return Math.max(m, r.gp_24h||0); }, 0);
  el.innerHTML =
      (CAP ? stat("Liquid capital", gp(CAP), true) : "")
    + (FLOOR ? stat("Item floor", gp(FLOOR)) : "")
    + stat("Picks", DATA.length)
    + stat("Top pick", esc(best.name), true)
    + stat("Best gp / 4h", gp(topGp), true)
    + stat("Avg score", avg.toFixed(0))
    + stat("With news", withNews);
}
function stat(k, v, gold) {
  return '<div class="stat"><div class="k">' + k + '</div><div class="v'
    + (gold ? " gold" : "") + '">' + v + '</div></div>';
}

function setSort(k) {
  if (sortKey === k) sortAsc = !sortAsc;
  else { sortKey = k; sortAsc = (k === "name"); }
  render();
}
document.querySelectorAll("thead th").forEach(function(th){
  var k = th.getAttribute("data-k");
  if (!k) return;                       // 30d sparkline column isn't sortable
  th.addEventListener("click", function(){ setSort(k); });
});

var ES = null;
function setBusy(on) {
  document.getElementById("refresh").disabled = on;
  document.getElementById("confirm").disabled = on;
  document.getElementById("overlay").classList.toggle("on", on);
}
function conReset() {
  document.getElementById("con-log").innerHTML = "";
  document.getElementById("con-phase").textContent = "starting…";
  document.getElementById("con-pct").textContent = "";
  document.getElementById("con-bar").style.width = "0";
  document.getElementById("con-progress").classList.add("indet");
}
function conLog(msg, level) {
  var log = document.getElementById("con-log");
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
  var pr = document.getElementById("con-progress");
  pr.classList.remove("indet");
  var pct = total ? Math.round((done / total) * 100) : 0;
  document.getElementById("con-bar").style.width = pct + "%";
  document.getElementById("con-pct").textContent = pct + "%";
}

function scan() {
  var st = document.getElementById("status");
  var cap = document.getElementById("capital").value.trim();
  var floor = document.getElementById("floor").value.trim();
  if (ES) { ES.close(); ES = null; }
  setBusy(true);
  conReset();
  st.innerHTML = "Scanning the live Grand Exchange&hellip;";
  var t0 = Date.now();
  var q = [];
  if (cap) q.push("capital=" + encodeURIComponent(cap));
  if (floor) q.push("floor=" + encodeURIComponent(floor));
  var url = "/api/scan/stream" + (q.length ? "?" + q.join("&") : "");
  var es = new EventSource(url);
  ES = es;
  var finished = false;

  es.onmessage = function(ev) {
    var d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.kind === "phase") {
      document.getElementById("con-phase").textContent = d.label;
      conLog(d.label, "phase");
    } else if (d.kind === "log") {
      conLog(d.msg, d.level);
    } else if (d.kind === "progress") {
      conProgress(d.done, d.total);
    } else if (d.kind === "result") {
      finished = true; es.close(); ES = null;
      conProgress(1, 1);
      conLog("done", "ok");
      DATA = d.rows || [];
      CAP = d.capital || null;
      FLOOR = d.floor || null;
      if (CAP) document.getElementById("capital").value = gp(CAP);
      if (FLOOR) document.getElementById("floor").value = gp(FLOOR);
      sortKey = "margin"; sortAsc = false;
      render(); renderStats();
      var secs = ((Date.now()-t0)/1000).toFixed(0);
      st.innerHTML = "<b>" + DATA.length + "</b> picks &middot; live "
        + new Date().toLocaleTimeString() + " &middot; " + secs + "s";
      setTimeout(function(){ setBusy(false); }, 450);
    } else if (d.kind === "error") {
      finished = true; es.close(); ES = null;
      failScan(st, d.error);
    }
  };
  es.onerror = function() {
    if (finished) return;               // normal close after result
    es.close(); ES = null;
    failScan(st, "connection lost (is the scanner still running?)");
  };
}
function failScan(st, msg) {
  conLog("scan failed: " + msg, "err");
  document.getElementById("rows").innerHTML =
    '<tr><td colspan="15" class="err">Scan failed: ' + esc(msg)
    + '. Check the terminal running the scanner.</td></tr>';
  st.textContent = "Scan failed.";
  setTimeout(function(){ setBusy(false); }, 900);
}
document.getElementById("refresh").addEventListener("click", scan);
document.getElementById("confirm").addEventListener("click", scan);
document.getElementById("capital").addEventListener("keydown", function(e){
  if (e.key === "Enter") scan();
});
window.addEventListener("load", scan);   // refresh all data on open
</script>
</body>
</html>"""


def serve_gui(params, port=8777):
    """Launch the local OSRS-themed web app. Every /api/scan hits the live
    API fresh; the page auto-scans on load and on each Refresh click."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            parts = self.path.split("?", 1)
            path = parts[0]
            query = urllib.parse.parse_qs(parts[1]) if len(parts) > 1 else {}
            if path in ("/", "/index.html"):
                self._send(200, GUI_PAGE.encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if path in ("/api/scan", "/api/scan/stream"):
                # Capital is set live from the GUI input; fall back to launch value.
                run_params = dict(params)
                cap = (query.get("capital") or [None])[0]
                if cap:
                    try:
                        run_params["bankroll"] = parse_gp(cap)
                    except ValueError:
                        pass
                floor = (query.get("floor") or [None])[0]
                if floor:
                    try:
                        run_params["min_price"] = parse_gp(floor)
                    except ValueError:
                        pass
                print(f"gui: live scan requested "
                      f"(capital {fmt_gp(run_params['bankroll'])}, "
                      f"floor {fmt_gp(run_params.get('min_price', 500_000))})...",
                      file=sys.stderr)
                if path == "/api/scan/stream":
                    self._scan_stream(run_params)
                else:
                    self._scan_json(run_params)
                return
            self._send(404, b"not found", "text/plain")

        def _scan_json(self, run_params):
            try:
                rows = run_scan(run_params)
                payload = {"rows": rows,
                           "capital": run_params.get("bankroll"),
                           "floor": run_params.get("min_price", 500_000),
                           "ts": datetime.now(timezone.utc).isoformat()}
                self._send(200, json.dumps(payload).encode("utf-8"),
                           "application/json")
                print(f"gui: returned {len(rows)} picks", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"gui: scan error: {e}", file=sys.stderr)
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"),
                           "application/json")

        def _scan_stream(self, run_params):
            """Run the scan and stream progress as Server-Sent Events so the
            GUI can show a live log + progress bar instead of a spinner."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit(kind, **data):
                data["kind"] = kind
                chunk = f"data: {json.dumps(data)}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()

            try:
                rows = run_scan(run_params, on_event=emit)
                emit("result", rows=rows,
                     capital=run_params.get("bankroll"),
                     floor=run_params.get("min_price", 500_000),
                     ts=datetime.now(timezone.utc).isoformat())
                print(f"gui: streamed {len(rows)} picks", file=sys.stderr)
            except (BrokenPipeError, ConnectionResetError):
                print("gui: client disconnected mid-scan", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"gui: scan error: {e}", file=sys.stderr)
                try:
                    emit("error", error=str(e))
                except OSError:
                    pass

        def log_message(self, *_a):  # quiet default request logging
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"OSRS Merch Scanner GUI -> {url}", file=sys.stderr)
    print("(the browser will open; each Refresh pulls the live GE. Ctrl-C to stop.)",
          file=sys.stderr)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.", file=sys.stderr)
    finally:
        httpd.server_close()


def _print_text(rows, sort_key):
    """The classic terminal table, now including the composite score."""
    hdr = (f"{'item':<28}{'price':>9}{'margin':>9}{'roi':>7}{'limit/4h':>9}"
           f"{'gp/24h':>9}{'vol/d':>9}{'all%ile':>8}{'z30':>7}"
           f"{'trend':>9}{'move':>7}{'score':>7}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name'][:27]:<28}{fmt_gp(r['now']):>9}"
              f"{fmt_gp(r['margin']):>9}{r['roi']:>6.1f}%{r['limit']:>9,}"
              f"{fmt_gp(r['gp_24h']):>9}{r['vol_day']:>9,.0f}"
              f"{r['rank_all'] * 100:>7.0f}%"
              f"{r['z30']:>+7.2f}{r['trend']:>9}{r['volatility']:>6.1f}%"
              f"{r['merch_score']:>7.0f}")
    print(f"\ntop {len(rows)} by composite merch score.")
    print("margin is net of the 2% GE tax (capped 5m/item).")
    print("gp/24h = margin x units fillable in 24h (buy limit x6, volume, capital).")
    print("z30 = standard deviations from the 30-day mean. Negative is cheap.")
    print("'bounce' = 30d downtrend that turned up in the last week.\n")
    for r in rows:
        tag = "  [news]" if r.get("catalyst") else ""
        print(f"  {r['name']}{tag}: {r['reason']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="ITEM",
                    help="inspect one item and exit; skips the full scan")
    ap.add_argument("--bankroll", type=parse_gp, default=None,
                    help="liquid capital / buying power (e.g. 700m, 1.5b); "
                         "prompted if omitted")
    ap.add_argument("--top", type=int, default=50, help="rows to print")
    ap.add_argument("--shortlist", type=int, default=160,
                    help="items to pull history for; ~0.85s each")
    ap.add_argument("--sort", default="merch_score",
                    choices=["merch_score", "margin", "swing", "flip", "roi",
                             "gp_24h", "rank_all", "volatility", "vol_day",
                             "limit"])
    ap.add_argument("--mode", default="any", choices=["any", "buy", "avoid"],
                    help="buy = cheap half of own range; avoid = expensive half")
    ap.add_argument("--min-deploy-frac", type=float, default=0.01,
                    help="item must absorb this fraction of bankroll per 4h")
    ap.add_argument("--min-price", type=int, default=500_000,
                    help="ignore items trading below this price (default 500k)")
    ap.add_argument("--max-spread-pct", type=float, default=25.0)
    ap.add_argument("--stale-hours", type=float, default=12.0)
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--csv", help="also write results here (text mode only)")
    ap.add_argument("--scan", action="store_true",
                    help="force the classic terminal scan instead of the GUI")
    ap.add_argument("--port", type=int, default=8777,
                    help="port for the GUI web app (default 8777)")
    args = ap.parse_args()

    if args.check:
        check_item(args.check)
        return

    # Liquid capital: use --bankroll if given, else prompt (fallback 700m).
    bankroll = args.bankroll
    if bankroll is None:
        bankroll = prompt_capital(700_000_000)
    print(f"liquid capital: {fmt_gp(bankroll)}", file=sys.stderr)

    params = {
        "bankroll": bankroll,
        "min_deploy_frac": args.min_deploy_frac,
        "max_spread_pct": args.max_spread_pct,
        "stale_hours": args.stale_hours,
        "min_price": args.min_price,
        "shortlist": args.shortlist,
        "sleep": args.sleep,
        "top": args.top,
        "mode": args.mode,
    }

    # GUI-first: bare invocation launches the themed web app.
    if not args.scan:
        serve_gui(params, port=args.port)
        return

    print("stage 1: bulk scan of every tradeable item...", file=sys.stderr)
    print(f"stage 2: pulling history for up to {args.shortlist} items "
          f"(~{args.shortlist * (args.sleep + 0.25):.0f}s)...", file=sys.stderr)

    def progress(done, total):
        if done % 25 == 0 or done == total:
            print(f"  {done}/{total}", file=sys.stderr)

    rows = run_scan(params, progress=progress)
    if not rows:
        print("nothing scored. loosen --min-deploy-frac / --stale-hours "
              "or raise --shortlist.")
        return

    if args.sort != "merch_score":
        rows.sort(key=lambda r: r[args.sort], reverse=True)

    _print_text(rows, args.sort)

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nfull results -> {args.csv}")


if __name__ == "__main__":
    main()
