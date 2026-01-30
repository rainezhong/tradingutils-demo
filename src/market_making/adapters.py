"""Adapters to connect core infrastructure to market-making interfaces.

These adapters wrap existing concrete implementations to conform
to the abstract interfaces defined in this module.
"""

from datetime import datetime
from typing import Callable, Optional

from ..core.api_client import KalshiClient
from ..core.models import Snapshot
from ..core.utils import parse_iso_timestamp, utc_now
from .constants import CENTS_TO_PROB
from .interfaces import (
    APIClient,
    DataProvider,
    DataUnavailableError,
    MarketNotFoundError,
    OrderError,
)
from .models import Fill, MarketState


class KalshiDataAdapter(DataProvider):
    """Adapter that wraps KalshiClient as a DataProvider.

    Converts core Snapshot data to MarketState for market-making.

    Example:
        >>> from src.core.api_client import KalshiClient
        >>> client = KalshiClient()
        >>> provider = KalshiDataAdapter(client)
        >>> state = provider.get_current_market("TICKER")
    """

    def __init__(self, client: KalshiClient) -> None:
        """Initialize adapter with a KalshiClient.

        Args:
            client: Core KalshiClient instance.
        """
        self._client = client
        self._subscriptions: dict[str, tuple[str, Callable]] = {}
        self._next_sub_id = 0

    def get_current_market(self, ticker: str) -> MarketState:
        """Get current market state from Kalshi API.

        Args:
            ticker: Market identifier.

        Returns:
            Current MarketState.

        Raises:
            MarketNotFoundError: If ticker doesn't exist.
            DataUnavailableError: If API request fails.
        """
        try:
            # Get orderbook for depth information
            orderbook = self._client.get_orderbook(ticker)
            market = self._client.get_market(ticker)

            market_data = market.get("market", {})

            # Create snapshot from orderbook
            snapshot = Snapshot.from_orderbook(
                ticker=ticker,
                orderbook=orderbook,
                volume_24h=market_data.get("volume_24h"),
                open_interest=market_data.get("open_interest"),
            )

            return MarketState.from_snapshot(snapshot)

        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "404" in error_msg:
                raise MarketNotFoundError(f"Market {ticker} not found")
            raise DataUnavailableError(f"Failed to get market data: {e}")

    def get_multiple_markets(self, tickers: list[str]) -> dict[str, MarketState]:
        """Get current state for multiple markets.

        Args:
            tickers: List of market identifiers.

        Returns:
            Dictionary mapping ticker to MarketState.
        """
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = self.get_current_market(ticker)
            except (MarketNotFoundError, DataUnavailableError):
                # Skip markets that can't be fetched
                continue
        return results

    def subscribe_to_updates(
        self,
        ticker: str,
        callback: Callable[[MarketState], None],
    ) -> str:
        """Subscribe to market updates.

        Note: This is a placeholder implementation. Real implementation
        would use websocket connection.

        Args:
            ticker: Market identifier.
            callback: Function to call on updates.

        Returns:
            Subscription ID.
        """
        sub_id = f"sub_{self._next_sub_id}"
        self._next_sub_id += 1
        self._subscriptions[sub_id] = (ticker, callback)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from market updates.

        Args:
            subscription_id: Subscription ID.

        Returns:
            True if unsubscribed successfully.
        """
        if subscription_id in self._subscriptions:
            del self._subscriptions[subscription_id]
            return True
        return False

    def get_available_markets(self) -> list[str]:
        """Get list of available market tickers.

        Returns:
            List of ticker strings.
        """
        markets = self._client.get_all_markets()
        return [m.ticker for m in markets]


class KalshiTradingAdapter(APIClient):
    """Adapter that wraps KalshiClient as an APIClient for trading.

    Note: This is a template implementation. The actual trading API
    requires authentication which the core KalshiClient doesn't have.
    Extend this class with authenticated client for live trading.

    Example:
        >>> # For testing/simulation
        >>> adapter = KalshiTradingAdapter(client)
    """

    def __init__(
        self,
        client: KalshiClient,
        authenticated: bool = False,
    ) -> None:
        """Initialize trading adapter.

        Args:
            client: Core KalshiClient instance.
            authenticated: Whether client has trading auth.
        """
        self._client = client
        self._authenticated = authenticated
        self._orders: dict[str, dict] = {}  # Simulated order tracking
        self._positions: dict[str, int] = {}  # Simulated positions
        self._fills: list[Fill] = []
        self._next_order_id = 1

    def _require_auth(self) -> None:
        """Raise error if not authenticated."""
        if not self._authenticated:
            raise OrderError(
                "Trading requires authentication. "
                "Initialize adapter with authenticated=True and "
                "provide authenticated client."
            )

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place an order.

        Args:
            ticker: Market identifier.
            side: 'BID' or 'ASK'.
            price: Order price (0-1 range).
            size: Number of contracts.

        Returns:
            Order ID.

        Raises:
            OrderError: If order placement fails.
        """
        self._require_auth()

        # TODO: Implement actual API call when authenticated
        # For now, create simulated order
        order_id = f"ORD{self._next_order_id:06d}"
        self._next_order_id += 1

        self._orders[order_id] = {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "price": price,
            "size": size,
            "filled_size": 0,
            "status": "open",
            "created_at": utc_now().isoformat(),
        }

        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Order ID.

        Returns:
            True if cancelled.
        """
        self._require_auth()

        if order_id not in self._orders:
            return False

        order = self._orders[order_id]
        if order["status"] in ("filled", "cancelled"):
            return False

        order["status"] = "cancelled"
        return True

    def get_order_status(self, order_id: str) -> dict:
        """Get order status.

        Args:
            order_id: Order ID.

        Returns:
            Order status dictionary.
        """
        if order_id not in self._orders:
            return {"status": "unknown", "error": "Order not found"}

        order = self._orders[order_id]
        return {
            "status": order["status"],
            "filled_size": order["filled_size"],
            "remaining_size": order["size"] - order["filled_size"],
            "avg_fill_price": order["price"] if order["filled_size"] > 0 else None,
        }

    def get_market_data(self, ticker: str) -> MarketState:
        """Get current market state.

        Args:
            ticker: Market identifier.

        Returns:
            Current MarketState.
        """
        # Delegate to data adapter logic
        try:
            orderbook = self._client.get_orderbook(ticker)
            market = self._client.get_market(ticker)
            market_data = market.get("market", {})

            snapshot = Snapshot.from_orderbook(
                ticker=ticker,
                orderbook=orderbook,
                volume_24h=market_data.get("volume_24h"),
                open_interest=market_data.get("open_interest"),
            )

            return MarketState.from_snapshot(snapshot)

        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "404" in error_msg:
                raise MarketNotFoundError(f"Market {ticker} not found")
            raise DataUnavailableError(f"Failed to get market data: {e}")

    def get_positions(self) -> dict[str, int]:
        """Get current positions.

        Returns:
            Dictionary mapping ticker to position.
        """
        self._require_auth()
        # TODO: Implement actual API call
        return self._positions.copy()

    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[Fill]:
        """Get recent fills.

        Args:
            ticker: Filter by ticker.
            limit: Maximum fills to return.

        Returns:
            List of Fill objects.
        """
        self._require_auth()

        fills = self._fills
        if ticker:
            fills = [f for f in fills if f.ticker == ticker]

        return fills[:limit]

    def simulate_fill(
        self,
        order_id: str,
        fill_size: Optional[int] = None,
    ) -> Optional[Fill]:
        """Simulate a fill for testing.

        Args:
            order_id: Order to fill.
            fill_size: Size to fill (defaults to full order).

        Returns:
            Fill object if successful.
        """
        if order_id not in self._orders:
            return None

        order = self._orders[order_id]
        if order["status"] != "open":
            return None

        remaining = order["size"] - order["filled_size"]
        size = min(fill_size or remaining, remaining)

        if size <= 0:
            return None

        fill = Fill(
            order_id=order_id,
            ticker=order["ticker"],
            side=order["side"],
            price=order["price"],
            size=size,
            timestamp=utc_now(),
        )

        order["filled_size"] += size
        if order["filled_size"] >= order["size"]:
            order["status"] = "filled"
        else:
            order["status"] = "partial"

        # Update position
        ticker = order["ticker"]
        position_delta = size if order["side"] == "BID" else -size
        self._positions[ticker] = self._positions.get(ticker, 0) + position_delta

        self._fills.insert(0, fill)
        return fill
