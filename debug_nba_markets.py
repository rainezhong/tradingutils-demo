#!/usr/bin/env python3
"""Debug NBA market timing data."""

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
        markets = await exchange.get_markets(series_ticker="KXNBAGAME", status="open", limit=5)

        now = datetime.now(timezone.utc)

        print(f"\nCurrent time: {now}")
        print(f"\nDEBUG: First 3 markets raw data:\n")

        for i, market in enumerate(markets[:3]):
            print(f"Market {i+1}: {market.ticker}")
            print(f"  Raw market object type: {type(market)}")
            print(f"  Available attributes: {[attr for attr in dir(market) if not attr.startswith('_')]}")
            print(f"  close_time: {market.close_time}")
            print(f"  close_time type: {type(market.close_time)}")

            # Check for other time fields
            if hasattr(market, 'expiration_time'):
                print(f"  expiration_time: {market.expiration_time}")
            if hasattr(market, 'settlement_time'):
                print(f"  settlement_time: {market.settlement_time}")
            if hasattr(market, 'open_time'):
                print(f"  open_time: {market.open_time}")
            if hasattr(market, 'event_ticker'):
                print(f"  event_ticker: {market.event_ticker}")
            if hasattr(market, 'subtitle'):
                print(f"  subtitle: {market.subtitle}")
            if hasattr(market, 'title'):
                print(f"  title: {market.title}")

            print()

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
