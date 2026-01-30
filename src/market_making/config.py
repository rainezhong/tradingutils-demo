"""Configuration classes for market-making operations.

These configs are specific to market-making strategy parameters
and risk management, building on top of the core app config.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .constants import (
    DEFAULT_MAX_DAILY_LOSS,
    DEFAULT_MAX_LOSS_PER_POSITION,
    DEFAULT_MAX_POSITION,
    DEFAULT_MAX_TOTAL_POSITION,
    DEFAULT_QUOTE_SIZE,
    DEFAULT_TARGET_SPREAD,
    MIN_VIABLE_SPREAD,
)


@dataclass
class MarketMakerConfig:
    """Configuration for market-making strategy parameters.

    Attributes:
        target_spread: Target spread to capture (e.g., 0.04 for 4%).
        edge_per_side: Edge to add on each side of mid (e.g., 0.005 for 0.5%).
        quote_size: Default number of contracts per quote.
        max_position: Maximum position size per market.
        inventory_skew_factor: How much to skew quotes based on inventory.
        min_spread_to_quote: Minimum market spread to participate.

    Example:
        >>> config = MarketMakerConfig(
        ...     target_spread=0.04,
        ...     edge_per_side=0.005,
        ...     quote_size=20
        ... )
        >>> config.target_spread
        0.04
    """

    target_spread: float = DEFAULT_TARGET_SPREAD
    edge_per_side: float = 0.005
    quote_size: int = DEFAULT_QUOTE_SIZE
    max_position: int = DEFAULT_MAX_POSITION
    inventory_skew_factor: float = 0.01
    min_spread_to_quote: float = MIN_VIABLE_SPREAD

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self._validate()

    def _validate(self) -> None:
        """Validate all configuration values."""
        if self.target_spread <= 0:
            raise ValueError(f"target_spread must be positive, got {self.target_spread}")

        if self.edge_per_side < 0:
            raise ValueError(f"edge_per_side must be non-negative, got {self.edge_per_side}")

        if self.quote_size <= 0:
            raise ValueError(f"quote_size must be positive, got {self.quote_size}")

        if self.max_position <= 0:
            raise ValueError(f"max_position must be positive, got {self.max_position}")

        if self.inventory_skew_factor < 0:
            raise ValueError(
                f"inventory_skew_factor must be non-negative, got {self.inventory_skew_factor}"
            )

        if self.min_spread_to_quote < 0:
            raise ValueError(
                f"min_spread_to_quote must be non-negative, got {self.min_spread_to_quote}"
            )

    @classmethod
    def from_dict(cls, data: dict) -> "MarketMakerConfig":
        """Create config from dictionary.

        Args:
            data: Dictionary with config values.

        Returns:
            MarketMakerConfig instance.
        """
        return cls(
            target_spread=data.get("target_spread", DEFAULT_TARGET_SPREAD),
            edge_per_side=data.get("edge_per_side", 0.005),
            quote_size=data.get("quote_size", DEFAULT_QUOTE_SIZE),
            max_position=data.get("max_position", DEFAULT_MAX_POSITION),
            inventory_skew_factor=data.get("inventory_skew_factor", 0.01),
            min_spread_to_quote=data.get("min_spread_to_quote", MIN_VIABLE_SPREAD),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "target_spread": self.target_spread,
            "edge_per_side": self.edge_per_side,
            "quote_size": self.quote_size,
            "max_position": self.max_position,
            "inventory_skew_factor": self.inventory_skew_factor,
            "min_spread_to_quote": self.min_spread_to_quote,
        }


@dataclass
class RiskConfig:
    """Configuration for risk management.

    Attributes:
        max_position_per_market: Maximum contracts per market.
        max_total_position: Maximum total contracts across all markets.
        max_loss_per_position: Maximum loss before closing position (dollars).
        max_daily_loss: Maximum daily loss before halting (dollars).

    Example:
        >>> config = RiskConfig(
        ...     max_position_per_market=50,
        ...     max_daily_loss=100.0
        ... )
    """

    max_position_per_market: int = DEFAULT_MAX_POSITION
    max_total_position: int = DEFAULT_MAX_TOTAL_POSITION
    max_loss_per_position: float = DEFAULT_MAX_LOSS_PER_POSITION
    max_daily_loss: float = DEFAULT_MAX_DAILY_LOSS

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self._validate()

    def _validate(self) -> None:
        """Validate all configuration values."""
        if self.max_position_per_market <= 0:
            raise ValueError(
                f"max_position_per_market must be positive, got {self.max_position_per_market}"
            )

        if self.max_total_position <= 0:
            raise ValueError(
                f"max_total_position must be positive, got {self.max_total_position}"
            )

        if self.max_total_position < self.max_position_per_market:
            raise ValueError(
                f"max_total_position ({self.max_total_position}) must be >= "
                f"max_position_per_market ({self.max_position_per_market})"
            )

        if self.max_loss_per_position <= 0:
            raise ValueError(
                f"max_loss_per_position must be positive, got {self.max_loss_per_position}"
            )

        if self.max_daily_loss <= 0:
            raise ValueError(f"max_daily_loss must be positive, got {self.max_daily_loss}")

    @classmethod
    def from_dict(cls, data: dict) -> "RiskConfig":
        """Create config from dictionary.

        Args:
            data: Dictionary with config values.

        Returns:
            RiskConfig instance.
        """
        return cls(
            max_position_per_market=data.get("max_position_per_market", DEFAULT_MAX_POSITION),
            max_total_position=data.get("max_total_position", DEFAULT_MAX_TOTAL_POSITION),
            max_loss_per_position=data.get("max_loss_per_position", DEFAULT_MAX_LOSS_PER_POSITION),
            max_daily_loss=data.get("max_daily_loss", DEFAULT_MAX_DAILY_LOSS),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "max_position_per_market": self.max_position_per_market,
            "max_total_position": self.max_total_position,
            "max_loss_per_position": self.max_loss_per_position,
            "max_daily_loss": self.max_daily_loss,
        }


@dataclass
class TradingConfig:
    """Combined configuration for market-making trading.

    Aggregates strategy and risk configuration.

    Attributes:
        strategy: Market-making strategy parameters.
        risk: Risk management parameters.

    Example:
        >>> config = TradingConfig.load("config/trading.yaml")
        >>> config.strategy.target_spread
        0.04
    """

    strategy: MarketMakerConfig = field(default_factory=MarketMakerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "TradingConfig":
        """Create config from dictionary.

        Args:
            data: Dictionary with nested 'strategy' and 'risk' sections.

        Returns:
            TradingConfig instance.
        """
        strategy_data = data.get("strategy", {})
        risk_data = data.get("risk", {})

        return cls(
            strategy=MarketMakerConfig.from_dict(strategy_data),
            risk=RiskConfig.from_dict(risk_data),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "TradingConfig":
        """Load configuration from YAML file.

        Args:
            path: Path to YAML config file.

        Returns:
            TradingConfig instance.

        Example:
            >>> config = TradingConfig.from_yaml("config/trading.yaml")
        """
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "TradingConfig":
        """Load config from file or return defaults.

        Args:
            path: Optional path to config file.

        Returns:
            TradingConfig instance.
        """
        if path:
            return cls.from_yaml(path)

        # Check for default config file
        default_path = Path("config/trading.yaml")
        if default_path.exists():
            return cls.from_yaml(str(default_path))

        return cls()

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "strategy": self.strategy.to_dict(),
            "risk": self.risk.to_dict(),
        }
