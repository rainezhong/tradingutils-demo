#!/usr/bin/env python3
"""Cancel all resting NBA orders."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

async def main():
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    # Get all orders
    response = await exchange._request("GET", "/portfolio/orders?status=resting")
    orders = response.get("orders", [])

    print(f"Found {len(orders)} resting orders")

    for order in orders:
        ticker = order.get("ticker", "")
        if "KXNBAGAME" in ticker:  # Only cancel NBA orders
            order_id = order["order_id"]
            print(f"Cancelling {ticker} order {order_id}...")
            try:
                await exchange.cancel_order(order_id)
                print(f"  ✅ Cancelled")
            except Exception as e:
                print(f"  ❌ Error: {e}")

    await exchange.exit()

if __name__ == "__main__":
    asyncio.run(main())
