# Clan Tools — Merch Desk

Clan web app for OSRS helpers. Today the only live tool is **Merch Desk**, which
ranks Grand Exchange flip opportunities from the wiki’s real-time prices.
One shared scan serves the whole clan; each member’s capital and price floor
are applied instantly at read time — the UI never calls the wiki.

More tools plug into the same hamburger shell later (`ClanShell.TOOLS`).

```
  WRITER (only wiki caller)        DB (snapshots)           READER (stateless)
  scan every 10 min (FAST_SCAN) ─▶ scans + picks + history ─▶ GET /api/picks?capital=&floor=
  + on-demand (single-flight)                               personalised + analysis per request
```

**Default scan path is FAST_SCAN:** three bulk wiki calls + DB-backed daily
history. Run `backfill_history.py` once before relying on it. The older
shortlist + per-item `/timeseries` path remains available with `FAST_SCAN=0`.

Public site: **https://scan.wiseoldtools.com**

---

## What’s in the repo

| Path | Role |
|---|---|
| `app.py` | FastAPI reader: auth, `/merch` UI, `/api/*`. Zero wiki calls. |
| `worker.py` | Writer: APScheduler, single-flight scans, SSE fan-out, daily history rollover. |
| `scanner.py` | `scan_market` / `scan_market_fast` (capital-agnostic) + `rank_for` (personalise). |
| `db.py` | Snapshots, picks, `items` / `item_daily_history` / `item_snapshots`. SQLite or Postgres. |
| `analysis.py` | Dip/flip/risk heuristics layered on ranked rows (also on `/api/picks`). |
| `config.py` | All env vars, read once at import. |
| `backfill_history.py` | One-time resumable `/timeseries` → `item_daily_history` backfill. |
| `predict_cli.py` | Terminal client for the prediction layer (same DB as the UI). |
| `static/tools/merch.html` | Merch Desk page (shell chrome + desk body). |
| `static/css/shell.css`, `static/js/shell.js` | Shared Clan Tools shell: drawer, theme, `TOOLS` registry. |
| `static/css/merch.css`, `static/js/merch.js` | Merch Desk UI (desktop three-pane + mobile cards/sheets). |
| `static/archive/` | Older UIs, not mounted as routes. |
| `osrs_merch_scan.py` | Original standalone CLI/GUI (parity / history only). |

---

## Run locally

```fish
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# First time (or after wiping the DB) — required for FAST_SCAN:
.venv/bin/python backfill_history.py
.venv/bin/python app.py            # http://127.0.0.1:8777 → redirects to /merch
```

Without a backfill, FAST_SCAN refuses to score (visible error) instead of
serving an empty “ok” snapshot. Quick legacy smoke test:

```fish
env FAST_SCAN=0 SHORTLIST=25 .venv/bin/python app.py
```

Changing **Cap** / **Floor** re-ranks the snapshot already in the database.
Only **Scan** hits the wiki.

Open locally with no password (`CLAN_PASSWORD` unset). Binding off loopback
requires a password — the process refuses to start otherwise.

---

## Routes & UI

| Path | Purpose |
|---|---|
| `/` | Redirects to `/merch`. |
| `/merch` | Merch Desk (Clan Tools shell + desk). |
| `/login`, `/logout` | Shared clan-password cookie session. |
| `/static/...` | CSS/JS and other static assets. |
| `/healthz` | Open (no auth). Ready only when a fresh ok snapshot exists. |

**Shell** — hamburger drawer lists tools from `TOOLS` in `static/js/shell.js`.
Sun/moon theme toggle (`merchdesk.theme`). Emerald Ladder palette (dark
default).

**Merch Desk (desktop)** — filter rail · sortable table · item inspector.
Cap/Floor and Scan in the top chrome; ticker shows session highlights.

**Merch Desk (mobile, below 860px)** — card list; Filters / Cap / Floor / Scan in a
compact bar; filters and item detail open as sheets. Nested `#cardwrap` scroll
(shell stays locked).

Prefs live in `localStorage` (`merch.capital`, `merch.floor`,
`merchdesk.filters`, `merchdesk.watch`, `merchdesk.theme`).

### Adding another tool later

1. Add `{ id, href, label, status: "live"|"soon" }` to `TOOLS` in `shell.js`.
2. Add `static/tools/<id>.html` (+ optional CSS/JS) with the same shell chrome.
3. Add `GET /<id>` in `app.py` via `_tool_html("<id>")`.

---

## Configuration

All optional; defaults suit a local single-instance run. Gp values accept
`700m` / `1.5b` shorthand.

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///osrs_scanner.db` | `postgresql+psycopg://…` for Postgres. |
| `SCAN_INTERVAL_MIN` | `10` | Scheduled scan interval. |
| `SCAN_ON_STARTUP` | `1` | Scan at boot only when there is no ok snapshot. |
| `KEEP_SCANS` | `50` | Older `scans`/`picks` pruned after each scan. |
| `FAST_SCAN` | `1` | All-items path from DB history. Needs backfill. |
| `MIN_HISTORY_READY` | `50` | Refuse FAST_SCAN until this many items have ≥`MIN_HISTORY_DAYS`. |
| `MIN_HISTORY_DAYS` | `45` | Same bar as trend derivation. |
| `MIN_SCORED_ITEMS` | `1` | Below this, a finished scan is `degraded`, not `ok`. |
| `READY_MAX_AGE_MIN` | `30` | `/healthz` 503 when the ok snapshot is older than this. |
| `SNAPSHOT_KEEP_DAYS` | `3` | Raw 10-min `item_snapshots` retention. |
| `ROLE` | `all` | `all` = API+writer; `api` = reader; `writer` = scheduler only. |
| `REFERENCE_BANKROLL` | `50m` | Smallest capital any member might enter (see below). |
| `GLOBAL_FLOOR` | `100k` | Lowest floor used when building the shared shortlist/superset. |
| `SHORTLIST` | `300` | **Legacy only** (`FAST_SCAN=0`): per-item `/timeseries` budget. |
| `SLEEP` | `0.6` | Per-item delay (legacy enrich + history gap-fill). |
| `DEFAULT_CAPITAL` / `DEFAULT_FLOOR` | `700m` / `500k` | UI starting Cap/Floor. |
| `CLAN_PASSWORD` | *(unset)* | Unset = open on loopback. Required off-loopback. |
| `SECRET_KEY` | derived from password | Set to keep sessions across password changes. |
| `SECURE_COOKIES` | auto | On when `HOST` is not loopback. |
| `HOST` / `PORT` | `127.0.0.1` / `8777` | Bind `0.0.0.0` only behind TLS + password. |
| `UA` | `merch-scanner - nekrosisx` | Wiki blocks the default requests agent. |

`REFERENCE_BANKROLL` feeds prefilter capacity
(`limit × high ≥ bankroll × min_deploy_frac`). Larger values are *stricter*
and drop small/mid items smaller members need — keep it at the smallest
plausible clan capital so the stored set is a superset for everyone.

---

## How scanning works (today)

### FAST_SCAN (default)

1. Bulk `/mapping`, `/latest`, `/1h` (+ news RSS, best-effort).
2. Score tradeable items using **stored** daily history (`item_daily_history`),
   not a fresh `/timeseries` per item.
3. Persist a capital-agnostic snapshot; readers personalise with `rank_for`.

Backfill once with `backfill_history.py`. Ongoing gap-fill / rollover is handled
by the writer.

### Legacy path (`FAST_SCAN=0`)

Two-stage funnel: bulk **prefilter**, then `/timeseries` for the top
`SHORTLIST` items. Use for debugging or when history is empty. Wall-clock cost
scales with `SHORTLIST * (SLEEP + latency)`.

### Price sources

| Source | Used for |
|---|---|
| `/mapping` | Names, 4h GE buy limit |
| `/latest` | Insta-buy (`high`) / insta-sell (`low`) + timestamps |
| `/1h` | Hourly volume (liveness) |
| `/timeseries?timestep=24h` | Daily candles → history backfill / legacy enrich |
| `latest_news.rss` | Keyword content catalysts (failures ignored) |

These are **RuneLite-observed trades**, not the GE guide price.

### Prefilter gates (shared)

Tradeable, real spread, `low >= GLOBAL_FLOOR`, fresh ≤12h, spread ≤25%,
post-tax margin > 0, hourly volume > 0, and capacity vs `REFERENCE_BANKROLL`.

---

## Calculation logic

Percentile ranks are *within the current candidate set*, not absolute.
A score of 80 means “high in this scan,” not “universally good.”

**Per-unit economics** (writer, capital-independent):

```
tax      = min(high * 0.02, 5_000_000)   # GE sale tax, capped per item
margin   = high - low - tax              # net gp per unit
roi      = margin / low * 100
buy_price = low, sell_price = high
```

Every margin, ROI, and gp/24h the app shows is **net of GE tax**. Tax enters
once in `net_margin()` (`scanner.py`) and everything inherits it.

| Rule | Status |
|---|---|
| 2% sale tax, 5m cap | Applied |
| Charged on sale (`high`) only | Applied |
| Exempt items (e.g. bonds) | **Not modelled** — profits understated (conservative) |
| Wiki “round down” vs code `round()` | ≤1 gp/unit, immaterial |

**History metrics** (from daily mids): `rank_all` / `rank90`, `z30`,
`slope30` / `slope7`, `volatility`, `vol_day`, `trend`
(`bounce` / `rising` / `falling` / `flat`).

**Capital scaling** (reader, per request):

```
units_24h = min(limit * 6, vol_day, capital / buy_price)
gp_24h    = margin * units_24h
```

**Composite scores** (percentile parts of throughput / liquidity / volatility /
value + trend + catalyst):

```
flip        ≈ throughput + liquidity
swing       ≈ cheapness + mean-reversion + vol + liquidity (−knife on falling)
merch_score ≈ weighted blend + trend_adj + catalyst   # /api/picks sort key
```

Exact weights live in `scanner.py`; they were set by judgement, not backtest.

---

## Dip / rise prediction

`analysis.py` (+ `predict_cli.py`) adds dip/flip/risk fields on the same
snapshot. Rule-based on the stored 30d spark — not a trained model, not
backtested.

```fish
.venv/bin/python predict_cli.py
.venv/bin/python predict_cli.py --mode flip --top 15
.venv/bin/python predict_cli.py --mode risk
.venv/bin/python predict_cli.py --capital 1.5b --floor 1m
```

Needs at least one ok snapshot in the DB. The same fields appear on
`/api/picks` rows (`dip_confidence`, `flip_score`, `risk_level`,
`predicted_trend`, …) with an `analysis_note` caveat.

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/picks?capital=&floor=&top=&mode=` | Personalised ranking + analysis fields. `503` until first ok scan. |
| `GET /api/status` | Snapshot age, next run, scanning flag, readiness hints. |
| `POST /api/refresh` | Trigger shared scan. Single-flight; debounce → **429**. |
| `GET /api/refresh/stream` | SSE `phase` / `log` / `progress` / `result`. |
| `GET /api/scans?limit=` | Snapshot history. |
| `GET /api/item/{id}/history?limit=` | Recent stored rows for one item (inspector chart). |
| `GET /healthz` | Liveness always; HTTP 200 only when `ready`. |

Readers never call the wiki. Only the writer does, on a schedule / Scan, with
the custom UA and retry/backoff.

---

## Tests

```fish
.venv/bin/python tests/test_parity.py     # split scanner vs frozen monolith
.venv/bin/python tests/test_app.py        # storage, personalisation, SSE, auth, shell routes
.venv/bin/python tests/test_analysis.py   # EMA/RSI + dip/risk thresholds
.venv/bin/python tests/test_phase2.py     # history gate, degraded snapshots, rollover
```

Offline: `tests/fake_wiki.py` stubs the wiki; `tests/_legacy_monolith.py` is the
parity oracle (do not edit).

---

## Honesty notes

- Prices are RuneLite trades — they will **not** match the lagged GE guide price.
- News / catalyst badges are keyword matches on the OSRS news RSS — hints only.
- Dip/flip/risk and merch weights are heuristics — **not backtested**.
- Tax-exempt items (notably the Old school bond) are still taxed in code, so
  profit is understated, never overstated.

---

## Deploying

### Production (current)

DigitalOcean droplet. Push to `main` deploys via
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)
(SSH → `git pull` → `pip install` → restart systemd units).

```bash
ssh <SERVER_USER>@157.230.53.158
cd /var/www/wiseoldtools/osrs-project
source venv/bin/activate
systemctl restart osrs-app
systemctl restart osrs-scanner
```

Secrets live in `/var/www/wiseoldtools/osrs-project/.env` (gitignored). The
droplet uses `venv/`; local docs use `.venv/` (both ignored).

### Alternate: Docker Compose + Cloudflare Tunnel

Still in-repo under `docker-compose.yml` and `deploy/` for a self-hosted
Postgres + tunnel setup. Hostname stays **https://scan.wiseoldtools.com**.
Prefer the systemd path above unless you intentionally run Compose.

```bash
docker compose up -d --build db app
docker compose exec app python backfill_history.py
docker compose --profile tunnel up -d tunnel   # when cutting over public traffic
```

`CLAN_PASSWORD` is required when `HOST` is not loopback (Compose sets
`HOST=0.0.0.0`).

### Split roles (optional)

`ROLE=writer` (scheduler only) and `ROLE=api` (HTTP only) sharing Postgres —
not required at clan scale; local `ROLE=all` + SQLite is enough for development.
