"""analysis.py — deterministic dip/rise prediction layer on top of scanner.py.

Pure functions over an already-personalised `rank_for()` result. Makes zero
network calls and mutates nothing it is handed, same discipline as
scanner.rank_for. Every score here is an explicit, auditable formula over
fields the scan already computed (or that this module derives from the
30-day `spark` series and the `/1h` buy/sell volume split) — there is no
trained model and no hidden state, by design: see README's "tuned by
judgement, not backtest" philosophy and the request for deterministic,
explainable recommendations over an opaque ML score.

Indicator note: EMA(5,20) and RSI(14) are computed on the *daily* `spark`
series (30 points), not true 5m/1h intraday candles — that data is only
ever fetched in bulk (current snapshot, not history) to stay inside the
writer's per-scan wiki call budget. Treat them as daily-resolution signals.

    analyze(picks) -> list[dict]
        Takes scanner.rank_for()'s output, adds predictive fields. Does not
        change the sort order — callers sort by whichever new field their
        mode cares about (see predict_cli.py).

        Adding these lines to test something. Fully ignore.
"""

import scanner


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ------------------------------------------------------------- indicators

def ema(values, period):
    """Exponential moving average. Degrades `period` down to the series
    length rather than failing outright on thin history."""
    vals = [v for v in values if v]
    if not vals:
        return None
    period = min(period, len(vals))
    k = 2 / (period + 1)
    e = sum(vals[:period]) / period
    for v in vals[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values, period=14):
    """Wilder's RSI. None if there isn't enough history to seed it."""
    vals = [v for v in values if v]
    if len(vals) < period + 1:
        return None

    gains = [max(b - a, 0) for a, b in zip(vals, vals[1:])]
    losses = [max(a - b, 0) for a, b in zip(vals, vals[1:])]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema_signal(ema5, ema20):
    if ema5 is None or ema20 is None:
        return "unknown"
    if ema5 > ema20:
        return "bullish"
    if ema5 < ema20:
        return "bearish"
    return "neutral"


def _rsi_state(rsi14):
    if rsi14 is None:
        return "unknown"
    if rsi14 < 30:
        return "oversold"
    if rsi14 > 70:
        return "overbought"
    return "neutral"


# ------------------------------------------------------------------ risk

def _risk_flags(r, vol_day_rank):
    flags = []

    hr_vol = (r.get("buy_vol_1h") or 0) + (r.get("sell_vol_1h") or 0)
    typical_hourly = (r.get("vol_day") or 0) / 24
    # A burst of trades on an item that's normally thin is the classic
    # setup for a manufactured spike, not organic demand.
    if vol_day_rank < 0.30 and typical_hourly > 0 and hr_vol > 5 * typical_hourly:
        flags.append("volume_spike")

    # prefilter already rejects spreads over 25%; flag anything hugging
    # that ceiling as a widening book rather than a stable opportunity.
    if r["spread_pct"] >= 20:
        flags.append("wide_spread")

    rsi14 = r["rsi14"]
    if (rsi14 is not None and rsi14 > 80 and r.get("trend") == "rising"
            and (r.get("sc_volatility_rank") or 0) >= 75):
        flags.append("pump_dump_risk")

    return flags


def _risk_level(flags):
    if len(flags) >= 2:
        return "HIGH"
    if len(flags) == 1:
        return "MEDIUM"
    return "LOW"


def _predicted_trend(r):
    if "pump_dump_risk" in r["risk_flags"]:
        return "RISK: PUMP-DUMP"
    if r["dip_confidence"] >= 70 and r["rsi_state"] == "oversold":
        return "STRONG BUY DIP"
    if r["dip_confidence"] >= 45:
        return "BUY DIP"
    if r["rsi_state"] == "overbought" or (r.get("z30") or 0) > 1.5:
        return "OVERBOUGHT"
    return "NEUTRAL"


# --------------------------------------------------------------- analyze

def analyze(rows):
    """Add predictive fields to a rank_for() result. Returns new dicts;
    does not mutate or reorder `rows`."""
    if not rows:
        return []

    out = [dict(r) for r in rows]

    hr_liq = [(r.get("buy_vol_1h") or 0) + (r.get("sell_vol_1h") or 0) for r in out]
    vol_day = [r.get("vol_day") or 0 for r in out]

    for r in out:
        bp, sp = r.get("buy_price") or 0, r.get("sell_price") or 0
        r["spread_pct"] = round((sp - bp) / bp * 100, 3) if bp else 0.0

        spark = r.get("spark") or []
        r["ema5"] = ema(spark, 5)
        r["ema20"] = ema(spark, 20)
        r["ema_signal"] = _ema_signal(r["ema5"], r["ema20"])
        r["rsi14"] = rsi(spark, 14)
        r["rsi_state"] = _rsi_state(r["rsi14"])

        buy_v, sell_v = r.get("buy_vol_1h") or 0, r.get("sell_vol_1h") or 0
        r["vol_ratio"] = round(buy_v / sell_v, 3) if sell_v else None

    spread = [r["spread_pct"] for r in out]

    for i, r in enumerate(out):
        liq_rank = scanner.rank_within(hr_liq, hr_liq[i])
        vol_day_rank = scanner.rank_within(vol_day, vol_day[i])
        spread_rank = scanner.rank_within(spread, spread[i])

        rsi14 = r["rsi14"]
        oversold = clamp((30 - rsi14) / 30, 0, 1) if rsi14 is not None else 0.0
        z30 = r.get("z30") or 0.0
        revert = clamp(-z30, 0, 2) / 2

        # Short-term Dip Confidence: oversold RSI + price well below its own
        # mean + fresh (last-hour) liquidity backing the move up.
        r["dip_confidence"] = round(100 * clamp(
            0.40 * oversold + 0.35 * revert + 0.25 * liq_rank, 0, 1), 1)

        # High-Margin Flipping Opportunity: wide spread + throughput
        # liquidity (structural, so vol_day rather than the noisier 1h read).
        r["flip_score"] = round(100 * clamp(
            0.55 * spread_rank + 0.45 * vol_day_rank, 0, 1), 1)

        r["risk_flags"] = _risk_flags(r, vol_day_rank)
        r["risk_level"] = _risk_level(r["risk_flags"])
        r["predicted_trend"] = _predicted_trend(r)
        r["est_roi_pct"] = round(r.get("roi") or 0.0, 2)

    return out
