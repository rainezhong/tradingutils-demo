"""Queue position calculator for hypothetical limit orders."""

import logging

from src.core.orderbook_manager import OrderBookState

from .config import FillTimeConfig

logger = logging.getLogger(__name__)


class QueuePositionCalculator:
    """Estimates queue position for a hypothetical limit order.

    Three cases:
    - Price improves on best -> queue = 0 (we're at front, new price level)
    - Price == best -> queue = depth_at_best * position_pct
    - Price worse than best -> queue = all depth between best and our price
    """

    def __init__(self, config: FillTimeConfig):
        self._config = config

    def estimate_queue_position(
        self,
        book: OrderBookState,
        side: str,
        price: int,
    ) -> float:
        """Estimate contracts ahead of us in the queue.

        Args:
            book: Current order book state
            side: "bid" or "ask"
            price: Our hypothetical order price in cents

        Returns:
            Estimated number of contracts ahead in queue.
            Minimum return is 1.0 to avoid division by zero.
        """
        if side == "bid":
            return self._bid_queue(book, price)
        else:
            return self._ask_queue(book, price)

    def _bid_queue(self, book: OrderBookState, price: int) -> float:
        """Queue position for a buy (bid) order."""
        if not book.best_bid:
            return 1.0

        best = book.best_bid.price

        if price > best:
            # Price improves on best bid -> front of new level
            return 1.0

        if price == best:
            # Join existing best bid level
            return max(1.0, book.best_bid.size * self._config.queue_position_pct)

        # Price worse than best -> sum all depth from best down to our price
        queue = 0.0
        for level in book.bids:  # sorted descending
            if level.price > price:
                queue += level.size
            elif level.price == price:
                queue += level.size * self._config.queue_position_pct
                break
            else:
                break  # past our price
        return max(1.0, queue)

    def _ask_queue(self, book: OrderBookState, price: int) -> float:
        """Queue position for a sell (ask) order."""
        if not book.best_ask:
            return 1.0

        best = book.best_ask.price

        if price < best:
            # Price improves on best ask -> front of new level
            return 1.0

        if price == best:
            # Join existing best ask level
            return max(1.0, book.best_ask.size * self._config.queue_position_pct)

        # Price worse than best -> sum all depth from best up to our price
        queue = 0.0
        for level in book.asks:  # sorted ascending
            if level.price < price:
                queue += level.size
            elif level.price == price:
                queue += level.size * self._config.queue_position_pct
                break
            else:
                break  # past our price
        return max(1.0, queue)
