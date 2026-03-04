#!/usr/bin/env python3
"""Investigate Kalshi NBA market structure."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


async def main():
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        exchange = KalshiExchangeClient.from_env(demo=True)

    await exchange.connect()

    try:
        # Get NBA markets
        markets = await exchange.get_markets(series_ticker="KXNBAGAME", status="open", limit=50)

        print(f"\n{'='*120}")
        print(f"KALSHI NBA MARKET INVESTIGATION")
        print(f"{'='*120}\n")

        # Get detailed info for a few markets
        sample_tickers = [m.ticker for m in markets[:5]]

        for ticker in sample_tickers:
            # Get full market details from API
            response = await exchange._client.get(f"/trade-api/v2/markets/{ticker}")

            # Debug response
            print(f"Response status: {response.status_code}")
            print(f"Response content type: {response.headers.get('content-type')}")
            print(f"Response text (first 500 chars): {response.text[:500]}")

            try:
                market_data = response.json()
                market = market_data.get('market', {})
            except Exception as e:
                print(f"Failed to parse JSON: {e}")
                continue

            print(f"{'='*120}")
            print(f"TICKER: {ticker}")
            print(f"{'='*120}")
            print(f"Title: {market.get('title', 'N/A')}")
            print(f"Subtitle: {market.get('subtitle', 'N/A')}")
            print(f"Category: {market.get('category', 'N/A')}")
            print(f"Status: {market.get('status', 'N/A')}")
            print(f"\nTIMING:")
            print(f"  Open time:       {market.get('open_time', 'N/A')}")
            print(f"  Close time:      {market.get('close_time', 'N/A')}")
            print(f"  Expiration time: {market.get('expiration_time', 'N/A')}")
            print(f"  Settlement time: {market.get('expected_expiration_time', 'N/A')}")
            print(f"\nRULES:")
            print(f"  {market.get('rules', 'N/A')[:500]}")
            print(f"\nPRICING:")
            print(f"  Yes bid/ask: {market.get('yes_bid', 0)}¢ / {market.get('yes_ask', 0)}¢")
            print(f"  No bid/ask:  {market.get('no_bid', 0)}¢ / {market.get('no_ask', 0)}¢")
            print(f"  Volume: {market.get('volume', 0)}, Open Interest: {market.get('open_interest', 0)}")
            print(f"\nOTHER:")
            print(f"  Can close early: {market.get('can_close_early', 'N/A')}")
            print(f"  Floor/Cap: {market.get('floor_strike', 'N/A')} / {market.get('cap_strike', 'N/A')}")
            print(f"  Result: {market.get('result', 'N/A')}")
            print()

        # Check what series are available
        print(f"\n{'='*120}")
        print("CHECKING FOR OTHER NBA SERIES")
        print(f"{'='*120}\n")

        # Try different series patterns
        series_to_try = [
            "KXNBAGAME",
            "NBAWIN",
            "NBA",
            "BASKETBALL",
            "KXNBA",
        ]

        for series in series_to_try:
            try:
                test_markets = await exchange.get_markets(series_ticker=series, status="open", limit=5)
                if test_markets:
                    print(f"✓ {series}: {len(test_markets)} open markets")
                else:
                    print(f"✗ {series}: No markets found")
            except Exception as e:
                print(f"✗ {series}: Error - {e}")

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
