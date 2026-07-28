# OSRS Merch Desk — clan app

Ranks Grand Exchange flip opportunities from the wiki's real-time prices.
One shared scan serves the whole clan; each member's capital and price floor
are applied instantly at read time, without touching the wiki again.

```
  WRITER (only wiki caller)        DB (snapshots)           READER (stateless)
  scan every 10 min (FAST_SCAN) ─▶ scans + picks + history ─▶ GET /api/picks?capital=&floor=
  + on-demand (single-flight)                               personalised + analysis per request
```

Default scan path is **FAST_SCAN**: three bulk wiki calls + DB-backed daily
history (run `backfill_history.py` once first). Legacy shortlist+`/timeseries`
path remains available with `FAST_SCAN=0`.

## Files

| File | Role |
|---|---|
| `scanner.py` | Scanning core. `scan_market` / `scan_market_fast` are capital-agnostic; `rank_for` personalises a stored snapshot. |
| `db.py` | Snapshots + Phase-2 `items` / `item_daily_history` / `item_snapshots`. SQLite locally, Postgres via `DATABASE_URL`. |
| `worker.py` | Writer: APScheduler, single-flight, SSE fan-out, daily history rollover (+ timeseries gap-fill). |
| `app.py` | FastAPI reader + login + Merch Desk UI. Makes zero wiki calls. |
| `static/index.html` | Merch Desk UI (Emerald Ladder, dark default + light toggle). No framework. |
| `static/archive/index_classic.html` | Pre-redesign UI, archived. |
| `config.py` | Every env var, read once. |
| `analysis.py` | Deterministic dip/flip/risk heuristics over `rank_for` rows (also exposed on `/api/picks`). |
| `predict_cli.py` | Terminal command for the prediction layer. |
| `backfill_history.py` | One-time resumable `/timeseries` → `item_daily_history` backfill. |
| `osrs_merch_scan.py` | Original standalone CLI/GUI tool. |

## Run it locally

```fish
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# First time (or after wiping the DB), backfill daily history for FAST_SCAN:
.venv/bin/python backfill_history.py
.venv/bin/python app.py            # http://127.0.0.1:8777
```

Without a backfill, `FAST_SCAN` refuses to score (visible error) instead of
serving an empty “ok” snapshot. For a quick legacy smoke test:

```fish
env FAST_SCAN=0 SHORTLIST=25 .venv/bin/python app.py
```

Changing capital or floor re-ranks the snapshot already in the database —
it does not scan. Only **Scan** hits the wiki.
## Configuration

All optional; the defaults suit a local single-instance run. Gp values accept
`700m` / `1.5b` shorthand.

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///osrs_scanner.db` | `postgresql+psycopg://…` for managed Postgres (`psycopg` is in `requirements.txt`). |
| `SCAN_INTERVAL_MIN` | `10` | Scheduled scan interval. |
| `SCAN_ON_STARTUP` | `1` | Scan at boot only when the database has no ok snapshot. |
| `KEEP_SCANS` | `50` | Older `scans`/`picks` pruned after each scan. |
| `FAST_SCAN` | `1` | All-items path from DB history. Requires backfill (`MIN_HISTORY_READY`). |
| `MIN_HISTORY_READY` | `50` | Refuse FAST_SCAN until this many items have ≥`MIN_HISTORY_DAYS` history. |
| `MIN_HISTORY_DAYS` | `45` | Same bar as `derive_trend_metrics`. |
| `MIN_SCORED_ITEMS` | `1` | Below this, a finished scan is stored as `degraded`, not `ok`. |
| `READY_MAX_AGE_MIN` | `30` | `/healthz` returns 503 when the ok snapshot is older than this. |
| `SNAPSHOT_KEEP_DAYS` | `3` | Raw 10-min `item_snapshots` retention. |
| `ROLE` | `all` | `all` = API+writer; `api` = reader only; `writer` = scheduler only (`python app.py` with `ROLE=writer`). |
| `REFERENCE_BANKROLL` | `50m` | The **smallest** capital any member might enter — see below. |
| `GLOBAL_FLOOR` | `100k` | The **lowest** floor any member might pick. |
| `SHORTLIST` | `300` | Legacy path only (`FAST_SCAN=0`): items given a per-item `/timeseries` call. |
| `SLEEP` | `0.6` | Per-item politeness delay (legacy enrich + gap-fill). |
| `DEFAULT_CAPITAL` / `DEFAULT_FLOOR` | `700m` / `500k` | What the UI starts on. |
| `CLAN_PASSWORD` | *(unset)* | Unset = open. **Required** when `HOST` is not loopback. |
| `SECRET_KEY` | derived from the password | Set it to keep sessions valid across password changes. |
| `SECURE_COOKIES` | auto | Defaults on when `HOST` is not loopback. |
| `HOST` / `PORT` | `127.0.0.1` / `8777` | Bind `0.0.0.0` only behind TLS with `CLAN_PASSWORD` set. |
| `UA` | `merch-scanner - nekrosisx` | The wiki blocks the default requests agent. |
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

## Dip/rise prediction (`analysis.py`, `predict_cli.py`)

A second, deterministic scoring layer on top of the snapshot, for spotting
short-term dip-buy and flip opportunities rather than the swing/merch
timeframe the main scores target. Rule-based by design, not a trained
model — see "Calculation logic" above: this project's philosophy is
transparent, auditable formulas, and there is no labelled outcome dataset
(did a flagged dip actually reverse profitably?) to train or validate a
model against yet.

```fish
.venv/bin/python predict_cli.py                    # top 30 by dip confidence
.venv/bin/python predict_cli.py --mode flip --top 15
.venv/bin/python predict_cli.py --mode risk
.venv/bin/python predict_cli.py --capital 1.5b --floor 1m
```

It reads the DB the same way `GET /api/picks` does — no wiki calls, and it
needs the app or worker to have produced at least one snapshot first.

**Indicators**, computed on the 30-day daily `spark` series already stored
per item (not true 5m/1h intraday candles — those are only ever fetched as
a current-value bulk snapshot, never as history, to stay inside the
writer's per-scan wiki call budget):

| Field | Definition |
|---|---|
| `ema5` / `ema20` / `ema_signal` | EMA of daily midpoints; `bullish` when `ema5 > ema20`. |
| `rsi14` / `rsi_state` | Wilder's RSI; `oversold` <30, `overbought` >70. |
| `spread_pct` | `(sell_price - buy_price) / buy_price * 100`. |
| `vol_ratio` | `buy_vol_1h / sell_vol_1h`, from the `/1h` bulk call already made in `prefilter()` (previously summed and discarded; now also stored per item). |

**Scores**, percentile-normalised within the candidate set like `merch_score`:

```
dip_confidence = 100 * clamp(0.40*oversold + 0.35*revert + 0.25*liq_rank, 0, 1)
    oversold = clamp((30 - rsi14) / 30, 0, 1)
    revert   = clamp(-z30, 0, 2) / 2
    liq_rank = percentile rank of (buy_vol_1h + sell_vol_1h)

flip_score = 100 * clamp(0.55*spread_rank + 0.45*vol_day_rank, 0, 1)

risk_flags:
    volume_spike    — bottom-30% vol_day item with 1h volume > 5x its typical hourly rate
    wide_spread      — spread_pct >= 20 (close to the 25% prefilter cap)
    pump_dump_risk   — rsi14 > 80, trend == rising, sc_volatility_rank >= 75
```

`predicted_trend` is `RISK: PUMP-DUMP` (if flagged) > `STRONG BUY DIP`
(`dip_confidence >= 70` and oversold) > `BUY DIP` (`dip_confidence >= 45`) >
`OVERBOUGHT` (overbought RSI or `z30 > 1.5`) > `NEUTRAL`.

Storage: `buy_vol_1h`/`sell_vol_1h` are new `picks` columns, backfilled onto
existing databases by a migration in `db.init_db()`. Pre-migration snapshot
rows have them as `NULL` until the next scan runs.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/picks?capital=&floor=&top=&mode=` | Personalised ranking + heuristic dip/flip/risk fields (`analysis_note` explains confidence). `503` until the first ok scan lands. |
| `GET /api/status` | Snapshot age, next scheduled run, whether a scan is in flight, readiness hints. |
| `POST /api/refresh` | Trigger a scan. Single-flight; debounced manuals return **429**. |
| `GET /api/refresh/stream` | SSE `phase` / `log` / `progress` / `result` events, replayed from the start of the current scan. |
| `GET /api/scans?limit=` | Snapshot history. |
| `GET /api/item/{id}/history?limit=` | One item's stored fields across recent scans — feeds the inspector chart. Bounded by `KEEP_SCANS`. |
| `GET /healthz` | Open (no auth). `ready: true` and HTTP 200 only when an ok snapshot is fresh and non-empty; otherwise 503 with `ok: true` for process liveness detail in the body. |

The wiki is only ever contacted by the writer, on a schedule, with the custom
user agent and the existing retry/backoff. Readers never call it.

## Frontend (static/index.html)

Merch Desk — Emerald Ladder palette (tracker-style olive dark + emerald accent),
Source Sans 3 with tabular figures for columns, dark default with a light toggle
(`merchdesk.theme`). Single vanilla-JS file, no framework.

- **Layout** — filter rail · sortable table · item inspector. Capital/floor in
  the top bar; ticker shows session highlights only (best gp/24h, avg score, top
  pick). **Scan** starts a shared wiki refresh with a live progress panel.
- **Filters** — search (`/`), buy-dips/avoid, trend, score threshold,
  watchlist-only, column toggles. Client-side over `top=200`. Persisted in
  `localStorage` (`merchdesk.filters`).
- **Analysis columns** — risk in the table; dip / flip / predicted trend in the
  inspector (heuristic; see footer and `analysis_note`).
- **Inspector** — qty execution calc, GE tax, signals, 30d chart, recent scan
  history, score breakdown, catalyst/reason when present, copy buy/sell, wiki,
  local watchlist. Does not re-list buy/sell/margin (those stay in the table).
- **Mobile** — panes stack below 860px; secondary columns hide.

## Tests

```fish
.venv/bin/python tests/test_parity.py    # new split == the original run_scan
.venv/bin/python tests/test_app.py       # storage, personalisation, single-flight, SSE, auth
.venv/bin/python tests/test_analysis.py  # EMA/RSI math, dip/risk classification thresholds
.venv/bin/python tests/test_phase2.py    # history gate, degraded snapshots, rollover gap-fill
```

Fully offline: `tests/fake_wiki.py` is a deterministic stand-in for the API,
and `tests/_legacy_monolith.py` is a frozen pre-refactor copy of the tool used
as the parity oracle (do not edit it).

## Honesty notes

Prices are real trades observed by RuneLite and will **not** match the GE's
lagged guide price. "News" badges are best-effort keyword matches against the
OSRS news RSS — a hint, not authoritative release data. Dip/flip/risk scores
are rule-based heuristics on daily spark data — **not backtested**.

Every margin, ROI and gp/24h figure is **net of the 2% GE sale tax** (capped at
5m/item) — audited 2026-07-24, see "GE tax — confirmed applied". The one
inaccuracy is in the conservative direction: tax-exempt items such as the Old
school bond are taxed anyway, so their profit is understated.

## Deploying (VPS + Cloudflare Tunnel)

Production target: a small always-on VPS running Docker Compose (`app` +
Postgres + `cloudflared`). The public hostname
**https://scan.wiseoldtools.com** stays on the existing Cloudflare Tunnel
(`osrs-merch-scanner`); the VPS is the only origin. No public HTTP ports —
firewall SSH only; the tunnel reaches `app:8777` on the Compose network.

### One-time VPS bootstrap

1. Create an Ubuntu 24.04 VPS (~2 GB RAM). SSH in as root.
2. Copy the repo to `/opt/merch-desk` (git clone or `rsync`).
3. Run `sudo bash deploy/bootstrap-vps.sh` (Docker + UFW SSH-only).
4. Copy secrets:
   - `.env` from `.env.example` — set `POSTGRES_PASSWORD`, `CLAN_PASSWORD`,
     `SECRET_KEY`.
   - Tunnel credentials:
     `cp ~/.cloudflared/079ca8d2-f96c-4b97-99ac-9a450c68c6a6.json \
        /opt/merch-desk/deploy/cloudflared/credentials.json`
     (from the machine that created the tunnel; file is gitignored).
5. Start DB + app (**leave the tunnel profile off** while backfilling):

```bash
cd /opt/merch-desk
docker compose up -d --build db app
docker compose exec app python backfill_history.py   # ~40 min
```

6. First scan — either set `SCAN_ON_STARTUP=1` and recreate the app container,
   or log in and hit Refresh in the UI (or `POST /api/refresh` with a session).
   Wait until `curl -s http://127.0.0.1:8777/healthz` shows `"ready":true`.

### Cutover (move origin off your home machine)

1. On the **home** machine: stop `cloudflared tunnel run osrs-merch-scanner`
   and stop any local `python app.py`.
2. On the **VPS**: `docker compose --profile tunnel up -d tunnel`
3. Verify: `https://scan.wiseoldtools.com/healthz` and UI login.
4. Confirm home no longer runs the tunnel or the app.

Only one `cloudflared` connector should be active for this tunnel at a time.

### Compose cheatsheet

| Command | Purpose |
|---|---|
| `docker compose up -d --build db app` | App + Postgres (no public tunnel) |
| `docker compose --profile tunnel up -d tunnel` | Attach Cloudflare Tunnel |
| `docker compose exec app python backfill_history.py` | One-time history backfill |
| `docker compose logs -f app` | Follow writer/API logs |

`CLAN_PASSWORD` is **required** when `HOST` is not loopback — the process
refuses to start otherwise. Compose sets `HOST=0.0.0.0` inside the container.

### Local / split-process notes

For development, SQLite + `python app.py` on loopback still works. To split
roles later: `ROLE=writer` (scheduler only) and `ROLE=api` (HTTP only) sharing
Postgres — not required at clan scale.