"""Market-making foundation layer.

This module provides the contracts and interfaces for building
market-making strategies on top of the core data collection layer.

Submodules:
    constants: Price limits, size limits, and other constants
    models: Data structures for trading (MarketState, Quote, Position, Fill)
    config: Strategy and risk configuration classes
    interfaces: Abstract base classes for API and data providers
    adapters: Adapters connecting core infrastructure to MM interfaces
    utils: Price validation and calculation utilities

Example:
    >>> from src.market_making import (
    ...     MarketState,
    ...     Quote,
    ...     Position,
    ...     MarketMakerConfig,
    ...     RiskConfig,
    ... )
    >>>
    >>> # Create configuration
    >>> config = MarketMakerConfig(target_spread=0.04, quote_size=20)
    >>>
    >>> # Create a quote
    >>> quote = Quote(ticker="AAPL-YES", side="BID", price=0.45, size=20)
"""

# Constants
from .constants import (
    CENTS_TO_PROB,
    DEFAULT_MAX_POSITION,
    DEFAULT_QUOTE_SIZE,
    DEFAULT_TARGET_SPREAD,
    MAX_PRICE,
    MAX_QUOTE_SIZE,
    MIN_PRICE,
    MIN_QUOTE_SIZE,
    MIN_VIABLE_SPREAD,
    SIDE_ASK,
    SIDE_BID,
    VALID_SIDES,
)

# Models
from .models import (
    Fill,
    MarketState,
    Position,
    Quote,
)

# Configuration
from .config import (
    MarketMakerConfig,
    RiskConfig,
    TradingConfig,
)

# Interfaces
from .interfaces import (
    APIClient,
    DataProvider,
    DataUnavailableError,
    MarketNotFoundError,
    OrderError,
)

# Adapters
from .adapters import (
    KalshiDataAdapter,
    KalshiTradingAdapter,
)

# Utilities
from .utils import (
    calculate_inventory_skew,
    calculate_mid,
    calculate_quote_prices,
    calculate_spread_absolute,
    calculate_spread_pct,
    cents_to_probability,
    probability_to_cents,
    should_quote,
    validate_price,
    validate_side,
    validate_size,
    validate_spread,
)

__all__ = [
    # Constants
    "MIN_PRICE",
    "MAX_PRICE",
    "MIN_QUOTE_SIZE",
    "MAX_QUOTE_SIZE",
    "DEFAULT_QUOTE_SIZE",
    "DEFAULT_MAX_POSITION",
    "DEFAULT_TARGET_SPREAD",
    "MIN_VIABLE_SPREAD",
    "SIDE_BID",
    "SIDE_ASK",
    "VALID_SIDES",
    "CENTS_TO_PROB",
    # Models
    "MarketState",
    "Quote",
    "Position",
    "Fill",
    # Config
    "MarketMakerConfig",
    "RiskConfig",
    "TradingConfig",
    # Interfaces
    "APIClient",
    "DataProvider",
    "OrderError",
    "MarketNotFoundError",
    "DataUnavailableError",
    # Adapters
    "KalshiDataAdapter",
    "KalshiTradingAdapter",
    # Utils
    "validate_price",
    "validate_spread",
    "validate_size",
    "validate_side",
    "calculate_mid",
    "calculate_spread_pct",
    "calculate_spread_absolute",
    "cents_to_probability",
    "probability_to_cents",
    "calculate_quote_prices",
    "calculate_inventory_skew",
    "should_quote",
]
