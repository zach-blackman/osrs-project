"""Parity: the new scan_market/rank_for split must reproduce the original
monolithic run_scan exactly.

`_legacy_monolith.py` is a frozen copy of osrs_merch_scan.py from before the
refactor. It is the oracle — not something to edit.

The equivalence holds where the two pipelines are given the same inputs,
i.e. when the snapshot's (reference_bankroll, global_floor) equal the
user's (capital, floor). It cannot hold universally, and that is not a bug:
`score`/`merch_score` are percentile-normalised *within the candidate set*,
and the legacy pipeline's set depends on its floor and bankroll (both feed
prefilter) before the top-`shortlist` trim. Reading a shared snapshot at a
higher floor therefore ranks within a legitimately different population.
test_read_time_personalisation pins the invariants that DO hold there.
"""

import importlib.util
import pathlib
import sys
import time
from datetime import date, timedelta

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_wiki  # noqa: E402
import scanner  # noqa: E402


def _load_legacy():
    spec = importlib.util.spec_from_file_location(
        "_legacy_monolith", HERE / "_legacy_monolith.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PAIRS = [
    (100_000_000, 100_000),
    (700_000_000, 500_000),
    (50_000_000, 0),
    (5_000_000_000, 1_000_000),
]

# Fields added to PICK_KEYS after _legacy_monolith was frozen.
_LEGACY_SKIP = {
    "sc_throughput", "sc_liquidity", "sc_volatility_rank", "sc_value",
    "sc_trend_adj", "sc_catalyst", "buy_vol_1h", "sell_vol_1h",
}


def run_parity():
    real_time = time.time
    legacy = _load_legacy()
    world = fake_wiki.build_world()
    fake_wiki.install(legacy, world)
    fake_wiki.install(scanner, world)

    failures = []
    try:
        for capital, floor in PAIRS:
            params = {
                "bankroll": capital,
                "min_price": floor,
                "shortlist": 160,
                "sleep": 0,
                "min_deploy_frac": 0.01,
                "max_spread_pct": 25.0,
                "stale_hours": 12.0,
                "top": 50,
                "mode": "any",
            }
            expected = legacy.run_scan(params)

            cfg = scanner.ScanConfig(
                reference_bankroll=capital, global_floor=floor,
                shortlist=160, sleep=0, min_deploy_frac=0.01,
                max_spread_pct=25.0, stale_hours=12.0)
            _meta, market_rows = scanner.scan_market(cfg)
            actual = scanner.rank_for(market_rows, capital, floor, top=50)

            label = f"capital={capital:,} floor={floor:,}"
            if len(expected) != len(actual):
                failures.append(
                    f"{label}: {len(expected)} legacy picks vs {len(actual)} new")
                continue
            n_before = len(failures)
            for e, a in zip(expected, actual):
                for k in scanner.PICK_KEYS:
                    if k in _LEGACY_SKIP:
                        continue
                    if e.get(k) != a.get(k):
                        failures.append(
                            f"{label}: item {e['name']!r} field {k!r}: "
                            f"legacy {e.get(k)!r} != new {a.get(k)!r}")
            if len(failures) == n_before:
                print(f"  ok  {label}: {len(actual)} picks identical")
            else:
                print(f"  FAIL {label}: {len(failures) - n_before} field diffs")
    finally:
        time.time = real_time
    return failures


def run_read_time_personalisation():
    """One shared snapshot, many users — the whole point of the migration."""
    real_time = time.time
    world = fake_wiki.build_world()
    fake_wiki.install(scanner, world)

    calls = {"n": 0}
    inner = scanner.get

    def counting_get(path, retries=3, **params):
        calls["n"] += 1
        return inner(path, retries, **params)

    failures = []
    try:
        scanner.get = counting_get
        cfg = scanner.ScanConfig(reference_bankroll=100_000_000,
                                 global_floor=100_000, shortlist=160, sleep=0)
        _meta, rows = scanner.scan_market(cfg)
        after_scan = calls["n"]

        prev_units = None
        for capital in (50_000_000, 200_000_000, 2_000_000_000):
            picks = scanner.rank_for(rows, capital, floor=1_000_000, top=50)

            if any(p["buy_price"] < 1_000_000 for p in picks):
                failures.append(f"capital={capital}: floor not applied")

            scores = [p["merch_score"] for p in picks]
            if scores != sorted(scores, reverse=True):
                failures.append(f"capital={capital}: not sorted by merch_score")

            units = sum(p["units_24h"] for p in picks)
            if prev_units is not None and units < prev_units:
                failures.append(
                    f"capital={capital}: total units_24h fell as capital rose")
            prev_units = units
            print(f"  ok  capital={capital:,}: {len(picks)} picks, "
                  f"{units:,} total units/24h")

        if calls["n"] != after_scan:
            failures.append(
                f"rank_for made {calls['n'] - after_scan} API calls — readers "
                f"must make zero")
        else:
            print(f"  ok  reads made 0 API calls ({after_scan} during the scan)")
    finally:
        scanner.get = inner
        time.time = real_time
    return failures


def _fake_history_fn(world):
    """A history_fn (see scanner.scan_market_fast) backed by fake_wiki's
    /timeseries fixture instead of a real item_daily_history table — lets the
    fast path be parity-tested offline, with no real backfill involved."""
    _, _, _, series = world

    def history_fn(item_ids):
        out = {}
        base_day = date(2024, 1, 1)
        for iid in item_ids:
            pts = series.get(iid, [])
            prices, vols = scanner._parse_timeseries(pts)
            if prices:
                out[iid] = [(base_day + timedelta(days=i), p, v)
                            for i, (p, v) in enumerate(zip(prices, vols))]
        return out

    return history_fn


def run_fast_parity():
    """scan_market_fast, fed history from fake_wiki's /timeseries data via a
    DB-shaped history_fn, must score every item identically to scan_market's
    live-per-item enrich() path — same derive_trend_metrics() call either
    way. shortlist is set above N_ITEMS so scan_market's shortlist cut can't
    hide a mismatch: both paths must cover exactly the same candidate set."""
    real_time = time.time
    world = fake_wiki.build_world()
    fake_wiki.install(scanner, world)

    failures = []
    try:
        for capital, floor in PAIRS:
            cfg = scanner.ScanConfig(
                reference_bankroll=capital, global_floor=floor,
                shortlist=fake_wiki.N_ITEMS + 1, sleep=0,
                min_deploy_frac=0.01, max_spread_pct=25.0, stale_hours=12.0)

            _meta_slow, slow_rows = scanner.scan_market(cfg)
            _meta_fast, fast_rows = scanner.scan_market_fast(cfg, _fake_history_fn(world))

            label = f"capital={capital:,} floor={floor:,}"
            slow_by_id = {r["id"]: r for r in slow_rows}
            fast_by_id = {r["id"]: r for r in fast_rows}

            if set(slow_by_id) != set(fast_by_id):
                only_slow = set(slow_by_id) - set(fast_by_id)
                only_fast = set(fast_by_id) - set(slow_by_id)
                failures.append(
                    f"{label}: item set differs — only in slow: {only_slow}, "
                    f"only in fast: {only_fast}")
                continue

            for iid, slow in slow_by_id.items():
                fast = fast_by_id[iid]
                for k in scanner.MARKET_KEYS:
                    if slow.get(k) != fast.get(k):
                        failures.append(
                            f"{label}: item {slow['name']!r} field {k!r}: "
                            f"slow {slow.get(k)!r} != fast {fast.get(k)!r}")
            print(f"  ok  {label}: {len(slow_rows)} items identical "
                  "(slow live-fetch vs fast DB-fed)")
    finally:
        time.time = real_time
    return failures


if __name__ == "__main__":
    print("parity: legacy run_scan vs scan_market + rank_for")
    bad = run_parity()
    print("\nread-time personalisation from one snapshot")
    bad += run_read_time_personalisation()
    print("\nscan_market_fast (DB-fed) vs scan_market (live-fetch)")
    bad += run_fast_parity()

    if bad:
        print(f"\nFAILED ({len(bad)} problems)")
        for f in bad[:20]:
            print("  -", f)
        sys.exit(1)
    print("\nPASS")
