#!/usr/bin/env python3
"""
Analyze underdog value using historical market database with snapshots.

This gives accurate pre-game prices instead of volume-based estimates.

Usage:
    python3 scripts/analyze_underdog_from_probe.py --db data/markets.db --series KXNCAAMBGAME
    python3 scripts/analyze_underdog_from_probe.py --db data/probe_ncaab.db --series KXNCAAMBGAME
"""

import sqlite3
import argparse
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from pathlib import Path


@dataclass
class UnderdogResult:
    """Result of one underdog bet."""
    ticker: str
    underdog_price: float
    won: bool
    game_ticker: str


@dataclass
class BucketStats:
    """Statistics for a price bucket."""
    bucket_label: str
    count: int
    wins: int
    win_rate: float
    avg_price: float
    edge: float  # Win rate - implied probability
    ev_per_dollar: float
    roi: float


def detect_schema(conn: sqlite3.Connection) -> dict:
    """Detect database schema (probe vs markets.db)."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]

    if 'kalshi_snapshots' in tables:
        return {
            'table': 'kalshi_snapshots',
            'timestamp_col': 'ts',
            'is_probe': True
        }
    elif 'snapshots' in tables:
        return {
            'table': 'snapshots',
            'timestamp_col': 'timestamp',
            'is_probe': False
        }
    else:
        raise ValueError("Unknown database schema - no snapshots table found")


def get_opening_price(conn: sqlite3.Connection, ticker: str, schema: dict) -> Optional[float]:
    """Get opening mid price (first snapshot)."""
    cursor = conn.cursor()
    query = f"""
        SELECT (yes_bid + yes_ask) / 2.0 as mid_price
        FROM {schema['table']}
        WHERE ticker = ?
        ORDER BY {schema['timestamp_col']} ASC
        LIMIT 1
    """
    cursor.execute(query, (ticker,))

    row = cursor.fetchone()
    return row[0] if row else None


def get_settlement(conn: sqlite3.Connection, ticker: str, schema: dict) -> Optional[bool]:
    """Check if YES won (returns True if yes_bid/yes_ask near 100 at end)."""
    cursor = conn.cursor()
    query = f"""
        SELECT yes_bid, yes_ask
        FROM {schema['table']}
        WHERE ticker = ?
        ORDER BY {schema['timestamp_col']} DESC
        LIMIT 1
    """
    cursor.execute(query, (ticker,))

    row = cursor.fetchone()
    if not row:
        return None

    yes_bid, yes_ask = row
    # YES won if final prices at 100
    if yes_bid >= 95 and yes_ask >= 95:
        return True
    # NO won if final prices at 0
    if yes_bid <= 5 and yes_ask <= 5:
        return False

    # Not settled yet
    return None


def analyze_underdogs(
    db_path: str,
    series: str = None,
    min_price: float = 10.0,
    max_price: float = 40.0
) -> List[UnderdogResult]:
    """Analyze underdogs from database with historical snapshots."""
    conn = sqlite3.Connection(db_path)
    cursor = conn.cursor()

    # Detect schema
    schema = detect_schema(conn)

    # Get all unique tickers (optionally filtered by series)
    if series:
        query = f"SELECT DISTINCT ticker FROM {schema['table']} WHERE ticker LIKE ?"
        cursor.execute(query, (f"{series}%",))
    else:
        query = f"SELECT DISTINCT ticker FROM {schema['table']}"
        cursor.execute(query)

    all_tickers = [row[0] for row in cursor.fetchall()]

    # Group by game (event ticker)
    games = defaultdict(list)
    for ticker in all_tickers:
        # Parse event ticker from market ticker
        # Format: KXNCAAMBGAME-26JAN21CHSOPRE-CHSO
        # Split gives: ['KXNCAAMBGAME', '26JAN21CHSOPRE', 'CHSO']
        # Event ticker: KXNCAAMBGAME-26JAN21CHSOPRE (first 2 parts, excluding team)
        parts = ticker.split('-')
        if len(parts) >= 3:
            event_ticker = '-'.join(parts[:-1])  # Everything except last part (team name)
            games[event_ticker].append(ticker)

    results = []

    for event_ticker, market_tickers in games.items():
        # Each game should have 2 markets (one per team)
        if len(market_tickers) != 2:
            continue

        ticker1, ticker2 = market_tickers

        # Get opening prices
        price1 = get_opening_price(conn, ticker1, schema)
        price2 = get_opening_price(conn, ticker2, schema)

        # Skip if can't get prices
        if price1 is None or price2 is None:
            continue

        # Get settlements
        won1 = get_settlement(conn, ticker1, schema)
        won2 = get_settlement(conn, ticker2, schema)

        # Skip if not settled
        if won1 is None or won2 is None:
            continue

        # Skip if both won or both lost (shouldn't happen)
        if won1 == won2:
            continue

        # Determine underdog (lower price)
        if price1 < price2:
            underdog_ticker = ticker1
            underdog_price = price1
            underdog_won = won1
        else:
            underdog_ticker = ticker2
            underdog_price = price2
            underdog_won = won2

        # Filter by price range
        if min_price <= underdog_price <= max_price:
            results.append(UnderdogResult(
                ticker=underdog_ticker,
                underdog_price=underdog_price,
                won=underdog_won,
                game_ticker=event_ticker
            ))

    conn.close()
    return results


def calculate_bucket_stats(results: List[UnderdogResult]) -> List[BucketStats]:
    """Calculate statistics by price bucket."""
    # Define buckets
    buckets = [
        (10, 15),
        (15, 20),
        (20, 25),
        (25, 30),
        (30, 35),
        (35, 40),
    ]

    bucket_stats = []

    for min_price, max_price in buckets:
        # Filter results in this bucket
        bucket_results = [r for r in results if min_price <= r.underdog_price < max_price]

        if not bucket_results:
            continue

        count = len(bucket_results)
        wins = sum(1 for r in bucket_results if r.won)
        win_rate = wins / count
        avg_price = sum(r.underdog_price for r in bucket_results) / count
        implied_prob = avg_price / 100.0
        edge = win_rate - implied_prob

        # EV per dollar: (win_rate * (100 - avg_price) - (1 - win_rate) * avg_price) / avg_price
        # = (win_rate * 100 - avg_price) / avg_price
        ev_per_dollar = (win_rate * 100 - avg_price) / avg_price
        roi = ev_per_dollar * 100

        bucket_stats.append(BucketStats(
            bucket_label=f"{min_price}-{max_price}¢",
            count=count,
            wins=wins,
            win_rate=win_rate,
            avg_price=avg_price,
            edge=edge,
            ev_per_dollar=ev_per_dollar,
            roi=roi
        ))

    return bucket_stats


def print_analysis(results: List[UnderdogResult], bucket_stats: List[BucketStats], db_name: str):
    """Print analysis results."""
    print("=" * 80)
    print(f"UNDERDOG VALUE ANALYSIS ({db_name})")
    print("=" * 80)
    print()
    print(f"Total games analyzed: {len(results)}")

    if not results:
        print("\n❌ No settled games found")
        return

    # Overall stats
    total_wins = sum(1 for r in results if r.won)
    win_rate = total_wins / len(results)
    avg_price = sum(r.underdog_price for r in results) / len(results)

    print(f"Overall win rate: {win_rate*100:.1f}%")
    print(f"Average underdog price: {avg_price:.1f}¢")
    print()

    # Bucket breakdown
    print("Bucket       Games    Win Rate     Implied      Edge         EV/$1        ROI")
    print("-" * 90)

    for stats in bucket_stats:
        implied = stats.avg_price / 100.0
        edge_str = f"{stats.edge:+.1%}"
        ev_str = f"{stats.ev_per_dollar:+.2f}¢" if abs(stats.ev_per_dollar) < 10 else f"{stats.ev_per_dollar:+.1f}¢"
        roi_str = f"{stats.roi:+.1f}%"

        # Determine status
        if stats.ev_per_dollar > 0.05:
            status = "✅"
        elif stats.ev_per_dollar < -0.05:
            status = "❌"
        else:
            status = "⚠️ "

        print(f"{stats.bucket_label:12} {stats.count:4}     {stats.win_rate:5.1%}        {implied:5.1%}        {edge_str:8}     {ev_str:12} {roi_str:12} {status}")

    # Summary
    print()
    profitable = [s for s in bucket_stats if s.ev_per_dollar > 0.05]
    if profitable:
        print(f"✅ PROFITABLE BUCKETS: {len(profitable)}")
        for stats in sorted(profitable, key=lambda x: x.ev_per_dollar, reverse=True):
            print(f"   {stats.bucket_label}: {stats.ev_per_dollar:+.2f}¢ EV ({stats.win_rate:.1%} win rate, {stats.count} games)")


def main():
    parser = argparse.ArgumentParser(description="Analyze underdog value from historical market database")
    parser.add_argument("--db", required=True, help="Path to database (markets.db or probe_*.db)")
    parser.add_argument("--series", help="Series to analyze (e.g., KXNCAAMBGAME, KXNBAGAME)")
    parser.add_argument("--min-price", type=float, default=10.0, help="Minimum underdog price (default: 10)")
    parser.add_argument("--max-price", type=float, default=40.0, help="Maximum underdog price (default: 40)")

    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"❌ Database not found: {args.db}")
        return 1

    # Analyze
    print(f"Analyzing {args.series or 'all series'} from {Path(args.db).name}...")
    print()
    results = analyze_underdogs(args.db, args.series, args.min_price, args.max_price)
    bucket_stats = calculate_bucket_stats(results)

    # Print
    series_name = args.series or Path(args.db).stem
    print_analysis(results, bucket_stats, series_name)

    return 0


if __name__ == "__main__":
    exit(main())
