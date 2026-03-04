#!/usr/bin/env python3
"""Download historical Bitcoin trade data from Binance public API.

Downloads 1-minute klines (OHLCV) which we can convert to 5-second windows
by sampling trades. Much faster than collecting live data.

Usage:
    # Download 1 year of BTC/USDT data
    python3 scripts/download_binance_historical.py --months 12 --output data/btc_historical_1year.db

    # Download 3 months
    python3 scripts/download_binance_historical.py --months 3 --output data/btc_historical_3months.db
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


def create_database(db_path: str) -> None:
    """Create database schema for historical Bitcoin data."""
    with sqlite3.connect(db_path) as conn:
        # Simplified schema - just trades (no L2 for historical)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS binance_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                exchange_ts REAL,
                price REAL NOT NULL,
                qty REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_binance_trade_ts
            ON binance_trades(ts)
        """)

        conn.commit()

    print(f"✅ Created database: {db_path}")


def download_klines(symbol: str, interval: str, start_time: int, end_time: int) -> list:
    """Download klines from Binance public API.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        interval: Candle interval (e.g., "1m", "5m")
        start_time: Start timestamp (milliseconds)
        end_time: End timestamp (milliseconds)

    Returns:
        List of klines: [open_time, open, high, low, close, volume, ...]
    """
    # Use data-stream.binance.vision to avoid geo-blocking (HTTP 451)
    url = "https://data-api.binance.vision/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": 1000  # Max per request
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    return response.json()


def kline_to_trades(kline: list, base_ts: float) -> list:
    """Convert 1-minute kline to simulated 5-second trades.

    Approximates 12 trades per minute (one every 5 seconds) by interpolating
    the OHLC prices. Not perfect but good enough for regime detection.

    Args:
        kline: [open_time, open, high, low, close, volume, ...]
        base_ts: Base timestamp in seconds

    Returns:
        List of (ts, price, qty) tuples
    """
    open_price = float(kline[1])
    high_price = float(kline[2])
    low_price = float(kline[3])
    close_price = float(kline[4])
    volume = float(kline[5])

    # Simulate 12 trades (every 5 seconds) with OHLC interpolation
    trades = []
    prices = [
        open_price,
        (open_price + high_price) / 2,
        high_price,
        (high_price + low_price) / 2,
        low_price,
        (low_price + close_price) / 2,
        close_price,
        close_price,
        close_price,
        close_price,
        close_price,
        close_price,
    ]

    qty_per_trade = volume / 12.0

    for i, price in enumerate(prices):
        ts = base_ts + (i * 5.0)  # Every 5 seconds
        trades.append((ts, price, qty_per_trade))

    return trades


def download_historical_data(
    symbol: str,
    months: int,
    db_path: str,
    interval: str = "1m"
) -> None:
    """Download historical Bitcoin data and store in database.

    Args:
        symbol: Trading pair (default: "BTCUSDT")
        months: How many months of history to download
        db_path: Output database path
        interval: Kline interval (default: "1m")
    """
    print("="*70)
    print("BINANCE HISTORICAL DATA DOWNLOAD")
    print("="*70)
    print(f"Symbol: {symbol}")
    print(f"Duration: {months} months")
    print(f"Interval: {interval}")
    print(f"Output: {db_path}")
    print()

    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=months * 30)

    print(f"Time range: {start_time} to {end_time}")
    print()

    # Create database
    create_database(db_path)

    # Download in chunks (1000 klines per request = ~16 hours for 1m interval)
    current_time = start_time
    total_trades = 0
    batch_size = 1000  # klines per request

    with sqlite3.connect(db_path) as conn:
        while current_time < end_time:
            # Calculate chunk end (max 1000 klines)
            chunk_start_ms = int(current_time.timestamp() * 1000)
            chunk_end = min(
                current_time + timedelta(minutes=batch_size),
                end_time
            )
            chunk_end_ms = int(chunk_end.timestamp() * 1000)

            try:
                # Download klines
                klines = download_klines(symbol, interval, chunk_start_ms, chunk_end_ms)

                if not klines:
                    break

                # Convert to trades and insert
                trades_batch = []
                for kline in klines:
                    open_time_ms = kline[0]
                    base_ts = open_time_ms / 1000.0

                    # Convert kline to simulated trades
                    trades = kline_to_trades(kline, base_ts)
                    trades_batch.extend(trades)

                # Insert batch
                conn.executemany(
                    "INSERT INTO binance_trades (ts, exchange_ts, price, qty) VALUES (?, ?, ?, ?)",
                    [(t[0], t[0], t[1], t[2]) for t in trades_batch]
                )
                conn.commit()

                total_trades += len(trades_batch)

                # Progress
                pct = ((current_time - start_time) / (end_time - start_time)) * 100
                print(f"  [{current_time.strftime('%Y-%m-%d')}] Downloaded {len(klines)} klines → {len(trades_batch)} trades | Total: {total_trades:,} | {pct:.1f}%", end='\r')

                # Move to next chunk
                if len(klines) < batch_size:
                    break

                current_time = datetime.fromtimestamp(klines[-1][0] / 1000.0) + timedelta(minutes=1)

                # Rate limit (Binance allows 1200 requests/min)
                time.sleep(0.1)

            except Exception as e:
                print(f"\n❌ Error downloading chunk: {e}")
                break

    print()
    print()
    print("="*70)
    print("DOWNLOAD COMPLETE")
    print("="*70)
    print(f"Total trades: {total_trades:,}")
    print(f"Database: {db_path}")

    # Show stats
    with sqlite3.connect(db_path) as conn:
        stats = conn.execute("""
            SELECT
                COUNT(*) as count,
                datetime(MIN(ts), 'unixepoch') as start,
                datetime(MAX(ts), 'unixepoch') as end,
                AVG(price) as avg_price,
                SUM(qty) as total_volume
            FROM binance_trades
        """).fetchone()

        if stats[0] > 0:
            print(f"Time range: {stats[1]} to {stats[2]}")
            print(f"Avg price: ${stats[3]:,.2f}")
            print(f"Total volume: {stats[4]:,.2f} BTC")
        else:
            print("No data downloaded")

    print()
    print("Next step: Train HMM on this historical data")
    print()
    print(f"  python3 scripts/train_crypto_regime_hmm.py \\")
    print(f"      --db {db_path} \\")
    print(f"      --states 3 \\")
    print(f"      --bic \\")
    print(f"      --output models/crypto_regime_hmm_historical.pkl")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Download historical Bitcoin data from Binance"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=12,
        help="Number of months to download (default: 12)"
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Trading pair (default: BTCUSDT)"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output database path"
    )

    args = parser.parse_args()

    try:
        download_historical_data(
            symbol=args.symbol,
            months=args.months,
            db_path=args.output,
        )
        print("✅ Success!")
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
