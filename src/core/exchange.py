"""Exchange-agnostic trading interface.

This module provides abstract and concrete classes for exchange-agnostic trading:
- ExchangeClient: Abstract interface for connecting to any prediction market exchange
- TradableMarket: A market bound to an exchange with trading capabilities
- Order: Standardized order representation
- OrderBook: Standardized orderbook representation
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .models import Fill, Market, Position
from .utils import utc_now

if TYPE_CHECKING:
    pass


@dataclass
class Order:
    """Standardized order representation across exchanges.

    Attributes:
        order_id: Unique identifier for this order
        ticker: Market identifier
        side: Order side ('buy' or 'sell')
        price: Order price (0-100 cents for prediction markets)
        size: Number of contracts
        filled_size: Number of contracts filled
        status: Order status (pending, open, filled, partial, canceled)
        created_at: When the order was created
        exchange: Exchange name (e.g., 'kalshi', 'polymarket')
    """

    order_id: str
    ticker: str
    side: str  # 'buy' or 'sell'
    price: float
    size: int
    filled_size: int = 0
    status: str = "pending"  # pending, open, filled, partial, canceled
    created_at: Optional[datetime] = None
    exchange: Optional[str] = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Order side must be 'buy' or 'sell', got '{self.side}'")
        if self.price < 0:
            raise ValueError(f"Order price cannot be negative, got {self.price}")
        if self.size <= 0:
            raise ValueError(f"Order size must be positive, got {self.size}")
        if self.filled_size < 0:
            raise ValueError(f"filled_size cannot be negative, got {self.filled_size}")
        if self.filled_size > self.size:
            raise ValueError(
                f"filled_size ({self.filled_size}) cannot exceed size ({self.size})"
            )

    @property
    def remaining_size(self) -> int:
        """Return unfilled size."""
        return self.size - self.filled_size

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.filled_size >= self.size

    @property
    def is_active(self) -> bool:
        """Check if order is still active (can be filled)."""
        return self.status in ("pending", "open", "partial")


@dataclass
class OrderBook:
    """Standardized orderbook representation.

    Attributes:
        ticker: Market identifier
        bids: List of (price, size) tuples for buy orders, sorted by price descending
        asks: List of (price, size) tuples for sell orders, sorted by price ascending
        timestamp: When this orderbook snapshot was taken
    """

    ticker: str
    bids: List[Tuple[float, int]]  # (price, size) pairs
    asks: List[Tuple[float, int]]
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def best_bid(self) -> Optional[float]:
        """Get the best (highest) bid price."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Get the best (lowest) ask price."""
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        """Get the spread between best bid and ask."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Get the mid price between best bid and ask."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def bid_depth(self) -> int:
        """Get total size of all bids."""
        return sum(size for _, size in self.bids)

    @property
    def ask_depth(self) -> int:
        """Get total size of all asks."""
        return sum(size for _, size in self.asks)


class ExchangeClient(ABC):
    """Abstract interface for connecting to any prediction market exchange.

    This is the main entry point for interacting with an exchange. Implementations
    should wrap exchange-specific API clients and provide a unified interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name (e.g., 'kalshi', 'polymarket')."""
        pass

    # Market discovery
    @abstractmethod
    def get_market(self, ticker: str) -> "TradableMarket":
        """Get a tradable market by ticker.

        Args:
            ticker: The market identifier

        Returns:
            TradableMarket instance with trading capabilities
        """
        pass

    @abstractmethod
    def get_markets(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List["TradableMarket"]:
        """Get multiple tradable markets.

        Args:
            status: Filter by status (e.g., 'open', 'closed')
            limit: Maximum number of markets to return

        Returns:
            List of TradableMarket instances
        """
        pass

    # Account-level operations
    @abstractmethod
    def get_balance(self) -> float:
        """Get account balance in dollars."""
        pass

    @abstractmethod
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all positions across all markets.

        Returns:
            Dict mapping ticker to Position
        """
        pass

    @abstractmethod
    def get_all_orders(self, status: Optional[str] = None) -> List[Order]:
        """Get all orders across all markets.

        Args:
            status: Filter by status (e.g., 'open', 'filled')

        Returns:
            List of Order instances
        """
        pass

    # Internal methods for TradableMarket to call
    @abstractmethod
    def _place_order(
        self, ticker: str, side: str, price: float, size: int
    ) -> Order:
        """Place an order on the exchange.

        Args:
            ticker: Market identifier
            side: 'buy' or 'sell'
            price: Order price
            size: Number of contracts

        Returns:
            Order instance with order_id populated
        """
        pass

    @abstractmethod
    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: The order to cancel

        Returns:
            True if successfully canceled
        """
        pass

    @abstractmethod
    def _get_orderbook(self, ticker: str) -> OrderBook:
        """Get orderbook for a market.

        Args:
            ticker: Market identifier

        Returns:
            OrderBook instance
        """
        pass

    @abstractmethod
    def _get_position(self, ticker: str) -> Optional[Position]:
        """Get position for a specific market.

        Args:
            ticker: Market identifier

        Returns:
            Position if exists, None otherwise
        """
        pass

    @abstractmethod
    def _get_orders(self, ticker: str, status: Optional[str] = None) -> List[Order]:
        """Get orders for a specific market.

        Args:
            ticker: Market identifier
            status: Filter by status

        Returns:
            List of Order instances
        """
        pass

    @abstractmethod
    def _get_fills(self, ticker: str, limit: int = 100) -> List[Fill]:
        """Get fills for a specific market.

        Args:
            ticker: Market identifier
            limit: Maximum number of fills to return

        Returns:
            List of Fill instances
        """
        pass

    @abstractmethod
    def _get_market_data(self, ticker: str) -> Market:
        """Get fresh market data.

        Args:
            ticker: Market identifier

        Returns:
            Market instance with updated data
        """
        pass


class TradableMarket:
    """A market bound to an exchange with trading capabilities.

    This class wraps a Market dataclass and provides trading methods that
    delegate to the underlying ExchangeClient.
    """

    def __init__(self, market: Market, client: ExchangeClient):
        """Initialize a tradable market.

        Args:
            market: The underlying Market data
            client: The exchange client to use for trading
        """
        self.market = market
        self._client = client

    # Delegate Market properties
    @property
    def ticker(self) -> str:
        """Market ticker/identifier."""
        return self.market.ticker

    @property
    def title(self) -> str:
        """Market title/question."""
        return self.market.title

    @property
    def status(self) -> Optional[str]:
        """Market status (e.g., 'open', 'closed')."""
        return self.market.status

    @property
    def category(self) -> Optional[str]:
        """Market category."""
        return self.market.category

    @property
    def close_time(self) -> Optional[str]:
        """Market close time."""
        return self.market.close_time

    @property
    def volume_24h(self) -> Optional[int]:
        """24-hour trading volume."""
        return self.market.volume_24h

    @property
    def open_interest(self) -> Optional[int]:
        """Open interest."""
        return self.market.open_interest

    @property
    def exchange(self) -> str:
        """Exchange name."""
        return self._client.name

    # Trading operations
    def buy(self, price: float, size: int) -> Order:
        """Place a buy order.

        Args:
            price: Order price (0-100 cents for prediction markets)
            size: Number of contracts to buy

        Returns:
            Order instance with order details
        """
        return self._client._place_order(self.ticker, "buy", price, size)

    def sell(self, price: float, size: int) -> Order:
        """Place a sell order.

        Args:
            price: Order price (0-100 cents for prediction markets)
            size: Number of contracts to sell

        Returns:
            Order instance with order details
        """
        return self._client._place_order(self.ticker, "sell", price, size)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: The order to cancel

        Returns:
            True if successfully canceled
        """
        return self._client._cancel_order(order_id)

    # Position & orders for THIS market
    def get_position(self) -> Optional[Position]:
        """Get position for this market.

        Returns:
            Position if exists, None otherwise
        """
        return self._client._get_position(self.ticker)

    def get_orders(self, status: Optional[str] = None) -> List[Order]:
        """Get orders for this market.

        Args:
            status: Filter by status (e.g., 'open', 'filled')

        Returns:
            List of Order instances
        """
        return self._client._get_orders(self.ticker, status)

    # Market data
    def get_orderbook(self) -> OrderBook:
        """Get current orderbook.

        Returns:
            OrderBook instance with current bids and asks
        """
        return self._client._get_orderbook(self.ticker)

    def get_fills(self, limit: int = 100) -> List[Fill]:
        """Get recent fills for this market.

        Args:
            limit: Maximum number of fills to return

        Returns:
            List of Fill instances
        """
        return self._client._get_fills(self.ticker, limit)

    def refresh(self) -> None:
        """Update market data from the exchange."""
        self.market = self._client._get_market_data(self.ticker)

    def __repr__(self) -> str:
        return f"TradableMarket(ticker={self.ticker!r}, title={self.title!r}, exchange={self.exchange!r})"
