"""Phase-2 storage / readiness / rollover gap-fill tests — fully offline.

Run: .venv/bin/python tests/test_phase2.py
"""

import os
import pathlib
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_TMP = tempfile.mkdtemp(prefix="osrs-phase2-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "phase2.db")
os.environ["SCAN_ON_STARTUP"] = "0"
os.environ["SLEEP"] = "0"
os.environ["FAST_SCAN"] = "1"
os.environ["MIN_HISTORY_READY"] = "2"
os.environ["MIN_SCORED_ITEMS"] = "1"
os.environ["READY_MAX_AGE_MIN"] = "30"
os.environ["ROLE"] = "all"

import db  # noqa: E402
import worker  # noqa: E402
import scanner  # noqa: E402

failures = []


def check(cond, label):
    print(("  ok  " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


def main():
    db.init_db()
    now = time.time()

    print("upsert_items refreshes metadata")
    db.upsert_items([{"id": 1, "name": "Old name", "limit": 10, "members": True,
                       "highalch": 500, "value": 300}], now)
    db.upsert_items([{"id": 1, "name": "New name", "limit": 25, "members": False,
                       "highalch": 900, "value": 400}], now + 1)
    with db.engine().connect() as cx:
        import sqlalchemy as sa
        row = cx.execute(sa.select(db.items).where(db.items.c.item_id == 1)).mappings().first()
    check(row["name"] == "New name" and row["buy_limit"] == 25, "name/limit updated on upsert")
    check(row["members"] is False, "members flag updated on upsert")
    check(row["highalch"] == 900 and row["value"] == 400, "highalch/value updated on upsert")
    # Partial rows must not wipe known alch data.
    db.upsert_items([{"id": 1, "name": "Same name", "limit": 25, "members": False}], now + 2)
    with db.engine().connect() as cx:
        import sqlalchemy as sa
        row = cx.execute(sa.select(db.items).where(db.items.c.item_id == 1)).mappings().first()
    check(row["highalch"] == 900, "partial upsert preserves highalch")

    print("\nhistory_ready_count gates FAST_SCAN")
    check(db.history_ready_count(min_days=45) == 0, "cold DB has zero ready history")
    db.upsert_items([
        {"id": 2, "name": "Item two", "limit": 10, "members": True},
        {"id": 3, "name": "Item three", "limit": 5, "members": True},
    ], now)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(50)]
    db.insert_daily_history([
        {"item_id": 1, "day": d, "price": 1000 + i, "volume": 10, "source": "backfill"}
        for i, d in enumerate(days)
    ])
    db.insert_daily_history([
        {"item_id": 2, "day": d, "price": 2000 + i, "volume": 5, "source": "backfill"}
        for i, d in enumerate(days)
    ])
    # item 3 too thin
    db.insert_daily_history([
        {"item_id": 3, "day": days[0], "price": 50, "volume": 1, "source": "backfill"}
    ])
    check(db.history_ready_count(min_days=45) == 2, "two items meet the 45-day bar")

    print("\nempty write_snapshot is degraded, not ok")
    meta = {"started_at": now, "finished_at": now, "params": {},
            "degraded_reason": "test empty"}
    sid = db.write_snapshot(meta, [])
    with db.engine().connect() as cx:
        import sqlalchemy as sa
        st = cx.execute(sa.select(db.scans.c.status, db.scans.c.n_items)
                        .where(db.scans.c.id == sid)).one()
    check(st.status == "degraded" and st.n_items == 0, "empty snapshot stored as degraded")
    check(db.latest_ok_scan() is None, "latest_ok_scan ignores degraded")

    print("\nok snapshot + readiness")
    market = [{
        "id": 1, "name": "New name", "buy_price": 1000, "sell_price": 1100,
        "tax": 22, "sell_net": 1078, "margin": 78, "roi": 7.8, "now": 1050,
        "vol_day": 100, "limit": 25, "volatility": 5.0, "trend": "flat",
        "rank_all": 0.4, "rank90": 0.5, "z30": -0.2, "catalyst": None,
        "catalyst_bonus": 0, "spark": [1000] * 30, "buy_vol_1h": 4, "sell_vol_1h": 3,
    }]
    # Fill required MARKET_KEYS that may be missing
    for k in scanner.MARKET_KEYS:
        market[0].setdefault(k, None)
    market[0].update({"id": 1, "name": "New name", "buy_price": 1000, "sell_price": 1100,
                       "tax": 22, "sell_net": 1078, "margin": 78, "roi": 7.8})
    ok_id = db.write_snapshot({"started_at": now, "finished_at": now, "params": {}}, market)
    latest = db.latest_ok_scan()
    check(latest is not None and latest["id"] == ok_id, "ok snapshot is readable")
    probe = db.readiness()
    check(probe["ready"] is True, f"readiness ready ({probe})")
    check(probe["history_ready"] == 2, "readiness reports history_ready")

    print("\nFAST_SCAN refused without enough history")
    # Temporarily raise the bar above what we stored.
    import config
    old_ready = config.MIN_HISTORY_READY
    config.MIN_HISTORY_READY = 99
    config.FAST_SCAN = True
    started = worker.runner.trigger(source="test")
    check(started["started"], "trigger accepted")
    end = time.monotonic() + 10
    while time.monotonic() < end and worker.runner.status()["scanning"]:
        time.sleep(0.05)
    err = worker.runner.status()["last_error"]
    check(err and "FAST_SCAN refused" in err, f"writer error mentions refuse ({err!r})")
    config.MIN_HISTORY_READY = old_ready

    print("\nrollover aggregates fat days and gap-fills thin ones")
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    # Fake a scan row in yesterday's window
    with db.engine().begin() as cx:
        import sqlalchemy as sa
        scan_id = cx.execute(sa.insert(db.scans).values(
            started_at=start + 60, finished_at=start + 120, status="ok",
            n_items=0, params={})).inserted_primary_key[0]
    # Item 1: enough samples to self-aggregate
    fat = [{"id": 1, "high": 1100 + i, "low": 1000 + i, "buy_vol_1h": 2, "sell_vol_1h": 1}
           for i in range(worker.MIN_SNAPSHOTS_PER_DAY)]
    db.insert_snapshots(scan_id, fat)
    # Item 2: thin → gap-fill path
    db.insert_snapshots(scan_id, [
        {"id": 2, "high": 2100, "low": 2000, "buy_vol_1h": 1, "sell_vol_1h": 1}
    ])

    calls = {"n": 0}
    real_fetch = scanner.fetch_daily_candle

    def fake_fetch(item_id, day, sleep=0.6):
        calls["n"] += 1
        return {"item_id": item_id, "day": day, "price": 2050.0, "volume": 9,
                "source": "gapfill"}

    scanner.fetch_daily_candle = fake_fetch
    try:
        worker._daily_rollover()
    finally:
        scanner.fetch_daily_candle = real_fetch

    present = db.history_day_rows_present(yesterday, [1, 2])
    check(1 in present, "fat item rolled into daily history")
    check(2 in present, "thin item gap-filled into daily history")
    check(calls["n"] == 1, f"gap-fill called once (got {calls['n']})")

    with db.engine().connect() as cx:
        import sqlalchemy as sa
        srcs = {r.item_id: r.source for r in cx.execute(
            sa.select(db.item_daily_history.c.item_id, db.item_daily_history.c.source)
            .where(db.item_daily_history.c.day == yesterday))}
    check(srcs.get(1) == "rollover", "fat day source=rollover")
    check(srcs.get(2) == "gapfill", "thin day source=gapfill")

    print()
    if failures:
        print(f"FAILED ({len(failures)} problems)")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
