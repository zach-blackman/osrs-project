# OSRS Merch Scanner — Phase 2: All-Items Snapshot DB + One-Time History Backfill

> Handoff document for a fresh agent. Read `MIGRATION_PLAN.md` first — this
> assumes Phase 1 (the DB-backed clan app: `scanner.py` / `db.py` / `worker.py`
> / `app.py`) is done and running, per its status header.

## 0. What "migrate to a persistent DB" actually means here

The app is **already** DB-backed: `worker.py` runs a scan every
`SCAN_INTERVAL_MIN` (10 min) minutes, writes it via `db.write_snapshot`, and
`app.py` reads the latest snapshot and scores it with zero live API calls per
request (`scanner.rank_for`, `app.py:147` `/api/picks`). So a page load never
hits the wiki today — that part of the ask is already true.

What's **not** true yet, and is the real target of this phase:

1. **Only the top `SHORTLIST` items (currently 300) are ever scored or
   stored.** `scanner.py:559` (`cands = cands[:cfg.shortlist]`) cuts the
   candidate list before the expensive stage-2 enrichment runs, based on a
   crude same-instant `margin × volume` proxy (`scanner.py:196-197`). We
   already proved this skews results — see the SHORTLIST=160→300 comparison
   earlier in this project: the single best-scoring item in the whole market
   was invisible at 160. It's still invisible above 300, just less often.
2. **The expensive part is being redone every 10 minutes for no reason.**
   `enrich()` (`scanner.py:205-252`) calls `/timeseries?timestep=24h` — a full
   year of daily candles — **per item, every single scan cycle**, via
   `scan_market`'s stage-2 loop (`scanner.py:566-581`). The daily series it
   fetches changes by at most one data point per day. At 300 items × 6
   scans/hour × 24h, that's ~43,200 needless `/timeseries` calls a day to
   re-derive numbers that were 99.7% identical to ten minutes ago.

Fix both by splitting "history" from "current price" into separate tables
with separate refresh cadences: history is written **once**, then appended to
**once a day**; the 10-minute cycle only touches cheap bulk endpoints and
covers **every tradeable item**, not a pre-filtered subset.

## 1. New schema (additive — `picks`/`scans` stay untouched until step 6)

```
items                                  -- static-ish metadata, ~4000 rows
  item_id       INTEGER PRIMARY KEY
  name          TEXT
  buy_limit     INTEGER
  members       BOOLEAN
  first_seen    FLOAT
  last_seen     FLOAT               -- last time /mapping listed this id

item_daily_history                     -- one row per (item, day); backing
  item_id       INTEGER  FK items          store for spark/trend/volatility
  day           DATE                       (replaces the live /timeseries
  price         FLOAT                      call in enrich())
  volume        INTEGER
  source        TEXT     -- 'backfill' | 'rollover'
  PRIMARY KEY (item_id, day)

item_snapshots                         -- the new 10-min pulse, ALL items
  id            INTEGER PRIMARY KEY
  scan_id       INTEGER  FK scans
  item_id       INTEGER  FK items
  high          INTEGER
  low           INTEGER
  buy_vol_1h    INTEGER
  sell_vol_1h   INTEGER
  INDEX (item_id, scan_id)
```

`scans` (`db.py:24-35`) is unchanged — still one row per cycle. `picks` stays
as-is through the transition (step 6 decides its fate).

Why split `item_daily_history` from `item_snapshots` instead of one table:
different retention needs. Daily history is small (~4000 × 365 ≈ 1.46M rows
total, ever) and should live forever. 10-min snapshots are ~4000 rows every
10 minutes (~576k/day) and need aggressive pruning — see §5.

## 2. One-time backfill (`backfill_history.py`, new script)

1. `GET /mapping` once → full tradeable item list → upsert into `items`.
2. For each item: `GET /timeseries?id=&timestep=24h` (same endpoint
   `enrich()` already uses, `scanner.py:207`), same `sleep` politeness delay
   (`config.SLEEP`, default 0.6s) → insert every daily candle into
   `item_daily_history` with `source='backfill'`.
3. ~4000 items × 0.6s ≈ 40 minutes. Run once, off-hours, as a background
   process. Make it **idempotent and resumable**: skip any item that already
   has ≥350 days of rows, so a killed/restarted run doesn't restart from zero
   or double-insert (use `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` on the
   `(item_id, day)` primary key).
4. Verify before trusting it: for a sample of ~20 items, diff the backfilled
   `item_daily_history` rows against a live `/timeseries` call and against
   the `spark` field already stored in the current `picks` table for that
   item. They should match (mod rounding).

This is the **only place** `/timeseries` gets called per-item ever again
under normal operation.

## 3. Fast writer path (`scanner.scan_market_fast`, new function)

Parallel to, not a replacement for, `scan_market` — keep both importable
until step 6.

1. Reuse the bulk-fetch half of `prefilter()` (`scanner.py:125-`) — `/mapping`
   + `/latest` + `/1h`, 3 calls total regardless of item count — but **drop
   the `cands[:cfg.shortlist]` cut**. Keep the existing junk filters (bad
   spread, zero recent volume, price below `global_floor`) since those are
   legitimate data-quality gates, not opportunity gates.
2. For each surviving item, instead of calling `enrich()` (network + sleep),
   pull that item's rows out of `item_daily_history` — **one bulk DB query
   for all items**, not N — and run the exact same math `enrich()` already
   does (`scanner.py:221-251`: `rank_within`, `slope_pct_per_day`,
   `volatility_pct`, trend classification, `spark` slice). Factor that math
   out of `enrich()` into a pure helper, e.g. `derive_trend_metrics(row,
   series, vols)`, so both the old live path and the new DB-fed path call
   the identical function — no formula drift, easy to parity-test.
3. Result: a 10-minute cycle becomes 3 bulk HTTP calls + 1 DB read, taking a
   couple of seconds total, independent of whether it covers 300 items or
   4000. No more `SLEEP`-gated per-item loop in the hot path.
4. Write results to `item_snapshots` (raw current price/volume) — `scans`
   row as today. Derived/scored fields (`merch_score`, `flip`, `swing`, etc.)
   stay computed at **read time** in `rank_for`, unchanged, just now over the
   full item set instead of a shortlist. This is what actually fixes the
   skew: there's no pre-scoring cut left to skew anything.

## 4. Daily rollover job (new APScheduler cron in `worker.py`)

Once a day (just after UTC midnight, alongside the existing interval job),
append each item's now-complete day into `item_daily_history`:

- Preferred: aggregate that day's own `item_snapshots` rows (already in the
  DB, zero extra API calls) into one daily candle — e.g. mean of
  `(high+low)/2` across the day's snapshots as `price`, sum of
  `buy_vol_1h+sell_vol_1h` samples as a `volume` proxy.
- Fallback, only for gap-filling: if a day has too few snapshots (app was
  down, etc.), fetch that one day's slice via `/timeseries` for the affected
  items instead of guessing — rare, so the cost stays negligible.

## 5. Retention

- `item_daily_history`: keep forever (it's the whole point — this is now the
  durable long-term record instead of re-fetching it).
- `item_snapshots`: needs its own pruning job, separate from `db.prune()`
  (`db.py:206-219`, which only touches `scans`/`picks`). Something like "keep
  N days of raw 10-min snapshots" is enough — the daily rollover has already
  folded anything older into `item_daily_history` by then.

## 6. Cutover sequence (safe, reversible until the last step)

1. Add the three new tables via `db.py` (additive `metadata.create_all` —
   doesn't touch `picks`/`scans`). App keeps running unmodified.
2. Run the backfill script (§2). Verify (§2.4).
3. Implement `scan_market_fast` (§3) behind an env flag, e.g.
   `FAST_SCAN=1` in `config.py`, defaulting **off**. `worker.py:_run`
   branches on it.
4. Extend `tests/test_parity.py` (it already exists for exactly this kind of
   check) to assert `scan_market_fast` output matches `scan_market` output
   for a shared sample of items, post-backfill.
5. Add the daily rollover job (§4). Let it run for a few real days before
   trusting it — the first cutover day is the one place a bug would be
   invisible until the *next* day's `spark` looks wrong.
6. Flip `FAST_SCAN` on by default. Keep the old path available for one
   release as a manual fallback.
7. Retire `SHORTLIST` as a *scoring* cut — repurpose it (if kept at all) as
   just the UI's "how many rows to display" default, since `rank_for`
   already takes a `top=` param (`scanner.py:613`, `639`) independent of how
   many items were scored.
8. Once stable: drop the now-redundant `spark` JSON blob from `picks`
   (`db.py:63`) — or leave `picks` as a materialized per-scan cache computed
   from `item_daily_history` at write time, still fine for the UI's existing
   read shape (`db.read_picks`, `app.py`'s drill-down history endpoint) — and
   remove the live-network branch of `enrich()`/`scan_market` if no longer
   needed anywhere (CLI tool `predict_cli.py` may still want a live one-shot
   path; check before deleting).

## 7. Risks / open questions to resolve before building

- **Daily-candle aggregation fidelity**: does `mean((high+low)/2)` across a
  day's `item_snapshots` samples produce values close enough to the wiki's
  own `avgHighPrice`/`avgLowPrice` daily candle that `trend`/`volatility`
  don't visibly jump the day rollover starts? Worth a side-by-side check
  before relying on it (compare a week of self-aggregated days against the
  wiki's real daily candles for the same items once both exist).
- **New items mid-game**: detect via `/mapping` diff in the 10-min cycle;
  they'll have <45 days of `item_daily_history` and should be skipped from
  scoring the same way `enrich()` already skips thin-history items today
  (`scanner.py:218-219`), not error out.
- **Score-shape shift at cutover**: `merch_score`/`flip`/`swing` are
  percentile-normalised within whatever set gets scored (`rank_within` over
  `rows`, `scanner.py:262-269`, `410-419`). Scoring ~4000 items instead of
  300 will shift absolute numbers for existing top picks — expected, and the
  whole reason for doing this, but worth a heads-up before it lands so it
  isn't mistaken for a bug.
- **Downtime spanning a day boundary** leaves a gap in `item_daily_history`
  for that day — the fallback in §4 handles it, but confirm the rollover job
  actually detects "too few snapshots" rather than silently writing a
  low-confidence candle.
