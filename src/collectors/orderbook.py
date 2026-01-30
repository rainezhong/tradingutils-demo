"""Orderbook fetcher for detailed depth metrics."""

import argparse
import logging
from dataclasses import dataclass
from typing import Optional

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


@dataclass
class OrderbookDepth:
    """Aggregated orderbook depth metrics."""

    ticker: str
    yes_bid: Optional[int] = None
    yes_ask: Optional[int] = None
    bid_depth_total: int = 0
    ask_depth_total: int = 0
    bid_levels: int = 0
    ask_levels: int = 0
    bid_depth_at_best: int = 0
    ask_depth_at_best: int = 0
    spread_cents: Optional[int] = None
    spread_pct: Optional[float] = None
    mid_price: Optional[float] = None

    # Volume at each price level (price -> volume)
    bid_volume_by_price: dict = None
    ask_volume_by_price: dict = None

    def __post_init__(self):
        if self.bid_volume_by_price is None:
            self.bid_volume_by_price = {}
        if self.ask_volume_by_price is None:
            self.ask_volume_by_price = {}


class OrderbookFetcher:
    """Fetches and analyzes orderbook depth."""

    def __init__(
        self,
        config: Optional[Config] = None,
        client: Optional[KalshiClient] = None,
        db: Optional[MarketDatabase] = None,
    ):
        """
        Initialize the orderbook fetcher.

        Args:
            config: Configuration instance
            client: API client instance
            db: Database instance
        """
        self.config = config or get_config()
        self.client = client or KalshiClient(self.config)
        self.db = db or MarketDatabase(self.config.db_path)
        self.db.init_db()

    def _parse_orderbook(self, ticker: str, orderbook: dict) -> OrderbookDepth:
        """
        Parse orderbook response into depth metrics.

        Args:
            ticker: Market ticker
            orderbook: Raw orderbook response

        Returns:
            OrderbookDepth with calculated metrics
        """
        ob_data = orderbook.get("orderbook", {})
        yes_bids = ob_data.get("yes", [])  # [[price, volume], ...]
        no_bids = ob_data.get("no", [])    # [[price, volume], ...]

        depth = OrderbookDepth(ticker=ticker)

        # Process bid side (yes bids)
        if yes_bids:
            depth.yes_bid = yes_bids[0][0]
            depth.bid_depth_at_best = yes_bids[0][1]
            depth.bid_levels = len(yes_bids)
            depth.bid_depth_total = sum(level[1] for level in yes_bids)
            depth.bid_volume_by_price = {level[0]: level[1] for level in yes_bids}

        # Process ask side (derived from no bids)
        # yes_ask = 100 - no_bid_price
        if no_bids:
            depth.yes_ask = 100 - no_bids[0][0]
            depth.ask_depth_at_best = no_bids[0][1]
            depth.ask_levels = len(no_bids)
            depth.ask_depth_total = sum(level[1] for level in no_bids)
            # Convert no prices to yes ask prices
            depth.ask_volume_by_price = {100 - level[0]: level[1] for level in no_bids}

        # Calculate spread metrics
        if depth.yes_bid is not None and depth.yes_ask is not None:
            depth.spread_cents = depth.yes_ask - depth.yes_bid
            depth.mid_price = (depth.yes_bid + depth.yes_ask) / 2
            if depth.mid_price > 0:
                depth.spread_pct = (depth.spread_cents / depth.mid_price) * 100

        return depth

    def fetch_depth(
        self,
        ticker: str,
        max_retries: int = 3,
    ) -> Optional[OrderbookDepth]:
        """
        Fetch orderbook depth for a single market.

        Args:
            ticker: Market ticker
            max_retries: Number of retries on failure

        Returns:
            OrderbookDepth or None on failure
        """
        for attempt in range(max_retries):
            try:
                orderbook = self.client.get_orderbook(ticker)
                return self._parse_orderbook(ticker, orderbook)
            except Exception as e:
                logger.warning(
                    f"Failed to fetch orderbook for {ticker} "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt >= max_retries - 1:
                    logger.error(f"Giving up on {ticker} after {max_retries} attempts")
                    return None
        return None

    def fetch_and_store(
        self,
        tickers: Optional[list[str]] = None,
        show_progress: bool = True,
    ) -> int:
        """
        Fetch orderbook depth and store as snapshots.

        Args:
            tickers: Specific tickers (None = all active markets)
            show_progress: Whether to print progress

        Returns:
            Number of snapshots stored
        """
        # Get markets to process
        if tickers:
            markets = []
            for ticker in tickers:
                market = self.db.get_market(ticker)
                if market:
                    markets.append(market)
                else:
                    logger.warning(f"Market not found: {ticker}")
        else:
            markets = self.db.get_active_markets()

        if not markets:
            logger.warning("No markets to process")
            return 0

        timestamp = utc_now_iso()
        total = len(markets)
        stored = 0
        failed: list[str] = []

        for idx, market in enumerate(markets, 1):
            depth = self.fetch_depth(market.ticker)

            if depth:
                try:
                    snapshot = Snapshot(
                        ticker=depth.ticker,
                        timestamp=timestamp,
                        yes_bid=depth.yes_bid,
                        yes_ask=depth.yes_ask,
                        spread_cents=depth.spread_cents,
                        spread_pct=depth.spread_pct,
                        mid_price=depth.mid_price,
                        volume_24h=market.volume_24h,
                        open_interest=market.open_interest,
                        orderbook_bid_depth=depth.bid_depth_total,
                        orderbook_ask_depth=depth.ask_depth_total,
                    )
                    self.db.add_snapshot(snapshot)
                    stored += 1

                    if show_progress:
                        print(
                            f"[{idx}/{total}] {market.ticker} ✓ "
                            f"(spread: {depth.spread_cents}c, "
                            f"depth: {depth.bid_depth_total}/{depth.ask_depth_total})"
                        )
                except Exception as e:
                    logger.error(f"Failed to store snapshot for {market.ticker}: {e}")
                    failed.append(market.ticker)
                    if show_progress:
                        print(f"[{idx}/{total}] {market.ticker} ✗ (store failed)")
            else:
                failed.append(market.ticker)
                if show_progress:
                    print(f"[{idx}/{total}] {market.ticker} ✗ (fetch failed)")

        logger.info(f"Stored {stored} snapshots, {len(failed)} failed")
        return stored

    def get_depth_summary(self, ticker: str) -> Optional[dict]:
        """
        Get a summary of orderbook depth for display.

        Args:
            ticker: Market ticker

        Returns:
            Summary dict or None on failure
        """
        depth = self.fetch_depth(ticker)
        if not depth:
            return None

        return {
            "ticker": depth.ticker,
            "best_bid": depth.yes_bid,
            "best_ask": depth.yes_ask,
            "spread_cents": depth.spread_cents,
            "spread_pct": depth.spread_pct,
            "mid_price": depth.mid_price,
            "bid_levels": depth.bid_levels,
            "ask_levels": depth.ask_levels,
            "bid_depth_total": depth.bid_depth_total,
            "ask_depth_total": depth.ask_depth_total,
            "bid_at_best": depth.bid_depth_at_best,
            "ask_at_best": depth.ask_depth_at_best,
        }

    def close(self) -> None:
        """Close database connection."""
        self.db.close()


def main():
    """CLI entry point for orderbook fetcher."""
    parser = argparse.ArgumentParser(description="Fetch orderbook depth")
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated list of tickers",
    )
    parser.add_argument(
        "--summary",
        type=str,
        default=None,
        help="Show depth summary for a single ticker",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    print("=== Orderbook Fetcher ===\n")

    fetcher = OrderbookFetcher()

    try:
        if args.summary:
            # Show summary for single ticker
            summary = fetcher.get_depth_summary(args.summary)
            if summary:
                print(f"Ticker: {summary['ticker']}")
                print(f"Best Bid: {summary['best_bid']}c")
                print(f"Best Ask: {summary['best_ask']}c")
                print(f"Spread: {summary['spread_cents']}c ({summary['spread_pct']:.1f}%)")
                print(f"Mid Price: {summary['mid_price']:.1f}c")
                print(f"Bid Levels: {summary['bid_levels']} (total: {summary['bid_depth_total']})")
                print(f"Ask Levels: {summary['ask_levels']} (total: {summary['ask_depth_total']})")
            else:
                print(f"Failed to fetch orderbook for {args.summary}")
        else:
            # Fetch and store for multiple tickers
            tickers = None
            if args.tickers:
                tickers = [t.strip() for t in args.tickers.split(",")]

            count = fetcher.fetch_and_store(
                tickers=tickers,
                show_progress=not args.quiet,
            )
            print(f"\nStored {count} snapshots")
    finally:
        fetcher.close()


if __name__ == "__main__":
    main()
