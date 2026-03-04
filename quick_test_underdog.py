#!/usr/bin/env python3
"""Quick one-shot test of NBA underdog strategy."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

logging.basicConfig(level=logging.INFO)

async def main():
    # Initialize
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    # Config for tiny account
    config = NBAUnderdogConfig()
    config.min_price_cents = 10
    config.max_price_cents = 30
    config.position_size = 1
    config.max_positions = 5

    # Run strategy (LIVE, not dry run!)
    strategy = NBAUnderdogStrategy(exchange, config, dry_run=False)
    print("=" * 60)
    print("NBA UNDERDOG STRATEGY - LIVE TEST")
    print(f"Position size: 1 contract")
    print(f"Price range: 10-30¢ (excludes 20-25¢)")
    print(f"Max positions: 5")
    print("=" * 60)

    bets = await strategy.scan_and_bet()
    print(f"\n✅ Placed {bets} bets")

    # Show status
    status = strategy.get_status()
    print("\n" + status)

    await exchange.exit()

if __name__ == "__main__":
    asyncio.run(main())
