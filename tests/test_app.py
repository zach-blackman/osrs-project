"""End-to-end smoke test for the hosted app, entirely offline.

Covers the verification list in MIGRATION_PLAN.md §8: snapshot into SQLite,
read-time personalisation with zero wiki calls, single-flight refresh, the SSE
progress stream, and durability across a simulated restart.

Run: .venv/bin/python tests/test_app.py
"""

import json
import os
import pathlib
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

_TMP = tempfile.mkdtemp(prefix="osrs-smoke-")
# Must be set before config is imported — it reads the environment once.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "smoke.db")
os.environ["SCAN_ON_STARTUP"] = "0"
os.environ["CLAN_PASSWORD"] = ""
os.environ["SLEEP"] = "0"
# Legacy shortlist path for this smoke test; Phase-2 coverage lives in test_phase2.py.
os.environ["FAST_SCAN"] = "0"
os.environ["ROLE"] = "all"

import fake_wiki  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as web  # noqa: E402
import db  # noqa: E402
import scanner  # noqa: E402
import worker  # noqa: E402

failures = []
CALLS = {"n": 0}


def check(cond, label):
    print(("  ok  " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


def install_counting_fake():
    """The fake wiki, wrapped so every API call is counted."""
    world = fake_wiki.build_world()
    fake_wiki.install(scanner, world)
    inner = scanner.get

    def counting(path, retries=3, **params):
        CALLS["n"] += 1
        return inner(path, retries, **params)

    scanner.get = counting


def wait_until(pred, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def main():
    real_time = time.time
    install_counting_fake()
    worker.MANUAL_DEBOUNCE_SEC = 0        # the debounce has its own check below

    try:
        with TestClient(web.app) as client:
            print("snapshot: writer stores a scan in SQLite")
            started = worker.runner.trigger(source="test")
            check(started["started"], "trigger started a scan")
            check(wait_until(lambda: not worker.runner.status()["scanning"]),
                  "scan finished")
            scan = db.latest_ok_scan()
            check(scan is not None and scan["status"] == "ok",
                  "scans row written with status ok")
            check(scan and scan["n_items"] > 0,
                  f"picks stored ({scan['n_items'] if scan else 0} items)")
            rows = db.read_picks(scan["id"])
            check(len(rows) == scan["n_items"], "read_picks returns every row")
            check(all(isinstance(r.get("spark"), list) for r in rows),
                  "spark round-trips as a list")
            check(set(rows[0]) == set(scanner.MARKET_KEYS),
                  "stored keys match scanner.MARKET_KEYS exactly")
            first_scan_calls = CALLS["n"]
            check(first_scan_calls > 3, f"scan cost {first_scan_calls} wiki calls")

            print("\nread-time personalisation, no re-scan")
            before = CALLS["n"]
            seen = {}
            for capital, floor in (("50m", "100k"), ("700m", "500k"),
                                   ("5b", "2m")):
                r = client.get(f"/api/picks?capital={capital}&floor={floor}")
                check(r.status_code == 200, f"GET /api/picks {capital}/{floor}")
                d = r.json()
                seen[(capital, floor)] = d
                lo = min((p["buy_price"] for p in d["rows"]), default=None)
                check(lo is None or lo >= scanner.parse_gp(floor),
                      f"floor {floor} respected")
                scores = [p["merch_score"] for p in d["rows"]]
                check(scores == sorted(scores, reverse=True),
                      f"{capital}/{floor} sorted by merch_score")
            check(CALLS["n"] == before, "reads made ZERO wiki calls")
            small = seen[("50m", "100k")]["rows"]
            big = seen[("5b", "2m")]["rows"]
            check(sum(p["units_24h"] for p in small)
                  != sum(p["units_24h"] for p in big),
                  "different capital yields genuinely different picks")
            check(all(d["scan_id"] == scan["id"] for d in seen.values()),
                  "all reads served from the one snapshot")

            print("\nstatus endpoint")
            st = client.get("/api/status").json()
            check(st["scanning"] is False, "status reports not scanning")
            check(st["n_items"] == scan["n_items"], "status echoes snapshot size")
            check(st["age_seconds"] is not None, "status reports snapshot age")

            print("\nsingle-flight: 5 concurrent refreshes -> 1 scan")
            scans_before = len(db.list_scans(100))
            calls_before = CALLS["n"]
            results = []
            lock = threading.Lock()

            def fire():
                r = client.post("/api/refresh").json()
                with lock:
                    results.append(r)

            threads = [threading.Thread(target=fire) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            check(wait_until(lambda: not worker.runner.status()["scanning"]),
                  "the one scan finished")
            starts = sum(1 for r in results if r["started"])
            check(starts == 1, f"exactly one request started a scan ({starts})")
            check(len(db.list_scans(100)) == scans_before + 1,
                  "exactly one new scans row")
            # The fake world is deterministic, so one scan always costs the
            # same number of calls. Five refreshes costing exactly one scan's
            # worth is the real proof that nothing ran twice.
            check(CALLS["n"] - calls_before == first_scan_calls,
                  f"wiki paid for one scan only "
                  f"({CALLS['n'] - calls_before} calls vs {first_scan_calls})")
            attached = [r for r in results if not r["started"]]
            check(all(r["scanning"] for r in attached),
                  "the other four were told a scan is already running")

            print("\ndebounce: a manual refresh right after a scan is refused")
            worker.MANUAL_DEBOUNCE_SEC = 30
            deb = client.post("/api/refresh")
            d = deb.json()
            check(deb.status_code == 429, f"debounced returns 429 (got {deb.status_code})")
            check(not d["started"] and not d["scanning"],
                  f"debounced ({d.get('reason')})")
            worker.MANUAL_DEBOUNCE_SEC = 0

            print("\nSSE: /api/refresh/stream carries the scan")
            events = []
            done = threading.Event()

            def consume():
                try:
                    with client.stream("GET", "/api/refresh/stream") as resp:
                        for line in resp.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            ev = json.loads(line[6:])
                            events.append(ev)
                            if ev["kind"] in ("result", "error", "idle"):
                                break
                finally:
                    done.set()

            worker.runner.trigger(source="test")
            reader = threading.Thread(target=consume, daemon=True)
            reader.start()
            check(done.wait(120), "stream closed after the scan")
            kinds = {e["kind"] for e in events}
            check("phase" in kinds, "stream carried phase events")
            check("log" in kinds, "stream carried log events")
            check("progress" in kinds, "stream carried progress events")
            check("result" in kinds, "stream ended with a result")
            result = next((e for e in events if e["kind"] == "result"), {})
            check(result.get("n_items", 0) > 0,
                  f"result reports {result.get('n_items')} items")

            print("\nauth: shared clan password")
            import config
            config.CLAN_PASSWORD = "hunter2"    # middleware reads this per call
            try:
                bare = TestClient(web.app)
                check(bare.get("/api/picks").status_code == 401,
                      "API refuses an unauthenticated read")
                check(bare.get("/", follow_redirects=False).status_code == 303,
                      "page redirects to the login form")
                check(bare.get("/merch", follow_redirects=False).status_code == 303,
                      "/merch redirects to the login form")
                hz = bare.get("/healthz")
                check(hz.status_code in (200, 503),
                      "/healthz stays reachable without auth")
                # Length-mismatched wrong password must not 500 (compare_digest).
                bad = bare.post("/login", data={"password": "no"},
                                follow_redirects=False)
                check(bad.status_code == 303 and bad.headers.get("location") == "/login?bad=1",
                      "wrong password is rejected without 500")
                good = bare.post("/login", data={"password": "hunter2"},
                                 follow_redirects=False)
                check(web.COOKIE_NAME in good.cookies,
                      "correct password sets a session cookie")
                check(good.headers.get("location") == "/merch",
                      "login lands on /merch")
                check(bare.get("/api/picks").status_code == 200,
                      "signed-in read works")
                check(bare.get("/merch").status_code == 200,
                      "signed-in /merch serves the desk")
            finally:
                config.CLAN_PASSWORD = ""

            print("\nshell routes + static assets")
            root = client.get("/", follow_redirects=False)
            check(root.status_code == 303 and root.headers.get("location") == "/merch",
                  "/ redirects to /merch")
            merch = client.get("/merch")
            check(merch.status_code == 200 and b"Clan Tools" in merch.content,
                  "/merch returns Clan Tools shell")
            check(b"data-tool=\"merch\"" in merch.content, "/merch marks the merch tool")
            css = client.get("/static/css/shell.css")
            check(css.status_code == 200 and b"--accent" in css.content,
                  "/static/css/shell.css is served")
            js = client.get("/static/js/shell.js")
            check(js.status_code == 200 and b"TOOLS" in js.content,
                  "/static/js/shell.js exposes TOOLS registry")
            mjs = client.get("/static/js/merch.js")
            check(mjs.status_code == 200 and b"renderCards" in mjs.content,
                  "merch.js includes mobile card render")

            print("\nhealth")
            hr = client.get("/healthz")
            h = hr.json()
            check(h["ok"] and h["has_snapshot"], "/healthz sees a snapshot")
            check(h.get("ready") is True and hr.status_code == 200,
                  "fresh ok snapshot is ready")
            picks = client.get("/api/picks?capital=700m&floor=100k").json()
            check("analysis_note" in picks, "picks carry analysis caveat")
            if picks.get("rows"):
                row0 = picks["rows"][0]
                check("dip_confidence" in row0 and "risk_level" in row0,
                      "picks rows include analysis fields")

        print("\ndurability: a fresh process still serves the snapshot")
        expected = db.latest_ok_scan()
        db.engine().dispose()
        db._engine = None                  # simulate a restart: new engine
        again = db.latest_ok_scan()
        check(again is not None and again["id"] == expected["id"],
              "latest snapshot survives a reconnect")
        check(len(db.read_picks(again["id"])) == again["n_items"],
              "its picks survive too")

        print("\npruning keeps history bounded")
        kept = db.prune(keep=1)
        check(len(db.list_scans(100)) == 1, f"pruned {kept} old scans, kept 1")
    finally:
        time.time = real_time

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
