#!/usr/bin/env python3
"""Test that orders fill immediately with buy_max_cost."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

logging.basicConfig(level=logging.INFO, format='%(message)s')

async def main():
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    config = NBAUnderdogConfig()
    config.min_price_cents = 10
    config.max_price_cents = 30
    config.position_size = 1
    config.max_positions = 5

    strategy = NBAUnderdogStrategy(exchange, config, dry_run=False)

    print("=" * 60)
    print("Testing immediate order fills with buy_max_cost")
    print("=" * 60)

    # Place bets
    bets_placed = await strategy.scan_and_bet()
    print(f"\n✅ Placed {bets_placed} bets")

    # Check order status
    print("\nChecking order statuses...")
    response = await exchange._request("GET", "/portfolio/orders?status=filled")
    filled = response.get("orders", [])
    filled_nba = [o for o in filled if "KXNBAGAME" in o.get("ticker", "")]

    response = await exchange._request("GET", "/portfolio/orders?status=resting")
    resting = response.get("orders", [])
    resting_nba = [o for o in resting if "KXNBAGAME" in o.get("ticker", "")]

    print(f"\n📊 Order Status:")
    print(f"  FILLED: {len(filled_nba)} NBA orders")
    print(f"  RESTING: {len(resting_nba)} NBA orders")

    if resting_nba:
        print(f"\n⚠️  WARNING: {len(resting_nba)} orders still resting (not filled)")
        for order in resting_nba[:3]:
            print(f"    - {order['ticker']}: {order.get('side')} @ {order.get('yes_price') or order.get('no_price')}¢")
    else:
        print("\n✅ All orders filled immediately!")

    await exchange.exit()

if __name__ == "__main__":
    asyncio.run(main())
