#!/usr/bin/env python3
"""Detailed evidence analysis showing concrete examples of Kalshi lagging spot price.

This provides trade-by-trade breakdowns with timestamps to prove the correlation.
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


def analyze_specific_market(db_path: Path, ticker: str):
    """Deep dive into a specific market showing the lag in action."""

    conn = sqlite3.connect(str(db_path))

    # Get settlement info
    settlement = conn.execute("""
        SELECT floor_strike, settled_yes, expiration_value, kalshi_last_mid, close_time
        FROM market_settlements
        WHERE ticker = ?
    """, (ticker,)).fetchone()

    if not settlement:
        print(f"No settlement found for {ticker}")
        return

    strike, settled_yes, expiry_val, final_kalshi, close_time = settlement

    # Get price evolution over time
    kalshi_evolution = conn.execute("""
        SELECT ts, yes_bid, yes_ask, yes_mid, seconds_to_close
        FROM kalshi_snapshots
        WHERE ticker = ?
        ORDER BY ts
    """, (ticker,)).fetchall()

    # Get corresponding Kraken prices
    price_data = []
    for k_ts, bid, ask, mid, ttl in kalshi_evolution:
        # Find nearest Kraken snapshot
        kraken = conn.execute("""
            SELECT avg_60s, spot_price
            FROM kraken_snapshots
            WHERE ABS(ts - ?) < 1.0
            ORDER BY ABS(ts - ?)
            LIMIT 1
        """, (k_ts, k_ts)).fetchone()

        if kraken and mid is not None:
            kraken_avg, kraken_spot = kraken
            price_data.append({
                'ts': k_ts,
                'ttl': ttl,
                'kalshi_bid': bid,
                'kalshi_ask': ask,
                'kalshi_mid': mid,
                'kraken_avg': kraken_avg,
                'kraken_spot': kraken_spot,
                'btc_above_strike': kraken_avg > strike,
            })

    conn.close()

    if not price_data:
        print(f"No price data found for {ticker}")
        return

    # Print detailed breakdown
    print("\n" + "=" * 100)
    print(f"DETAILED ANALYSIS: {ticker}")
    print("=" * 100)

    print(f"\nMarket Details:")
    print(f"  Strike: ${strike:,.2f}")
    print(f"  Settlement: {'YES' if settled_yes else 'NO'} (BTC settled at ${expiry_val:,.2f})")
    print(f"  Final Kalshi price: {final_kalshi:.1f}¢")
    print(f"  Close time: {close_time}")
    print(f"  Total snapshots: {len(price_data)}")

    # Show key moments
    print(f"\n{'Timestamp':12s} {'TTL':>8s} {'BTC Spot':>10s} {'BTC Avg':>10s} {'Kalshi':>8s} {'Spread':>8s} {'BTC>Strike':>11s} {'Fair Price':>11s} {'Mispricing':>11s}")
    print("-" * 100)

    # Sample evenly across the timeline
    sample_indices = [0] + [int(i * len(price_data) / 10) for i in range(1, 10)] + [len(price_data) - 1]

    for idx in sample_indices:
        if idx >= len(price_data):
            continue

        p = price_data[idx]
        ts_str = datetime.fromtimestamp(p['ts']).strftime('%H:%M:%S')
        ttl_str = f"{p['ttl']:.0f}s" if p['ttl'] else "N/A"

        # Fair price = 100 if BTC > strike, 0 if BTC < strike
        fair_price = 100 if p['btc_above_strike'] else 0
        mispricing = abs(p['kalshi_mid'] - fair_price)

        spread = p['kalshi_ask'] - p['kalshi_bid'] if p['kalshi_ask'] and p['kalshi_bid'] else 0

        above_str = "YES" if p['btc_above_strike'] else "NO"

        print(f"{ts_str:12s} {ttl_str:>8s} ${p['kraken_spot']:>9.2f} ${p['kraken_avg']:>9.2f} {p['kalshi_mid']:>7.1f}¢ {spread:>7.0f}¢ {above_str:>11s} {fair_price:>10d}¢ {mispricing:>10.0f}¢")

    # Calculate lag metrics
    print("\n" + "-" * 100)
    print("LAG ANALYSIS")
    print("-" * 100)

    # Find moments where BTC crossed the strike
    crossings = []
    for i in range(1, len(price_data)):
        prev = price_data[i-1]
        curr = price_data[i]

        # BTC crossed from below to above strike
        if not prev['btc_above_strike'] and curr['btc_above_strike']:
            crossings.append({
                'type': 'UPWARD',
                'ts': curr['ts'],
                'ttl': curr['ttl'],
                'btc_price': curr['kraken_avg'],
                'kalshi_before': prev['kalshi_mid'],
                'kalshi_after': curr['kalshi_mid'],
                'immediate_lag': curr['kalshi_mid'],  # Should be ~100 but isn't
            })
        # BTC crossed from above to below strike
        elif prev['btc_above_strike'] and not curr['btc_above_strike']:
            crossings.append({
                'type': 'DOWNWARD',
                'ts': curr['ts'],
                'ttl': curr['ttl'],
                'btc_price': curr['kraken_avg'],
                'kalshi_before': prev['kalshi_mid'],
                'kalshi_after': curr['kalshi_mid'],
                'immediate_lag': curr['kalshi_mid'],  # Should be ~0 but isn't
            })

    if crossings:
        print(f"\nDetected {len(crossings)} strike crossings:")
        for c in crossings:
            ts_str = datetime.fromtimestamp(c['ts']).strftime('%H:%M:%S')
            expected = 100 if c['type'] == 'UPWARD' else 0
            lag = abs(c['immediate_lag'] - expected)
            print(f"  {ts_str} ({c['ttl']:.0f}s TTL): BTC {c['type']:8s} ${c['btc_price']:.2f} → Kalshi {c['kalshi_before']:.0f}¢ → {c['kalshi_after']:.0f}¢ (lag: {lag:.0f}¢ from fair)")
    else:
        print("\nNo strike crossings detected (BTC stayed on one side)")

    # Calculate how Kalshi converged to fair value
    last_30_points = price_data[-30:] if len(price_data) >= 30 else price_data

    print(f"\nFinal 30 seconds convergence:")
    mispricings = []
    for p in last_30_points:
        fair = 100 if p['btc_above_strike'] else 0
        mispricings.append(abs(p['kalshi_mid'] - fair))

    print(f"  Initial mispricing (30s before): {mispricings[0]:.1f}¢")
    print(f"  Final mispricing (at expiry): {mispricings[-1]:.1f}¢")
    print(f"  Convergence: {mispricings[0] - mispricings[-1]:.1f}¢")

    # Show profit opportunity
    print("\n" + "-" * 100)
    print("PROFIT OPPORTUNITY")
    print("-" * 100)

    # Strategy: Bet with spot at 60-120s before expiry
    entry_window = [p for p in price_data if 60 <= p['ttl'] <= 120]

    if entry_window:
        # Take midpoint of window
        entry_point = entry_window[len(entry_window) // 2]

        ts_str = datetime.fromtimestamp(entry_point['ts']).strftime('%H:%M:%S')
        btc_above = entry_point['btc_above_strike']

        # Entry cost (buy YES if BTC above, buy NO if below)
        if btc_above:
            entry_cost = entry_point['kalshi_ask'] if entry_point['kalshi_ask'] else entry_point['kalshi_mid'] + 2.5
            side = "BUY YES"
        else:
            entry_cost = entry_point['kalshi_bid'] if entry_point['kalshi_bid'] else entry_point['kalshi_mid'] - 2.5
            side = "BUY NO"

        # Payout
        payout = 100 if (btc_above and settled_yes) or (not btc_above and not settled_yes) else 0

        profit = payout - entry_cost

        print(f"\nEntry at 60-120s window:")
        print(f"  Time: {ts_str} (TTL: {entry_point['ttl']:.0f}s)")
        print(f"  BTC: ${entry_point['kraken_avg']:.2f} vs Strike ${strike:.2f}")
        print(f"  BTC position: {'ABOVE' if btc_above else 'BELOW'} strike")
        print(f"  Action: {side}")
        print(f"  Entry cost: {entry_cost:.1f}¢")
        print(f"  Settlement: {'YES' if settled_yes else 'NO'}")
        print(f"  Payout: {payout}¢")
        print(f"  Profit: {profit:+.1f}¢")

        if profit > 0:
            print(f"\n  ✅ PROFITABLE TRADE (+{profit:.1f}¢)")
        else:
            print(f"\n  ❌ LOSING TRADE ({profit:.1f}¢)")
    else:
        print("\nNo data in 60-120s window")

    return price_data


def show_profitable_trades_breakdown(db_path: Path):
    """Show concrete examples of profitable trades."""

    conn = sqlite3.connect(str(db_path))

    # Get all settlements
    settlements = conn.execute("""
        SELECT ticker, floor_strike, settled_yes, expiration_value, kalshi_last_mid
        FROM market_settlements
        WHERE floor_strike IS NOT NULL
        ORDER BY close_time
    """).fetchall()

    conn.close()

    print("\n" + "=" * 100)
    print("PROFITABLE TRADES BREAKDOWN (60-120s Entry Strategy)")
    print("=" * 100)

    trades = []

    for ticker, strike, settled_yes, expiry_val, final_kalshi in settlements:
        # Analyze this market
        conn = sqlite3.connect(str(db_path))

        # Get snapshot at 60-120s before expiry
        entry_snapshot = conn.execute("""
            SELECT yes_bid, yes_ask, yes_mid, seconds_to_close, ts
            FROM kalshi_snapshots
            WHERE ticker = ?
              AND seconds_to_close BETWEEN 60 AND 120
              AND yes_mid IS NOT NULL
            ORDER BY ABS(seconds_to_close - 90)
            LIMIT 1
        """, (ticker,)).fetchone()

        if not entry_snapshot:
            conn.close()
            continue

        bid, ask, mid, ttl, snapshot_ts = entry_snapshot

        # Get corresponding Kraken price
        kraken = conn.execute("""
            SELECT avg_60s
            FROM kraken_snapshots
            WHERE ABS(ts - ?) < 1.0
            ORDER BY ABS(ts - ?)
            LIMIT 1
        """, (snapshot_ts, snapshot_ts)).fetchone()

        conn.close()

        if not kraken or kraken[0] is None or kraken[0] == 0:
            continue

        kraken_avg = kraken[0]
        btc_above_strike = kraken_avg > strike

        # Entry decision based on spot
        if btc_above_strike:
            side = "YES"
            entry_cost = ask if ask else mid + 2.5
        else:
            side = "NO"
            entry_cost = bid if bid else mid - 2.5

        # Outcome
        correct = (btc_above_strike and settled_yes) or (not btc_above_strike and not settled_yes)
        payout = 100 if correct else 0
        profit = payout - entry_cost

        trades.append({
            'ticker': ticker,
            'strike': strike,
            'btc_price': kraken_avg,
            'btc_above': btc_above_strike,
            'side': side,
            'entry_cost': entry_cost,
            'kalshi_mid': mid,
            'settled_yes': settled_yes,
            'correct': correct,
            'payout': payout,
            'profit': profit,
            'ttl': ttl,
        })

    # Sort by profit
    trades.sort(key=lambda x: x['profit'], reverse=True)

    print(f"\n{'Ticker':30s} {'Strike':>10s} {'BTC':>10s} {'Side':>5s} {'Entry':>7s} {'Result':>7s} {'Profit':>8s}")
    print("-" * 100)

    for t in trades:
        strike_str = f"${t['strike']:,.0f}"
        btc_str = f"${t['btc_price']:,.0f}"
        entry_str = f"{t['entry_cost']:.1f}¢"
        result_str = "WIN" if t['correct'] else "LOSS"
        profit_str = f"{t['profit']:+.1f}¢"

        print(f"{t['ticker']:30s} {strike_str:>10s} {btc_str:>10s} {t['side']:>5s} {entry_str:>7s} {result_str:>7s} {profit_str:>8s}")

    # Statistics
    total_trades = len(trades)
    wins = sum(1 for t in trades if t['profit'] > 0)
    win_rate = wins / total_trades if total_trades > 0 else 0
    avg_profit = np.mean([t['profit'] for t in trades]) if trades else 0
    total_profit = sum(t['profit'] for t in trades)

    print("\n" + "-" * 100)
    print(f"Total trades: {total_trades}")
    print(f"Wins: {wins} ({win_rate:.1%})")
    print(f"Losses: {total_trades - wins} ({1-win_rate:.1%})")
    print(f"Average profit: {avg_profit:+.1f}¢")
    print(f"Total profit: {total_profit:+.1f}¢ (${total_profit/100:+.2f})")

    # Best and worst trades
    if trades:
        print(f"\nBest trade: {trades[0]['ticker']}")
        print(f"  {trades[0]['side']} at {trades[0]['entry_cost']:.1f}¢ → {trades[0]['profit']:+.1f}¢ profit")

        print(f"\nWorst trade: {trades[-1]['ticker']}")
        print(f"  {trades[-1]['side']} at {trades[-1]['entry_cost']:.1f}¢ → {trades[-1]['profit']:+.1f}¢ profit")


def main():
    db_path = Path('data/btc_probe_20260227.db')

    if not db_path.exists():
        print(f"Error: {db_path} not found")
        return 1

    # Show overall profitable trades
    show_profitable_trades_breakdown(db_path)

    # Deep dive into specific markets
    print("\n\n")
    print("=" * 100)
    print("DETAILED MARKET EXAMPLES")
    print("=" * 100)

    # Get some interesting markets
    conn = sqlite3.connect(str(db_path))

    # Best mispricing (market priced low but settled YES)
    best_mispricing = conn.execute("""
        SELECT ticker
        FROM market_settlements
        WHERE settled_yes = 1
          AND kalshi_last_mid < 50
        ORDER BY kalshi_last_mid
        LIMIT 1
    """).fetchone()

    # Worst mispricing (market priced high but settled NO)
    worst_mispricing = conn.execute("""
        SELECT ticker
        FROM market_settlements
        WHERE settled_yes = 0
          AND kalshi_last_mid > 50
        ORDER BY kalshi_last_mid DESC
        LIMIT 1
    """).fetchone()

    # High confidence success (market priced >80 and settled YES)
    high_confidence = conn.execute("""
        SELECT ticker
        FROM market_settlements
        WHERE settled_yes = 1
          AND kalshi_last_mid > 80
        LIMIT 1
    """).fetchone()

    conn.close()

    if best_mispricing:
        print("\n\nEXAMPLE 1: Massive Underpricing (Kalshi said NO, BTC said YES)")
        analyze_specific_market(db_path, best_mispricing[0])

    if worst_mispricing:
        print("\n\nEXAMPLE 2: Overpricing (Kalshi said YES, BTC said NO)")
        analyze_specific_market(db_path, worst_mispricing[0])

    if high_confidence:
        print("\n\nEXAMPLE 3: High Confidence Success (Kalshi >80¢ → YES)")
        analyze_specific_market(db_path, high_confidence[0])


if __name__ == '__main__':
    sys.exit(main())
