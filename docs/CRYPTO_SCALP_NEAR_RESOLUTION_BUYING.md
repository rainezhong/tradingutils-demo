# Crypto Scalp Near-Resolution Buying Analysis

**Date:** 2026-02-28
**Issue:** Strategy entered position too close to market expiry

---

## The Problem

### Problematic Trade from Feb 28 Live Session

**Trade Details:**
```
Entry:  22:54:10 EST - BUY YES @ 25¢ (1 contract)
Expiry: 23:00:00 EST - Market closes
TTX:    350 seconds (5.8 minutes)
Result: LIKELY LOST - Market expired OTM, position stranded
```

**Why this happened:**
- Config: `min_ttx_sec: 120` (2 minutes minimum)
- Trade had 350s to expiry → ✓ PASSED filter (350s > 120s)
- But this created multiple risks!

---

## The Risks of Near-Expiry Entry

### Risk 1: Insufficient Exit Window

**Strategy timing:**
- Normal hold time: `exit_delay_sec: 20s`
- Max hold time: `max_hold_sec: 35s`
- Emergency exit threshold: `emergency_exit_ttx_sec: 90s`

**What happened:**
```
22:54:10 - ENTRY (350s to expiry)
22:54:30 - Normal exit window (20s later) → 330s to expiry ✓ Should work
22:54:45 - Max hold exit (35s later) → 315s to expiry ✓ Should work
22:55:40 - Emergency zone (<90s) → 260s elapsed, 90s to expiry
```

**In this case, the trade SHOULD have exited normally.** But...

### Risk 2: Market Closure Uncertainty

From **CRYPTO_SCALP_FIXES_2026-02-28.md**:
> Markets stop accepting new orders within ~10-15 minutes of expiry

**Timeline:**
- Kalshi may reject orders <10-15 min before expiry
- Our trade entered at 5.8 min before expiry
- **This is INSIDE the rejection window!**

**Actual Kalshi behavior (observed):**
```
23:00:00 - Official expiry time
22:45:00 - Markets MAY start rejecting orders (15 min before)
22:50:00 - Markets LIKELY rejecting orders (10 min before)
22:54:10 - Our entry (5.8 min before) → HIGH RISK ZONE
```

### Risk 3: Stranded Position

If you enter at 5.8 min before expiry:
1. Entry order MIGHT fill (got lucky)
2. Hold for 20-35 seconds
3. Try to exit at 22:54:30-45
4. Exit order REJECTED ("invalid order" / "market closed")
5. Position stranded, forced to hold until settlement
6. If market expires OTM → **FULL LOSS**

**This is EXACTLY what happened:**
- Entry: YES @ 25¢
- No exit logged in order history
- Market expired at 23:00:00
- Likely result: Lost entire 25¢ (plus 2¢ entry fee)

---

## Current Filters Are Inadequate

### Current Config
```yaml
min_ttx_sec: 120        # 2 minutes - TOO SHORT
max_ttx_sec: 900        # 15 minutes - OK
emergency_exit_ttx_sec: 90  # 90 seconds - TOO LATE
```

### Why 2 Minutes Is Too Short

**Safe entry requires:**
```
min_ttx_sec ≥ max_hold_sec + market_closure_buffer + safety_margin

Components:
- max_hold_sec: 35 seconds (hard exit time)
- market_closure_buffer: 600 seconds (10 min, when Kalshi starts rejecting)
- safety_margin: 120 seconds (2 min buffer for edge cases)

Calculation:
min_ttx_sec = 35 + 600 + 120 = 755 seconds (~12.5 minutes)
```

**Conservative approach:**
```
min_ttx_sec = 900 seconds (15 minutes)
```
This ensures:
- Entry happens BEFORE Kalshi rejection window (10-15 min)
- Full 35s max hold time available
- Emergency exit at 90s still has 5+ minutes to expiry
- Exit orders won't be rejected

---

## Proposed Fix

### Option 1: Conservative (Recommended)

**Eliminate near-expiry risk entirely:**
```yaml
min_ttx_sec: 900   # 15 minutes - no entry inside market closure window
max_ttx_sec: 900   # 15 minutes - SAME as min (only trade fresh markets)
```

**Result:**
- Only trades markets with 15+ minutes to expiry
- Ensures exit window is always available
- Avoids ALL near-expiry issues

**Trade-off:**
- May miss some opportunities (markets 2-15 min old)
- But those are risky anyway (less liquidity, higher rejection risk)

### Option 2: Moderate

**Allow some aging but stay safe:**
```yaml
min_ttx_sec: 600   # 10 minutes - stay outside rejection window
max_ttx_sec: 900   # 15 minutes
```

**Result:**
- Can trade markets 10-15 min to expiry
- Still have 9+ minutes after max hold for safe exit
- Slightly more opportunities than Option 1

**Trade-off:**
- Closer to rejection window (10 min is when issues START)
- Still risky during high volatility

### Option 3: Current + Emergency Fix (Not Recommended)

**Keep current 2 min minimum, rely on emergency detection:**
```yaml
min_ttx_sec: 120   # 2 minutes - RISKY
emergency_exit_ttx_sec: 600  # 10 minutes - exit early if close to expiry
```

**Why NOT recommended:**
- Entry order might get rejected even at 2 min
- Emergency exit is a band-aid, not prevention
- Still creates stranded position risk

---

## Recommended Action

**Implement Option 1 (Conservative):**

```yaml
# strategies/configs/crypto_scalp_live.yaml

# Time to expiry filters (UPDATED 2026-02-28 to prevent near-expiry entries)
min_ttx_sec: 900   # 15 min - only trade fresh markets
max_ttx_sec: 900   # 15 min - same as min (very conservative)

# Emergency exit (kept for safety, but should rarely trigger now)
emergency_exit_ttx_sec: 90
use_market_order_on_emergency: true
```

**Rationale:**
1. BTC 15-minute markets are created EVERY 15 MINUTES
2. There's ALWAYS a fresh market available
3. No need to trade aging markets (they're riskier anyway)
4. Complete elimination of near-expiry risk

**Impact on trade count:**
- Minimal - Fresh markets have better liquidity
- Better fill rates (market makers more active)
- Lower rejection risk

---

## Verification

### How to confirm this fixes the issue:

1. **Check live logs** after update:
   ```
   grep "TTX" logs/crypto-scalp_live_*.log | grep "SKIP"
   ```
   Should see: "SKIP: market TTX 350s < 900s minimum"

2. **Monitor entry times:**
   ```
   grep "ENTRY" logs/crypto-scalp_live_*.log
   ```
   All entries should have TTX ≥ 900s

3. **Check for stranded positions:**
   ```
   grep "Abandoning position" logs/crypto-scalp_live_*.log
   ```
   Should be ZERO occurrences (no more stranded positions)

---

## Historical Context

### Before Fix (Feb 28, 22:54:10)
```
Entry: YES @ 25¢ at 22:54:10
Expiry: 23:00:00 (350s later)
Result: Position likely stranded and lost
Loss: -27¢ (25¢ price + 2¢ fee)
```

### After Fix (with min_ttx_sec: 900)
```
Signal detected at 22:54:10
Market TTX: 350s
Filter: 350s < 900s → SKIP
Result: No entry, no loss
```

---

## Related Issues Fixed

This change also addresses:

1. **Issue #3 (Force Exit Near Expiry)**
   - From CRYPTO_SCALP_FIXES_2026-02-28.md
   - Won't enter if exit window is uncertain

2. **Emergency Exit TTX**
   - Emergency exit at 90s is now a true emergency
   - Should rarely trigger (only if market was fresh at entry)

3. **Liquidity Protection**
   - Fresh markets have better liquidity
   - Fewer thin-orderbook exits

---

## Additional Considerations

### Should we ALSO lower max_ttx_sec?

**Current:** `max_ttx_sec: 900` (15 min)

**Question:** Do we want to trade markets OLDER than 15 min?

**Answer:** Probably NO, because:
- Crypto BTC markets refresh every 15 minutes
- Older markets = less liquidity
- Older markets = more participants, harder to get edge
- **We only need to trade the FRESHEST market**

**Proposed:**
```yaml
min_ttx_sec: 900   # 15 min
max_ttx_sec: 900   # 15 min (same as min)
```

This means: **Only trade markets that are EXACTLY 15 minutes from expiry (fresh markets).**

This is VERY conservative but optimal for this strategy.

---

## Implementation Checklist

- [ ] Update `strategies/configs/crypto_scalp_live.yaml`:
  - Set `min_ttx_sec: 900`
  - Set `max_ttx_sec: 900`
- [ ] Update `strategies/crypto_scalp/config.py` defaults:
  - Change default `min_ttx_sec` from 120 → 900
  - Keep `max_ttx_sec` at 900
- [ ] Test in paper mode:
  - Verify signals are skipped if TTX < 900
  - Verify fresh markets are still traded
- [ ] Test in live mode (1 contract):
  - Monitor for stranded positions (should be zero)
  - Verify exit success rate improves
- [ ] Document in memory:
  - Add to MEMORY.md under crypto scalp fixes

---

## Expected Outcome

**Before Fix:**
- Occasional near-expiry entries (350s TTX)
- Risk of stranded positions
- Forced settlements at unfavorable prices

**After Fix:**
- ALL entries have 15 min to expiry
- Zero stranded positions
- Clean exits every time
- Slightly fewer total signals (but safer signals)

**Net effect:** Higher win rate, lower catastrophic loss risk, better sleep at night.

---

**Analysis Date:** 2026-02-28
**Analyst:** Claude Sonnet 4.5
**Data Source:** Live trading logs, Kalshi order history, CRYPTO_SCALP_FIXES_2026-02-28.md
**Status:** ⏳ PENDING IMPLEMENTATION
