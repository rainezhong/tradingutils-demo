#!/usr/bin/env python3
"""Comprehensive backtest of latency arbitrage strategy on historical settlements.

Strategy:
1. Entry window: 60-120s before market expiry
2. Check BTC spot price vs strike
3. If BTC > strike and Kalshi < 70¢, buy YES
4. If BTC < strike and Kalshi > 30¢, buy NO
5. Wait for settlement

This tests the actual strategy with realistic entry criteria.
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


def backtest_strategy(
    db_path: Path,
    min_edge_cents: float = 10.0,
    entry_window_min: int = 60,
    entry_window_max: int = 120,
):
    """Run backtest on all historical settlements."""

    conn = sqlite3.connect(str(db_path))

    # Get all settlements
    settlements = conn.execute("""
        SELECT ticker, floor_strike, settled_yes, expiration_value,
               kalshi_last_mid, close_time
        FROM market_settlements
        WHERE floor_strike IS NOT NULL
        ORDER BY close_time
    """).fetchall()

    print("=" * 120)
    print("LATENCY ARBITRAGE STRATEGY BACKTEST")
    print("=" * 120)
    print()
    print(f"Database: {db_path}")
    print(f"Entry window: {entry_window_min}-{entry_window_max}s before expiry")
    print(f"Minimum edge: {min_edge_cents}¢")
    print()
    print("Strategy Rules:")
    print("  1. Get BTC spot price and Kalshi price at entry window")
    print("  2. If BTC > strike and Kalshi < 70¢: BUY YES")
    print("  3. If BTC < strike and Kalshi > 30¢: BUY NO")
    print("  4. Only enter if edge ≥ minimum threshold")
    print("  5. Wait for settlement")
    print()
    print("=" * 120)
    print()

    trades = []
    skipped_no_data = 0
    skipped_no_edge = 0
    skipped_already_priced = 0

    for ticker, strike, settled_yes, expiry_val, final_kalshi, close_time in settlements:
        # Get snapshot in entry window
        entry_data = conn.execute("""
            SELECT
                ks.ts,
                ks.yes_bid,
                ks.yes_ask,
                ks.yes_mid,
                ks.seconds_to_close,
                kr.avg_60s,
                kr.spot_price
            FROM kalshi_snapshots ks
            LEFT JOIN kraken_snapshots kr ON ABS(ks.ts - kr.ts) < 1.0
            WHERE ks.ticker = ?
              AND ks.seconds_to_close BETWEEN ? AND ?
              AND ks.yes_mid IS NOT NULL
            ORDER BY ABS(ks.seconds_to_close - ?)
            LIMIT 1
        """, (ticker, entry_window_min, entry_window_max, (entry_window_min + entry_window_max) / 2)).fetchone()

        if not entry_data or not entry_data[5]:
            skipped_no_data += 1
            continue

        ts, bid, ask, mid, ttl, btc_avg, btc_spot = entry_data

        # Determine BTC position
        btc_above_strike = btc_avg > strike

        # Calculate edge
        if btc_above_strike:
            # BTC above strike → should buy YES
            # Fair value = 100¢, Kalshi = mid¢
            # Edge = 100 - (mid + spread)
            entry_cost = (ask if ask else mid + 2.5)
            fair_value = 100
            edge = fair_value - entry_cost
            side = "YES"

            # Entry criteria: only if Kalshi < 70¢ (room to run)
            if mid >= 70:
                skipped_already_priced += 1
                continue
        else:
            # BTC below strike → should buy NO
            # Fair value = 100¢ for NO, Kalshi YES = mid¢
            # Cost to buy NO = 100 - mid + spread
            entry_cost = (100 - bid if bid else 100 - mid + 2.5)
            fair_value = 100
            edge = fair_value - entry_cost
            side = "NO"

            # Entry criteria: only if Kalshi > 30¢ (room to run)
            if mid <= 30:
                skipped_already_priced += 1
                continue

        # Minimum edge filter
        if edge < min_edge_cents:
            skipped_no_edge += 1
            continue

        # Execute trade
        actual_outcome = settled_yes

        # Determine if we won
        if side == "YES":
            won = actual_outcome
        else:  # side == "NO"
            won = not actual_outcome

        # Calculate P&L
        payout = 100 if won else 0
        pnl = payout - entry_cost

        # Check if BTC prediction was correct
        btc_predicted_yes = btc_above_strike
        btc_was_right = (btc_predicted_yes == actual_outcome)

        trades.append({
            'ticker': ticker,
            'close_time': close_time,
            'strike': strike,
            'btc_at_entry': btc_avg,
            'btc_at_settlement': expiry_val,
            'btc_above_at_entry': btc_above_strike,
            'btc_above_at_settlement': expiry_val > strike,
            'kalshi_mid': mid,
            'kalshi_bid': bid,
            'kalshi_ask': ask,
            'side': side,
            'entry_cost': entry_cost,
            'edge': edge,
            'ttl': ttl,
            'settled_yes': actual_outcome,
            'won': won,
            'pnl': pnl,
            'btc_was_right': btc_was_right,
        })

    conn.close()

    # Print results
    print(f"{'#':>3s} {'Ticker':25s} {'TTL':>5s} {'Strike':>10s} {'BTC Entry':>11s} {'BTC Final':>11s} {'Position':>9s} {'Kalshi':>8s} {'Side':>5s} {'Entry':>7s} {'Edge':>6s} {'Result':>7s} {'P&L':>8s}")
    print("-" * 120)

    for i, t in enumerate(trades, 1):
        ticker_short = t['ticker'][-20:]
        ttl_str = f"{t['ttl']:.0f}s"
        strike_str = f"${t['strike']:,.0f}"
        btc_entry_str = f"${t['btc_at_entry']:,.2f}"
        btc_final_str = f"${t['btc_at_settlement']:,.2f}"
        position = "ABOVE" if t['btc_above_at_entry'] else "BELOW"
        kalshi_str = f"{t['kalshi_mid']:.0f}¢"
        entry_str = f"{t['entry_cost']:.1f}¢"
        edge_str = f"{t['edge']:.0f}¢"
        result = "WIN" if t['won'] else "LOSS"
        pnl_str = f"{t['pnl']:+.1f}¢"

        print(f"{i:3d} {ticker_short:25s} {ttl_str:>5s} {strike_str:>10s} {btc_entry_str:>11s} {btc_final_str:>11s} {position:>9s} {kalshi_str:>8s} {t['side']:>5s} {entry_str:>7s} {edge_str:>6s} {result:>7s} {pnl_str:>8s}")

    # Summary statistics
    print()
    print("=" * 120)
    print("SUMMARY STATISTICS")
    print("=" * 120)
    print()

    total_markets = len(settlements)
    total_trades = len(trades)
    wins = sum(1 for t in trades if t['won'])
    losses = total_trades - wins

    if total_trades > 0:
        win_rate = wins / total_trades
        avg_pnl = np.mean([t['pnl'] for t in trades])
        total_pnl = sum(t['pnl'] for t in trades)
        avg_win = np.mean([t['pnl'] for t in trades if t['won']]) if wins > 0 else 0
        avg_loss = np.mean([t['pnl'] for t in trades if not t['won']]) if losses > 0 else 0

        print(f"Markets analyzed: {total_markets}")
        print(f"Skipped (no data): {skipped_no_data}")
        print(f"Skipped (no edge): {skipped_no_edge}")
        print(f"Skipped (already priced): {skipped_already_priced}")
        print(f"Trades executed: {total_trades}")
        print()
        print(f"Wins: {wins} ({win_rate:.1%})")
        print(f"Losses: {losses} ({1-win_rate:.1%})")
        print(f"Win rate: {win_rate:.1%}")
        print()
        print(f"Average profit per trade: {avg_pnl:+.1f}¢")
        print(f"Total profit: {total_pnl:+.1f}¢ (${total_pnl/100:+.2f})")
        print(f"Average win: {avg_win:+.1f}¢")
        print(f"Average loss: {avg_loss:+.1f}¢")
        print()

        # BTC prediction accuracy
        btc_correct = sum(1 for t in trades if t['btc_was_right'])
        print(f"BTC position persistence: {btc_correct}/{total_trades} = {btc_correct/total_trades:.1%}")
        print(f"  (Did BTC stay on same side of strike from entry to settlement?)")
        print()

        # Profit distribution
        big_wins = [t for t in trades if t['pnl'] > 50]
        medium_wins = [t for t in trades if 20 <= t['pnl'] <= 50]
        small_wins = [t for t in trades if 0 < t['pnl'] < 20]
        small_losses = [t for t in trades if -20 < t['pnl'] < 0]
        big_losses = [t for t in trades if t['pnl'] <= -20]

        print("Profit distribution:")
        if big_wins:
            print(f"  Big wins (>50¢): {len(big_wins)} trades, avg {np.mean([t['pnl'] for t in big_wins]):.1f}¢, total {sum(t['pnl'] for t in big_wins):.0f}¢")
        if medium_wins:
            print(f"  Medium wins (20-50¢): {len(medium_wins)} trades, avg {np.mean([t['pnl'] for t in medium_wins]):.1f}¢, total {sum(t['pnl'] for t in medium_wins):.0f}¢")
        if small_wins:
            print(f"  Small wins (0-20¢): {len(small_wins)} trades, avg {np.mean([t['pnl'] for t in small_wins]):.1f}¢, total {sum(t['pnl'] for t in small_wins):.0f}¢")
        if small_losses:
            print(f"  Small losses (0 to -20¢): {len(small_losses)} trades, avg {np.mean([t['pnl'] for t in small_losses]):.1f}¢, total {sum(t['pnl'] for t in small_losses):.0f}¢")
        if big_losses:
            print(f"  Big losses (<-20¢): {len(big_losses)} trades, avg {np.mean([t['pnl'] for t in big_losses]):.1f}¢, total {sum(t['pnl'] for t in big_losses):.0f}¢")
        print()

        # Strike crossings
        crossings = [t for t in trades if t['btc_above_at_entry'] != t['btc_above_at_settlement']]
        if crossings:
            print(f"Strike crossings detected: {len(crossings)}/{total_trades}")
            print("  These are trades where BTC crossed strike between entry and settlement:")
            for t in crossings:
                entry_pos = "ABOVE" if t['btc_above_at_entry'] else "BELOW"
                final_pos = "ABOVE" if t['btc_above_at_settlement'] else "BELOW"
                print(f"    {t['ticker'][-25:]}: {entry_pos} → {final_pos} (P&L: {t['pnl']:+.1f}¢)")
            print()
        else:
            print(f"Strike crossings: 0/{total_trades}")
            print("  BTC stayed on same side of strike for ALL trades ✓")
            print()

        # Best and worst trades
        if trades:
            best_trade = max(trades, key=lambda x: x['pnl'])
            worst_trade = min(trades, key=lambda x: x['pnl'])

            print(f"Best trade: {best_trade['ticker']}")
            print(f"  {best_trade['side']} at {best_trade['entry_cost']:.1f}¢, edge {best_trade['edge']:.0f}¢ → P&L {best_trade['pnl']:+.1f}¢")
            print()
            print(f"Worst trade: {worst_trade['ticker']}")
            print(f"  {worst_trade['side']} at {worst_trade['entry_cost']:.1f}¢, edge {worst_trade['edge']:.0f}¢ → P&L {worst_trade['pnl']:+.1f}¢")
            print()

        # Performance by edge size
        print("Performance by edge size:")
        for min_e, max_e in [(10, 20), (20, 30), (30, 50), (50, 100)]:
            bucket_trades = [t for t in trades if min_e <= t['edge'] < max_e]
            if bucket_trades:
                bucket_wins = sum(1 for t in bucket_trades if t['won'])
                bucket_wr = bucket_wins / len(bucket_trades)
                bucket_avg_pnl = np.mean([t['pnl'] for t in bucket_trades])
                print(f"  {min_e}-{max_e}¢ edge: {len(bucket_trades)} trades, {bucket_wr:.1%} WR, {bucket_avg_pnl:+.1f}¢ avg")
        print()

    else:
        print("No trades executed (no opportunities found with current filters)")
        print()
        print(f"Markets analyzed: {total_markets}")
        print(f"Skipped (no data): {skipped_no_data}")
        print(f"Skipped (no edge): {skipped_no_edge}")
        print(f"Skipped (already priced): {skipped_already_priced}")

    return trades


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest latency arb strategy")
    parser.add_argument('--db', type=Path, default=Path('data/btc_probe_20260227.db'))
    parser.add_argument('--min-edge', type=float, default=10.0,
                       help='Minimum edge in cents to enter trade')
    parser.add_argument('--entry-min', type=int, default=60,
                       help='Minimum seconds before expiry to enter')
    parser.add_argument('--entry-max', type=int, default=120,
                       help='Maximum seconds before expiry to enter')

    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found: {args.db}")
        return 1

    trades = backtest_strategy(
        args.db,
        min_edge_cents=args.min_edge,
        entry_window_min=args.entry_min,
        entry_window_max=args.entry_max,
    )

    return 0


if __name__ == '__main__':
    sys.exit(main())
