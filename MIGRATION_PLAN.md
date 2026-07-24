# OSRS Merch Scanner — Migration to a Hosted, DB-Backed Clan App

> Handoff document for a fresh agent. Everything needed is here; you were not
> present for the design conversation that produced it. Read it fully before
> writing code. The project now lives in `/home/nekrosisx/Projects/osrs-project`
> (moved from `~/Downloads`).

## 0. Status — 2026-07-24

Steps 1–5 are **done and verified**; see `README.md` for how to run it.

- ✅ Step 1 `scanner.py` — `scan_market` / `rank_for` split, parity test passing.
- ✅ Step 2 `db.py` — SQLAlchemy Core, SQLite + Postgres one code path, pruning.
- ✅ Step 3 `worker.py` — APScheduler interval job, single-flight, SSE fan-out
  with replay so a client joining mid-scan sees the whole log.
- ✅ Step 4 `app.py` — FastAPI reader, shared-password auth (`CLAN_PASSWORD`),
  `/api/picks` `/api/status` `/api/refresh` `/api/refresh/stream` `/healthz`.
- ✅ Step 5 `static/index.html` — original page, rewired: Apply re-ranks the
  snapshot instantly, Refresh drives the same progress console over SSE.
- ⏸️ Step 6 deployment — **deliberately deferred.** User decision (2026-07-24):
  run it locally for now, choose Render vs Fly later. No Dockerfile /
  `render.yaml` / `fly.toml` yet; `README.md` records the intended shape.
- ⏸️ Verification item 9 (point at Postgres) — deferred with Step 6. The code
  path is dialect-neutral and needs only `DATABASE_URL` + psycopg.

Verification 1–8 all pass, offline via `tests/test_app.py` plus one real live
scan against the wiki (25 items) that produced two very different rankings from
a single snapshot with zero extra API calls.

Two decisions taken while implementing, both departures from the text below:

1. **`REFERENCE_BANKROLL` must be the SMALLEST plausible capital, not the
   largest** — §4 subtlety 1 has it backwards. `prefilter`'s capacity test gets
   *stricter* as the bankroll rises, so a generous value silently drops the
   small and mid-cap items a smaller member needs. Default is now `50m`.
2. Login parses its urlencoded body by hand rather than using `fastapi.Form`,
   which would pull in `python-multipart` for a single field.

## 1. What exists today

`osrs_merch_scan.py` is a **single-file Python 3.14 tool** (~1100 lines) that
scans the Old School RuneScape Grand Exchange via the wiki Real-time Prices API
and ranks the top 50 "merch" (flip) opportunities. It currently runs as a local
web app: a Python **stdlib `http.server`** backend serves an OSRS-themed HTML
page (inline CSS/JS, no framework) that streams a live scan over **SSE**.

It works. **Do not break it during migration** — build the new system in new
files alongside it.

### Current internals (reuse this logic — it is correct and battle-tested)
- `get(path, retries, **params)` — wiki API wrapper using a `requests.Session`
  with a **custom `User-Agent`**. API base: `https://prices.runescape.wiki/api/v1/osrs`.
  Endpoints used: `/mapping`, `/latest`, `/1h`, `/timeseries?id=&timestep=24h`.
- `rank_within`, `slope_pct_per_day`, `volatility_pct`, `net_margin` — stats.
- `GE_WINDOWS_24H = 6`; `fillable_units_24h(limit, buy_price, vol_day, bankroll)`
  — units flippable in 24h = min(buy-limit×6, daily volume, capital÷price).
- `prefilter(bankroll, min_deploy_frac, max_spread_pct, stale_hours, min_price)`
  — 3 bulk API calls, filters all ~4600 items to a liquid/affordable shortlist.
- `enrich(row, sleep)` — one `/timeseries` call per item; derives `now`, `vol_day`,
  `rank90`, `rank_all`, `z30`, `slope30/7`, `trend`, `volatility`, `spark` (30d
  midpoints), `days`.
- `score(rows)` — flip/swing sub-scores. `merch_score(rows, news)` — composite
  0–100. `reason_for(r)` — one-sentence rationale. `fetch_news`/`catalyst_for` —
  OSRS news RSS keyword catalysts.
- `run_scan(params, progress, on_event)` — orchestrates the whole pipeline and
  returns the ranked top-N as JSON-serialisable dicts. `on_event(kind, **data)`
  emits `phase`/`log`/`progress`/`result` events (this drives the SSE console).
- `serve_gui(params, port)` — `ThreadingHTTPServer`; `/api/scan` (JSON),
  `/api/scan/stream` (SSE), parses `?capital=&floor=`. `GUI_PAGE` is the inline
  page. `parse_gp`/`fmt_gp` handle `700m`/`1.5b`/`500k` shorthand.

### Per-pick fields produced
`id, name, now, margin, roi, gp_24h, vol_day, rank90, rank_all, z30, trend,
volatility, flip, swing, merch_score, catalyst, reason, limit, buy_price,
sell_price, tax, sell_net, units_24h, spark`.

## 2. Hard constraints — do not violate

- **Preserve the `UA` constant** near the top of `osrs_merch_scan.py`
  (currently `UA = "merch-scanner - nekrosisx"`). It is user-customised. Reuse
  the same value in the new code. The wiki blocks the default requests UA.
- **Be a good API citizen.** The wiki must see ONE polite scanner: only the
  writer process calls the wiki, on a schedule, with the custom UA and the
  existing retry/backoff + per-item `sleep`. Readers must NEVER call the wiki.
- The environment: `/home/nekrosisx/Downloads`, **not a git repo** (offer to
  `git init`), `.venv` currently has only `requests`, Python 3.14.6, `fish`
  shell. tkinter is broken here (irrelevant — this is web). New dependencies are
  fine now (we are intentionally leaving the zero-dependency constraint behind).
- Prices are real RuneLite-observed trades; keep the honesty notes already in
  the UI (they will NOT match the GE's lagged guide price; news catalysts are
  fuzzy keyword matches, not authoritative release data).

## 3. Decisions already made (do not re-litigate)

- **Scale:** ~10 clan users to start.
- **Freshness:** a shared scan every **10 minutes** is fine, PLUS a manual
  **live-refresh** button that triggers an on-demand scan.
- **Hosting:** a **PaaS with one always-on instance** (Render or Fly.io) is the
  recommendation. VPS (Hetzner) is the fallback. NOT serverless/static (the
  1–2 min scan + SSE progress fights that model).
- **Database:** **managed Postgres** if writer and reader are separate services
  or you expect growth (recommended for PaaS); **SQLite** is an acceptable
  simpler start IF writer+reader share one instance + a persistent volume.
  Write the storage layer so switching is a config change.
- **Frontend:** keep the **current vanilla HTML/JS page** (port it to read from
  the API). React is explicitly **deferred** — only worth it once the UI grows
  (filters, saved views, multiple pages, auth flows). Do not rewrite to React
  in this migration.

## 4. The core architectural idea

**Separate shared market data (expensive) from per-user personalization (cheap).**

Most of a scan is NOT user-specific. Only a few fields depend on the user's
capital/floor:

| Data | User-specific? | Cost |
|---|---|---|
| margins, roi, vol_day, volatility, trend, rank_all/rank90, z30, spark, catalyst, buy/sell/tax, limit | **No** (pure market) | Expensive (~160 API calls) |
| `units_24h`, `gp_24h`, floor filter, and the gp-driven part of `merch_score` | **Yes** (capital + floor) | Trivial arithmetic |

Therefore:
- The **writer** runs the expensive scan ONCE, capital-agnostically, and stores
  the per-item market metrics as a **snapshot** in the DB.
- The **reader** applies each user's `capital` + `floor` at query time: compute
  `units_24h`/`gp_24h`, filter by floor, then run the (cheap) `score` +
  `merch_score` + `reason_for` over the personalized set and sort. One snapshot
  serves every user's personal settings instantly. Capital/floor stop being a
  reason to re-scan — only price freshness is.

### Two subtleties you MUST handle
1. **`prefilter` currently uses `bankroll`** for its capacity filter
   (`cap_per_4h = limit×high >= bankroll×min_deploy_frac`). For a shared scan,
   run it with a **generous `REFERENCE_BANKROLL`** (config, e.g. the largest
   plausible clan bankroll) so the shortlist is a *superset* that serves all
   users. Do not shortlist against one user's capital.
2. **Floor at scan vs read.** Scan with a **low `GLOBAL_FLOOR`** (config, e.g.
   100k or 0) so the snapshot contains items down to the global minimum, then
   apply each user's higher floor at read time.

## 5. Target architecture

```
  WRITER (only wiki caller)          DB (snapshots)            READER (stateless)
  scan_market() every 10 min  ──▶  scans + picks tables  ──▶  GET /api/picks?capital=&floor=
  + on-demand (single-flight)                                  applies capital/floor per request
```

## 6. Migration steps

### Step 0 — safety net
- `git init` (offer), commit the current working `osrs_merch_scan.py` first.
- Create `.venv`-installable `requirements.txt`.

### Step 1 — extract the scanner core into a module (`scanner.py`)
- Move the pure logic (`get`, stats helpers, `fillable_units_24h`, `prefilter`,
  `enrich`, `score`, `merch_score`, `reason_for`, `fetch_news`, `catalyst_for`,
  `parse_gp`, `fmt_gp`, and the `UA`/`BASE`/`SESSION`) into `scanner.py`.
- Split `run_scan` into two functions:
  - **`scan_market(cfg, on_event=None) -> (meta, market_rows)`** — capital-
    AGNOSTIC. Uses `REFERENCE_BANKROLL` + `GLOBAL_FLOOR`. Returns snapshot meta
    (timestamps, params) and the per-item market records (everything EXCEPT
    `units_24h`, `gp_24h`, and the final personalized `merch_score`). Keep the
    `on_event` progress feed intact.
  - **`rank_for(market_rows, capital, floor, top=50) -> list[pick]`** — applies
    floor filter, computes `units_24h`/`gp_24h` via `fillable_units_24h`, then
    runs `score` + `merch_score` + `reason_for` and sorts by `merch_score`
    (default view still sorts by margin in the UI). Cheap; call per request.
- Keep `osrs_merch_scan.py` importing from `scanner.py` so the old CLI/GUI still
  runs. **Parity requirement:** for a given snapshot, `rank_for(rows, cap, floor)`
  must match the old `run_scan` output for the same capital/floor. Add a test.

### Step 2 — storage layer (`db.py`)
- Thin interface over SQLite (local default) and Postgres (`DATABASE_URL`).
  Recommended: **SQLAlchemy Core** (one code path, both dialects) — or plain
  `sqlite3`/`psycopg` behind the same functions. `spark` stored as JSON.
- Schema:
  ```
  scans:  id · started_at · finished_at · status(ok|running|error) · price_ts · params(json) · n_items
  picks:  scan_id → · item_id · name · buy_price · sell_price · tax · margin · roi ·
          vol_day · limit · volatility · trend · rank_all · rank90 · z30 ·
          catalyst · reason_base · spark(json)
  ```
- Functions: `init_db()`, `write_snapshot(meta, market_rows)`,
  `latest_ok_scan()`, `read_picks(scan_id)`, `list_scans(limit)`.
- History is a free bonus (keep N recent snapshots for future
  score-over-time / backtest features; add a prune job).

### Step 3 — writer + scheduler + single-flight (`worker.py`)
- `run_and_store(cfg, on_event=None)` = `scan_market` → `write_snapshot`.
- **APScheduler** interval job every `SCAN_INTERVAL_MIN` (default 10).
- **Single-flight**: a lock/flag so concurrent refresh requests attach to the
  ONE in-flight scan instead of launching duplicates. Debounce manual triggers.
- Fan the `on_event` stream out to any connected SSE clients (simple in-process
  pub/sub: a set of subscriber queues).

### Step 4 — API (`app.py`, FastAPI + uvicorn)
- `GET /api/picks?capital=&floor=&top=` → read `latest_ok_scan`, `read_picks`,
  `rank_for`, return JSON incl. `updated_at`, `age_seconds`, echoed capital/floor.
- `GET /api/status` → last updated, next scheduled run, `scanning` bool.
- `POST /api/refresh` → trigger writer (single-flight); returns immediately.
- `GET /api/refresh/stream` → SSE of the current/last scan's progress (reuse the
  existing `phase`/`log`/`progress`/`result` event shapes so the console UI is
  unchanged).
- Serve the frontend. **Auth:** shared clan password via env `CLAN_PASSWORD`
  (simple middleware / cookie) — or a stub with a clear TODO for Discord OAuth.

### Step 5 — port the frontend
- Reuse the existing `GUI_PAGE` markup/CSS/JS almost verbatim. Changes:
  - Capital + floor inputs now hit `GET /api/picks` (instant; no scan needed).
  - Show "updated N min ago" from `/api/status`; poll it lightly.
  - The refresh button → `POST /api/refresh` then subscribe to
    `/api/refresh/stream` and drive the SAME progress console.
  - Guard: if a scan is already running, the button attaches to it.
- Serve as a static file (or keep inline). Do NOT introduce React here.

### Step 6 — deployment
- `Dockerfile` (python:3.12+ slim; install `requirements.txt`; run uvicorn).
- One always-on service running API + in-process APScheduler (simplest), OR
  split web + worker services sharing managed Postgres (cleaner, if desired).
- `render.yaml` and/or `fly.toml`; attach managed Postgres; set env
  (`DATABASE_URL`, `SCAN_INTERVAL_MIN=10`, `REFERENCE_BANKROLL`, `GLOBAL_FLOOR`,
  `SHORTLIST`, `UA` = the preserved custom value, `CLAN_PASSWORD`, `PORT`).
- Health check endpoint. Log the writer's last-success timestamp; surface
  staleness in `/api/status`.

## 7. Config (env vars)
`DATABASE_URL` (sqlite path or postgres URL) · `SCAN_INTERVAL_MIN=10` ·
`REFERENCE_BANKROLL` (generous, e.g. 5b) · `GLOBAL_FLOOR` (e.g. 100k) ·
`SHORTLIST` (e.g. 160) · `SLEEP` (per-item, keep polite) · `UA` (preserved) ·
`CLAN_PASSWORD` · `PORT`.

## 8. Verification (do all before calling it done)
1. `python -m py_compile` on every new module; old tool still compiles.
2. **Parity test:** snapshot once; assert `rank_for(rows, cap, floor)` equals the
   old `run_scan` picks for several (capital, floor) pairs.
3. Local run with **SQLite**: trigger a scan → `scans`/`picks` populated →
   `GET /api/picks` returns personalized results for DIFFERENT capital/floor
   **without re-scanning** (prove read-time personalization).
4. **Single-flight:** fire `POST /api/refresh` 5× concurrently → exactly ONE
   wiki scan runs; all requests see the same result.
5. **SSE:** `/api/refresh/stream` streams phase/log/progress and a final result.
6. Restart the process → latest snapshot still served from DB (durability).
7. Confirm readers make **zero** wiki calls (only the writer does).
8. Confirm the `UA` value is unchanged and used by the writer's session.
9. Point it at Postgres via `DATABASE_URL` and re-run 3–6.

## 9. Deliverables
- `scanner.py`, `db.py`, `worker.py`, `app.py`, ported frontend, `requirements.txt`,
  `Dockerfile`, `render.yaml`/`fly.toml`, a short `README` (run locally + deploy),
  and a parity/smoke test. The original `osrs_merch_scan.py` remains runnable.

## 10. Open items to confirm with the user (ask early)
- SQLite-single-instance vs managed Postgres for the first deploy.
- Render vs Fly.io.
- Auth: shared password now vs Discord OAuth later (stub is fine to start).
- `REFERENCE_BANKROLL` / `GLOBAL_FLOOR` default values.
