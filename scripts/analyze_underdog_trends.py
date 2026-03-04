#!/usr/bin/env python3
"""Analyze NBA underdog strategy performance trends over time."""

import csv
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple


def parse_csv(csv_path: str, min_price: int = 10, max_price: int = 20) -> List[Dict]:
    """Parse CSV and return all trades in the price range."""
    trades = []

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)

        # Track which tickers we've entered (take first qualifying price only)
        entered_tickers = set()

        for row in reader:
            ticker = row["ticker"]

            # Skip if we already entered this ticker
            if ticker in entered_tickers:
                continue

            ts = int(float(row["timestamp"]))
            price = float(row["yes_price"]) * 100  # Convert to cents
            won = row["won"] == "True"

            # Check if this is in our underdog range
            if min_price <= price <= max_price:
                # Calculate P&L (buying YES at entry_price cents)
                entry_price_cents = int(price)

                if won:
                    pnl = (100 - entry_price_cents) - 0.07 * entry_price_cents  # Win minus fee
                else:
                    pnl = -entry_price_cents - 0.07 * entry_price_cents  # Loss minus fee

                trades.append({
                    'ticker': ticker,
                    'timestamp': ts,
                    'date': datetime.fromtimestamp(ts),
                    'entry_price': entry_price_cents,
                    'won': won,
                    'pnl': pnl,
                })

                entered_tickers.add(ticker)

    return trades


def analyze_by_period(trades: List[Dict], period_days: int = 30) -> None:
    """Analyze trades grouped by time periods."""

    if not trades:
        print("No trades found")
        return

    # Sort by timestamp
    trades.sort(key=lambda x: x['timestamp'])

    # Group by period
    first_date = trades[0]['date']
    last_date = trades[-1]['date']

    periods = defaultdict(list)

    for trade in trades:
        # Calculate which period this belongs to
        days_since_start = (trade['date'] - first_date).days
        period_num = days_since_start // period_days
        period_name = f"{first_date.year}-{first_date.month + (period_num * period_days // 30):02d}"

        periods[period_num].append(trade)

    # Analyze each period
    print(f"\n{'='*80}")
    print(f"PERFORMANCE BY {period_days}-DAY PERIOD")
    print(f"{'='*80}")
    print(f"{'Period':<12} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Total P&L':<12} {'P&L/Trade':<12} {'Avg Price':<10}")
    print(f"{'-'*80}")

    period_results = []

    for period_num in sorted(periods.keys()):
        period_trades = periods[period_num]
        start_date = period_trades[0]['date']
        end_date = period_trades[-1]['date']

        total = len(period_trades)
        wins = sum(1 for t in period_trades if t['won'])
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in period_trades)
        pnl_per_trade = total_pnl / total if total > 0 else 0
        avg_price = sum(t['entry_price'] for t in period_trades) / total if total > 0 else 0

        period_name = f"{start_date.strftime('%Y-%m-%d')}"

        print(f"{period_name:<12} {total:<8} {wins:<6} {win_rate*100:<7.1f}% ${total_pnl:<11.2f} ${pnl_per_trade:<11.2f} {avg_price:<9.1f}¢")

        period_results.append({
            'period': period_name,
            'start': start_date,
            'trades': total,
            'wins': wins,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'pnl_per_trade': pnl_per_trade,
            'avg_price': avg_price,
        })

    print(f"{'-'*80}")

    # Overall stats
    total = len(trades)
    wins = sum(1 for t in trades if t['won'])
    win_rate = wins / total if total > 0 else 0
    total_pnl = sum(t['pnl'] for t in trades)
    pnl_per_trade = total_pnl / total if total > 0 else 0
    avg_price = sum(t['entry_price'] for t in trades) / total if total > 0 else 0

    print(f"{'OVERALL':<12} {total:<8} {wins:<6} {win_rate*100:<7.1f}% ${total_pnl:<11.2f} ${pnl_per_trade:<11.2f} {avg_price:<9.1f}¢")
    print(f"{'='*80}\n")

    # Calculate trend
    if len(period_results) >= 3:
        print("\nTREND ANALYSIS:")
        print("-" * 60)

        # Compare first third vs last third
        third = len(period_results) // 3
        early_periods = period_results[:third] if third > 0 else period_results[:1]
        late_periods = period_results[-third:] if third > 0 else period_results[-1:]

        early_wr = sum(p['win_rate'] for p in early_periods) / len(early_periods)
        late_wr = sum(p['win_rate'] for p in late_periods) / len(late_periods)

        early_pnl = sum(p['pnl_per_trade'] for p in early_periods) / len(early_periods)
        late_pnl = sum(p['pnl_per_trade'] for p in late_periods) / len(late_periods)

        early_price = sum(p['avg_price'] for p in early_periods) / len(early_periods)
        late_price = sum(p['avg_price'] for p in late_periods) / len(late_periods)

        print(f"Early period avg win rate:  {early_wr*100:.1f}%")
        print(f"Late period avg win rate:   {late_wr*100:.1f}%")
        print(f"Change:                     {(late_wr - early_wr)*100:+.1f}%")
        print()
        print(f"Early period P&L/trade:     ${early_pnl:.2f}")
        print(f"Late period P&L/trade:      ${late_pnl:.2f}")
        print(f"Change:                     ${late_pnl - early_pnl:+.2f}")
        print()
        print(f"Early period avg price:     {early_price:.1f}¢")
        print(f"Late period avg price:      {late_price:.1f}¢")
        print(f"Change:                     {late_price - early_price:+.1f}¢")
        print()

        if late_wr < early_wr - 0.05:  # More than 5% decline
            print("⚠️  WARNING: Win rate has declined significantly over time")
            print("   This suggests markets are becoming more efficient.")

        if late_price < early_price - 2:  # More than 2¢ decline
            print("⚠️  WARNING: Average entry prices have declined over time")
            print("   Underdogs are getting cheaper, possibly less value.")

        if late_pnl < early_pnl - 1:  # More than $1 decline
            print("⚠️  WARNING: P&L per trade has declined over time")
            print("   Strategy edge appears to be eroding.")

        print("-" * 60)


def analyze_by_month(trades: List[Dict]) -> None:
    """Analyze trades grouped by month."""

    if not trades:
        return

    # Group by year-month
    months = defaultdict(list)

    for trade in trades:
        month_key = f"{trade['date'].year}-{trade['date'].month:02d}"
        months[month_key].append(trade)

    print(f"\n{'='*80}")
    print(f"PERFORMANCE BY MONTH")
    print(f"{'='*80}")
    print(f"{'Month':<12} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Total P&L':<12} {'P&L/Trade':<12}")
    print(f"{'-'*80}")

    for month_key in sorted(months.keys()):
        month_trades = months[month_key]

        total = len(month_trades)
        wins = sum(1 for t in month_trades if t['won'])
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in month_trades)
        pnl_per_trade = total_pnl / total if total > 0 else 0

        print(f"{month_key:<12} {total:<8} {wins:<6} {win_rate*100:<7.1f}% ${total_pnl:<11.2f} ${pnl_per_trade:<11.2f}")

    print(f"{'='*80}\n")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Analyze NBA underdog performance trends')
    parser.add_argument('--csv', default='data/nba_historical_candlesticks.csv', help='Path to CSV')
    parser.add_argument('--min', type=int, default=10, help='Minimum underdog price (cents)')
    parser.add_argument('--max', type=int, default=20, help='Maximum underdog price (cents)')
    parser.add_argument('--period-days', type=int, default=30, help='Period length in days')

    args = parser.parse_args()

    print(f"\nAnalyzing {args.csv}...")
    print(f"Price range: {args.min}-{args.max}¢")

    trades = parse_csv(args.csv, args.min, args.max)

    print(f"\nFound {len(trades)} qualifying trades")

    if trades:
        print(f"Date range: {trades[0]['date'].strftime('%Y-%m-%d')} to {trades[-1]['date'].strftime('%Y-%m-%d')}")

        analyze_by_month(trades)
        analyze_by_period(trades, args.period_days)
