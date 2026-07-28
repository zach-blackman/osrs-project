"""Storage for market snapshots.

One code path over SQLAlchemy Core serves both SQLite (the local default) and
Postgres, so moving to a managed database is a `DATABASE_URL` change and
nothing else.

A *snapshot* is one capital-agnostic scan: a `scans` row plus one `picks` row
per item. Rows come from and go back to `scanner.MARKET_KEYS`, which is what
`scanner.rank_for` consumes — nothing user-specific is ever stored, because
capital and floor are applied at read time.
"""

import json
import threading
import time

import sqlalchemy as sa

import config
import scanner

metadata = sa.MetaData()

scans = sa.Table(
    "scans", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("started_at", sa.Float, nullable=False),
    sa.Column("finished_at", sa.Float),
    sa.Column("status", sa.String(16), nullable=False),   # ok | running | error
    sa.Column("price_ts", sa.Float),
    sa.Column("params", sa.JSON),
    sa.Column("n_items", sa.Integer, nullable=False, default=0),
    sa.Column("error", sa.Text),
    sa.Index("ix_scans_status_finished", "status", "finished_at"),
)

# Column names deliberately differ from the row keys in two places: `id` and
# `limit` would collide with the primary key and a SQL keyword. _to_row and
# _from_row own that translation so no caller has to think about it.
picks = sa.Table(
    "picks", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("scan_id", sa.Integer,
              sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False),
    sa.Column("item_id", sa.Integer, nullable=False),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("buy_price", sa.Integer),
    sa.Column("sell_price", sa.Integer),
    sa.Column("tax", sa.Integer),
    sa.Column("sell_net", sa.Integer),
    sa.Column("margin", sa.Float),
    sa.Column("roi", sa.Float),
    sa.Column("price_now", sa.Float),
    sa.Column("vol_day", sa.Float),
    sa.Column("buy_limit", sa.Integer),
    sa.Column("volatility", sa.Float),
    sa.Column("trend", sa.String(16)),
    sa.Column("rank_all", sa.Float),
    sa.Column("rank90", sa.Float),
    sa.Column("z30", sa.Float),
    sa.Column("catalyst", sa.Text),
    sa.Column("catalyst_bonus", sa.Float),
    sa.Column("spark", sa.JSON),
    sa.Column("buy_vol_1h", sa.Integer),
    sa.Column("sell_vol_1h", sa.Integer),
    sa.Index("ix_picks_scan", "scan_id"),
)

# ---------------------------------------------------- phase-2: all-items DB
# See MIGRATION_PLAN_V2.md. `items`/`item_daily_history` are long-lived and
# refreshed cheaply; `item_snapshots` is the new 10-min pulse across every
# tradeable item, replacing the shortlist-gated `picks` table as the scoring
# input. `picks` stays untouched until the fast path is validated.

items = sa.Table(
    "items", metadata,
    sa.Column("item_id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("buy_limit", sa.Integer),
    sa.Column("members", sa.Boolean),
    sa.Column("first_seen", sa.Float, nullable=False),
    sa.Column("last_seen", sa.Float, nullable=False),
)

item_daily_history = sa.Table(
    "item_daily_history", metadata,
    sa.Column("item_id", sa.Integer,
              sa.ForeignKey("items.item_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("price", sa.Float, nullable=False),
    sa.Column("volume", sa.Integer),
    sa.Column("source", sa.String(16), nullable=False),   # 'backfill' | 'rollover'
)

item_snapshots = sa.Table(
    "item_snapshots", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("scan_id", sa.Integer,
              sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False),
    sa.Column("item_id", sa.Integer, nullable=False),
    sa.Column("high", sa.Integer),
    sa.Column("low", sa.Integer),
    sa.Column("buy_vol_1h", sa.Integer),
    sa.Column("sell_vol_1h", sa.Integer),
    sa.Index("ix_item_snapshots_scan", "scan_id"),
    sa.Index("ix_item_snapshots_item", "item_id"),
)

_COLUMN_FOR_KEY = {"id": "item_id", "limit": "buy_limit", "now": "price_now"}
_KEY_FOR_COLUMN = {v: k for k, v in _COLUMN_FOR_KEY.items()}

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER can be as low as 999; chunk any
# large `IN (...)` clause so item-count growth (now up to ~4000) can't trip it.
_IN_CHUNK = 500


def _chunked(seq, size=_IN_CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

_engine = None
_engine_lock = threading.Lock()


def engine():
    """Lazily-built, process-wide engine. Safe to call from any thread."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = config.DATABASE_URL
                kwargs = {"future": True, "pool_pre_ping": True}
                if url.startswith("sqlite"):
                    # The scheduler thread and request handlers share this
                    # engine; SQLite needs to be told that is allowed.
                    kwargs["connect_args"] = {"check_same_thread": False}
                    kwargs.pop("pool_pre_ping")
                _engine = sa.create_engine(url, **kwargs)
    return _engine


def _migrate_missing_columns(eng):
    """create_all only creates missing TABLES, not missing COLUMNS on tables
    that already exist. buy_vol_1h/sell_vol_1h were added after the initial
    schema, so backfill them onto any pre-existing `picks` table."""
    inspector = sa.inspect(eng)
    if "picks" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("picks")}
    with eng.begin() as cx:
        for col in picks.columns:
            if col.name not in existing and col.name in ("buy_vol_1h", "sell_vol_1h"):
                coltype = col.type.compile(eng.dialect)
                cx.exec_driver_sql(f"ALTER TABLE picks ADD COLUMN {col.name} {coltype}")


def init_db():
    eng = engine()
    metadata.create_all(eng)
    _migrate_missing_columns(eng)
    if eng.dialect.name == "sqlite":
        with eng.begin() as cx:
            # WAL lets the reader serve requests while the writer commits.
            cx.exec_driver_sql("PRAGMA journal_mode=WAL")
            cx.exec_driver_sql("PRAGMA foreign_keys=ON")


def _to_row(scan_id, market_row):
    out = {"scan_id": scan_id}
    for key in scanner.MARKET_KEYS:
        out[_COLUMN_FOR_KEY.get(key, key)] = market_row.get(key)
    return out


def _from_row(record):
    out = {}
    for column, value in record.items():
        key = _KEY_FOR_COLUMN.get(column, column)
        if key in ("scan_id", "id"):
            continue
        out[key] = value
    out["id"] = record["item_id"]
    if isinstance(out.get("spark"), str):     # sqlite JSON round-trip guard
        out["spark"] = json.loads(out["spark"])
    return out


def write_snapshot(meta, market_rows):
    """Persist one completed scan. Returns the new scan id.

    Thin or empty results are stored as status=degraded so readiness and
    latest_ok_scan do not treat "no scored items" as a healthy snapshot.
    """
    n = len(market_rows)
    status = "ok" if n >= config.MIN_SCORED_ITEMS else "degraded"
    params = dict(meta.get("params") or {})
    if status == "degraded":
        params["degraded_reason"] = meta.get("degraded_reason") or (
            f"only {n} scored items (need >= {config.MIN_SCORED_ITEMS})")
    with engine().begin() as cx:
        scan_id = cx.execute(sa.insert(scans).values(
            started_at=meta["started_at"],
            finished_at=meta.get("finished_at") or time.time(),
            status=status,
            price_ts=meta.get("price_ts"),
            params=params,
            n_items=n,
            error=params.get("degraded_reason") if status == "degraded" else None,
        )).inserted_primary_key[0]
        if market_rows:
            cx.execute(sa.insert(picks),
                       [_to_row(scan_id, r) for r in market_rows])
    return scan_id


def record_error(started_at, message, params=None):
    """Persist a failed scan so staleness has a visible cause."""
    with engine().begin() as cx:
        return cx.execute(sa.insert(scans).values(
            started_at=started_at, finished_at=time.time(), status="error",
            params=params or {}, n_items=0, error=str(message)[:2000],
        )).inserted_primary_key[0]


def latest_ok_scan():
    """The freshest usable snapshot's metadata, or None if there is none."""
    stmt = (sa.select(scans)
            .where(scans.c.status == "ok")
            .order_by(scans.c.finished_at.desc())
            .limit(1))
    with engine().connect() as cx:
        row = cx.execute(stmt).mappings().first()
    return dict(row) if row else None


def read_picks(scan_id):
    """Market rows for one snapshot, keyed exactly as scanner.rank_for wants."""
    stmt = sa.select(picks).where(picks.c.scan_id == scan_id)
    with engine().connect() as cx:
        return [_from_row(r) for r in cx.execute(stmt).mappings()]


def item_history(item_id, limit=50):
    """One item's stored market data across recent scans, oldest first — the
    scan-over-scan view in the drill-down. Bounded by KEEP_SCANS, so this
    covers roughly the last SCAN_INTERVAL_MIN * KEEP_SCANS of wall time, not
    a long history (that's what the wiki-sourced `spark` field is for)."""
    stmt = (sa.select(picks.c.buy_price, picks.c.sell_price, picks.c.margin,
                       picks.c.roi, picks.c.rank_all, picks.c.trend,
                       scans.c.finished_at)
            .join(scans, picks.c.scan_id == scans.c.id)
            .where(picks.c.item_id == item_id, scans.c.status == "ok")
            .order_by(scans.c.finished_at.desc())
            .limit(limit))
    with engine().connect() as cx:
        rows = [dict(r) for r in cx.execute(stmt).mappings()]
    return list(reversed(rows))


def list_scans(limit=20):
    stmt = (sa.select(scans).order_by(scans.c.started_at.desc()).limit(limit))
    with engine().connect() as cx:
        return [dict(r) for r in cx.execute(stmt).mappings()]


def prune(keep=None):
    """Drop all but the `keep` most recent scans. Returns how many went."""
    keep = config.KEEP_SCANS if keep is None else keep
    with engine().begin() as cx:
        ids = [r[0] for r in cx.execute(
            sa.select(scans.c.id).order_by(scans.c.started_at.desc()))]
        doomed = ids[keep:]
        if not doomed:
            return 0
        # Explicit child delete: SQLite only enforces ON DELETE CASCADE when
        # the foreign_keys pragma is on, and Postgres does not need the help.
        cx.execute(sa.delete(picks).where(picks.c.scan_id.in_(doomed)))
        cx.execute(sa.delete(item_snapshots).where(item_snapshots.c.scan_id.in_(doomed)))
        cx.execute(sa.delete(scans).where(scans.c.id.in_(doomed)))
    return len(doomed)


# ---------------------------------------------------- phase-2: items table

def upsert_items(item_rows, ts):
    """Insert never-seen items; refresh name/limit/members and bump last_seen
    on every item that showed up this cycle. `item_rows`: id/name/limit/members."""
    if not item_rows:
        return
    ids = [r["id"] for r in item_rows]
    by_id = {r["id"]: r for r in item_rows}
    with engine().begin() as cx:
        existing = set()
        for chunk in _chunked(ids):
            existing.update(r[0] for r in cx.execute(
                sa.select(items.c.item_id).where(items.c.item_id.in_(chunk))))
        new_rows = [{"item_id": r["id"], "name": r["name"],
                     "buy_limit": r.get("limit"), "members": r.get("members"),
                     "first_seen": ts, "last_seen": ts}
                    for r in item_rows if r["id"] not in existing]
        if new_rows:
            cx.execute(sa.insert(items), new_rows)
        for iid in existing:
            r = by_id[iid]
            cx.execute(sa.update(items).where(items.c.item_id == iid).values(
                name=r["name"],
                buy_limit=r.get("limit"),
                members=r.get("members"),
                last_seen=ts,
            ))


# ------------------------------------------- phase-2: daily history (long-lived)

def history_days_present(item_id):
    """Set of `day`s already stored for one item — powers the resumable,
    idempotent backfill (skip days that are already there)."""
    stmt = (sa.select(item_daily_history.c.day)
            .where(item_daily_history.c.item_id == item_id))
    with engine().connect() as cx:
        return {r[0] for r in cx.execute(stmt)}


def history_depth(item_ids):
    """{item_id: n_days_stored} for the given items — powers the backfill's
    'skip items that already look complete' resumability check."""
    out = {}
    with engine().connect() as cx:
        for chunk in _chunked(item_ids):
            stmt = (sa.select(item_daily_history.c.item_id,
                               sa.func.count().label("n"))
                    .where(item_daily_history.c.item_id.in_(chunk))
                    .group_by(item_daily_history.c.item_id))
            out.update({r.item_id: r.n for r in cx.execute(stmt)})
    return out


def history_ready_count(min_days=None):
    """How many items have at least `min_days` of daily history stored.
    Used to gate FAST_SCAN so a cold DB cannot silently produce empty picks."""
    min_days = config.MIN_HISTORY_DAYS if min_days is None else min_days
    subq = (sa.select(item_daily_history.c.item_id)
            .group_by(item_daily_history.c.item_id)
            .having(sa.func.count() >= min_days)
            .subquery())
    with engine().connect() as cx:
        return cx.execute(sa.select(sa.func.count()).select_from(subq)).scalar() or 0


def readiness():
    """Probe payload for /healthz: ok snapshot present, fresh, and non-empty."""
    scan = latest_ok_scan()
    if not scan:
        return {
            "ready": False,
            "reason": "no ok snapshot",
            "has_snapshot": False,
            "age_seconds": None,
            "n_items": 0,
            "history_ready": history_ready_count(),
        }
    updated = scan.get("finished_at") or scan.get("started_at")
    age = round(time.time() - updated) if updated else None
    max_age = config.READY_MAX_AGE_MIN * 60
    reasons = []
    if age is None:
        reasons.append("snapshot missing timestamp")
    elif age > max_age:
        reasons.append(f"snapshot age {age}s > {max_age}s")
    if (scan.get("n_items") or 0) < config.MIN_SCORED_ITEMS:
        reasons.append(f"n_items={scan.get('n_items')} below minimum")
    return {
        "ready": not reasons,
        "reason": "; ".join(reasons) if reasons else None,
        "has_snapshot": True,
        "age_seconds": age,
        "n_items": scan.get("n_items") or 0,
        "history_ready": history_ready_count(),
        "scan_id": scan["id"],
    }


def history_day_rows_present(day, item_ids):
    """Subset of item_ids that already have a stored row for exactly `day` —
    lets the daily rollover job stay idempotent if it's ever run twice for
    the same day (one batched query instead of one per item)."""
    out = set()
    with engine().connect() as cx:
        for chunk in _chunked(item_ids):
            stmt = (sa.select(item_daily_history.c.item_id)
                    .where(item_daily_history.c.day == day,
                           item_daily_history.c.item_id.in_(chunk)))
            out.update(r[0] for r in cx.execute(stmt))
    return out


def insert_daily_history(rows):
    """Plain bulk insert. Callers are expected to have already filtered out
    days that exist (see `history_days_present`), so no upsert is needed —
    keeps this dialect-neutral like the rest of the file."""
    if not rows:
        return
    with engine().begin() as cx:
        cx.execute(sa.insert(item_daily_history), rows)


def daily_history_for(item_ids):
    """{item_id: [(day, price, volume), ...]} oldest-first, for every id in
    one batched read — feeds scan_market_fast instead of N /timeseries calls."""
    out = {}
    with engine().connect() as cx:
        for chunk in _chunked(item_ids):
            stmt = (sa.select(item_daily_history.c.item_id, item_daily_history.c.day,
                               item_daily_history.c.price, item_daily_history.c.volume)
                    .where(item_daily_history.c.item_id.in_(chunk))
                    .order_by(item_daily_history.c.item_id, item_daily_history.c.day))
            for r in cx.execute(stmt):
                out.setdefault(r.item_id, []).append((r.day, r.price, r.volume))
    return out


# ------------------------------------------ phase-2: 10-min snapshots (all items)

def insert_snapshots(scan_id, item_rows):
    """item_rows: dicts with id/high/low/buy_vol_1h/sell_vol_1h — the raw
    current-price pulse for every item that passed the cheap data-quality
    filters this cycle (no per-item history call involved)."""
    if not item_rows:
        return
    with engine().begin() as cx:
        cx.execute(sa.insert(item_snapshots), [
            {"scan_id": scan_id, "item_id": r["id"], "high": r["high"], "low": r["low"],
             "buy_vol_1h": r.get("buy_vol_1h"), "sell_vol_1h": r.get("sell_vol_1h")}
            for r in item_rows
        ])


def snapshots_between(start_ts, end_ts):
    """Raw item_snapshots rows for scans that finished in [start_ts, end_ts) —
    feeds the daily rollover's self-aggregation (no wiki call needed)."""
    stmt = (sa.select(item_snapshots.c.item_id, item_snapshots.c.high, item_snapshots.c.low,
                       item_snapshots.c.buy_vol_1h, item_snapshots.c.sell_vol_1h)
            .join(scans, item_snapshots.c.scan_id == scans.c.id)
            .where(scans.c.finished_at >= start_ts, scans.c.finished_at < end_ts,
                   scans.c.status == "ok"))
    with engine().connect() as cx:
        return [dict(r) for r in cx.execute(stmt).mappings()]


def prune_snapshots(keep_days=None):
    """Drop item_snapshots older than `keep_days`. Independent of `prune()`/
    KEEP_SCANS — the daily rollover has already folded anything useful from
    old snapshots into item_daily_history by the time this runs, so raw
    10-min granularity beyond a few days is pure bloat (~4000 rows/cycle)."""
    keep_days = config.SNAPSHOT_KEEP_DAYS if keep_days is None else keep_days
    cutoff = time.time() - keep_days * 86400
    with engine().begin() as cx:
        doomed_scan_ids = [r[0] for r in cx.execute(
            sa.select(scans.c.id).where(scans.c.finished_at < cutoff))]
        deleted = 0
        for chunk in _chunked(doomed_scan_ids):
            result = cx.execute(sa.delete(item_snapshots)
                                 .where(item_snapshots.c.scan_id.in_(chunk)))
            deleted += result.rowcount or 0
    return deleted
