"""NBA Early Game Mispricing Trading Strategy.

DEMO VERSION - Strategy logic removed.
This file shows the class structure but contains no proprietary trading logic.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.core.interfaces import APIClient
from src.core.models import Fill, MarketState
from src.strategies.base import Signal, StrategyConfig, TradingStrategy

logger = logging.getLogger(__name__)


@dataclass
class NBAMispricingConfig:
    """Configuration for NBA mispricing strategy."""

    position_size: int = 10
    max_period: int = 2
    order_timeout_ms: int = 5000
    poll_interval_ms: int = 500
    cooldown_seconds: float = 3.0
    min_edge_cents: float = 3.0
    position_scale_factor: float = 1.0
    max_position_per_game: int = 100
    use_kelly_sizing: bool = False
    kelly_fraction: float = 0.25
    score_staleness_threshold: int = 15
    extend_past_first_half: bool = True
    enable_smart_exits: bool = True
    smart_exit_profit_threshold: float = 0.10

    @classmethod
    def conservative(cls) -> "NBAMispricingConfig":
        """Conservative preset."""
        return cls(min_edge_cents=5.0, position_scale_factor=0.5)

    @classmethod
    def moderate(cls) -> "NBAMispricingConfig":
        """Moderate preset."""
        return cls(min_edge_cents=3.0, position_scale_factor=1.0)

    @classmethod
    def aggressive(cls) -> "NBAMispricingConfig":
        """Aggressive preset."""
        return cls(min_edge_cents=1.0, position_scale_factor=2.0, use_kelly_sizing=True)


@dataclass
class DualOrderState:
    """Tracks a pair of orders placed on both sides of a mispricing.

    DEMO: Stub class with minimal fields.
    """

    order_id_a: str = ""
    order_id_b: str = ""
    ticker_a: str = ""
    ticker_b: str = ""
    placed_at: Optional[datetime] = None
    game_id: str = ""
    edge_at_placement: float = 0.0
    side_a: str = "NO"
    side_b: str = "YES"
    filled_order: Optional[str] = None


@dataclass
class PositionEntry:
    """Tracks a single position entry.

    DEMO: Stub class.
    """

    quantity: int = 0
    entry_price: float = 0.0
    side: str = ""
    entry_time: Optional[datetime] = None


@dataclass
class GameContext:
    """Tracks state for a single NBA game."""

    game_id: str
    home_team: str
    away_team: str
    home_ticker: str
    away_ticker: str
    score_feed: Optional[Any] = None
    last_trade_at: Optional[datetime] = None
    yes_positions: List[PositionEntry] = field(default_factory=list)
    no_positions: List[PositionEntry] = field(default_factory=list)


class NBAMispricingStrategy(TradingStrategy):
    """Strategy that exploits score-implied probability vs market price mispricings.

    DEMO VERSION - All trading logic has been removed.
    This class demonstrates the interface but does not contain real strategy logic.
    """

    def __init__(
        self,
        client: APIClient,
        config: StrategyConfig,
        mispricing_config: Optional[NBAMispricingConfig] = None,
        kalshi_wrapper: Optional[Any] = None,
    ) -> None:
        """Initialize the strategy."""
        super().__init__(client, config)
        self._mispricing_config = mispricing_config or NBAMispricingConfig()
        self._kalshi_wrapper = kalshi_wrapper
        self._games: Dict[str, GameContext] = {}
        logger.info("NBA Mispricing Strategy initialized (DEMO MODE)")

    def on_start(self) -> None:
        """Initialize strategy."""
        logger.info("Starting NBA Mispricing Strategy (DEMO MODE)")

    def evaluate(self, market: MarketState) -> List[Signal]:
        """Evaluate markets for signals.

        DEMO: Returns empty list - no real trading logic.
        """
        return []

    def on_fill(self, fill: Fill) -> None:
        """Handle fill events.

        DEMO: No-op implementation.
        """
        pass

    def on_stop(self) -> None:
        """Clean up resources."""
        logger.info("Stopping NBA Mispricing Strategy (DEMO MODE)")
        self._games.clear()

    def should_trade(self, signal: Signal) -> Tuple[bool, str]:
        """Check if trade should be executed.

        DEMO: Always returns False.
        """
        return False, "Demo mode - trading disabled"

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "demo_mode": True,
            "tracked_games": len(self._games),
        })
        return stats
