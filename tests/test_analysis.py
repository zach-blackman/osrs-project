"""analysis.py: EMA/RSI math against hand-computed values, plus the
deterministic classification boundaries (dip / overbought / risk flags).
No network, no DB — pure function checks in the same style as
test_parity.py.
"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import analysis  # noqa: E402


def run_indicator_checks():
    failures = []

    # Constant series: EMA of a flat line is that same value, exactly.
    flat = [42.0] * 25
    e = analysis.ema(flat, 5)
    if e != 42.0:
        failures.append(f"ema(flat) = {e}, want 42.0")

    # period == len(values): EMA degrades to a plain SMA (no smoothing left).
    e = analysis.ema([10, 20, 30], 3)
    if e != 20.0:
        failures.append(f"ema([10,20,30], 3) = {e}, want 20.0 (SMA)")

    # period > len(values): degrades to SMA over what's available.
    e = analysis.ema([5, 10], 5)
    if e != 7.5:
        failures.append(f"ema([5,10], 5) = {e}, want 7.5")

    # Monotonic uptrend: every step is a gain, avg_loss is 0 -> RSI 100.
    up = list(range(71, 101))  # 30 points, strictly increasing
    r = analysis.rsi(up, 14)
    if r != 100.0:
        failures.append(f"rsi(monotonic up) = {r}, want 100.0")

    # Monotonic downtrend: every step is a loss, avg_gain is 0 -> RSI 0.
    down = list(range(100, 70, -1))  # 30 points, strictly decreasing
    r = analysis.rsi(down, 14)
    if r != 0.0:
        failures.append(f"rsi(monotonic down) = {r}, want 0.0")

    # Too little history to seed Wilder's average -> None, not a guess.
    r = analysis.rsi([1, 2, 3, 4, 5], 14)
    if r is not None:
        failures.append(f"rsi(short series) = {r}, want None")

    if not failures:
        print("  ok  ema/rsi hand-computed values")
    return failures


def _row(name, spark, buy_price=100_000, sell_price=110_000, vol_day=500,
        buy_vol_1h=100, sell_vol_1h=100, z30=0.0, trend="flat",
        sc_volatility_rank=10.0, roi=5.0):
    return {
        "id": hash(name) % 100000, "name": name, "buy_price": buy_price,
        "sell_price": sell_price, "vol_day": vol_day, "buy_vol_1h": buy_vol_1h,
        "sell_vol_1h": sell_vol_1h, "z30": z30, "trend": trend,
        "sc_volatility_rank": sc_volatility_rank, "roi": roi, "spark": spark,
    }


def run_classification_checks():
    failures = []

    up = list(range(71, 101))
    down = list(range(100, 70, -1))

    dip_row = _row("Dip candidate", down, z30=-2.5,
                   buy_vol_1h=1000, sell_vol_1h=200, vol_day=500)
    overbought_row = _row("Overbought item", up, z30=0.5,
                          buy_vol_1h=50, sell_vol_1h=50, vol_day=50)
    pump_row = _row("Pump risk", up, z30=0.5, trend="rising",
                    sc_volatility_rank=90.0)
    wide_spread_row = _row("Wide spread item", [50_000] * 20,
                           buy_price=100_000, sell_price=124_000)

    rows = analysis.analyze([dip_row, overbought_row, pump_row, wide_spread_row])
    by_name = {r["name"]: r for r in rows}

    dip = by_name["Dip candidate"]
    if dip["rsi_state"] != "oversold":
        failures.append(f"dip row rsi_state = {dip['rsi_state']}, want oversold")
    if dip["predicted_trend"] != "STRONG BUY DIP":
        failures.append(
            f"dip row predicted_trend = {dip['predicted_trend']!r}, "
            f"want 'STRONG BUY DIP' (dip_confidence={dip['dip_confidence']})")

    ob = by_name["Overbought item"]
    if ob["rsi_state"] != "overbought":
        failures.append(f"overbought row rsi_state = {ob['rsi_state']}")
    if ob["predicted_trend"] != "OVERBOUGHT":
        failures.append(
            f"overbought row predicted_trend = {ob['predicted_trend']!r}, "
            f"want 'OVERBOUGHT'")

    pump = by_name["Pump risk"]
    if "pump_dump_risk" not in pump["risk_flags"]:
        failures.append(
            f"pump row risk_flags = {pump['risk_flags']}, "
            f"want 'pump_dump_risk' present")
    if pump["predicted_trend"] != "RISK: PUMP-DUMP":
        failures.append(
            f"pump row predicted_trend = {pump['predicted_trend']!r}, "
            f"want 'RISK: PUMP-DUMP' (overrides other signals)")

    wide = by_name["Wide spread item"]
    if wide["spread_pct"] != 24.0:
        failures.append(f"wide spread row spread_pct = {wide['spread_pct']}, want 24.0")
    if "wide_spread" not in wide["risk_flags"]:
        failures.append(f"wide spread row risk_flags = {wide['risk_flags']}, "
                        f"want 'wide_spread' present")
    if wide["risk_level"] == "LOW":
        failures.append("wide spread row risk_level = LOW, want at least MEDIUM")

    if not failures:
        print("  ok  dip / overbought / pump-dump / wide-spread classification")
    return failures


if __name__ == "__main__":
    print("analysis: indicator math and classification thresholds")
    bad = run_indicator_checks()
    bad += run_classification_checks()

    if bad:
        print(f"\nFAILED ({len(bad)} problems)")
        for f in bad[:20]:
            print("  -", f)
        sys.exit(1)
    print("\nPASS")
