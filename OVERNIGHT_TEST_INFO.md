# Overnight Paper Mode Test - Started 2026-03-01 23:24

## Status: RUNNING ✅

**Process ID:** 86512
**Log File:** `logs/paper_mode_overnight_2026-03-01.log`
**Config:** `strategies/configs/crypto_scalp_live.yaml`
**Mode:** Paper (dry-run, no real money)

---

## What's Being Tested

### Entry Timing Optimizations
- ✅ Minimum delta threshold: $15 (was $10)
- ✅ Momentum filter: 0.8 threshold (recent ≥ 80% of older half)
- ✅ Regime filter: osc < 3.0 to allow trading

### Statistical Exit Methods (5 layers)
- Depth-momentum exit (price up but depth draining)
- Spread reversion exit (spread widening + depth dropping)
- Volatility-adjusted hold times (9-28s based on regime)
- CEX imbalance reversal exit (orderbook flip prediction)
- Cross-exchange divergence exit (price discovery chaos)

### Infrastructure Fixes
- ✅ Scanner event loop (run_coroutine_threadsafe)
- ✅ OrderBookManager sync wrapper
- ✅ WebSocket subscriptions via callbacks
- ✅ All imports fixed (auth, websocket, orderbook)

---

## How to Check Tomorrow

### Quick Status Check
```bash
# See if it's still running
ps aux | grep "crypto-scalp" | grep -v grep

# Get latest dashboard stats
tail -50 logs/paper_mode_overnight_2026-03-01.log | grep DASH | tail -5

# Count total signals and trades
grep "signals=" logs/paper_mode_overnight_2026-03-01.log | tail -1
```

### Detailed Analysis
```bash
# Full log review
less logs/paper_mode_overnight_2026-03-01.log

# Search for trades
grep -E "(ENTRY|Trade|Fill|EXIT)" logs/paper_mode_overnight_2026-03-01.log

# Check for errors
grep ERROR logs/paper_mode_overnight_2026-03-01.log | tail -20

# Regime analysis (when was it favorable?)
grep "regime=osc" logs/paper_mode_overnight_2026-03-01.log | awk '{print $NF}' | sort -n | head -20

# Signal generation rate
grep "signals=" logs/paper_mode_overnight_2026-03-01.log | tail -20
```

### Stop the Test
```bash
# Find the process
ps aux | grep "crypto-scalp" | grep -v grep

# Kill it (use PID 86512 or whatever shows up)
kill 86512

# Or force kill if needed
kill -9 86512
```

---

## Expected Outcomes

### If Market Conditions Are Favorable (regime osc < 3.0)

**Success Indicators:**
- ✅ Trades executed (trades > 0)
- ✅ Fill rate ≥60% (vs 25% baseline)
- ✅ Win rate ≥45% (vs 38% baseline)
- ✅ No catastrophic losses (-125¢ type losses eliminated)
- ✅ Average hold time 9-15s (vs 20s baseline)
- ✅ Statistical exits distributing across methods

**What to Look For:**
- Dashboard entries with `trades > 0`
- "ENTRY SIGNAL" log entries
- "Trade filled" confirmations
- "EXIT" log entries with P&L
- Exit method distribution (depth, spread, imbalance, etc.)

### If Market Conditions Are Unfavorable (regime osc > 3.0)

**Expected Behavior:**
- ❌ No trades (correct - protecting capital)
- ✅ Signals generated (shows detection working)
- ✅ Regime filter blocking entry (shows protection working)
- ✅ System stability (no crashes, continuous operation)

**What to Look For:**
- Dashboard showing `trades=0` but `signals > 0`
- Regime oscillation values mostly > 3.0
- No "ENTRY SIGNAL" log entries
- Continuous scanner activity ("Found X active markets")

---

## Key Metrics to Extract Tomorrow

### 1. System Stability
```bash
# Check for crashes
tail -1 logs/paper_mode_overnight_2026-03-01.log

# Count scanner errors
grep "Scan failed" logs/paper_mode_overnight_2026-03-01.log | wc -l

# Verify continuous operation
grep "DASH" logs/paper_mode_overnight_2026-03-01.log | wc -l
```

### 2. Signal Quality
```bash
# Total signals generated
grep "signals=" logs/paper_mode_overnight_2026-03-01.log | tail -1

# Signals filtered by momentum
grep "MOMENTUM FILTER" logs/paper_mode_overnight_2026-03-01.log | wc -l

# Signals filtered by regime
# (Implied by signals count not increasing when regime high)
```

### 3. Trade Performance (if any trades)
```bash
# Count trades
grep "trades=" logs/paper_mode_overnight_2026-03-01.log | tail -1

# Extract P&L
grep "P&L=" logs/paper_mode_overnight_2026-03-01.log | tail -10

# Win rate
grep "trades=" logs/paper_mode_overnight_2026-03-01.log | tail -1 | grep -oP '\d+% win'

# Exit method distribution
grep -E "(DEPTH-MOMENTUM EXIT|SPREAD REVERSION EXIT|IMBALANCE REVERSAL EXIT)" logs/paper_mode_overnight_2026-03-01.log | awk '{print $4}' | sort | uniq -c
```

### 4. Regime Analysis
```bash
# When was regime favorable?
grep "regime=osc" logs/paper_mode_overnight_2026-03-01.log | awk -F'osc=' '{print $2}' | awk '{print $1}' | awk '$1 < 3.0' | wc -l

# Lowest regime values
grep "regime=osc" logs/paper_mode_overnight_2026-03-01.log | awk -F'osc=' '{print $2}' | awk '{print $1}' | sort -n | head -10

# Average regime
grep "regime=osc" logs/paper_mode_overnight_2026-03-01.log | awk -F'osc=' '{print $2}' | awk '{print $1}' | awk '{sum+=$1; count++} END {print sum/count}'
```

---

## What Changes to Consider Tomorrow

### If NO Trades (All Night)

**Possible Issues:**
1. **Regime threshold too strict** (3.0 might be too low)
   - Check: How often was regime < 3.0?
   - Fix: Consider raising to 5.0 or 10.0

2. **Volume requirements too high** (0.7 BTC binance, 0.4 coinbase)
   - Check: Logs don't show this, need to add debug logging
   - Fix: Consider lowering to 0.5 / 0.3

3. **Momentum filter too strict** (0.8 threshold)
   - Check: Count "MOMENTUM FILTER" logs
   - Fix: Consider lowering to 0.6 or 0.7

4. **Liquidity requirements too high** (10 contracts exit-side)
   - Check: Look for "No orderbook data" or "Liquidity" warnings
   - Fix: Consider lowering to 5 contracts

### If MANY Trades (Good!)

**Analysis Needed:**
1. **Win rate** - Is it ≥45%? If not, tighten entry filters
2. **P&L per trade** - Is it positive? If not, check exit timing
3. **Max loss** - Any catastrophic losses? Should be capped at -75¢
4. **Fill rate** - Is it ≥60%? Check for "Market order fallback" usage
5. **Exit distribution** - Is one method dominating (>60%)? If so, others may need tuning

### If FEW Trades (1-5 trades)

**Perfect for initial validation!**
1. Review each trade in detail
2. Check entry/exit timing
3. Validate filters worked correctly
4. Measure actual vs expected P&L
5. Verify no edge cases or bugs

---

## Quick Start Guide for Tomorrow

```bash
# 1. Check if still running
ps aux | grep "crypto-scalp"

# 2. Get latest stats
tail -100 logs/paper_mode_overnight_2026-03-01.log | grep DASH | tail -5

# 3. Look for trades
grep "trades=" logs/paper_mode_overnight_2026-03-01.log | tail -1

# 4. If trades > 0, analyze them
grep -E "(ENTRY|EXIT|Trade)" logs/paper_mode_overnight_2026-03-01.log

# 5. If trades = 0, check why
echo "=== Regime Analysis ==="
grep "regime=osc" logs/paper_mode_overnight_2026-03-01.log | awk -F'osc=' '{print $2}' | awk '{print $1}' | sort -n | head -20

echo "=== Signal Count ==="
grep "signals=" logs/paper_mode_overnight_2026-03-01.log | tail -1
```

---

## Files to Review

1. **Main log**: `logs/paper_mode_overnight_2026-03-01.log`
2. **Config**: `strategies/configs/crypto_scalp_live.yaml`
3. **Implementation docs**:
   - `ENTRY_TIMING_OPTIMIZATION.md`
   - `PAPER_MODE_SUCCESS.md`
   - `STATISTICAL_EXITS_IMPLEMENTATION.md`

---

## Current Baseline (Before Tonight's Test)

**From previous 41-minute test:**
- Runtime: 2,441 seconds (41 min)
- Signals: 320
- Trades: 0
- Best regime: 1.7 (well below threshold)
- Worst regime: 65.9
- System stability: Perfect (no crashes, continuous operation)

**Hypothesis:** No trades despite low regime = other filters (volume, momentum, liquidity) protecting capital correctly.

**Tomorrow's Goal:** Determine if this is correct behavior or if filters are too strict.

---

## Contact Points

- Configuration: `strategies/configs/crypto_scalp_live.yaml`
- Main orchestrator: `strategies/crypto_scalp/orchestrator.py`
- Entry detector: `strategies/crypto_scalp/detector.py`
- Config dataclass: `strategies/crypto_scalp/config.py`

---

**Test started:** 2026-03-01 23:24:37
**Expected end:** Tomorrow morning (manual stop)
**Process ID:** 86512 (use `kill 86512` to stop)

Good luck! 🚀
