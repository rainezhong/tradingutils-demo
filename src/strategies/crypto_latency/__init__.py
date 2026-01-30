"""Crypto latency arbitrage strategy for prediction markets.

DEMO VERSION - Strategy logic removed.
This module provides stub classes with no real implementation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime


@dataclass
class CryptoLatencyConfig:
    """Configuration for crypto latency strategy.

    DEMO: Stub configuration class.
    """
    min_edge_threshold: float = 0.02
    max_position_size: int = 100
    update_interval_ms: int = 100
    enabled: bool = False  # Always disabled in demo


@dataclass
class Opportunity:
    """Represents a detected latency arbitrage opportunity.

    DEMO: Stub class.
    """
    market_id: str = ""
    direction: str = ""  # "YES" or "NO"
    edge: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    confidence: float = 0.0


@dataclass
class CryptoMarket:
    """Represents a crypto prediction market.

    DEMO: Stub class.
    """
    market_id: str = ""
    ticker: str = ""
    crypto_symbol: str = ""
    target_price: float = 0.0
    expiration: Optional[datetime] = None


class LatencyDetector:
    """Detects latency arbitrage opportunities.

    DEMO VERSION - No real detection logic.
    """

    def __init__(self, config: Optional[CryptoLatencyConfig] = None):
        self.config = config or CryptoLatencyConfig()

    def detect(self, market: CryptoMarket, spot_price: float) -> Optional[Opportunity]:
        """Detect opportunities. DEMO: Always returns None."""
        return None


class CryptoMarketScanner:
    """Scans for crypto prediction markets.

    DEMO VERSION - Returns empty list.
    """

    def __init__(self, client: Any = None):
        self._client = client

    def scan(self) -> List[CryptoMarket]:
        """Scan for markets. DEMO: Returns empty list."""
        return []


class LatencyExecutor:
    """Executes latency arbitrage trades.

    DEMO VERSION - No-op implementation.
    """

    def __init__(self, client: Any = None, config: Optional[CryptoLatencyConfig] = None):
        self._client = client
        self.config = config or CryptoLatencyConfig()

    def execute(self, opportunity: Opportunity) -> bool:
        """Execute trade. DEMO: Always returns False."""
        return False


class CryptoLatencyOrchestrator:
    """Orchestrates the crypto latency strategy.

    DEMO VERSION - No real orchestration.
    """

    def __init__(
        self,
        config: Optional[CryptoLatencyConfig] = None,
        client: Any = None,
    ):
        self.config = config or CryptoLatencyConfig()
        self._client = client
        self._running = False

    def start(self) -> None:
        """Start orchestrator. DEMO: No-op."""
        print("[CryptoLatencyOrchestrator] DEMO MODE - not starting")

    def stop(self) -> None:
        """Stop orchestrator. DEMO: No-op."""
        self._running = False

    def get_stats(self) -> Dict[str, Any]:
        """Get stats. DEMO: Returns demo placeholder."""
        return {"demo_mode": True, "opportunities_found": 0}


# Kalshi-specific stubs
@dataclass
class KalshiCryptoMarket:
    """Kalshi crypto market. DEMO stub."""
    ticker: str = ""
    crypto_symbol: str = ""
    target_price: float = 0.0


@dataclass
class KalshiOpportunity:
    """Kalshi opportunity. DEMO stub."""
    ticker: str = ""
    direction: str = ""
    edge: float = 0.0


@dataclass
class KalshiExecutionResult:
    """Kalshi execution result. DEMO stub."""
    success: bool = False
    order_id: str = ""


class KalshiCryptoScanner:
    """Kalshi crypto scanner. DEMO stub."""

    def __init__(self, client: Any = None):
        pass

    def scan(self) -> List[KalshiCryptoMarket]:
        return []


class KalshiExecutor:
    """Kalshi executor. DEMO stub."""

    def __init__(self, client: Any = None):
        pass

    def execute(self, opportunity: KalshiOpportunity) -> KalshiExecutionResult:
        return KalshiExecutionResult()


class KalshiCryptoOrchestrator:
    """Kalshi orchestrator. DEMO stub."""

    def __init__(self, client: Any = None, config: Any = None):
        pass

    def start(self) -> None:
        print("[KalshiCryptoOrchestrator] DEMO MODE - not starting")

    def stop(self) -> None:
        pass


def run_kalshi_orchestrator(*args, **kwargs) -> None:
    """Run Kalshi orchestrator. DEMO: No-op."""
    print("[run_kalshi_orchestrator] DEMO MODE - not running")


__all__ = [
    # Config
    "CryptoLatencyConfig",
    # Detector (shared)
    "LatencyDetector",
    "Opportunity",
    # Polymarket components
    "CryptoMarketScanner",
    "CryptoMarket",
    "LatencyExecutor",
    "CryptoLatencyOrchestrator",
    # Kalshi components
    "KalshiCryptoScanner",
    "KalshiCryptoMarket",
    "KalshiExecutor",
    "KalshiOpportunity",
    "KalshiExecutionResult",
    "KalshiCryptoOrchestrator",
    "run_kalshi_orchestrator",
]
