"""Discord OAuth helpers (stdlib + requests)."""

from __future__ import annotations

import urllib.parse

import requests

import config

API = "https://discord.com/api/v10"
TOKEN_URL = f"{API}/oauth2/token"
USER_URL = f"{API}/users/@me"
GUILDS_URL = f"{API}/users/@me/guilds"
GUILD_MEMBER_URL = f"{API}/users/@me/guilds/{{guild_id}}/member"


def authorize_url(state: str) -> str:
    q = urllib.parse.urlencode({
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": config.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    })
    return f"https://discord.com/api/oauth2/authorize?{q}"


def exchange_code(code: str) -> dict:
    r = requests.post(TOKEN_URL, data={
        "client_id": config.DISCORD_CLIENT_ID,
        "client_secret": config.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.DISCORD_REDIRECT_URI,
    }, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_user(access_token: str) -> dict:
    r = requests.get(USER_URL, headers={"Authorization": f"Bearer {access_token}"},
                     timeout=20)
    r.raise_for_status()
    return r.json()


def user_in_guild(access_token: str, guild_id: str) -> bool:
    r = requests.get(GUILDS_URL, headers={"Authorization": f"Bearer {access_token}"},
                     timeout=20)
    r.raise_for_status()
    return any(str(g.get("id")) == str(guild_id) for g in r.json())


def user_has_role(access_token: str, guild_id: str, role_id: str) -> bool:
    if not role_id:
        return True
    url = GUILD_MEMBER_URL.format(guild_id=guild_id)
    r = requests.get(url, headers={"Authorization": f"Bearer {access_token}"},
                     timeout=20)
    if r.status_code == 403:
        # Missing guilds.members.read — fall back to guild membership only.
        return True
    r.raise_for_status()
    roles = r.json().get("roles") or []
    return str(role_id) in [str(x) for x in roles]


def display_name(user: dict) -> str:
    return user.get("global_name") or user.get("username") or f"user-{user.get('id')}"
