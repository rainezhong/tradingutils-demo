"""Strategy type definitions."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar

from core.order_manager import Side, Action

T = TypeVar("T", bound="StrategyConfig")

# Path to config templates
CONFIG_DIR = Path(__file__).parent / "configs"


class SignalStrength(Enum):
    """Signal strength levels."""

    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


@dataclass
class Signal:
    """Trading signal from a strategy.

    Represents a directional trading opportunity with
    target price, strength, and rationale.
    """

    side: Optional[Side]
    action: Optional[Action]
    strength: float  # 0.0 to 1.0
    target_price_cents: Optional[int]
    reason: str

    @property
    def has_signal(self) -> bool:
        """Whether this represents an actionable signal."""
        return self.side is not None and self.action is not None

    @property
    def strength_level(self) -> SignalStrength:
        """Categorical strength level."""
        if self.strength <= 0:
            return SignalStrength.NONE
        elif self.strength < 0.3:
            return SignalStrength.WEAK
        elif self.strength < 0.6:
            return SignalStrength.MODERATE
        else:
            return SignalStrength.STRONG

    @classmethod
    def no_signal(cls, reason: str = "No signal") -> "Signal":
        """Create an empty/no-action signal."""
        return cls(
            side=None,
            action=None,
            strength=0.0,
            target_price_cents=None,
            reason=reason,
        )

    @classmethod
    def buy(
        cls, side: Side, price_cents: int, strength: float, reason: str
    ) -> "Signal":
        """Create a buy signal."""
        return cls(
            side=side,
            action=Action.BUY,
            strength=strength,
            target_price_cents=price_cents,
            reason=reason,
        )


@dataclass
class StrategyConfig:
    """Base class for strategy configurations.

    All strategy configs should inherit from this and implement
    from_yaml_dict() for loading from YAML templates.
    """

    @classmethod
    def from_yaml(cls: Type[T], path: Optional[Path] = None) -> T:
        """Load config from YAML file.

        Args:
            path: Path to YAML file. If None, uses default template.

        Returns:
            Config instance populated from YAML.
        """
        try:
            import yaml
        except ImportError:
            raise ImportError("PyYAML required: pip install pyyaml")

        if path is None:
            # Use default template based on class name
            config_name = cls.__name__.replace("Config", "").lower()
            path = CONFIG_DIR / f"{_camel_to_snake(config_name)}_strategy.yaml"

        with open(path) as f:
            data = yaml.safe_load(f)

        return cls.from_yaml_dict(data)

    @classmethod
    def from_yaml_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """Create config from parsed YAML dict. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement from_yaml_dict")

    def to_yaml(self, path: Path) -> None:
        """Save config to YAML file."""
        try:
            import yaml
        except ImportError:
            raise ImportError("PyYAML required: pip install pyyaml")

        with open(path, "w") as f:
            yaml.dump(self.to_yaml_dict(), f, default_flow_style=False, sort_keys=False)

    def to_yaml_dict(self) -> Dict[str, Any]:
        """Convert config to YAML-serializable dict. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement to_yaml_dict")


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@dataclass
class ScalpConfig(StrategyConfig):
    """Configuration for scalp strategy."""

    # Position sizing
    max_position_per_market: int = 50  # Max contracts per market
    order_size: int = 10  # Contracts per order

    # Entry criteria
    min_edge_cents: int = 2  # Min edge to enter (cents)
    strong_side_threshold: float = 0.60  # Price threshold for "strong side"

    # Exit criteria
    take_profit_cents: int = 3  # Target profit per contract
    stop_loss_cents: int = 5  # Max loss per contract
    max_hold_seconds: float = 60.0  # Max time to hold position

    # Market filters
    min_volume: int = 100  # Min daily volume
    max_spread_cents: int = 5  # Max bid-ask spread

    # Timing
    tick_interval_seconds: float = 1.0  # How often to check markets

    @classmethod
    def from_yaml_dict(cls, data: Dict[str, Any]) -> "ScalpConfig":
        """Create ScalpConfig from parsed YAML."""
        pos = data.get("position", {})
        entry = data.get("entry", {})
        exit_ = data.get("exit", {})
        filters = data.get("filters", {})
        timing = data.get("timing", {})

        return cls(
            max_position_per_market=pos.get("max_per_market", 50),
            order_size=pos.get("order_size", 10),
            min_edge_cents=entry.get("min_edge_cents", 2),
            strong_side_threshold=entry.get("strong_side_threshold", 0.60),
            take_profit_cents=exit_.get("take_profit_cents", 3),
            stop_loss_cents=exit_.get("stop_loss_cents", 5),
            max_hold_seconds=exit_.get("max_hold_seconds", 60.0),
            min_volume=filters.get("min_volume", 100),
            max_spread_cents=filters.get("max_spread_cents", 5),
            tick_interval_seconds=timing.get("tick_interval_seconds", 1.0),
        )

    def to_yaml_dict(self) -> Dict[str, Any]:
        """Convert to YAML-serializable dict."""
        return {
            "strategy": "ScalpStrategy",
            "version": 1,
            "position": {
                "max_per_market": self.max_position_per_market,
                "order_size": self.order_size,
            },
            "entry": {
                "min_edge_cents": self.min_edge_cents,
                "strong_side_threshold": self.strong_side_threshold,
            },
            "exit": {
                "take_profit_cents": self.take_profit_cents,
                "stop_loss_cents": self.stop_loss_cents,
                "max_hold_seconds": self.max_hold_seconds,
            },
            "filters": {
                "min_volume": self.min_volume,
                "max_spread_cents": self.max_spread_cents,
            },
            "timing": {
                "tick_interval_seconds": self.tick_interval_seconds,
            },
        }


@dataclass
class MultiderivativeConfig(StrategyConfig):
    """Configuration for multiderivative strategy."""

    # Position sizing
    max_position_per_market: int = 50  # Max contracts per market
    order_size: int = 10  # Contracts per order

    # Tickers
    team_a: str = ""
    team_b: str = ""

    @classmethod
    def from_yaml_dict(cls, data: Dict[str, Any]) -> "MultiderivativeConfig":
        """Create MultiderivativeConfig from parsed YAML."""
        pos = data.get("position", {})
        tickers = data.get("tickers", {})

        return cls(
            max_position_per_market=pos.get("max_per_market", 50),
            order_size=pos.get("order_size", 10),
            team_a=tickers.get("team_a", ""),
            team_b=tickers.get("team_b", ""),
        )

    def to_yaml_dict(self) -> Dict[str, Any]:
        """Convert to YAML-serializable dict."""
        return {
            "strategy": "MultiderivativeStrategy",
            "version": 1,
            "position": {
                "max_per_market": self.max_position_per_market,
                "order_size": self.order_size,
            },
            "tickers": {
                "team_a": self.team_a,
                "team_b": self.team_b,
            },
        }


@dataclass
class Position:
    """Tracks an open position."""

    ticker: str
    side: Side
    quantity: int
    avg_entry_cents: int
    entry_time: datetime
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    @property
    def hold_time_seconds(self) -> float:
        """Seconds since position was opened."""
        return (datetime.now() - self.entry_time).total_seconds()

    def unrealized_pnl_cents(self, current_price_cents: int) -> int:
        """Calculate unrealized P&L in cents."""
        return (current_price_cents - self.avg_entry_cents) * self.quantity

    @property
    def total_pnl(self) -> float:
        """Total P&L (realized + unrealized)."""
        return self.realized_pnl + self.unrealized_pnl


@dataclass
class Quote:
    """A quote to place in the market."""

    ticker: str
    side: Side  # YES or NO
    action: Action  # BID (buy) or ASK (sell)
    price_cents: int
    size: int
    timestamp: datetime

    @classmethod
    def bid(cls, ticker: str, side: Side, price_cents: int, size: int) -> "Quote":
        """Create a bid quote (to buy)."""
        return cls(
            ticker=ticker,
            side=side,
            action=Action.BUY,
            price_cents=price_cents,
            size=size,
            timestamp=datetime.now(),
        )

    @classmethod
    def ask(cls, ticker: str, side: Side, price_cents: int, size: int) -> "Quote":
        """Create an ask quote (to sell)."""
        return cls(
            ticker=ticker,
            side=side,
            action=Action.SELL,
            price_cents=price_cents,
            size=size,
            timestamp=datetime.now(),
        )


@dataclass
class MarketMakingConfig(StrategyConfig):
    """Configuration for market making strategy.

    Designed for high-spread markets where we can profit from
    providing liquidity on both sides.
    """

    # Quote parameters
    target_spread_pct: float = 0.04  # 4% target spread to capture
    edge_per_side_pct: float = 0.005  # 0.5% edge added each side
    quote_size: int = 20  # Contracts per quote

    # Position limits
    max_position: int = 50  # Max contracts per market

    # Inventory management
    inventory_skew_factor: float = 0.01  # Skew quotes based on position

    # Market filters (for high spread markets)
    min_spread_cents: int = 4  # Only trade markets with >= 4c spread
    max_spread_cents: int = 20  # Avoid extremely wide/illiquid markets
    min_volume: int = 50  # Minimum daily volume
    min_price_cents: int = 20  # Avoid extreme prices
    max_price_cents: int = 80  # Avoid extreme prices

    # Risk
    max_loss_per_position: float = 20.0  # Stop loss in dollars
    max_daily_loss: float = 100.0  # Daily loss limit

    # Timing
    tick_interval_seconds: float = 2.0  # Quote update frequency

    @classmethod
    def from_yaml_dict(cls, data: Dict[str, Any]) -> "MarketMakingConfig":
        """Create config from parsed YAML."""
        quotes = data.get("quotes", {})
        position = data.get("position", {})
        inventory = data.get("inventory", {})
        filters = data.get("filters", {})
        risk = data.get("risk", {})
        timing = data.get("timing", {})

        return cls(
            target_spread_pct=quotes.get("target_spread_pct", 0.04),
            edge_per_side_pct=quotes.get("edge_per_side_pct", 0.005),
            quote_size=quotes.get("size", 20),
            max_position=position.get("max_per_market", 50),
            inventory_skew_factor=inventory.get("skew_factor", 0.01),
            min_spread_cents=filters.get("min_spread_cents", 4),
            max_spread_cents=filters.get("max_spread_cents", 20),
            min_volume=filters.get("min_volume", 50),
            min_price_cents=filters.get("min_price_cents", 20),
            max_price_cents=filters.get("max_price_cents", 80),
            max_loss_per_position=risk.get("max_loss_per_position", 20.0),
            max_daily_loss=risk.get("max_daily_loss", 100.0),
            tick_interval_seconds=timing.get("tick_interval_seconds", 2.0),
        )

    def to_yaml_dict(self) -> Dict[str, Any]:
        """Convert to YAML-serializable dict."""
        return {
            "strategy": "MarketMakingStrategy",
            "version": 1,
            "quotes": {
                "target_spread_pct": self.target_spread_pct,
                "edge_per_side_pct": self.edge_per_side_pct,
                "size": self.quote_size,
            },
            "position": {
                "max_per_market": self.max_position,
            },
            "inventory": {
                "skew_factor": self.inventory_skew_factor,
            },
            "filters": {
                "min_spread_cents": self.min_spread_cents,
                "max_spread_cents": self.max_spread_cents,
                "min_volume": self.min_volume,
                "min_price_cents": self.min_price_cents,
                "max_price_cents": self.max_price_cents,
            },
            "risk": {
                "max_loss_per_position": self.max_loss_per_position,
                "max_daily_loss": self.max_daily_loss,
            },
            "timing": {
                "tick_interval_seconds": self.tick_interval_seconds,
            },
        }
