"""Kalshi-specific type definitions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class KalshiBalance:
    """Account balance information."""

    balance_cents: int
    portfolio_value_cents: int
    available_balance_cents: int

    @property
    def balance(self) -> float:
        """Balance in dollars."""
        return self.balance_cents / 100.0

    @property
    def available(self) -> float:
        """Available balance in dollars."""
        return self.available_balance_cents / 100.0


@dataclass
class KalshiPosition:
    """A position in a market."""

    ticker: str
    market_id: str
    position: int  # Positive = long YES, negative = long NO
    avg_price_cents: int
    realized_pnl_cents: int

    @property
    def is_long_yes(self) -> bool:
        return self.position > 0

    @property
    def quantity(self) -> int:
        return abs(self.position)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "KalshiPosition":
        return cls(
            ticker=data.get("ticker", ""),
            market_id=data.get("market_id", ""),
            position=data.get("position", 0),
            avg_price_cents=int(data.get("market_exposure", 0) * 100)
            if data.get("position", 0)
            else 0,
            realized_pnl_cents=int(data.get("realized_pnl", 0) * 100),
        )


@dataclass
class KalshiMarketData:
    """Raw market data from Kalshi API."""

    ticker: str
    event_ticker: str
    title: str
    status: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    volume: int
    open_interest: int
    close_time: Optional[datetime] = None

    @property
    def mid_price(self) -> float:
        """Midpoint price for YES contracts (in dollars)."""
        return (self.yes_bid + self.yes_ask) / 200.0

    @property
    def spread(self) -> int:
        """Bid-ask spread in cents."""
        return self.yes_ask - self.yes_bid

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "KalshiMarketData":
        close_time = None
        if data.get("close_time"):
            try:
                close_time = datetime.fromisoformat(
                    data["close_time"].replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        return cls(
            ticker=data.get("ticker", ""),
            event_ticker=data.get("event_ticker", ""),
            title=data.get("title", ""),
            status=data.get("status", ""),
            yes_bid=data.get("yes_bid") or 0,
            yes_ask=data.get("yes_ask") or 100,
            no_bid=data.get("no_bid") or 0,
            no_ask=data.get("no_ask") or 100,
            volume=data.get("volume") or 0,
            open_interest=data.get("open_interest") or 0,
            close_time=close_time,
        )


@dataclass
class KalshiOrderResponse:
    """Response from order submission."""

    order_id: str
    ticker: str
    status: str
    side: str
    action: str
    count: int
    filled_count: int
    price_cents: int
    created_time: datetime

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "KalshiOrderResponse":
        created = datetime.now()
        if data.get("created_time"):
            try:
                created = datetime.fromisoformat(
                    data["created_time"].replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        return cls(
            order_id=data.get("order_id", ""),
            ticker=data.get("ticker", ""),
            status=data.get("status", "pending"),
            side=data.get("side", "yes"),
            action=data.get("action", "buy"),
            count=data.get("count", 0),
            filled_count=data.get("filled_count", 0),
            price_cents=data.get("yes_price") or data.get("no_price") or 0,
            created_time=created,
        )


@dataclass
class KalshiFill:
    """A fill/trade from Kalshi."""

    trade_id: str
    order_id: str
    ticker: str
    side: str
    action: str
    count: int
    price_cents: int
    created_time: float

    @property
    def notional_cents(self) -> int:
        return self.price_cents * self.count

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "KalshiFill":
        return cls(
            trade_id=data.get("trade_id", ""),
            order_id=data.get("order_id", ""),
            ticker=data.get("ticker", ""),
            side=data.get("side", "yes"),
            action=data.get("action", "buy"),
            count=data.get("count", 0),
            price_cents=data.get("yes_price") or data.get("no_price") or 0,
            created_time=data.get("created_time", 0),
        )
