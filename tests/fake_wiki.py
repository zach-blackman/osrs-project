"""A deterministic, offline stand-in for the wiki Real-time Prices API.

Lets the parity test run the whole pipeline — both the legacy monolith and
the new scanner.py split — over byte-identical input with zero network
calls, which is the only way to compare two ~160-item scans meaningfully.
"""

import random

N_ITEMS = 90

# A fixed "now" keeps the staleness filter and any time-derived branch
# deterministic across runs.
NOW = 1_760_000_000.0


def build_world(seed=7):
    """Generate a plausible GE: mapping, /latest, /1h and per-id /timeseries."""
    rng = random.Random(seed)

    mapping, latest, hourly, series = [], {}, {}, {}

    for i in range(N_ITEMS):
        iid = 1000 + i
        # A wide price spread so different floors actually partition the set.
        low = int(rng.choice([50_000, 120_000, 400_000, 900_000,
                              3_000_000, 20_000_000]) * rng.uniform(0.7, 1.4))
        spread = rng.uniform(1.02, 1.20)
        high = int(low * spread)
        limit = rng.choice([8, 30, 100, 1000, 10_000])

        mapping.append({
            "id": iid,
            "name": f"Test item {i} {rng.choice(['blade', 'tome', 'sigil'])}",
            "limit": limit,
            "members": True,
            "highalch": int(low * rng.uniform(1.05, 1.35)),
            "value": int(low * 0.6),
        })
        latest[str(iid)] = {
            "high": high,
            "low": low,
            "highTime": NOW - rng.uniform(0, 3600),
            "lowTime": NOW - rng.uniform(0, 3600),
        }
        hourly[str(iid)] = {
            "highPriceVolume": rng.randint(0, 5000),
            "lowPriceVolume": rng.randint(0, 5000),
        }

        # A year of daily candles, random-walked around `low`. A few items
        # get a short history so the <45-point skip path is exercised.
        n_days = rng.choice([12, 60, 200, 365])
        px = float(low)
        pts = []
        for _ in range(n_days):
            px *= rng.uniform(0.97, 1.03)
            pts.append({
                "avgHighPrice": int(px * 1.01),
                "avgLowPrice": int(px * 0.99),
                "highPriceVolume": rng.randint(0, 2000),
                "lowPriceVolume": rng.randint(0, 2000),
            })
        series[iid] = pts

    return mapping, latest, hourly, series


NEWS = [
    # `when=None` on purpose: catalyst_for then uses a fixed 45-day age, so
    # the bonus does not drift with the wall clock.
    {"title": "Test item 3 blade rebalanced in this week's update",
     "url": "https://example.invalid/1", "when": None},
    {"title": "Changes to sigil drop rates", "url": "https://example.invalid/2",
     "when": None},
]


def install(module, world):
    """Point `module`'s API layer at the fake world. Works on both the legacy
    monolith and scanner.py, since both expose `get` and `fetch_news`."""
    mapping, latest, hourly, series = world

    def fake_get(path, retries=3, **params):
        if path == "/mapping":
            return mapping
        if path == "/latest":
            return {"data": latest}
        if path == "/1h":
            return {"data": hourly}
        if path == "/timeseries":
            return {"data": series.get(int(params["id"]), [])}
        raise AssertionError(f"unexpected API path: {path}")

    module.get = fake_get
    module.fetch_news = lambda limit=40: list(NEWS)

    # Freeze the clock the prefilter staleness test reads.
    module.time.time = lambda: NOW
