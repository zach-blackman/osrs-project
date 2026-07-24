"""Environment-driven configuration for the hosted clan app.

Everything the writer and reader need is read once, here, so the deployment
surface is a list of env vars rather than edits scattered through the code.
Defaults are tuned for a local single-instance run (SQLite, no auth).
"""

import os

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
SHORTLIST = _int("SHORTLIST", 160)
SLEEP = _float("SLEEP", 0.6)                 # per-item politeness delay

# Defaults the UI starts with; each user overrides them at read time.
DEFAULT_CAPITAL = _int("DEFAULT_CAPITAL", 700_000_000)
DEFAULT_FLOOR = _int("DEFAULT_FLOOR", 500_000)

# Auth. Unset means the app is open — fine on localhost, not on the internet.
CLAN_PASSWORD = os.environ.get("CLAN_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

PORT = int(os.environ.get("PORT", "8777"))
HOST = os.environ.get("HOST", "127.0.0.1")

# The wiki blocks the default requests UA; this value is user-customised and
# preserved from the original tool. Overriding it is possible but rarely right.
UA = os.environ.get("UA", scanner.UA).strip() or scanner.UA
if UA != scanner.UA:
    scanner.UA = UA
    scanner.SESSION.headers.update({"User-Agent": UA})


def scan_config():
    """The ScanConfig the writer uses for every scheduled and manual scan."""
    return scanner.ScanConfig(
        reference_bankroll=REFERENCE_BANKROLL,
        global_floor=GLOBAL_FLOOR,
        shortlist=SHORTLIST,
        sleep=SLEEP,
    )
