"""Identity, prefs, watchlist, admin, and achievements routes for Clan Tools."""

from __future__ import annotations

import json
import secrets
import time
import urllib.parse

import requests
from fastapi import Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import accounts
import config
import discord_oauth
import userdb
import worker

COOKIE_NAME = config.SESSION_COOKIE
LEGACY_COOKIE = "clan_session"

_ACH_TYPES = {"drop", "collection_log", "combat_ach", "clue", "custom"}
_ingest_hits = {}  # token_prefix -> [timestamps]


def auth_open_paths():
    return {
        "/login", "/healthz",
        "/auth/discord", "/auth/discord/callback",
        "/api/achievements/ingest",
    }


def _secret_bytes():
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    raw = config.CLAN_PASSWORD or "dev-open"
    return __import__("hashlib").sha256(
        ("osrs-merch-scanner:" + raw).encode()).digest()


def _legacy_token():
    import hashlib
    import hmac
    return hmac.new(_secret_bytes(), b"clan-member", hashlib.sha256).hexdigest()


def _password_ok(password: str) -> bool:
    import hashlib
    import hmac
    if not config.CLAN_PASSWORD:
        return False
    left = hashlib.sha256(password.encode("utf-8")).digest()
    right = hashlib.sha256(config.CLAN_PASSWORD.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def current_user(request: Request):
    return getattr(request.state, "user", None)


def require_user(request: Request):
    user = current_user(request)
    if user:
        return user
    if not config.auth_required():
        return None
    return None


def _set_session_cookie(resp, session_id: str):
    resp.set_cookie(
        COOKIE_NAME, session_id, max_age=config.SESSION_TTL_SEC,
        httponly=True, samesite="lax", secure=config.SECURE_COOKIES)


def _clear_session_cookie(resp):
    resp.delete_cookie(COOKIE_NAME)
    resp.delete_cookie(LEGACY_COOKIE)


def start_user_session(user_id: int):
    sid = accounts.new_session_id()
    userdb.create_session(user_id, sid, config.SESSION_TTL_SEC)
    userdb.touch_login(user_id)
    return sid


def resolve_request_user(request: Request):
    """Attach request.state.user from session cookie (or legacy clan password)."""
    request.state.user = None
    request.state.legacy_auth = False
    sid = request.cookies.get(COOKIE_NAME, "")
    if sid and config.auth_providers_active():
        row = userdb.get_session_user(sid)
        if row:
            request.state.user = row
            return
    # Legacy shared-password cookie when providers are off / clan password mode.
    if config.CLAN_PASSWORD and not config.auth_providers_active():
        got = request.cookies.get(COOKIE_NAME, "") or request.cookies.get(LEGACY_COOKIE, "")
        import hmac
        if got and hmac.compare_digest(got, _legacy_token()):
            request.state.legacy_auth = True
            return


def needs_login(request: Request) -> bool:
    if not config.auth_required():
        return False
    if current_user(request):
        return False
    if request.state.legacy_auth:
        return False
    return True


def login_html(*, error: str = "", invite_token: str = "") -> str:
    discord_btn = ""
    if config.discord_configured():
        discord_btn = (
            '<a class="btn discord" href="/auth/discord">Continue with Discord</a>'
            '<div class="or">or</div>')
    invite_block = ""
    if config.INVITES_ENABLED or invite_token:
        action = f"/invite/{invite_token}" if invite_token else "/login"
        invite_block = f"""
<form method="post" action="{action}">
  <input type="text" name="username" placeholder="Username" autocomplete="username"
         {"required" if invite_token else ""}>
  <input type="password" name="password" placeholder="Password"
         autocomplete="{"new-password" if invite_token else "current-password"}" required>
  <button type="submit">{"Create account" if invite_token else "Sign in with invite account"}</button>
</form>"""
    legacy = ""
    if config.CLAN_PASSWORD and not config.auth_providers_active():
        legacy = """
<form method="post" action="/login">
  <input type="password" name="password" placeholder="Clan password" autofocus
         autocomplete="current-password">
  <button type="submit">Sign in</button>
</form>"""
    err = f'<div class="err">{error}</div>' if error else ""
    blurb = "Clan members only — Discord or an admin invite."
    if not config.auth_providers_active() and config.CLAN_PASSWORD:
        blurb = "Clan members only. Shared snapshot tools — start with Merch Desk."
    return f"""<!doctype html><html lang="en" data-theme="dark"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Clan Tools — sign in</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0F1412;--panel:#16201C;--ink:#E6EDEA;--muted:#8FA399;--faint:#6B7F74;
      --line:#24332C;--accent:#2FBF71;--surface:#121A16;--scan-fg:#0F1412;
      --sans:"Source Sans 3","Helvetica Neue",sans-serif}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;min-height:100dvh;display:grid;place-items:center;
     background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);
     padding:max(16px,env(safe-area-inset-top)) max(16px,env(safe-area-inset-right))
             max(16px,env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left))}}
.box{{background:var(--panel);border:1px solid var(--line);padding:28px 26px;width:min(380px,92vw)}}
h1{{margin:0 0 4px;font:700 1.45rem/1.2 var(--sans);letter-spacing:-.02em}}
h1 em{{font-style:normal;color:var(--accent)}}
p{{margin:0 0 18px;color:var(--muted);font-size:13.5px}}
input{{width:100%;font:inherit;padding:12px;min-height:44px;border-radius:2px;margin-top:10px;
      background:var(--surface);border:1px solid var(--line);color:var(--ink);outline:none}}
input:focus{{border-color:var(--accent)}}
button,.btn{{display:block;width:100%;margin-top:12px;font:inherit;font-weight:600;cursor:pointer;
       padding:12px;min-height:44px;border:0;border-radius:2px;color:var(--scan-fg);
       background:var(--accent);text-align:center;text-decoration:none}}
button:hover,.btn:hover{{filter:brightness(1.06)}}
.btn.discord{{background:#5865F2;color:#fff}}
.or{{text-align:center;color:var(--faint);font-size:12px;margin:14px 0 4px}}
.err{{color:#E06A6A;font-size:13px;margin-top:10px}}
</style>
<div class="box">
  <h1>Clan <em>Tools</em></h1>
  <p>{blurb}</p>
  {discord_btn}
  {invite_block}
  {legacy}
  {err}
</div>"""


def post_achievements_webhook(text: str) -> str | None:
    url = config.DISCORD_ACHIEVEMENTS_WEBHOOK_URL
    if not url:
        return None
    try:
        r = requests.post(url, json={"content": text[:1900]}, timeout=10)
        if r.ok:
            # webhooks may not return message id without wait=true
            return (r.json() or {}).get("id")
    except Exception:
        return None
    return None


def rate_limit_ingest(prefix: str, limit=30, window=60) -> bool:
    now = time.time()
    hits = _ingest_hits.setdefault(prefix or "x", [])
    hits[:] = [t for t in hits if now - t < window]
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def register(app, *, tool_html, gp_parse):
    """Attach identity routes to the FastAPI app."""

    @app.get("/login", response_class=HTMLResponse)
    def login_page(bad: int = 0, error: str = ""):
        msg = error or ("Wrong password." if bad else "")
        return login_html(error=msg)

    @app.post("/login")
    async def login_submit(request: Request):
        body = (await request.body()).decode("utf-8", "replace")
        form = urllib.parse.parse_qs(body)
        username = (form.get("username") or [""])[0].strip()
        password = (form.get("password") or [""])[0]

        # Invite-account password login
        if username and config.INVITES_ENABLED:
            user = userdb.get_user_by_username(username)
            if (user and user.get("password_hash") and not user.get("disabled_at")
                    and accounts.verify_password(password, user["password_hash"])):
                sid = start_user_session(user["id"])
                resp = RedirectResponse("/merch", status_code=303)
                _set_session_cookie(resp, sid)
                return resp
            return HTMLResponse(login_html(error="Invalid username or password."),
                                status_code=401)

        # Legacy shared clan password
        if config.CLAN_PASSWORD and not config.auth_providers_active():
            if not _password_ok(password):
                return RedirectResponse("/login?bad=1", status_code=303)
            resp = RedirectResponse("/merch", status_code=303)
            resp.set_cookie(
                COOKIE_NAME, _legacy_token(), max_age=config.SESSION_TTL_SEC,
                httponly=True, samesite="lax", secure=config.SECURE_COOKIES)
            return resp

        return HTMLResponse(login_html(error="Sign-in not configured."), status_code=400)

    @app.post("/logout")
    def logout(request: Request):
        sid = request.cookies.get(COOKIE_NAME, "")
        userdb.delete_session(sid)
        resp = RedirectResponse("/login", status_code=303)
        _clear_session_cookie(resp)
        return resp

    @app.get("/auth/discord")
    def auth_discord():
        if not config.discord_configured():
            return RedirectResponse("/login?error=discord_off", status_code=303)
        state = secrets.token_urlsafe(16)
        resp = RedirectResponse(discord_oauth.authorize_url(state), status_code=303)
        resp.set_cookie("oauth_state", state, max_age=600, httponly=True,
                        samesite="lax", secure=config.SECURE_COOKIES)
        return resp

    @app.get("/auth/discord/callback")
    def auth_discord_callback(request: Request, code: str = "", state: str = "",
                              error: str = ""):
        if error or not code:
            return HTMLResponse(login_html(error="Discord login cancelled."),
                                status_code=400)
        if state != request.cookies.get("oauth_state", ""):
            return HTMLResponse(login_html(error="Invalid OAuth state."),
                                status_code=400)
        try:
            tok = discord_oauth.exchange_code(code)
            access = tok["access_token"]
            du = discord_oauth.fetch_user(access)
            if not discord_oauth.user_in_guild(access, config.DISCORD_GUILD_ID):
                return HTMLResponse(
                    login_html(error="You must be in the clan Discord server."),
                    status_code=403)
            if not discord_oauth.user_has_role(
                    access, config.DISCORD_GUILD_ID, config.DISCORD_REQUIRED_ROLE_ID):
                return HTMLResponse(
                    login_html(error="Missing required Discord role."),
                    status_code=403)
            user = userdb.upsert_discord_user(
                du["id"], discord_oauth.display_name(du), du.get("avatar"))
            # Optional: link Discord onto existing invite session
            link_uid = request.cookies.get("link_user_id")
            if link_uid and not user.get("disabled_at"):
                try:
                    userdb.link_discord(int(link_uid), du["id"],
                                        discord_oauth.display_name(du), du.get("avatar"))
                    user = userdb.get_user(int(link_uid))
                except ValueError:
                    pass
            if user.get("disabled_at"):
                return HTMLResponse(login_html(error="Account disabled."),
                                    status_code=403)
            sid = start_user_session(user["id"])
            resp = RedirectResponse("/merch", status_code=303)
            _set_session_cookie(resp, sid)
            resp.delete_cookie("oauth_state")
            resp.delete_cookie("link_user_id")
            return resp
        except Exception as e:  # noqa: BLE001
            return HTMLResponse(login_html(error=f"Discord login failed: {e}"),
                                status_code=502)

    @app.get("/invite/{token}", response_class=HTMLResponse)
    def invite_page(token: str):
        inv = userdb.get_invite_by_hash(accounts.hash_token(token))
        if not inv or inv.get("claimed_by") or inv["expires_at"] < time.time():
            return HTMLResponse(login_html(error="Invite invalid or expired."),
                                status_code=400)
        return login_html(invite_token=token)

    @app.post("/invite/{token}")
    async def invite_claim(token: str, request: Request):
        inv = userdb.get_invite_by_hash(accounts.hash_token(token))
        if not inv or inv.get("claimed_by") or inv["expires_at"] < time.time():
            return HTMLResponse(login_html(error="Invite invalid or expired."),
                                status_code=400)
        body = (await request.body()).decode("utf-8", "replace")
        form = urllib.parse.parse_qs(body)
        username = (form.get("username") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        if len(username) < 2 or len(password) < 8:
            return HTMLResponse(
                login_html(error="Username ≥2 chars, password ≥8.",
                           invite_token=token), status_code=400)
        if userdb.get_user_by_username(username):
            return HTMLResponse(
                login_html(error="Username taken.", invite_token=token),
                status_code=400)
        user = userdb.create_user(
            username=username,
            password_hash=accounts.hash_password(password),
            role="member")
        userdb.claim_invite(accounts.hash_token(token), user["id"])
        sid = start_user_session(user["id"])
        resp = RedirectResponse("/merch", status_code=303)
        _set_session_cookie(resp, sid)
        return resp

    def _me_payload(user):
        prefs = userdb.get_prefs(user["id"]) or {}
        watch = userdb.list_watchlist(user["id"])
        return {
            "user": userdb._user_dict(user),
            "prefs": {
                "capital": prefs.get("capital"),
                "floor": prefs.get("floor"),
                "nature_cost": prefs.get("nature_cost"),
                "theme": prefs.get("theme"),
                "merch_filters": prefs.get("merch_filters"),
            },
            "watchlist": [{"item_id": w["item_id"],
                           "target_buy": w.get("target_buy"),
                           "target_sell": w.get("target_sell"),
                           "alert_enabled": w.get("alert_enabled")} for w in watch],
            "ingest_token_prefix": user.get("ingest_token_prefix"),
            "auth": {
                "discord": config.discord_configured(),
                "invites": config.INVITES_ENABLED,
            },
        }

    @app.get("/api/me")
    def api_me(request: Request):
        user = current_user(request)
        if not user:
            if not config.auth_required():
                return {"user": None, "prefs": {}, "watchlist": [], "auth": {
                    "discord": config.discord_configured(),
                    "invites": config.INVITES_ENABLED,
                    "open": True,
                }}
            return JSONResponse({"error": "not signed in"}, status_code=401)
        return _me_payload(user)

    @app.put("/api/me/prefs")
    async def api_me_prefs(request: Request):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        body = await request.json()
        capital = body.get("capital")
        floor = body.get("floor")
        if isinstance(capital, str):
            capital = gp_parse(capital, config.DEFAULT_CAPITAL)
        if isinstance(floor, str):
            floor = gp_parse(floor, config.DEFAULT_FLOOR)
        nature = body.get("nature_cost")
        if isinstance(nature, str):
            nature = gp_parse(nature, config.DEFAULT_NATURE_COST)
        prefs = userdb.upsert_prefs(
            user["id"],
            capital=capital, floor=floor, nature_cost=nature,
            theme=body.get("theme"), merch_filters=body.get("merch_filters"))
        return {"prefs": prefs}

    @app.put("/api/me/watchlist/{item_id}")
    async def api_watch_put(item_id: int, request: Request):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        userdb.add_watch(user["id"], item_id)
        if any(k in body for k in ("target_buy", "target_sell", "alert_enabled", "enabled")):
            userdb.set_watch_alert(
                user["id"], item_id,
                target_buy=body.get("target_buy"),
                target_sell=body.get("target_sell"),
                enabled=body.get("alert_enabled", body.get("enabled", True)))
        return {"ok": True, "watchlist": userdb.list_watchlist(user["id"])}

    @app.delete("/api/me/watchlist/{item_id}")
    def api_watch_del(item_id: int, request: Request):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        userdb.remove_watch(user["id"], item_id)
        return {"ok": True}

    @app.get("/api/me/alerts/active")
    def api_alerts_active(request: Request):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        fired, scan = userdb.active_watch_alerts(user["id"])
        return {
            "alerts": fired,
            "scan_id": scan["id"] if scan else None,
            "updated_at": (scan.get("finished_at") if scan else None),
        }

    @app.post("/api/me/ingest-token/rotate")
    def api_rotate_ingest(request: Request):
        user = current_user(request)
        if not user:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        plain, th, prefix = accounts.new_ingest_token()
        userdb.rotate_ingest_token(user["id"], th, prefix)
        return {"token": plain, "prefix": prefix,
                "note": "Copy now — it will not be shown again."}

    @app.post("/api/admin/invites")
    def api_admin_invite(request: Request):
        user = current_user(request)
        if not user or user.get("role") != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        if not config.INVITES_ENABLED:
            return JSONResponse({"error": "invites disabled"}, status_code=400)
        token = accounts.new_invite_token()
        userdb.create_invite(user["id"], accounts.hash_token(token))
        return {"token": token, "url": f"/invite/{token}"}

    @app.get("/api/admin/users")
    def api_admin_users(request: Request):
        user = current_user(request)
        if not user or user.get("role") != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        return {"users": userdb.list_users()}

    @app.post("/api/admin/users/{user_id}/disable")
    def api_admin_disable(user_id: int, request: Request):
        user = current_user(request)
        if not user or user.get("role") != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        if user_id == user["id"]:
            return JSONResponse({"error": "cannot disable yourself"}, status_code=400)
        userdb.disable_user(user_id)
        return {"ok": True}

    @app.post("/api/achievements/ingest")
    async def api_ach_ingest(request: Request):
        auth = request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "bearer token required"}, status_code=401)
        token = auth.split(" ", 1)[1].strip()
        user = userdb.get_user_by_ingest_hash(accounts.hash_token(token))
        if not user:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        prefix = user.get("ingest_token_prefix") or "x"
        if not rate_limit_ingest(prefix):
            return JSONResponse({"error": "rate limited"}, status_code=429)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        et = (body.get("event_type") or "").strip()
        title = (body.get("title") or "").strip()
        if et not in _ACH_TYPES or not title:
            return JSONResponse(
                {"error": f"event_type must be one of {sorted(_ACH_TYPES)}; title required"},
                status_code=400)
        now = time.time()
        occurred = body.get("occurred_at")
        try:
            occurred = float(occurred) if occurred is not None else now
        except (TypeError, ValueError):
            occurred = now
        row = {
            "user_id": user["id"],
            "event_type": et,
            "title": title[:256],
            "detail": (body.get("detail") or None),
            "item_id": body.get("item_id"),
            "value_gp": body.get("value_gp"),
            "rsn": body.get("rsn"),
            "occurred_at": occurred,
            "received_at": now,
            "source": "runelite",
            "payload": body.get("payload") or body,
        }
        aid = userdb.insert_achievement(row)
        msg = f"**{user['username']}** — {et}: {title}"
        if body.get("value_gp"):
            msg += f" ({int(body['value_gp']):,} gp)"
        mid = post_achievements_webhook(msg)
        if mid:
            userdb.set_achievement_discord_message(aid, mid)
        return {"id": aid, "ok": True}

    @app.get("/api/achievements")
    def api_achievements(request: Request,
                         user: int = Query(None),
                         type: str = Query(None),
                         min_value: int = Query(None),
                         top: int = Query(50, ge=1, le=200)):
        if needs_login(request) and not current_user(request) and not request.state.legacy_auth:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        rows = userdb.list_achievements(
            user_id=user, event_type=type, min_value=min_value, top=top)
        return {"rows": rows}

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request):
        user = current_user(request)
        if not user or user.get("role") != "admin":
            return RedirectResponse("/login", status_code=303)
        return tool_html("admin")

    @app.get("/achievements", response_class=HTMLResponse)
    def achievements_desk():
        return tool_html("achievements")
