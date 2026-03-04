#!/usr/bin/env python3
"""Check current NBA markets and timing windows."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


async def main():
    # Initialize exchange client
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        exchange = KalshiExchangeClient.from_env(demo=True)

    await exchange.connect()

    try:
        # Get NBA markets
        markets = await exchange.get_markets(series_ticker="KXNBAGAME", status="open")

        now = datetime.now(timezone.utc)

        print(f"\n{'='*100}")
        print(f"NBA MARKETS ANALYSIS ({len(markets)} markets)")
        print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'='*100}")
        print(f"{'Ticker':<45} {'Price':<8} {'Side':<8} {'Closes':<20} {'Hours Until':<12} {'Status':<15}")
        print(f"{'-'*100}")

        optimal_markets = []

        for market in markets:
            ticker = market.ticker
            yes_bid = market.yes_bid or 0
            yes_ask = market.yes_ask or 100
            no_bid = market.no_bid or 0
            no_ask = market.no_ask or 100
            close_time = market.close_time

            # Calculate hours until close
            hours_until = (close_time - now).total_seconds() / 3600

            # Determine underdog side and price
            yes_price = yes_ask
            no_price = no_ask

            if yes_price < no_price:
                underdog_side = "YES"
                underdog_price = yes_price
            else:
                underdog_side = "NO"
                underdog_price = no_price

            # Check if in optimal range
            in_price_range = 5 <= underdog_price <= 15
            in_time_range = 2 <= hours_until <= 5
            is_optimal = in_price_range and in_time_range

            if is_optimal:
                optimal_markets.append((ticker, underdog_side, underdog_price, hours_until))

            # Status indicator
            if is_optimal:
                status = "✓ OPTIMAL"
            elif in_price_range and hours_until > 5:
                status = "⏰ Too early"
            elif in_price_range and hours_until < 2:
                status = "⏰ Too late"
            elif in_time_range and underdog_price < 5:
                status = "💰 Too cheap"
            elif in_time_range and underdog_price > 15:
                status = "💰 Too expensive"
            else:
                status = "⊘ Outside range"

            print(
                f"{ticker:<45} {underdog_price:>4}¢    {underdog_side:<8} "
                f"{close_time.strftime('%Y-%m-%d %H:%M'):<20} {hours_until:>6.1f}h     {status:<15}"
            )

        print(f"{'-'*100}")
        print(f"\nOPTIMAL MARKETS (5-15¢, 2-5h window): {len(optimal_markets)}")

        if optimal_markets:
            print("\nReady to bet:")
            for ticker, side, price, hours in optimal_markets:
                print(f"  • {ticker} {side} @ {price}¢ ({hours:.1f}h until close)")
        else:
            print("  None - waiting for optimal entry conditions")

        print(f"\n{'='*100}\n")

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
