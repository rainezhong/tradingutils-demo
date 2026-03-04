#!/usr/bin/env python3
"""Debug version showing why each market is/isn't bet."""

import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.nba_underdog_strategy import NBAUnderdogConfig
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


async def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    # Initialize exchange
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    try:
        # Config
        config = NBAUnderdogConfig.moderate()
        config.min_time_until_close_mins = 0

        # Fetch markets
        markets = await exchange.get_markets(
            series_ticker="KXNBAGAME",
            status="open",
            limit=200
        )

        if isinstance(markets, dict):
            markets = markets.get("markets", [])

        logger.info(f"\n{'='*80}")
        logger.info(f"DEBUG: CHECKING ALL {len(markets)} MARKETS")
        logger.info(f"{'='*80}\n")

        qualifies = []
        reasons = {}

        for market in markets:
            ticker = market.ticker

            # Manual check
            if market.status != "open":
                reasons[ticker] = "not_open"
                continue

            yes_bid = market.yes_bid
            yes_ask = market.yes_ask
            no_bid = market.no_bid
            no_ask = market.no_ask

            if not all([yes_bid, yes_ask, no_bid, no_ask]):
                reasons[ticker] = "no_prices"
                continue

            yes_mid = (yes_bid + yes_ask) / 2
            no_mid = (no_bid + no_ask) / 2
            underdog_price = min(yes_mid, no_mid)

            if config.min_price_cents <= underdog_price <= config.max_price_cents:
                qualifies.append({
                    "ticker": ticker,
                    "title": market.title,
                    "underdog_price": underdog_price,
                    "event_ticker": market.event_ticker,
                })
                reasons[ticker] = f"QUALIFIES ({underdog_price:.0f}¢)"
            else:
                reasons[ticker] = f"price_out_of_range ({underdog_price:.0f}¢)"

        logger.info(f"\n✅ QUALIFYING MARKETS: {len(qualifies)}\n")
        for m in qualifies:
            logger.info(f"{m['title'][:60]}")
            logger.info(f"  Ticker: {m['ticker']}")
            logger.info(f"  Event: {m['event_ticker']}")
            logger.info(f"  Price: {m['underdog_price']:.0f}¢\n")

        logger.info(f"\n{'='*80}")
        logger.info(f"REASON BREAKDOWN")
        logger.info(f"{'='*80}\n")

        reason_counts = {}
        for reason in reasons.values():
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            logger.info(f"{reason}: {count}")

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
