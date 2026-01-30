# Backtesting & Simulation Guide

A comprehensive guide to the backtesting and simulation infrastructure for developing and testing trading strategies.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Key Components](#key-components)
3. [Design Decisions](#design-decisions)
4. [Quick Start](#quick-start)
5. [Historical Backtesting](#historical-backtesting)
6. [Simulated Trading](#simulated-trading)
7. [Paper Trading](#paper-trading)
8. [Data Collection](#data-collection)
9. [Fee Calculations](#fee-calculations)
10. [CLI Tools Reference](#cli-tools-reference)
11. [Extending the System](#extending-the-system)
12. [Best Practices](#best-practices)
13. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

The backtesting system has three complementary approaches:

```
                         ┌─────────────────────────────────────────────────────────┐
                         │                  BACKTESTING SYSTEM                      │
                         └─────────────────────────────────────────────────────────┘
                                                 │
          ┌──────────────────────────────────────┼──────────────────────────────────────┐
          │                                      │                                      │
  ┌───────▼───────┐                      ┌───────▼───────┐                      ┌───────▼───────┐
  │   HISTORICAL  │                      │   SIMULATED   │                      │    PAPER      │
  │  BACKTESTING  │                      │    TRADING    │                      │   TRADING     │
  └───────────────┘                      └───────────────┘                      └───────────────┘
          │                                      │                                      │
  ┌───────┼───────┐                      ┌───────┼───────┐                      ┌───────┼───────┐
  │       │       │                      │       │       │                      │       │       │
┌─▼─┐  ┌──▼──┐ ┌──▼──┐              ┌────▼────┐ ┌▼────┐ ┌▼───────┐        ┌─────▼─────┐ ┌▼─────┐
│API│  │Spread│ │Coll.│              │Simulator│ │Mock │ │Scenario│        │Paper      │ │Live  │
│Cndl│ │Bktst│ │Data │              │         │ │API  │ │        │        │Client     │ │Data  │
└───┘  └─────┘ └─────┘              └─────────┘ └─────┘ └────────┘        └───────────┘ └──────┘
  │       │       │                      │         │         │                  │           │
  └───────┴───────┘                      └─────────┴─────────┘                  └───────────┘
          │                                      │                                      │
  Uses real historical                   Uses synthetic data                   Live market data
  data from exchanges                    for strategy testing                  simulated execution
```

### Three Approaches

| Approach | Use Case | Data Source | Execution | Speed |
|----------|----------|-------------|-----------|-------|
| **Historical Backtesting** | Validate strategies on past market conditions | Kalshi API candlesticks, collected spreads | N/A (analysis only) | Fast (batch) |
| **Simulated Trading** | Develop/debug strategies, test edge cases | Synthetic price generation | Simulated fills | Real-time sim |
| **Paper Trading** | Test strategies against live markets without risk | Live exchange data | Simulated fills + P&L | Real-time live |

---

## Key Components

### Historical Backtesting (`arb/backtest.py`)

The core backtesting module for analyzing historical arbitrage opportunities.

```
arb/
├── backtest.py           # Core backtest engine with fee calculations
├── spread_collector.py   # Continuous data collection to SQLite
├── spread_detector.py    # Real-time cross-platform spread detection
├── kalshi_scanner.py     # Market pair discovery
└── live_arb.py           # Live monitoring utilities
```

### Simulation Layer (`src/simulation/`)

Synthetic market generation and paper trading for algorithm development.

```
src/simulation/
├── market_simulator.py      # Price generation engine
├── simulated_api_client.py  # Mock API that mimics real exchange
├── paper_trading.py         # Paper trading with live data + simulated execution
├── scenarios.py             # Pre-built market condition templates
├── paired_simulator.py      # Two correlated markets for arb testing
└── spread_scenarios.py      # Spread-specific test scenarios
```

### CLI Tools (`scripts/`)

```
scripts/
├── backtest_kalshi_spread.py  # Backtest any Kalshi market pair
├── backtest_collected.py      # Backtest on locally collected data
└── collect_spreads.py         # Continuous data collection daemon
```

---

## Design Decisions

### 1. Fee-Aware Calculations

All profit calculations include realistic fee structures:

```python
# Kalshi fee model: rate * contracts * P * (1-P), rounded up to cent
def kalshi_fee_total(C: int, P: float, maker: bool = False) -> float:
    rate = 0.0175 if maker else 0.07  # 1.75% maker, 7% taker
    return round_up_cent(rate * C * P * (1.0 - P))
```

**Why?** Prediction market fees are non-linear and depend on price. A 2-cent gross edge can become negative after fees at certain price points.

### 2. All-In Cost Accounting

The system always calculates "all-in" costs that include fees:

```python
all_in_buy_cost(price, contracts, maker) = price + fee_per_contract
all_in_sell_proceeds(price, contracts, maker) = price - fee_per_contract
```

**Why?** This prevents false positive opportunity detection. An apparent arbitrage disappears when you account for crossing the spread twice plus fees on both sides.

### 3. Candlestick-Based Analysis

Uses exchange candlestick data rather than trade-by-trade:

- **1-minute candles**: High resolution, catches short-lived opportunities
- **60-minute candles**: Longer patterns, reduced noise
- **1440-minute candles**: Daily trends, minimal data

**Why?** Trade-by-trade data isn't available via public API. Candlesticks provide bid/ask OHLC which is sufficient for spread analysis.

### 4. Timestamp Alignment

When analyzing market pairs, data is aligned by timestamp:

```python
ts_common = sorted(set(candles_1.keys()) & set(candles_2.keys()))
```

**Why?** Markets may have different activity levels. Only timestamps where both markets have data give valid arbitrage calculations.

### 5. Interface-Based Simulation

The simulation layer implements the same interfaces as real API clients:

```python
class SimulatedAPIClient(APIClient):
    # Same interface as real Kalshi client
    def place_order(...) -> str
    def get_market_data(...) -> MarketState
```

**Why?** Strategies developed against simulations work unchanged against real APIs. No code changes needed when going live.

### 6. Reproducible Simulations

All simulators accept optional random seeds:

```python
simulator = MarketSimulator(ticker="TEST", seed=42)  # Reproducible
```

**Why?** Debugging requires reproducing exact conditions. Seeds allow deterministic replay of any simulation run.

---

## Quick Start

### 1. Backtest a Kalshi Market Pair

```bash
# Backtest two complementary markets (e.g., team A vs team B)
python scripts/backtest_kalshi_spread.py \
    KXNBAGAME-26JAN21TORSAC-TOR \
    KXNBAGAME-26JAN21TORSAC-SAC \
    --lookback 24

# Output includes:
# - Max arbitrage profit per contract
# - Dutch book opportunities
# - Opportunity duration streaks
# - Matplotlib visualizations
```

### 2. Run a Simulation

```python
from src.simulation.scenarios import volatile_market

# Create simulated exchange
client = volatile_market(ticker="TEST-MARKET")

# Place an order
order_id = client.place_order("TEST-MARKET", "buy", 0.45, 10)

# Advance simulation and check fills
for _ in range(100):
    market = client.step()
    print(f"Mid: {market.mid:.3f}, Bid: {market.bid:.3f}, Ask: {market.ask:.3f}")

# Check order status
status = client.get_order_status(order_id)
print(f"Filled: {status['filled_size']} / {status['size']}")
```

### 3. Collect Data for Future Backtests

```bash
# Start collecting spread data every 60 seconds
python scripts/collect_spreads.py --interval 60

# Later, backtest on collected data
python scripts/backtest_collected.py --list                    # See available pairs
python scripts/backtest_collected.py --pair TICKER_A:TICKER_B  # Backtest specific pair
```

---

## Historical Backtesting

### Using `backtest_pair()` Function

The main entry point for historical backtesting:

```python
from arb.backtest import backtest_pair

# Backtest any two Kalshi markets
rows = backtest_pair(
    ticker_1="MARKET-TEAMA",
    ticker_2="MARKET-TEAMB",
    period_interval=1,      # 1-minute candles
    lookback_hours=12.0,    # Last 12 hours (or full lifetime if closed)
    contract_size=100,      # For fee calculations
    entry_maker=False,      # Assume taker fees on entry
    exit_maker=False,       # Assume taker fees on exit
    arb_floor=0.002,        # Min $0.002/contract to report
    dutch_floor=0.002,      # Min dutch profit to report
    show_plots=True,        # Display matplotlib charts
)

# Each row contains:
# - ts: Unix timestamp
# - arb_m1, arb_m2: Arbitrage PnL for each exposure direction
# - dutch: Dutch book profit (if mutually exclusive)
# - best_kind: Recommended action ("ARB_M1", "ARB_M2", "DUTCH", "NO_TRADE")
# - best_val: Best opportunity value
# - Prices: m1_yes_bid, m1_yes_ask, m2_yes_bid, m2_yes_ask
```

### Understanding Output Metrics

| Metric | Description |
|--------|-------------|
| `edge_m1` | Cost savings getting M1 exposure via M2 NO instead of M1 YES |
| `edge_m2` | Cost savings getting M2 exposure via M1 NO instead of M2 YES |
| `arb_m1` | Profit from buying cheapest M1 exposure + selling richest |
| `arb_m2` | Profit from buying cheapest M2 exposure + selling richest |
| `dutch` | Profit from buying both complementary sides and holding to settlement ($1) |

### Streak Analysis

The backtest identifies sustained opportunity windows:

```
Longest opportunity streaks:
  ARB_M1       | 8.5 min | peak $0.0234 | 2024-01-21 15:32:00 UTC
  DUTCH        | 3.2 min | peak $0.0156 | 2024-01-21 16:45:00 UTC
```

**Interpretation**: Longer streaks indicate more executable opportunities (you have time to act).

### Custom Data Sources

For non-Kalshi data or custom databases:

```python
def my_data_func(ticker, start_ts, end_ts, period_interval):
    """Return candlestick data in expected format."""
    return [{
        "end_period_ts": 1705855200,  # Unix timestamp
        "yes_bid": {"close_dollars": 0.43},
        "yes_ask": {"close_dollars": 0.45},
    }, ...]

# Use with backtest engine
from arb.backtest import backtest_nba_pair
# Modify to accept data_func parameter (see extending section)
```

---

## Simulated Trading

### MarketSimulator

Generates realistic price movements using random walk:

```python
from src.simulation.market_simulator import MarketSimulator

sim = MarketSimulator(
    ticker="MY-MARKET",
    initial_mid=0.50,           # Starting price (50%)
    volatility=0.02,            # 2% std dev per step
    spread_range=(0.03, 0.06),  # 3-6% bid-ask spread
    seed=42,                    # Reproducible
)

# Generate price sequence
states = sim.simulate_sequence(1000)
for state in states:
    print(f"Time: {state.timestamp}, Mid: {state.mid:.3f}")
```

### Simulator Variants

| Simulator | Behavior | Use Case |
|-----------|----------|----------|
| `MarketSimulator` | Pure random walk | Basic strategy testing |
| `TrendingSimulator` | Random walk + drift | Trend-following strategies |
| `MeanRevertingSimulator` | Oscillates around fair value | Mean-reversion strategies |

```python
from src.simulation.market_simulator import TrendingSimulator, MeanRevertingSimulator

# Trending market (upward)
trending = TrendingSimulator(
    ticker="TREND-UP",
    initial_mid=0.30,
    drift=0.003,  # +0.3% per step average
)

# Mean-reverting market
reverting = MeanRevertingSimulator(
    ticker="MEAN-REV",
    fair_value=0.50,
    reversion_speed=0.15,  # How fast it snaps back
)
```

### SimulatedAPIClient

Full mock exchange with order management:

```python
from src.simulation.simulated_api_client import SimulatedAPIClient
from src.simulation.market_simulator import MarketSimulator

# Create simulator and client
sim = MarketSimulator("TEST", initial_mid=0.45)
client = SimulatedAPIClient(sim, fill_probability=1.0)

# Place orders (same API as real exchange)
order_id = client.place_order(
    ticker="TEST",
    side="buy",       # or "sell", "BID", "ASK"
    price=0.43,       # Limit price
    size=10,          # Contracts
)

# Run simulation loop
for _ in range(100):
    market = client.step()  # Advances time, checks fills

# Check results
status = client.get_order_status(order_id)
fills = client.get_all_fills()
```

### Pre-Built Scenarios

Quick setup for common market conditions:

```python
from src.simulation.scenarios import (
    stable_market,
    volatile_market,
    trending_up,
    trending_down,
    mean_reverting,
    list_scenarios,
)

# List all available scenarios
print(list_scenarios())
# ['stable_market', 'volatile_market', 'trending_up', 'trending_down',
#  'mean_reverting', 'choppy_market', 'wide_spread', 'tight_spread']

# Create client with scenario
client = volatile_market(ticker="VOL-TEST")

# Or get config and customize
from src.simulation.scenarios import get_scenario, create_api_client

config = get_scenario("volatile_market")
config.volatility = 0.08  # Make it even more volatile
client = create_api_client(config, ticker="CUSTOM")
```

### Running Strategy Simulations

```python
from src.simulation.scenarios import run_simulation, stable_market

client = stable_market()

def my_strategy(client):
    """Called once per simulation step."""
    market = client.get_market_data("SIM-MARKET")

    # Simple strategy: buy if price < 0.45
    if market.mid < 0.45:
        client.place_order("SIM-MARKET", "buy", market.ask, 5)

result = run_simulation(
    client=client,
    strategy=my_strategy,
    n_steps=500,
    scenario_name="my_test",
)

print(f"Fills: {result.n_fills}")
print(f"Volume: {result.total_volume}")
print(f"Final position: {result.final_position}")
```

---

## Paper Trading

Paper trading bridges the gap between simulation and live trading. It uses **real market data** from the exchange while **simulating order execution locally**. This lets you test strategies against actual market conditions without risking capital.

### Key Differences from Simulation

| Feature | `SimulatedAPIClient` | `PaperTradingClient` |
|---------|---------------------|----------------------|
| Market Data | Synthetic (generated) | Live from exchange |
| Fill Checking | At order placement only | Continuous background polling |
| Fee Calculations | None | Full Kalshi fee model |
| P&L Tracking | None | Realized + unrealized |
| Balance Management | None | Cash balance tracking |
| State Persistence | None | JSON save/restore |
| Resting Orders | Not tracked after placement | Fills when market crosses |

### PaperTradingClient

The main class for paper trading:

```python
from src.simulation import PaperTradingClient
from src.core.api_client import KalshiClient, Config

# Create real client for market data
config = Config.from_yaml('config.yaml')
kalshi = KalshiClient(config)

# Create paper trading client
paper = PaperTradingClient(
    market_data_client=kalshi,      # Real client for live data
    initial_balance=10000.0,        # Starting cash
    persist_path=Path("paper.json"), # Optional: save/restore state
    fill_probability=0.95,          # Probability of resting fills
    poll_interval_ms=1000,          # Background check interval
)

# Start background fill checking
paper.start()

# Get live market data (passes through to real exchange)
market = paper.get_market_data("KXNBA-TEAM-YES")
print(f"Live bid/ask: {market.bid}/{market.ask}")

# Place paper orders (same interface as real trading)
order_id = paper.place_order("KXNBA-TEAM-YES", "BID", 0.45, 50)

# Check order status
status = paper.get_order_status(order_id)
print(f"Status: {status['status']}, Filled: {status['filled_size']}")

# View positions and P&L
positions = paper.get_positions()
report = paper.get_pnl_report()
print(f"Balance: ${report['current_balance']:.2f}")
print(f"Total P&L: ${report['total_pnl']:.2f}")
print(f"Total Fees: ${report['total_fees']:.2f}")

# Stop and save state
paper.stop()
paper.save_state()
```

### Fill Simulation Logic

The paper trading client simulates fills realistically:

**Immediate Fills** (marketable orders):
- BID at price >= current ask: Fills at ask price (taker)
- ASK at price <= current bid: Fills at bid price (taker)

**Resting Order Fills** (checked each poll cycle):
- BID fills when market ask drops to/below order price (maker)
- ASK fills when market bid rises to/above order price (maker)

```python
# This order will fill immediately (crossing the spread)
market = paper.get_market_data("TICKER")
paper.place_order("TICKER", "BID", market.ask, 10)  # Fills as taker

# This order will rest until market moves
paper.place_order("TICKER", "BID", market.bid - 0.05, 10)  # Rests, fills as maker later
```

### Fee Calculation

Fees follow the Kalshi model exactly:

```python
from src.simulation import calculate_fee

# Taker fee (7% of P*(1-P))
taker_fee = calculate_fee(price=0.50, size=100, maker=False)
# = ceil(0.07 * 100 * 0.50 * 0.50 * 100) / 100 = $1.75

# Maker fee (1.75% of P*(1-P))
maker_fee = calculate_fee(price=0.50, size=100, maker=True)
# = ceil(0.0175 * 100 * 0.50 * 0.50 * 100) / 100 = $0.44
```

### P&L Report

Get a comprehensive breakdown of performance:

```python
report = paper.get_pnl_report()

# Returns:
{
    "initial_balance": 10000.0,
    "current_balance": 9850.0,
    "total_realized_pnl": -100.0,    # From closed positions
    "total_unrealized_pnl": -50.0,   # From open positions (MTM)
    "total_pnl": -150.0,             # Realized + unrealized
    "total_fees": 25.0,
    "positions": {
        "TICKER-A": {
            "size": 100,
            "avg_entry_price": 0.45,
            "realized_pnl": -50.0,
            "unrealized_pnl": -25.0,
            "fees": 12.50
        }
    }
}
```

### State Persistence

Save and restore paper trading sessions:

```python
from pathlib import Path

# Configure persistence path
paper = PaperTradingClient(
    market_data_client=kalshi,
    persist_path=Path("paper_state.json"),
)

# ... trade during the day ...

# Save state at end of session
paper.save_state()

# Next day: restore state
paper2 = PaperTradingClient(
    market_data_client=kalshi,
    persist_path=Path("paper_state.json"),
)
paper2.load_state()
print(f"Restored balance: ${paper2.get_balance():.2f}")
```

State file format:
```json
{
    "version": 1,
    "saved_at": "2025-01-23T12:00:00Z",
    "initial_balance": 10000.0,
    "current_balance": 9850.0,
    "orders": {"paper_abc123": {...}},
    "positions": {"TICKER": {...}},
    "fills": [...]
}
```

### Data Classes

The paper trading system uses three data classes:

```python
from src.simulation import PaperFill, PaperOrder, PaperPosition

# PaperFill - Record of a simulated fill
fill = PaperFill(
    fill_id="fill_abc123",
    order_id="paper_xyz789",
    ticker="TICKER",
    side="BID",
    price=0.45,
    size=10,
    fee=0.08,
    timestamp=datetime.now(),
)

# PaperOrder - Tracks order state
order = PaperOrder(
    order_id="paper_xyz789",
    ticker="TICKER",
    side="BID",
    price=0.45,
    size=10,
    filled_size=5,
    status="PARTIALLY_FILLED",  # OPEN, FILLED, PARTIALLY_FILLED, CANCELED
    fills=[fill],
    created_at=datetime.now(),
    updated_at=datetime.now(),
)

# PaperPosition - Tracks position with P&L
position = PaperPosition(
    ticker="TICKER",
    size=100,              # Positive=long, negative=short
    avg_entry_price=0.45,
    realized_pnl=25.0,
    total_fees=3.50,
)
```

### Integration with Strategies

Since `PaperTradingClient` implements the `APIClient` interface, any strategy code works unchanged:

```python
def my_strategy(client):
    """Works with SimulatedAPIClient, PaperTradingClient, or real client."""
    market = client.get_market_data("TICKER")

    if market.mid < 0.40:
        client.place_order("TICKER", "BID", market.ask, 10)
    elif market.mid > 0.60:
        client.place_order("TICKER", "ASK", market.bid, 10)

# Test with simulation
sim_client = SimulatedAPIClient(MarketSimulator("TICKER"))
my_strategy(sim_client)

# Test with paper trading (live data)
paper_client = PaperTradingClient(kalshi)
paper_client.start()
my_strategy(paper_client)
paper_client.stop()

# Go live (when ready)
my_strategy(kalshi)
```

### Session Summary

Print a human-readable summary:

```python
paper.print_summary()

# Output:
# ============================================================
# PAPER TRADING SUMMARY
# ============================================================
# Initial Balance:    $   10,000.00
# Current Balance:    $    9,850.00
# ------------------------------------------------------------
# Realized P&L:       $     -100.00
# Unrealized P&L:     $      -50.00
# Total P&L:          $     -150.00
# Total Fees:         $       25.00
# ------------------------------------------------------------
#
# Positions:
#   TICKER-A: +100 contracts @ 0.4500 (unrealized: $-25.00)
#   TICKER-B: -50 contracts @ 0.6200 (unrealized: $-25.00)
#
# Open Orders: 2
#   paper_abc123... BID 50/100 @ 0.4200
#   paper_def456... ASK 25/25 @ 0.6500
#
# Total Fills: 15
# ============================================================
```

---

## Data Collection

### SpreadCollector

Continuously collects spread data for later backtesting:

```python
from arb.spread_collector import SpreadCollector

# Requires a Kalshi API client
collector = SpreadCollector(
    kalshi_client=your_client,
    db_path="data/spreads.db",
    auto_discover=True,  # Find pairs automatically
)

# Collect every 60 seconds
collector.start(interval_seconds=60)

# ... let it run ...

# Stop and see stats
collector.stop()
print(collector.get_stats())
```

### Database Schema

```sql
-- Tracked market pairs
spread_pairs (
    pair_id TEXT,        -- "TICKER_A:TICKER_B"
    ticker_a TEXT,
    ticker_b TEXT,
    event_ticker TEXT,
    event_title TEXT,
    match_type TEXT
)

-- Historical snapshots
spread_snapshots (
    pair_id TEXT,
    timestamp TEXT,

    -- Market A quotes
    a_yes_bid, a_yes_ask, a_no_bid, a_no_ask REAL,

    -- Market B quotes
    b_yes_bid, b_yes_ask, b_no_bid, b_no_ask REAL,

    -- Calculated
    combined_yes_ask REAL,   -- Cost to buy both sides
    dutch_edge REAL,         -- 1.0 - combined_yes_ask
    routing_edge_a REAL,     -- A_YES - B_NO cost difference
    routing_edge_b REAL      -- B_YES - A_NO cost difference
)
```

### Loading Collected Data

```python
from arb.spread_collector import (
    load_spread_history,
    list_collected_pairs,
    get_collection_stats,
)

# See what's available
stats = get_collection_stats("data/spreads.db")
print(f"Snapshots: {stats['num_snapshots']}")
print(f"Time range: {stats['first_snapshot']} to {stats['last_snapshot']}")

pairs = list_collected_pairs("data/spreads.db")
for p in pairs:
    print(f"  {p['pair_id']}: {p['event_title']}")

# Load specific pair history
history = load_spread_history(
    db_path="data/spreads.db",
    pair_id="TICKER_A:TICKER_B",
    start_time="2024-01-20T00:00:00Z",
    end_time="2024-01-21T00:00:00Z",
    limit=10000,
)

for row in history:
    print(f"{row['timestamp']}: edge={row['dutch_edge']:.4f}")
```

---

## Fee Calculations

### Kalshi Fee Model

```python
from arb.backtest import (
    kalshi_fee_total,
    fee_per_contract,
    all_in_buy_cost,
    all_in_sell_proceeds,
)

# Total fee for 100 contracts at $0.45
fee = kalshi_fee_total(C=100, P=0.45, maker=False)
# Taker: 0.07 * 100 * 0.45 * 0.55 = $1.73 (rounded up)

# Per-contract fee
per_contract = fee_per_contract(C=100, P=0.45, maker=False)
# $0.0173/contract

# All-in costs (what you actually pay/receive)
buy_cost = all_in_buy_cost(P_ask=0.47, C=100, maker=False)
# 0.47 + fee = ~$0.487/contract

sell_proceeds = all_in_sell_proceeds(P_bid=0.43, C=100, maker=False)
# 0.43 - fee = ~$0.413/contract
```

### Multi-Platform Fees

For cross-platform arbitrage:

```python
from arb.spread_detector import (
    Platform,
    calculate_fee,
    all_in_buy_cost,
)

# Kalshi fee
kalshi_fee = calculate_fee(Platform.KALSHI, price=0.50, contracts=100, maker=False)

# Polymarket fee (different structure)
poly_fee = calculate_fee(Platform.POLYMARKET, price=0.50, contracts=100, maker=False)

# Compare all-in costs across platforms
kalshi_cost = all_in_buy_cost(Platform.KALSHI, 0.50, 100)
poly_cost = all_in_buy_cost(Platform.POLYMARKET, 0.50, 100)
```

### Fee Impact Analysis

Fees are highest at mid-prices (P=0.50) and lowest at extremes:

| Price | Taker Fee (7%) | Per Contract |
|-------|----------------|--------------|
| 0.10  | 0.07 * 0.10 * 0.90 = 0.63% | $0.0063 |
| 0.30  | 0.07 * 0.30 * 0.70 = 1.47% | $0.0147 |
| 0.50  | 0.07 * 0.50 * 0.50 = 1.75% | $0.0175 |
| 0.70  | 0.07 * 0.70 * 0.30 = 1.47% | $0.0147 |
| 0.90  | 0.07 * 0.90 * 0.10 = 0.63% | $0.0063 |

**Implication**: Arbitrage is easier at price extremes where fees are lower.

---

## CLI Tools Reference

### `backtest_kalshi_spread.py`

Backtest any pair of Kalshi markets:

```bash
python scripts/backtest_kalshi_spread.py TICKER1 TICKER2 [OPTIONS]

Options:
  --lookback HOURS    Hours of history (default: 12, or full lifetime if closed)
  --interval MINUTES  Candle interval: 1, 60, or 1440 (default: 1)
  --contracts N       Contract size for fee calc (default: 100)
  --arb-floor CENTS   Min arb profit to report (default: 0.002)
  --dutch-floor CENTS Min dutch profit to report (default: 0.002)
  --no-plot           Skip matplotlib visualizations

Examples:
  # Recent NBA game with 24 hours of data
  python scripts/backtest_kalshi_spread.py \
      KXNBAGAME-26JAN21TORSAC-TOR \
      KXNBAGAME-26JAN21TORSAC-SAC \
      --lookback 24

  # Hourly candles for longer-term analysis
  python scripts/backtest_kalshi_spread.py TICKER1 TICKER2 --interval 60
```

### `backtest_collected.py`

Analyze locally collected spread data:

```bash
python scripts/backtest_collected.py [OPTIONS]

Options:
  --db PATH           Database path (default: data/spreads.db)
  --list              List available pairs
  --pair ID           Specific pair (TICKER_A:TICKER_B)
  --export FILE       Export to CSV
  --threshold FLOAT   Entry threshold for backtest (default: 0.0)
  --start TIME        Start time (ISO format)
  --end TIME          End time (ISO format)
  --no-plot           Skip plots

Examples:
  # List what's collected
  python scripts/backtest_collected.py --list

  # Backtest all pairs
  python scripts/backtest_collected.py

  # Specific pair with threshold
  python scripts/backtest_collected.py --pair KXNBA-A:KXNBA-B --threshold 0.01

  # Export for external analysis
  python scripts/backtest_collected.py --export analysis.csv
```

### `collect_spreads.py`

Continuous data collection:

```bash
python scripts/collect_spreads.py [OPTIONS]

Options:
  --interval SECONDS  Collection interval (default: 60)
  --db PATH           Database path (default: data/spreads.db)
  --once              Collect once and exit
  --auto-discover     Discover pairs from parlay markets

Examples:
  # Start continuous collection
  python scripts/collect_spreads.py --interval 30

  # One-shot collection
  python scripts/collect_spreads.py --once
```

---

## Extending the System

### Adding a New Simulator Type

Create a subclass of `MarketSimulator`:

```python
# src/simulation/my_simulator.py
from src.simulation.market_simulator import MarketSimulator

class JumpDiffusionSimulator(MarketSimulator):
    """Market with occasional large jumps."""

    def __init__(
        self,
        ticker: str,
        jump_probability: float = 0.02,
        jump_size: float = 0.10,
        **kwargs
    ):
        super().__init__(ticker, **kwargs)
        self.jump_probability = jump_probability
        self.jump_size = jump_size

    def _evolve_price(self):
        # Normal evolution
        change = self._rng.gauss(0, self.volatility)

        # Occasional jumps
        if self._rng.random() < self.jump_probability:
            direction = 1 if self._rng.random() > 0.5 else -1
            change += direction * self.jump_size

        self._last_price = self.mid_price
        self.mid_price = max(0.01, min(0.99, self.mid_price + change))
```

### Adding a New Data Source

Implement the data fetch interface:

```python
def fetch_polymarket_candles(ticker, start_ts, end_ts, period_interval):
    """Fetch candles from Polymarket API."""
    # Your implementation
    return [{
        "end_period_ts": ts,
        "yes_bid": {"close_dollars": bid},
        "yes_ask": {"close_dollars": ask},
    } for ts, bid, ask in your_data]

# Use with backtest
from arb.backtest import backtest_pair
# Modify backtest_pair to accept data_func parameter
```

### Adding a New Scenario

Add to `src/simulation/scenarios.py`:

```python
CRASH_SCENARIO = ScenarioConfig(
    name="flash_crash",
    description="Market crashes then recovers",
    initial_mid=0.70,
    volatility=0.08,
    spread_range=(0.10, 0.20),
    fair_value=0.50,
    reversion_speed=0.05,
    seed=42,
)

# Add to SCENARIOS dict
SCENARIOS["flash_crash"] = CRASH_SCENARIO
```

### Custom Fee Structure

For new platforms:

```python
from arb.spread_detector import Platform, FeeStructure, PLATFORM_FEES

# Add new platform
class Platform(Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"
    PREDICTIT = "predictit"  # New

PLATFORM_FEES[Platform.PREDICTIT] = FeeStructure(
    taker_rate=0.10,    # 10% of profit
    maker_rate=0.05,
    min_fee=0.0,
)
```

---

## Best Practices

### 1. Always Account for Fees

```python
# BAD: Ignoring fees
gross_profit = sell_price - buy_price

# GOOD: Include all costs
net_profit = all_in_sell_proceeds(sell_price, contracts) - all_in_buy_cost(buy_price, contracts)
```

### 2. Use Reproducible Seeds

```python
# For debugging
sim = MarketSimulator("TEST", seed=12345)

# For production testing, vary seeds
for seed in range(100):
    sim = MarketSimulator("TEST", seed=seed)
    results.append(run_backtest(sim))
analyze_distribution(results)
```

### 3. Test Edge Cases

```python
# Test at price extremes
for initial_mid in [0.05, 0.10, 0.50, 0.90, 0.95]:
    sim = MarketSimulator("TEST", initial_mid=initial_mid)
    run_strategy_test(sim)
```

### 4. Validate Against Real Data

```python
# After simulation testing, validate on historical
# 1. Test on simulation
sim_results = run_on_simulation(strategy)

# 2. Backtest on historical
hist_results = backtest_on_collected(strategy)

# 3. Compare metrics
compare_sharpe_ratios(sim_results, hist_results)
```

### 5. Handle Missing Data

```python
# Candlesticks may have gaps
for row in history:
    if row['dutch_edge'] is None:
        continue  # Skip incomplete data
    process(row)
```

### 6. Consider Slippage

```python
# Simulation fills at best price, reality may slip
slippage_bps = 5  # 5 basis points
adjusted_profit = profit - (slippage_bps / 10000) * notional
```

---

## Troubleshooting

### "No aligned candlesticks found"

**Cause**: Markets have no overlapping active periods.
**Solution**: Check market open/close times, ensure both were active simultaneously.

### Backtest shows opportunities but live trading doesn't

**Causes**:
1. Fees not properly accounted (check maker vs taker)
2. Execution latency (candlesticks capture closes, not real-time)
3. Liquidity (backtest assumes infinite, reality has depth limits)

### Simulator fills never happen

**Cause**: Order prices never cross the market.
**Solution**:
```python
# Check current market vs your order
market = client.get_market_data("TEST")
print(f"Bid: {market.bid}, Ask: {market.ask}")
print(f"Your buy order at: {your_price}")  # Must be >= ask to fill
```

### Database locked errors

**Cause**: Multiple processes accessing SQLite.
**Solution**: Use WAL mode or serialize access:
```python
conn = sqlite3.connect(db_path, timeout=30)
conn.execute("PRAGMA journal_mode=WAL")
```

### Memory issues with large backtests

**Solution**: Process in chunks:
```python
history = load_spread_history(
    db_path="data/spreads.db",
    pair_id="PAIR",
    limit=10000,  # Process 10k at a time
)
```

### Paper trading orders never fill

**Causes**:
1. Resting orders placed too far from market
2. Background polling not started
3. Market data not being refreshed

**Solutions**:
```python
# Ensure polling is started
paper.start()

# For immediate fills, cross the spread
market = paper.get_market_data("TICKER")
paper.place_order("TICKER", "BID", market.ask, 10)  # Fills immediately

# For resting orders, check they're within reasonable range
paper.place_order("TICKER", "BID", market.bid, 10)  # Rests, needs market to move
```

### Paper trading balance goes negative

**Cause**: Orders placed without sufficient balance check.
**Note**: The client validates balance on BID orders but not on settlement. If you're short and the market settles at $1, you may go negative.

### Paper trading state file corrupted

**Solution**: Reset and start fresh:
```python
paper.reset()  # Clears all state
# Or delete the JSON file and recreate
```

---

## Appendix: API Reference

### Core Functions

| Function | Module | Description |
|----------|--------|-------------|
| `backtest_pair()` | `arb.backtest` | Main historical backtest |
| `backtest_nba_pair()` | `arb.backtest` | Lower-level backtest with more options |
| `kalshi_fee_total()` | `arb.backtest` | Calculate Kalshi fees |
| `all_in_buy_cost()` | `arb.backtest` | Price + fees to buy |
| `all_in_sell_proceeds()` | `arb.backtest` | Price - fees to sell |
| `load_spread_history()` | `arb.spread_collector` | Load collected data |
| `create_api_client()` | `src.simulation.scenarios` | Create simulated client |
| `run_simulation()` | `src.simulation.scenarios` | Run strategy simulation |
| `calculate_fee()` | `src.simulation.paper_trading` | Kalshi fee calculation |

### Key Classes

| Class | Module | Description |
|-------|--------|-------------|
| `MarketSimulator` | `src.simulation.market_simulator` | Base price simulator |
| `TrendingSimulator` | `src.simulation.market_simulator` | With directional drift |
| `MeanRevertingSimulator` | `src.simulation.market_simulator` | Oscillates around fair value |
| `SimulatedAPIClient` | `src.simulation.simulated_api_client` | Mock exchange |
| `PaperTradingClient` | `src.simulation.paper_trading` | Paper trading with live data |
| `PaperOrder` | `src.simulation.paper_trading` | Paper order record |
| `PaperFill` | `src.simulation.paper_trading` | Paper fill record |
| `PaperPosition` | `src.simulation.paper_trading` | Paper position with P&L |
| `SpreadCollector` | `arb.spread_collector` | Continuous data collection |
| `SpreadDetector` | `arb.spread_detector` | Real-time opportunity detection |

---

*Last updated: January 2026*
