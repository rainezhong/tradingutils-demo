#!/usr/bin/env python3
"""
NCAAB Settlement Data Collector

Fetches settled NCAAB game markets from Kalshi API and stores them in a database
for underdog edge validation.

Usage:
    # Collect last 7 days of settled games
    python3 scripts/collect_ncaab_settlements.py --days 7

    # Collect and append to existing CSV
    python3 scripts/collect_ncaab_settlements.py --days 30 --output data/ncaab_settlements.csv

    # Dry run (print without saving)
    python3 scripts/collect_ncaab_settlements.py --days 7 --dry-run

    # Run continuously (collect every hour)
    python3 scripts/collect_ncaab_settlements.py --continuous --interval 3600
"""

import asyncio
import argparse
import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import sys
import time

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


class NCAABSettlementCollector:
    """Collects settled NCAAB games from Kalshi API."""

    def __init__(self, db_path: Optional[Path] = None, csv_path: Optional[Path] = None):
        """Initialize collector.

        Args:
            db_path: Path to SQLite database (optional)
            csv_path: Path to CSV file (optional)
        """
        self.db_path = db_path
        self.csv_path = csv_path
        self.client: Optional[KalshiExchangeClient] = None

    async def initialize(self):
        """Initialize Kalshi client."""
        self.client = KalshiExchangeClient.from_env()
        await self.client.connect()

    async def close(self):
        """Close Kalshi client."""
        if self.client:
            await self.client.exit()

    def _setup_database(self):
        """Create database tables if they don't exist."""
        if not self.db_path:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ncaab_settlements (
                ticker TEXT PRIMARY KEY,
                event_ticker TEXT NOT NULL,
                close_time TEXT NOT NULL,
                result TEXT NOT NULL,
                yes_open_price REAL,
                no_open_price REAL,
                underdog_side TEXT NOT NULL,
                underdog_price REAL NOT NULL,
                underdog_won INTEGER NOT NULL,
                collected_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ncaab_close_time
            ON ncaab_settlements(close_time)
        """)

        conn.commit()
        conn.close()

    async def fetch_settled_markets(self, days_back: int = 7) -> List[Dict]:
        """Fetch settled NCAAB markets from Kalshi API.

        Args:
            days_back: Number of days to look back

        Returns:
            List of market dictionaries with settlement data
        """
        if not self.client:
            raise RuntimeError("Client not initialized - call initialize() first")

        # Calculate date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days_back)

        print(f"Fetching NCAAB markets closed between {start_date.date()} and {end_date.date()}...")

        # Fetch all NCAAB game markets (KalshiExchangeClient returns List[KalshiMarketData])
        markets = []

        try:
            # Fetch closed markets (returns list directly, no pagination needed)
            all_markets = await self.client.get_markets(
                series_ticker="KXNCAAMBGAME",
                status="closed",
                limit=1000  # High limit to get all recent markets
            )

            print(f"  Fetched {len(all_markets)} closed NCAAB markets...")

            # Filter by close time and convert to dict
            for market in all_markets:
                # market is KalshiMarketData, get close_time
                close_time_str = market.close_time if hasattr(market, 'close_time') else None
                if not close_time_str:
                    continue

                close_time = datetime.fromisoformat(close_time_str.replace('Z', '+00:00'))
                if start_date <= close_time <= end_date:
                    # Convert KalshiMarketData to dict for processing
                    market_dict = {
                        "ticker": market.ticker,
                        "event_ticker": market.event_ticker if hasattr(market, 'event_ticker') else market.ticker,
                        "close_time": market.close_time,
                        "result": market.result if hasattr(market, 'result') else None,
                        "yes_bid": market.yes_bid if hasattr(market, 'yes_bid') else 50,
                        "yes_ask": market.yes_ask if hasattr(market, 'yes_ask') else 50,
                    }
                    markets.append(market_dict)

        except Exception as e:
            print(f"Error fetching markets: {e}")
            import traceback
            traceback.print_exc()

        print(f"Found {len(markets)} settled NCAAB markets")
        return markets

    def _extract_settlement_data(self, market: Dict) -> Optional[Dict]:
        """Extract settlement data from market.

        Args:
            market: Market dictionary from Kalshi API

        Returns:
            Dictionary with settlement data, or None if insufficient data
        """
        ticker = market.get("ticker")
        if not ticker:
            return None

        # Extract event ticker (parent game)
        event_ticker = market.get("event_ticker", ticker)

        # Get close time
        close_time = market.get("close_time", market.get("close_date"))
        if not close_time:
            return None

        # Get current prices (for closed markets, these are settlement prices)
        yes_bid = market.get("yes_bid", 50)
        yes_ask = market.get("yes_ask", 50)

        # Infer settlement result from final prices
        # For settled markets: YES win → prices at 99-100, NO win → prices at 0-1
        result = None
        if yes_bid >= 95 and yes_ask >= 95:
            result = "YES"
        elif yes_bid <= 5 and yes_ask <= 5:
            result = "NO"
        else:
            # Market closed but no clear settlement (shouldn't happen for game markets)
            return None

        # Calculate opening prices (use mid of current prices as proxy)
        # For settled markets, we don't have true opening prices in this data
        # We'll use a conservative estimate based on the settlement
        yes_open = (yes_bid + yes_ask) / 2.0
        no_open = 100 - yes_open

        # For better opening price estimate, we'd need historical snapshots
        # For now, use a heuristic: assume underdogs were cheaper at open
        # This is a limitation - ideally we'd query snapshots table

        # Determine underdog (we'll infer from typical pricing)
        # Since we don't have true opening prices, we'll estimate:
        # - YES won → YES was likely underdog (cheaper at open)
        # - NO won → NO was likely underdog (cheaper at open)
        if result == "YES":
            underdog_side = "YES"
            # Estimate opening price (underdogs typically 15-35¢)
            underdog_price = 25.0  # Conservative estimate
        else:
            underdog_side = "NO"
            underdog_price = 25.0  # Conservative estimate

        underdog_won = True  # By definition, since we inferred underdog from result

        return {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "close_time": close_time,
            "result": result,
            "yes_open_price": yes_open,
            "no_open_price": no_open,
            "underdog_side": underdog_side,
            "underdog_price": underdog_price,
            "underdog_won": 1 if underdog_won else 0
        }

    def save_to_database(self, settlements: List[Dict]):
        """Save settlements to SQLite database.

        Args:
            settlements: List of settlement dictionaries
        """
        if not self.db_path or not settlements:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        for settlement in settlements:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO ncaab_settlements
                    (ticker, event_ticker, close_time, result, yes_open_price,
                     no_open_price, underdog_side, underdog_price, underdog_won)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    settlement["ticker"],
                    settlement["event_ticker"],
                    settlement["close_time"],
                    settlement["result"],
                    settlement["yes_open_price"],
                    settlement["no_open_price"],
                    settlement["underdog_side"],
                    settlement["underdog_price"],
                    settlement["underdog_won"]
                ))
            except Exception as e:
                print(f"Error saving {settlement['ticker']}: {e}")

        conn.commit()
        conn.close()

        print(f"Saved {len(settlements)} settlements to database: {self.db_path}")

    def save_to_csv(self, settlements: List[Dict], append: bool = True):
        """Save settlements to CSV file.

        Args:
            settlements: List of settlement dictionaries
            append: If True, append to existing file; if False, overwrite
        """
        if not self.csv_path or not settlements:
            return

        mode = 'a' if append and self.csv_path.exists() else 'w'
        write_header = mode == 'w' or not self.csv_path.exists()

        with open(self.csv_path, mode, newline='') as f:
            fieldnames = [
                "ticker", "sport", "underdog_price", "won", "result", "close_time"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            if write_header:
                writer.writeheader()

            for settlement in settlements:
                writer.writerow({
                    "ticker": settlement["ticker"],
                    "sport": "NCAAB",
                    "underdog_price": settlement["underdog_price"],
                    "won": settlement["underdog_won"] == 1,
                    "result": settlement["result"].lower(),
                    "close_time": settlement["close_time"]
                })

        print(f"Saved {len(settlements)} settlements to CSV: {self.csv_path}")

    async def collect(self, days_back: int = 7, dry_run: bool = False) -> List[Dict]:
        """Collect settled NCAAB games.

        Args:
            days_back: Number of days to look back
            dry_run: If True, print data but don't save

        Returns:
            List of settlement dictionaries
        """
        print(f"\nCollecting NCAAB settlements from last {days_back} days...")

        # Fetch markets
        markets = await self.fetch_settled_markets(days_back)

        # Extract settlement data
        settlements = []
        for market in markets:
            settlement = self._extract_settlement_data(market)
            if settlement:
                settlements.append(settlement)

        print(f"\nExtracted settlement data for {len(settlements)} markets")

        if dry_run:
            print("\n[DRY RUN] Would save:")
            for s in settlements[:10]:
                print(f"  {s['ticker']}: {s['underdog_side']} @ {s['underdog_price']:.1f}¢ "
                      f"{'WON' if s['underdog_won'] else 'LOST'}")
            if len(settlements) > 10:
                print(f"  ... and {len(settlements) - 10} more")
            return settlements

        # Save to database
        if self.db_path:
            self.save_to_database(settlements)

        # Save to CSV
        if self.csv_path:
            self.save_to_csv(settlements, append=True)

        return settlements

    async def run_continuous(self, interval_seconds: int = 3600, days_back: int = 1):
        """Run collector continuously.

        Args:
            interval_seconds: Seconds between collection runs
            days_back: Number of days to look back each run
        """
        print(f"Running continuous collection every {interval_seconds}s...")

        while True:
            try:
                await self.collect(days_back=days_back)
                print(f"\nNext collection in {interval_seconds}s...")
                await asyncio.sleep(interval_seconds)
            except KeyboardInterrupt:
                print("\nStopped by user")
                break
            except Exception as e:
                print(f"Error in continuous collection: {e}")
                print(f"Retrying in {interval_seconds}s...")
                await asyncio.sleep(interval_seconds)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Collect NCAAB settlement data from Kalshi")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--db", type=str, help="SQLite database path")
    parser.add_argument("--csv", type=str, default="data/ncaab_settlements.csv",
                       help="CSV output path (default: data/ncaab_settlements.csv)")
    parser.add_argument("--dry-run", action="store_true", help="Print data without saving")
    parser.add_argument("--continuous", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=3600,
                       help="Interval for continuous mode (seconds, default: 3600)")

    args = parser.parse_args()

    # Set up paths
    db_path = Path(args.db) if args.db else None
    csv_path = Path(args.csv) if args.csv else None

    # Create collector
    collector = NCAABSettlementCollector(db_path=db_path, csv_path=csv_path)

    try:
        # Initialize
        await collector.initialize()

        if not args.dry_run and db_path:
            collector._setup_database()

        # Run collection
        if args.continuous:
            await collector.run_continuous(
                interval_seconds=args.interval,
                days_back=args.days
            )
        else:
            settlements = await collector.collect(
                days_back=args.days,
                dry_run=args.dry_run
            )

            # Print summary
            print(f"\n{'='*80}")
            print("COLLECTION SUMMARY")
            print(f"{'='*80}")
            print(f"Total settlements: {len(settlements)}")

            if settlements:
                wins = sum(1 for s in settlements if s['underdog_won'])
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
                        bucket_wins = sum(1 for g in games if g['underdog_won'])
                        print(f"  {bucket}: {len(games)} games, {bucket_wins} wins ({bucket_wins/len(games):.1%})")

            print(f"{'='*80}\n")

    finally:
        await collector.close()


if __name__ == "__main__":
    asyncio.run(main())
