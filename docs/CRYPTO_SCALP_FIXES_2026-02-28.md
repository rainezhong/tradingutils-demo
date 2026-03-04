# Crypto Scalp Fixes - 2026-02-28

## Issue #2: Liquidity Protection Too Strict

### Problem
The liquidity protection added earlier today was TOO STRICT:
- Blocked exit at -27¢ adverse movement (limit was 20¢)
- Position got trapped and couldn't exit
- Market kept moving against us → eventually -34¢+ loss
- Force exit at 35s also failed due to Issue #3

### Fix
**Loosened the protection thresholds:**

```yaml
# Before:
min_exit_bid_depth: 5  # Too strict
max_adverse_exit_cents: 20  # Too strict (blocked -27¢ exit)

# After:
min_exit_bid_depth: 3  # More reasonable
max_adverse_exit_cents: 35  # Allows exits up to -35¢
```

### Rationale
- **Original -47¢ loss:** Was truly catastrophic (orderbook collapsed to 7¢)
- **The -27¢ exit:** Was bad but not catastrophic (market naturally moved)
- **New 35¢ threshold:** Protects against orderbook collapse while allowing normal bad trades

**Protection levels:**
- `-5¢ to -15¢`: Normal losing trade (allowed)
- `-15¢ to -35¢`: Bad trade (allowed, but logged)
- `-35¢ to -50¢+`: Catastrophic (BLOCKED - likely orderbook collapse)

### Files Changed
- `strategies/configs/crypto_scalp_live.yaml`
- `strategies/crypto_scalp/config.py`

---

## Issue #3: Force Exit Fails Near Market Expiry

### Problem
Markets stop accepting new orders within ~10-15 minutes of expiry:

```
20:17:12 - Force exit attempted (35s after entry, max_hold_sec)
           POST order → "400 Bad Request: invalid order"
           [Retries ~200 times over 3 minutes]
           [Position trapped until I killed the process]
```

**Market expiry:** 23:30 ET
**Trade time:** 20:17 PT = 23:17 ET (13 minutes before expiry)
**Result:** Kalshi rejects ALL new orders

### Fix
**Detect "market closed" errors and abandon position:**

```python
except Exception as e:
    error_msg = str(e).lower()

    # Detect market closure errors
    is_market_closed_error = any(
        x in error_msg
        for x in ["invalid order", "market closed", "not found", "expired"]
    )

    # If force exit fails due to market closure, give up
    if force and is_market_closed_error:
        logger.error(
            "EXIT FAILED (market likely closed/expired): %s - "
            "Abandoning position of %d contracts @ %d¢ entry. Error: %s",
            ticker, position.size, position.entry_price_cents, e
        )

        # Remove position to stop retrying
        with self._lock:
            if ticker in self._positions:
                del self._positions[ticker]
```

### Behavior

**Normal exit failure:**
- Logs error
- Keeps retrying (position stays in `self._positions`)

**Force exit failure near expiry:**
- Detects "invalid order" / "market closed" error
- Logs ERROR with context
- Removes position from tracking
- Stops retrying
- Position will settle at market close (we keep the shares until settlement)

### Why This Is Acceptable

When a market is minutes from expiry:
1. **Can't exit anyway** - Kalshi won't accept orders
2. **Position will settle** - We keep the shares and get paid if they win
3. **Better than infinite retry** - Spamming failed API calls is useless

**Example:**
- Bought YES @ 42¢ when BTC was $67,250
- Market strike: $67,300 (BTC must be > $67,300 for YES to win)
- At expiry: BTC = $67,203 → YES loses, worth $0
- **Loss: -42¢** (would have been same even if we could exit)

### Files Changed
- `strategies/crypto_scalp/orchestrator.py` (lines 1227-1247)

---

## Summary

**Issue #2 (Liquidity Protection):**
- ✅ Fixed: Loosened from 20¢ → 35¢ adverse limit
- ✅ Fixed: Lowered depth requirement from 5 → 3 contracts
- ✅ Result: Won't trap positions on normal losing trades

**Issue #3 (Force Exit Near Expiry):**
- ✅ Fixed: Detects market closure errors
- ✅ Fixed: Abandons position instead of infinite retry
- ✅ Result: Graceful degradation when market won't accept orders

## Testing Plan

1. **Test liquidity protection:**
   - Monitor next live session
   - Check if -27¢ exits are now allowed
   - Verify -35¢+ exits are still blocked

2. **Test expiry handling:**
   - Won't see this again unless trading <15min before expiry
   - Should log error and remove position gracefully
   - No more infinite "invalid order" spam

## Next Steps

Still need to address **Issue #1 (Fill Rate)**:
- 10% fill rate is the bigger problem
- Liquidity protection and expiry handling are edge cases
- Fill rate affects EVERY trade

Options for Issue #1:
1. Increase fill timeout (3s → 8s)
2. Add pre-flight orderbook check
3. Use more aggressive limit pricing
4. Switch to market orders for speed

**Recommendation:** Implement fill rate fix next before resuming live trading.
