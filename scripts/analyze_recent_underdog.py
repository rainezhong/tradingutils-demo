#!/usr/bin/env python3
"""Analyze NBA underdog strategy performance on recent games from probe database."""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.exchange_client.kalshi import KalshiExchangeClient


async def get_market_result(client: KalshiExchangeClient, ticker: str) -> Optional[str]:
    """Get the result of a settled market.

    Returns 'yes' if YES won, 'no' if NO won, None if not settled or error.
    Settled markets have prices at extremes: winner at 99-100, loser at 0-1.
    """
    try:
        market = await client.request_market(ticker)

        # Check if market is finalized
        if market.status != 'finalized':
            return None

        # Winner has yes_bid=99, yes_ask=100
        # Loser has yes_bid=0, yes_ask=1
        if market.yes_bid >= 99 and market.yes_ask >= 99:
            return 'yes'
        elif market.yes_bid <= 1 and market.yes_ask <= 1:
            return 'no'
        else:
            # Market settled but not at extremes (shouldn't happen)
            return None

    except Exception as e:
        # Market not found or other error - likely not settled yet
        return None


async def analyze_recent_underdog_performance(db_path: str, min_price: int = 10, max_price: int = 20):
    """Analyze underdog strategy on recent NBA games."""

    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all unique game events from the last few days
    cursor.execute("""
        SELECT DISTINCT ticker
        FROM kalshi_snapshots
        WHERE ticker LIKE 'KXNBAGAME-%'
        AND ts >= strftime('%s', 'now', '-10 days')
        ORDER BY ts DESC
    """)

    all_tickers = [row[0] for row in cursor.fetchall()]
    print(f"Found {len(all_tickers)} NBA markets in the last 10 days")

    # Initialize Kalshi client
    client = KalshiExchangeClient.from_env()
    await client.connect()

    # Analyze each ticker
    trades: List[Dict] = []

    for ticker in all_tickers:
        # Get price snapshots for this ticker
        cursor.execute("""
            SELECT ts, yes_bid, yes_ask, yes_mid
            FROM kalshi_snapshots
            WHERE ticker = ?
            ORDER BY ts ASC
        """, (ticker,))

        snapshots = cursor.fetchall()
        if not snapshots:
            continue

        # Check if this would be an underdog entry
        # Strategy enters when price is in the min-max range
        entry_price = None
        entry_ts = None

        for ts, yes_bid, yes_ask, yes_mid in snapshots:
            if yes_mid is None:
                continue

            price_cents = int(yes_mid)

            # Check if this is in our underdog range
            if min_price <= price_cents <= max_price:
                entry_price = price_cents
                entry_ts = ts
                break

        if entry_price is None:
            continue

        # Get the actual result
        result = await get_market_result(client, ticker)

        if result is None:
            print(f"Skipping {ticker} - no result yet or error")
            continue

        # Calculate P&L
        # Buying YES at entry_price cents
        won = (result == 'yes')

        if won:
            pnl = (100 - entry_price) - 0.07 * entry_price  # Win minus fee
        else:
            pnl = -entry_price - 0.07 * entry_price  # Loss minus fee

        trades.append({
            'ticker': ticker,
            'entry_price': entry_price,
            'entry_time': datetime.fromtimestamp(entry_ts, tz=timezone.utc),
            'result': result,
            'won': won,
            'pnl': pnl,
        })

        print(f"{ticker}: Entry @ {entry_price}¢ → {result.upper()} → ${pnl:.2f}")

    await client.exit()
    conn.close()

    # Calculate summary statistics
    if not trades:
        print("\nNo qualifying trades found in the date range")
        return

    total_pnl = sum(t['pnl'] for t in trades)
    wins = sum(1 for t in trades if t['won'])
    losses = len(trades) - wins
    win_rate = wins / len(trades) if trades else 0

    avg_winner = sum(t['pnl'] for t in trades if t['won']) / wins if wins > 0 else 0
    avg_loser = sum(t['pnl'] for t in trades if not t['won']) / losses if losses > 0 else 0

    print("\n" + "="*60)
    print(f"RECENT NBA UNDERDOG ANALYSIS ({min_price}-{max_price}¢)")
    print("="*60)
    print(f"Total trades:     {len(trades)}")
    print(f"Winners:          {wins}")
    print(f"Losers:           {losses}")
    print(f"Win rate:         {win_rate:.1%}")
    print(f"\nTotal P&L:        ${total_pnl:.2f}")
    print(f"P&L per trade:    ${total_pnl/len(trades):.2f}")
    print(f"\nAvg winner:       ${avg_winner:.2f}")
    print(f"Avg loser:        ${avg_loser:.2f}")
    print(f"Payoff ratio:     {abs(avg_winner/avg_loser):.2f}" if avg_loser != 0 else "N/A")
    print("="*60)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Analyze recent NBA underdog strategy performance')
    parser.add_argument('--db', default='data/probe_nba.db', help='Path to probe database')
    parser.add_argument('--min', type=int, default=10, help='Minimum underdog price (cents)')
    parser.add_argument('--max', type=int, default=20, help='Maximum underdog price (cents)')

    args = parser.parse_args()

    asyncio.run(analyze_recent_underdog_performance(args.db, args.min, args.max))
