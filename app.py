"""The reader: a FastAPI app that serves personalised picks from stored
snapshots and never once calls the wiki.

Every request here is arithmetic over a snapshot the writer already paid for.
Two users with different capital and floor get different rankings from the
same rows, instantly — which is the whole point of the migration.
"""

import hashlib
import hmac
import json
import os
import queue
import secrets
import time
import urllib.parse

from fastapi import FastAPI, Query, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)

import config
import db
import scanner
import worker

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE_NAME = "clan_session"
OPEN_PATHS = {"/login", "/healthz"}

app = FastAPI(title="OSRS Merch Scanner", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- auth

def _secret():
    """Signing key. Falls back to a key derived from the password so a
    restart does not silently invalidate every cookie."""
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    return hashlib.sha256(
        ("osrs-merch-scanner:" + config.CLAN_PASSWORD).encode()).digest()


def _token():
    return hmac.new(_secret(), b"clan-member", hashlib.sha256).hexdigest()


def _authed(request):
    if not config.CLAN_PASSWORD:
        return True                       # no password set: open (localhost)
    got = request.cookies.get(COOKIE_NAME, "")
    return bool(got) and hmac.compare_digest(got, _token())


@app.middleware("http")
async def require_login(request: Request, call_next):
    if config.CLAN_PASSWORD and request.url.path not in OPEN_PATHS \
            and not _authed(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "not signed in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


LOGIN_PAGE = """<!doctype html><meta charset="utf-8">
<title>Merch Scanner &mdash; sign in</title>
<style>
 body{margin:0;height:100vh;display:grid;place-items:center;background:#0f1115;
      color:#e7ebf2;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",
      Roboto,Arial,sans-serif}
 form{background:#171b24;border:1px solid #262c39;border-radius:14px;
      padding:28px 26px;width:320px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
 h1{margin:0 0 4px;font-size:18px}h1 span{color:#f4c430}
 p{margin:0 0 18px;color:#9aa4b6;font-size:12.5px}
 input{width:100%%;font:inherit;padding:9px 11px;border-radius:8px;
       background:#0f1115;border:1px solid #313947;color:#e7ebf2;outline:none}
 input:focus{border-color:#d9a521}
 button{width:100%%;margin-top:12px;font:inherit;font-weight:600;cursor:pointer;
        padding:10px;border:0;border-radius:9px;color:#241b04;
        background:linear-gradient(180deg,#ffd75a,#f4c430 55%%,#d9a521)}
 .err{color:#ef5b5b;font-size:12.5px;margin-top:10px}
</style>
<form method="post" action="/login">
  <h1>Grand Exchange <span>Merch Scanner</span></h1>
  <p>Clan members only.</p>
  <input type="password" name="password" placeholder="Clan password" autofocus>
  <button type="submit">Sign in</button>
  %s
</form>"""


@app.get("/login", response_class=HTMLResponse)
def login_page(bad: int = 0):
    return LOGIN_PAGE % ('<div class="err">Wrong password.</div>' if bad else "")


@app.post("/login")
async def login_submit(request: Request):
    # Parsed by hand: both fastapi.Form and request.form() require
    # python-multipart, which is a lot of dependency for one urlencoded field.
    body = (await request.body()).decode("utf-8", "replace")
    password = urllib.parse.parse_qs(body).get("password", [""])[0]
    if not config.CLAN_PASSWORD or not secrets.compare_digest(
            password, config.CLAN_PASSWORD):
        return RedirectResponse("/login?bad=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, _token(), max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite="lax")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ------------------------------------------------------------- lifecycle

@app.on_event("startup")
def _startup():
    db.init_db()
    worker.start_scheduler()


# ------------------------------------------------------------------ api

def _gp(value, default):
    """Accept 700m / 1.5b / 500000 from the query string."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(0, scanner.parse_gp(value))
    except (ValueError, KeyError):
        return default


def _snapshot_or_none():
    scan = db.latest_ok_scan()
    if not scan:
        return None, []
    return scan, db.read_picks(scan["id"])


@app.get("/api/picks")
def api_picks(capital: str = Query(None), floor: str = Query(None),
              top: int = Query(50, ge=1, le=200),
              mode: str = Query("any")):
    cap = _gp(capital, config.DEFAULT_CAPITAL)
    flr = _gp(floor, config.DEFAULT_FLOOR)
    scan, rows = _snapshot_or_none()
    if not scan:
        return JSONResponse(
            {"rows": [], "capital": cap, "floor": flr, "updated_at": None,
             "age_seconds": None, "scanning": worker.runner.status()["scanning"],
             "error": "no snapshot yet — the first scan is still running"},
            status_code=503)

    picks = scanner.rank_for(rows, cap, flr, top=top, mode=mode)
    updated = scan.get("finished_at") or scan.get("started_at")
    return {
        "rows": picks,
        "capital": cap,
        "floor": flr,
        "scan_id": scan["id"],
        "updated_at": updated,
        "age_seconds": round(time.time() - updated) if updated else None,
        "n_market_items": len(rows),
    }


@app.get("/api/status")
def api_status():
    scan = db.latest_ok_scan()
    st = worker.runner.status()
    updated = (scan.get("finished_at") or scan.get("started_at")) if scan else None
    return {
        "scanning": st["scanning"],
        "scan_started_at": st["started_at"],
        "updated_at": updated,
        "age_seconds": round(time.time() - updated) if updated else None,
        "n_items": scan["n_items"] if scan else 0,
        "next_run_at": worker.next_run_at(),
        "interval_minutes": config.SCAN_INTERVAL_MIN,
        "last_error": st["last_error"],
        "defaults": {"capital": config.DEFAULT_CAPITAL,
                     "floor": config.DEFAULT_FLOOR},
    }


@app.post("/api/refresh")
def api_refresh():
    """Ask for a fresh scan. Single-flight: if one is already running you are
    told so and should just attach to the stream."""
    return worker.runner.trigger(source="manual")


@app.get("/api/refresh/stream")
def api_refresh_stream():
    """SSE feed of the current scan: phase / log / progress, then result."""
    q = worker.runner.subscribe()

    def gen():
        deadline = time.time() + 15 * 60
        try:
            while time.time() < deadline:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"      # keeps proxies from hanging up
                    continue
                yield "data: " + json.dumps(event) + "\n\n"
                if event.get("kind") in ("result", "error", "idle"):
                    return
        finally:
            worker.runner.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/scans")
def api_scans(limit: int = Query(20, ge=1, le=100)):
    return {"scans": db.list_scans(limit)}


@app.get("/api/item/{item_id}/history")
def api_item_history(item_id: int, limit: int = Query(50, ge=1, le=200)):
    """Scan-over-scan market data for one item — feeds the drill-down's
    recent-history mini chart. Bounded by KEEP_SCANS; see db.item_history."""
    return {"item_id": item_id, "history": db.item_history(item_id, limit)}


@app.get("/healthz")
def healthz():
    scan = db.latest_ok_scan()
    updated = (scan.get("finished_at") or scan.get("started_at")) if scan else None
    return {"ok": True, "has_snapshot": bool(scan),
            "age_seconds": round(time.time() - updated) if updated else None}


# ------------------------------------------------------------- frontend

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


def main():
    import uvicorn
    db.init_db()
    if not config.CLAN_PASSWORD:
        print("! CLAN_PASSWORD is unset — the app is open to anyone who can "
              "reach it. Fine on localhost; set it before exposing the port.")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
