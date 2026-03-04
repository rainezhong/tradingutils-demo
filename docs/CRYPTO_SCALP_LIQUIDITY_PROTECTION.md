# Crypto Scalp Liquidity Protection

**Date:** 2026-02-28
**Author:** Empirical Kelly Analysis + Live Trading Incident
**Issue:** Trade #7 lost 47¢ (-87%) due to thin orderbook liquidity

## The Problem

During live trading on 2026-02-28, Trade #7 experienced a catastrophic loss:
- Entry: YES @ 54¢ (19:55:08)
- Exit: YES @ 7¢ (19:55:28)
- Loss: **-47¢** (-87% of entry price)
- Hold time: 20 seconds (normal exit, not force)

### Root Cause

The strategy placed a **limit sell order at 7¢** because:
1. Best bid in orderbook was 7¢ or lower
2. Exit logic: `limit_price = max(best_bid - exit_slippage, 1)`
3. With `exit_slippage_cents = 0`, limit = 7¢
4. Order filled immediately at 7¢

This was **not a bug** - it was a **liquidity crisis**. The Kalshi orderbook had no buyers above 7¢.

## The Solution

Added three-layer liquidity protection:

### 1. Maximum Adverse Price Limit
```yaml
max_adverse_exit_cents: 20  # Refuse exits worse than -20¢ from entry
```

**Logic:**
```python
adverse_movement = entry_price - exit_price
if adverse_movement > 20 cents:
    skip exit, retry later
```

**Example:**
- Entry @ 54¢, exit price 7¢ → adverse = 47¢ > 20¢ → **EXIT REFUSED**
- Entry @ 50¢, exit price 35¢ → adverse = 15¢ < 20¢ → exit allowed

### 2. Minimum Bid Depth Check
```yaml
min_exit_bid_depth: 5  # Require at least 5 contracts at best bid
```

**Logic:**
```python
if orderbook.best_bid.quantity < 5 contracts:
    skip exit, retry later
```

**Example:**
- Best bid: 50¢ with 2 contracts → **EXIT REFUSED** (depth too low)
- Best bid: 48¢ with 10 contracts → exit allowed

### 3. Skip on Thin Liquidity (Master Switch)
```yaml
skip_exit_on_thin_liquidity: true  # Enable protection
```

Enables/disables both protections above.

## Behavior

### Normal Exit (Conditions Good)
1. Check: adverse movement ≤ 20¢? ✅
2. Check: bid depth ≥ 5 contracts? ✅
3. Place limit order at best_bid - exit_slippage
4. Exit completes

### Protected Exit (Liquidity Too Thin)
1. Check: adverse movement > 20¢? ❌ **PROTECTION TRIGGERED**
2. Log warning: "LIQUIDITY PROTECTION: Refusing exit..."
3. Skip exit, return early
4. Retry on next check (100ms later)
5. Continue retrying until:
   - Liquidity improves (adverse < 20¢, depth ≥ 5), OR
   - Force exit time reached (35 seconds)

### Force Exit (Max Hold Time Reached)
At 35 seconds (max_hold_sec):
- **Liquidity protection is bypassed** (force=True)
- Uses market order regardless of liquidity
- Accepts any fill price (last resort)

## Configuration

**Conservative (Recommended):**
```yaml
min_exit_bid_depth: 10
max_adverse_exit_cents: 15
skip_exit_on_thin_liquidity: true
```

**Moderate:**
```yaml
min_exit_bid_depth: 5
max_adverse_exit_cents: 20
skip_exit_on_thin_liquidity: true
```

**Aggressive (Accept More Risk):**
```yaml
min_exit_bid_depth: 2
max_adverse_exit_cents: 30
skip_exit_on_thin_liquidity: true
```

**Disabled (Original Behavior):**
```yaml
skip_exit_on_thin_liquidity: false
```

## Trade-offs

### Benefits
- ✅ Prevents catastrophic losses from thin orderbooks
- ✅ Protects against -47¢ style events
- ✅ Gives market time to find liquidity
- ✅ Automatic retry mechanism

### Costs
- ⚠️ May delay exits in volatile markets
- ⚠️ Could miss optimal exit prices
- ⚠️ Still forces exit at max_hold_sec regardless
- ⚠️ Adds complexity to exit logic

## Monitoring

Watch for these log messages:

**Protection Triggered:**
```
WARNING LIQUIDITY PROTECTION: Refusing exit for KXBTC15M-... -
adverse movement 47¢ > limit 20¢ (entry=54¢, exit=7¢, depth=2)
```

**Protection Triggered (Depth):**
```
WARNING LIQUIDITY PROTECTION: Refusing exit for KXBTC15M-... -
insufficient depth 2 < minimum 5 (price=45¢)
```

**Force Exit (Protection Bypassed):**
```
INFO EXIT [FORCE]: YES KXBTC15M-... 1 @ 7c (was 54c)
```

## Testing

To test the protection:
1. Run in paper mode with thin markets
2. Set `max_adverse_exit_cents: 5` (very strict)
3. Check logs for "LIQUIDITY PROTECTION" warnings
4. Verify exits are skipped and retried

## Historical Context

**Before Protection (2026-02-28 19:48-20:05):**
- 10 trades
- 0% win rate
- -$1.01 total P&L
- One catastrophic -47¢ loss (Trade #7)

**After Protection:**
- TBD - will monitor in next live session

## Implementation Details

**Files Modified:**
- `strategies/crypto_scalp/config.py`: Added 3 new config fields
- `strategies/crypto_scalp/orchestrator.py`: Added checks in `_place_exit()`
- `strategies/configs/crypto_scalp_live.yaml`: Enabled protection

**Code Location:**
- Check: `orchestrator.py:1145-1180` (approx)
- Config: `config.py:75-78`
