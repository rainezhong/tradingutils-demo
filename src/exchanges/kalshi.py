"""Kalshi exchange implementation.

This module provides the KalshiExchange class which implements the ExchangeClient
interface for trading on the Kalshi prediction market exchange.
"""

from datetime import datetime
from typing import Dict, List, Optional
import uuid

from ..core.api_client import KalshiClient
from ..core.config import Config, get_config
from ..core.exchange import ExchangeClient, Order, OrderBook, TradableMarket
from ..core.models import Fill, Market, Position
from ..core.utils import setup_logger, utc_now

logger = setup_logger(__name__)


class KalshiExchange(ExchangeClient):
    """Kalshi implementation of ExchangeClient.

    This class wraps the KalshiClient to provide the exchange-agnostic interface
    for trading on Kalshi markets.

    Example:
        exchange = KalshiExchange()
        market = exchange.get_market("AAPL-24JAN-100")
        order = market.buy(price=45, size=10)
    """

    def __init__(self, config: Optional[Config] = None):
        """Initialize the Kalshi exchange client.

        Args:
            config: Optional configuration. Uses global config if not provided.
        """
        self._config = config or get_config()
        self._api = KalshiClient(self._config)

        # Cache for positions and orders (would be populated from API in real implementation)
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}

        if self._api.is_authenticated:
            logger.info("KalshiExchange initialized with authentication")
        else:
            logger.warning(
                "KalshiExchange initialized without authentication - trading disabled"
            )

    @property
    def name(self) -> str:
        """Exchange name."""
        return "kalshi"

    def get_market(self, ticker: str) -> TradableMarket:
        """Get a tradable market by ticker.

        Args:
            ticker: The market ticker

        Returns:
            TradableMarket instance
        """
        data = self._api.get_market(ticker)
        market_data = data.get("market", data)
        market = Market.from_api_response(market_data)
        return TradableMarket(market, self)

    def get_markets(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List[TradableMarket]:
        """Get multiple tradable markets.

        Args:
            status: Filter by status (e.g., 'open', 'closed')
            limit: Maximum number of markets to return

        Returns:
            List of TradableMarket instances
        """
        response = self._api.get_markets(status=status, limit=limit)
        markets_data = response.get("markets", [])

        tradable_markets = []
        for market_data in markets_data[:limit]:
            try:
                market = Market.from_api_response(market_data)
                tradable_markets.append(TradableMarket(market, self))
            except Exception as e:
                logger.warning(f"Failed to parse market: {e}")

        return tradable_markets

    def get_balance(self) -> float:
        """Get account balance in dollars.

        Returns:
            Account balance. Returns 0 if not authenticated.
        """
        if not self._api.is_authenticated:
            logger.warning("Cannot get balance - not authenticated")
            return 0.0

        try:
            response = self._api._request("GET", "/portfolio/balance")
            # Balance is returned in cents, convert to dollars
            balance_cents = response.get("balance", 0)
            return balance_cents / 100.0
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0.0

    def get_all_positions(self) -> Dict[str, Position]:
        """Get all positions across all markets.

        Returns:
            Dict mapping ticker to Position
        """
        if not self._api.is_authenticated:
            logger.warning("Cannot get positions - not authenticated")
            return {}

        try:
            response = self._api._request("GET", "/portfolio/positions")
            positions = {}

            for pos_data in response.get("market_positions", []):
                ticker = pos_data.get("ticker", "")
                if ticker:
                    position = Position(
                        ticker=ticker,
                        size=pos_data.get("position", 0),
                        entry_price=pos_data.get("average_cost", 0),
                        current_price=pos_data.get("market_exposure", 0),
                        realized_pnl=pos_data.get("realized_pnl", 0) / 100.0,
                    )
                    positions[ticker] = position

            self._positions = positions
            return positions
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return {}

    def get_all_orders(self, status: Optional[str] = None) -> List[Order]:
        """Get all orders across all markets.

        Args:
            status: Filter by status (e.g., 'open', 'filled')

        Returns:
            List of Order instances
        """
        if not self._api.is_authenticated:
            logger.warning("Cannot get orders - not authenticated")
            return []

        try:
            params = {}
            if status:
                params["status"] = status

            response = self._api._request("GET", "/portfolio/orders", params=params)
            orders = []

            for order_data in response.get("orders", []):
                order = self._parse_order(order_data)
                if order:
                    orders.append(order)
                    self._orders[order.order_id] = order

            return orders
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            return []

    def _place_order(
        self, ticker: str, side: str, price: float, size: int
    ) -> Order:
        """Place an order on the exchange.

        Args:
            ticker: Market identifier
            side: 'buy' or 'sell' (from YES perspective)
            price: Order price in cents (0-100) - always in YES terms
            size: Number of contracts

        Returns:
            Order instance with order_id populated
            
        Note:
            - 'buy' side means buying YES contracts at the given price
            - 'sell' side means selling YES contracts at the given price
              (implemented as buying NO contracts at 100-price)
        """
        if not self._api.is_authenticated:
            raise RuntimeError("Cannot place order - not authenticated")

        try:
            # Build order request
            # All prices are in YES terms from the bot's perspective
            order_request = {
                "ticker": ticker,
                "action": "buy",
                "type": "limit",
                "count": size,
            }
            
            if side == "buy":
                # Buying YES contracts at the given YES price
                order_request["side"] = "yes"
                order_request["yes_price"] = int(price)
            else:
                # Selling YES = Buying NO at (100 - yes_price)
                # If we want to sell YES at 55c, we buy NO at 45c
                order_request["side"] = "no"
                order_request["no_price"] = 100 - int(price)

            logger.info(f"Placing order: {order_request}")
            response = self._api._request("POST", "/portfolio/orders", json_body=order_request)

            order = Order(
                order_id=response.get("order", {}).get("order_id", str(uuid.uuid4())),
                ticker=ticker,
                side=side,
                price=price,
                size=size,
                filled_size=0,
                status="pending",
                created_at=utc_now(),
                exchange=self.name,
            )

            self._orders[order.order_id] = order
            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            raise

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: The order to cancel

        Returns:
            True if successfully canceled
            
        Raises:
            Exception: If cancel fails (including 404 when order already filled)
        """
        if not self._api.is_authenticated:
            raise RuntimeError("Cannot cancel order - not authenticated")

        try:
            self._api._request("DELETE", f"/portfolio/orders/{order_id}")

            if order_id in self._orders:
                self._orders[order_id].status = "canceled"

            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            # Re-raise so caller can detect fills (404 = order already filled)
            raise

    def _get_orderbook(self, ticker: str) -> OrderBook:
        """Get orderbook for a market.

        Args:
            ticker: Market identifier

        Returns:
            OrderBook instance
        """
        response = self._api.get_orderbook(ticker)
        ob_data = response.get("orderbook", {})

        # Parse bids (yes side) and asks (no side)
        # Kalshi returns [[price, size], ...] format
        yes_levels = ob_data.get("yes", [])
        no_levels = ob_data.get("no", [])

        # Bids are yes prices, sorted descending
        bids = [(level[0], level[1]) for level in yes_levels]
        bids.sort(key=lambda x: x[0], reverse=True)

        # Asks are 100 - no price, sorted ascending
        asks = [(100 - level[0], level[1]) for level in no_levels]
        asks.sort(key=lambda x: x[0])

        return OrderBook(
            ticker=ticker,
            bids=bids,
            asks=asks,
            timestamp=utc_now(),
        )

    def _get_position(self, ticker: str) -> Optional[Position]:
        """Get position for a specific market.

        Args:
            ticker: Market identifier

        Returns:
            Position if exists, None otherwise
        """
        if ticker in self._positions:
            return self._positions[ticker]

        # Try to fetch from API
        positions = self.get_all_positions()
        return positions.get(ticker)

    def _get_orders(self, ticker: str, status: Optional[str] = None) -> List[Order]:
        """Get orders for a specific market.

        Args:
            ticker: Market identifier
            status: Filter by status

        Returns:
            List of Order instances
        """
        all_orders = self.get_all_orders(status=status)
        return [o for o in all_orders if o.ticker == ticker]

    def _get_fills(self, ticker: str, limit: int = 100) -> List[Fill]:
        """Get fills for a specific market.

        Args:
            ticker: Market identifier
            limit: Maximum number of fills to return

        Returns:
            List of Fill instances
        """
        if not self._api.is_authenticated:
            logger.warning("Cannot get fills - not authenticated")
            return []

        try:
            params = {"ticker": ticker, "limit": limit}
            response = self._api._request("GET", "/portfolio/fills", params=params)

            fills = []
            for fill_data in response.get("fills", []):
                fill = Fill(
                    ticker=fill_data.get("ticker", ticker),
                    side="BID" if fill_data.get("side") == "yes" else "ASK",
                    price=fill_data.get("price", 0),
                    size=fill_data.get("count", 0),
                    order_id=fill_data.get("order_id", ""),
                    fill_id=fill_data.get("fill_id"),
                    timestamp=datetime.fromisoformat(fill_data["created_time"].replace("Z", "+00:00"))
                    if fill_data.get("created_time")
                    else None,
                    fee=fill_data.get("fee", 0) / 100.0,
                )
                fills.append(fill)

            return fills[:limit]
        except Exception as e:
            logger.error(f"Failed to get fills for {ticker}: {e}")
            return []

    def _get_market_data(self, ticker: str) -> Market:
        """Get fresh market data.

        Args:
            ticker: Market identifier

        Returns:
            Market instance with updated data
        """
        data = self._api.get_market(ticker)
        market_data = data.get("market", data)
        return Market.from_api_response(market_data)

    def _parse_order(self, order_data: dict) -> Optional[Order]:
        """Parse order data from API response.

        Args:
            order_data: Raw order data from API

        Returns:
            Order instance or None if parsing fails
        """
        try:
            # Map Kalshi status to our status
            status_map = {
                "pending": "pending",
                "resting": "open",
                "canceled": "canceled",
                "executed": "filled",
                "partial": "partial",
            }

            kalshi_side = order_data.get("side", "")
            side = "buy" if kalshi_side == "yes" else "sell"

            return Order(
                order_id=order_data.get("order_id", ""),
                ticker=order_data.get("ticker", ""),
                side=side,
                price=order_data.get("yes_price") or order_data.get("no_price") or 0,
                size=order_data.get("original_count", 0),
                filled_size=order_data.get("original_count", 0)
                - order_data.get("remaining_count", 0),
                status=status_map.get(order_data.get("status", ""), "pending"),
                created_at=datetime.fromisoformat(order_data["created_time"].replace("Z", "+00:00"))
                if order_data.get("created_time")
                else None,
                exchange=self.name,
            )
        except Exception as e:
            logger.warning(f"Failed to parse order: {e}")
            return None
