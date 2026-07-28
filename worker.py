"""The writer: the only process that ever talks to the wiki.

Three jobs live here.

1. `run_and_store` — scan_market -> write_snapshot.
2. Single-flight — concurrent refresh requests attach to the ONE scan already
   in flight rather than launching a second one. The wiki must see one polite
   scanner, no matter how many clan members mash the button.
3. Fan-out — the scan's progress events are broadcast to every connected SSE
   client, and buffered so a client that connects mid-scan still sees the log
   from the top.
"""

import logging
import queue
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import config
import db
import scanner

log = logging.getLogger("osrs.worker")

# A manual refresh this soon after the last one finished is refused; the
# snapshot cannot have meaningfully changed and the wiki should not be asked.
MANUAL_DEBOUNCE_SEC = 30

# Cap on the replay buffer, so a long scan cannot grow it without bound.
MAX_BUFFER = 600

# Below this many 10-min samples in a day, prefer timeseries gap-fill over
# storing a low-confidence self-aggregated candle (MIGRATION_PLAN_V2.md §4).
# SCAN_INTERVAL_MIN=10 gives up to 144 samples/day; ~70% is a reasonable bar.
MIN_SNAPSHOTS_PER_DAY = 100


class ScanRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._subscribers = set()
        self._buffer = []
        self._scanning = False
        self._started_at = None
        self._last_finished = None
        self._last_error = None
        self._last_scan_id = None

    # ------------------------------------------------------------ pub/sub

    def subscribe(self):
        """A queue fed every event of the current scan, replay included."""
        q = queue.Queue(maxsize=2000)
        with self._lock:
            for event in self._buffer:
                q.put_nowait(event)
            if not self._scanning:
                # Nothing in flight: tell the client so it does not hang.
                q.put_nowait({"kind": "idle"})
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def _publish(self, event):
        with self._lock:
            self._buffer.append(event)
            if len(self._buffer) > MAX_BUFFER:
                del self._buffer[:len(self._buffer) - MAX_BUFFER]
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A client that cannot keep up loses lines, not the scan.
                pass

    # ------------------------------------------------------------- status

    def status(self):
        with self._lock:
            return {
                "scanning": self._scanning,
                "started_at": self._started_at,
                "last_finished": self._last_finished,
                "last_error": self._last_error,
                "last_scan_id": self._last_scan_id,
            }

    # ------------------------------------------------------------ trigger

    def trigger(self, source="manual"):
        """Start a scan unless one is running (or just ran). Never blocks.

        Returns a dict describing what happened, so the caller can tell
        "yours started" from "you attached to one already running"."""
        with self._lock:
            if self._scanning:
                return {"started": False, "scanning": True,
                        "reason": "attached to the scan already running"}
            if (source == "manual" and self._last_finished
                    and time.time() - self._last_finished < MANUAL_DEBOUNCE_SEC):
                wait = MANUAL_DEBOUNCE_SEC - (time.time() - self._last_finished)
                return {"started": False, "scanning": False,
                        "reason": f"just scanned; try again in {wait:.0f}s",
                        "retry_after": int(wait) + 1}
            self._scanning = True
            self._started_at = time.time()
            self._buffer = []
            self._thread = threading.Thread(
                target=self._run, args=(source,),
                name="scan-writer", daemon=True)
        self._thread.start()
        return {"started": True, "scanning": True, "reason": None}

    def _run(self, source):
        started_at = time.time()
        cfg = config.scan_config()
        self._publish({"kind": "phase", "label": f"Scan starting ({source})"})
        try:
            if config.FAST_SCAN:
                ready = db.history_ready_count()
                if ready < config.MIN_HISTORY_READY:
                    raise RuntimeError(
                        f"FAST_SCAN refused: only {ready} items have "
                        f">={config.MIN_HISTORY_DAYS} days of history "
                        f"(need >={config.MIN_HISTORY_READY}). "
                        "Run backfill_history.py or set FAST_SCAN=0.")
                meta, rows = scanner.scan_market_fast(
                    cfg, db.daily_history_for, on_event=self._on_event)
            else:
                meta, rows = scanner.scan_market(cfg, on_event=self._on_event)
            if len(rows) < config.MIN_SCORED_ITEMS:
                meta["degraded_reason"] = (
                    f"scan scored {len(rows)} items "
                    f"(need >={config.MIN_SCORED_ITEMS})")
                log.warning("degraded snapshot: %s", meta["degraded_reason"])
            scan_id = db.write_snapshot(meta, rows)
            if config.FAST_SCAN:
                finished = meta.get("finished_at") or time.time()
                db.upsert_items(meta.get("item_meta") or [], finished)
                db.insert_snapshots(scan_id, meta.get("raw_snapshots") or [])
                db.prune_snapshots()
            db.prune()
            with self._lock:
                self._last_scan_id = scan_id
                self._last_error = meta.get("degraded_reason")
            kind = "result" if len(rows) >= config.MIN_SCORED_ITEMS else "error"
            payload = {
                "kind": kind, "scan_id": scan_id, "n_items": len(rows),
                "updated_at": meta.get("finished_at"),
            }
            if meta.get("degraded_reason"):
                payload["error"] = meta["degraded_reason"]
                payload["degraded"] = True
            self._publish(payload)
        except Exception as e:  # noqa: BLE001 — any failure must be visible
            log.exception("scan failed (%s)", source)
            db.record_error(started_at, e, cfg.as_params())
            with self._lock:
                self._last_error = str(e)
            self._publish({"kind": "error", "error": str(e)})
        finally:
            with self._lock:
                self._scanning = False
                self._last_finished = time.time()

    def _on_event(self, kind, **data):
        self._publish({"kind": kind, **data})


runner = ScanRunner()
scheduler = BackgroundScheduler(daemon=True)


def _scheduled_scan():
    runner.trigger(source="schedule")


def _gapfill_candle(item_id, day):
    """Fetch one day's wiki candle for an item that lacked enough snapshots."""
    try:
        return scanner.fetch_daily_candle(item_id, day, sleep=config.SLEEP)
    except Exception as e:  # noqa: BLE001
        log.warning("gap-fill failed for item %s day %s: %s", item_id, day, e)
        return None


def _daily_rollover():
    """Fold yesterday's item_snapshots into item_daily_history — prefer
    self-aggregation, fall back to /timeseries for thin days. See
    MIGRATION_PLAN_V2.md §4. No-op when FAST_SCAN is off."""
    if not config.FAST_SCAN:
        return

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    start = datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    end = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp()

    raw = db.snapshots_between(start, end)
    by_item = {}
    for r in raw:
        by_item.setdefault(r["item_id"], []).append(r)

    # Also gap-fill items that appear in the items table but had zero snapshots
    # yesterday (full outage for that item) — only those already in history so
    # we do not invent series for never-scored ids.
    if not by_item:
        log.info("daily rollover: no snapshots for %s — skipping", yesterday)
        return

    already = db.history_day_rows_present(yesterday, list(by_item))
    rows = []
    gapfilled = 0
    skipped_thin = 0
    for item_id, samples in by_item.items():
        if item_id in already:
            continue
        if len(samples) >= MIN_SNAPSHOTS_PER_DAY:
            prices = [(s["high"] + s["low"]) / 2 for s in samples
                      if s["high"] and s["low"]]
            if not prices:
                continue
            rows.append({
                "item_id": item_id,
                "day": yesterday,
                "price": statistics.mean(prices),
                "volume": sum((s.get("buy_vol_1h") or 0) + (s.get("sell_vol_1h") or 0)
                              for s in samples),
                "source": "rollover",
            })
            continue
        # Thin day → timeseries gap-fill instead of a low-confidence aggregate.
        candle = _gapfill_candle(item_id, yesterday)
        if candle:
            rows.append(candle)
            gapfilled += 1
        else:
            skipped_thin += 1

    db.insert_daily_history(rows)
    log.info("daily rollover %s: +%d rows (%d gap-filled, %d thin skipped)",
             yesterday, len(rows), gapfilled, skipped_thin)


def start_scheduler():
    """Begin the interval job. Idempotent; safe if already running."""
    if scheduler.running:
        return scheduler
    scheduler.add_job(_scheduled_scan, "interval",
                      minutes=config.SCAN_INTERVAL_MIN,
                      id="scan", max_instances=1, coalesce=True,
                      replace_existing=True)
    scheduler.add_job(_daily_rollover, "cron", hour=0, minute=5,
                      id="daily_rollover", max_instances=1, coalesce=True,
                      replace_existing=True)
    scheduler.start()
    if config.SCAN_ON_STARTUP and db.latest_ok_scan() is None:
        # Cold start: an empty database has nothing to serve, so fill it.
        runner.trigger(source="startup")
    return scheduler


def next_run_at():
    job = scheduler.get_job("scan") if scheduler.running else None
    return job.next_run_time.timestamp() if job and job.next_run_time else None


def run_and_store(cfg=None, on_event=None):
    """Synchronous scan-and-store. Used by the CLI path and by tests; the
    server goes through `runner.trigger` so single-flight is enforced."""
    cfg = cfg or config.scan_config()
    meta, rows = scanner.scan_market(cfg, on_event=on_event)
    return db.write_snapshot(meta, rows), meta, rows


def run_writer_forever():
    """ROLE=writer entry: scheduler only, no HTTP. Blocks until interrupted."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()
    start_scheduler()
    log.info("writer role started (SCAN_INTERVAL_MIN=%s FAST_SCAN=%s)",
             config.SCAN_INTERVAL_MIN, config.FAST_SCAN)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("writer stopping")
        if scheduler.running:
            scheduler.shutdown(wait=False)
