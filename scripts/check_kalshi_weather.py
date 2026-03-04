#!/usr/bin/env python3
"""Check what weather markets exist on Kalshi."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient
from core.exchange_client.kalshi.kalshi_auth import KalshiAuth, get_credentials_from_env


async def main():
    """Check for weather markets."""

    try:
        api_key, private_key = get_credentials_from_env()
        auth = KalshiAuth(api_key, private_key)
        client = KalshiExchangeClient(auth)
    except Exception as e:
        print(f"⚠️  Error: {e}")
        return

    print("Checking Kalshi for weather markets...\n")

    # Try different status values
    for status in ["open", "closed", "settled", "all"]:
        print(f"\n{'='*60}")
        print(f"Status: {status}")
        print(f"{'='*60}")

        try:
            markets = await client.get_markets(status=status, limit=100)
            print(f"Total markets: {len(markets)}")

            # Look for weather-related
            weather_keywords = ["HIGH", "LOW", "TEMP", "WEATHER", "SNOW", "RAIN", "CLIMATE"]
            weather_markets = []

            for m in markets:
                for keyword in weather_keywords:
                    if keyword in m.ticker.upper() or keyword in m.subtitle.upper():
                        weather_markets.append(m)
                        break

            if weather_markets:
                print(f"\n🌡️  Found {len(weather_markets)} weather markets:")
                for m in weather_markets[:10]:
                    print(f"  - {m.ticker}")
                    print(f"    {m.subtitle}")
                    print(f"    Status: {m.status}, Series: {m.series_ticker}")
            else:
                print(f"⚠️  No weather markets found")

        except Exception as e:
            print(f"⚠️  Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
