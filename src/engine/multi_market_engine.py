"""Multi-market engine for managing multiple markets simultaneously.

Extends MarketMakingEngine to handle multiple markets with shared
risk management and centralized monitoring.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.core.config import RiskConfig as CoreRiskConfig
from src.market_making.config import MarketMakerConfig
from src.market_making.interfaces import APIClient
from src.market_making.models import MarketState

from .market_making_engine import MarketMakingEngine


logger = logging.getLogger(__name__)


@dataclass
class MultiEngineState:
    """Aggregate state across all markets."""

    total_updates: int = 0
    total_fills: int = 0
    engines_active: int = 0
    last_update_time: Optional[datetime] = None


class MultiMarketEngine:
    """Manages multiple market-making engines simultaneously.

    Provides centralized management for multiple markets with:
    - Shared API client
    - Cross-market risk management
    - Aggregate status reporting
    - Coordinated start/stop

    Example:
        >>> client = MockAPIClient()
        >>> engine = MultiMarketEngine(client)
        >>> engine.add_market("MARKET-A", MarketMakerConfig())
        >>> engine.add_market("MARKET-B", MarketMakerConfig())
        >>> engine.on_market_update("MARKET-A", market_state)
    """

    def __init__(
        self,
        api_client: APIClient,
        default_mm_config: Optional[MarketMakerConfig] = None,
        global_risk_config: Optional[CoreRiskConfig] = None,
    ) -> None:
        """Initialize multi-market engine.

        Args:
            api_client: Shared API client for all markets
            default_mm_config: Default config for markets without specific config
            global_risk_config: Global risk limits across all markets
        """
        self._api_client = api_client
        self._default_mm_config = default_mm_config or MarketMakerConfig()
        self._global_risk_config = global_risk_config or CoreRiskConfig()

        self._engines: dict[str, MarketMakingEngine] = {}
        self._market_configs: dict[str, MarketMakerConfig] = {}
        self._state = MultiEngineState()

        logger.info("MultiMarketEngine initialized")

    def add_market(
        self,
        ticker: str,
        mm_config: Optional[MarketMakerConfig] = None,
        risk_config: Optional[CoreRiskConfig] = None,
    ) -> MarketMakingEngine:
        """Add a market to manage.

        Args:
            ticker: Market identifier
            mm_config: Market-making config (uses default if not provided)
            risk_config: Risk config (uses global if not provided)

        Returns:
            The created MarketMakingEngine

        Raises:
            ValueError: If market already exists
        """
        if ticker in self._engines:
            raise ValueError(f"Market {ticker} already exists")

        config = mm_config or self._default_mm_config
        risk = risk_config or self._global_risk_config

        engine = MarketMakingEngine(
            ticker=ticker,
            api_client=self._api_client,
            mm_config=config,
            risk_config=risk,
        )

        self._engines[ticker] = engine
        self._market_configs[ticker] = config
        self._state.engines_active = len(self._engines)

        logger.info(f"Added market {ticker} to multi-engine")

        return engine

    def remove_market(self, ticker: str) -> bool:
        """Remove a market from management.

        Args:
            ticker: Market identifier

        Returns:
            True if market was removed, False if not found
        """
        if ticker not in self._engines:
            return False

        engine = self._engines[ticker]
        engine.reset()

        del self._engines[ticker]
        del self._market_configs[ticker]
        self._state.engines_active = len(self._engines)

        logger.info(f"Removed market {ticker} from multi-engine")

        return True

    def on_market_update(self, ticker: str, market: MarketState) -> None:
        """Handle a market update for a specific ticker.

        Routes the update to the appropriate engine.

        Args:
            ticker: Market identifier
            market: Current market state
        """
        if ticker not in self._engines:
            logger.warning(f"No engine for market {ticker}")
            return

        self._state.total_updates += 1
        self._state.last_update_time = datetime.now()

        # Check cross-market risk before processing
        if not self._check_cross_market_risk():
            logger.warning("Cross-market risk limit breached, halting all engines")
            self._halt_all_engines()
            return

        engine = self._engines[ticker]
        engine.on_market_update(market)

        # Update fill count
        self._state.total_fills = sum(
            e._state.fills_processed for e in self._engines.values()
        )

    def on_market_updates(self, updates: dict[str, MarketState]) -> None:
        """Handle multiple market updates at once.

        Args:
            updates: Dictionary mapping ticker to MarketState
        """
        for ticker, market in updates.items():
            self.on_market_update(ticker, market)

    def _check_cross_market_risk(self) -> bool:
        """Check aggregate risk across all markets.

        Returns:
            True if trading is allowed, False if risk limit breached
        """
        total_position = 0
        total_unrealized_pnl = 0.0
        total_realized_pnl = 0.0

        for engine in self._engines.values():
            mm_pos = engine.market_maker.position
            total_position += abs(mm_pos.contracts)
            total_unrealized_pnl += mm_pos.unrealized_pnl
            total_realized_pnl += mm_pos.realized_pnl

        # Check against global limits
        if total_position > self._global_risk_config.max_total_position:
            logger.critical(
                f"Cross-market position limit breached: "
                f"{total_position} > {self._global_risk_config.max_total_position}"
            )
            return False

        total_pnl = total_realized_pnl + total_unrealized_pnl
        if total_pnl < -self._global_risk_config.max_daily_loss:
            logger.critical(
                f"Cross-market daily loss limit breached: "
                f"${-total_pnl:.2f} > ${self._global_risk_config.max_daily_loss}"
            )
            return False

        return True

    def _halt_all_engines(self) -> None:
        """Halt all engines and cancel all quotes."""
        for ticker, engine in self._engines.items():
            engine._cancel_all_quotes()
            logger.warning(f"Halted engine for {ticker}")

    def get_aggregate_status(self) -> dict:
        """Get aggregate status across all markets.

        Returns:
            Dictionary with aggregate statistics and per-market status
        """
        total_position = 0
        total_unrealized_pnl = 0.0
        total_realized_pnl = 0.0
        total_quotes = 0

        market_statuses = {}

        for ticker, engine in self._engines.items():
            status = engine.get_status()
            market_statuses[ticker] = status

            mm_pos = engine.market_maker.position
            total_position += abs(mm_pos.contracts)
            total_unrealized_pnl += mm_pos.unrealized_pnl
            total_realized_pnl += mm_pos.realized_pnl
            total_quotes += len(engine._state.active_order_ids)

        return {
            "aggregate": {
                "markets_active": len(self._engines),
                "total_updates": self._state.total_updates,
                "total_fills": self._state.total_fills,
                "total_position": total_position,
                "total_unrealized_pnl": total_unrealized_pnl,
                "total_realized_pnl": total_realized_pnl,
                "total_pnl": total_realized_pnl + total_unrealized_pnl,
                "total_active_quotes": total_quotes,
                "position_utilization": (
                    total_position / self._global_risk_config.max_total_position
                    if self._global_risk_config.max_total_position > 0
                    else 0
                ),
                "loss_utilization": (
                    max(0, -(total_realized_pnl + total_unrealized_pnl))
                    / self._global_risk_config.max_daily_loss
                    if self._global_risk_config.max_daily_loss > 0
                    else 0
                ),
                "last_update": (
                    self._state.last_update_time.isoformat()
                    if self._state.last_update_time
                    else None
                ),
            },
            "markets": market_statuses,
        }

    def get_engine(self, ticker: str) -> Optional[MarketMakingEngine]:
        """Get engine for a specific market.

        Args:
            ticker: Market identifier

        Returns:
            MarketMakingEngine or None if not found
        """
        return self._engines.get(ticker)

    def list_markets(self) -> list[str]:
        """Get list of managed market tickers.

        Returns:
            List of ticker strings
        """
        return list(self._engines.keys())

    def reset_all(self) -> None:
        """Reset all engines and aggregate state."""
        for ticker, engine in self._engines.items():
            engine.reset()
            logger.info(f"Reset engine for {ticker}")

        self._state = MultiEngineState()
        self._state.engines_active = len(self._engines)

        logger.info("Reset all engines")

    def reset_daily(self) -> None:
        """Reset daily counters for all engines."""
        for engine in self._engines.values():
            engine.risk_manager.reset_daily()

        self._state.total_fills = 0

        logger.info("Daily reset complete for all engines")


class StatusPrinter:
    """Console status display for monitoring."""

    def __init__(self, engine: MultiMarketEngine) -> None:
        """Initialize status printer.

        Args:
            engine: MultiMarketEngine to monitor
        """
        self.engine = engine
        self._last_print_time: Optional[datetime] = None

    def print_status(self, force: bool = False) -> None:
        """Print current status to console.

        Args:
            force: Print even if recently printed
        """
        now = datetime.now()

        # Rate limit printing
        if not force and self._last_print_time:
            if (now - self._last_print_time).total_seconds() < 1.0:
                return

        self._last_print_time = now

        status = self.engine.get_aggregate_status()
        agg = status["aggregate"]

        # Header
        print("\n" + "=" * 70)
        print(f"MARKET MAKING STATUS - {now.strftime('%H:%M:%S')}")
        print("=" * 70)

        # Aggregate stats
        print(f"\nAGGREGATE:")
        print(f"  Markets: {agg['markets_active']}")
        print(f"  Position: {agg['total_position']} ({agg['position_utilization']:.1%} util)")
        print(
            f"  P&L: ${agg['total_pnl']:+.2f} "
            f"(realized: ${agg['total_realized_pnl']:+.2f}, "
            f"unrealized: ${agg['total_unrealized_pnl']:+.2f})"
        )
        print(f"  Loss Util: {agg['loss_utilization']:.1%}")
        print(f"  Active Quotes: {agg['total_active_quotes']}")
        print(f"  Updates: {agg['total_updates']}, Fills: {agg['total_fills']}")

        # Per-market status
        print(f"\nMARKETS:")
        for ticker, mkt_status in status["markets"].items():
            pos = mkt_status["market_maker"]["position"]
            print(
                f"  {ticker}: "
                f"pos={pos['contracts']:+d}, "
                f"pnl=${pos['total_pnl']:+.2f}, "
                f"quotes={mkt_status['engine']['active_quotes']}"
            )

        print("=" * 70)

    def format_compact(self) -> str:
        """Get compact one-line status.

        Returns:
            Single-line status string
        """
        status = self.engine.get_aggregate_status()
        agg = status["aggregate"]

        return (
            f"Markets:{agg['markets_active']} "
            f"Pos:{agg['total_position']} "
            f"P&L:${agg['total_pnl']:+.2f} "
            f"Quotes:{agg['total_active_quotes']} "
            f"Fills:{agg['total_fills']}"
        )
