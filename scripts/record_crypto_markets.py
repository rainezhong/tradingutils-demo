#!/usr/bin/env python3
"""Record crypto latency market data for backtesting.

Captures:
- Kalshi 15M crypto market prices (bid/ask) every second
- Coinbase spot prices for BTC/ETH/SOL
- Market settlement results

Usage:
    python scripts/record_crypto_markets.py                    # Record until Ctrl+C
    python scripts/record_crypto_markets.py --duration 3600    # Record for 1 hour
    python scripts/record_crypto_markets.py --output data.json # Custom output file
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.api_client import KalshiClient
from src.core.config import Config
from src.core.trading_state import get_trading_state
from src.feeds.coinbase_feed import CoinbasePriceFeed, CoinbasePriceUpdate


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class CryptoMarketRecorder:
    """Records crypto market data for backtesting."""

    def __init__(
        self,
        output_path: Path,
        symbols: List[str] = None,
        record_interval_sec: float = 1.0,
    ):
        self.output_path = output_path
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.record_interval_sec = record_interval_sec

        # Initialize clients
        self._config = Config.load()
        self._kalshi = KalshiClient(config=self._config)
        self._price_feed = CoinbasePriceFeed(
            symbols=self.symbols,
            poll_interval_sec=0.5,
        )

        # State
        self._running = False
        self._lock = threading.Lock()
        self._latest_spots: Dict[str, float] = {}
        self._markets: Dict[str, dict] = {}

        # Recording data
        self._snapshots: List[dict] = []
        self._settlements: List[dict] = []
        self._start_time: Optional[datetime] = None

    def start(self, duration_sec: Optional[float] = None):
        """Start recording."""
        self._running = True
        self._start_time = datetime.utcnow()

        logger.info("=" * 50)
        logger.info("CRYPTO MARKET RECORDER")
        logger.info("=" * 50)
        logger.info(f"Output: {self.output_path}")
        logger.info(f"Symbols: {self.symbols}")
        logger.info(f"Interval: {self.record_interval_sec}s")
        if duration_sec:
            logger.info(f"Duration: {duration_sec}s")
        logger.info("=" * 50)

        # Start price feed
        self._price_feed.on_price_update(self._on_spot_update)
        self._price_feed.start()

        # Initial market scan
        self._scan_markets()

        # Recording loop
        shutdown = threading.Event()

        def handle_signal(signum, frame):
            logger.info("Shutdown signal received...")
            shutdown.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        logger.info("Recording started. Press Ctrl+C to stop.")

        last_record = 0
        last_scan = 0
        scan_interval = 60  # Scan for new markets every minute

        while self._running and not shutdown.is_set():
            now = time.time()

            # Record snapshot
            if now - last_record >= self.record_interval_sec:
                self._record_snapshot()
                last_record = now

            # Scan for new markets periodically
            if now - last_scan >= scan_interval:
                self._scan_markets()
                last_scan = now

            # Check duration
            if duration_sec:
                elapsed = (datetime.utcnow() - self._start_time).total_seconds()
                if elapsed >= duration_sec:
                    logger.info("Duration reached.")
                    break

            time.sleep(0.1)

        self.stop()

    def stop(self):
        """Stop recording and save data."""
        self._running = False
        self._price_feed.stop()

        # Check for settled markets
        self._check_settlements()

        # Save data
        self._save()

        logger.info("Recording stopped.")

    def _on_spot_update(self, update: CoinbasePriceUpdate):
        """Handle spot price update."""
        with self._lock:
            self._latest_spots[update.symbol.upper()] = update.price

    def _scan_markets(self):
        """Scan for active crypto 15M markets."""
        try:
            for asset in ["BTC", "ETH", "SOL"]:
                resp = self._kalshi._request(
                    "GET",
                    "/markets",
                    params={
                        "series_ticker": f"KX{asset}15M",
                        "status": "open",
                        "limit": 10,
                    },
                )

                for market in resp.get("markets", []):
                    ticker = market.get("ticker", "")
                    if ticker and ticker not in self._markets:
                        self._markets[ticker] = {
                            "ticker": ticker,
                            "asset": asset,
                            "title": market.get("title", ""),
                            "close_time": market.get("close_time", ""),
                            "strike": self._parse_strike(ticker),
                            "discovered_at": datetime.utcnow().isoformat(),
                        }
                        logger.info(f"Found market: {ticker}")

        except Exception as e:
            logger.debug(f"Market scan error: {e}")

    def _parse_strike(self, ticker: str) -> Optional[float]:
        """Parse strike price from ticker (e.g., KXBTC15M-26JAN282215-15)."""
        # The last number after the dash is often a reference value
        # For now, we'll get the actual strike from the market data
        return None

    def _record_snapshot(self):
        """Record current market state."""
        # Pause if trading is active to avoid competing for rate limits
        trading_state = get_trading_state()
        if trading_state.should_pause():
            logger.debug("Skipping snapshot: trading is active")
            return

        timestamp = datetime.utcnow().isoformat()

        with self._lock:
            spots = dict(self._latest_spots)

        for ticker, market_info in list(self._markets.items()):
            try:
                # Get current market prices
                resp = self._kalshi._request("GET", f"/markets/{ticker}")
                market = resp.get("market", resp)

                status = market.get("status", "unknown")

                # Skip if market closed
                if status not in ("open", "active"):
                    # Record settlement
                    result = market.get("result", "")
                    if result and ticker not in [s["ticker"] for s in self._settlements]:
                        self._settlements.append({
                            "ticker": ticker,
                            "asset": market_info["asset"],
                            "result": result,
                            "settled_at": timestamp,
                        })
                        logger.info(f"Market settled: {ticker} -> {result}")
                    continue

                # Get spot price for this asset
                asset = market_info["asset"]
                spot_symbol = f"{asset}USDT"
                spot_price = spots.get(spot_symbol)

                snapshot = {
                    "timestamp": timestamp,
                    "ticker": ticker,
                    "asset": asset,
                    "yes_bid": market.get("yes_bid", 0),
                    "yes_ask": market.get("yes_ask", 0),
                    "no_bid": market.get("no_bid", 0),
                    "no_ask": market.get("no_ask", 0),
                    "volume": market.get("volume", 0),
                    "open_interest": market.get("open_interest", 0),
                    "spot_price": spot_price,
                    "close_time": market.get("close_time", ""),
                }

                self._snapshots.append(snapshot)

            except Exception as e:
                logger.debug(f"Snapshot error for {ticker}: {e}")

        # Log progress
        if len(self._snapshots) % 100 == 0:
            logger.info(f"Recorded {len(self._snapshots)} snapshots, {len(self._settlements)} settlements")

    def _check_settlements(self):
        """Check all tracked markets for settlements."""
        for ticker, market_info in list(self._markets.items()):
            if ticker in [s["ticker"] for s in self._settlements]:
                continue

            try:
                resp = self._kalshi._request("GET", f"/markets/{ticker}")
                market = resp.get("market", resp)
                result = market.get("result", "")

                if result:
                    self._settlements.append({
                        "ticker": ticker,
                        "asset": market_info["asset"],
                        "result": result,
                        "settled_at": datetime.utcnow().isoformat(),
                    })
                    logger.info(f"Market settled: {ticker} -> {result}")

            except Exception as e:
                logger.debug(f"Settlement check error for {ticker}: {e}")

    def _save(self):
        """Save recorded data to file."""
        data = {
            "metadata": {
                "start_time": self._start_time.isoformat() if self._start_time else None,
                "end_time": datetime.utcnow().isoformat(),
                "symbols": self.symbols,
                "record_interval_sec": self.record_interval_sec,
                "total_snapshots": len(self._snapshots),
                "total_settlements": len(self._settlements),
                "markets_tracked": len(self._markets),
            },
            "markets": self._markets,
            "snapshots": self._snapshots,
            "settlements": self._settlements,
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.output_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved {len(self._snapshots)} snapshots to {self.output_path}")
        logger.info(f"Saved {len(self._settlements)} settlements")


def main():
    parser = argparse.ArgumentParser(description="Record crypto market data")
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path (default: data/recordings/crypto_YYYYMMDD_HHMMSS.json)",
    )
    parser.add_argument(
        "--duration", "-d",
        type=float,
        default=None,
        help="Recording duration in seconds (default: until Ctrl+C)",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=1.0,
        help="Recording interval in seconds (default: 1.0)",
    )
    args = parser.parse_args()

    # Generate output path if not specified
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = project_root / "data" / "recordings" / f"crypto_{timestamp}.json"

    recorder = CryptoMarketRecorder(
        output_path=output_path,
        record_interval_sec=args.interval,
    )

    recorder.start(duration_sec=args.duration)

    return 0


if __name__ == "__main__":
    sys.exit(main())
