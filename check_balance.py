#!/usr/bin/env python3
"""Quick script to check Kalshi account balance."""

import asyncio
import sys
from pathlib import Path

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
        # Get balance
        balance = await exchange.get_balance()
        print(f"Balance object: {balance}")
        print(f"Balance type: {type(balance)}")
        print(f"Balance attributes: {dir(balance)}")

        # Try to get the actual balance value
        if hasattr(balance, 'balance'):
            print(f"\nCurrent Kalshi Balance: ${balance.balance:,.2f}")
        elif hasattr(balance, 'amount'):
            print(f"\nCurrent Kalshi Balance: ${balance.amount:,.2f}")
        else:
            print(f"\nBalance: {balance}")

        return balance
    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
