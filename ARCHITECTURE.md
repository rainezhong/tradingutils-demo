# TradingUtils Architecture Guide

This document describes the structure and implementation philosophy for LLM-assisted development.

## Core Principles

1. **Interface-First Design** - All components are defined by abstract interfaces (`I_*` prefix)
2. **No Dicts** - Use typed dataclasses for all data structures
3. **Exchange Agnostic** - Core logic works across any exchange implementation
4. **Strategy Agnostic** - Infrastructure doesn't know about specific strategies

## Directory Structure

```
tradingutils/
├── core/                      # Infrastructure (exchange-agnostic interfaces + implementations)
│   ├── exchange_client/       # Exchange connectivity
│   │   ├── i_exchange_client.py      # I_ExchangeClient interface
│   │   ├── exchange_client_types.py  # Shared types (ExchangeStatus, etc.)
│   │   └── kalshi/                   # Kalshi-specific implementation
│   │       ├── __init__.py           # Public exports
│   │       ├── kalshi_auth.py        # RSA-PSS authentication
│   │       ├── kalshi_client.py      # KalshiExchangeClient (REST API)
│   │       ├── kalshi_types.py       # KalshiBalance, KalshiPosition, etc.
│   │       ├── kalshi_exceptions.py  # KalshiError, WebSocketError, etc.
│   │       ├── kalshi_websocket.py   # KalshiWebSocket (async)
│   │       └── kalshi_websocket_sync.py  # KalshiWebSocketSync (sync wrapper)
│   ├── order_manager/         # Order lifecycle management
│   │   ├── i_order_manager.py        # I_OrderManager interface
│   │   ├── kalshi_order_manager.py   # KalshiOrderManager implementation
│   │   └── order_manager_types.py    # OrderRequest, Fill, TrackedOrder, etc.
│   ├── market/                # Market data abstraction
│   │   ├── i_market.py               # I_Market interface
│   │   ├── kalshi_market.py          # KalshiMarket implementation
│   │   └── market_types.py           # OrderBook, etc.
│   └── recorder/              # Game recording for replay/backtest
│       ├── recorder_types.py         # GameFrame, GameSeries, GameSeriesMetadata
│       └── game_recorder.py          # GameRecorder implementation
│
├── scanner/                   # Market discovery (exchange-agnostic)
│   ├── i_scanner.py                  # I_Scanner interface
│   ├── kalshi_scanner.py             # KalshiScanner implementation
│   └── scanner_types.py              # ScanFilter, ScanResult
│
├── strategies/                # Trading strategies
│   ├── i_strategy.py                 # I_Strategy interface
│   ├── strategy_types.py             # Signal, Position, StrategyConfig, etc.
│   ├── scalp_strategy.py             # ScalpStrategy implementation
│   └── configs/                      # YAML config templates
│       └── scalp_strategy.yaml
│
└── main.py                    # CLI entry point
```

## Interface Conventions

### Naming
- Interfaces: `I_ComponentName` (e.g., `I_ExchangeClient`, `I_Scanner`, `I_Strategy`)
- Implementations: `ExchangeNameComponentName` (e.g., `KalshiExchangeClient`, `KalshiScanner`)
- Types: `component_types.py` files contain dataclasses only

### Abstract Methods
All interfaces use `ABC` with `@abstractmethod`. Implementations must provide:

```python
class I_Strategy(ABC):
    @abstractmethod
    async def load_markets(self) -> None: ...
    
    @abstractmethod
    async def refresh_markets(self) -> None: ...
    
    @abstractmethod
    def get_signal(self, market: Any) -> Signal: ...
    
    @abstractmethod
    async def run(self) -> None: ...
```

## Type Philosophy

### No Dicts - Use Dataclasses
```python
# BAD
def get_signal(self) -> dict:
    return {"side": "yes", "price": 65}

# GOOD
@dataclass
class Signal:
    side: Side
    target_price_cents: int
    
def get_signal(self) -> Signal:
    return Signal(side=Side.YES, target_price_cents=65)
```

### Factory Methods Over Constructors
```python
@dataclass
class Signal:
    side: Optional[Side]
    target_price_cents: int
    has_signal: bool
    
    @classmethod
    def buy(cls, side: Side, price_cents: int, reason: str = "") -> "Signal":
        return cls(side=side, target_price_cents=price_cents, has_signal=True, ...)
    
    @classmethod
    def no_signal(cls, reason: str = "") -> "Signal":
        return cls(side=None, target_price_cents=0, has_signal=False, ...)
```

### Separate Types from Implementations
```
core/exchange_client/
├── exchange_client_types.py  # ExchangeStatus (shared across exchanges)
└── kalshi/                   # Exchange-specific subfolder
    ├── kalshi_types.py       # KalshiMarketData, KalshiBalance, etc.
    └── kalshi_client.py      # Uses the types, doesn't define them
```

## Adding a New Exchange

1. Create exchange subfolder: `core/exchange_client/polymarket/`
2. Create types file: `core/exchange_client/polymarket/polymarket_types.py`
3. Create auth: `core/exchange_client/polymarket/polymarket_auth.py`
4. Create client: `core/exchange_client/polymarket/polymarket_client.py` implementing `I_ExchangeClient`
5. Create WebSocket (optional): `core/exchange_client/polymarket/polymarket_websocket.py`
6. Create `__init__.py` with public exports
7. Update parent `core/exchange_client/__init__.py` to re-export
8. Create scanner: `scanner/polymarket_scanner.py` implementing `I_Scanner`

## Adding a New Strategy

1. Create strategy file: `strategies/momentum_strategy.py` implementing `I_Strategy`
2. Create config dataclass in `strategies/strategy_types.py`:
   ```python
   @dataclass
   class MomentumConfig(StrategyConfig):
       lookback_seconds: int = 60
       momentum_threshold: float = 0.05
   ```
3. Create YAML template: `strategies/configs/momentum_strategy.yaml`
4. Register in `main.py`:
   ```python
   register_strategy("momentum", MomentumStrategy, MomentumConfig, "Momentum-based trading")
   ```

## Strategy-Scanner Integration

Strategies define their own market filters:

```python
class MyStrategy(I_Strategy):
    def market_filter(self, market: Any) -> bool:
        """Return True if market is suitable for this strategy."""
        return market.volume >= 100 and market.yes_bid >= 60
```

Scanners use strategy filters:

```python
scanner = KalshiScanner()
results = await scanner.scan_for_strategy(my_strategy, series_ticker="KXNBAGAME")
```

## Recording and Replay

Record live games for backtesting:

```python
from core.recorder import GameRecorder, GameSeries, GameFrame

# Record
recorder = GameRecorder(game_id="...", home_team="LAL", ...)
series = await recorder.start_async(exchange_client)
series.save("data/recordings/game.json")

# Load and iterate
series = GameSeries.load("data/recordings/game.json")
for frame in series:
    print(f"Q{frame.period}: {frame.home_score}-{frame.away_score}, home_mid={frame.home_mid:.0%}")
```

## CLI Commands

```bash
# Run strategy
python main.py run -s scalp -t TICKER1,TICKER2 --dry-run

# Scan markets
python main.py scan --series KXNBAGAME --strong-side --threshold 0.60

# Record game
python main.py record --game-id 0022600123 --home-team LAL --away-team BOS \
    --home-ticker KXNBA... --away-ticker KXNBA...

# List strategies
python main.py list-strategies
```

## Key Files Quick Reference

| Need to... | Look at... |
|------------|------------|
| Add exchange support | `core/exchange_client/i_exchange_client.py` |
| Add Kalshi features | `core/exchange_client/kalshi/` |
| Add WebSocket data | `core/exchange_client/kalshi/kalshi_websocket.py` |
| Add order types | `core/order_manager/order_manager_types.py` |
| Create strategy | `strategies/i_strategy.py`, `strategies/scalp_strategy.py` |
| Add scan criteria | `scanner/scanner_types.py` |
| Record games | `core/recorder/game_recorder.py` |
| CLI commands | `main.py` |
