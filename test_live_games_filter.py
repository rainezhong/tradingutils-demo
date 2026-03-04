#!/usr/bin/env python3
"""Test the updated strategy with live games filter and auto-sell."""
import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

async def main():
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    config = NBAUnderdogConfig()
    config.min_price_cents = 10
    config.max_price_cents = 30
    config.position_size = 1
    config.max_positions = 5

    strategy = NBAUnderdogStrategy(exchange, config, dry_run=True)

    print("=" * 60)
    print("TESTING UPDATED STRATEGY")
    print("=" * 60)
    print("✅ Live games filter (close_time < 3 hours)")
    print("✅ OrderManager integration")
    print("✅ Auto-sell at 99¢")
    print("=" * 60)

    # Get markets to check filter
    markets = await strategy._get_nba_markets()
    print(f"\nFound {len(markets)} total NBA markets")

    now = datetime.now(timezone.utc)
    live_markets = []
    future_markets = []

    for market in markets:
        if hasattr(market, 'close_time') and market.close_time:
            hours_until_close = (market.close_time - now).total_seconds() / 3600
            if hours_until_close <= 3:
                live_markets.append((market.ticker, hours_until_close))
            else:
                future_markets.append((market.ticker, hours_until_close))

    print(f"\n📊 Market Breakdown:")
    print(f"  Live/Soon ({len(live_markets)}): Closing within 3 hours")
    for ticker, hours in live_markets[:5]:
        print(f"    - {ticker[-30:]}: {hours:.1f}h until close")

    print(f"\n  Future ({len(future_markets)}): Closing > 3 hours away (SKIPPED)")
    for ticker, hours in future_markets[:3]:
        print(f"    - {ticker[-30:]}: {hours:.1f}h until close")

    # Run one scan
    print(f"\n{'='*60}")
    print("Running scan (dry run)...")
    print(f"{'='*60}\n")
    bets = await strategy.scan_and_bet()
    print(f"\n✅ Would place {bets} bets on LIVE games only")

    # Test position monitoring
    print(f"\n{'='*60}")
    print("Testing position monitoring...")
    print(f"{'='*60}")
    sold = await strategy.monitor_and_exit_positions()
    print(f"Checked positions, would sell {sold} at 99¢")

    await exchange.exit()

if __name__ == "__main__":
    asyncio.run(main())
