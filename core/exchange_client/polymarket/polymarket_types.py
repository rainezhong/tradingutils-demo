"""Polymarket-specific type definitions."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PolymarketBalance:
    """Account USDC balance information."""

    balance_usdc: float  # USDC balance (e.g. 150.50)

    @property
    def balance(self) -> float:
        """Balance in dollars (USDC is 1:1 USD)."""
        return self.balance_usdc

    @property
    def available(self) -> float:
        """Available balance in dollars."""
        return self.balance_usdc

    @property
    def balance_cents(self) -> int:
        """Balance in cents for interface compatibility."""
        return int(self.balance_usdc * 100)


@dataclass
class PolymarketPosition:
    """A position in a Polymarket market."""

    token_id: str
    condition_id: str
    size: float
    avg_price: float  # 0-1 probability
    side: str  # "YES" or "NO"

    @property
    def size_int(self) -> int:
        return int(self.size)

    @property
    def avg_price_cents(self) -> int:
        return int(self.avg_price * 100)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "PolymarketPosition":
        return cls(
            token_id=data.get("asset", {}).get("token_id", data.get("token_id", "")),
            condition_id=data.get("asset", {}).get(
                "condition_id", data.get("condition_id", "")
            ),
            size=float(data.get("size", 0)),
            avg_price=float(data.get("avg_price", 0)),
            side=data.get("asset", {}).get("outcome", data.get("side", "YES")).upper(),
        )


@dataclass
class PolymarketMarketData:
    """Market data from Polymarket Gamma API."""

    condition_id: str
    question: str
    tokens: List[Dict[str, Any]]
    active: bool
    closed: bool
    slug: str = ""
    description: str = ""
    market_slug: str = ""

    @property
    def yes_token_id(self) -> Optional[str]:
        """Get the YES outcome token ID."""
        for token in self.tokens:
            if token.get("outcome", "").upper() == "YES":
                return token.get("token_id", "")
        return self.tokens[0].get("token_id", "") if self.tokens else None

    @property
    def no_token_id(self) -> Optional[str]:
        """Get the NO outcome token ID."""
        for token in self.tokens:
            if token.get("outcome", "").upper() == "NO":
                return token.get("token_id", "")
        return self.tokens[1].get("token_id", "") if len(self.tokens) > 1 else None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "PolymarketMarketData":
        tokens = data.get("tokens", [])
        if isinstance(tokens, str):
            import json

            try:
                tokens = json.loads(tokens)
            except (json.JSONDecodeError, TypeError):
                tokens = []

        return cls(
            condition_id=data.get("condition_id", ""),
            question=data.get("question", ""),
            tokens=tokens,
            active=data.get("active", False),
            closed=data.get("closed", False),
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            market_slug=data.get("market_slug", ""),
        )


@dataclass
class PolymarketOrderResponse:
    """Response from order submission."""

    order_id: str
    status: str
    asset_id: str  # token_id
    side: str  # "BUY" or "SELL"
    price: float  # 0-1 probability
    original_size: float
    size_matched: float

    @property
    def price_cents(self) -> int:
        """Price in cents for interface compatibility."""
        return int(self.price * 100)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "PolymarketOrderResponse":
        return cls(
            order_id=data.get("id", data.get("order_id", "")),
            status=data.get("status", "LIVE"),
            asset_id=data.get("asset_id", data.get("token_id", "")),
            side=data.get("side", "BUY"),
            price=float(data.get("price", 0)),
            original_size=float(data.get("original_size", 0)),
            size_matched=float(data.get("size_matched", 0)),
        )
