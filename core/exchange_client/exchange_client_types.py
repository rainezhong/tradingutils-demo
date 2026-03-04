"""Exchange client type definitions and enums."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class ExchangeStatus(Enum):
    """Exchange connection status."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class ExchangeName(Enum):
    """Supported exchanges."""

    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


@dataclass
class ExchangeConfig:
    """Configuration for an exchange client."""

    name: ExchangeName
    base_url: str
    timeout_seconds: float = 30.0
    max_retries: int = 3
    requests_per_second: float = 10.0
    demo_mode: bool = False

    @classmethod
    def kalshi_production(cls) -> "ExchangeConfig":
        """Create production Kalshi config."""
        return cls(
            name=ExchangeName.KALSHI,
            base_url="https://api.elections.kalshi.com/trade-api/v2",
        )

    @classmethod
    def kalshi_demo(cls) -> "ExchangeConfig":
        """Create demo Kalshi config."""
        return cls(
            name=ExchangeName.KALSHI,
            base_url="https://demo-api.kalshi.com/trade-api/v2",
            demo_mode=True,
        )

    @classmethod
    def polymarket_production(cls) -> "ExchangeConfig":
        """Create production Polymarket config."""
        return cls(
            name=ExchangeName.POLYMARKET,
            base_url="https://clob.polymarket.com",
        )


@dataclass
class ExchangeError:
    """Error from an exchange operation."""

    exchange: str
    operation: str
    code: Optional[str]
    message: str
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False


@dataclass
class RateLimitInfo:
    """Rate limit information from exchange."""

    requests_remaining: int
    requests_limit: int
    reset_at_timestamp: float
    window_seconds: int = 60
