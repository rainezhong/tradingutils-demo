# Statistical Exit Timing Implementation

**Date:** 2026-03-01
**Status:** ✅ IMPLEMENTED (Backtest Validation Pending)

## Overview

Implemented 5 statistical exit methods that use orderbook microstructure and real-time market dynamics to exit positions near optimal timing, replacing the fixed 20-second hold time.

## Implementation Summary

### Phase 1: Infrastructure (COMPLETE)

#### 1. Extended ScalpPosition Dataclass
**File:** `strategies/crypto_scalp/orchestrator.py:107-126`

Added entry metrics tracking:
```python
entry_exit_depth: int = 0              # Depth at best exit price
entry_spread_cents: int = 0            # Spread at entry
entry_cex_imbalance: float = 0.0       # CEX orderbook imbalance
entry_cross_exchange_std: float = 0.0  # Cross-exchange price std dev
```

#### 2. Extended L2BookState
**File:** `core/indicators/cex_feeds.py:27-38`

Added depth and imbalance fields:
```python
bid_depth: float = 0.0
ask_depth: float = 0.0
imbalance: float = 0.0  # (bid_depth - ask_depth) / total
```

Updated all 5 exchange feeds (Kraken, Coinbase, Bitstamp, Gemini, Crypto.com) to calculate and populate these fields.

#### 3. Extended BRTITracker
**File:** `core/indicators/brti_tracker.py:153-210`

Added statistical methods:
- `get_volatility(window_sec)` - Price std dev over trailing window
- `get_acceleration()` - 2nd derivative of price ($/s²)
- `get_imbalance()` - Cross-exchange average imbalance
- `get_imbalance_velocity(window_sec)` - Rate of change in imbalance
- `get_cross_exchange_std()` - Std dev of exchange prices

Added imbalance history tracking alongside price history.

#### 4. Entry Metrics Capture
**File:** `strategies/crypto_scalp/orchestrator.py:1041-1075`

Created `_capture_entry_metrics()` helper method to capture:
- Orderbook depth at exit side
- Spread (bid-ask)
- CEX imbalance
- Cross-exchange price dispersion

Integrated into both limit order and market order fallback paths.

### Phase 2: Exit Methods (COMPLETE)

**File:** `strategies/crypto_scalp/orchestrator.py:1639-1754`

Implemented 5 exit methods with proper priority ordering:

#### 1. Depth-Momentum Exit (Highest Priority)
**Trigger:** After 5s, if price up ≥3¢ but exit-side depth ≤40% of entry depth

**Logic:**
```python
if price_move >= 3¢ AND depth_ratio <= 0.4:
    force_exit()  # Lock in profits before depth vacuum reversal
```

**Expected Impact:** +6-10¢ per winning trade

#### 2. Spread Reversion Exit
**Trigger:** After 3s, if spread ≥2.0× baseline AND depth ≤60% of entry

**Logic:**
```python
if spread_expansion >= 2.0x AND depth_ratio <= 0.6:
    force_exit()  # Liquidity draining, imminent reversion
```

**Expected Impact:** +3-6¢ per trade

#### 3. CEX Imbalance Reversal Exit
**Trigger:** After 2s, if imbalance sign flip AND magnitude ≥0.5 AND velocity ≥0.3

**Logic:**
```python
if sign_flip AND magnitude >= 0.5 AND rapid:
    force_exit()  # Predictive exit before Kalshi reverses
```

**Expected Impact:** +5-10¢ per trade

#### 4. Cross-Exchange Divergence Exit
**Trigger:** After 4s, if cross-exchange std dev ≥$30

**Logic:**
```python
if cross_std >= $30:
    force_exit()  # Price discovery chaos, filter fake signals
```

**Expected Impact:** +4-7¢ per trade

#### 5. Volatility-Adjusted Hold Times
**Trigger:** Continuous adaptation based on volatility and acceleration

**Logic:**
```python
if vol >= $50:
    hold_time = 10s  # High vol: faster edge decay
elif vol <= $15:
    hold_time = 28s  # Low vol: slower edge decay
else:
    hold_time = 20s  # Normal regime

if accel > 100:
    hold_time *= 0.8  # Further reduce on high acceleration
```

**Expected Impact:** +2-4¢ per trade via regime-matching

### Phase 3: Configuration (COMPLETE)

**File:** `strategies/crypto_scalp/config.py:128-160`

Added config parameters for all exit methods:

```python
# Depth-momentum exit
enable_depth_momentum_exit: bool = True
depth_drain_threshold: float = 0.4
depth_min_profit_cents: int = 3
depth_min_hold_sec: float = 5.0

# Spread reversion exit
enable_spread_reversion_exit: bool = True
spread_reversion_multiplier: float = 2.0
spread_depth_threshold: float = 0.6

# Volatility-adjusted hold
enable_volatility_adjusted_hold: bool = True
high_vol_threshold: float = 50.0
low_vol_threshold: float = 15.0
accel_threshold: float = 100.0

# CEX imbalance reversal
enable_imbalance_reversal_exit: bool = True
imbalance_reversal_threshold: float = 0.5
imbalance_velocity_threshold: float = 0.3

# Cross-exchange divergence
enable_divergence_exit: bool = True
divergence_std_threshold: float = 30.0
```

All methods enabled by default with conservative thresholds.

## Exit Priority Order

The implementation enforces this priority (first match wins):

1. **Stop-loss** (existing) - Catastrophic loss prevention
2. **Reversal exit** (existing) - Signal flip detection
3. **Depth-momentum** (NEW) - Lock in profits before reversal
4. **Spread reversion** (NEW) - Liquidity drainage detection
5. **Imbalance reversal** (NEW) - Predictive CEX signal
6. **Divergence** (NEW) - Price discovery chaos filter
7. **Volatility-adjusted** (NEW) - Regime-based timing
8. **Normal exit** (existing) - Time-based fallback

## Next Steps

### Task #11: Backtest Adapter Update (PENDING)

Need to extend `src/backtesting/adapters/scalp_adapter.py` to:

1. **Load additional data:**
   - Orderbook depths from `kalshi_orderbook` table
   - CEX L2 data from `binance_l2`/`coinbase_l2` tables
   - Calculate volatility from price history

2. **Simulate exit methods:**
   - Check each exit condition at every backtest frame
   - Track which method triggered
   - Compare exit timing vs baseline 20s hold

3. **Enhanced metrics:**
   - Exit type distribution (% of exits by method)
   - Per-method P&L contribution
   - Average hold time by method
   - Win rate by method

4. **Validation queries:**
   ```sql
   -- Depth evolution during hold
   SELECT ts, best_bid, bid_depth, best_ask, ask_depth
   FROM kalshi_orderbook
   WHERE ticker = ? AND ts BETWEEN entry_ts AND entry_ts + 40
   ORDER BY ts;

   -- CEX imbalance during hold
   SELECT ts, imbalance, bid_depth, ask_depth
   FROM binance_l2
   WHERE ts BETWEEN entry_ts - 30 AND entry_ts + 40
   ORDER BY ts;
   ```

## Expected Performance

### Conservative Case (+10-15% P&L)
- Depth-momentum: +4¢/trade
- Spread reversion: +2¢/trade
- Volatility-adjusted: +1¢/trade
- Imbalance reversal: +2¢/trade
- Divergence: +1¢/trade
- **Total:** ~+10¢/trade average

### Optimistic Case (+20-30% P&L)
- Depth-momentum: +8¢/trade
- Spread reversion: +4¢/trade
- Volatility-adjusted: +2¢/trade
- Imbalance reversal: +6¢/trade
- Divergence: +3¢/trade
- **Total:** ~+23¢/trade average

### Baseline Comparison
- Current: +22.62¢/trade, 100 trades = $2,195 total
- Conservative (+15%): $2,524 total (+$329)
- Optimistic (+30%): $2,854 total (+$659)

## Risk Mitigation

1. **Over-optimization:** Separate train/test splits (Feb 22-24 train, Feb 27-28 test)
2. **Execution complexity:** Clear priority ordering, each method has `continue` guard
3. **Data staleness:** Existing stale data checks extended to new metrics
4. **Interaction effects:** Priority order prevents double-exits
5. **Config explosion:** Conservative defaults from research, minimize parameters

## Files Modified

1. `strategies/crypto_scalp/orchestrator.py` - Position dataclass, entry capture, exit methods
2. `strategies/crypto_scalp/config.py` - Config parameters (15 new fields)
3. `core/indicators/brti_tracker.py` - Statistical methods (5 new methods)
4. `core/indicators/cex_feeds.py` - L2BookState extension, all feed implementations

## Testing Status

- ✅ Syntax validation (all files compile)
- ⏳ Unit tests (not yet written)
- ⏳ Backtest validation (adapter update pending)
- ⏳ Live paper trading (after backtest validation)

## Documentation

- This file: Implementation summary
- Plan transcript: `/Users/raine/.claude/projects/-Users-raine-tradingutils/b10fa2ed-12ea-47e8-9fd0-77f0b4e34ed4.jsonl`
- Original plan: See task list for full details

---

**Implementation Complete:** 2026-03-01
**Next:** Update backtest adapter for validation
