#!/usr/bin/env python3
"""
Collect Kalshi Weather Market Data

Pulls historical data for KXHIGH series (daily high temperature markets)
for major cities: NYC, Chicago, LA, Miami, Austin.

Markets settle on NWS Daily Climate Report (official government data).
"""

import asyncio
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient
from core.exchange_client.kalshi.kalshi_auth import KalshiAuth, get_credentials_from_env


class WeatherMarketCollector:
    """Collects Kalshi weather/temperature market data."""

    def __init__(self, db_path: str = "data/weather_markets.db"):
        self.db_path = db_path
        self.conn = None
        self.client = None

        # Weather market series by city
        self.series = {
            "NYC": "KXHIGHNY",
            "Chicago": "KXHIGHCHI",
            "LA": "KXHIGHLAX",
            "Miami": "KXHIGHMIA",
            "Austin": "KXHIGHAUST",
        }

    def init_database(self):
        """Create database schema."""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        # Markets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                ticker TEXT PRIMARY KEY,
                series_ticker TEXT,
                city TEXT,
                date TEXT,
                strike_temp INTEGER,
                subtitle TEXT,
                open_time INTEGER,
                close_time INTEGER,
                expiration_time INTEGER,
                status TEXT,
                yes_bid INTEGER,
                yes_ask INTEGER,
                no_bid INTEGER,
                no_ask INTEGER,
                last_price INTEGER,
                volume INTEGER,
                open_interest INTEGER,
                created_at INTEGER
            )
        """)

        # Price snapshots table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                ts INTEGER,
                yes_bid INTEGER,
                yes_ask INTEGER,
                no_bid INTEGER,
                no_ask INTEGER,
                last_price INTEGER,
                volume INTEGER,
                open_interest INTEGER,
                FOREIGN KEY (ticker) REFERENCES markets(ticker)
            )
        """)

        # Settlements table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settlements (
                ticker TEXT PRIMARY KEY,
                settled_at INTEGER,
                result TEXT,
                actual_temp REAL,
                strike_temp INTEGER,
                nws_station TEXT,
                FOREIGN KEY (ticker) REFERENCES markets(ticker)
            )
        """)

        # Create indices
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON snapshots(ticker)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_series ON markets(series_ticker)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_date ON markets(date)")

        self.conn.commit()
        print(f"✓ Database initialized: {self.db_path}")

    async def collect_markets(self, days_back: int = 7):
        """
        Collect weather markets from past N days.

        Args:
            days_back: How many days of historical markets to collect
        """
        # Initialize client
        api_key, private_key = get_credentials_from_env()
        auth = KalshiAuth(api_key, private_key)
        self.client = KalshiExchangeClient(auth)

        print(f"\n{'='*70}")
        print(f"Collecting weather markets (last {days_back} days)")
        print(f"{'='*70}\n")

        total_markets = 0

        for city, series in self.series.items():
            print(f"\n📍 {city} ({series})")

            try:
                # Get all markets for this series
                markets = await self.client.get_markets(
                    series_ticker=series,
                    limit=200,
                    status="all"  # Get both open and settled markets
                )

                print(f"   Found {len(markets)} markets")

                for market in markets:
                    # Extract date from ticker (e.g., KXHIGHNY-26FEB27-75)
                    parts = market.ticker.split('-')
                    if len(parts) >= 3:
                        date_str = parts[1]  # e.g., "26FEB27"
                        strike = int(parts[2])  # e.g., "75"
                    else:
                        continue

                    # Save market
                    self._save_market(market, city, date_str, strike)
                    total_markets += 1

                    # Get current snapshot
                    self._save_snapshot(market)

            except Exception as e:
                print(f"   ⚠️  Error: {e}")

        await self.client.close()

        print(f"\n{'='*70}")
        print(f"✓ Collected {total_markets} weather markets")
        print(f"{'='*70}\n")

        return total_markets

    def _save_market(self, market, city: str, date_str: str, strike: int):
        """Save market to database."""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO markets (
                ticker, series_ticker, city, date, strike_temp,
                subtitle, open_time, close_time, expiration_time,
                status, yes_bid, yes_ask, no_bid, no_ask,
                last_price, volume, open_interest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market.ticker,
            market.series_ticker,
            city,
            date_str,
            strike,
            market.subtitle,
            int(market.open_time.timestamp()) if market.open_time else None,
            int(market.close_time.timestamp()) if market.close_time else None,
            int(market.expiration_time.timestamp()) if market.expiration_time else None,
            market.status,
            market.yes_bid,
            market.yes_ask,
            market.no_bid,
            market.no_ask,
            market.last_price,
            market.volume,
            market.open_interest,
            int(datetime.now().timestamp())
        ))

        self.conn.commit()

    def _save_snapshot(self, market):
        """Save current price snapshot."""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO snapshots (
                ticker, ts, yes_bid, yes_ask, no_bid, no_ask,
                last_price, volume, open_interest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market.ticker,
            int(datetime.now().timestamp()),
            market.yes_bid,
            market.yes_ask,
            market.no_bid,
            market.no_ask,
            market.last_price,
            market.volume,
            market.open_interest
        ))

        self.conn.commit()

    async def collect_live_snapshots(self, duration_minutes: int = 60, interval_seconds: int = 60):
        """
        Collect live price snapshots for open markets.

        Args:
            duration_minutes: How long to collect data
            interval_seconds: How often to snapshot prices
        """
        auth = KalshiAuth()
        self.client = KalshiExchangeClient(auth)

        print(f"\n{'='*70}")
        print(f"Live data collection: {duration_minutes} minutes")
        print(f"Snapshot interval: {interval_seconds} seconds")
        print(f"{'='*70}\n")

        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        snapshot_count = 0

        while datetime.now() < end_time:
            print(f"\r⏱️  Snapshot {snapshot_count + 1}...", end="", flush=True)

            for city, series in self.series.items():
                try:
                    # Get open markets
                    markets = await self.client.get_markets(
                        series_ticker=series,
                        limit=50,
                        status="open"
                    )

                    for market in markets:
                        self._save_snapshot(market)

                except Exception as e:
                    print(f"\n   ⚠️  Error for {city}: {e}")

            snapshot_count += 1

            # Wait for next interval
            await asyncio.sleep(interval_seconds)

        await self.client.close()

        print(f"\n\n✓ Collected {snapshot_count} snapshots")

    def get_summary(self):
        """Print database summary."""
        cursor = self.conn.cursor()

        # Markets by city
        cursor.execute("""
            SELECT city, COUNT(*), COUNT(DISTINCT date)
            FROM markets
            GROUP BY city
        """)

        print(f"\n{'='*70}")
        print("Weather Markets Database Summary")
        print(f"{'='*70}\n")

        print("Markets by City:")
        for city, count, days in cursor.fetchall():
            print(f"  {city:12} {count:4} markets across {days} days")

        # Total snapshots
        cursor.execute("SELECT COUNT(*) FROM snapshots")
        total_snapshots = cursor.fetchone()[0]
        print(f"\nTotal price snapshots: {total_snapshots:,}")

        # Date range
        cursor.execute("SELECT MIN(date), MAX(date) FROM markets")
        min_date, max_date = cursor.fetchone()
        print(f"Date range: {min_date} to {max_date}")

        print(f"\n{'='*70}\n")


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Collect Kalshi weather market data")
    parser.add_argument("--db", default="data/weather_markets.db", help="Database path")
    parser.add_argument("--days", type=int, default=7, help="Days of history to collect")
    parser.add_argument("--live", type=int, help="Collect live snapshots for N minutes")
    parser.add_argument("--interval", type=int, default=60, help="Snapshot interval (seconds)")
    parser.add_argument("--summary", action="store_true", help="Show database summary")

    args = parser.parse_args()

    collector = WeatherMarketCollector(args.db)
    collector.init_database()

    if args.summary:
        collector.get_summary()
    elif args.live:
        await collector.collect_live_snapshots(args.live, args.interval)
    else:
        await collector.collect_markets(args.days)
        collector.get_summary()

    if collector.conn:
        collector.conn.close()


if __name__ == "__main__":
    asyncio.run(main())
