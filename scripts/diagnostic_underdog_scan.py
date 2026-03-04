#!/usr/bin/env python3
"""Diagnostic scan showing all NBA markets and why they qualify/don't qualify."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    logger = logging.getLogger(__name__)

    # Initialize exchange
    logger.info("Connecting to Kalshi...")
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        exchange = KalshiExchangeClient.from_env(demo=True)
    await exchange.connect()

    try:
        # Fetch markets
        markets = await exchange.get_markets(
            series_ticker="KXNBAGAME",
            status="open",
            limit=200
        )

        if isinstance(markets, dict):
            markets = markets.get("markets", [])

        logger.info("\n" + "=" * 80)
        logger.info(f"FOUND {len(markets)} NBA MARKETS")
        logger.info("=" * 80)

        if not markets:
            logger.info("\nNo NBA markets currently open.")
            return

        # Analyze each market
        qualifying = []
        non_qualifying = []

        for market in markets:
            ticker = market.ticker
            yes_bid = market.yes_bid
            yes_ask = market.yes_ask
            no_bid = market.no_bid
            no_ask = market.no_ask

            if not all([yes_bid, yes_ask, no_bid, no_ask]):
                continue

            yes_mid = (yes_bid + yes_ask) / 2
            no_mid = (no_bid + no_ask) / 2

            underdog_price = min(yes_mid, no_mid)
            favorite_price = max(yes_mid, no_mid)
            underdog_side = "YES" if yes_mid < no_mid else "NO"

            # Check if qualifies for 10-30¢ range
            qualifies = 10 <= underdog_price <= 30

            market_info = {
                "ticker": ticker,
                "title": market.title,
                "underdog_price": underdog_price,
                "favorite_price": favorite_price,
                "underdog_side": underdog_side,
                "yes_mid": yes_mid,
                "no_mid": no_mid,
                "status": market.status,
            }

            if qualifies:
                qualifying.append(market_info)
            else:
                non_qualifying.append(market_info)

        # Show qualifying markets
        logger.info(f"\n✅ QUALIFYING MARKETS (10-30¢ underdogs): {len(qualifying)}")
        logger.info("=" * 80)
        if qualifying:
            for m in qualifying:
                logger.info(f"\n{m['title'][:60]}")
                logger.info(f"  Ticker: {m['ticker']}")
                logger.info(f"  Underdog: {m['underdog_side']} @ {m['underdog_price']:.0f}¢")
                logger.info(f"  Favorite: {'NO' if m['underdog_side'] == 'YES' else 'YES'} @ {m['favorite_price']:.0f}¢")
                logger.info(f"  Spread: YES {m['yes_mid']:.0f}¢ / NO {m['no_mid']:.0f}¢")
        else:
            logger.info("\nNone found in target range (10-30¢)")

        # Show sample of non-qualifying markets
        logger.info(f"\n\n❌ NON-QUALIFYING MARKETS: {len(non_qualifying)}")
        logger.info("=" * 80)
        logger.info("(Showing first 10 as examples)\n")

        for m in non_qualifying[:10]:
            reason = []
            if m['underdog_price'] < 10:
                reason.append(f"too cheap ({m['underdog_price']:.0f}¢ < 10¢)")
            elif m['underdog_price'] > 30:
                reason.append(f"too expensive ({m['underdog_price']:.0f}¢ > 30¢)")

            logger.info(f"{m['title'][:55]}")
            logger.info(f"  {m['underdog_side']} @ {m['underdog_price']:.0f}¢ | Reason: {', '.join(reason)}")

        # Price distribution
        logger.info("\n\n📊 PRICE DISTRIBUTION")
        logger.info("=" * 80)

        buckets = {
            "0-10¢": 0,
            "10-20¢": 0,
            "20-30¢": 0,
            "30-40¢": 0,
            "40-50¢": 0,
        }

        for m in markets:
            yes_mid = (m.yes_bid + m.yes_ask) / 2
            no_mid = (m.no_bid + m.no_ask) / 2
            underdog_price = min(yes_mid, no_mid)

            if underdog_price < 10:
                buckets["0-10¢"] += 1
            elif underdog_price < 20:
                buckets["10-20¢"] += 1
            elif underdog_price < 30:
                buckets["20-30¢"] += 1
            elif underdog_price < 40:
                buckets["30-40¢"] += 1
            else:
                buckets["40-50¢"] += 1

        for bucket, count in buckets.items():
            bar = "█" * (count * 2)
            logger.info(f"{bucket:>10}: {bar} ({count} markets)")

        logger.info("\n" + "=" * 80)

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
