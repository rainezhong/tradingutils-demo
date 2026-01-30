"""Data logger for capturing market snapshots and spread data."""

import argparse
import logging
import time
from collections import defaultdict
from typing import Optional, List, Tuple, Dict, Any

from ..core import (
    Config,
    KalshiClient,
    Market,
    MarketDatabase,
    Snapshot,
    get_config,
    setup_logger,
    utc_now_iso,
)

logger = setup_logger(__name__)

# Configure file logger for errors
error_handler = logging.FileHandler("errors.log")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(error_handler)


class Logger:
    """Logs market snapshots with batch database inserts."""

    BATCH_SIZE = 100

    def __init__(
        self,
        config: Optional[Config] = None,
        client: Optional[KalshiClient] = None,
        db: Optional[MarketDatabase] = None,
    ):
        """
        Initialize the logger.

        Args:
            config: Configuration instance
            client: API client instance
            db: Database instance
        """
        self.config = config or get_config()
        self.client = client or KalshiClient(self.config)
        self.db = db or MarketDatabase(self.config.db_path)
        self.db.init_db()

    def _fetch_snapshot(
        self,
        market: Market,
        timestamp: str,
        max_retries: int = 3,
    ) -> Optional[Snapshot]:
        """
        Fetch current state for a market and create snapshot.

        Args:
            market: Market to fetch
            timestamp: ISO8601 timestamp for snapshot
            max_retries: Number of retries on failure

        Returns:
            Snapshot instance or None on failure
        """
        for attempt in range(max_retries):
            try:
                orderbook = self.client.get_orderbook(market.ticker)
                snapshot = Snapshot.from_orderbook(
                    ticker=market.ticker,
                    orderbook=orderbook,
                    timestamp=timestamp,
                    volume_24h=market.volume_24h,
                    open_interest=market.open_interest,
                )
                return snapshot
            except Exception as e:
                logger.warning(
                    f"Failed to fetch {market.ticker} (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt >= max_retries - 1:
                    logger.error(f"Giving up on {market.ticker} after {max_retries} attempts")
                    return None
        return None

    def _batch_insert(self, snapshots: list[Snapshot]) -> int:
        """
        Insert snapshots in batches for performance.

        Args:
            snapshots: List of snapshots to insert

        Returns:
            Number of successfully inserted snapshots
        """
        inserted = 0
        for i in range(0, len(snapshots), self.BATCH_SIZE):
            batch = snapshots[i:i + self.BATCH_SIZE]
            for snapshot in batch:
                try:
                    self.db.add_snapshot(snapshot)
                    inserted += 1
                except Exception as e:
                    logger.error(f"Failed to insert snapshot for {snapshot.ticker}: {e}")
        return inserted

    def log_snapshots(
        self,
        tickers: Optional[list[str]] = None,
        show_progress: bool = True,
    ) -> int:
        """
        Log snapshots for markets in database.

        Args:
            tickers: Specific tickers to log (None = all active markets)
            show_progress: Whether to print progress

        Returns:
            Number of snapshots logged
        """
        # Get markets to process
        if tickers:
            markets = []
            for ticker in tickers:
                market = self.db.get_market(ticker)
                if market:
                    markets.append(market)
                else:
                    logger.warning(f"Market not found in database: {ticker}")
        else:
            # First try active markets, fall back to all markets if none found
            markets = self.db.get_active_markets()
            if not markets:
                markets = self.db.get_all_markets()

        if not markets:
            logger.warning("No markets to process")
            return 0

        timestamp = utc_now_iso()
        total = len(markets)
        snapshots: list[Snapshot] = []
        failed_tickers: list[str] = []

        for idx, market in enumerate(markets, 1):
            snapshot = self._fetch_snapshot(market, timestamp)

            if snapshot:
                snapshots.append(snapshot)
                if show_progress:
                    print(f"[{idx}/{total}] {market.ticker} ✓")
            else:
                failed_tickers.append(market.ticker)
                if show_progress:
                    print(f"[{idx}/{total}] {market.ticker} ✗")

        # Batch insert all snapshots
        inserted = self._batch_insert(snapshots)

        logger.info(f"Logged {inserted} snapshots, {len(failed_tickers)} failed")

        if failed_tickers:
            logger.warning(f"Failed tickers: {', '.join(failed_tickers)}")

        return inserted

    def log_snapshots_bulk(self, show_progress: bool = True) -> int:
        """
        Log snapshots for all markets using efficient bulk fetch.

        This is MUCH faster than log_snapshots() - fetches all markets
        with prices in ~25 paginated API calls instead of 2400+ individual calls.

        Args:
            show_progress: Whether to print progress

        Returns:
            Number of snapshots logged
        """
        if show_progress:
            print("Fetching all markets with prices (bulk mode)...")

        # Single bulk fetch - ~25 API calls instead of 2400+
        market_data_list = self.client.get_all_markets_with_prices()

        if not market_data_list:
            logger.warning("No markets returned from API")
            return 0

        timestamp = utc_now_iso()
        snapshots: list[Snapshot] = []
        min_volume = self.config.min_volume

        # Also upsert markets so they match snapshots
        markets_saved = 0
        skipped_low_volume = 0
        skipped_no_liquidity = 0

        for data in market_data_list:
            try:
                volume = data.get("volume_24h") or 0

                # Skip low-volume markets
                if volume < min_volume:
                    skipped_low_volume += 1
                    continue

                # Save market data
                market = Market.from_api_response(data)
                self.db.upsert_market(market)
                markets_saved += 1

                # Create snapshot - only save if has real liquidity (both bid and ask > 0)
                snapshot = Snapshot.from_market_data(data, timestamp)
                if snapshot.yes_bid and snapshot.yes_ask:
                    snapshots.append(snapshot)
                else:
                    skipped_no_liquidity += 1
            except Exception as e:
                logger.warning(f"Failed to process {data.get('ticker')}: {e}")

        if show_progress:
            print(f"Fetched {len(market_data_list)} markets total")
            print(f"  Skipped {skipped_low_volume} low-volume (< {min_volume})")
            print(f"  Skipped {skipped_no_liquidity} no-liquidity (bid=0 or ask=0)")
            print(f"  Saved {markets_saved} markets, {len(snapshots)} snapshots")

        # Batch insert
        inserted = self._batch_insert(snapshots)

        logger.info(f"Bulk logged {inserted} snapshots")
        return inserted

    def close(self) -> None:
        """Close database connection."""
        self.db.close()

    # ========== SPREAD LOGGING ==========

    def log_spreads(
        self,
        ticker_pairs: Optional[List[Tuple[str, str]]] = None,
        auto_discover: bool = False,
        show_progress: bool = True,
    ) -> int:
        """
        Log spread pair snapshots.

        Args:
            ticker_pairs: List of (ticker_a, ticker_b) to track
            auto_discover: If True, discover pairs from parlay markets
            show_progress: Whether to print progress

        Returns:
            Number of spread snapshots logged
        """
        from arb.spread_collector import SpreadDatabase

        # Get pairs to track
        if ticker_pairs:
            pairs = ticker_pairs
        elif auto_discover:
            pairs = self._discover_spread_pairs()
        else:
            # Use known pairs
            from arb.kalshi_scanner import get_all_known_pairs
            pairs = get_all_known_pairs()

        if not pairs:
            logger.warning("No spread pairs to log")
            return 0

        if show_progress:
            print(f"Logging {len(pairs)} spread pairs...")

        # Initialize spread database
        spread_db = SpreadDatabase("data/spreads.db")
        timestamp = utc_now_iso()
        logged = 0

        for i, (ticker_a, ticker_b) in enumerate(pairs):
            try:
                # Fetch both markets
                time.sleep(0.3)  # Rate limit
                m1 = self.client.get_market(ticker_a).get("market", {})
                time.sleep(0.3)
                m2 = self.client.get_market(ticker_b).get("market", {})

                # Convert prices
                def to_dollars(v):
                    if v is None:
                        return None
                    return v / 100.0 if v > 1 else v

                # Create pair info and snapshot
                from arb.kalshi_scanner import KalshiMarketInfo, ComplementaryPair

                market_a = KalshiMarketInfo(
                    ticker=ticker_a,
                    title=m1.get("title", ""),
                    event_ticker=m1.get("event_ticker", ""),
                    subtitle=m1.get("yes_sub_title", ticker_a.split("-")[-1]),
                    yes_bid=to_dollars(m1.get("yes_bid")),
                    yes_ask=to_dollars(m1.get("yes_ask")),
                    no_bid=to_dollars(m1.get("no_bid")),
                    no_ask=to_dollars(m1.get("no_ask")),
                )

                market_b = KalshiMarketInfo(
                    ticker=ticker_b,
                    title=m2.get("title", ""),
                    event_ticker=m2.get("event_ticker", ""),
                    subtitle=m2.get("yes_sub_title", ticker_b.split("-")[-1]),
                    yes_bid=to_dollars(m2.get("yes_bid")),
                    yes_ask=to_dollars(m2.get("yes_ask")),
                    no_bid=to_dollars(m2.get("no_bid")),
                    no_ask=to_dollars(m2.get("no_ask")),
                )

                pair = ComplementaryPair(
                    market_a=market_a,
                    market_b=market_b,
                    event_ticker=market_a.event_ticker,
                    event_title=f"{market_a.subtitle} vs {market_b.subtitle}",
                    match_type="sports",
                )

                # Store in database
                spread_db.upsert_pair(pair)
                spread_db.add_snapshot(pair, timestamp)
                logged += 1

                if show_progress:
                    edge = pair.dutch_book_edge
                    edge_str = f"${edge:+.4f}" if edge else "N/A"
                    print(f"  [{i+1}/{len(pairs)}] {pair.event_title}: {edge_str}")

            except Exception as e:
                logger.warning(f"Failed to log spread {ticker_a}/{ticker_b}: {e}")

        spread_db.close()
        logger.info(f"Logged {logged} spread snapshots")
        return logged

    def _discover_spread_pairs(self, max_pages: int = 3) -> List[Tuple[str, str]]:
        """Discover complementary pairs from parlay markets."""
        all_events: Dict[str, set] = defaultdict(set)
        cursor = None
        pages = 0

        while pages < max_pages:
            response = self.client.get_markets(status="open", limit=100, cursor=cursor)
            markets = response.get("markets", [])
            if not markets:
                break

            for m in markets:
                legs = m.get("mve_selected_legs", [])
                for leg in legs:
                    ticker = leg.get("market_ticker", "")
                    event = leg.get("event_ticker", "")
                    if ticker and event and "GAME" in ticker:
                        all_events[event].add(ticker)

            cursor = response.get("cursor")
            pages += 1
            if not cursor:
                break
            time.sleep(0.2)

        # Find events with exactly 2 markets
        pairs = []
        for event, tickers in all_events.items():
            if len(tickers) == 2:
                t = sorted(list(tickers))
                pairs.append((t[0], t[1]))

        return pairs

    def log_all(
        self,
        include_spreads: bool = True,
        spread_pairs: Optional[List[Tuple[str, str]]] = None,
        discover_spreads: bool = False,
        show_progress: bool = True,
    ) -> Dict[str, int]:
        """
        Log both market snapshots and spread pairs in one call.

        Args:
            include_spreads: Whether to log spread pairs
            spread_pairs: Specific pairs to track (None = known pairs)
            discover_spreads: Auto-discover pairs from parlays
            show_progress: Whether to print progress

        Returns:
            Dict with 'snapshots' and 'spreads' counts
        """
        results = {}

        # Log regular snapshots
        if show_progress:
            print("=== Logging Market Snapshots ===")
        results["snapshots"] = self.log_snapshots_bulk(show_progress=show_progress)

        # Log spreads
        if include_spreads:
            if show_progress:
                print("\n=== Logging Spread Pairs ===")
            results["spreads"] = self.log_spreads(
                ticker_pairs=spread_pairs,
                auto_discover=discover_spreads,
                show_progress=show_progress,
            )

        return results


def main():
    """CLI entry point for data logger."""
    parser = argparse.ArgumentParser(description="Log market snapshots and spread data")
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated list of tickers to log",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )
    parser.add_argument(
        "--bulk",
        action="store_true",
        help="Use efficient bulk logging (recommended)",
    )
    parser.add_argument(
        "--spreads",
        action="store_true",
        help="Also log spread pairs",
    )
    parser.add_argument(
        "--spreads-only",
        action="store_true",
        help="Only log spread pairs (skip regular snapshots)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Auto-discover spread pairs from parlay markets",
    )
    parser.add_argument(
        "--loop",
        type=int,
        metavar="SECONDS",
        help="Run continuously with specified interval",
    )
    args = parser.parse_args()

    print("=== Data Logger ===\n")

    tickers = None
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]

    data_logger = Logger()

    def run_once():
        if args.spreads_only:
            # Only log spreads
            count = data_logger.log_spreads(
                auto_discover=args.discover,
                show_progress=not args.quiet,
            )
            print(f"\nLogged {count} spread snapshots")
        elif args.spreads or args.bulk:
            # Log both snapshots and spreads
            results = data_logger.log_all(
                include_spreads=args.spreads,
                discover_spreads=args.discover,
                show_progress=not args.quiet,
            )
            print(f"\nLogged {results.get('snapshots', 0)} snapshots, {results.get('spreads', 0)} spreads")
        else:
            # Just log snapshots
            count = data_logger.log_snapshots(
                tickers=tickers,
                show_progress=not args.quiet,
            )
            print(f"\nLogged {count} snapshots")

    try:
        if args.loop:
            import signal
            import sys

            print(f"Running in loop mode (every {args.loop} seconds)")
            print("Press Ctrl+C to stop\n")

            running = True

            def signal_handler(sig, frame):
                nonlocal running
                running = False
                print("\nStopping...")

            signal.signal(signal.SIGINT, signal_handler)

            iteration = 0
            while running:
                iteration += 1
                timestamp = utc_now_iso()[:19]
                print(f"\n--- Iteration {iteration} at {timestamp} ---")
                run_once()

                # Interruptible sleep
                for _ in range(args.loop):
                    if not running:
                        break
                    time.sleep(1)

            print(f"\nCompleted {iteration} iterations")
        else:
            run_once()
    finally:
        data_logger.close()


if __name__ == "__main__":
    main()
