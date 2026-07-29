"""Clan identity tables and helpers — users, sessions, invites, prefs, watchlist,
achievements. Shares db.metadata so init_db() creates these tables too.
"""

from __future__ import annotations

import json
import time

import sqlalchemy as sa

import config
import db

metadata = db.metadata

users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("discord_id", sa.String(32), unique=True),
    sa.Column("username", sa.String(128), nullable=False),
    sa.Column("avatar_hash", sa.String(64)),
    sa.Column("password_hash", sa.Text),
    sa.Column("role", sa.String(16), nullable=False, server_default="user"),
    sa.Column("ingest_token_hash", sa.String(64)),
    sa.Column("ingest_token_prefix", sa.String(16)),
    sa.Column("disabled_at", sa.Float),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("last_login_at", sa.Float),
)

invites = sa.Table(
    "invites", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("claimed_by", sa.Integer, sa.ForeignKey("users.id")),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
)

sessions = sa.Table(
    "sessions", metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.Integer,
              sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Index("ix_sessions_user", "user_id"),
)

user_prefs = sa.Table(
    "user_prefs", metadata,
    sa.Column("user_id", sa.Integer,
              sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("capital", sa.Integer),
    sa.Column("floor", sa.Integer),
    sa.Column("nature_cost", sa.Integer),
    sa.Column("theme", sa.String(16)),
    sa.Column("merch_filters", sa.JSON),
    sa.Column("updated_at", sa.Float, nullable=False),
)

watchlist = sa.Table(
    "watchlist", metadata,
    sa.Column("user_id", sa.Integer,
              sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("item_id", sa.Integer, primary_key=True),
    sa.Column("created_at", sa.Float, nullable=False),
)

watch_alerts = sa.Table(
    "watch_alerts", metadata,
    sa.Column("user_id", sa.Integer,
              sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("item_id", sa.Integer, primary_key=True),
    sa.Column("target_buy", sa.Integer),
    sa.Column("target_sell", sa.Integer),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
    sa.Column("last_triggered_at", sa.Float),
)

achievements = sa.Table(
    "achievements", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer,
              sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("event_type", sa.String(32), nullable=False),
    sa.Column("title", sa.String(256), nullable=False),
    sa.Column("detail", sa.Text),
    sa.Column("item_id", sa.Integer),
    sa.Column("value_gp", sa.Integer),
    sa.Column("rsn", sa.String(64)),
    sa.Column("occurred_at", sa.Float, nullable=False),
    sa.Column("received_at", sa.Float, nullable=False),
    sa.Column("source", sa.String(32), nullable=False, server_default="runelite"),
    sa.Column("payload", sa.JSON),
    sa.Column("discord_message_id", sa.String(64)),
    sa.Index("ix_achievements_user_time", "user_id", "occurred_at"),
)


def _engine():
    return db.engine()


def _user_dict(row):
    if not row:
        return None
    d = dict(row)
    d.pop("password_hash", None)
    d.pop("ingest_token_hash", None)
    d.pop("expires_at", None)
    return d


def get_user(user_id):
    with _engine().connect() as cx:
        row = cx.execute(sa.select(users).where(users.c.id == user_id)).mappings().first()
    return dict(row) if row else None


def get_user_public(user_id):
    return _user_dict(get_user(user_id))


def get_user_by_discord(discord_id):
    with _engine().connect() as cx:
        row = cx.execute(sa.select(users).where(
            users.c.discord_id == str(discord_id))).mappings().first()
    return dict(row) if row else None


def get_user_by_username(username):
    with _engine().connect() as cx:
        row = cx.execute(sa.select(users).where(
            users.c.username == username)).mappings().first()
    return dict(row) if row else None


def get_user_by_ingest_hash(token_hash):
    with _engine().connect() as cx:
        row = cx.execute(sa.select(users).where(
            users.c.ingest_token_hash == token_hash,
            users.c.disabled_at.is_(None))).mappings().first()
    return dict(row) if row else None


def count_users():
    with _engine().connect() as cx:
        return cx.execute(sa.select(sa.func.count()).select_from(users)).scalar() or 0


def migrate_member_roles_to_user(eng=None):
    """One-shot: rename legacy role 'member' → 'user'."""
    eng = eng or _engine()
    insp = sa.inspect(eng)
    if "users" not in insp.get_table_names():
        return
    with eng.begin() as cx:
        cx.execute(sa.text("UPDATE users SET role='user' WHERE role='member'"))


def list_users():
    with _engine().connect() as cx:
        return [_user_dict(r) for r in cx.execute(
            sa.select(users).order_by(users.c.id)).mappings()]


def create_user(*, username, role="user", discord_id=None, avatar_hash=None,
                password_hash=None):
    now = time.time()
    if role == "user" and count_users() == 0:
        role = "admin"
    if (config.BOOTSTRAP_ADMIN_DISCORD_ID and discord_id
            and str(discord_id) == str(config.BOOTSTRAP_ADMIN_DISCORD_ID)):
        role = "admin"
    if config.BOOTSTRAP_ADMIN_USERNAME and username == config.BOOTSTRAP_ADMIN_USERNAME:
        role = "admin"
    with _engine().begin() as cx:
        uid = cx.execute(sa.insert(users).values(
            discord_id=str(discord_id) if discord_id else None,
            username=username,
            avatar_hash=avatar_hash,
            password_hash=password_hash,
            role=role,
            created_at=now,
            last_login_at=now,
        )).inserted_primary_key[0]
        cx.execute(sa.insert(user_prefs).values(
            user_id=uid,
            capital=config.DEFAULT_CAPITAL,
            floor=config.DEFAULT_FLOOR,
            nature_cost=config.DEFAULT_NATURE_COST,
            theme="dark",
            merch_filters=None,
            updated_at=now,
        ))
    return get_user(uid)


def upsert_discord_user(discord_id, username, avatar_hash=None):
    existing = get_user_by_discord(discord_id)
    now = time.time()
    if existing:
        with _engine().begin() as cx:
            cx.execute(sa.update(users).where(users.c.id == existing["id"]).values(
                username=username, avatar_hash=avatar_hash, last_login_at=now))
        return get_user(existing["id"])
    return create_user(username=username, discord_id=discord_id, avatar_hash=avatar_hash)


def link_discord(user_id, discord_id, username=None, avatar_hash=None):
    other = get_user_by_discord(discord_id)
    if other and other["id"] != user_id:
        raise ValueError("discord already linked")
    values = {"discord_id": str(discord_id)}
    if username:
        values["username"] = username
    if avatar_hash is not None:
        values["avatar_hash"] = avatar_hash
    with _engine().begin() as cx:
        cx.execute(sa.update(users).where(users.c.id == user_id).values(**values))
    return get_user(user_id)


def set_password(user_id, password_hash):
    with _engine().begin() as cx:
        cx.execute(sa.update(users).where(users.c.id == user_id).values(
            password_hash=password_hash))


def touch_login(user_id):
    with _engine().begin() as cx:
        cx.execute(sa.update(users).where(users.c.id == user_id).values(
            last_login_at=time.time()))


def disable_user(user_id):
    with _engine().begin() as cx:
        cx.execute(sa.update(users).where(users.c.id == user_id).values(
            disabled_at=time.time()))
        cx.execute(sa.delete(sessions).where(sessions.c.user_id == user_id))


def create_session(user_id, session_id, ttl_seconds=60 * 60 * 24 * 30):
    with _engine().begin() as cx:
        cx.execute(sa.insert(sessions).values(
            id=session_id, user_id=user_id,
            expires_at=time.time() + ttl_seconds))


def get_session_user(session_id):
    if not session_id:
        return None
    now = time.time()
    with _engine().connect() as cx:
        row = cx.execute(
            sa.select(users, sessions.c.expires_at)
            .select_from(sessions.join(users, sessions.c.user_id == users.c.id))
            .where(sessions.c.id == session_id)
        ).mappings().first()
    if not row:
        return None
    if row["expires_at"] < now or row.get("disabled_at"):
        delete_session(session_id)
        return None
    return dict(row)


def delete_session(session_id):
    if not session_id:
        return
    with _engine().begin() as cx:
        cx.execute(sa.delete(sessions).where(sessions.c.id == session_id))


def create_invite(created_by, token_hash, ttl_seconds=60 * 60 * 24 * 7):
    now = time.time()
    with _engine().begin() as cx:
        return cx.execute(sa.insert(invites).values(
            token_hash=token_hash, created_by=created_by,
            expires_at=now + ttl_seconds, created_at=now,
        )).inserted_primary_key[0]


def get_invite_by_hash(token_hash):
    with _engine().connect() as cx:
        row = cx.execute(sa.select(invites).where(
            invites.c.token_hash == token_hash)).mappings().first()
    return dict(row) if row else None


def claim_invite(token_hash, user_id):
    with _engine().begin() as cx:
        cx.execute(sa.update(invites).where(
            invites.c.token_hash == token_hash,
            invites.c.claimed_by.is_(None),
        ).values(claimed_by=user_id))


def get_prefs(user_id):
    with _engine().connect() as cx:
        row = cx.execute(sa.select(user_prefs).where(
            user_prefs.c.user_id == user_id)).mappings().first()
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("merch_filters"), str):
        d["merch_filters"] = json.loads(d["merch_filters"])
    return d


def upsert_prefs(user_id, *, capital=None, floor=None, nature_cost=None,
                 theme=None, merch_filters=None):
    now = time.time()
    existing = get_prefs(user_id)
    values = {"updated_at": now}
    if capital is not None:
        values["capital"] = capital
    if floor is not None:
        values["floor"] = floor
    if nature_cost is not None:
        values["nature_cost"] = nature_cost
    if theme is not None:
        values["theme"] = theme
    if merch_filters is not None:
        values["merch_filters"] = merch_filters
    with _engine().begin() as cx:
        if existing:
            cx.execute(sa.update(user_prefs).where(
                user_prefs.c.user_id == user_id).values(**values))
        else:
            values.update({
                "user_id": user_id,
                "capital": capital if capital is not None else config.DEFAULT_CAPITAL,
                "floor": floor if floor is not None else config.DEFAULT_FLOOR,
                "nature_cost": (nature_cost if nature_cost is not None
                                else config.DEFAULT_NATURE_COST),
                "theme": theme or "dark",
                "merch_filters": merch_filters,
            })
            cx.execute(sa.insert(user_prefs).values(**values))
    return get_prefs(user_id)


def list_watchlist(user_id):
    with _engine().connect() as cx:
        items = [dict(r) for r in cx.execute(sa.select(watchlist).where(
            watchlist.c.user_id == user_id)).mappings()]
        alerts = {r["item_id"]: dict(r) for r in cx.execute(
            sa.select(watch_alerts).where(watch_alerts.c.user_id == user_id)
        ).mappings()}
    for it in items:
        a = alerts.get(it["item_id"]) or {}
        it["target_buy"] = a.get("target_buy")
        it["target_sell"] = a.get("target_sell")
        it["alert_enabled"] = bool(a.get("enabled")) if a else False
    return items


def add_watch(user_id, item_id):
    now = time.time()
    with _engine().begin() as cx:
        existing = cx.execute(sa.select(watchlist).where(
            watchlist.c.user_id == user_id,
            watchlist.c.item_id == item_id)).first()
        if not existing:
            cx.execute(sa.insert(watchlist).values(
                user_id=user_id, item_id=item_id, created_at=now))


def remove_watch(user_id, item_id):
    with _engine().begin() as cx:
        cx.execute(sa.delete(watchlist).where(
            watchlist.c.user_id == user_id, watchlist.c.item_id == item_id))
        cx.execute(sa.delete(watch_alerts).where(
            watch_alerts.c.user_id == user_id, watch_alerts.c.item_id == item_id))


def set_watch_alert(user_id, item_id, target_buy=None, target_sell=None, enabled=True):
    add_watch(user_id, item_id)
    with _engine().begin() as cx:
        existing = cx.execute(sa.select(watch_alerts).where(
            watch_alerts.c.user_id == user_id,
            watch_alerts.c.item_id == item_id)).first()
        values = {
            "target_buy": target_buy,
            "target_sell": target_sell,
            "enabled": bool(enabled),
        }
        if existing:
            cx.execute(sa.update(watch_alerts).where(
                watch_alerts.c.user_id == user_id,
                watch_alerts.c.item_id == item_id).values(**values))
        else:
            values.update({"user_id": user_id, "item_id": item_id})
            cx.execute(sa.insert(watch_alerts).values(**values))


def rotate_ingest_token(user_id, token_hash, prefix):
    with _engine().begin() as cx:
        cx.execute(sa.update(users).where(users.c.id == user_id).values(
            ingest_token_hash=token_hash, ingest_token_prefix=prefix))


def insert_achievement(row):
    with _engine().begin() as cx:
        return cx.execute(sa.insert(achievements).values(**row)).inserted_primary_key[0]


def set_achievement_discord_message(achievement_id, message_id):
    with _engine().begin() as cx:
        cx.execute(sa.update(achievements).where(
            achievements.c.id == achievement_id).values(
            discord_message_id=str(message_id)))


def list_achievements(*, user_id=None, event_type=None, min_value=None, top=50):
    top = max(1, min(int(top), 200))
    stmt = (sa.select(achievements, users.c.username)
            .select_from(achievements.join(users, achievements.c.user_id == users.c.id))
            .order_by(achievements.c.occurred_at.desc())
            .limit(top))
    if user_id is not None:
        stmt = stmt.where(achievements.c.user_id == user_id)
    if event_type:
        stmt = stmt.where(achievements.c.event_type == event_type)
    if min_value is not None:
        stmt = stmt.where(achievements.c.value_gp >= int(min_value))
    with _engine().connect() as cx:
        rows = []
        for r in cx.execute(stmt).mappings():
            d = dict(r)
            if isinstance(d.get("payload"), str):
                d["payload"] = json.loads(d["payload"])
            rows.append(d)
        return rows


def active_watch_alerts(user_id):
    prices, scan = db.latest_snapshot_prices()
    if not prices:
        scan = db.latest_ok_scan()
        if scan:
            picks = db.read_picks(scan["id"])
            prices = {
                r["id"]: {"low": r.get("buy_price"), "high": r.get("sell_price")}
                for r in picks if r.get("buy_price")
            }
    watched = list_watchlist(user_id)
    names = db.item_names([w["item_id"] for w in watched])
    fired = []
    for w in watched:
        if not w.get("alert_enabled"):
            continue
        pulse = prices.get(w["item_id"])
        if not pulse:
            continue
        low, high = pulse.get("low"), pulse.get("high")
        reasons = []
        if w.get("target_buy") is not None and low is not None and low <= w["target_buy"]:
            reasons.append("buy")
        if (w.get("target_sell") is not None and high is not None
                and high >= w["target_sell"]):
            reasons.append("sell")
        if not reasons:
            continue
        fired.append({
            "item_id": w["item_id"],
            "name": names.get(w["item_id"]) or f"Item {w['item_id']}",
            "low": low, "high": high,
            "target_buy": w.get("target_buy"),
            "target_sell": w.get("target_sell"),
            "reasons": reasons,
        })
    return fired, scan
