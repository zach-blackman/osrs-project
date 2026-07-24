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

import queue
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

import config
import db
import scanner

# A manual refresh this soon after the last one finished is refused; the
# snapshot cannot have meaningfully changed and the wiki should not be asked.
MANUAL_DEBOUNCE_SEC = 30

# Cap on the replay buffer, so a long scan cannot grow it without bound.
MAX_BUFFER = 600


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
                        "reason": f"just scanned; try again in {wait:.0f}s"}
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
            meta, rows = scanner.scan_market(cfg, on_event=self._on_event)
            scan_id = db.write_snapshot(meta, rows)
            db.prune()
            with self._lock:
                self._last_scan_id = scan_id
                self._last_error = None
            self._publish({
                "kind": "result", "scan_id": scan_id, "n_items": len(rows),
                "updated_at": meta.get("finished_at"),
            })
        except Exception as e:  # noqa: BLE001 — any failure must be visible
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


def start_scheduler():
    """Begin the interval job. Idempotent; safe if already running."""
    if scheduler.running:
        return scheduler
    scheduler.add_job(_scheduled_scan, "interval",
                      minutes=config.SCAN_INTERVAL_MIN,
                      id="scan", max_instances=1, coalesce=True,
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
