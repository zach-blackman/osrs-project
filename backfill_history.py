#!/usr/bin/env python3
"""One-time (resumable) backfill of `item_daily_history` — see
MIGRATION_PLAN_V2.md §2. Pulls a year of daily candles per tradeable item via
the same `/timeseries` endpoint `scanner.enrich()` already uses, but only
ever needs to do it once: `scan_market_fast` reads history from the DB
instead of re-fetching it every 10 minutes.

Idempotent: re-running skips items that already look complete (>= --min-days
rows) and, for partially-backfilled items, skips days already stored. Safe
to kill and restart at any point.

Usage:
    python backfill_history.py                  # backfill everything missing
    python backfill_history.py --limit 50        # smoke-test on 50 items
    python backfill_history.py --verify 20        # spot-check 20 items
"""

import argparse
import statistics
import sys
import time
from datetime import date, datetime, timezone

import config   # noqa: F401 — applies UA override before scanner is used
import db
import scanner


def _candle_to_row(item_id, point):
    """One /timeseries point -> an item_daily_history row, or None if empty.
    Mirrors the midpoint logic in scanner.enrich() (scanner.py:211-216)."""
    hi, lo = point.get("avgHighPrice"), point.get("avgLowPrice")
    if hi is None and lo is None:
        return None
    price = (hi + lo) / 2 if (hi and lo) else (hi or lo)
    day = datetime.fromtimestamp(point["timestamp"], tz=timezone.utc).date()
    volume = (point.get("highPriceVolume") or 0) + (point.get("lowPriceVolume") or 0)
    return {"item_id": item_id, "day": day, "price": price, "volume": volume,
            "source": "backfill"}


def backfill(min_days=350, limit=None, sleep=None, verbose=True):
    sleep = config.SLEEP if sleep is None else sleep

    mapping = scanner.get("/mapping")
    tradeable = [m for m in mapping if (m.get("limit") or 0) > 0]
    if verbose:
        print(f"/mapping: {len(mapping)} items, {len(tradeable)} tradeable "
              f"(limit > 0)", file=sys.stderr)

    now = time.time()
    db.upsert_items([{"id": m["id"], "name": m["name"], "limit": m.get("limit"),
                       "members": m.get("members"),
                       "highalch": m.get("highalch"),
                       "value": m.get("value")} for m in tradeable], now)

    ids = [m["id"] for m in tradeable]
    depth = db.history_depth(ids)
    todo = [m for m in tradeable if depth.get(m["id"], 0) < min_days]
    if verbose:
        print(f"{len(tradeable) - len(todo)} items already have >= {min_days} "
              f"days stored; {len(todo)} need work", file=sys.stderr)

    if limit:
        todo = todo[:limit]

    done = errors = 0
    for i, meta in enumerate(todo, 1):
        iid, name = meta["id"], meta["name"]
        try:
            existing_days = db.history_days_present(iid)
            data = scanner.get("/timeseries", id=iid, timestep="24h").get("data", [])
            rows = [_candle_to_row(iid, p) for p in data]
            rows = [r for r in rows if r and r["day"] not in existing_days]
            db.insert_daily_history(rows)
            done += 1
            if verbose:
                print(f"[{i}/{len(todo)}] {name} — +{len(rows)} days "
                      f"(had {len(existing_days)})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — one bad item must not kill the run
            errors += 1
            print(f"[{i}/{len(todo)}] {name} — ERROR: {e}", file=sys.stderr)
        time.sleep(sleep)

    if verbose:
        print(f"backfill done: {done} items updated, {errors} errors", file=sys.stderr)
    return done, errors


def verify(sample_n=20):
    """Spot-check: for a random sample of already-backfilled items, compare
    our stored last-30-days midpoints against a fresh live /timeseries call.
    Confirms the backfill matches the wiki's own numbers before scan_market_fast
    is allowed to depend on them (MIGRATION_PLAN_V2.md §2.4)."""
    import random

    with db.engine().connect() as cx:
        import sqlalchemy as sa
        rows = cx.execute(
            sa.select(db.items.c.item_id, db.items.c.name)
            .join(db.item_daily_history,
                  db.items.c.item_id == db.item_daily_history.c.item_id)
            .group_by(db.items.c.item_id, db.items.c.name)
            .having(sa.func.count() >= 45)
        ).all()
    if not rows:
        print("nothing backfilled yet to verify", file=sys.stderr)
        return

    sample = random.sample(rows, min(sample_n, len(rows)))
    stored = db.daily_history_for([r.item_id for r in sample])

    mismatches = 0
    for r in sample:
        live = scanner.get("/timeseries", id=r.item_id, timestep="24h").get("data", [])
        live_by_day = {}
        for p in live:
            row = _candle_to_row(r.item_id, p)
            if row:
                live_by_day[row["day"]] = row["price"]

        ours = {d: price for d, price, _ in stored.get(r.item_id, [])}
        common = sorted(set(ours) & set(live_by_day))[-30:]
        bad = [d for d in common if abs(ours[d] - live_by_day[d]) > max(1, ours[d] * 0.001)]
        status = "OK" if not bad else f"MISMATCH on {len(bad)}/{len(common)} days"
        print(f"{r.name} ({r.item_id}): {len(common)} days compared — {status}")
        if bad:
            mismatches += 1
        time.sleep(config.SLEEP)

    print(f"\n{len(sample) - mismatches}/{len(sample)} items matched exactly.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-days", type=int, default=350,
                     help="skip items already stored at least this many days deep")
    ap.add_argument("--limit", type=int, default=None,
                     help="only process the first N items needing work (smoke test)")
    ap.add_argument("--sleep", type=float, default=None,
                     help="override the politeness delay between items")
    ap.add_argument("--verify", type=int, default=None, metavar="N",
                     help="skip backfilling; spot-check N already-backfilled items instead")
    args = ap.parse_args()

    db.init_db()
    if args.verify is not None:
        verify(args.verify)
    else:
        backfill(min_days=args.min_days, limit=args.limit, sleep=args.sleep)
