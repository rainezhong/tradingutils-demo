# TradingUtils Development Guide

This project follows **interface-first design** principles for LLM-assisted development. All architectural decisions, conventions, and implementation patterns are documented in [ARCHITECTURE.md](./ARCHITECTURE.md).

## Core Development Principles

1. **Read ARCHITECTURE.md First** — Before adding features or modifying code, consult [ARCHITECTURE.md](./ARCHITECTURE.md) for the canonical structure and patterns.

2. **Interface-First Design** — All components implement abstract interfaces with `I_*` prefix:
   - `I_ExchangeClient` → `KalshiExchangeClient`, `PolymarketExchangeClient`, etc.
   - `I_Strategy` → `ScalpStrategy`, `NBAUnderdogStrategy`, etc.
   - `I_OrderManager` → `KalshiOrderManager`, etc.
   - `I_Scanner` → `KalshiScanner`, etc.

3. **No Dicts — Use Dataclasses** — All data structures are typed:
   ```python
   # BAD
   signal = {"side": "yes", "price": 65}

   # GOOD
   @dataclass
   class Signal:
       side: Side
       target_price_cents: int
   ```

4. **Separate Types from Implementations** — Each component has a `*_types.py` file containing only dataclasses.

## Project Structure (Canonical Locations)

```
tradingutils/
├── core/                      # Infrastructure (exchange-agnostic)
│   ├── exchange_client/       # I_ExchangeClient + implementations
│   ├── order_manager/         # I_OrderManager + implementations
│   ├── market/                # I_Market + implementations
│   ├── recorder/              # Game recording for replay
│   ├── risk/                  # Risk management
│   │   ├── risk_manager.py    # Position/loss limits, drawdown tracking
│   │   ├── position_sizer.py  # Kelly criterion sizing
│   │   ├── kelly.py           # Kelly calculations
│   │   ├── drawdown.py        # Portfolio drawdown tracking
│   │   └── correlation_limits.py  # Correlation-based exposure limits
│   └── automation/            # Automation modules
├── scanner/                   # I_Scanner implementations
├── strategies/                # I_Strategy implementations
│   └── configs/               # YAML strategy configs
├── main.py                    # MrClean CLI entry point
└── ARCHITECTURE.md            # **READ THIS FIRST**
```

## Import Conventions (Critical)

These import paths are **enforced** — do not deviate:

```python
# Strategies
from strategies.scalp_strategy import ScalpStrategy
from strategies.i_strategy import I_Strategy

# Automation
from core.automation.scheduler import Scheduler

# Trading state
from core.trading_state import get_trading_state

# Risk management
from core.risk import RiskManager, RiskConfig
from core.risk.position_sizer import PositionSizer
from core.risk.kelly import KellyCalculator

# Exchange clients
from core.exchange_client import I_ExchangeClient
from core.exchange_client.kalshi import KalshiExchangeClient

# Order managers
from core.order_manager import I_OrderManager, KalshiOrderManager
```

## Adding New Components

### Adding a New Exchange
1. Create subfolder: `core/exchange_client/polymarket/`
2. Create types: `core/exchange_client/polymarket/polymarket_types.py`
3. Implement `I_ExchangeClient` in `polymarket_client.py`
4. Export in `__init__.py`
5. Create scanner: `scanner/polymarket_scanner.py` implementing `I_Scanner`

See [ARCHITECTURE.md § Adding a New Exchange](./ARCHITECTURE.md#adding-a-new-exchange) for full details.

### Adding a New Strategy
1. Create strategy: `strategies/my_strategy.py` implementing `I_Strategy`
2. Add config dataclass to `strategies/strategy_types.py`
3. Create YAML template: `strategies/configs/my_strategy.yaml`
4. Register in `main.py` via `register_strategy()`

See [ARCHITECTURE.md § Adding a New Strategy](./ARCHITECTURE.md#adding-a-new-strategy) for full details.

## Agent Memory Updates

**Update your agent memory** as you discover:
- Codepaths and module locations
- Architectural patterns and conventions
- Library usage patterns
- Key design decisions
- Import path gotchas
- Reusable utilities and helpers
- Test patterns and fixtures
- Risk management integration points

Write concise notes about what you found and where. This builds institutional knowledge across conversations.

## Risk Management Integration

When implementing strategies or execution logic, integrate with the risk management system:

```python
from core.risk import RiskManager, RiskConfig

# Initialize risk manager
risk_config = RiskConfig(
    max_position_size=100,
    max_daily_loss=200.0,
    max_rolling_drawdown_pct=0.15,
)
risk_manager = RiskManager(config=risk_config)

# Pre-trade validation
allowed, reason = risk_manager.can_trade(ticker, "buy", size)
if not allowed:
    logger.warning(f"Trade blocked: {reason}")
    return

# Position sizing with Kelly
from core.risk.position_sizer import PositionSizer
sizer = PositionSizer(capital_manager, risk_manager, kelly_calculator)
result = sizer.calculate_size(opportunity, execution_metrics)
```

See [core/risk/README.md](./core/risk/) for the complete risk management API.

## MrClean CLI

The `main.py` CLI uses a **strategy registry pattern**:

```bash
# List registered strategies
python main.py list-strategies

# Run a strategy
python main.py run scalp --tickers TICKER1,TICKER2 --dry-run

# Scan markets
python main.py scan --series KXNBAGAME --strong-side

# Record game for replay
python main.py record --game-id 0022600123 --home-team LAL
```

## Quick Reference

| Need to... | Look at... |
|------------|------------|
| Understand the architecture | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| Add exchange support | `core/exchange_client/i_exchange_client.py` |
| Add a strategy | `strategies/i_strategy.py` + [ARCHITECTURE.md](./ARCHITECTURE.md) |
| Risk management | `core/risk/risk_manager.py` |
| Position sizing | `core/risk/position_sizer.py` |
| Kelly criterion | `core/risk/kelly.py` |
| Order management | `core/order_manager/i_order_manager.py` |
| Market scanning | `scanner/i_scanner.py` |
| CLI commands | `main.py` |
| Import conventions | This file § Import Conventions |

## Python Environment

- **Always use `python3`** (never `python`)
- Python 3.9 on macOS via pyenv
- **No Python 3.10+ syntax** — use `Optional[float]` not `float | None`

---

**For complete architectural details, implementation patterns, and examples, see [ARCHITECTURE.md](./ARCHITECTURE.md).**
