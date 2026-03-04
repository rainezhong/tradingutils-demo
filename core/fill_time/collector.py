"""Depth snapshot collector - hooks into OrderBookManager for rate-limited capture."""

import logging
import time
from typing import Dict

from core.market.orderbook_manager import OrderBookManager, OrderBookState

from .config import FillTimeConfig
from .models import SnapshotRecord
from .snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)


class DepthSnapshotCollector:
    """Collects order book snapshots at configurable intervals.

    Registers as an update listener on OrderBookManager. Rate-limits
    captures to snapshot_interval_seconds per ticker to avoid flooding
    storage while still capturing meaningful depth evolution.
    """

    def __init__(self, config: FillTimeConfig, store: SnapshotStore):
        self._config = config
        self._store = store
        self._last_capture: Dict[str, float] = {}  # ticker -> last capture time
        self._total_captured: int = 0

    def attach(self, manager: OrderBookManager) -> None:
        manager.add_update_listener(self._on_update)
        logger.info(
            f"DepthSnapshotCollector attached (interval={self._config.snapshot_interval_seconds}s)"
        )

    def detach(self, manager: OrderBookManager) -> None:
        manager.remove_update_listener(self._on_update)
        logger.info(
            f"DepthSnapshotCollector detached (captured={self._total_captured})"
        )

    def _on_update(self, ticker: str, book: OrderBookState) -> None:
        now = time.time()
        last = self._last_capture.get(ticker, 0.0)
        if now - last < self._config.snapshot_interval_seconds:
            return

        record = SnapshotRecord.from_orderbook_state(book)
        self._store.write(record)
        self._last_capture[ticker] = now
        self._total_captured += 1

    @property
    def total_captured(self) -> int:
        return self._total_captured
