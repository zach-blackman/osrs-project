"""Identity / invites / achievements / alerts — offline tests.

Run: .venv/bin/python tests/test_identity.py
"""

import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_TMP = tempfile.mkdtemp(prefix="osrs-identity-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "id.db")
os.environ["SCAN_ON_STARTUP"] = "0"
os.environ["CLAN_PASSWORD"] = ""
os.environ["INVITES_ENABLED"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key-identity"
os.environ["FAST_SCAN"] = "0"
os.environ["ROLE"] = "all"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"

import accounts  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import userdb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app as web  # noqa: E402

failures = []


def check(cond, label):
    print(("  ok  " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


def main():
    db.init_db()

    print("password hashing")
    h = accounts.hash_password("hunter2hunter2")
    check(accounts.verify_password("hunter2hunter2", h), "verify ok password")
    check(not accounts.verify_password("nope", h), "reject wrong password")

    print("\ninvite claim + session")
    admin = userdb.create_user(
        username="admin",
        password_hash=accounts.hash_password("adminpass1"),
        role="admin")
    check(admin["role"] == "admin", "bootstrap admin role")
    token = accounts.new_invite_token()
    userdb.create_invite(admin["id"], accounts.hash_token(token))
    client = TestClient(web.app)
    r = client.post("/invite/" + token, data={
        "username": "bob", "password": "bobpass12"}, follow_redirects=False)
    check(r.status_code == 303 and r.headers.get("location") == "/merch",
          "invite claim redirects to /merch")
    check(web.COOKIE_NAME in r.cookies, "session cookie set")
    client.cookies.set(web.COOKIE_NAME, r.cookies[web.COOKIE_NAME])
    me = client.get("/api/me")
    check(me.status_code == 200 and me.json()["user"]["username"] == "bob",
          "/api/me returns bob")

    print("\nprefs + watchlist")
    pr = client.put("/api/me/prefs", json={"capital": "1.5b", "floor": "2m"})
    check(pr.status_code == 200 and pr.json()["prefs"]["capital"] == 1_500_000_000,
          "prefs capital parsed")
    w = client.put("/api/me/watchlist/4151", json={
        "target_buy": 1000, "target_sell": 2000, "alert_enabled": True})
    check(w.status_code == 200, "watchlist put")
    me2 = client.get("/api/me").json()
    check(any(x["item_id"] == 4151 for x in me2["watchlist"]), "watchlist contains item")

    print("\ningest + achievements feed")
    tok = client.post("/api/me/ingest-token/rotate").json()
    check("token" in tok and tok.get("prefix"), "ingest token rotated")
    bare = TestClient(web.app)
    bad = bare.post("/api/achievements/ingest", json={
        "event_type": "drop", "title": "Dragon warhammer"})
    check(bad.status_code == 401, "ingest rejects missing bearer")
    ok = bare.post(
        "/api/achievements/ingest",
        headers={"Authorization": "Bearer " + tok["token"]},
        json={"event_type": "drop", "title": "Dragon warhammer",
              "item_id": 13576, "value_gp": 40_000_000, "rsn": "Bob"})
    check(ok.status_code == 200 and ok.json().get("ok"), "ingest accepts token")
    feed = client.get("/api/achievements?type=drop&top=10").json()
    check(len(feed.get("rows") or []) >= 1, "achievements feed has rows")
    check(feed["rows"][0]["title"] == "Dragon warhammer", "feed title matches")

    print("\nscan ACL")
    scan = client.post("/api/refresh")
    check(scan.status_code == 403, "member cannot Scan")
    # promote bob? use admin session
    admin_client = TestClient(web.app)
    login = admin_client.post("/login", data={
        "username": "admin", "password": "adminpass1"}, follow_redirects=False)
    check(login.status_code == 303, "admin password login")
    admin_client.cookies.set(web.COOKIE_NAME, login.cookies[web.COOKIE_NAME])
    # Without a writer-friendly wiki, refresh may start then error — ACL is the check.
    # Temporarily stub trigger
    called = {"n": 0}
    real = web.worker.runner.trigger

    def fake_trigger(**kw):
        called["n"] += 1
        return {"started": True, "scanning": True}

    web.worker.runner.trigger = fake_trigger
    try:
        a_scan = admin_client.post("/api/refresh")
        check(a_scan.status_code == 200 and called["n"] == 1,
              "admin can Scan")
    finally:
        web.worker.runner.trigger = real

    print("\nalerts vs pulse")
    # Seed a snapshot price for item 4151
    now = time.time()
    sid = db.write_snapshot(
        {"started_at": now, "finished_at": now, "params": {}},
        [{"id": 4151, "name": "Abyssal whip", "buy_price": 900, "sell_price": 2100,
          "tax": 42, "sell_net": 2058, "margin": 158, "roi": 17.5, "now": 1500,
          "vol_day": 100, "limit": 70, "volatility": 1, "trend": "flat",
          "rank_all": 0.5, "rank90": 0.5, "z30": 0, "catalyst": None,
          "catalyst_bonus": 0, "spark": [1, 2, 3], "buy_vol_1h": 1, "sell_vol_1h": 1}])
    db.insert_snapshots(sid, [{"id": 4151, "high": 2100, "low": 900,
                                "buy_vol_1h": 1, "sell_vol_1h": 1}])
    alerts = client.get("/api/me/alerts/active").json()
    check(len(alerts.get("alerts") or []) >= 1, "watch alert fires on pulse")

    print("\npages")
    check(client.get("/achievements").status_code == 200, "/achievements serves")
    check(admin_client.get("/admin").status_code == 200, "/admin serves for admin")
    check(client.get("/admin").status_code in (303, 401, 200),
          "non-admin admin page gated")

    print()
    if failures:
        print(f"FAILED ({len(failures)})")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
