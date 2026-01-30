#!/usr/bin/env python3
"""
Kalshi Spread Backtester

Backtest arbitrage opportunities on historical Kalshi candlestick data.

Usage:
    python scripts/backtest_kalshi_spread.py TICKER1 TICKER2
    python scripts/backtest_kalshi_spread.py KXNBAGAME-26JAN21TORSAC-TOR KXNBAGAME-26JAN21TORSAC-SAC
    python scripts/backtest_kalshi_spread.py TICKER1 TICKER2 --lookback 24
    python scripts/backtest_kalshi_spread.py TICKER1 TICKER2 --interval 60  # 60-minute candles

Examples:
    # Backtest a recent NBA game
    python scripts/backtest_kalshi_spread.py KXNBAGAME-26JAN21TORSAC-TOR KXNBAGAME-26JAN21TORSAC-SAC

    # Backtest with 24 hours of data
    python scripts/backtest_kalshi_spread.py TICKER1 TICKER2 --lookback 24
"""

import sys
import os
import argparse
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Kalshi Spread Backtester")
    parser.add_argument("ticker1", help="First market ticker (e.g., KXNBAGAME-26JAN21TORSAC-TOR)")
    parser.add_argument("ticker2", help="Second market ticker (e.g., KXNBAGAME-26JAN21TORSAC-SAC)")
    parser.add_argument("--lookback", type=float, default=12.0, help="Hours of history (default: 12)")
    parser.add_argument("--interval", type=int, default=1, choices=[1, 60, 1440],
                       help="Candle interval in minutes (1, 60, or 1440)")
    parser.add_argument("--contracts", type=int, default=100, help="Contract size for calculations")
    parser.add_argument("--arb-floor", type=float, default=0.002, help="Min arb PnL to report ($/contract)")
    parser.add_argument("--dutch-floor", type=float, default=0.002, help="Min dutch profit to report")
    parser.add_argument("--no-plot", action="store_true", help="Skip matplotlib plots")

    args = parser.parse_args()

    print_header("KALSHI SPREAD BACKTESTER")
    print(f"Ticker 1: {args.ticker1}")
    print(f"Ticker 2: {args.ticker2}")
    print(f"Lookback: {args.lookback} hours")
    print(f"Interval: {args.interval} minute candles")
    print(f"Contract size: {args.contracts}")

    # Import backtester
    from arb.backtest import (
        get_series_ticker_for_market,
        get_candles,
        iso_to_ts,
        all_in_buy_cost,
        all_in_sell_proceeds,
        fee_per_contract,
        _f,
    )
    from datetime import timezone
    import math

    KALSHI_HOST = "https://api.elections.kalshi.com"

    print("\nFetching market info...")

    try:
        series_1, m_1, e_1 = get_series_ticker_for_market(KALSHI_HOST, args.ticker1)
        series_2, m_2, e_2 = get_series_ticker_for_market(KALSHI_HOST, args.ticker2)
    except Exception as e:
        print(f"Error fetching market info: {e}")
        print("\nMake sure the tickers are valid. Example tickers:")
        print("  KXNBAGAME-26JAN21TORSAC-TOR")
        print("  KXNBAGAME-26JAN21TORSAC-SAC")
        return

    if m_1["event_ticker"] != m_2["event_ticker"]:
        print(f"Warning: Different events: {m_1['event_ticker']} vs {m_2['event_ticker']}")

    mutually_exclusive = bool(e_1.get("mutually_exclusive", False))
    status = m_1.get("status", "unknown")

    print(f"\nEvent: {m_1['event_ticker']}")
    print(f"Status: {status}")
    print(f"Mutually exclusive: {mutually_exclusive}")
    print(f"Market 1: {m_1.get('title', 'N/A')}")
    print(f"Market 2: {m_2.get('title', 'N/A')}")

    # Determine time window
    now_ts = int(datetime.now(timezone.utc).timestamp())
    open_ts = iso_to_ts(m_1["open_time"])
    close_ts = iso_to_ts(m_1["close_time"])

    if status in ("closed", "settled"):
        start_ts = open_ts
        end_ts = close_ts
        print(f"\nUsing full market lifetime (closed/settled)")
    else:
        start_ts = max(open_ts, now_ts - int(args.lookback * 3600))
        end_ts = now_ts
        print(f"\nUsing last {args.lookback} hours")

    print(f"Time range: {datetime.fromtimestamp(start_ts, tz=timezone.utc)} to {datetime.fromtimestamp(end_ts, tz=timezone.utc)}")

    # Fetch candles
    print("\nFetching candlestick data...")
    try:
        c_1 = get_candles(KALSHI_HOST, series_1, args.ticker1, start_ts, end_ts, period_interval=args.interval)
        c_2 = get_candles(KALSHI_HOST, series_2, args.ticker2, start_ts, end_ts, period_interval=args.interval)
    except Exception as e:
        print(f"Error fetching candles: {e}")
        return

    print(f"Got {len(c_1)} candles for ticker 1, {len(c_2)} candles for ticker 2")

    # Align by timestamp
    by_ts_1 = {c["end_period_ts"]: c for c in c_1}
    by_ts_2 = {c["end_period_ts"]: c for c in c_2}
    ts_set = sorted(set(by_ts_1.keys()) & set(by_ts_2.keys()))

    if not ts_set:
        print("No overlapping candles found!")
        return

    print(f"Aligned {len(ts_set)} candles")

    # Calculate metrics
    rows = []
    C = args.contracts

    for t in ts_set:
        u = by_ts_1[t]
        s = by_ts_2[t]

        # Use close prices
        m1_yes_bid = _f(u["yes_bid"].get("close_dollars"))
        m1_yes_ask = _f(u["yes_ask"].get("close_dollars"))
        m2_yes_bid = _f(s["yes_bid"].get("close_dollars"))
        m2_yes_ask = _f(s["yes_ask"].get("close_dollars"))

        if None in (m1_yes_bid, m1_yes_ask, m2_yes_bid, m2_yes_ask):
            continue

        # Derive NO prices
        m1_no_ask = 1.0 - m1_yes_bid
        m1_no_bid = 1.0 - m1_yes_ask
        m2_no_ask = 1.0 - m2_yes_bid
        m2_no_bid = 1.0 - m2_yes_ask

        # Fee-adjusted costs
        buy_m1_yes = all_in_buy_cost(m1_yes_ask, C, maker=False)
        buy_m1_no = all_in_buy_cost(m1_no_ask, C, maker=False)
        buy_m2_yes = all_in_buy_cost(m2_yes_ask, C, maker=False)
        buy_m2_no = all_in_buy_cost(m2_no_ask, C, maker=False)

        sell_m1_yes = all_in_sell_proceeds(m1_yes_bid, C, maker=False)
        sell_m1_no = all_in_sell_proceeds(m1_no_bid, C, maker=False)
        sell_m2_yes = all_in_sell_proceeds(m2_yes_bid, C, maker=False)
        sell_m2_no = all_in_sell_proceeds(m2_no_bid, C, maker=False)

        # Routing edges
        edge_m1 = buy_m1_yes - buy_m2_no
        edge_m2 = buy_m2_yes - buy_m1_no

        # Arb PnL
        arb_m1 = max(sell_m1_yes, sell_m2_no) - min(buy_m1_yes, buy_m2_no)
        arb_m2 = max(sell_m2_yes, sell_m1_no) - min(buy_m2_yes, buy_m1_no)

        # Dutch book
        dutch = (1.0 - (min(buy_m1_yes, buy_m2_no) + min(buy_m2_yes, buy_m1_no))) if mutually_exclusive else float("nan")

        # Best action
        candidates = []
        if arb_m1 >= args.arb_floor:
            candidates.append(("ARB_M1", arb_m1))
        if arb_m2 >= args.arb_floor:
            candidates.append(("ARB_M2", arb_m2))
        if mutually_exclusive and dutch >= args.dutch_floor:
            candidates.append(("DUTCH", dutch))

        best_kind, best_val = max(candidates, key=lambda kv: kv[1]) if candidates else ("NO_TRADE", 0.0)

        rows.append({
            "ts": t,
            "edge_m1": edge_m1,
            "edge_m2": edge_m2,
            "arb_m1": arb_m1,
            "arb_m2": arb_m2,
            "dutch": dutch,
            "best_kind": best_kind,
            "best_val": best_val,
            "m1_yes_bid": m1_yes_bid,
            "m1_yes_ask": m1_yes_ask,
            "m2_yes_bid": m2_yes_bid,
            "m2_yes_ask": m2_yes_ask,
        })

    if not rows:
        print("No valid data points after processing!")
        return

    # Results summary
    print_header("BACKTEST RESULTS")

    def fmt_ts(t):
        return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"Time range: {fmt_ts(rows[0]['ts'])} -> {fmt_ts(rows[-1]['ts'])}")
    print(f"Data points: {len(rows)}")

    best_arb_m1 = max(r["arb_m1"] for r in rows)
    best_arb_m2 = max(r["arb_m2"] for r in rows)
    best_dutch = max((r["dutch"] for r in rows if not math.isnan(r["dutch"])), default=float("nan"))

    print(f"\nMax ARB (Market 1 exposure): ${best_arb_m1:.4f}/contract")
    print(f"Max ARB (Market 2 exposure): ${best_arb_m2:.4f}/contract")
    if not math.isnan(best_dutch):
        print(f"Max Dutch book profit: ${best_dutch:.4f}/contract")

    # Count opportunities
    arb_count = sum(1 for r in rows if r["best_kind"].startswith("ARB"))
    dutch_count = sum(1 for r in rows if r["best_kind"] == "DUTCH")
    no_trade_count = sum(1 for r in rows if r["best_kind"] == "NO_TRADE")

    print(f"\nOpportunity breakdown:")
    print(f"  ARB opportunities: {arb_count} ({100*arb_count/len(rows):.1f}%)")
    print(f"  Dutch opportunities: {dutch_count} ({100*dutch_count/len(rows):.1f}%)")
    print(f"  No trade: {no_trade_count} ({100*no_trade_count/len(rows):.1f}%)")

    # Streak analysis
    streaks = []
    cur = rows[0]["best_kind"]
    start_i = 0
    for i in range(1, len(rows)):
        if rows[i]["best_kind"] != cur:
            streaks.append((cur, start_i, i-1))
            cur = rows[i]["best_kind"]
            start_i = i
    streaks.append((cur, start_i, len(rows)-1))

    trade_streaks = []
    for kind, a, b in streaks:
        if kind != "NO_TRADE":
            dur_s = rows[b]["ts"] - rows[a]["ts"]
            peak = max(rows[i]["best_val"] for i in range(a, b+1))
            trade_streaks.append((dur_s, peak, kind, a, b))
    trade_streaks.sort(reverse=True)

    if trade_streaks:
        print(f"\nLongest opportunity streaks:")
        for dur_s, peak, kind, a, b in trade_streaks[:5]:
            print(f"  {kind:12s} | {dur_s/60:.1f} min | peak ${peak:.4f} | {fmt_ts(rows[a]['ts'])}")

    # Plot if requested
    if not args.no_plot:
        try:
            import matplotlib.pyplot as plt

            xs = [(r["ts"] - rows[0]["ts"]) / 60.0 for r in rows]

            fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

            # Top: PnL signals
            axes[0].plot(xs, [r["arb_m1"] for r in rows], label="ARB (M1 exposure)")
            axes[0].plot(xs, [r["arb_m2"] for r in rows], label="ARB (M2 exposure)")
            axes[0].plot(xs, [r["dutch"] for r in rows], label="Dutch book")
            axes[0].axhline(0.0, color='gray', linestyle='-', alpha=0.3)
            axes[0].axhline(args.arb_floor, color='green', linestyle='--', alpha=0.3)
            axes[0].set_ylabel("$/contract (fees included)")
            axes[0].set_title(f"Backtest: {args.ticker1} vs {args.ticker2}")
            axes[0].legend()

            # Bottom: Prices
            axes[1].plot(xs, [r["m1_yes_ask"] for r in rows], label="M1 YES ask", alpha=0.7)
            axes[1].plot(xs, [r["m2_yes_ask"] for r in rows], label="M2 YES ask", alpha=0.7)
            axes[1].plot(xs, [r["m1_yes_ask"] + r["m2_yes_ask"] for r in rows],
                        label="Combined ask", linestyle='--', color='red')
            axes[1].axhline(1.0, color='gray', linestyle='-', alpha=0.5)
            axes[1].set_xlabel("Minutes since start")
            axes[1].set_ylabel("Price ($)")
            axes[1].legend()

            plt.tight_layout()
            plt.show()
        except ImportError:
            print("\nMatplotlib not available, skipping plots")
        except Exception as e:
            print(f"\nCould not display plots: {e}")

    print(f"\nBacktest completed at {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
