#!/usr/bin/env python3
"""predict_cli.py — dip/rise prediction table over the clan app's snapshot.

Reads the most recent scan already stored by the writer (worker.py / app.py)
via db.py, the same way GET /api/picks does. Makes ZERO wiki calls itself —
if there is no snapshot yet, run the app once first (`python app.py`) so the
writer can populate one.

    python predict_cli.py                       # top 30 by dip confidence
    python predict_cli.py --mode flip --top 15
    python predict_cli.py --mode risk
    python predict_cli.py --capital 1.5b --floor 1m
"""

import argparse
import sys

import analysis
import db
import scanner


def _print_table(rows, mode):
    hdr = (f"{'item':<28}{'high':>10}{'low':>10}{'trend':>18}"
           f"{'roi%':>8}{'conf':>6}{'risk':>7}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        score = r["flip_score"] if mode == "flip" else r["dip_confidence"]
        print(f"{r['name'][:27]:<28}{scanner.fmt_gp(r['sell_price']):>10}"
              f"{scanner.fmt_gp(r['buy_price']):>10}"
              f"{r['predicted_trend']:>18}{r['est_roi_pct']:>7.1f}%"
              f"{score:>6.0f}{r['risk_level']:>7}")
    print(f"\n{len(rows)} items. roi% is net of the 2% GE tax (capped 5m/item).")
    print("trend/conf/risk are deterministic rule-based scores — see analysis.py, "
          "not a trained model.")
    if any(r["risk_flags"] for r in rows):
        print("\nrisk flags:")
        for r in rows:
            if r["risk_flags"]:
                print(f"  {r['name']}: {', '.join(r['risk_flags'])}")


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic dip/rise prediction over the latest "
                     "stored GE snapshot (no wiki calls).")
    ap.add_argument("--capital", type=scanner.parse_gp, default=None,
                    help="liquid capital, e.g. 700m, 1.5b (default: server default)")
    ap.add_argument("--floor", type=scanner.parse_gp, default=None,
                    help="minimum item price, e.g. 500k (default: server default)")
    ap.add_argument("--top", type=int, default=30, help="rows to print")
    ap.add_argument("--mode", default="dip", choices=["dip", "flip", "risk"],
                    help="dip = sort by dip confidence; flip = sort by flip "
                         "score; risk = flagged items only, worst first")
    args = ap.parse_args()

    import config
    capital = args.capital if args.capital is not None else config.DEFAULT_CAPITAL
    floor = args.floor if args.floor is not None else config.DEFAULT_FLOOR

    db.init_db()
    scan = db.latest_ok_scan()
    if not scan:
        print("no snapshot yet — run `python app.py` (or worker.run_and_store) "
              "at least once first.", file=sys.stderr)
        sys.exit(1)

    market_rows = db.read_picks(scan["id"])
    picks = scanner.rank_for(market_rows, capital, floor,
                             top=max(args.top * 4, 200), mode="any")
    rows = analysis.analyze(picks)

    if args.mode == "flip":
        rows.sort(key=lambda r: r["flip_score"], reverse=True)
    elif args.mode == "risk":
        rows = [r for r in rows if r["risk_flags"]]
        rows.sort(key=lambda r: (len(r["risk_flags"]), r["dip_confidence"]),
                  reverse=True)
    else:
        rows.sort(key=lambda r: r["dip_confidence"], reverse=True)

    rows = rows[:args.top]
    if not rows:
        print("no items matched." if args.mode != "risk"
              else "no items currently carry a risk flag.")
        return

    _print_table(rows, args.mode)


if __name__ == "__main__":
    main()
