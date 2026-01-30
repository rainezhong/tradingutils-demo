"""Main data collection script for Kalshi markets."""

from datetime import datetime, timezone

from .client import KalshiPublicClient
from .database import MarketDatabase


class DataCollector:
    """Collects and stores market data from Kalshi."""

    def __init__(self, db_path: str = "data/markets.db"):
        self.db = MarketDatabase(db_path)
        self.client = KalshiPublicClient()
        self.db.init_db()

    def scan_markets(self, min_volume: int = 1000) -> int:
        """Fetch open markets, filter by volume, and store in database."""
        markets = self.client.get_all_markets(min_volume=min_volume)

        for market in markets:
            self.db.upsert_market({
                "ticker": market.get("ticker"),
                "title": market.get("title"),
                "category": market.get("category"),
                "close_time": market.get("close_time"),
                "status": market.get("status"),
                "volume_24h": market.get("volume_24h"),
            })

        return len(markets)

    def log_snapshots(self) -> int:
        """For each active market, fetch prices and store snapshot."""
        markets = self.db.get_active_markets()
        timestamp = datetime.now(timezone.utc).isoformat()
        count = 0

        for market in markets:
            ticker = market["ticker"]

            try:
                orderbook = self.client.get_orderbook(ticker)
                ob_data = orderbook.get("orderbook", {})

                yes_bids = ob_data.get("yes", [])
                no_bids = ob_data.get("no", [])

                yes_bid = yes_bids[0][0] if yes_bids else None
                yes_ask = 100 - no_bids[0][0] if no_bids else None

                if yes_bid is not None and yes_ask is not None:
                    spread_cents = yes_ask - yes_bid
                    mid_price = (yes_bid + yes_ask) / 2
                    spread_pct = (spread_cents / mid_price) * 100 if mid_price > 0 else 0
                else:
                    spread_cents = None
                    mid_price = None
                    spread_pct = None

                self.db.add_snapshot({
                    "ticker": ticker,
                    "timestamp": timestamp,
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "spread_cents": spread_cents,
                    "spread_pct": spread_pct,
                    "mid_price": mid_price,
                    "volume_24h": market.get("volume_24h"),
                })
                count += 1

            except Exception as e:
                print(f"   Error fetching orderbook for {ticker}: {e}")

        return count

    def print_stats(self) -> None:
        """Print summary statistics."""
        stats = self.db.get_summary_stats()

        print("\n3. Summary Statistics:")
        print(f"   Total Markets: {stats['total_markets']}")
        print(f"   Total Snapshots: {stats['total_snapshots']}")

        if stats["avg_spread_cents"] is not None:
            print(f"   Avg Spread: {stats['avg_spread_cents']:.1f} cents ({stats['avg_spread_pct']:.1f}%)")
            print(f"   Min Spread: {stats['min_spread']} cent{'s' if stats['min_spread'] != 1 else ''}")
            print(f"   Max Spread: {stats['max_spread']} cents")
        else:
            print("   No spread data available")

    def close(self) -> None:
        """Close database connection."""
        self.db.close()


def main():
    """CLI entry point: scan -> snapshot -> stats."""
    print("=== Market Data Collection Test ===\n")

    collector = DataCollector()

    try:
        print("1. Scanning active markets...")
        market_count = collector.scan_markets(min_volume=1000)
        print(f"   Found {market_count} markets with volume >= 1000")

        print("\n2. Logging snapshots...")
        snapshot_count = collector.log_snapshots()
        print(f"   Logged {snapshot_count} snapshots")

        collector.print_stats()

    finally:
        collector.close()


if __name__ == "__main__":
    main()
