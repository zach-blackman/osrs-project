#!/usr/bin/env python3
"""
scanner.py — the OSRS Grand Exchange merch-scanning core, extracted from
osrs_merch_scan.py so it can be shared by the CLI/GUI tool and the hosted
clan app.

The important split lives at the bottom of this file:

    scan_market(cfg, on_event=None) -> (meta, market_rows)
        Capital-AGNOSTIC. Does all the expensive work: ~3 bulk API calls
        plus one /timeseries call per shortlisted item. This is the ONLY
        code that talks to the wiki, and it should run on a schedule from
        exactly one writer process.

    rank_for(market_rows, capital, floor, top=50) -> list[pick]
        Pure arithmetic over an already-fetched snapshot. Applies one
        user's capital + price floor, then scores and ranks. Cheap enough
        to run on every HTTP request. Makes ZERO network calls.

Together they are equivalent to the original one-shot `run_scan`: for the
same (bankroll, floor), scan_market + rank_for reproduces run_scan's output
exactly. See test_parity.py.

Docs: https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices
"""

import math
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

# The wiki blocks the default python-requests agent. This value is
# user-customised and deliberately preserved across the migration — every
# request this project makes must carry it.
UA = "merch-scanner - nekrosisx"
BASE = "https://prices.runescape.wiki/api/v1/osrs"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


# ---------------------------------------------------------------- api

def get(path, retries=3, **params):
    for attempt in range(retries):
        try:
            r = SESSION.get(f"{BASE}{path}", params=params, timeout=30)
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"gave up on {path}")


# ------------------------------------------------------------- stats

def rank_within(values, v):
    """Fraction of `values` at or below `v`. 0.0 = lowest ever seen."""
    if not values:
        return 0.5
    return sum(1 for x in values if x <= v) / len(values)


def slope_pct_per_day(prices):
    """Least-squares slope of log price, expressed as % change per day."""
    pts = [p for p in prices if p and p > 0]
    n = len(pts)
    if n < 5:
        return 0.0
    xs = range(n)
    ys = [math.log(p) for p in pts]
    mx = (n - 1) / 2
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return (math.exp(num / den) - 1) * 100


def volatility_pct(prices):
    """Stdev of daily log returns, in percent. This is the merch fuel."""
    pts = [p for p in prices if p and p > 0]
    if len(pts) < 10:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(pts, pts[1:])]
    try:
        return statistics.stdev(rets) * 100
    except statistics.StatisticsError:
        return 0.0


def net_margin(high, low):
    """Spread after the 2% GE sale tax, which caps at 5m per item."""
    return high - low - min(high * 0.02, 5_000_000)


GE_WINDOWS_24H = 6  # the 4-hour GE buy limit resets six times per day


def fillable_units_24h(limit, buy_price, vol_day, bankroll):
    """Units you can realistically buy & flip in a 24h window, bounded by:
      - the GE buy limit (resets 6x/day, so limit * 6),
      - the item's average daily traded volume (can't buy what isn't sold),
      - your liquid capital (can't buy more than you can afford)."""
    by_limit = limit * GE_WINDOWS_24H
    by_capital = bankroll / buy_price if buy_price and buy_price > 0 else 0
    return max(0, int(min(by_limit, vol_day or 0, by_capital)))


# --------------------------------------------------- stage 1: prefilter

def prefilter(bankroll, min_deploy_frac, max_spread_pct, stale_hours,
              min_price=500_000):
    """Cheap pass over every item. Three bulk calls, no per-item requests."""
    mapping = get("/mapping")
    latest = get("/latest").get("data", {})
    hourly = get("/1h").get("data", {})

    now = time.time()
    stale_cutoff = stale_hours * 3600
    min_deploy = bankroll * min_deploy_frac

    rows = []
    for meta in mapping:
        iid = meta["id"]
        limit = meta.get("limit") or 0
        if limit <= 0:
            continue

        live = latest.get(str(iid))
        if not live:
            continue
        high, low = live.get("high"), live.get("low")
        if not high or not low or high <= low:
            continue

        # Only serious items — skip anything trading under the price floor.
        if low < min_price:
            continue

        # Both sides must have traded recently or the spread is fiction.
        ht, lt = live.get("highTime", 0), live.get("lowTime", 0)
        if now - min(ht, lt) > stale_cutoff:
            continue

        # Absurd spreads are manipulation or dead books, not opportunity.
        if (high - low) / low * 100 > max_spread_pct:
            continue

        margin = net_margin(high, low)
        if margin <= 0:
            continue

        # Must have traded at all in the last hour.
        hr = hourly.get(str(iid)) or {}
        buy_vol_1h = hr.get("highPriceVolume") or 0
        sell_vol_1h = hr.get("lowPriceVolume") or 0
        hr_vol = buy_vol_1h + sell_vol_1h
        if hr_vol <= 0:
            continue

        # Can this item absorb a meaningful slice of the bankroll?
        cap_per_4h = limit * high
        if cap_per_4h < min_deploy:
            continue

        rows.append({
            "id": iid,
            "name": meta["name"],
            "limit": limit,
            "members": meta.get("members"),
            "high": high,
            "low": low,
            "margin": margin,
            "roi": margin / low * 100,
            "cap_per_4h": cap_per_4h,
            "hr_vol": hr_vol,
            "buy_vol_1h": buy_vol_1h,
            "sell_vol_1h": sell_vol_1h,
        })

    # Rank by raw gp opportunity per window, capped by observed flow.
    for r in rows:
        units = min(r["limit"], r["hr_vol"] * 4)
        r["prefilter_score"] = r["margin"] * units

    rows.sort(key=lambda r: r["prefilter_score"], reverse=True)
    return rows


# ----------------------------------------------------- stage 2: history

def _parse_timeseries(data):
    """/timeseries `data` array -> (series, vols) daily price/volume lists,
    oldest first. Shared by the live enrich() path and anything reading the
    same shape from storage (the backfill script)."""
    series, vols = [], []
    for p in data:
        hi, lo = p.get("avgHighPrice"), p.get("avgLowPrice")
        if hi is None and lo is None:
            continue
        series.append((hi + lo) / 2 if (hi and lo) else (hi or lo))
        vols.append((p.get("highPriceVolume") or 0) + (p.get("lowPriceVolume") or 0))
    return series, vols


def derive_trend_metrics(row, series, vols):
    """Pure: derive trend/volatility/rank stats from a daily price series and
    matching daily-volume series (oldest first, `row` already has high/low
    set). Returns the updated row, or None if there is not enough history
    yet. This is the math half of the old enrich() — split out so both the
    live per-item /timeseries path AND the DB-backed all-items path
    (scan_market_fast) compute identically, from different sources."""
    if len(series) < 45:
        return None

    now = (row["high"] + row["low"]) / 2
    d30, d90 = series[-30:], series[-90:]
    ma30 = statistics.mean(d30)
    sd30 = statistics.pstdev(d30) or 1.0
    vol_day = statistics.mean(vols[-30:]) if vols else 0.0

    s30 = slope_pct_per_day(d30)
    s7 = slope_pct_per_day(series[-7:])

    if s30 < -0.2 and s7 > 0.2:
        trend = "bounce"
    elif s30 > 0.2:
        trend = "rising"
    elif s30 < -0.2:
        trend = "falling"
    else:
        trend = "flat"

    row.update({
        "now": now,
        "vol_day": vol_day,
        "rank90": rank_within(d90, now),
        "rank_all": rank_within(series, now),
        "z30": (now - ma30) / sd30,
        "slope30": s30,
        "slope7": s7,
        "trend": trend,
        "volatility": volatility_pct(d90),
        "spark": [round(x) for x in d30],   # 30d midpoints for the sparkline
        "days": len(series),
    })
    return row


def fetch_daily_candle(item_id, day, sleep=0.6):
    """Gap-fill helper: one /timeseries call → the candle for `day`, or None.
    Used only by the daily rollover when an item had too few 10-min snapshots
    (app downtime). Returns a dict ready for db.insert_daily_history."""
    data = get("/timeseries", id=item_id, timestep="24h").get("data", [])
    time.sleep(sleep)
    for point in data:
        ts = point.get("timestamp")
        if ts is None:
            continue
        candle_day = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if candle_day != day:
            continue
        hi, lo = point.get("avgHighPrice"), point.get("avgLowPrice")
        if hi is None and lo is None:
            return None
        price = (hi + lo) / 2 if (hi and lo) else (hi or lo)
        volume = (point.get("highPriceVolume") or 0) + (point.get("lowPriceVolume") or 0)
        return {"item_id": item_id, "day": day, "price": price, "volume": volume,
                "source": "gapfill"}
    return None


def enrich(row, sleep):
    """Pull a year of daily candles and derive trend metrics. Legacy,
    network-per-item path — scan_market_fast() gets the same series shape
    from item_daily_history instead. See MIGRATION_PLAN_V2.md."""
    data = get("/timeseries", id=row["id"], timestep="24h").get("data", [])
    time.sleep(sleep)
    series, vols = _parse_timeseries(data)
    return derive_trend_metrics(row, series, vols)


# -------------------------------------------------------------- scoring

def score(rows):
    """Percentile-normalised within the candidate set, not absolute."""
    if not rows:
        return

    gp = [r["gp_24h"] for r in rows]
    vv = [r["volatility"] for r in rows]
    lv = [math.log1p(r["vol_day"]) for r in rows]

    for r in rows:
        liq = rank_within(lv, math.log1p(r["vol_day"]))
        vol = rank_within(vv, r["volatility"])
        thr = rank_within(gp, r["gp_24h"])

        # Flip: repeatable spread capture. Wants throughput and liquidity.
        r["flip"] = 100 * (0.60 * thr + 0.40 * liq)

        # Swing: buy low in the item's own range and wait. Wants cheapness
        # and movement, and penalises catching a falling knife.
        cheap = 1.0 - r["rank_all"]
        revert = min(max(-r["z30"], 0.0), 2.0) / 2.0
        knife = 0.65 if r["trend"] == "falling" else 1.0
        bonus = 1.15 if r["trend"] == "bounce" else 1.0
        raw = (0.40 * cheap + 0.25 * revert + 0.20 * vol + 0.15 * liq)
        r["swing"] = 100 * min(raw * knife * bonus, 1.0)


# --------------------------------------------------------------- output

def fmt_gp(n):
    if n is None:
        return "-"
    for div, suf in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if abs(n) >= div:
            return f"{n / div:.2f}{suf}"
    return f"{n:.0f}"


def parse_gp(text):
    """Parse a gp amount like '700m', '1.5b', '500k' or '1000000' -> int."""
    s = str(text).strip().lower().replace(",", "").replace("gp", "").strip()
    if not s:
        raise ValueError("empty")
    mult = 1
    if s[-1] in "kmb":
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[s[-1]]
        s = s[:-1]
    return int(round(float(s) * mult))


# ----------------------------------------------------- content catalysts

NEWS_RSS = "https://secure.runescape.com/m=news/latest_news.rss?oldschool=true"

# Words that appear in item names but carry no signal about a specific item.
_STOP_TOKENS = {
    "rune", "dragon", "ancient", "ornament", "godsword", "armour", "armor",
    "boots", "gloves", "helm", "shield", "sword", "staff", "ring", "amulet",
    "cape", "body", "legs", "plate", "kit", "seed", "ore", "bar", "potion",
    "scroll", "crystal", "guthix", "saradomin", "zamorak", "armadyl", "bandos",
}


def fetch_news(limit=40):
    """Recent OSRS updates from the official news RSS. [] on any failure."""
    try:
        r = SESSION.get(NEWS_RSS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []

    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        when = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                when = parsedate_to_datetime(pub)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                when = None
        out.append({
            "title": title,
            "url": (item.findtext("link") or "").strip(),
            "when": when,
        })
        if len(out) >= limit:
            break
    return out


def _sig_tokens(name):
    """Meaningful lowercase tokens of an item name for catalyst matching."""
    toks = re.findall(r"[a-z0-9']{4,}", name.lower())
    return [t for t in toks if t not in _STOP_TOKENS]


def catalyst_for(name, news):
    """Best-effort: does a recent update mention this item? (bonus, note)."""
    tokens = _sig_tokens(name)
    if not tokens:
        return 0.0, None

    now = datetime.now(timezone.utc)
    best = (0.0, None)
    for entry in news:
        title_l = entry["title"].lower()
        matched = [t for t in tokens
                   if re.search(rf"\b{re.escape(t)}\b", title_l)]
        # Need a strong single word or two corroborating words to count.
        if not matched or not (len(matched) >= 2 or any(len(t) >= 6 for t in matched)):
            continue

        if entry["when"]:
            age_days = (now - entry["when"]).total_seconds() / 86400
        else:
            age_days = 45.0
        if age_days <= 30:
            weight = 1.0
        elif age_days <= 90:
            weight = 0.5
        else:
            weight = 0.2

        bonus = min(0.15, 0.05 * weight * len(matched))
        if bonus > best[0]:
            note = entry["title"]
            if entry["when"]:
                note += f" ({entry['when'].date().isoformat()})"
            best = (bonus, note)
    return best


# --------------------------------------------------- composite merch score

def merch_score(rows, news=None):
    """Single 0-100 score blending profit throughput, liquidity, value,
    volatility, trend and any live content catalyst. Percentile-normalised
    within the current candidate set, so it ranks *these* items against
    each other rather than against absolutes.

    `news` is the live RSS feed (writer path). Pass None to reuse the
    catalyst bonus already resolved onto each row as `catalyst_bonus` —
    that is the reader path, and it is why serving a snapshot never needs
    a network call. Both paths do identical arithmetic."""
    if not rows:
        return

    gp = [r["gp_24h"] for r in rows]
    lv = [math.log1p(r["vol_day"]) for r in rows]
    vv = [r["volatility"] for r in rows]

    trend_adj = {"bounce": 0.10, "rising": 0.08, "flat": 0.0, "falling": -0.10}

    for r in rows:
        throughput = rank_within(gp, r["gp_24h"])
        liq = rank_within(lv, math.log1p(r["vol_day"]))
        vol = rank_within(vv, r["volatility"])
        value = 1.0 - r["rank_all"]          # cheap within its own range

        if news is None:
            bonus = r.get("catalyst_bonus") or 0.0
        else:
            bonus, note = catalyst_for(r["name"], news)
            r["catalyst_bonus"] = bonus
            r["catalyst"] = note

        # Kept on the row (not folded away) so the UI can show a per-item
        # breakdown of what drove merch_score, not just the final number.
        r["sc_throughput"] = round(100 * throughput, 1)
        r["sc_liquidity"] = round(100 * liq, 1)
        r["sc_volatility_rank"] = round(100 * vol, 1)
        r["sc_value"] = round(100 * value, 1)
        r["sc_trend_adj"] = round(100 * trend_adj.get(r["trend"], 0.0), 1)
        r["sc_catalyst"] = round(100 * bonus, 1)

        raw = (0.30 * throughput + 0.20 * liq + 0.15 * vol
               + 0.20 * value + trend_adj.get(r["trend"], 0.0) + bonus)
        r["merch_score"] = round(100 * min(max(raw, 0.0), 1.0), 1)
        r["reason"] = reason_for(r)


def reason_for(r):
    """One-sentence, human-readable rationale from the dominant factors."""
    bits = []

    pct = r["rank_all"] * 100
    if pct <= 35:
        bits.append(f"cheap at the {pct:.0f}th percentile of its own range")
    elif pct >= 70:
        bits.append(f"pricey at the {pct:.0f}th percentile of its range")
    else:
        bits.append(f"mid-range ({pct:.0f}th percentile)")

    bits.append(f"{fmt_gp(r['margin'])} net margin/unit after tax "
                f"(~{fmt_gp(r['gp_24h'])}/24h on {r['units_24h']:,} units)")

    trend_word = {
        "bounce": "just turned up after a dip",
        "rising": "trending up",
        "falling": "still sliding — knife risk",
        "flat": "flat",
    }.get(r["trend"], r["trend"])
    bits.append(trend_word)

    if r["catalyst"]:
        bits.append(f"recent update: {r['catalyst']}")

    return "; ".join(bits) + "."


# ------------------------------------------------------- scan orchestration

# The stable, JSON-serialisable shape handed to the CLI, the GUI and the API.
PICK_KEYS = ("id", "name", "now", "margin", "roi", "gp_24h", "vol_day",
             "rank90", "rank_all", "z30", "trend", "volatility", "flip",
             "swing", "merch_score", "catalyst", "reason", "limit",
             "buy_price", "sell_price", "tax", "sell_net", "units_24h", "spark",
             "sc_throughput", "sc_liquidity", "sc_volatility_rank", "sc_value",
             "sc_trend_adj", "sc_catalyst", "buy_vol_1h", "sell_vol_1h")

# What a snapshot stores per item: everything that is pure market data, i.e.
# independent of any one user's capital or floor. Note the absentees —
# units_24h, gp_24h, flip, swing, merch_score and reason are all derived at
# read time by rank_for, because all six depend on capital.
MARKET_KEYS = ("id", "name", "buy_price", "sell_price", "tax", "sell_net",
               "margin", "roi", "now", "vol_day", "limit", "volatility",
               "trend", "rank_all", "rank90", "z30", "catalyst",
               "catalyst_bonus", "spark", "buy_vol_1h", "sell_vol_1h")


@dataclass
class ScanConfig:
    """Parameters for a shared, capital-agnostic scan.

    `reference_bankroll` is NOT "the biggest clan bankroll". It feeds
    prefilter's capacity test (`limit * high >= bankroll * min_deploy_frac`),
    which gets STRICTER as the bankroll grows — a big reference value would
    quietly drop the small and mid-cap items that a smaller member needs.
    Set it to the SMALLEST capital any user might enter so the shortlist is
    a superset for everyone.

    `global_floor` is likewise the LOWEST price floor any user might pick;
    higher personal floors are applied at read time by rank_for."""
    reference_bankroll: int = 100_000_000
    global_floor: int = 100_000
    shortlist: int = 160
    sleep: float = 0.6
    min_deploy_frac: float = 0.01
    max_spread_pct: float = 25.0
    stale_hours: float = 12.0

    def as_params(self):
        return {
            "reference_bankroll": self.reference_bankroll,
            "global_floor": self.global_floor,
            "shortlist": self.shortlist,
            "sleep": self.sleep,
            "min_deploy_frac": self.min_deploy_frac,
            "max_spread_pct": self.max_spread_pct,
            "stale_hours": self.stale_hours,
        }


def scan_market(cfg, on_event=None):
    """Run the expensive, capital-agnostic half of the pipeline.

    Returns `(meta, market_rows)`. `meta` describes the snapshot; each
    market row holds MARKET_KEYS. Nothing here depends on a user's capital
    or floor — that is rank_for's job.

    `on_event(kind, **data)` is the structured progress feed that drives the
    live console. Kinds: "log" (msg, level), "phase" (label), "progress"
    (done, total, name, kept). Unchanged from the original run_scan."""

    def log(msg, level="info"):
        if on_event:
            on_event("log", msg=msg, level=level)

    def phase(label):
        if on_event:
            on_event("phase", label=label)

    started_at = time.time()

    phase("Pulling recent OSRS updates")
    log("Fetching official OSRS news feed for content catalysts...")
    news = fetch_news()
    log(f"news feed: {len(news)} recent updates", "ok")

    phase("Bulk scan of every tradeable item")
    log(f"Reference capital: {fmt_gp(cfg.reference_bankroll)} "
        f"· global price floor {fmt_gp(cfg.global_floor)}")
    log("Fetching /mapping, /latest and /1h (three bulk calls)...")
    cands = prefilter(cfg.reference_bankroll, cfg.min_deploy_frac,
                      cfg.max_spread_pct, cfg.stale_hours, cfg.global_floor)
    log(f"{len(cands)} items passed liquidity + price + capacity filters", "ok")
    cands = cands[:cfg.shortlist]
    log(f"shortlist trimmed to top {len(cands)} by raw gp opportunity")

    phase(f"Pulling a year of history for {len(cands)} items")
    rows = []
    total = len(cands)
    kept = 0
    for i, c in enumerate(cands, 1):
        try:
            enriched = enrich(c, cfg.sleep)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {c['name']}: {e}", file=sys.stderr)
            log(f"[{i}/{total}] {c['name']} — error: {e}", "err")
            enriched = None
        if enriched:
            rows.append(enriched)
            kept += 1
            log(f"[{i}/{total}] {c['name']} — {enriched['days']}d history, "
                f"trend {enriched['trend']}", "ok")
        elif enriched is None and c["name"]:
            log(f"[{i}/{total}] {c['name']} — skipped (thin history)", "dim")
        if on_event:
            on_event("progress", done=i, total=total, name=c["name"], kept=kept)

    phase("Computing market economics")
    log(f"Deriving after-tax margins for {len(rows)} items...")

    # Concrete economics per item. Buy at the insta-sell price (low); sell
    # into the current insta-buy price (high) for a safe fill. margin is
    # already net of the 2% GE tax (capped at 5m/item). The capital-scaled
    # part — units_24h and gp_24h — is deliberately NOT computed here.
    for r in rows:
        high, low = r["high"], r["low"]
        r["buy_price"] = low
        r["sell_price"] = high
        r["tax"] = round(min(high * 0.02, 5_000_000))
        r["sell_net"] = round(high - r["tax"])
        bonus, note = catalyst_for(r["name"], news)
        r["catalyst_bonus"] = bonus
        r["catalyst"] = note

    log(f"snapshot ready — {len(rows)} items with market metrics", "ok")

    meta = {
        "started_at": started_at,
        "finished_at": time.time(),
        "price_ts": started_at,
        "params": cfg.as_params(),
        "n_items": len(rows),
    }
    market_rows = [{k: r.get(k) for k in MARKET_KEYS} for r in rows]
    return meta, market_rows


def scan_market_fast(cfg, history_fn, on_event=None):
    """All-items scan: 3 bulk calls + one batched history read, ZERO
    per-item network calls. See MIGRATION_PLAN_V2.md §3.

    Reuses prefilter()'s bulk fetch and data-quality filters, but — unlike
    scan_market — does NOT apply cfg.shortlist. Every item that passes the
    liquidity/price/spread checks gets scored, sourcing its daily history
    from `history_fn(item_ids) -> {item_id: [(day, price, volume), ...]}`
    (oldest first) instead of scanner.enrich()'s live /timeseries call. The
    caller (worker.py) passes db.daily_history_for, keeping this module free
    of any storage dependency — same separation scan_market already has.

    `derive_trend_metrics` is the exact same function enrich() uses, so a
    given item scores identically whichever path fetched its history from.

    Returns (meta, market_rows) like scan_market. meta also carries
    "item_meta" (id/name/limit/members for every candidate, for db.upsert_items)
    and "raw_snapshots" (id/high/low/buy_vol_1h/sell_vol_1h for every
    candidate — including thin-history ones excluded from market_rows — for
    db.insert_snapshots, which feeds the daily rollover)."""
    def log(msg, level="info"):
        if on_event:
            on_event("log", msg=msg, level=level)

    def phase(label):
        if on_event:
            on_event("phase", label=label)

    started_at = time.time()

    phase("Pulling recent OSRS updates")
    log("Fetching official OSRS news feed for content catalysts...")
    news = fetch_news()
    log(f"news feed: {len(news)} recent updates", "ok")

    phase("Bulk scan of every tradeable item")
    log(f"Reference capital: {fmt_gp(cfg.reference_bankroll)} "
        f"· global price floor {fmt_gp(cfg.global_floor)}")
    log("Fetching /mapping, /latest and /1h (three bulk calls)...")
    cands = prefilter(cfg.reference_bankroll, cfg.min_deploy_frac,
                      cfg.max_spread_pct, cfg.stale_hours, cfg.global_floor)
    log(f"{len(cands)} items passed liquidity + price + capacity filters "
        "(no shortlist cut — every one gets scored)", "ok")

    phase(f"Loading stored history for {len(cands)} items")
    history = history_fn([c["id"] for c in cands])
    log(f"history loaded for {len(history)}/{len(cands)} items from the DB "
        "— 0 /timeseries calls this cycle", "ok")

    rows = []
    total = len(cands)
    kept = 0
    for i, c in enumerate(cands, 1):
        series_vols = history.get(c["id"])
        enriched = None
        if series_vols:
            series = [p for _, p, _ in series_vols]
            vols = [v or 0 for _, _, v in series_vols]
            enriched = derive_trend_metrics(c, series, vols)
        if enriched:
            rows.append(enriched)
            kept += 1
        if on_event:
            on_event("progress", done=i, total=total, name=c["name"], kept=kept)
    log(f"{kept}/{total} items had enough stored history to score "
        f"({total - kept} too new / not yet backfilled)", "ok")

    phase("Computing market economics")
    log(f"Deriving after-tax margins for {len(rows)} items...")

    for r in rows:
        high, low = r["high"], r["low"]
        r["buy_price"] = low
        r["sell_price"] = high
        r["tax"] = round(min(high * 0.02, 5_000_000))
        r["sell_net"] = round(high - r["tax"])
        bonus, note = catalyst_for(r["name"], news)
        r["catalyst_bonus"] = bonus
        r["catalyst"] = note

    log(f"snapshot ready — {len(rows)} items with market metrics", "ok")

    meta = {
        "started_at": started_at,
        "finished_at": time.time(),
        "price_ts": started_at,
        "params": cfg.as_params(),
        "n_items": len(rows),
        "item_meta": [{"id": c["id"], "name": c["name"], "limit": c.get("limit"),
                        "members": c.get("members")} for c in cands],
        "raw_snapshots": [{"id": c["id"], "high": c["high"], "low": c["low"],
                            "buy_vol_1h": c.get("buy_vol_1h"),
                            "sell_vol_1h": c.get("sell_vol_1h")} for c in cands],
    }
    market_rows = [{k: r.get(k) for k in MARKET_KEYS} for r in rows]
    return meta, market_rows


def rank_for(market_rows, capital, floor=0, top=50, mode="any"):
    """Personalise a snapshot for one user. No network, no mutation of the
    caller's rows — safe to call concurrently on a shared snapshot.

    Applies the user's price floor, computes how much of each flip their
    capital can actually absorb in 24h, then scores and ranks. Percentiles
    are taken within the post-floor set, which is exactly what the original
    single-shot pipeline did (its floor was applied during prefilter)."""
    rows = [dict(r) for r in market_rows if (r.get("buy_price") or 0) >= floor]
    if not rows:
        return []

    for r in rows:
        r["units_24h"] = fillable_units_24h(r["limit"], r["buy_price"],
                                            r["vol_day"], capital)
        r["gp_24h"] = round(r["margin"] * r["units_24h"])

    score(rows)
    merch_score(rows)          # news=None -> reuse the stored catalyst bonus

    if mode == "buy":
        rows = [r for r in rows if r["rank_all"] <= 0.5]
    elif mode == "avoid":
        rows = [r for r in rows if r["rank_all"] >= 0.5]

    rows.sort(key=lambda r: r["merch_score"], reverse=True)
    picks = rows[:top]

    for r in picks:
        r["reason"] = (r["reason"].rstrip(".")
                       + f"; buy ~{fmt_gp(r['buy_price'])}, "
                       f"safe-sell ~{fmt_gp(r['sell_price'])}.")

    return [{k: r.get(k) for k in PICK_KEYS} for r in picks]
