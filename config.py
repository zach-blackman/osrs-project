"""Environment-driven configuration for the hosted clan app.

Everything the writer and reader need is read once, here, so the deployment
surface is a list of env vars rather than edits scattered through the code.
Defaults are tuned for a local single-instance run (SQLite, optional auth).
"""

import os
import sys

import scanner


def _int(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    # Accept the same 700m / 1.5b shorthand the UI takes, so REFERENCE_BANKROLL
    # can be written the way a player would say it.
    return scanner.parse_gp(raw)


def _float(name, default):
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else float(raw)


# Storage. SQLite path by default; set to postgresql+psycopg://... to switch.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "osrs_scanner.db"))

# Writer schedule.
SCAN_INTERVAL_MIN = _int("SCAN_INTERVAL_MIN", 10)
KEEP_SCANS = _int("KEEP_SCANS", 50)          # snapshots retained by the pruner
SCAN_ON_STARTUP = os.environ.get("SCAN_ON_STARTUP", "1") not in ("0", "false")

# Shared-scan parameters. See scanner.ScanConfig for why reference_bankroll is
# the SMALLEST plausible capital, not the largest.
REFERENCE_BANKROLL = _int("REFERENCE_BANKROLL", 50_000_000)
GLOBAL_FLOOR = _int("GLOBAL_FLOOR", 100_000)
SHORTLIST = _int("SHORTLIST", 300)
SLEEP = _float("SLEEP", 0.6)                 # per-item politeness delay

# Phase 2 (see MIGRATION_PLAN_V2.md): score every tradeable item from a
# DB-backed history table instead of re-fetching a year of daily candles per
# item on every scan. Requires a completed backfill (see MIN_HISTORY_READY).
FAST_SCAN = os.environ.get("FAST_SCAN", "1") not in ("0", "false", "")
SNAPSHOT_KEEP_DAYS = _int("SNAPSHOT_KEEP_DAYS", 3)   # raw 10-min pulse retention
MIN_HISTORY_DAYS = _int("MIN_HISTORY_DAYS", 45)       # same bar as derive_trend_metrics
# Refuse FAST_SCAN until at least this many items have MIN_HISTORY_DAYS stored.
MIN_HISTORY_READY = _int("MIN_HISTORY_READY", 50)
# A finished scan with fewer scored items is stored as status=degraded, not ok.
MIN_SCORED_ITEMS = _int("MIN_SCORED_ITEMS", 1)
# Readiness: /healthz fails when the latest ok snapshot is older than this.
READY_MAX_AGE_MIN = _int("READY_MAX_AGE_MIN", 30)

# Process role: "all" = API + writer (default), "api" = reader only,
# "writer" = scheduler only (no HTTP, or HTTP without scheduler when co-hosted).
ROLE = os.environ.get("ROLE", "all").strip().lower() or "all"

# Defaults the UI starts with; each user overrides them at read time.
DEFAULT_CAPITAL = _int("DEFAULT_CAPITAL", 700_000_000)
DEFAULT_FLOOR = _int("DEFAULT_FLOOR", 500_000)
# Alch Desk: nature-rune cost when the client does not pass nature=.
DEFAULT_NATURE_COST = _int("DEFAULT_NATURE_COST", 100)

# Auth. Unset Discord + invites means the app is open — fine on localhost only.
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

# Discord OAuth as identity provider (like Google Sign-In). All three required.
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.environ.get(
    "DISCORD_REDIRECT_URI", "").strip()  # e.g. https://host/auth/discord/callback
DISCORD_ACHIEVEMENTS_WEBHOOK_URL = os.environ.get(
    "DISCORD_ACHIEVEMENTS_WEBHOOK_URL", "").strip()

# Invite accounts for users without Discord (admin-created).
INVITES_ENABLED = os.environ.get("INVITES_ENABLED", "0") not in ("0", "false", "")

# Bootstrap first admin (optional).
BOOTSTRAP_ADMIN_DISCORD_ID = os.environ.get("BOOTSTRAP_ADMIN_DISCORD_ID", "").strip()
BOOTSTRAP_ADMIN_USERNAME = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()

SESSION_COOKIE = "clan_session"
SESSION_TTL_SEC = 60 * 60 * 24 * 30

PORT = int(os.environ.get("PORT", "8777"))
HOST = os.environ.get("HOST", "127.0.0.1")

# Secure cookies: on by default when bound off-loopback; override with 0/1.
_LOOPBACK = HOST in ("127.0.0.1", "localhost", "::1")
_secure_env = os.environ.get("SECURE_COOKIES", "").strip().lower()
if _secure_env in ("1", "true", "yes"):
    SECURE_COOKIES = True
elif _secure_env in ("0", "false", "no"):
    SECURE_COOKIES = False
else:
    SECURE_COOKIES = not _LOOPBACK

# The wiki blocks the default requests UA; this value is user-customised and
# preserved from the original tool. Overriding it is possible but rarely right.
UA = os.environ.get("UA", scanner.UA).strip() or scanner.UA
if UA != scanner.UA:
    scanner.UA = UA
    scanner.SESSION.headers.update({"User-Agent": UA})


def discord_configured():
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)


def auth_providers_active():
    """True when Discord and/or invites replace anonymous open access."""
    return discord_configured() or INVITES_ENABLED


def auth_required():
    """Middleware: require a signed-in user when any auth provider is configured."""
    return auth_providers_active()


def is_loopback():
    return _LOOPBACK


def assert_deploy_safe():
    """Refuse to start an open app on a non-loopback bind."""
    if not is_loopback() and not auth_required():
        print("FATAL: set Discord OAuth (CLIENT_ID/SECRET/REDIRECT_URI) or "
              f"INVITES_ENABLED=1 when HOST is not loopback (HOST={HOST!r}). "
              "Refusing to start.",
              file=sys.stderr)
        raise SystemExit(2)
    if ROLE not in ("all", "api", "writer"):
        print(f"FATAL: ROLE must be all|api|writer (got {ROLE!r}).",
              file=sys.stderr)
        raise SystemExit(2)
    if auth_providers_active() and not SECRET_KEY:
        print("WARNING: SECRET_KEY unset — set it so sessions survive restarts.",
              file=sys.stderr)


def scan_config():
    """The ScanConfig the writer uses for every scheduled and manual scan."""
    return scanner.ScanConfig(
        reference_bankroll=REFERENCE_BANKROLL,
        global_floor=GLOBAL_FLOOR,
        shortlist=SHORTLIST,
        sleep=SLEEP,
    )