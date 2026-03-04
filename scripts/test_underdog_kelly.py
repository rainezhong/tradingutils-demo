#!/usr/bin/env python3
"""Test the NBA underdog strategy with Half Kelly sizing."""

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

    logger.info("=" * 80)
    logger.info("NBA UNDERDOG STRATEGY - HALF KELLY SIZING TEST")
    logger.info("=" * 80)

    # Parse args for bankroll
    bankroll = 1000.0
    if len(sys.argv) > 1:
        try:
            bankroll = float(sys.argv[1])
        except ValueError:
            logger.error(f"Invalid bankroll: {sys.argv[1]}")
            return 1

    # Create Kelly config
    config = NBAUnderdogConfig.kelly(bankroll=bankroll)
    config.min_time_until_close_mins = 0  # Allow betting on any open market

    logger.info(f"\nConfiguration:")
    logger.info(f"  Price range: {config.min_price_cents}-{config.max_price_cents}¢")
    logger.info(f"  Position sizing: Half Kelly")
    logger.info(f"  Bankroll: ${config.bankroll:.2f}")
    logger.info(f"  Kelly fraction: {config.kelly_fraction}")
    logger.info(f"  Max bet size: {config.max_kelly_bet_size} contracts")
    logger.info(f"  Max positions: {config.max_positions}")
    logger.info("=" * 80)

    # Initialize exchange
    logger.info("\nInitializing Kalshi exchange client...")
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
        logger.info("\nScanning markets with Half Kelly sizing...\n")
        bets_placed = await strategy.scan_and_bet()

        # Show results
        logger.info("\n" + "=" * 80)
        logger.info("HALF KELLY SIZING RESULTS")
        logger.info("=" * 80)
        logger.info(f"Bets that would be placed: {bets_placed}")

        if bets_placed > 0:
            # Show detailed bet breakdown
            logger.info("\n📋 DETAILED BET BREAKDOWN:")
            logger.info("=" * 80)

            total_investment = 0

            for bet in strategy.would_bet_log:
                price = bet['price']
                quantity = bet['quantity']
                investment = bet['investment']
                bucket = bet.get('bucket', f"{price}¢")

                total_investment += investment

                # Calculate Kelly metrics for display
                kelly_pct = (investment / bankroll) * 100

                logger.info(f"\n{bet['ticker']}")
                logger.info(f"  Side: {bet['side']}")
                logger.info(f"  Price: {price}¢ (bucket: {bucket})")
                logger.info(f"  Quantity: {quantity} contracts")
                logger.info(f"  Investment: ${investment:.2f}")
                logger.info(f"  % of Bankroll: {kelly_pct:.1f}%")

            # Overall summary
            logger.info("\n" + "=" * 80)
            logger.info("OVERALL SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total bets: {bets_placed}")
            logger.info(f"Total investment: ${total_investment:.2f}")
            logger.info(f"% of bankroll used: {(total_investment/bankroll)*100:.1f}%")
            logger.info(f"Remaining bankroll: ${bankroll - total_investment:.2f}")

            # Show expected performance by bucket
            logger.info("\n" + "=" * 80)
            logger.info("EXPECTED PERFORMANCE (from historical data)")
            logger.info("=" * 80)

            from strategies.nba_underdog_strategy import PerformanceTracker

            bucket_stats = {}
            for bet in strategy.would_bet_log:
                bucket = bet.get('bucket', '').replace('¢', '')
                if bucket not in bucket_stats:
                    bucket_stats[bucket] = {
                        'count': 0,
                        'investment': 0.0,
                        'expected_wr': PerformanceTracker.EXPECTED_WIN_RATES.get(bucket, 0.25)
                    }
                bucket_stats[bucket]['count'] += 1
                bucket_stats[bucket]['investment'] += bet['investment']

            for bucket, stats in sorted(bucket_stats.items()):
                wr = stats['expected_wr']
                inv = stats['investment']
                avg_price = inv / stats['count']
                expected_return = stats['count'] * wr * 1.0
                expected_profit = expected_return - inv
                expected_roi = (expected_profit / inv * 100) if inv > 0 else 0

                logger.info(f"\n{bucket}¢:")
                logger.info(f"  Bets: {stats['count']}")
                logger.info(f"  Expected win rate: {wr:.1%}")
                logger.info(f"  Investment: ${inv:.2f}")
                logger.info(f"  Expected return: ${expected_return:.2f}")
                logger.info(f"  Expected profit: ${expected_profit:.2f}")
                logger.info(f"  Expected ROI: {expected_roi:.1f}%")

        else:
            logger.info("\nNo qualifying markets found in target price range.")
            logger.info("This could mean:")
            logger.info("  1. No NBA games currently active")
            logger.info("  2. No underdogs priced in 10-30¢ range (excluding 20-25¢)")
            logger.info("  3. All markets already positioned or in cooldown")

        logger.info("\n" + "=" * 80)

    except Exception as e:
        logger.error(f"Error during scan: {e}", exc_info=True)
        return 1
    finally:
        await exchange.exit()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
