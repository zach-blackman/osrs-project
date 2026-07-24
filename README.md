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

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/picks?capital=&floor=&top=&mode=` | Personalised picks from the latest snapshot. `503` until the first scan lands. |
| `GET /api/status` | Snapshot age, next scheduled run, whether a scan is in flight. |
| `POST /api/refresh` | Trigger a scan. Single-flight: returns `started: false` if one is already running or if it was debounced (30 s). |
| `GET /api/refresh/stream` | SSE `phase` / `log` / `progress` / `result` events, replayed from the start of the current scan. |
| `GET /api/scans?limit=` | Snapshot history. |
| `GET /healthz` | Open (no auth) — snapshot presence and age. |

The wiki is only ever contacted by the writer, on a schedule, with the custom
user agent and the existing retry/backoff. Readers never call it.

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

## Deploying

Deliberately not set up yet: this runs locally while the scoring is being
trialled. When it moves to a host, the shape is one always-on service running
the API with the in-process scheduler, plus managed Postgres via
`DATABASE_URL`, `CLAN_PASSWORD` set, and `HOST=0.0.0.0`.
