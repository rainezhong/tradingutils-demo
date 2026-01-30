"""Market scanner for discovering and filtering prediction markets."""

import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..core import (
    Config,
    KalshiClient,
    Market,
    MarketDatabase,
    get_config,
    parse_iso_timestamp,
    setup_logger,
    utc_now,
)

logger = setup_logger(__name__)

# Configure file logger for errors
error_handler = logging.FileHandler("errors.log")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(error_handler)


class Scanner:
    """Scans and filters markets from Kalshi API."""

    def __init__(
        self,
        config: Optional[Config] = None,
        client: Optional[KalshiClient] = None,
        db: Optional[MarketDatabase] = None,
    ):
        """
        Initialize the scanner.

        Args:
            config: Configuration instance
            client: API client instance
            db: Database instance
        """
        self.config = config or get_config()
        self.client = client or KalshiClient(self.config)
        self.db = db or MarketDatabase(self.config.db_path)
        self.db.init_db()

    def _days_until_close(self, close_time: Optional[str]) -> Optional[int]:
        """
        Calculate days until market closes.

        Args:
            close_time: ISO8601 close time string

        Returns:
            Days until close, or None if no close time
        """
        if not close_time:
            return None

        try:
            close_dt = parse_iso_timestamp(close_time)
            now = utc_now()
            delta = close_dt - now
            return delta.days
        except Exception:
            return None

    def _passes_filters(
        self,
        market: Market,
        min_volume: int,
        min_days_until_close: int,
    ) -> bool:
        """
        Check if market passes all filters.

        Args:
            market: Market to check
            min_volume: Minimum 24h volume
            min_days_until_close: Minimum days until close

        Returns:
            True if market passes all filters
        """
        # Volume filter
        volume = market.volume_24h or 0
        if volume < min_volume:
            return False

        # Status filter
        if market.status != "open":
            return False

        # Days until close filter
        days = self._days_until_close(market.close_time)
        if days is not None and days < min_days_until_close:
            return False

        return True

    def scan(
        self,
        min_volume: Optional[int] = None,
        min_days_until_close: int = 7,
        max_retries: int = 3,
    ) -> list[Market]:
        """
        Scan for markets matching filter criteria.

        Args:
            min_volume: Minimum 24h volume (uses config default if None)
            min_days_until_close: Minimum days until market closes
            max_retries: Number of retries on failure

        Returns:
            List of filtered Market instances
        """
        min_vol = min_volume if min_volume is not None else self.config.min_volume
        filtered_markets = []
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Fetch all open markets (pagination handled by client)
                all_markets = self.client.get_all_markets(min_volume=0)
                logger.info(f"Fetched {len(all_markets)} total markets")

                # Apply filters
                for market in all_markets:
                    if self._passes_filters(market, min_vol, min_days_until_close):
                        filtered_markets.append(market)

                logger.info(
                    f"Filtered to {len(filtered_markets)} markets "
                    f"(volume >= {min_vol}, days >= {min_days_until_close})"
                )
                break

            except Exception as e:
                retry_count += 1
                logger.error(f"Scan failed (attempt {retry_count}/{max_retries}): {e}")
                if retry_count >= max_retries:
                    raise

        return filtered_markets

    def scan_and_save(
        self,
        min_volume: Optional[int] = None,
        min_days_until_close: int = 7,
    ) -> int:
        """
        Scan markets and save to database.

        Args:
            min_volume: Minimum 24h volume
            min_days_until_close: Minimum days until market closes

        Returns:
            Number of markets saved
        """
        markets = self.scan(min_volume, min_days_until_close)

        saved_count = 0
        for market in markets:
            try:
                self.db.upsert_market(market)
                saved_count += 1
            except Exception as e:
                logger.error(f"Failed to save market {market.ticker}: {e}")

        logger.info(f"Saved {saved_count} markets to database")
        return saved_count

    def close(self) -> None:
        """Close database connection."""
        self.db.close()


def main():
    """CLI entry point for market scanner."""
    parser = argparse.ArgumentParser(description="Scan Kalshi markets")
    parser.add_argument(
        "--min-volume",
        type=int,
        default=None,
        help="Minimum 24h volume (default: from config)",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=7,
        help="Minimum days until close (default: 7)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file",
    )
    args = parser.parse_args()

    print("=== Market Scanner ===\n")

    scanner = Scanner()
    try:
        count = scanner.scan_and_save(
            min_volume=args.min_volume,
            min_days_until_close=args.min_days,
        )
        print(f"\nScanned and saved {count} markets")
    finally:
        scanner.close()


if __name__ == "__main__":
    main()
