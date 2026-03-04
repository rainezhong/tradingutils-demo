# Stuckness Filter - Implementation Complete ✅

**Date:** 2026-03-01
**Status:** Implemented and tested, ready for validation

---

## What Was Implemented

Added entropy-based stuckness filter to crypto scalp strategy that detects when markets have no trading edge.

### Files Modified

1. **`strategies/crypto_scalp/detector.py`**
   - Added `numpy` import
   - Added `_price_history` tracking (last 30 prices per ticker)
   - Added `_compute_price_entropy()` method (Shannon entropy calculation)
   - Added `_update_price_history()` method
   - Added `_is_market_stuck()` method (main detection logic)
   - Integrated stuckness check in `detect()` method

2. **`strategies/crypto_scalp/config.py`**
   - Added 5 new config parameters:
     - `enable_stuckness_filter: bool = False`
     - `min_price_entropy: float = 1.0`
     - `min_price_volatility_cents: float = 2.0`
     - `max_extreme_price: int = 90`
     - `stuckness_lookback_sec: float = 300.0`
   - Updated `from_yaml()` to load new parameters

3. **`strategies/configs/crypto_scalp_live.yaml`**
   - Added stuckness filter configuration section
   - Documented each parameter with analysis-based rationale
   - Default: filter DISABLED (enable after validation)

---

## How It Works

### Detection Logic

A market is classified as "stuck" (no edge) if:

```python
# Stuck if EITHER:
is_stuck = (
    entropy < min_price_entropy OR  # Low entropy (concentrated)
    (is_extreme AND volatility < min_volatility)  # Extreme + stuck
)
```

### Metrics Computed

**1. Price Entropy (Shannon entropy)**
- Uses histogram of last 30 prices (10¢ buckets)
- Formula: `H = -Σ p(x) * log₂(p(x))`
- Range: 0 bits (stuck) to ~3.3 bits (uniform)
- **Threshold: < 1.0 bits = stuck**

**2. Price Volatility**
- Standard deviation of price changes
- Computed on last 30 samples
- **Threshold: < 2.0¢ = low volatility**

**3. Price Extremity**
- Checks if price >90¢ or <10¢
- **Threshold: 90¢ (configurable)**

### Integration Point

Filter runs in `detector.detect()` method, **AFTER** all other filters but **BEFORE** returning signal:

```python
# Filter on price bounds
if entry_price > max_entry_price:
    return None

# NEW: Check if market is stuck
if self._is_market_stuck(ticker, entry_price, orderbook):
    return None  # Skip stuck markets

# Check spread filter
if orderbook.spread < min_spread:
    return None

return Signal(...)  # Only reached if not stuck
```

---

## Configuration

### Default Settings (Conservative)

```yaml
enable_stuckness_filter: false  # DISABLED by default
min_price_entropy: 1.0  # Skip if entropy < 1.0 bits
min_price_volatility_cents: 2.0  # Skip if volatility < 2¢
max_extreme_price: 90  # Skip if >90¢ or <10¢
```

### Tuning Options

**More Aggressive (fewer skips, more trades):**
```yaml
min_price_entropy: 0.7  # Allow lower entropy
min_price_volatility_cents: 1.5  # Allow lower volatility
max_extreme_price: 92  # Allow more extreme prices
```

**More Conservative (more skips, higher quality):**
```yaml
min_price_entropy: 1.2  # Require higher entropy
min_price_volatility_cents: 2.5  # Require higher volatility
max_extreme_price: 85  # Stricter extreme threshold
```

---

## Test Results

✅ **All tests passed:**

| Test | Result |
|------|--------|
| Config loading | ✅ Pass |
| Detector creation | ✅ Pass |
| Entropy calculation | ✅ Pass |
| Stuck detection (95¢ constant) | ✅ Pass (correctly marked stuck) |
| Active detection (45-54¢ range) | ✅ Pass (correctly marked active) |
| Extreme + volatility | ✅ Pass (marked stuck) |
| Extreme + low volatility | ✅ Pass (marked stuck) |
| Insufficient data | ✅ Pass (returns False) |
| Filter disabled | ✅ Pass (always returns False) |

### Example Results

```
Stuck prices (95-96¢): entropy = 0.00 bits → STUCK ✓
Active prices (45-54¢): entropy = 1.00 bits → NOT STUCK ✓
```

---

## Expected Impact (Based on Historical Analysis)

### Current Performance (No Filter)
- 135 signals per session
- 68% occur during stuck periods
- 38% win rate
- **-$2.50 to -$5.00 P&L per session**

### Projected Performance (With Filter)
- 43 signals per session (68% reduction)
- Only non-stuck periods traded
- **65% win rate** (+27 percentage points)
- **+$1.00 to +$2.00 P&L per session**

### Financial Impact
```
Per session:    -$2.50 → +$1.50  ($4.00 improvement)
Per day (3x):   -$7.50 → +$4.50  ($12.00 improvement)
Per month:      -$225  → +$135   ($360 improvement)
```

---

## Deployment Plan

### Phase 1: Paper Mode Testing (CURRENT PHASE)

**Goal:** Validate filter works without risking capital

```yaml
# crypto_scalp_live.yaml
enable_stuckness_filter: false  # Keep disabled, just log
```

**Steps:**
1. Run strategy in paper mode for 2-4 hours
2. Monitor logs for `STUCK FILTER` messages
3. Manually check if filtered signals would have lost
4. Verify non-filtered signals perform better

**Expected logs:**
```
DEBUG STUCK FILTER: KXBTC15M-... @ 95¢ (entropy=0.12, vol=0.3, extreme=True)
```

### Phase 2: Enable Filter Logging

**Goal:** Track filter performance in live mode

```python
# Temporarily modify detector.py to always log:
if self._is_market_stuck(...):
    logger.info("WOULD SKIP (stuck): %s @ %d¢ (ent=%.2f, vol=%.1f)", ...)
    # return None  # Comment out to still trade
```

**Duration:** 1-2 live sessions
**Metrics to track:**
- % of signals marked as stuck
- Win rate of stuck vs non-stuck signals
- P&L of stuck vs non-stuck signals

### Phase 3: Enable Filter (Production)

**Goal:** Use filter in live trading

```yaml
# crypto_scalp_live.yaml
enable_stuckness_filter: true  # ENABLE
```

**Monitor:**
- Total signals dropped from ~135 → ~43 (68% reduction)
- Win rate improved from ~38% → ~65%
- P&L improved from negative → positive

**Rollback criteria:**
- If win rate DOESN'T improve after 50+ trades
- If filter blocks >80% of signals (too aggressive)
- If non-stuck signals still lose money

### Phase 4: Tune Thresholds

Based on Phase 3 results, adjust thresholds:

**If filter too aggressive (>75% skipped):**
```yaml
min_price_entropy: 0.8  # Lower from 1.0
```

**If filter too loose (win rate not improving):**
```yaml
min_price_entropy: 1.2  # Raise from 1.0
```

---

## Validation Checklist

### Pre-Deployment
- [x] Code implemented
- [x] Unit tests pass
- [x] Config parameters added
- [x] Documentation created
- [ ] Paper mode testing (2-4 hours)
- [ ] Log analysis confirms stuck signals lose

### Post-Deployment (Phase 3)
- [ ] Filter enabled in live mode
- [ ] Monitor first 20 signals (baseline)
- [ ] Compare win rate after 50 signals
- [ ] Validate P&L improvement
- [ ] Tune thresholds if needed

---

## Monitoring Commands

### Check if filter is enabled
```bash
grep "enable_stuckness_filter" strategies/configs/crypto_scalp_live.yaml
```

### Monitor stuck signals in logs
```bash
tail -f logs/crypto-scalp_live_*.log | grep "STUCK FILTER"
```

### Count stuck vs non-stuck
```bash
grep "STUCK FILTER" logs/crypto-scalp_live_*.log | wc -l
```

### Analyze filter performance
```python
# After session, analyze logs
import re
with open('logs/crypto-scalp_live_TIMESTAMP.log') as f:
    stuck_count = len([l for l in f if 'STUCK FILTER' in l])
    total_signals = len([l for l in f if 'SIGNAL:' in l])
    print(f'Filtered: {stuck_count} / {total_signals} ({100*stuck_count/total_signals:.1f}%)')
```

---

## Troubleshooting

### Issue: Too many signals filtered (>80%)

**Cause:** Thresholds too strict

**Fix:**
```yaml
min_price_entropy: 0.7  # Lower from 1.0
max_extreme_price: 92  # Raise from 90
```

### Issue: Win rate not improving

**Cause:** Filter not strict enough OR wrong assumption

**Fix 1 (stricter):**
```yaml
min_price_entropy: 1.2  # Raise from 1.0
min_price_volatility_cents: 2.5  # Raise from 2.0
```

**Fix 2 (validate assumption):**
- Check if stuck signals actually have lower win rate
- If not, disable filter (`enable_stuckness_filter: false`)

### Issue: ImportError for numpy

**Cause:** numpy not installed

**Fix:**
```bash
pip3 install numpy
```

---

## Files Reference

**Implementation:**
- `strategies/crypto_scalp/detector.py` (lines ~8, ~95, ~310-415, ~220)
- `strategies/crypto_scalp/config.py` (lines ~95-100, ~165-170)
- `strategies/configs/crypto_scalp_live.yaml` (stuckness section)

**Documentation:**
- `STUCKNESS_ANALYSIS_RESULTS.md` (detailed analysis)
- `STUCKNESS_KEY_FINDINGS.md` (quick reference)
- `STUCKNESS_FILTER_IMPLEMENTED.md` (this file)
- `analysis_stuckness_feb18.csv` (raw data)

**Testing:**
- Inline tests in this file (see Test Results section)

---

## Summary

✅ **Implementation complete and tested**
✅ **Expected to improve win rate by +27pp**
✅ **Expected to improve P&L by $4+ per session**
✅ **Ready for paper mode validation**

**Next step:** Run in paper mode with logging to validate filter performance before enabling in live mode.

---

**Implementation Date:** 2026-03-01
**Implemented By:** Claude Sonnet 4.5
**Status:** ✅ Complete, pending validation
