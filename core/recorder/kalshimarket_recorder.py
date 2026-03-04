"""Kalshi Market Recorder - Records live Kalshi market prices for replay.

Captures snapshots of a single Kalshi market's bid/ask/volume/orderbook
at regular intervals. Uses I_ExchangeClient for market data and
KalshiExchangeClient.get_orderbook() for depth data.

Records into MarketSeries (single-ticker) from recorder_types.
"""

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Optional

from core.exchange_client import I_ExchangeClient
from .recorder_types import (
    MarketFrame,
    MarketSeries,
    MarketSeriesMetadata,
    OrderbookSnapshot,
)

logger = logging.getLogger(__name__)


class KalshiMarketRecorder:
    """Records live Kalshi market data for a single ticker at a given poll frequency.

    Captures MarketFrame snapshots including orderbook depth at regular intervals.

    Example:
        >>> from core.exchange_client import KalshiExchangeClient
        >>>
        >>> client = KalshiExchangeClient.from_env()
        >>> await client.connect()
        >>>
        >>> recorder = KalshiMarketRecorder(
        ...     ticker="KXNBAGAME-26FEB06-LAL",
        ...     poll_interval_ms=500,
        ... )
        >>> series = await recorder.start_async(client)
        >>> series.save("data/recordings/KXNBAGAME-26FEB06-LAL.json")
    """

    def __init__(
        self,
        ticker: str,
        poll_interval_ms: int = 500,
        orderbook_depth: int = 10,
    ):
        """Initialize the recorder.

        Args:
            ticker: Kalshi market ticker to record
            poll_interval_ms: How often to capture snapshots in milliseconds (default 500)
            orderbook_depth: Number of orderbook levels to capture per side (default 10)
        """
        self.ticker = ticker
        self.poll_interval_ms = poll_interval_ms
        self.orderbook_depth = orderbook_depth

        self._series = MarketSeries(
            metadata=MarketSeriesMetadata(
                ticker=ticker,
                date=datetime.now().strftime("%Y-%m-%d"),
                recorded_at=datetime.now().isoformat(),
                poll_interval_ms=poll_interval_ms,
            )
        )

        self._stop_event = threading.Event()
        self._recording_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def get_series(self) -> MarketSeries:
        """Get the current MarketSeries."""
        with self._lock:
            return self._series

    async def start_async(
        self,
        exchange_client: I_ExchangeClient,
        max_duration_seconds: Optional[int] = None,
    ) -> MarketSeries:
        """Start recording asynchronously (runs until market closes or stopped).

        Args:
            exchange_client: Exchange client for fetching market data.
                             Must have get_orderbook() method (e.g. KalshiExchangeClient).
            max_duration_seconds: Optional max recording duration

        Returns:
            The completed MarketSeries
        """
        poll_interval = self.poll_interval_ms / 1000.0
        start_time = time.time()

        logger.info(f"Starting recording for ticker: {self.ticker}")
        logger.info(f"Poll interval: {self.poll_interval_ms}ms")
        logger.info(f"Orderbook depth: {self.orderbook_depth}")

        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                frame = await self._capture_frame(exchange_client)

                if frame:
                    with self._lock:
                        self._series.add_frame(frame)

                    # Log progress every 30 frames
                    if len(self._series) % 30 == 0:
                        ob_info = ""
                        if frame.orderbook:
                            ob_info = (
                                f" | ob_depth: yes={frame.orderbook.total_yes_depth} "
                                f"no={frame.orderbook.total_no_depth}"
                            )
                        logger.info(
                            f"Captured {len(self._series)} frames | "
                            f"yes_bid={frame.yes_bid} yes_ask={frame.yes_ask} "
                            f"vol={frame.volume} | status={frame.market_status}"
                            f"{ob_info}"
                        )

                    # Check if market closed
                    if frame.market_status == "closed":
                        logger.info(f"Market closed for {self.ticker}")
                        self._series.metadata.final_status = "closed"
                        break

                # Check max duration
                if (
                    max_duration_seconds
                    and (time.time() - start_time) > max_duration_seconds
                ):
                    logger.info(f"Max duration reached ({max_duration_seconds}s)")
                    break

            except Exception as e:
                logger.error(f"Error capturing frame: {e}")

            await asyncio.sleep(poll_interval)

        logger.info(f"Recording complete. Total frames: {len(self._series)}")
        return self._series

    def start(self, exchange_client: I_ExchangeClient) -> MarketSeries:
        """Start recording synchronously (blocking)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.start_async(exchange_client))
        finally:
            loop.close()

    def start_background(self, exchange_client: I_ExchangeClient) -> None:
        """Start recording in a background thread."""
        if self._recording_thread and self._recording_thread.is_alive():
            raise RuntimeError("Recording already in progress")

        self._stop_event.clear()
        self._recording_thread = threading.Thread(
            target=self.start,
            args=(exchange_client,),
            daemon=True,
        )
        self._recording_thread.start()

    def stop(self) -> MarketSeries:
        """Stop recording and return the series."""
        self._stop_event.set()
        if self._recording_thread:
            self._recording_thread.join(timeout=5.0)
        return self.get_series()

    async def _capture_frame(
        self,
        exchange_client: I_ExchangeClient,
    ) -> Optional[MarketFrame]:
        """Capture a single market data snapshot with orderbook depth."""
        try:
            # Fetch market data + orderbook in parallel
            market = await exchange_client.request_market(self.ticker)

            # Fetch orderbook (Kalshi-specific method)
            orderbook = None
            if hasattr(exchange_client, "get_orderbook"):
                try:
                    ob_data = await exchange_client.get_orderbook(
                        self.ticker, depth=self.orderbook_depth
                    )
                    # Kalshi API returns {"yes": [[price, qty], ...], "no": [[price, qty], ...]}
                    orderbook = OrderbookSnapshot(
                        yes=ob_data.get("yes", []),
                        no=ob_data.get("no", []),
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch orderbook: {e}")

            return MarketFrame(
                timestamp=int(time.time() * 1000),  # ms epoch
                ticker=self.ticker,
                yes_bid=market.yes_bid or 0,
                yes_ask=market.yes_ask or 100,
                volume=market.volume or 0,
                market_status=getattr(market, "status", "open") or "open",
                orderbook=orderbook,
            )
        except Exception as e:
            logger.error(f"Error fetching market data for {self.ticker}: {e}")
            return None

    def save(self, filepath: str) -> None:
        """Save recording to JSON file."""
        series = self.get_series()
        series.save(filepath)
        logger.info(f"Saved recording to {filepath} ({len(series)} frames)")

    @classmethod
    def load(cls, filepath: str) -> "KalshiMarketRecorder":
        """Load a recording from file into a recorder instance."""
        series = MarketSeries.load(filepath)

        recorder = cls(
            ticker=series.metadata.ticker,
            poll_interval_ms=series.metadata.poll_interval_ms,
        )
        recorder._series = series

        logger.info(f"Loaded recording from {filepath}")
        logger.info(f"Ticker: {series.metadata.ticker}")
        logger.info(f"Total frames: {len(series)}")

        return recorder
