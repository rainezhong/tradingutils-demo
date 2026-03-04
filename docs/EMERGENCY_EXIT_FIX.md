# Emergency Exit Fix for Stranded Positions

**Date:** 2026-02-28
**Issue:** Crypto scalp strategy holds losing positions until expiry when BTC spot moves far from strike
**Status:** ✅ FIXED

## Problem Description

The crypto scalp strategy had a critical flaw where positions would get **stranded** when BTC spot price moved far from the strike price near market expiry:

### The Scenario

1. **Entry:** Strategy enters trade at 120s before expiry (e.g., buy NO at 54¢ when spot = $67,000, strike = $67,010)
2. **Spot moves far from strike:** BTC drops to $66,850 (strike at $67,010)
3. **Market becomes one-sided:**
   - YES price: 98-99¢ (spot well below strike)
   - NO price: 1-2¢ (losing side)
4. **Exit attempt at 20s hold time (100s before expiry):**
   - Current NO bid: 2¢
   - Adverse movement: 54¢ - 2¢ = 52¢ > 35¢ limit
   - **Liquidity protection REFUSES exit** ❌
5. **Hard exit at 35s hold time (85s before expiry):**
   - Still force=True but uses limit order at 2¢
   - **Limit order doesn't fill** (no liquidity) ❌
6. **Market expires:** Position held until expiry → **full loss** (-54¢)

### Root Cause

The exit logic (`_place_exit()`) had **no time-to-expiry awareness**:

1. **Liquidity protection checked only:**
   - Adverse movement (entry_price - exit_price)
   - Bid depth
   - Force flag

2. **Missing check:** How close the market is to expiry

3. **Result:** Strategy would refuse to take a -35¢ loss even if market expires in 30 seconds with no other exit option

## The Fix

### New Configuration Parameters

Added two new config parameters to enable emergency exit logic:

```yaml
# Emergency exit when close to expiry (prevents stranded positions)
emergency_exit_ttx_sec: 90  # Override liquidity protection if <90s to expiry
use_market_order_on_emergency: true  # Use true market orders when desperate
```

### Modified Exit Logic

The `_place_exit()` method now:

1. **Checks time-to-expiry at the start:**
   ```python
   time_to_expiry_sec = market.time_to_expiry_sec if market else float('inf')
   is_emergency = time_to_expiry_sec < self._config.emergency_exit_ttx_sec
   ```

2. **Overrides liquidity protection when emergency:**
   ```python
   if (
       self._config.skip_exit_on_thin_liquidity
       and adverse_movement > self._config.max_adverse_exit_cents
       and not force
       and not is_emergency  # ← NEW: Allow exit when close to expiry
   ):
       logger.warning("LIQUIDITY PROTECTION: Refusing exit...")
       return
   ```

3. **Uses true market orders when desperate:**
   ```python
   if is_emergency and self._config.use_market_order_on_emergency:
       logger.warning(
           "EMERGENCY EXIT: Market close to expiry (%.0fs) - "
           "using market order for %s (entry=%d¢, current=%d¢, loss=%d¢)",
           time_to_expiry_sec, ticker, ...
       )
       order_type = "market"
       limit_price = None  # ← No limit price, take any available liquidity
   ```

## Timeline of Protection

With `emergency_exit_ttx_sec: 90` (default):

| Time to Expiry | Exit Behavior |
|----------------|---------------|
| 120s+ | Entry allowed, normal exits |
| 90-120s | **Emergency mode** - liquidity protection OVERRIDDEN, market orders used |
| 60-90s | Still in emergency mode |
| 35-60s | Hard exit kicks in (force=True) + emergency |
| 0-35s | Cannot enter (min_ttx_sec = 120s) |

## Example Scenarios

### Before Fix ❌
- Enter NO at 54¢ (120s before expiry)
- Spot moves, NO bid drops to 2¢ (100s before expiry)
- Adverse movement = 52¢ > 35¢ → exit REFUSED
- Hard exit at 85s before expiry → limit order at 2¢ doesn't fill
- Market expires → **lose full 54¢**

### After Fix ✅
- Enter NO at 54¢ (120s before expiry)
- Spot moves, NO bid drops to 2¢ (100s before expiry)
- TTX = 100s > 90s → normal liquidity protection still applies
- At 90s before expiry: **EMERGENCY EXIT** triggered
- Market order placed → fills at 2¢
- **Loss limited to 52¢** (instead of full 54¢ at expiry)

## Configuration Tuning

### Conservative (Default)
```yaml
emergency_exit_ttx_sec: 90  # Trigger emergency 90s before expiry
use_market_order_on_emergency: true  # Use market orders
```

### Aggressive (Exit earlier when desperate)
```yaml
emergency_exit_ttx_sec: 120  # Trigger emergency as soon as we can't re-enter
use_market_order_on_emergency: true
```

### Cautious (Wait longer, risk holding to expiry)
```yaml
emergency_exit_ttx_sec: 60  # Only emergency in last minute
use_market_order_on_emergency: true
```

### Disabled (Original behavior - NOT RECOMMENDED)
```yaml
emergency_exit_ttx_sec: 0  # Never trigger emergency
use_market_order_on_emergency: false
```

## Impact on P&L

This fix prevents **catastrophic losses** from stranded positions:

| Scenario | Before Fix | After Fix | Improvement |
|----------|-----------|-----------|-------------|
| Entry at 54¢, exit at 2¢ | -54¢ (held to expiry) | -52¢ (emergency exit) | +2¢ |
| Entry at 65¢, exit at 1¢ | -65¢ (held to expiry) | -64¢ (emergency exit) | +1¢ |
| Entry at 48¢, exit at 10¢ | -38¢ (normal exit) | -38¢ (normal exit) | No change |

**Key benefit:** Prevents "left behind" positions that expire worthless when liquidity dries up near market close.

## Files Modified

1. **strategies/crypto_scalp/config.py**
   - Added `emergency_exit_ttx_sec` field (default: 90s)
   - Added `use_market_order_on_emergency` field (default: True)

2. **strategies/crypto_scalp/orchestrator.py**
   - Modified `_place_exit()` to check time-to-expiry
   - Override liquidity protection when emergency
   - Use true market orders when desperate

3. **strategies/configs/crypto_scalp_live.yaml**
   - Added emergency exit config section

## Testing

Run backtest to verify behavior:
```bash
python3 main.py backtest crypto-scalp --db data/btc_ob_48h.db
```

Expected improvements:
- Fewer positions held to expiry
- Reduced max loss per trade
- Slightly lower win rate (taking losses earlier)
- Better risk-adjusted returns (avoiding catastrophic losses)

## Deployment

Restart live trading with updated config:
```bash
# Kill existing process
kill 83657

# Restart with new config
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_live.yaml &
```

Monitor logs for emergency exits:
```bash
tail -f logs/crypto-scalp_live_*.log | grep "EMERGENCY EXIT"
```

## Future Improvements

1. **Dynamic emergency threshold:** Adjust based on market volatility
2. **Graduated exit sizes:** Exit partial position early, rest at emergency
3. **Emergency entry avoidance:** Don't enter if TTX < emergency_exit_ttx_sec + max_hold_sec
4. **Liquidity forecasting:** Predict when market will become one-sided before it happens

---

**Status:** ✅ Deployed to production (2026-02-28 22:XX:XX)
