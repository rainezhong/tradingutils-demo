"""Polymarket-specific data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class OrderSide(str, Enum):
    """Order side enum."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type enum."""

    GTC = "GTC"  # Good till canceled
    FOK = "FOK"  # Fill or kill
    GTD = "GTD"  # Good till date


class OrderStatus(str, Enum):
    """Order status enum."""

    LIVE = "LIVE"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"


@dataclass
class PolymarketMarket:
    """Represents a Polymarket market/condition.

    Attributes:
        condition_id: Unique condition identifier
        question_id: Question identifier
        question: Market question text
        description: Market description
        end_date: When the market ends
        tokens: List of token info (YES/NO outcomes)
        active: Whether market is active
        closed: Whether market is closed
        minimum_order_size: Minimum order size
        minimum_tick_size: Minimum price tick
    """

    condition_id: str
    question_id: str
    question: str
    description: str = ""
    end_date: Optional[datetime] = None
    tokens: List[Dict[str, Any]] = field(default_factory=list)
    active: bool = True
    closed: bool = False
    minimum_order_size: float = 1.0
    minimum_tick_size: float = 0.01

    @classmethod
    def from_api_response(cls, data: dict) -> "PolymarketMarket":
        """Create from API response."""
        end_date = None
        if data.get("end_date_iso"):
            try:
                end_date = datetime.fromisoformat(
                    data["end_date_iso"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return cls(
            condition_id=data.get("condition_id", ""),
            question_id=data.get("question_id", ""),
            question=data.get("question", ""),
            description=data.get("description", ""),
            end_date=end_date,
            tokens=data.get("tokens", []),
            active=data.get("active", True),
            closed=data.get("closed", False),
            minimum_order_size=float(data.get("minimum_order_size", 1.0)),
            minimum_tick_size=float(data.get("minimum_tick_size", 0.01)),
        )


@dataclass
class PolymarketOrder:
    """Represents a Polymarket CLOB order.

    Attributes:
        order_id: Unique order identifier
        market: Market/condition ID
        asset_id: Token ID
        side: BUY or SELL
        price: Order price (0-1)
        original_size: Original order size
        size_matched: Size that has been matched
        status: Order status
        owner: Order owner address
        created_at: When order was created
        expiration: Order expiration timestamp
        order_type: GTC, FOK, or GTD
    """

    order_id: str
    market: str
    asset_id: str
    side: OrderSide
    price: float
    original_size: float
    size_matched: float = 0.0
    status: OrderStatus = OrderStatus.LIVE
    owner: str = ""
    created_at: Optional[datetime] = None
    expiration: Optional[int] = None
    order_type: OrderType = OrderType.GTC

    @property
    def remaining_size(self) -> float:
        """Get remaining unfilled size."""
        return self.original_size - self.size_matched

    @property
    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.status == OrderStatus.LIVE

    @classmethod
    def from_api_response(cls, data: dict) -> "PolymarketOrder":
        """Create from API response."""
        created_at = None
        if data.get("created_at"):
            try:
                created_at = datetime.fromisoformat(
                    str(data["created_at"]).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return cls(
            order_id=data.get("id", ""),
            market=data.get("market", ""),
            asset_id=data.get("asset_id", ""),
            side=OrderSide(data.get("side", "BUY").upper()),
            price=float(data.get("price", 0)),
            original_size=float(data.get("original_size", 0)),
            size_matched=float(data.get("size_matched", 0)),
            status=OrderStatus(data.get("status", "LIVE").upper()),
            owner=data.get("owner", ""),
            created_at=created_at,
            expiration=data.get("expiration"),
            order_type=OrderType(data.get("order_type", "GTC").upper()),
        )


@dataclass
class PolymarketTrade:
    """Represents a Polymarket trade/fill.

    Attributes:
        trade_id: Unique trade identifier
        market: Market/condition ID
        asset_id: Token ID
        side: BUY or SELL
        price: Execution price
        size: Trade size
        fee: Trading fee
        timestamp: When trade occurred
        maker_order_id: Maker order ID
        taker_order_id: Taker order ID
    """

    trade_id: str
    market: str
    asset_id: str
    side: OrderSide
    price: float
    size: float
    fee: float = 0.0
    timestamp: Optional[datetime] = None
    maker_order_id: str = ""
    taker_order_id: str = ""

    @classmethod
    def from_api_response(cls, data: dict) -> "PolymarketTrade":
        """Create from API response."""
        timestamp = None
        if data.get("timestamp"):
            try:
                timestamp = datetime.fromisoformat(
                    str(data["timestamp"]).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return cls(
            trade_id=data.get("id", ""),
            market=data.get("market", ""),
            asset_id=data.get("asset_id", ""),
            side=OrderSide(data.get("side", "BUY").upper()),
            price=float(data.get("price", 0)),
            size=float(data.get("size", 0)),
            fee=float(data.get("fee", 0)),
            timestamp=timestamp,
            maker_order_id=data.get("maker_order_id", ""),
            taker_order_id=data.get("taker_order_id", ""),
        )


@dataclass
class OrderBookLevel:
    """A single price level in the order book."""

    price: float
    size: float

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {"price": self.price, "size": self.size}


@dataclass
class PolymarketOrderBook:
    """Order book for a Polymarket token.

    Attributes:
        asset_id: Token ID
        market: Market/condition ID
        bids: List of bid levels (price, size)
        asks: List of ask levels (price, size)
        timestamp: When book was captured
    """

    asset_id: str
    market: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    timestamp: Optional[datetime] = None

    @property
    def best_bid(self) -> Optional[float]:
        """Get best bid price."""
        if self.bids:
            return max(b.price for b in self.bids)
        return None

    @property
    def best_ask(self) -> Optional[float]:
        """Get best ask price."""
        if self.asks:
            return min(a.price for a in self.asks)
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Get mid price."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        """Get bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def bid_depth(self) -> float:
        """Get total bid depth."""
        return sum(b.size for b in self.bids)

    @property
    def ask_depth(self) -> float:
        """Get total ask depth."""
        return sum(a.size for a in self.asks)

    @classmethod
    def from_api_response(cls, data: dict, asset_id: str, market: str) -> "PolymarketOrderBook":
        """Create from API response."""
        bids = [
            OrderBookLevel(price=float(level["price"]), size=float(level["size"]))
            for level in data.get("bids", [])
        ]
        asks = [
            OrderBookLevel(price=float(level["price"]), size=float(level["size"]))
            for level in data.get("asks", [])
        ]

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        return cls(
            asset_id=asset_id,
            market=market,
            bids=bids,
            asks=asks,
            timestamp=datetime.now(),
        )
