"""Unified trading system bootstrap and wiring.

Provides a builder pattern for setting up the complete trading system
with all components properly wired together.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type, TypeVar

from src.core.config import RiskConfig
from src.core.exchange import ExchangeClient
from src.core.orderbook_manager import OrderBookManager
from src.risk.risk_manager import RiskManager
from strategies.base import StrategyConfig, TradingStrategy

from .capital_manager import CapitalManager
from .fill_notifier import FillNotifier
from .order_manager import OMSConfig, OrderManagementSystem
from .spread_executor import SpreadExecutor, SpreadExecutorConfig


logger = logging.getLogger(__name__)

T = TypeVar("T", bound=TradingStrategy)


@dataclass
class TradingSystemConfig:
    """Configuration for the complete trading system.

    Attributes:
        oms_config: OMS configuration
        risk_config: Risk manager configuration
        spread_config: Spread executor configuration
        initial_capital: Starting capital per exchange
        enable_paper_trading: Whether to use paper trading mode
    """

    oms_config: Optional[OMSConfig] = None
    risk_config: Optional[RiskConfig] = None
    spread_config: Optional[SpreadExecutorConfig] = None
    initial_capital: Dict[str, float] = field(default_factory=dict)
    enable_paper_trading: bool = False


class TradingSystem:
    """Unified trading system with all components wired together.

    Provides a single entry point for:
    - Order management (OMS)
    - Capital management
    - Risk management
    - Spread execution
    - Strategy creation and management

    Example:
        >>> system = (
        ...     TradingSystemBuilder()
        ...     .with_exchange(kalshi_client)
        ...     .with_exchange(polymarket_client)
        ...     .with_risk_config(RiskConfig(max_position_size=100))
        ...     .with_initial_capital("kalshi", 10000)
        ...     .build()
        ... )
        >>>
        >>> # Create a strategy with OMS already wired
        >>> strategy = system.create_strategy(
        ...     MyStrategy,
        ...     config=StrategyConfig(name="my_strat", tickers=["TICKER-A"]),
        ...     exchange="kalshi",
        ... )
        >>>
        >>> # Use the 4-way API directly
        >>> system.oms.buy_yes("kalshi", "TICKER-A", price=0.50, size=10)
        >>>
        >>> # Start the system
        >>> system.start()
    """

    def __init__(
        self,
        config: TradingSystemConfig,
        exchanges: Dict[str, ExchangeClient],
        orderbook_manager: Optional[OrderBookManager] = None,
        fill_notifier: Optional[FillNotifier] = None,
    ) -> None:
        """Initialize the trading system.

        Use TradingSystemBuilder instead of calling this directly.
        """
        self._config = config
        self._exchanges = exchanges
        self._strategies: Dict[str, TradingStrategy] = {}
        self._is_running = False

        # Create capital manager
        self._capital_manager = CapitalManager()
        for exchange, amount in config.initial_capital.items():
            if exchange in exchanges:
                self._capital_manager.sync_from_exchange(exchanges[exchange])
            else:
                # Set initial capital directly
                self._capital_manager._balances[exchange] = amount

        # Create risk manager
        self._risk_manager = None
        if config.risk_config:
            self._risk_manager = RiskManager(config.risk_config)

        # Create OMS
        self._oms = OrderManagementSystem(
            config=config.oms_config,
            capital_manager=self._capital_manager,
            fill_notifier=fill_notifier,
            orderbook_manager=orderbook_manager,
            risk_manager=self._risk_manager,
        )

        # Register exchanges with OMS
        for client in exchanges.values():
            self._oms.register_exchange(client)

        # Create spread executor
        self._spread_executor = SpreadExecutor(
            oms=self._oms,
            capital_manager=self._capital_manager,
            config=config.spread_config,
            fill_notifier=fill_notifier,
        )

        logger.info(
            "TradingSystem initialized: exchanges=%s, risk=%s, capital=%s",
            list(exchanges.keys()),
            "enabled" if self._risk_manager else "disabled",
            config.initial_capital,
        )

    @property
    def oms(self) -> OrderManagementSystem:
        """Get the Order Management System."""
        return self._oms

    @property
    def capital_manager(self) -> CapitalManager:
        """Get the Capital Manager."""
        return self._capital_manager

    @property
    def risk_manager(self) -> Optional[RiskManager]:
        """Get the Risk Manager (if configured)."""
        return self._risk_manager

    @property
    def spread_executor(self) -> SpreadExecutor:
        """Get the Spread Executor."""
        return self._spread_executor

    @property
    def is_running(self) -> bool:
        """Check if the system is running."""
        return self._is_running

    def get_exchange(self, name: str) -> Optional[ExchangeClient]:
        """Get an exchange client by name."""
        return self._exchanges.get(name)

    def create_strategy(
        self,
        strategy_class: Type[T],
        config: StrategyConfig,
        exchange: str,
        **kwargs,
    ) -> T:
        """Create a strategy with OMS already wired.

        Args:
            strategy_class: The strategy class to instantiate
            config: Strategy configuration
            exchange: Exchange name for this strategy
            **kwargs: Additional arguments passed to strategy constructor

        Returns:
            Instantiated strategy with OMS integration
        """
        if exchange not in self._exchanges:
            raise ValueError(f"Exchange not registered: {exchange}")

        client = self._exchanges[exchange]

        strategy = strategy_class(
            client=client,
            config=config,
            risk_manager=self._risk_manager,
            oms=self._oms,
            exchange=exchange,
            **kwargs,
        )

        self._strategies[config.name] = strategy
        logger.info("Strategy created: %s (exchange=%s)", config.name, exchange)

        return strategy

    def get_strategy(self, name: str) -> Optional[TradingStrategy]:
        """Get a strategy by name."""
        return self._strategies.get(name)

    def get_all_strategies(self) -> List[TradingStrategy]:
        """Get all registered strategies."""
        return list(self._strategies.values())

    def start(self) -> "TradingSystem":
        """Start the trading system and all strategies.

        Returns:
            Self for method chaining
        """
        if self._is_running:
            return self

        # Start OMS
        self._oms.start()

        # Start all strategies
        for strategy in self._strategies.values():
            strategy.start()

        self._is_running = True
        logger.info("TradingSystem started: %d strategies", len(self._strategies))

        return self

    def stop(self) -> None:
        """Stop the trading system and all strategies."""
        if not self._is_running:
            return

        # Stop all strategies
        for strategy in self._strategies.values():
            try:
                strategy.stop()
            except Exception as e:
                logger.error("Error stopping strategy %s: %s", strategy.strategy_id, e)

        # Stop OMS
        self._oms.stop()

        self._is_running = False
        logger.info("TradingSystem stopped")

    def get_system_metrics(self) -> Dict:
        """Get comprehensive system metrics.

        Returns:
            Dictionary with metrics from all components
        """
        return {
            "oms": self._oms.get_metrics(),
            "capital": self._capital_manager.get_summary(),
            "strategies": {
                name: strategy.get_stats()
                for name, strategy in self._strategies.items()
            },
            "active_spreads": len(self._spread_executor.get_active_spreads()),
        }


class TradingSystemBuilder:
    """Builder for creating a TradingSystem with fluent configuration.

    Example:
        >>> system = (
        ...     TradingSystemBuilder()
        ...     .with_exchange(kalshi_client)
        ...     .with_exchange(polymarket_client)
        ...     .with_risk_config(RiskConfig(max_position_size=100))
        ...     .with_initial_capital("kalshi", 10000)
        ...     .with_initial_capital("polymarket", 5000)
        ...     .with_oms_config(OMSConfig(max_retries=5))
        ...     .build()
        ... )
    """

    def __init__(self) -> None:
        self._exchanges: Dict[str, ExchangeClient] = {}
        self._oms_config: Optional[OMSConfig] = None
        self._risk_config: Optional[RiskConfig] = None
        self._spread_config: Optional[SpreadExecutorConfig] = None
        self._initial_capital: Dict[str, float] = {}
        self._orderbook_manager: Optional[OrderBookManager] = None
        self._fill_notifier: Optional[FillNotifier] = None
        self._enable_paper_trading = False

    def with_exchange(self, client: ExchangeClient) -> "TradingSystemBuilder":
        """Add an exchange client.

        Args:
            client: Exchange client to add

        Returns:
            Self for method chaining
        """
        self._exchanges[client.name] = client
        return self

    def with_oms_config(self, config: OMSConfig) -> "TradingSystemBuilder":
        """Set OMS configuration.

        Args:
            config: OMS configuration

        Returns:
            Self for method chaining
        """
        self._oms_config = config
        return self

    def with_risk_config(self, config: RiskConfig) -> "TradingSystemBuilder":
        """Set risk manager configuration.

        Args:
            config: Risk configuration

        Returns:
            Self for method chaining
        """
        self._risk_config = config
        return self

    def with_spread_config(
        self, config: SpreadExecutorConfig
    ) -> "TradingSystemBuilder":
        """Set spread executor configuration.

        Args:
            config: Spread executor configuration

        Returns:
            Self for method chaining
        """
        self._spread_config = config
        return self

    def with_initial_capital(
        self, exchange: str, amount: float
    ) -> "TradingSystemBuilder":
        """Set initial capital for an exchange.

        Args:
            exchange: Exchange name
            amount: Initial capital amount

        Returns:
            Self for method chaining
        """
        self._initial_capital[exchange] = amount
        return self

    def with_orderbook_manager(
        self, manager: OrderBookManager
    ) -> "TradingSystemBuilder":
        """Set orderbook manager for constraint validation.

        Args:
            manager: Orderbook manager instance

        Returns:
            Self for method chaining
        """
        self._orderbook_manager = manager
        return self

    def with_fill_notifier(self, notifier: FillNotifier) -> "TradingSystemBuilder":
        """Set fill notifier for WebSocket fill detection.

        Args:
            notifier: Fill notifier instance

        Returns:
            Self for method chaining
        """
        self._fill_notifier = notifier
        return self

    def with_paper_trading(self, enabled: bool = True) -> "TradingSystemBuilder":
        """Enable or disable paper trading mode.

        Args:
            enabled: Whether to enable paper trading

        Returns:
            Self for method chaining
        """
        self._enable_paper_trading = enabled
        return self

    def build(self) -> TradingSystem:
        """Build the trading system.

        Returns:
            Configured TradingSystem instance

        Raises:
            ValueError: If no exchanges are registered
        """
        if not self._exchanges:
            raise ValueError("At least one exchange must be registered")

        config = TradingSystemConfig(
            oms_config=self._oms_config,
            risk_config=self._risk_config,
            spread_config=self._spread_config,
            initial_capital=self._initial_capital,
            enable_paper_trading=self._enable_paper_trading,
        )

        return TradingSystem(
            config=config,
            exchanges=self._exchanges,
            orderbook_manager=self._orderbook_manager,
            fill_notifier=self._fill_notifier,
        )


# Global trading system instance (optional singleton pattern)
_global_system: Optional[TradingSystem] = None


def get_trading_system() -> Optional[TradingSystem]:
    """Get the global trading system instance."""
    return _global_system


def set_trading_system(system: TradingSystem) -> None:
    """Set the global trading system instance."""
    global _global_system
    _global_system = system
