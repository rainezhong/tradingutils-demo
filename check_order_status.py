#!/usr/bin/env python3
"""Check status of all NBA orders."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

async def main():
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    # Get all orders (all statuses)
    response = await exchange._request("GET", "/portfolio/orders")
    all_orders = response.get("orders", [])
    nba_orders = [o for o in all_orders if "KXNBAGAME" in o.get("ticker", "")]

    print(f"Total NBA orders: {len(nba_orders)}\n")

    # Group by status
    by_status = {}
    for order in nba_orders:
        status = order.get("status", "unknown")
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(order)

    for status, orders in sorted(by_status.items()):
        print(f"\n{status.upper()}: {len(orders)} orders")
        for order in orders[:5]:  # Show first 5
            ticker = order.get("ticker", "")
            side = order.get("side", "")
            price = order.get("yes_price") or order.get("no_price")
            count = order.get("remaining_count", order.get("count", 0))
            print(f"  - {ticker[-20:]}: {side} @ {price}¢, {count} contracts")

    await exchange.exit()

if __name__ == "__main__":
    asyncio.run(main())
