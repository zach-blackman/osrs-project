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
    sa.Index("ix_picks_scan", "scan_id"),
)

_COLUMN_FOR_KEY = {"id": "item_id", "limit": "buy_limit", "now": "price_now"}
_KEY_FOR_COLUMN = {v: k for k, v in _COLUMN_FOR_KEY.items()}

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


def init_db():
    eng = engine()
    metadata.create_all(eng)
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
    """Persist one completed scan. Returns the new scan id."""
    with engine().begin() as cx:
        scan_id = cx.execute(sa.insert(scans).values(
            started_at=meta["started_at"],
            finished_at=meta.get("finished_at") or time.time(),
            status="ok",
            price_ts=meta.get("price_ts"),
            params=meta.get("params") or {},
            n_items=len(market_rows),
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
        cx.execute(sa.delete(scans).where(scans.c.id.in_(doomed)))
    return len(doomed)
