# OSRS Merch Scanner — clan app

Ranks the top Grand Exchange flip opportunities from the wiki's real-time
prices. One shared scan serves the whole clan; each member's capital and price
floor are applied instantly at read time, without touching the wiki again.

```
  WRITER (only wiki caller)        DB (snapshots)           READER (stateless)
  scan_market() every 10 min  ─▶  scans + picks tables  ─▶  GET /api/picks?capital=&floor=
  + on-demand (single-flight)                               personalised per request
```

## Files

| File | Role |
|---|---|
| `scanner.py` | The scanning core. `scan_market()` is capital-agnostic and expensive; `rank_for()` personalises a stored snapshot and is free. |
| `db.py` | Snapshot storage over SQLAlchemy Core — SQLite locally, Postgres by changing `DATABASE_URL`. |
| `worker.py` | The writer: APScheduler interval job, single-flight locking, SSE fan-out. |
| `app.py` | FastAPI reader + login + static page. Makes zero wiki calls. |
| `static/index.html` | The original UI, rewired to the API. No framework. |
| `config.py` | Every env var, read once. |
| `osrs_merch_scan.py` | The original standalone CLI/GUI tool. Still works, unchanged in behaviour. |

## Run it locally

```fish
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py            # http://127.0.0.1:8777
```

The first launch has no snapshot, so the writer scans immediately (~1–2 min for
the default 160-item shortlist) and the page shows the live progress console.
After that it rescans every 10 minutes, and **Refresh** forces one on demand.

Changing capital or the item floor and pressing **Apply** does *not* scan — it
re-ranks the snapshot already in the database. That is the point of the split.

To try it quickly without a long first scan, shorten the shortlist:

```fish
env SHORTLIST=25 .venv/bin/python app.py
```

## Configuration

All optional; the defaults suit a local single-instance run. Gp values accept
`700m` / `1.5b` shorthand.

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///osrs_scanner.db` | `postgresql+psycopg://…` to switch (uncomment psycopg in `requirements.txt`). |
| `SCAN_INTERVAL_MIN` | `10` | Scheduled scan interval. |
| `SCAN_ON_STARTUP` | `1` | Scan at boot only when the database has no snapshot. |
| `KEEP_SCANS` | `50` | Older snapshots are pruned after each scan. |
| `REFERENCE_BANKROLL` | `50m` | The **smallest** capital any member might enter — see below. |
| `GLOBAL_FLOOR` | `100k` | The **lowest** floor any member might pick. |
| `SHORTLIST` | `160` | Items given the expensive per-item history call. |
| `SLEEP` | `0.6` | Per-item delay. Keep it polite. |
| `DEFAULT_CAPITAL` / `DEFAULT_FLOOR` | `700m` / `500k` | What the UI starts on. |
| `CLAN_PASSWORD` | *(unset)* | Unset means **no auth**. Fine on localhost; set it before exposing the port. |
| `SECRET_KEY` | derived from the password | Set it to keep sessions valid across password changes. |
| `HOST` / `PORT` | `127.0.0.1` / `8777` | |
| `UA` | `merch-scanner - nekrosisx` | The wiki blocks the default requests agent. Rarely worth changing. |

`REFERENCE_BANKROLL` is counter-intuitive: it feeds `prefilter`'s capacity test
(`limit × high ≥ bankroll × min_deploy_frac`), which gets **stricter** as the
bankroll grows. A large value would quietly drop the small and mid-cap items a
smaller member needs, so it must be the smallest plausible capital — the
shortlist is then a superset that serves everyone.

## Where the prices come from

Everything numeric originates at the OSRS Wiki's real-time prices API,
`https://prices.runescape.wiki/api/v1/osrs`, which republishes trades observed
by RuneLite clients. Four endpoints, plus the news feed:

| Source | Used for | Cost |
|---|---|---|
| `/mapping` | Item names and the 4-hour GE buy limit. | 1 bulk call |
| `/latest` | Current insta-buy (`high`) / insta-sell (`low`) and their timestamps. | 1 bulk call |
| `/1h` | Last hour's traded volume, as a liveness check. | 1 bulk call |
| `/timeseries?timestep=24h` | A year of daily candles → trend, volatility, percentile. | **1 call per shortlisted item** |
| `latest_news.rss` | Keyword-matched content catalysts. | 1 call, failures ignored |

Two consequences worth remembering. These are *real trades*, so they will not
match the GE's lagged guide price. And the per-item `/timeseries` call is the
only part that scales with item count — it is the reason `SHORTLIST` exists.

## How the shortlist is built

A two-stage funnel. Stage 1 is bulk and nearly free; stage 2 is per-item and
expensive, so stage 1's whole job is to decide who deserves stage 2.

**Stage 1 — `prefilter()`.** One pass over every tradeable item. Drop it unless
all of the following hold:

| Gate | Rule | Why |
|---|---|---|
| Tradeable | `limit > 0` and present in `/latest` | No limit means no GE flip. |
| Real spread | `high` and `low` both set, `high > low` | |
| Price floor | `low >= GLOBAL_FLOOR` (100k) | Serious items only. Personal floors are applied later. |
| Fresh | `now - min(highTime, lowTime) <= 12h` | A spread nobody has traded is fiction. |
| Sane spread | `(high - low) / low <= 25%` | Wider is manipulation or a dead book. |
| Profitable | `net_margin(high, low) > 0` | Post-tax, not gross. |
| Alive | `/1h` volume `> 0` | Traded at all in the last hour. |
| Capacity | `limit * high >= REFERENCE_BANKROLL * 0.01` | Can absorb ≥1% of the reference capital per window. |

Survivors are ranked by raw gp opportunity per window:

```
prefilter_score = net_margin * min(limit, hourly_volume * 4)
```

**Stage 2 — `enrich()`.** Take the top `SHORTLIST` (160) of that ranking, and
only those, then spend one `/timeseries` call each on a year of daily candles.
Items with fewer than 45 days of history are dropped — which is why a 160-item
shortlist typically stores ~155–159.

### Why 160

It is a **request budget**, not a claim about how many items are worth
trading. Stage 2 costs one wiki call per item at `SLEEP` seconds apart, so
wall-clock scan time is roughly `SHORTLIST * (SLEEP + latency)` — ~100 s at the
defaults. That has to finish comfortably inside `SCAN_INTERVAL_MIN` (10 min)
while staying polite to a volunteer-run API.

Because the cut is by `prefilter_score`, raising `SHORTLIST` only *appends*
progressively weaker candidates; it does not reorder the top. Lower it (e.g.
`SHORTLIST=25`) for a fast iteration loop. Raise it only if you have evidence
that genuine picks are being truncated — check whether the last-ranked items in
a scan ever surface near the top of `/api/picks`.

## Calculation logic

Every formula below is percentile-normalised *within the current candidate
set*, not against absolutes. A score of 80 means "top fifth of this scan", not
"good in some universal sense" — so scores are not comparable across scans of
different sizes.

**Per-unit economics** (writer, capital-independent):

```
tax      = min(high * 0.02, 5_000_000)      # GE sale tax, capped per item
margin   = high - low - tax                  # net gp per unit
roi      = margin / low * 100
buy_price = low, sell_price = high, sell_net = high - tax
```

#### GE tax — confirmed applied ✅

Verified 2026-07-24 by tracing the code and checking the rate against the
wiki's Grand Exchange page. **Every profit figure the app reports is net of the
GE sale tax.** There is no gross margin anywhere downstream.

The tax enters once, in `net_margin()` (`scanner.py:105`), and everything
inherits from it:

```
prefilter() → margin = net_margin(high, low)      # taxed here, once
   ├─ roi      = margin / low * 100                # net
   ├─ gp_24h   = margin * units_24h  (rank_for)    # net
   └─ prefilter_score = margin * units             # net — even the shortlist
                                                   #   cut uses after-tax gp
scan_market() → tax      = min(high * 0.02, 5m)    # shown per row
                sell_net = high - tax              # sell_net - buy_price == margin
```

Because `prefilter_score` is itself net, items that only look profitable
before tax are eliminated before the expensive history call — a positive
`margin` is a hard gate (`scanner.py:165`).

| Rule | Wiki | Code | Status |
|---|---|---|---|
| Rate | 2% (raised from 1% on 2025-05-29) | `high * 0.02` | ✅ |
| Cap | 5,000,000 gp per item | `min(…, 5_000_000)` | ✅ — 4 items in the 2026-07-24 scan hit it |
| Charged on | Sale only, not purchase | Applied to `high`, never `low` | ✅ |
| Sub-50 gp exempt | Tax rounds down to 0 | n/a — `GLOBAL_FLOOR` is 100k | ✅ not reachable |
| Rounding | Rounds **down** | `round()` (nearest) | ⚠️ ≤1 gp per unit; immaterial |
| Exempt items | Bonds, tools, low-level food… | **Not modelled** | ⚠️ see below |

**Known gap — exempt items are taxed anyway.** The wiki exempts a list of
items (Old school bonds, basic tools, low-level food and consumables) that the
scanner charges tax on regardless. The only one that clears the 100k floor in
practice is the **Old school bond**: in the 2026-07-24 scan it was assessed
240k of tax on a 12.0m sale, reporting a 260k margin when the true untaxed
margin is 500k — a **48 % understatement**. The bond still ranked inside the
top 20, so the effect is that it is *under*-ranked, never over-ranked.

This errs conservative — the app never overstates profit — so it is recorded
rather than patched. Fixing it means a hardcoded exempt-item id set, since the
prices API does not expose exemption status.

**History metrics**, from 24h candles with `mid = (avgHigh + avgLow) / 2`:

| Metric | Definition |
|---|---|
| `rank_all` / `rank90` | Fraction of all / last-90 midpoints at or below today's. 0.0 = cheapest ever. |
| `z30` | `(now - mean(30d)) / pstdev(30d)`. Negative = below its recent mean. |
| `slope30` / `slope7` | Least-squares slope of *log* price, as % per day. |
| `volatility` | Stdev of daily log returns over 90d, in percent. The merch fuel. |
| `vol_day` | Mean daily traded volume over 30d. |
| `trend` | `bounce` if `slope30 < -0.2` and `slope7 > 0.2`; else `rising` / `falling` at `±0.2`; else `flat`. |

**Capital scaling** (reader, per request — this is what makes two members see
different numbers from one snapshot):

```
units_24h = min(limit * 6, vol_day, capital / buy_price)     # 6 GE windows/day
gp_24h    = margin * units_24h
```

**Composite scores.** With `thr`, `liq`, `vol`, `value` as percentile ranks of
`gp_24h`, `log1p(vol_day)`, `volatility` and `1 - rank_all` respectively:

```
flip  = 100 * (0.60*thr + 0.40*liq)

swing = 100 * min(1, (0.40*(1-rank_all) + 0.25*revert + 0.20*vol + 0.15*liq)
                     * knife * bonus)
        revert = clamp(-z30, 0, 2) / 2
        knife  = 0.65 if trend == falling else 1.0
        bonus  = 1.15 if trend == bounce  else 1.0

merch_score = 100 * clamp(0.30*thr + 0.20*liq + 0.15*vol + 0.20*value
                          + trend_adj + catalyst, 0, 1)
        trend_adj = bounce +0.10, rising +0.08, flat 0, falling -0.10
        catalyst  = min(0.15, 0.05 * age_weight * words_matched)
                    age_weight = 1.0 (≤30d), 0.5 (≤90d), 0.2 (older)
```

`merch_score` is the sort key for `/api/picks`. `flip` and `swing` are shown
alongside as the two strategy-specific readings of the same row.

### Tuning log

The constants above were set by judgement, not by backtest. Record changes here
so later measurements have a baseline to compare against.

| Date | Constant | From → To | Rationale / observed effect |
|---|---|---|---|
| 2026-07-24 | — | — | Baseline recorded at migration. `SHORTLIST=160`, `min_deploy_frac=0.01`, `max_spread_pct=25`, `stale_hours=12`, weights as above. First scan: 158/160 items retained, ~100 s. |
| 2026-07-24 | `SHORTLIST` | 160 → 400 (test only, **not adopted**) | See "Tail test" below. 160 kept. |
| 2026-07-24 | GE tax | — (audit, no change) | Confirmed applied end-to-end; 2% / 5m cap match the wiki. Two gaps logged: `round()` vs floor (≤1 gp), and exempt items not modelled (bond understated 48%). See "GE tax — confirmed applied". |

#### Tail test — is `SHORTLIST=160` truncating real picks? (2026-07-24)

One scan at `SHORTLIST=400`, ranked twice from the same fetched data: `head` =
items with prefilter rank < 160 (what production sees), `full` = all of them.
Read at the UI defaults (700m capital, 500k floor).

- **400 never binds.** Only **292** items passed the prefilter at all, so the
  effective ceiling today is 292, not 400. 289 survived the 45-day history
  gate (157 head + 132 tail).
- **The tail barely places.** Of the top 10 by `merch_score`, **0** came from
  the tail. Top 20: **1** — *Ursine chainmace (u)*, at prefilter rank **160**,
  i.e. the item immediately past the cut. Top 50: 6, none above rank 228.
- **Top-20 overlap was 18/20**, and one of the two changes (*Justiciar
  chestguard*) was a head item promoted by re-scoring, not a tail arrival. So
  widening the shortlist changed exactly **one** genuine recommendation.
- **Cost:** 186 s vs ~100 s — 1.8× the wiki calls and wall time.

**Conclusion: keep 160.** The only tail entrant sat on the boundary, which is
what a boundary looks like when it is drawn in roughly the right place. Nothing
from rank 170+ reached the top 20. Raising to ~200 would buy the boundary zone
for ~30 % more scan time; that is the only change the data would support, and
it is not currently worth it.

**Side finding — scores inflate with set size.** The 20 items in both rankings
drifted **+1.67 `merch_score` on average** (max +3.4, *Ghrazi rapier*) purely
from adding 132 lower-quality items below them, since percentiles are taken
within the candidate set. This confirms the caveat above in hard numbers:
**`merch_score` is not comparable across scans with different `SHORTLIST`**.
Any future weight experiment must hold `SHORTLIST` fixed or compare ranks
rather than scores.

Reproduce: the script lives in the scratchpad, not the repo — it monkeypatches
`scanner.prefilter` to record the ranking from the same call `scan_market`
makes, then calls `rank_for` on the head subset and the full set.

Open questions still worth measuring:

- ~~Do items past rank 160 reach the top 20?~~ **Answered above: essentially
  no.** Re-run occasionally — 292 survivors is one day's market, and a quiet
  or volatile week could move the ceiling.
- Is the `knife` penalty on `falling` too harsh, given `bounce` already
  rewards the reversal one step later?
- `volatility` carries weight in all three scores. Is that triple-counting?
- Catalyst matching is keyword-based and unvalidated — measure its hit rate
  against real update-driven price moves before trusting the ≤0.15 bonus.

Snapshot history is retained (`KEEP_SCANS=50`, `GET /api/scans`), so a
before/after comparison across a weight change is possible without new
scraping.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/picks?capital=&floor=&top=&mode=` | Personalised picks from the latest snapshot. `503` until the first scan lands. |
| `GET /api/status` | Snapshot age, next scheduled run, whether a scan is in flight. |
| `POST /api/refresh` | Trigger a scan. Single-flight: returns `started: false` if one is already running or if it was debounced (30 s). |
| `GET /api/refresh/stream` | SSE `phase` / `log` / `progress` / `result` events, replayed from the start of the current scan. |
| `GET /api/scans?limit=` | Snapshot history. |
| `GET /api/item/{id}/history?limit=` | One item's stored fields across recent scans — feeds the drill-down's "last few scans" chart. Bounded by `KEEP_SCANS`, so it's roughly the last `SCAN_INTERVAL_MIN * KEEP_SCANS` of wall time, not long history. |
| `GET /healthz` | Open (no auth) — snapshot presence and age. |

The wiki is only ever contacted by the writer, on a schedule, with the custom
user agent and the existing retry/backoff. Readers never call it.

## Frontend (static/index.html)

Still a single vanilla-JS file, no framework, no build step — filtering and
the drill-down are ~250 extra lines in the same style as the rest of the page.

- **Filter bar** — search (`/` to focus), a buy-dips/avoid mode toggle (wired
  to the `rank_all` field already in each row), trend chips, score/volatility
  thresholds, a news-only toggle, and a column-visibility menu for the fields
  the table doesn't show by default (`limit`, `vol_day`, `units_24h`, `spark`,
  `catalyst`, `reason`). Everything filters the **already-fetched** snapshot
  client-side — the page now requests `top=200` up front so a filter can reach
  every item in the snapshot, not just the first 50 by whatever sort was active.
  A result-count bar with removable chips shows what's currently narrowing the
  table; **Reset** clears it. Capital, floor, and all filters persist in
  `localStorage` across reloads.
- **Drill-down** — click a row (not a sort arrow) for a modal with the tax
  breakdown (buy/sell/tax/net margin), `merch`/`flip`/`swing` score bars with
  an optional expand into the underlying percentile components
  (`sc_throughput`, `sc_liquidity`, `sc_volatility_rank`, `sc_value`), a
  range-position gauge from `rank_all`/`z30`, the 30-day spark blown up, and a
  small bar chart from the new `/api/item/{id}/history` endpoint. `Esc` closes
  it, `j`/`k` step to the previous/next row in the current sort and filter.
- **Mobile** — below 900px the table hides secondary columns
  (`limit`, `vol_day`, `units_24h`, `spark`, `catalyst`, `reason`) via CSS; the
  drill-down is the way to see the rest.

None of this changed the reader's zero-network-call guarantee above — every
control re-slices data already sitting in the browser, except opening the
drill-down, which makes one lightweight call to the new history endpoint.

## Tests

```fish
.venv/bin/python tests/test_parity.py   # new split == the original run_scan
.venv/bin/python tests/test_app.py      # storage, personalisation, single-flight, SSE, auth
```

Both are fully offline: `tests/fake_wiki.py` is a deterministic stand-in for
the API, and `tests/_legacy_monolith.py` is a frozen pre-refactor copy of the
tool used as the parity oracle (do not edit it).

## Honesty notes

Prices are real trades observed by RuneLite and will **not** match the GE's
lagged guide price. "News" badges are best-effort keyword matches against the
OSRS news RSS — a hint, not authoritative release data.

Every margin, ROI and gp/24h figure is **net of the 2% GE sale tax** (capped at
5m/item) — audited 2026-07-24, see "GE tax — confirmed applied". The one
inaccuracy is in the conservative direction: tax-exempt items such as the Old
school bond are taxed anyway, so their profit is understated.

## Deploying

Deliberately not set up yet: this runs locally while the scoring is being
trialled. When it moves to a host, the shape is one always-on service running
the API with the in-process scheduler, plus managed Postgres via
`DATABASE_URL`, `CLAN_PASSWORD` set, and `HOST=0.0.0.0`.
