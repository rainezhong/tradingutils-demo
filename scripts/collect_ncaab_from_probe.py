#!/usr/bin/env python3
"""
Collect NCAAB settlements from probe database.

Uses the probe_ncaab.db snapshot data to get accurate opening prices
and settlement results.

Usage:
    # From probe database
    python3 scripts/collect_ncaab_from_probe.py --db data/probe_ncaab.db --output data/ncaab_settlements.csv

    # From markets.db
    python3 scripts/collect_ncaab_from_probe.py --db data/markets.db --output data/ncaab_settlements.csv
"""

import sqlite3
import argparse
import csv
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime


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

    if schema['is_probe']:
        # probe DB uses ts (unix timestamp) and yes_mid
        query = f"""
            SELECT yes_mid
            FROM {schema['table']}
            WHERE ticker = ?
            ORDER BY {schema['timestamp_col']} ASC
            LIMIT 1
        """
    else:
        # markets.db uses timestamp (ISO string) and calculated mid
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


def get_settlement(conn: sqlite3.Connection, ticker: str, schema: dict) -> Optional[str]:
    """Check settlement result (YES or NO) from final prices."""
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

    # YES won if final prices at ~100
    if yes_bid >= 95 and yes_ask >= 95:
        return "YES"
    # NO won if final prices at ~0
    elif yes_bid <= 5 and yes_ask <= 5:
        return "NO"
    else:
        # Not clearly settled
        return None


def get_close_time(conn: sqlite3.Connection, ticker: str, schema: dict) -> Optional[str]:
    """Get close time from last snapshot."""
    cursor = conn.cursor()

    if schema['is_probe']:
        # Convert unix timestamp to ISO string
        query = f"""
            SELECT datetime({schema['timestamp_col']}, 'unixepoch') as close_time
            FROM {schema['table']}
            WHERE ticker = ?
            ORDER BY {schema['timestamp_col']} DESC
            LIMIT 1
        """
    else:
        query = f"""
            SELECT {schema['timestamp_col']} as close_time
            FROM {schema['table']}
            WHERE ticker = ?
            ORDER BY {schema['timestamp_col']} DESC
            LIMIT 1
        """

    cursor.execute(query, (ticker,))
    row = cursor.fetchone()
    return row[0] if row else None


def collect_settlements(db_path: Path) -> List[Dict]:
    """Collect settled NCAAB games from database.

    Args:
        db_path: Path to probe database

    Returns:
        List of settlement dictionaries
    """
    conn = sqlite3.connect(db_path)
    schema = detect_schema(conn)

    print(f"Using schema: {schema['table']} (probe={schema['is_probe']})")

    # Get all NCAAB game tickers
    cursor = conn.cursor()
    query = f"""
        SELECT DISTINCT ticker
        FROM {schema['table']}
        WHERE ticker LIKE 'KXNCAAMBGAME-%'
    """

    cursor.execute(query)
    tickers = [row[0] for row in cursor.fetchall()]

    print(f"Found {len(tickers)} NCAAB game tickers")

    settlements = []

    for ticker in tickers:
        # Get opening price
        yes_mid = get_opening_price(conn, ticker, schema)
        if yes_mid is None:
            continue

        # Get settlement
        result = get_settlement(conn, ticker, schema)
        if result is None:
            continue

        # Get close time
        close_time = get_close_time(conn, ticker, schema)
        if close_time is None:
            continue

        # Calculate underdog price
        no_mid = 100 - yes_mid

        if yes_mid <= no_mid:
            underdog_side = "YES"
            underdog_price = yes_mid
        else:
            underdog_side = "NO"
            underdog_price = no_mid

        # Check if underdog won
        underdog_won = (underdog_side.upper() == result.upper())

        settlements.append({
            "ticker": ticker,
            "sport": "NCAAB",
            "underdog_price": underdog_price,
            "won": underdog_won,
            "result": result.lower(),
            "close_time": close_time
        })

    conn.close()

    return settlements


def save_to_csv(settlements: List[Dict], output_path: Path, append: bool = True):
    """Save settlements to CSV.

    Args:
        settlements: List of settlement dicts
        output_path: Path to CSV file
        append: If True, append to existing; if False, overwrite
    """
    mode = 'a' if append and output_path.exists() else 'w'
    write_header = mode == 'w' or not output_path.exists()

    # Load existing tickers to avoid duplicates
    existing_tickers = set()
    if output_path.exists() and append:
        with open(output_path, 'r') as f:
            reader = csv.DictReader(f)
            existing_tickers = {row['ticker'] for row in reader}

    # Filter out duplicates
    new_settlements = [s for s in settlements if s['ticker'] not in existing_tickers]

    if not new_settlements:
        print(f"No new settlements to add (all {len(settlements)} already exist)")
        return

    with open(output_path, mode, newline='') as f:
        fieldnames = ["ticker", "sport", "underdog_price", "won", "result", "close_time"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if write_header:
            writer.writeheader()

        for settlement in new_settlements:
            writer.writerow(settlement)

    print(f"Saved {len(new_settlements)} new settlements to {output_path}")
    print(f"Total in file: {len(existing_tickers) + len(new_settlements)}")


def print_summary(settlements: List[Dict]):
    """Print summary statistics."""
    if not settlements:
        print("No settlements found")
        return

    print(f"\n{'='*80}")
    print("SETTLEMENT SUMMARY")
    print(f"{'='*80}")
    print(f"Total games: {len(settlements)}")

    wins = sum(1 for s in settlements if s['won'])
    print(f"Underdog wins: {wins}/{len(settlements)} ({wins/len(settlements):.1%})")

    # Bucket analysis
    from collections import defaultdict
    buckets = defaultdict(list)
    for s in settlements:
        price = s['underdog_price']
        if price < 10:
            bucket = "0-10¢"
        elif price < 15:
            bucket = "10-15¢"
        elif price < 20:
            bucket = "15-20¢"
        elif price < 25:
            bucket = "20-25¢"
        elif price < 30:
            bucket = "25-30¢"
        elif price < 35:
            bucket = "30-35¢"
        else:
            bucket = "35+¢"
        buckets[bucket].append(s)

    print(f"\nBy bucket:")
    for bucket in ["0-10¢", "10-15¢", "15-20¢", "20-25¢", "25-30¢", "30-35¢", "35+¢"]:
        if bucket in buckets:
            games = buckets[bucket]
            bucket_wins = sum(1 for g in games if g['won'])
            avg_price = sum(g['underdog_price'] for g in games) / len(games)
            print(f"  {bucket}: {len(games)} games, {bucket_wins} wins ({bucket_wins/len(games):.1%}), avg price: {avg_price:.1f}¢")

    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Collect NCAAB settlements from probe database")
    parser.add_argument("--db", type=str, required=True, help="Path to database (probe_ncaab.db or markets.db)")
    parser.add_argument("--output", type=str, default="data/ncaab_settlements.csv", help="Output CSV path")
    parser.add_argument("--no-append", action="store_true", help="Overwrite instead of append")

    args = parser.parse_args()

    db_path = Path(args.db)
    output_path = Path(args.output)

    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        return 1

    print(f"Collecting settlements from {db_path}...")
    settlements = collect_settlements(db_path)

    if not settlements:
        print("No settled games found")
        return 0

    print_summary(settlements)

    save_to_csv(settlements, output_path, append=not args.no_append)

    return 0


if __name__ == "__main__":
    exit(main())
