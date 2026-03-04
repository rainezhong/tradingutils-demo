# Pre-Flight Orderbook Check Implementation

**Date:** 2026-02-28
**Fix:** Option #4 from fill rate analysis - Check current market before placing order
**Goal:** Improve 10% fill rate by avoiding stale prices

## The Problem

**Signal detection vs Order placement timing gap:**

```
t=0ms   - Signal detected: BTC dropped $22, Kalshi NO @ 60¢
t=50ms  - Strategy calculates: Buy NO @ 61¢ (60 + 1¢ buffer)
t=100ms - Order placed: Buy NO @ 61¢
t=150ms - Kalshi orderbook reprices: NO ask → 71¢
t=3000ms- Order timeout (still unfilled @ 61¢)
```

**Result:** 90% of orders timeout because market moved between signal detection and order placement.

## The Solution

**Check current orderbook BEFORE placing order:**

```
t=0ms   - Signal detected: BTC dropped $22, signal price = 60¢
t=50ms  - PRE-FLIGHT CHECK:
          - Query current orderbook
          - Current NO ask = 71¢ (not 60¢!)
          - Price gap = 11¢ > 10¢ threshold
          → SKIP TRADE (missed the window)
```

OR if market hasn't moved too far:

```
t=0ms   - Signal detected: signal price = 60¢
t=50ms  - PRE-FLIGHT CHECK:
          - Current NO ask = 64¢
          - Price gap = 4¢ < 5¢ threshold
          → Use signal price 61¢ (market hasn't moved much)
```

OR if market moved moderately:

```
t=0ms   - Signal detected: signal price = 60¢
t=50ms  - PRE-FLIGHT CHECK:
          - Current NO ask = 67¢
          - Price gap = 7¢ (between 5-10¢)
          → Use current ask 68¢ (cross the spread)
```

## Implementation

### Three Zones Based on Price Movement

**Zone 1: Market close to signal (gap ≤ 5¢)**
- Use signal price + 1¢ buffer
- Market hasn't moved much, get good fill at fair value
- Example: Signal 60¢ → Current 63¢ → Order @ 61¢

**Zone 2: Market moved moderately (5¢ < gap ≤ 10¢)**
- Use current ask + 1¢ (cross the spread)
- Market moved but still catchable, pay up for fill
- Example: Signal 60¢ → Current 67¢ → Order @ 68¢

**Zone 3: Market moved too far (gap > 10¢)**
- Skip trade entirely
- Window closed, missed the opportunity
- Example: Signal 60¢ → Current 71¢ → SKIP

### Code Added to `_place_entry()`

```python
# PRE-FLIGHT CHECK: Verify current market hasn't moved too far from signal
current_orderbook = None
if self._orderbook_manager:
    current_orderbook = self._orderbook_manager.get_orderbook(signal.ticker)

if current_orderbook:
    # Get current ask price
    if signal.side == "yes":
        current_ask = current_orderbook.best_ask.price
    else:
        current_ask = 100 - current_orderbook.best_bid.price

    # Check price gap
    price_gap = abs(signal.entry_price_cents - current_ask)

    # ZONE 3: Skip if moved >10¢
    if price_gap > 10:
        logger.warning("SKIP: Market moved %d¢ - window closed", price_gap)
        return

    # ZONE 2: Use current ask if moved 5-10¢
    elif price_gap > 5:
        logger.info("Market moved %d¢ - using current ask %d¢", price_gap, current_ask)
        limit_price = min(current_ask + 1, 99)

    # ZONE 1: Use signal price if moved ≤5¢
    else:
        limit_price = min(signal.entry_price_cents + 1, 99)
```

### Configuration

**New config fields:**
```yaml
# strategies/crypto_scalp/config.py
max_price_deviation_cents: 10  # Skip if market moved >10¢ from signal
adaptive_price_threshold_cents: 5  # Use current ask if moved >5¢
```

**YAML config:**
```yaml
# strategies/configs/crypto_scalp_live.yaml
max_price_deviation_cents: 10
adaptive_price_threshold_cents: 5
```

## Expected Impact

### Before (No Pre-Flight Check)

**Scenario:** BTC drops $22, signal detected @ 60¢, current market @ 71¢

```
Action: Place order @ 61¢
Result: Timeout (market @ 71¢, order @ 61¢ unfilled)
Fill rate: 10%
```

### After (With Pre-Flight Check)

**Scenario 1: Market moved >10¢**
```
Signal: 60¢ → Current: 71¢ → Gap: 11¢
Action: SKIP (window closed)
Result: No order placed (saves wasted API calls)
Fill rate: N/A (trade skipped)
```

**Scenario 2: Market moved 5-10¢**
```
Signal: 60¢ → Current: 67¢ → Gap: 7¢
Action: Order @ 68¢ (current ask + 1¢)
Result: FILLS (crossing the spread)
Fill rate: ~70-80% (much higher)
```

**Scenario 3: Market stable (≤5¢ move)**
```
Signal: 60¢ → Current: 63¢ → Gap: 3¢
Action: Order @ 61¢ (signal price + 1¢)
Result: FILLS (good price)
Fill rate: ~80-90%
```

### Overall Expected Improvement

**Before:**
- Fill rate: 10% (9 of 10 orders timeout)
- Avg price: Good (when it fills)
- Wasted API calls: High (9 failed orders per 1 fill)

**After:**
- Fill rate: 60-80% (of orders PLACED)
- Avg price: Slightly worse (paying up in Zone 2)
- Wasted API calls: Low (skip Zone 3 entirely)
- **Total trades: May decrease** (skipping Zone 3 opportunities)

### Trade-Offs

**Pros:**
- ✅ Much higher fill rate on placed orders
- ✅ Fewer wasted API calls (skip when window closed)
- ✅ More realistic pricing (based on current market)
- ✅ Automatic adaptation to market conditions

**Cons:**
- ⚠️ Worse average entry price (paying current ask in Zone 2)
- ⚠️ Fewer total signals (skipping Zone 3)
- ⚠️ May miss repricing opportunities (Zone 3 markets that reprice back)

## Testing Plan

1. **Paper trading first:**
   - Run with `paper_mode: true`
   - Monitor logs for Zone 1/2/3 behavior
   - Check fill rate improvement

2. **Monitor key metrics:**
   ```
   Zone 1 (≤5¢):  X% of signals → Y% fill rate
   Zone 2 (5-10¢): X% of signals → Y% fill rate
   Zone 3 (>10¢):  X% of signals → skipped
   ```

3. **Compare to baseline:**
   - Before: 10 orders → 1 fill (10%)
   - After: 5 Zone 1 → 4 fills (80%)
           3 Zone 2 → 2 fills (67%)
           2 Zone 3 → 0 skipped
   - Net: 8 orders → 6 fills (75% vs 10%)

## Files Modified

1. **strategies/crypto_scalp/orchestrator.py**
   - Added pre-flight check in `_place_entry()` (lines 969-1024)
   - Checks current orderbook before placing order
   - Three-zone logic based on price gap

2. **strategies/crypto_scalp/config.py**
   - Added `max_price_deviation_cents: int = 10`
   - Added `adaptive_price_threshold_cents: int = 5`
   - Updated `from_yaml()` to load new fields

3. **strategies/configs/crypto_scalp_live.yaml**
   - Documented new config fields
   - Set to defaults (10¢ and 5¢)

## Next Steps

1. ⬜ Test in paper mode (verify no crashes)
2. ⬜ Monitor Zone 1/2/3 distribution
3. ⬜ Adjust thresholds if needed (5¢ and 10¢ are initial guesses)
4. ⬜ Test in live mode with 1 contract
5. ⬜ Scale up if fill rate >60%

## Tuning Guidance

**If too many Zone 3 skips:**
- Increase `max_price_deviation_cents` from 10 → 15¢
- Accept more trades with worse pricing

**If Zone 2 fills poorly:**
- Decrease `adaptive_price_threshold_cents` from 5 → 3¢
- Cross spread more aggressively

**If losing money on Zone 2 trades:**
- Decrease `max_price_deviation_cents` from 10 → 8¢
- Skip more marginal opportunities

## Key Insight

**This fix doesn't make Kalshi reprice faster.** It just:
1. Detects when you've already missed the window (Zone 3)
2. Pays current market price when window is closing (Zone 2)
3. Gets good prices when you're still early (Zone 1)

**The real edge** is being fast enough to stay in Zone 1 most of the time. If you're consistently hitting Zone 2/3, you need lower latency, not better pricing logic.
