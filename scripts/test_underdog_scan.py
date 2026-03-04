#!/usr/bin/env python3
"""Test the NBA underdog strategy with a single scan."""

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
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Use moderate config with reduced time filter
    config = NBAUnderdogConfig.moderate()
    config.min_time_until_close_mins = 0  # Allow betting on any open market

    logger.info("=" * 70)
    logger.info("NBA UNDERDOG STRATEGY - DRY RUN TEST")
    logger.info("=" * 70)
    logger.info(f"Price range: {config.min_price_cents}-{config.max_price_cents}¢")
    logger.info(f"Position size: {config.position_size} contracts")
    logger.info(f"Max positions: {config.max_positions}")
    logger.info("=" * 70)

    # Initialize exchange (from environment or demo)
    logger.info("Initializing Kalshi exchange client...")
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        logger.info("Production credentials not found, trying demo...")
        exchange = KalshiExchangeClient.from_env(demo=True)
    await exchange.connect()

    try:
        # Create strategy in DRY RUN mode
        strategy = NBAUnderdogStrategy(exchange, config, dry_run=True)

        # Do single scan
        logger.info("\nScanning markets...")
        bets_placed = await strategy.scan_and_bet()

        # Show results
        logger.info("\n" + "=" * 70)
        logger.info("DRY RUN RESULTS")
        logger.info("=" * 70)
        logger.info(f"Bets that would be placed: {bets_placed}")

        if bets_placed > 0:
            # Show detailed bet breakdown
            logger.info("\n📋 DETAILED BET BREAKDOWN:")
            logger.info("=" * 70)

            total_investment = 0
            by_price_range = {"10-20¢": [], "20-30¢": []}

            for bet in strategy.would_bet_log:
                price = bet['price']
                if 10 <= price < 20:
                    by_price_range["10-20¢"].append(bet)
                elif 20 <= price <= 30:
                    by_price_range["20-30¢"].append(bet)

                total_investment += bet['investment']

                logger.info(f"\n{bet['ticker']}")
                logger.info(f"  Side: {bet['side']}")
                logger.info(f"  Quantity: {bet['quantity']} contracts")
                logger.info(f"  Price: {bet['price']}¢")
                logger.info(f"  Investment: ${bet['investment']:.2f}")
                logger.info(f"  Type: {bet['type']}")

            # Summary by price range
            logger.info("\n" + "=" * 70)
            logger.info("SUMMARY BY PRICE RANGE")
            logger.info("=" * 70)

            for range_name, bets in by_price_range.items():
                if bets:
                    count = len(bets)
                    investment = sum(b['investment'] for b in bets)

                    if range_name == "10-20¢":
                        ev = 8.57  # cents per dollar
                        expected_profit = investment * ev / 100
                    else:  # 20-30¢
                        ev = 4.98
                        expected_profit = investment * ev / 100

                    logger.info(f"\n{range_name}: {count} bets")
                    logger.info(f"  Investment: ${investment:.2f}")
                    logger.info(f"  Expected EV: +{ev}¢ per $1")
                    logger.info(f"  Expected profit: ${expected_profit:.2f}")

            # Overall summary
            logger.info("\n" + "=" * 70)
            logger.info("OVERALL SUMMARY")
            logger.info("=" * 70)
            logger.info(f"Total bets: {bets_placed}")
            logger.info(f"Total investment: ${total_investment:.2f}")

            # Calculate weighted average EV
            weighted_ev = 0
            for range_name, bets in by_price_range.items():
                if bets:
                    investment = sum(b['investment'] for b in bets)
                    ev = 8.57 if range_name == "10-20¢" else 4.98
                    weighted_ev += (investment / total_investment) * ev

            expected_profit = total_investment * weighted_ev / 100
            logger.info(f"Weighted avg EV: +{weighted_ev:.2f}¢ per $1")
            logger.info(f"Expected profit: ${expected_profit:.2f}")
            logger.info(f"Expected ROI: {(expected_profit / total_investment) * 100:.1f}%")

        else:
            logger.info("\nNo qualifying markets found in target price range.")
            logger.info("This could mean:")
            logger.info("  1. No NBA games currently active")
            logger.info("  2. No underdogs priced in 10-30¢ range")
            logger.info("  3. All markets already positioned or in cooldown")

        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"Error during scan: {e}", exc_info=True)
        return 1
    finally:
        await exchange.exit()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
