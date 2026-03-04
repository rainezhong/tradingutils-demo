#!/usr/bin/env python3
"""
Simple Kalshi Weather Market Data Collector

Collects specific weather market tickers directly.
Based on web search, these tickers exist:
- KXHIGHNY-26FEB27 (NYC high temp for Feb 27)
- KXHIGHCHI (Chicago)
- KXHIGHLAX (LA)
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


async def main():
    """Test collection of weather markets."""

    # Initialize client
    try:
        api_key, private_key = get_credentials_from_env()
        auth = KalshiAuth(api_key, private_key)
        client = KalshiExchangeClient(auth)
    except Exception as e:
        print(f"⚠️  Could not initialize Kalshi client: {e}")
        print("\nNote: You need KALSHI_API_KEY and KALSHI_API_SECRET environment variables.")
        print("See: https://kalshi.com/profile/api-keys")
        return

    print("✓ Connected to Kalshi API\n")

    # Try to get markets without series filter
    print("Fetching open markets (no filter)...")
    try:
        all_markets = await client.get_markets(status="open", limit=20)
        print(f"✓ Found {len(all_markets)} open markets\n")

        # Filter for weather/temperature markets
        weather_markets = []
        for m in all_markets:
            if any(x in m.ticker.upper() for x in ["HIGH", "LOW", "TEMP", "WEATHER"]):
                weather_markets.append(m)
                print(f"  🌡️  {m.ticker}: {m.subtitle}")

        if weather_markets:
            print(f"\n✓ Found {len(weather_markets)} weather-related markets")

            # Save to simple JSON file
            output_file = "data/weather_markets_sample.json"
            with open(output_file, "w") as f:
                json.dump([
                    {
                        "ticker": m.ticker,
                        "series": m.series_ticker,
                        "subtitle": m.subtitle,
                        "yes_bid": m.yes_bid,
                        "yes_ask": m.yes_ask,
                        "last_price": m.last_price,
                        "volume": m.volume,
                        "status": m.status
                    }
                    for m in weather_markets
                ], f, indent=2)

            print(f"\n✓ Saved to {output_file}")
        else:
            print("\n⚠️  No weather markets found in open markets")
            print("\nSample of what's available:")
            for m in all_markets[:5]:
                print(f"  - {m.ticker}")

    except Exception as e:
        print(f"⚠️  Error fetching markets: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
