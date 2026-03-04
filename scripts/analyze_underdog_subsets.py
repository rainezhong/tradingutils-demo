#!/usr/bin/env python3
"""Analyze NBA underdog strategy by different subsets to find profitable niches."""

import csv
import re
import sys
from collections import defaultdict
from typing import Dict, List


def parse_ticker_details(ticker: str) -> Dict:
    """Extract details from NBA ticker format: KXNBAGAME-26FEB26GSWLAL-GSW"""
    match = re.search(r'KXNBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{3})([A-Z]{3})-([A-Z]{3})', ticker)
    if not match:
        return {}

    away_team = match.group(1)
    home_team = match.group(2)
    betting_on = match.group(3)

    is_home = betting_on == home_team
    is_away = betting_on == away_team

    return {
        'home_team': home_team,
        'away_team': away_team,
        'betting_on': betting_on,
        'is_home_underdog': is_home,
        'is_away_underdog': is_away,
    }


def analyze_trades_by_subset(trades: List[Dict]) -> None:
    """Analyze trades by various subsets."""

    if not trades:
        print("No trades found")
        return

    # Subset 1: Home vs Away underdogs
    home_underdogs = [t for t in trades if t.get('is_home_underdog')]
    away_underdogs = [t for t in trades if t.get('is_away_underdog')]

    print("\n" + "="*80)
    print("ANALYSIS BY HOME/AWAY UNDERDOGS")
    print("="*80)

    for subset_name, subset_trades in [('Home Underdogs', home_underdogs), ('Away Underdogs', away_underdogs)]:
        if not subset_trades:
            continue

        total = len(subset_trades)
        wins = sum(1 for t in subset_trades if t['won'])
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in subset_trades)
        pnl_per_trade = total_pnl / total if total > 0 else 0
        avg_price = sum(t['entry_price'] for t in subset_trades) / total if total > 0 else 0

        print(f"\n{subset_name}:")
        print(f"  Trades:        {total}")
        print(f"  Wins:          {wins}")
        print(f"  Win rate:      {win_rate*100:.1f}%")
        print(f"  Total P&L:     ${total_pnl:.2f}")
        print(f"  P&L/trade:     ${pnl_per_trade:.2f}")
        print(f"  Avg price:     {avg_price:.1f}¢")

    # Subset 2: By price buckets (narrow ranges)
    price_buckets = {
        '10-12¢': (10, 12),
        '13-15¢': (13, 15),
        '16-18¢': (16, 18),
        '19-20¢': (19, 20),
    }

    print("\n" + "="*80)
    print("ANALYSIS BY PRICE BUCKETS")
    print("="*80)

    for bucket_name, (min_p, max_p) in price_buckets.items():
        subset_trades = [t for t in trades if min_p <= t['entry_price'] <= max_p]

        if not subset_trades:
            continue

        total = len(subset_trades)
        wins = sum(1 for t in subset_trades if t['won'])
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in subset_trades)
        pnl_per_trade = total_pnl / total if total > 0 else 0

        print(f"\n{bucket_name}:")
        print(f"  Trades:        {total}")
        print(f"  Win rate:      {win_rate*100:.1f}%")
        print(f"  Total P&L:     ${total_pnl:.2f}")
        print(f"  P&L/trade:     ${pnl_per_trade:.2f}")

    # Subset 3: Recent vs Historical
    print("\n" + "="*80)
    print("ANALYSIS: RECENT (2026) vs HISTORICAL (2025)")
    print("="*80)

    year_2025 = [t for t in trades if t['date'].year == 2025]
    year_2026 = [t for t in trades if t['date'].year == 2026]

    for subset_name, subset_trades in [('2025', year_2025), ('2026', year_2026)]:
        if not subset_trades:
            continue

        total = len(subset_trades)
        wins = sum(1 for t in subset_trades if t['won'])
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in subset_trades)
        pnl_per_trade = total_pnl / total if total > 0 else 0

        print(f"\n{subset_name}:")
        print(f"  Trades:        {total}")
        print(f"  Win rate:      {win_rate*100:.1f}%")
        print(f"  Total P&L:     ${total_pnl:.2f}")
        print(f"  P&L/trade:     ${pnl_per_trade:.2f}")

    # Subset 4: Best teams (teams that have won the most)
    team_performance = defaultdict(lambda: {'total': 0, 'wins': 0, 'pnl': 0.0})

    for trade in trades:
        team = trade.get('betting_on', 'UNKNOWN')
        team_performance[team]['total'] += 1
        team_performance[team]['wins'] += 1 if trade['won'] else 0
        team_performance[team]['pnl'] += trade['pnl']

    # Sort by win rate (min 10 trades)
    qualified_teams = [(team, stats) for team, stats in team_performance.items() if stats['total'] >= 10]
    qualified_teams.sort(key=lambda x: x[1]['wins'] / x[1]['total'], reverse=True)

    print("\n" + "="*80)
    print("TOP 10 TEAMS BY WIN RATE (min 10 trades)")
    print("="*80)
    print(f"{'Team':<8} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Total P&L':<12} {'P&L/Trade':<12}")
    print("-"*80)

    for team, stats in qualified_teams[:10]:
        total = stats['total']
        wins = stats['wins']
        win_rate = wins / total if total > 0 else 0
        pnl = stats['pnl']
        pnl_per_trade = pnl / total if total > 0 else 0

        print(f"{team:<8} {total:<8} {wins:<6} {win_rate*100:<7.1f}% ${pnl:<11.2f} ${pnl_per_trade:<11.2f}")

    print("\n" + "="*80)
    print("BOTTOM 10 TEAMS BY WIN RATE (min 10 trades)")
    print("="*80)
    print(f"{'Team':<8} {'Trades':<8} {'Wins':<6} {'WR%':<8} {'Total P&L':<12} {'P&L/Trade':<12}")
    print("-"*80)

    for team, stats in qualified_teams[-10:]:
        total = stats['total']
        wins = stats['wins']
        win_rate = wins / total if total > 0 else 0
        pnl = stats['pnl']
        pnl_per_trade = pnl / total if total > 0 else 0

        print(f"{team:<8} {total:<8} {wins:<6} {win_rate*100:<7.1f}% ${pnl:<11.2f} ${pnl_per_trade:<11.2f}")


def parse_csv(csv_path: str, min_price: int = 10, max_price: int = 20) -> List[Dict]:
    """Parse CSV and return all trades in the price range."""
    trades = []

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)

        # Track which tickers we've entered
        entered_tickers = set()

        for row in reader:
            ticker = row["ticker"]

            if ticker in entered_tickers:
                continue

            ts = int(float(row["timestamp"]))
            price = float(row["yes_price"]) * 100  # Convert to cents
            won = row["won"] == "True"

            # Check if this is in our underdog range
            if min_price <= price <= max_price:
                entry_price_cents = int(price)

                if won:
                    pnl = (100 - entry_price_cents) - 0.07 * entry_price_cents
                else:
                    pnl = -entry_price_cents - 0.07 * entry_price_cents

                from datetime import datetime
                trade = {
                    'ticker': ticker,
                    'timestamp': ts,
                    'date': datetime.fromtimestamp(ts),
                    'entry_price': entry_price_cents,
                    'won': won,
                    'pnl': pnl,
                }

                # Add ticker details
                details = parse_ticker_details(ticker)
                trade.update(details)

                trades.append(trade)
                entered_tickers.add(ticker)

    return trades


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Analyze NBA underdog strategy by subsets')
    parser.add_argument('--csv', default='data/nba_historical_candlesticks.csv', help='Path to CSV')
    parser.add_argument('--min', type=int, default=10, help='Minimum underdog price (cents)')
    parser.add_argument('--max', type=int, default=20, help='Maximum underdog price (cents)')

    args = parser.parse_args()

    print(f"\nAnalyzing {args.csv}...")
    print(f"Price range: {args.min}-{args.max}¢")

    trades = parse_csv(args.csv, args.min, args.max)

    print(f"\nFound {len(trades)} qualifying trades")

    if trades:
        analyze_trades_by_subset(trades)
