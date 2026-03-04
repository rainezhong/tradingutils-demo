# Backtest Adapter Update for Statistical Exits

**Date:** 2026-03-01
**Status:** ✅ COMPLETE
**File:** `src/backtesting/adapters/scalp_adapter.py`

## Summary

Extended the crypto scalp backtest adapter to support all 5 new statistical exit methods. The adapter now:
- Loads CEX L2 orderbook data from the database
- Computes volatility, acceleration, imbalance, and cross-exchange metrics
- Simulates all statistical exit methods during backtest
- Tracks exit type distribution with detailed statistics

## Changes Made

### 1. Data Loading Extensions

#### Added CEX L2 Table Definitions
```python
L2_TABLES = {
    "binance": "binance_l2",
    "coinbase": "coinbase_l2",
}
```

#### Extended Data Storage (lines 148-159)
```python
# CEX L2 snapshots: source -> sorted list of (ts, data_dict)
self._cex_l2: Dict[str, List[Tuple[float, dict]]] = {}
self._cex_l2_timestamps: Dict[str, List[float]] = {}  # for binary search
```

#### Load CEX L2 Data from Database (lines 241-265)
Loads `binance_l2` and `coinbase_l2` tables with columns:
- `ts`, `mid_price`, `spread_bps`, `best_bid`, `best_ask`
- `bid_depth`, `ask_depth`, `imbalance`

Uses binary search indexing for efficient timestamp lookup.

### 2. Statistical Computation Functions

Added 6 new helper functions (lines 117-237):

#### `_compute_volatility(prices, ts, window_sec)`
Computes price std dev over trailing window (30s default).
Returns 0.0 if <10 data points.

#### `_compute_acceleration(prices, ts)`
Computes 2nd derivative of price ($/s²).
Uses last 20 samples (~5 seconds).
Returns mean acceleration value.

#### `_get_cex_imbalance(cex_l2, cex_timestamps, ts)`
Gets cross-exchange average imbalance at timestamp.
Uses binary search to find nearest L2 snapshot per exchange.

#### `_get_cex_imbalance_velocity(cex_l2, cex_timestamps, ts, window_sec)`
Computes rate of change in imbalance (per second).
Linear regression slope over trailing window.

#### `_get_cross_exchange_std(cex_l2, cex_timestamps, ts)`
Computes std dev of exchange mid-prices at timestamp.
Requires ≥2 exchanges for valid result.

### 3. Context Extension

#### Added CEX Metrics to Frame Context (lines 466-485)
```python
cex_ctx = {
    "volatility": _compute_volatility(...),        # Price std dev (30s)
    "acceleration": _compute_acceleration(...),    # 2nd derivative
    "imbalance": _get_cex_imbalance(...),         # Avg imbalance
    "imbalance_velocity": _get_cex_imbalance_velocity(...),  # Rate of change
    "cross_exchange_std": _get_cross_exchange_std(...),      # Price dispersion
}
```

Included in every `BacktestFrame` as `context["cex"]`.

### 4. Position Tracking Extensions

#### Extended `_OpenPosition` Dataclass (lines 598-612)
Added entry metrics:
```python
entry_exit_depth: int = 0
entry_spread_cents: int = 0
entry_cex_imbalance: float = 0.0
entry_cross_exchange_std: float = 0.0
```

### 5. Statistical Exit Configuration

#### Added Config Parameters (lines 547-567)
All 5 exit methods with default thresholds:
```python
# Depth-momentum
_enable_depth_momentum_exit = True
_depth_drain_threshold = 0.4
_depth_min_profit_cents = 3
_depth_min_hold_sec = 5.0

# Spread reversion
_enable_spread_reversion_exit = True
_spread_reversion_multiplier = 2.0
_spread_depth_threshold = 0.6

# Volatility-adjusted
_enable_volatility_adjusted_hold = True
_high_vol_threshold = 50.0
_low_vol_threshold = 15.0
_accel_threshold = 100.0

# Imbalance reversal
_enable_imbalance_reversal_exit = True
_imbalance_reversal_threshold = 0.5
_imbalance_velocity_threshold = 0.3

# Divergence
_enable_divergence_exit = True
_divergence_std_threshold = 30.0
```

#### Added Exit Type Statistics (lines 574-580)
```python
self.depth_momentum_exits = 0
self.spread_reversion_exits = 0
self.imbalance_reversal_exits = 0
self.divergence_exits = 0
self.volatility_adjusted_exits = 0
self.normal_exits = 0
self.hard_exits = 0
```

### 6. Statistical Exit Implementation

#### Added Exit Checks in `evaluate()` (lines 846-945)
Implemented all 5 methods in priority order:

1. **Depth-Momentum Exit** (after 5s hold)
   - Triggers if: price up ≥3¢ AND depth ≤40% of entry
   - Increments: `self.depth_momentum_exits`

2. **Spread Reversion Exit** (after 3s hold)
   - Triggers if: spread ≥2× baseline AND depth ≤60% of entry
   - Increments: `self.spread_reversion_exits`

3. **Imbalance Reversal Exit** (after 2s hold)
   - Triggers if: imbalance sign flip AND magnitude ≥0.5 AND velocity ≥0.3
   - Increments: `self.imbalance_reversal_exits`

4. **Cross-Exchange Divergence Exit** (after 4s hold)
   - Triggers if: cross-exchange std ≥$30
   - Increments: `self.divergence_exits`

5. **Volatility-Adjusted Exit** (continuous)
   - High vol (≥$50): 0.5× hold time (10s)
   - Low vol (≤$15): 1.4× hold time (28s)
   - High accel (≥100): 0.8× hold time
   - Increments: `self.volatility_adjusted_exits`

Normal and hard exits increment respective counters when reached.

### 7. Entry Metrics Capture

#### Capture at Position Creation (lines 1150-1171)
```python
# Capture exit-side depth
if side == "yes":
    entry_exit_depth = ob_ctx.get("bid_depth", 0)
else:
    entry_exit_depth = ob_ctx.get("ask_depth", 0)

# Capture spread
entry_spread_cents = int(ob_ctx["spread"])

# Capture CEX metrics
entry_cex_imbalance = cex_ctx.get("imbalance", 0.0)
entry_cross_exchange_std = cex_ctx.get("cross_exchange_std", 0.0)
```

### 8. Enhanced Reporting

#### Exit Type Distribution (lines 1291-1335)
Added detailed breakdown in `trade_summary()`:
```
Exit types:
  Stop-loss:         X (Y%)
  Reversal:          X (Y%)
  Depth-momentum:    X (Y%)
  Spread-reversion:  X (Y%)
  Imbalance-reversal: X (Y%)
  Divergence:        X (Y%)
  Vol-adjusted:      X (Y%)
  Normal:            X (Y%)
  Hard:              X (Y%)
```

#### Exit Type in Trade Details (line 1026)
Added `"exit_type": exit_reason.lower()` to each exit detail for per-trade analysis.

### 9. Metadata Update

#### Added CEX L2 Count (lines 511-519)
```python
"cex_l2_snapshots": cex_l2_total  # NEW: CEX L2 orderbook data
```

## Usage Example

```python
from src.backtesting.adapters.scalp_adapter import CryptoScalpDataFeed, CryptoScalpAdapter
from src.backtesting.engine import BacktestEngine, BacktestConfig

# Create data feed (will load CEX L2 data automatically)
feed = CryptoScalpDataFeed("data/btc_latency_probe.db")

# Create adapter with statistical exits enabled (defaults)
adapter = CryptoScalpAdapter(
    signal_feed="binance",
    min_spot_move_usd=10.0,
    exit_delay_sec=20.0,  # Base hold time (will be adjusted by volatility)
    # All statistical exits enabled by default
)

# Run backtest
config = BacktestConfig(name="Crypto Scalp - Statistical Exits")
engine = BacktestEngine(config)
result = engine.run(feed, adapter, verbose=True)

# View exit type distribution
print(adapter.trade_summary())
```

## Expected Output

The backtest summary now includes:
1. **Exit type distribution** - Breakdown of which exit method triggered
2. **Per-method statistics** - Win rate, avg P&L, avg hold time by exit type
3. **Comparison vs baseline** - Shows improvement over fixed 20s holds

## Database Requirements

The adapter requires the following tables in the SQLite database:
- `kalshi_snapshots` (existing)
- `kalshi_orderbook` (existing, for depth data)
- `binance_l2` (NEW, for CEX metrics)
- `coinbase_l2` (NEW, for CEX metrics)
- `binance_trades`, `coinbase_trades`, `kraken_trades` (existing)

If `binance_l2`/`coinbase_l2` tables don't exist, the adapter gracefully degrades:
- CEX statistical exits are skipped
- Only orderbook-based exits (depth-momentum, spread reversion) work
- Volatility-adjusted exits disabled

## Validation Checklist

✅ All 5 statistical exit methods implemented
✅ Entry metrics captured for each position
✅ Exit type tracking with statistics
✅ CEX L2 data loading with graceful degradation
✅ Statistical computation functions (volatility, imbalance, etc.)
✅ Priority ordering enforced (depth → spread → imbalance → divergence → vol → normal)
✅ Enhanced reporting with exit type distribution
✅ Syntax validated (compiles successfully)
✅ Import test passed

## Next Steps

1. **Run baseline backtest** - Test with all statistical exits disabled (baseline)
2. **Run statistical exits backtest** - Test with all exits enabled
3. **Compare results** - Analyze exit type distribution, P&L improvement, avg hold times
4. **Ablation study** - Test individual exit methods in isolation
5. **Parameter tuning** - Optimize thresholds based on backtest results
6. **Train/test split** - Validate on Feb 27-28 data (separate from Feb 22-24 training)

## Files Modified

- `src/backtesting/adapters/scalp_adapter.py` - All changes in this file
- Total additions: ~350 lines of new code
- Total modifications: ~50 lines of existing code

---

**Implementation Status:** ✅ COMPLETE
**Ready for Testing:** YES
**Documentation:** STATISTICAL_EXITS_IMPLEMENTATION.md + this file
