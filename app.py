"""The reader: a FastAPI app that serves personalised picks from stored
snapshots and never once calls the wiki.

Every request here is arithmetic over a snapshot the writer already paid for.
Two users with different capital and floor get different rankings from the
same rows, instantly — which is the whole point of the migration.
"""

import hashlib
import hmac
import json
import logging
import os
import queue
import re
import secrets
import time
import urllib.parse

from fastapi import FastAPI, Query, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

import analysis
import config
import db
import scanner
import worker

log = logging.getLogger("osrs.app")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE_NAME = "clan_session"
OPEN_PATHS = {"/login", "/healthz"}

app = FastAPI(title="Clan Tools", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# HTML is no-cache; fingerprint CSS/JS so Cloudflare edge caches cannot keep
# serving a pre-deploy shell.js that still did btn.textContent = "Light".
_ASSET_REF = re.compile(r'((?:href|src)="/static/[^"?]+)(")')
_STATIC_FINGERPRINT_FILES = (
    "css/shell.css", "css/merch.css", "css/alch.css", "css/movers.css",
    "js/shell.js", "js/merch.js", "js/alch.js", "js/movers.js",
    "js/tool-status.js",
)


@app.middleware("http")
async def _static_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        # Short edge TTL so deploys propagate even without a query-string bust.
        response.headers.setdefault(
            "Cache-Control", "public, max-age=120, must-revalidate")
    return response


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


def _password_ok(password: str) -> bool:
    """Constant-time compare that tolerates length mismatch (unlike raw
    secrets.compare_digest on unequal strings, which raises ValueError)."""
    if not config.CLAN_PASSWORD:
        return False
    left = hashlib.sha256(password.encode("utf-8")).digest()
    right = hashlib.sha256(config.CLAN_PASSWORD.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def _authed(request):
    if not config.CLAN_PASSWORD:
        return True                       # no password set: open (localhost)
    got = request.cookies.get(COOKIE_NAME, "")
    return bool(got) and hmac.compare_digest(got, _token())


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if config.CLAN_PASSWORD and path not in OPEN_PATHS and not path.startswith("/static/") \
            and not _authed(request):
        if path.startswith("/api/"):
            return JSONResponse({"error": "not signed in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


LOGIN_PAGE = """<!doctype html><html lang="en" data-theme="dark"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Clan Tools — sign in</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0F1412;--panel:#16201C;--ink:#E6EDEA;--muted:#8FA399;--faint:#6B7F74;
      --line:#24332C;--accent:#2FBF71;--surface:#121A16;--scan-fg:#0F1412;
      --sans:"Source Sans 3","Helvetica Neue",sans-serif}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;min-height:100dvh;display:grid;place-items:center;
     background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);
     padding:max(16px,env(safe-area-inset-top)) max(16px,env(safe-area-inset-right))
             max(16px,env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left))}
form{background:var(--panel);border:1px solid var(--line);padding:28px 26px;width:min(360px,92vw)}
h1{margin:0 0 4px;font:700 1.45rem/1.2 var(--sans);letter-spacing:-.02em}
h1 em{font-style:normal;color:var(--accent)}
p{margin:0 0 18px;color:var(--muted);font-size:13.5px}
input{width:100%;font:inherit;padding:12px;min-height:44px;border-radius:2px;
      background:var(--surface);border:1px solid var(--line);color:var(--ink);outline:none}
input:focus{border-color:var(--accent)}
button{width:100%;margin-top:12px;font:inherit;font-weight:600;cursor:pointer;
       padding:12px;min-height:44px;border:0;border-radius:2px;color:var(--scan-fg);background:var(--accent)}
button:hover{filter:brightness(1.06)}
.err{color:#E06A6A;font-size:13px;margin-top:10px}
</style>
<form method="post" action="/login">
  <h1>Clan <em>Tools</em></h1>
  <p>Clan members only. Shared snapshot tools — start with Merch Desk.</p>
  <input type="password" name="password" placeholder="Clan password" autofocus autocomplete="current-password">
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
    if not _password_ok(password):
        return RedirectResponse("/login?bad=1", status_code=303)
    resp = RedirectResponse("/merch", status_code=303)
    resp.set_cookie(
        COOKIE_NAME, _token(), max_age=60 * 60 * 24 * 30,
        httponly=True, samesite="lax", secure=config.SECURE_COOKIES)
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ------------------------------------------------------------- lifecycle

@app.on_event("startup")
def _startup():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.assert_deploy_safe()
    db.init_db()
    if config.ROLE in ("all", "writer"):
        worker.start_scheduler()
        log.info("scheduler started (ROLE=%s)", config.ROLE)
    else:
        log.info("ROLE=api — scheduler not started in this process")


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
    picks = analysis.analyze(picks)
    updated = scan.get("finished_at") or scan.get("started_at")
    return {
        "rows": picks,
        "capital": cap,
        "floor": flr,
        "scan_id": scan["id"],
        "updated_at": updated,
        "age_seconds": round(time.time() - updated) if updated else None,
        "n_market_items": len(rows),
        "analysis_note": (
            "Heuristic dip/flip/risk scores from EMA/RSI on the 30-day spark "
            "and last-hour volume — not backtested. Treat as a hint."),
    }


@app.get("/api/status")
def api_status():
    scan = db.latest_ok_scan()
    st = worker.runner.status()
    updated = (scan.get("finished_at") or scan.get("started_at")) if scan else None
    ready = db.readiness()
    return {
        "scanning": st["scanning"],
        "scan_started_at": st["started_at"],
        "updated_at": updated,
        "age_seconds": round(time.time() - updated) if updated else None,
        "n_items": scan["n_items"] if scan else 0,
        "next_run_at": worker.next_run_at() if config.ROLE in ("all", "writer") else None,
        "interval_minutes": config.SCAN_INTERVAL_MIN,
        "last_error": st["last_error"],
        "ready": ready["ready"],
        "history_ready": ready.get("history_ready"),
        "fast_scan": config.FAST_SCAN,
        "defaults": {"capital": config.DEFAULT_CAPITAL,
                     "floor": config.DEFAULT_FLOOR},
    }


@app.post("/api/refresh")
def api_refresh():
    """Ask for a fresh scan. Single-flight: if one is already running you are
    told so and should just attach to the stream. Debounced manual refreshes
    return 429 so clients can back off."""
    if config.ROLE == "api":
        return JSONResponse(
            {"started": False, "scanning": False,
             "reason": "this process is ROLE=api — refresh the writer"},
            status_code=503)
    result = worker.runner.trigger(source="manual")
    if (not result.get("started") and not result.get("scanning")
            and result.get("retry_after")):
        return JSONResponse(result, status_code=429,
                            headers={"Retry-After": str(result["retry_after"])})
    return result


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


@app.get("/api/alch")
def api_alch(capital: str = Query(None), floor: str = Query(None),
             nature: str = Query(None), top: int = Query(50, ge=1, le=200)):
    """Alch Desk: high-alch profit ranking from stored mapping + latest pulse.

    profit = highalch - buy_price - nature_cost. Buy price is the latest
    insta-sell (low) from item_snapshots when available, else picks.
    Cap/Floor personalise at read time — no wiki calls.
    """
    cap = _gp(capital, config.DEFAULT_CAPITAL)
    flr = _gp(floor, config.DEFAULT_FLOOR)
    nature_cost = _gp(nature, config.DEFAULT_NATURE_COST)

    prices, scan = db.latest_snapshot_prices()
    if not prices:
        # Fallback: merch picks snapshot (legacy / no pulse yet).
        scan, picks = _snapshot_or_none()
        if not scan:
            return JSONResponse(
                {"rows": [], "capital": cap, "floor": flr,
                 "nature_cost": nature_cost, "updated_at": None,
                 "age_seconds": None, "error": "no snapshot yet",
                 "note": (
                     "High-alch profits from wiki mapping highalch minus GE "
                     "insta-sell and nature rune cost. Scaffold — fire-staff "
                     "mode and live nature pricing come later.")},
                status_code=503)
        prices = {
            r["id"]: {"low": r.get("buy_price"), "high": r.get("sell_price"),
                      "buy_vol_1h": r.get("buy_vol_1h"),
                      "sell_vol_1h": r.get("sell_vol_1h")}
            for r in picks if r.get("buy_price")
        }

    meta_by_id = db.items_with_alch(list(prices.keys()) or None)
    rows = []
    for iid, pulse in prices.items():
        meta = meta_by_id.get(iid)
        if not meta:
            continue
        highalch = meta.get("highalch")
        buy = pulse.get("low")
        if highalch is None or not buy or buy < flr:
            continue
        profit = int(highalch) - int(buy) - int(nature_cost)
        if profit <= 0:
            continue
        limit = meta.get("buy_limit") or 0
        units = min(limit * 6, cap // buy) if buy and limit else 0
        rows.append({
            "id": iid,
            "name": meta.get("name") or f"Item {iid}",
            "buy_price": int(buy),
            "highalch": int(highalch),
            "nature_cost": int(nature_cost),
            "profit": profit,
            "roi": round(profit / buy * 100, 2) if buy else 0,
            "limit": limit,
            "gp_24h": profit * units if units else 0,
            "members": meta.get("members"),
        })
    rows.sort(key=lambda r: (r["profit"], r["gp_24h"]), reverse=True)
    rows = rows[:top]
    updated = (scan.get("finished_at") or scan.get("started_at")) if scan else None
    return {
        "rows": rows,
        "capital": cap,
        "floor": flr,
        "nature_cost": nature_cost,
        "scan_id": scan["id"] if scan else None,
        "updated_at": updated,
        "age_seconds": round(time.time() - updated) if updated else None,
        "n_candidates": len(rows),
        "note": (
            "High-alch profits from wiki mapping highalch minus GE insta-sell "
            "and nature rune cost. RuneLite prices, not guide price. Fire-staff "
            "mode and live nature pricing are not modelled yet."),
    }


@app.get("/api/movers")
def api_movers(window: int = Query(6, ge=2, le=36),
               top: int = Query(50, ge=1, le=200)):
    """Movers Desk: % price change and 1h volume spikes across recent pulses.

    Pure DB derivation over item_snapshots — no wiki. `window` is how many
    ok pulsed scans to look back (newest vs oldest in that window).
    """
    scan_rows = db.recent_ok_scan_ids(limit=window)
    if len(scan_rows) < 2:
        return {
            "rows": [],
            "window": window,
            "scans_used": len(scan_rows),
            "updated_at": scan_rows[0]["finished_at"] if scan_rows else None,
            "age_seconds": (
                round(time.time() - scan_rows[0]["finished_at"])
                if scan_rows and scan_rows[0].get("finished_at") else None),
            "note": (
                "Needs at least two ok pulsed scans. Metrics: pct_high / "
                "pct_low vs oldest pulse in the window; vol_spike = current "
                "1h vol / median of prior pulses in the window."),
        }

    newest, oldest = scan_rows[0], scan_rows[-1]
    by_scan = db.snapshots_for_scans([s["id"] for s in scan_rows])
    now_map = by_scan.get(newest["id"]) or {}
    then_map = by_scan.get(oldest["id"]) or {}

    # Per-item volume series (newest-first scan order) for median spike.
    vol_series = {}
    for s in scan_rows:
        for iid, snap in (by_scan.get(s["id"]) or {}).items():
            vol = (snap.get("buy_vol_1h") or 0) + (snap.get("sell_vol_1h") or 0)
            vol_series.setdefault(iid, []).append(vol)

    names = db.item_names(list(now_map.keys()))
    rows = []
    for iid, now in now_map.items():
        then = then_map.get(iid)
        if not then:
            continue
        hi_now, hi_then = now.get("high"), then.get("high")
        lo_now, lo_then = now.get("low"), then.get("low")
        pct_high = None
        pct_low = None
        if hi_now and hi_then:
            pct_high = round((hi_now - hi_then) / hi_then * 100, 2)
        if lo_now and lo_then:
            pct_low = round((lo_now - lo_then) / lo_then * 100, 2)

        vols = vol_series.get(iid) or []
        cur_vol = vols[0] if vols else 0
        prior = vols[1:] if len(vols) > 1 else []
        median_prior = None
        vol_spike = None
        if prior:
            ordered = sorted(prior)
            mid = len(ordered) // 2
            median_prior = (
                ordered[mid] if len(ordered) % 2
                else (ordered[mid - 1] + ordered[mid]) / 2)
            if median_prior and median_prior > 0:
                vol_spike = round(cur_vol / median_prior, 2)

        # Skip quiet no-ops: need a real price move or volume spike.
        move = abs(pct_high or 0) + abs(pct_low or 0)
        if move < 0.5 and (vol_spike is None or vol_spike < 1.5):
            continue

        rows.append({
            "id": iid,
            "name": names.get(iid) or f"Item {iid}",
            "high": hi_now,
            "low": lo_now,
            "pct_high": pct_high,
            "pct_low": pct_low,
            "buy_vol_1h": now.get("buy_vol_1h"),
            "sell_vol_1h": now.get("sell_vol_1h"),
            "vol_1h": cur_vol,
            "vol_median_prior": median_prior,
            "vol_spike": vol_spike,
            "score": round(
                abs(pct_high or 0) * 0.5
                + abs(pct_low or 0) * 0.3
                + (vol_spike or 0) * 5, 2),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[:top]
    updated = newest.get("finished_at")
    return {
        "rows": rows,
        "window": window,
        "scans_used": len(scan_rows),
        "from_scan_id": oldest["id"],
        "to_scan_id": newest["id"],
        "updated_at": updated,
        "age_seconds": round(time.time() - updated) if updated else None,
        "note": (
            "Pulse movers from item_snapshots — RuneLite trades, not guide "
            "price. pct_* vs oldest pulse in window; vol_spike vs median of "
            "prior pulses. Heuristic score, not backtested."),
    }


@app.get("/healthz")
def healthz():
    """Liveness always answers; `ready` is false when the snapshot is missing,
    empty, or older than READY_MAX_AGE_MIN — use ready for load-balancer probes."""
    probe = db.readiness()
    body = {
        "ok": True,
        "ready": probe["ready"],
        "reason": probe.get("reason"),
        "has_snapshot": probe["has_snapshot"],
        "age_seconds": probe.get("age_seconds"),
        "n_items": probe.get("n_items"),
        "history_ready": probe.get("history_ready"),
        "role": config.ROLE,
    }
    return JSONResponse(body, status_code=200 if probe["ready"] else 503)


# ------------------------------------------------------------- frontend

def _static_fingerprint() -> str:
    """mtime fingerprint of shell + tool assets — changes on every deploy touch."""
    parts = []
    for rel in _STATIC_FINGERPRINT_FILES:
        path = os.path.join(STATIC_DIR, rel)
        try:
            parts.append(f"{rel}:{int(os.path.getmtime(path))}")
        except OSError:
            parts.append(f"{rel}:0")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:10]


def _tool_html(name: str) -> HTMLResponse:
    path = os.path.join(STATIC_DIR, "tools", f"{name}.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    v = _static_fingerprint()
    html = _ASSET_REF.sub(rf"\1?v={v}\2", html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    """Home redirects to Merch Desk until more tools are live."""
    return RedirectResponse("/merch", status_code=303)


@app.get("/merch", response_class=HTMLResponse)
def merch_desk():
    return _tool_html("merch")


@app.get("/alch", response_class=HTMLResponse)
def alch_desk():
    return _tool_html("alch")


@app.get("/movers", response_class=HTMLResponse)
def movers_desk():
    return _tool_html("movers")


def main():
    import uvicorn
    config.assert_deploy_safe()
    if config.ROLE == "writer":
        worker.run_writer_forever()
        return
    db.init_db()
    if not config.CLAN_PASSWORD:
        print("! CLAN_PASSWORD is unset — the app is open to anyone who can "
              "reach it. Fine on localhost; set it before exposing the port.")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
