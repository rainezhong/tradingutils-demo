"""Quote management for order lifecycle tracking.

Provides reliable quote placement, cancellation, and fill detection
with built-in retry logic and error handling.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..core.utils import utc_now
from ..market_making.interfaces import APIClient, OrderError
from ..market_making.models import Fill, Quote


logger = logging.getLogger(__name__)


@dataclass
class TrackedQuote:
    """Internal quote tracking with metadata."""

    quote: Quote
    placement_time: datetime
    last_status_check: Optional[datetime] = None
    filled_size: int = 0
    status: str = "open"  # open, partial, filled, cancelled


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay between retries in seconds.
        max_delay: Maximum delay between retries in seconds.
        exponential_base: Base for exponential backoff calculation.
    """

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0


class QuoteManager:
    """Manages quote lifecycle with reliability features.

    Handles quote placement, cancellation, and fill detection with
    built-in retry logic, exponential backoff, and error handling.

    Attributes:
        api_client: The APIClient implementation for exchange operations.
        retry_config: Configuration for retry behavior.

    Example:
        >>> from src.execution.mock_api_client import MockAPIClient
        >>> client = MockAPIClient()
        >>> manager = QuoteManager(client)
        >>> quote = Quote("TICKER", "BID", 0.45, 20)
        >>> placed = manager.place_quote(quote)
        >>> placed.order_id is not None
        True
    """

    def __init__(
        self,
        api_client: APIClient,
        retry_config: Optional[RetryConfig] = None,
    ) -> None:
        """Initialize QuoteManager.

        Args:
            api_client: APIClient implementation for exchange operations.
            retry_config: Optional retry configuration (uses defaults if not provided).
        """
        self._api_client = api_client
        self._retry_config = retry_config or RetryConfig()
        self._active_orders: dict[str, TrackedQuote] = {}

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for retry attempt with exponential backoff.

        Args:
            attempt: Current retry attempt number (0-indexed).

        Returns:
            Delay in seconds.
        """
        delay = self._retry_config.base_delay * (
            self._retry_config.exponential_base ** attempt
        )
        return min(delay, self._retry_config.max_delay)

    def _retry_operation(
        self,
        operation: str,
        func: callable,
        *args,
        **kwargs,
    ) -> any:
        """Execute an operation with retry logic.

        Args:
            operation: Description of the operation for logging.
            func: Function to execute.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            Result of the function.

        Raises:
            OrderError: If all retries exhausted.
        """
        last_error = None
        for attempt in range(self._retry_config.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except OrderError as e:
                last_error = e
                if attempt < self._retry_config.max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(
                        f"{operation} failed (attempt {attempt + 1}/"
                        f"{self._retry_config.max_retries + 1}): {e}. "
                        f"Retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"{operation} failed after "
                        f"{self._retry_config.max_retries + 1} attempts: {e}"
                    )

        raise last_error

    def place_quote(self, quote: Quote) -> Quote:
        """Place a quote on the exchange.

        Takes a Quote object and submits it to the exchange via the API client.
        Returns a new Quote with the order_id populated.

        Args:
            quote: Quote to place (order_id should be None).

        Returns:
            New Quote object with order_id set.

        Raises:
            OrderError: If placement fails after all retries.

        Example:
            >>> quote = Quote("TICKER", "BID", 0.45, 20)
            >>> placed = manager.place_quote(quote)
            >>> placed.order_id
            'order_123'
        """
        if quote.order_id is not None:
            logger.warning(f"Quote already has order_id: {quote.order_id}")
            return quote

        order_id = self._retry_operation(
            f"Place quote {quote.ticker} {quote.side}@{quote.price}",
            self._api_client.place_order,
            quote.ticker,
            quote.side,
            quote.price,
            quote.size,
        )

        # Create new Quote with order_id
        placed_quote = Quote(
            ticker=quote.ticker,
            side=quote.side,
            price=quote.price,
            size=quote.size,
            timestamp=quote.timestamp,
            order_id=order_id,
        )

        # Track the order
        tracked = TrackedQuote(
            quote=placed_quote,
            placement_time=utc_now(),
        )
        self._active_orders[order_id] = tracked

        logger.info(
            f"Placed quote: {order_id} {quote.ticker} "
            f"{quote.side}@{quote.price} x{quote.size}"
        )

        return placed_quote

    def cancel_quote(self, order_id: str) -> bool:
        """Cancel an active quote.

        Args:
            order_id: Order ID to cancel.

        Returns:
            True if cancellation successful, False otherwise.

        Example:
            >>> success = manager.cancel_quote("order_123")
            >>> success
            True
        """
        if order_id not in self._active_orders:
            logger.warning(f"Order not tracked: {order_id}")
            return False

        try:
            success = self._api_client.cancel_order(order_id)
        except OrderError as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

        if success:
            tracked = self._active_orders[order_id]
            tracked.status = "cancelled"
            del self._active_orders[order_id]
            logger.info(f"Cancelled order: {order_id}")
        else:
            logger.warning(f"Cancel request failed for order: {order_id}")

        return success

    def check_fills(self) -> list[Fill]:
        """Check for fills on active orders.

        Queries status of all active orders, detects fills,
        and removes fully filled orders from tracking.

        Returns:
            List of new Fill objects detected since last check.

        Example:
            >>> fills = manager.check_fills()
            >>> len(fills)
            2
        """
        fills: list[Fill] = []
        orders_to_remove: list[str] = []

        for order_id, tracked in list(self._active_orders.items()):
            try:
                status = self._api_client.get_order_status(order_id)
            except OrderError as e:
                logger.warning(f"Failed to get status for {order_id}: {e}")
                continue

            tracked.last_status_check = utc_now()
            new_filled = status["filled_size"] - tracked.filled_size

            if new_filled > 0:
                # Create Fill for newly filled portion
                fill = Fill(
                    order_id=order_id,
                    ticker=tracked.quote.ticker,
                    side=tracked.quote.side,
                    price=status.get("avg_fill_price") or tracked.quote.price,
                    size=new_filled,
                    timestamp=utc_now(),
                )
                fills.append(fill)
                tracked.filled_size = status["filled_size"]

                logger.info(
                    f"Fill detected: {order_id} {fill.ticker} "
                    f"{fill.side}@{fill.price} x{fill.size}"
                )

            # Update status and check if order is complete
            tracked.status = status["status"]
            if status["status"] in ("filled", "cancelled"):
                orders_to_remove.append(order_id)

        # Remove completed orders from tracking
        for order_id in orders_to_remove:
            del self._active_orders[order_id]
            logger.debug(f"Removed completed order from tracking: {order_id}")

        return fills

    def get_active_quotes(self, ticker: Optional[str] = None) -> list[Quote]:
        """Get all active quotes, optionally filtered by ticker.

        Args:
            ticker: Filter by ticker (optional, returns all if None).

        Returns:
            List of active Quote objects.

        Example:
            >>> quotes = manager.get_active_quotes("TICKER")
            >>> len(quotes)
            2
        """
        quotes = []
        for tracked in self._active_orders.values():
            if ticker is None or tracked.quote.ticker == ticker:
                quotes.append(tracked.quote)
        return quotes

    def get_tracked_quote(self, order_id: str) -> Optional[TrackedQuote]:
        """Get detailed tracking info for an order.

        Args:
            order_id: Order ID to look up.

        Returns:
            TrackedQuote or None if not found.
        """
        return self._active_orders.get(order_id)

    def cancel_all(self, ticker: Optional[str] = None) -> int:
        """Cancel all active quotes, optionally filtered by ticker.

        Args:
            ticker: Filter by ticker (optional, cancels all if None).

        Returns:
            Number of orders successfully cancelled.

        Example:
            >>> cancelled = manager.cancel_all("TICKER")
            >>> cancelled
            5
        """
        cancelled = 0
        orders_to_cancel = []

        for order_id, tracked in self._active_orders.items():
            if ticker is None or tracked.quote.ticker == ticker:
                orders_to_cancel.append(order_id)

        for order_id in orders_to_cancel:
            if self.cancel_quote(order_id):
                cancelled += 1

        return cancelled

    @property
    def active_order_count(self) -> int:
        """Get count of active orders."""
        return len(self._active_orders)

    def get_active_order_ids(self) -> list[str]:
        """Get list of active order IDs."""
        return list(self._active_orders.keys())
