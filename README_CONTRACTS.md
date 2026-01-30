# Market-Making Contracts & Interfaces

This document defines the foundation layer for market-making on Kalshi prediction markets.

## Architecture

```
src/core/              # Data collection layer (existing)
├── models.py          # Snapshot, Market
├── api_client.py      # KalshiClient
└── config.py          # App config

src/market_making/     # Trading layer (this module)
├── models.py          # MarketState, Quote, Position, Fill
├── config.py          # MarketMakerConfig, RiskConfig
├── interfaces.py      # APIClient (ABC), DataProvider (ABC)
├── adapters.py        # Bridges core → market_making
├── utils.py           # Validation & calculation functions
└── constants.py       # Price limits, size limits
```

## Data Models

All prices in the market-making layer use **0-1 probability range** (not cents).

### MarketState

Current tradeable state of a market.

```python
@dataclass
class MarketState:
    ticker: str           # Market identifier
    timestamp: datetime   # When captured
    best_bid: float       # Best bid (0-1)
    best_ask: float       # Best ask (0-1)
    mid_price: float      # Midpoint
    bid_size: int         # Depth at best bid
    ask_size: int         # Depth at best ask

    @property
    def spread_pct(self) -> float:
        """Spread as percentage of mid."""

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> MarketState:
        """Convert from core Snapshot (cents → probability)."""
```

**Validation:**
- `ticker` non-empty
- Prices in [0.01, 0.99] range
- `best_bid < best_ask`
- Sizes non-negative

### Quote

An order to be placed.

```python
@dataclass
class Quote:
    ticker: str
    side: str              # 'BID' or 'ASK'
    price: float           # 0-1 range
    size: int
    timestamp: datetime
    order_id: Optional[str]  # None until submitted

    @property
    def is_bid(self) -> bool
    @property
    def is_submitted(self) -> bool
```

### Position

Current holding in a market.

```python
@dataclass
class Position:
    ticker: str
    contracts: int          # positive=long, negative=short
    avg_entry_price: float
    unrealized_pnl: float
    realized_pnl: float

    @property
    def is_long(self) -> bool
    @property
    def is_short(self) -> bool
    @property
    def is_flat(self) -> bool
    @property
    def total_pnl(self) -> float

    def update_unrealized_pnl(self, current_price: float) -> None
```

### Fill

A completed trade execution.

```python
@dataclass
class Fill:
    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    timestamp: datetime

    @property
    def notional_value(self) -> float
```

## Configuration

### MarketMakerConfig

Strategy parameters.

```python
@dataclass
class MarketMakerConfig:
    target_spread: float = 0.04        # 4% target
    edge_per_side: float = 0.005       # 0.5% edge
    quote_size: int = 20               # Contracts per quote
    max_position: int = 50             # Max per market
    inventory_skew_factor: float = 0.01
    min_spread_to_quote: float = 0.02  # Don't quote tighter
```

### RiskConfig

Risk management limits.

```python
@dataclass
class RiskConfig:
    max_position_per_market: int = 50
    max_total_position: int = 100
    max_loss_per_position: float = 20.0   # dollars
    max_daily_loss: float = 50.0          # dollars
```

### TradingConfig

Combined configuration with YAML loading.

```python
config = TradingConfig.load("config/trading.yaml")
config.strategy.target_spread  # 0.04
config.risk.max_daily_loss     # 50.0
```

## Abstract Interfaces

### APIClient

Exchange trading operations.

```python
class APIClient(ABC):
    @abstractmethod
    def place_order(self, ticker, side, price, size) -> str:
        """Returns order_id."""

    @abstractmethod
    def cancel_order(self, order_id) -> bool:
        """Returns success."""

    @abstractmethod
    def get_order_status(self, order_id) -> dict:
        """Returns {status, filled_size, remaining_size, avg_fill_price}."""

    @abstractmethod
    def get_market_data(self, ticker) -> MarketState:
        """Returns current state."""

    @abstractmethod
    def get_positions(self) -> dict[str, int]:
        """Returns {ticker: contracts}."""

    @abstractmethod
    def get_fills(self, ticker=None, limit=100) -> list[Fill]:
        """Returns recent fills."""
```

### DataProvider

Market data access.

```python
class DataProvider(ABC):
    @abstractmethod
    def get_current_market(self, ticker) -> MarketState:
        """Get current state."""

    @abstractmethod
    def get_multiple_markets(self, tickers) -> dict[str, MarketState]:
        """Get multiple states."""

    @abstractmethod
    def subscribe_to_updates(self, ticker, callback) -> str:
        """Returns subscription_id."""

    @abstractmethod
    def unsubscribe(self, subscription_id) -> bool:
        """Cancel subscription."""

    @abstractmethod
    def get_available_markets(self) -> list[str]:
        """List tradeable tickers."""
```

## Adapters

Bridge existing infrastructure to MM interfaces.

```python
from src.core.api_client import KalshiClient
from src.market_making import KalshiDataAdapter, KalshiTradingAdapter

# Data provider (read-only, no auth needed)
client = KalshiClient()
provider = KalshiDataAdapter(client)
state = provider.get_current_market("TICKER")

# Trading client (requires auth for real trading)
trading = KalshiTradingAdapter(client, authenticated=True)
order_id = trading.place_order("TICKER", "BID", 0.45, 20)
```

## Utility Functions

### Validation

```python
validate_price(0.45)           # True
validate_price(1.5)            # False
validate_spread(0.45, 0.50)    # True (bid < ask)
validate_spread(0.50, 0.45)    # False
validate_size(20)              # True
validate_side("BID")           # True
```

### Calculations

```python
calculate_mid(0.45, 0.55)              # 0.50
calculate_spread_pct(0.45, 0.50)       # ~0.105 (10.5%)
cents_to_probability(45)               # 0.45
probability_to_cents(0.45)             # 45
```

### Quoting

```python
# Calculate quote prices around mid
bid, ask = calculate_quote_prices(
    mid_price=0.50,
    target_spread=0.04,
    inventory_skew=0.0
)
# bid=0.48, ask=0.52

# Inventory skew (long position pushes prices down)
skew = calculate_inventory_skew(
    position=25,      # 25 contracts long
    max_position=50,
    skew_factor=0.01
)
# skew=-0.005

# Should we quote?
quote_bid, quote_ask = should_quote(
    spread_pct=0.05,
    min_spread=0.03,
    position=0,
    max_position=50
)
# (True, True)
```

## Constants

```python
MIN_PRICE = 0.01          # 1 cent
MAX_PRICE = 0.99          # 99 cents
MIN_QUOTE_SIZE = 5
MAX_QUOTE_SIZE = 100
DEFAULT_QUOTE_SIZE = 20
SIDE_BID = "BID"
SIDE_ASK = "ASK"
CENTS_TO_PROB = 0.01      # Conversion factor
```

## Exceptions

```python
from src.market_making import OrderError, MarketNotFoundError, DataUnavailableError

try:
    client.place_order(...)
except OrderError as e:
    # Order placement failed
    pass

try:
    state = provider.get_current_market("INVALID")
except MarketNotFoundError:
    # Ticker doesn't exist
    pass
```

## Usage Example

```python
from src.core.api_client import KalshiClient
from src.market_making import (
    MarketState, Quote, Position,
    MarketMakerConfig, RiskConfig,
    KalshiDataAdapter,
    calculate_quote_prices, should_quote, calculate_inventory_skew,
    SIDE_BID, SIDE_ASK,
)

# Setup
client = KalshiClient()
provider = KalshiDataAdapter(client)
config = MarketMakerConfig(target_spread=0.04, quote_size=20)
risk = RiskConfig(max_position_per_market=50)

# Get market state
state = provider.get_current_market("SOME-TICKER")

# Current position (would come from exchange)
position = 25  # Long 25 contracts

# Should we quote?
quote_bid, quote_ask = should_quote(
    state.spread_pct,
    config.min_spread_to_quote,
    position,
    risk.max_position_per_market,
)

if quote_bid or quote_ask:
    # Calculate skew for inventory
    skew = calculate_inventory_skew(
        position,
        risk.max_position_per_market,
        config.inventory_skew_factor,
    )

    # Calculate prices
    bid_price, ask_price = calculate_quote_prices(
        state.mid_price,
        config.target_spread,
        skew,
    )

    # Create quotes
    if quote_bid:
        bid = Quote(state.ticker, SIDE_BID, bid_price, config.quote_size)
    if quote_ask:
        ask = Quote(state.ticker, SIDE_ASK, ask_price, config.quote_size)
```

## Testing

```bash
pytest tests/test_market_making.py -v
# 44 tests covering all models, configs, utils, and integration
```
