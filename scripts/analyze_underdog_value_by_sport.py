#!/usr/bin/env python3
"""Analyze underdog value across different sports on Kalshi.

Fetches settled markets from Kalshi API and analyzes whether underdogs
are systematically undervalued in different sports.

Usage:
    python3 scripts/analyze_underdog_value_by_sport.py --sport NCAAB
    python3 scripts/analyze_underdog_value_by_sport.py --sport NBA --days 30
    python3 scripts/analyze_underdog_value_by_sport.py --all-sports
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


# Sport series mappings
SPORT_SERIES = {
    'NBA': 'KXNBAGAME',
    'NCAAB': 'KXNCAAMBGAME',
    'NCAAWB': 'KXNCAAWBGAME',
    'NHL': 'KXNHLGAME',
    'AHL': 'KXAHLGAME',
    'KHL': 'KXKHLGAME',
    'SHL': 'KXSHLGAME',
    'EPL': 'KXEPLGAME',
    'LALIGA': 'KXLALIGAGAME',
    'SERIEA': 'KXSERIEAGAME',
    'BUNDESLIGA': 'KXBUNDESLIGAGAME',
    'LIGUE1': 'KXLIGUE1GAME',
    'MLB': 'KXMLBGAME',
    'NFL': 'KXNFLGAME',
    'NCAAFB': 'KXNCAAFBGAME',
}


@dataclass
class UnderdogResult:
    """Result of betting on an underdog."""
    ticker: str
    sport: str
    underdog_price: float
    won: bool
    result: str
    close_time: datetime


@dataclass
class BucketStats:
    """Statistics for a price bucket."""
    bucket_name: str
    n_games: int
    win_rate: float
    avg_price: float
    implied_prob: float
    edge: float  # win_rate - implied_prob
    ev_per_dollar: float  # Expected value per $1 bet
    total_invested: float
    total_return: float
    profit: float
    roi: float


class UnderdogAnalyzer:
    """Analyzes underdog value for different sports."""

    def __init__(self, client: KalshiExchangeClient):
        """Initialize analyzer.

        Args:
            client: Kalshi exchange client
        """
        self.client = client

    async def fetch_settled_markets(
        self,
        series_ticker: str,
        days_back: int = 30,
        limit: int = 1000
    ) -> List[Dict]:
        """Fetch settled markets for a series.

        Args:
            series_ticker: Series ticker (e.g., 'KXNBAGAME')
            days_back: How many days back to fetch
            limit: Max markets to fetch

        Returns:
            List of settled market dicts
        """
        logger.info(f"Fetching settled {series_ticker} markets from last {days_back} days...")

        try:
            # Fetch settled markets
            response = await self.client.get_markets(
                series_ticker=series_ticker,
                status="settled",
                limit=limit
            )

            if isinstance(response, list):
                markets = response
            elif isinstance(response, dict):
                markets = response.get("markets", [])
            else:
                markets = []

            # Filter by date (make timezone-aware)
            from datetime import timezone as tz
            cutoff = datetime.now(tz.utc) - timedelta(days=days_back)
            recent_markets = []

            for market in markets:
                close_time = market.close_time if hasattr(market, 'close_time') else market.get('close_time')
                if close_time:
                    if isinstance(close_time, str):
                        close_time = datetime.fromisoformat(close_time.replace('Z', '+00:00'))

                    if close_time >= cutoff:
                        recent_markets.append(market)

            logger.info(f"Found {len(recent_markets)} settled markets in date range")
            return recent_markets

        except Exception as e:
            logger.error(f"Error fetching markets: {e}", exc_info=True)
            return []

    def analyze_underdogs(
        self,
        markets: List[Dict],
        sport: str,
        min_price: float = 10.0,
        max_price: float = 40.0
    ) -> List[UnderdogResult]:
        """Analyze underdog results from settled markets.

        Args:
            markets: List of settled markets
            sport: Sport name
            min_price: Minimum underdog price
            max_price: Maximum underdog price

        Returns:
            List of UnderdogResult objects
        """
        # Group markets by game (event_ticker)
        games = defaultdict(list)
        for market in markets:
            event_ticker = getattr(market, 'event_ticker', None)
            if event_ticker:
                games[event_ticker].append(market)

        results = []

        for event_ticker, game_markets in games.items():
            # Each game should have 2 markets (one per team)
            if len(game_markets) != 2:
                continue

            market1, market2 = game_markets

            # Get settlement results (YES bid/ask at 99-100 means YES won)
            yes_bid1 = getattr(market1, 'yes_bid', 0)
            yes_ask1 = getattr(market1, 'yes_ask', 0)
            yes_bid2 = getattr(market2, 'yes_bid', 0)
            yes_ask2 = getattr(market2, 'yes_ask', 0)

            # Determine which market won
            market1_won = (yes_bid1 >= 95 and yes_ask1 >= 95)
            market2_won = (yes_bid2 >= 95 and yes_ask2 >= 95)

            # Skip if unclear settlement
            if not (market1_won or market2_won) or (market1_won and market2_won):
                continue

            # CRITICAL: Determine which side was underdog BEFORE the game
            # We need actual pre-game prices, but we only have final prices & volume
            # Approach: Use yes_bid/yes_ask from final snapshot as proxy for pre-game pricing
            # (These should reflect the last trading prices before settlement)

            # Get final trading prices (before settlement)
            price1 = (getattr(market1, 'yes_bid', 50) + getattr(market1, 'yes_ask', 50)) / 2
            price2 = (getattr(market2, 'yes_bid', 50) + getattr(market2, 'yes_ask', 50)) / 2

            # Check if these are settled prices (near 0 or 100) or trading prices
            is_settled1 = price1 >= 95 or price1 <= 5
            is_settled2 = price2 >= 95 or price2 <= 5

            if is_settled1 and is_settled2:
                # Both are settled prices - can't determine pre-game underdog
                # Fall back to volume proxy: lower volume = underdog (traditional)
                vol1 = getattr(market1, 'volume', 0)
                vol2 = getattr(market2, 'volume', 0)

                if vol1 < vol2:
                    underdog_market = market1
                    underdog_won = market1_won
                    underdog_price = (vol1 / (vol1 + vol2)) * 100 if (vol1 + vol2) > 0 else 50
                else:
                    underdog_market = market2
                    underdog_won = market2_won
                    underdog_price = (vol2 / (vol1 + vol2)) * 100 if (vol1 + vol2) > 0 else 50
            else:
                # Use actual trading prices - lower price = underdog
                if price1 < price2:
                    underdog_market = market1
                    underdog_won = market1_won
                    underdog_price = price1
                else:
                    underdog_market = market2
                    underdog_won = market2_won
                    underdog_price = price2

            # Clamp to reasonable range
            underdog_price = max(5, min(45, underdog_price))

            # Filter by price range
            if min_price <= underdog_price <= max_price:
                ticker = getattr(underdog_market, 'ticker', None)
                close_time = getattr(underdog_market, 'close_time', None)

                results.append(UnderdogResult(
                    ticker=ticker,
                    sport=sport,
                    underdog_price=underdog_price,
                    won=underdog_won,
                    result='yes' if underdog_won else 'no',
                    close_time=close_time
                ))

        return results

    def calculate_bucket_stats(
        self,
        results: List[UnderdogResult],
        buckets: List[Tuple[float, float]] = None
    ) -> List[BucketStats]:
        """Calculate statistics by price bucket.

        Args:
            results: List of UnderdogResult objects
            buckets: List of (min, max) price tuples

        Returns:
            List of BucketStats objects
        """
        if buckets is None:
            buckets = [
                (10, 15),
                (15, 20),
                (20, 25),
                (25, 30),
                (30, 35),
                (35, 40),
            ]

        bucket_stats = []

        for min_p, max_p in buckets:
            bucket_results = [
                r for r in results
                if min_p <= r.underdog_price < max_p
            ]

            if len(bucket_results) == 0:
                continue

            n_games = len(bucket_results)
            wins = sum(1 for r in bucket_results if r.won)
            win_rate = wins / n_games

            avg_price = sum(r.underdog_price for r in bucket_results) / n_games
            implied_prob = avg_price / 100
            edge = win_rate - implied_prob

            # Expected value per $1 bet
            # EV = (win_rate * payout) - (loss_rate * stake)
            # payout = (100 / avg_price) - 1
            payout = (100 / avg_price) - 1
            ev_per_dollar = (win_rate * payout) - ((1 - win_rate) * 1)

            # Portfolio metrics
            total_invested = n_games * avg_price / 100
            total_return = wins * 1.0  # $1 per win
            profit = total_return - total_invested
            roi = (profit / total_invested * 100) if total_invested > 0 else 0

            bucket_name = f"{int(min_p)}-{int(max_p)}¢"

            bucket_stats.append(BucketStats(
                bucket_name=bucket_name,
                n_games=n_games,
                win_rate=win_rate,
                avg_price=avg_price,
                implied_prob=implied_prob,
                edge=edge,
                ev_per_dollar=ev_per_dollar,
                total_invested=total_invested,
                total_return=total_return,
                profit=profit,
                roi=roi
            ))

        return bucket_stats

    def print_analysis(
        self,
        sport: str,
        results: List[UnderdogResult],
        bucket_stats: List[BucketStats]
    ):
        """Print analysis results.

        Args:
            sport: Sport name
            results: List of UnderdogResult objects
            bucket_stats: List of BucketStats objects
        """
        print("\n" + "=" * 80)
        print(f"{sport} UNDERDOG VALUE ANALYSIS")
        print("=" * 80)

        if len(results) == 0:
            print(f"\n❌ No settled {sport} games found in date range")
            return

        print(f"\nTotal games analyzed: {len(results)}")
        print(f"Date range: {min(r.close_time for r in results).date()} to {max(r.close_time for r in results).date()}")

        # Overall stats
        overall_wins = sum(1 for r in results if r.won)
        overall_wr = overall_wins / len(results)
        overall_avg_price = sum(r.underdog_price for r in results) / len(results)
        overall_implied = overall_avg_price / 100

        print(f"\nOverall (10-40¢ underdogs):")
        print(f"  Games: {len(results)}")
        print(f"  Win rate: {overall_wr:.1%}")
        print(f"  Avg price: {overall_avg_price:.1f}¢")
        print(f"  Avg implied prob: {overall_implied:.1%}")
        print(f"  Edge: {(overall_wr - overall_implied)*100:+.1f} percentage points")

        # Bucket analysis
        print(f"\n{'Bucket':<12} {'Games':<8} {'Win Rate':<12} {'Implied':<12} {'Edge':<12} {'EV/$1':<12} {'ROI':<12}")
        print("-" * 90)

        for stats in bucket_stats:
            status = "✅" if stats.ev_per_dollar > 0.05 else "❌" if stats.ev_per_dollar < -0.05 else "⚠️"

            print(f"{stats.bucket_name:<12} {stats.n_games:<8} {stats.win_rate:<12.1%} "
                  f"{stats.implied_prob:<12.1%} {stats.edge*100:<12.1f} "
                  f"{stats.ev_per_dollar*100:+12.2f}¢ {stats.roi:<12.1f}% {status}")

        # Best buckets
        positive_ev = [s for s in bucket_stats if s.ev_per_dollar > 0.05]
        if positive_ev:
            print(f"\n✅ PROFITABLE BUCKETS: {len(positive_ev)}")
            for stats in sorted(positive_ev, key=lambda x: x.ev_per_dollar, reverse=True):
                print(f"   {stats.bucket_name}: +{stats.ev_per_dollar*100:.2f}¢ EV "
                      f"({stats.win_rate:.1%} win rate, {stats.n_games} games)")
        else:
            print(f"\n❌ NO PROFITABLE BUCKETS FOUND")

    async def analyze_sport(
        self,
        sport: str,
        days_back: int = 30,
        save_csv: bool = True
    ) -> Tuple[List[UnderdogResult], List[BucketStats]]:
        """Analyze underdog value for a sport.

        Args:
            sport: Sport name (e.g., 'NBA', 'NCAAB')
            days_back: How many days back to analyze
            save_csv: Whether to save results to CSV

        Returns:
            Tuple of (results, bucket_stats)
        """
        series_ticker = SPORT_SERIES.get(sport)
        if not series_ticker:
            logger.error(f"Unknown sport: {sport}. Available: {list(SPORT_SERIES.keys())}")
            return [], []

        # Fetch settled markets
        markets = await self.fetch_settled_markets(series_ticker, days_back)

        if len(markets) == 0:
            logger.warning(f"No settled markets found for {sport}")
            return [], []

        # Analyze underdogs
        results = self.analyze_underdogs(markets, sport)

        if len(results) == 0:
            logger.warning(f"No underdog results found for {sport}")
            return [], []

        # Calculate bucket stats
        bucket_stats = self.calculate_bucket_stats(results)

        # Print analysis
        self.print_analysis(sport, results, bucket_stats)

        # Save to CSV
        if save_csv and len(results) > 0:
            df = pd.DataFrame([
                {
                    'ticker': r.ticker,
                    'sport': r.sport,
                    'underdog_price': r.underdog_price,
                    'won': r.won,
                    'result': r.result,
                    'close_time': r.close_time
                }
                for r in results
            ])
            filename = f"data/{sport.lower()}_underdog_analysis.csv"
            df.to_csv(filename, index=False)
            logger.info(f"\n✅ Saved results to {filename}")

        return results, bucket_stats

    async def compare_sports(
        self,
        sports: List[str],
        days_back: int = 30
    ):
        """Compare underdog value across multiple sports.

        Args:
            sports: List of sport names
            days_back: How many days back to analyze
        """
        all_results = {}

        for sport in sports:
            results, bucket_stats = await self.analyze_sport(sport, days_back, save_csv=False)
            all_results[sport] = (results, bucket_stats)

        # Print comparison
        print("\n" + "=" * 80)
        print("CROSS-SPORT COMPARISON")
        print("=" * 80)

        print(f"\n{'Sport':<12} {'Games':<10} {'Overall Win%':<15} {'Best Bucket':<20} {'Best EV/$1':<15}")
        print("-" * 80)

        for sport, (results, bucket_stats) in all_results.items():
            if len(results) == 0:
                print(f"{sport:<12} {'N/A':<10} {'N/A':<15} {'N/A':<20} {'N/A':<15}")
                continue

            overall_wr = sum(1 for r in results if r.won) / len(results)

            if len(bucket_stats) > 0:
                best_bucket = max(bucket_stats, key=lambda x: x.ev_per_dollar)
                best_bucket_name = best_bucket.bucket_name
                best_ev = best_bucket.ev_per_dollar * 100
            else:
                best_bucket_name = "N/A"
                best_ev = 0

            print(f"{sport:<12} {len(results):<10} {overall_wr:<15.1%} "
                  f"{best_bucket_name:<20} {best_ev:+15.2f}¢")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze underdog value across different sports"
    )
    parser.add_argument(
        '--sport',
        type=str,
        choices=list(SPORT_SERIES.keys()),
        help='Sport to analyze'
    )
    parser.add_argument(
        '--all-sports',
        action='store_true',
        help='Analyze all available sports'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days back to analyze (default: 30)'
    )
    parser.add_argument(
        '--no-save',
        action='store_true',
        help='Do not save results to CSV'
    )

    args = parser.parse_args()

    # Create client
    client = KalshiExchangeClient.from_env()
    analyzer = UnderdogAnalyzer(client)

    if args.all_sports:
        # Analyze all sports
        sports = list(SPORT_SERIES.keys())
        await analyzer.compare_sports(sports, args.days)
    elif args.sport:
        # Analyze single sport
        await analyzer.analyze_sport(
            args.sport,
            args.days,
            save_csv=not args.no_save
        )
    else:
        parser.print_help()
        print("\nExample usage:")
        print("  python3 scripts/analyze_underdog_value_by_sport.py --sport NCAAB")
        print("  python3 scripts/analyze_underdog_value_by_sport.py --all-sports --days 60")


if __name__ == "__main__":
    asyncio.run(main())
