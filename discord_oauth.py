"""Discord OAuth helpers (stdlib + requests).

Discord is used as an identity provider only (identify scope) — not guild-gated.
"""

from __future__ import annotations

import urllib.parse

import requests

import config

API = "https://discord.com/api/v10"
TOKEN_URL = f"{API}/oauth2/token"
USER_URL = f"{API}/users/@me"


def authorize_url(state: str) -> str:
    q = urllib.parse.urlencode({
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": config.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
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


def display_name(user: dict) -> str:
    return user.get("global_name") or user.get("username") or f"user-{user.get('id')}"
