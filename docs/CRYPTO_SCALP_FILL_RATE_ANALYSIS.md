# Crypto Scalp Fill Rate Analysis

**Date:** 2026-02-28
**Issue:** 10% fill rate in live trading vs 100% in paper trading

## Executive Summary

The strategy placed **~10 orders**, but only **1 filled** (10% fill rate). This is NOT a bug - it's a **timing and pricing issue**.

### Root Cause

The strategy uses **limit orders** with conservative pricing that wait for Kalshi to reprice after spot moves. When Kalshi doesn't reprice within 3 seconds, orders timeout.

## Detailed Analysis

### Paper Trading (Baseline)
- **Fill rate:** 100% (instant fills)
- **Reason:** Paper mode simulates fills without checking real orderbook
- **Entry price:** Uses calculated "fair value" from spot move
- **Result:** Misleading performance metrics

### Live Trading (Reality Check)

**Market:** `KXBTC15M-26FEB282330-30`
- **Strike:** ~$67,300 (BTC must be ABOVE this for YES to win)
- **Trading period:** 20:15-20:16 PT (14 minutes before expiry)
- **BTC spot:** $67,150-67,285 (below strike)
- **Current BTC:** $67,203 (still below strike, 3 min until expiry)

**Order Sequence:**

```
20:15:50 - SIGNAL: Buy NO @ 60¢ (BTC dropped $22)
           POST order @ 61¢ (60 + 1¢ slippage)
           Poll status for 3 seconds... 200 OK, 200 OK, 200 OK
           TIMEOUT - Cancel order

20:15:53 - SIGNAL: Buy NO @ 60¢
           POST order @ 61¢
           Poll status for 3 seconds...
           TIMEOUT - Cancel order

[Repeats 8 more times with NO @ 60¢]

20:16:37 - SIGNAL: Buy YES @ 41¢ (BTC rose $20)
           POST order @ 42¢
           ✅ FILLED @ 42¢!
```

**Why orders didn't fill:**

1. **Strategy calculates:** "BTC just dropped $22, Kalshi should reprice NO from current price to ~60¢"
2. **Strategy places:** Limit order to buy NO @ 61¢ (60 + 1¢ buffer)
3. **Reality:** Kalshi orderbook still has NO @ 71¢+ (hasn't repriced yet)
4. **Outcome:** Order sits in book waiting for someone to sell @ 61¢, but no one does
5. **After 3 seconds:** Order cancelled (timeout)

### Current Orderbook State

```
YES (BTC > $67,300):
  8¢ x 890    ← Best ask (cheapest to buy YES)
  10¢ x 206
  12¢ x 1925

NO (BTC ≤ $67,300):
  71¢ x 300   ← Best ask (cheapest to buy NO)
  74¢ x 3985
  75¢ x 15013
```

**Why is YES so cheap now?**
- Market expires in 3 minutes
- BTC is at $67,203 (need to jump $97 to hit $67,300 strike)
- Probability of BTC jumping $97 in 3 min = ~8%
- Therefore YES = 8¢, NO = 71¢+

## The Core Problem

**Paper trading assumption:** "If spot moves $20, Kalshi will instantly reprice"

**Live trading reality:** "Kalshi reprices when traders trade, not when spot moves"

The strategy is trying to **front-run the repricing** by placing limit orders at "fair value" before Kalshi reaches that price. But if Kalshi doesn't reprice within 3 seconds, the opportunity is missed.

## This Is NOT...

✅ **Not a bug** - Limit orders working as designed
✅ **Not a liquidity issue** - Orderbook has 15,000+ contracts available
✅ **Not an execution issue** - Orders are placed correctly
✅ **Not the asyncio error** - That's cosmetic and recovers

## This IS...

❌ **A strategy design mismatch:**
- **Paper mode:** Assumes instant fills at calculated price
- **Live mode:** Requires market to move to your price within 3s

❌ **A timing issue:**
- Kalshi reprices slower than the strategy expects
- 3-second timeout is too short to wait for repricing

## Solutions (Pick One or Combine)

### Option 1: Use Market Orders (Aggressive)
**Change:** Place orders at current best ask (guaranteed fill)

```yaml
# In config:
use_market_orders: true  # NEW
```

**Implementation:**
```python
# In _place_entry:
if self._config.use_market_orders:
    # Get current best ask from orderbook
    orderbook = self._get_orderbook(signal.ticker)
    limit_price = orderbook.best_ask + 1  # Cross the spread
else:
    # Current behavior (use calculated fair value)
    limit_price = signal.entry_price_cents + slippage_buffer
```

**Pros:**
- ✅ 100% fill rate
- ✅ Immediate execution

**Cons:**
- ❌ Worse entry prices (pay the ask instead of fair value)
- ❌ Could lose money if Kalshi hasn't repriced yet

---

### Option 2: Increase Timeout (Patient)
**Change:** Give Kalshi more time to reprice

```yaml
# In config:
fill_timeout_sec: 10.0  # was 3.0
```

**Pros:**
- ✅ Simple one-line config change
- ✅ Allows time for Kalshi to reprice after spot moves

**Cons:**
- ❌ Still might timeout if Kalshi doesn't reprice
- ❌ Capital tied up longer in unfilled orders

---

### Option 3: Dynamic Repricing (Walk the Book)
**Change:** If order doesn't fill after 1s, reprice toward market

```python
def _wait_for_fill_with_repricing(self, order_id, ticker, initial_price):
    """Wait for fill, repricing if necessary."""
    deadline = time.time() + self._config.fill_timeout_sec
    reprice_at = time.time() + 1.0  # Reprice after 1s

    while time.time() < deadline:
        order = self._check_order_status(order_id)
        if order.filled:
            return True

        # Reprice if not filled after 1s
        if time.time() > reprice_at and not reprice_at == -1:
            orderbook = self._get_orderbook(ticker)
            new_price = min(orderbook.best_ask + 1, initial_price + 5)
            self._update_order_price(order_id, new_price)
            reprice_at = -1  # Only reprice once

        time.sleep(0.2)
    return False
```

**Pros:**
- ✅ Balances fill rate vs price
- ✅ Gets fair value if available, crosses spread if needed

**Cons:**
- ❌ More complex
- ❌ Requires orderbook polling
- ❌ Might need Kalshi API support for order amendments

---

### Option 4: Pre-Flight Orderbook Check (Realistic Pricing)
**Change:** Check current orderbook BEFORE calculating entry price

```python
def _place_entry(self, signal: ScalpSignal):
    # Get current orderbook state
    orderbook = self._get_orderbook(signal.ticker)

    # Adjust entry price based on reality
    if signal.side == "yes":
        current_ask = orderbook.yes_ask
        # Don't place order more than 5¢ below current ask
        limit_price = max(
            signal.entry_price_cents,  # Fair value
            current_ask - 5,            # Not too far from market
        )
    else:
        current_ask = orderbook.no_ask
        limit_price = max(signal.entry_price_cents, current_ask - 5)

    # Place order at adjusted price
    ...
```

**Pros:**
- ✅ Realistic pricing based on current market
- ✅ Still tries for good price, but won't miss by 10¢
- ✅ Higher fill rate while avoiding overpaying

**Cons:**
- ❌ Extra API call before each order (latency)
- ❌ Still might not fill if market is moving fast

---

## Recommended Solution

**Combine Option 2 + Option 4:**

1. **Increase timeout to 5-10 seconds**
   - Gives Kalshi time to reprice
   - More realistic for latency arb strategy

2. **Add pre-flight orderbook check**
   - Prevents placing orders 10¢+ away from market
   - Adjusts fair value calculation to reality
   - Adds ~100-200ms latency but prevents wasted orders

**Config changes:**
```yaml
fill_timeout_sec: 8.0  # was 3.0
max_price_deviation_cents: 5  # Don't place orders >5¢ from current ask
```

**Code changes:**
```python
# In _place_entry(), before placing order:
orderbook = await self._client.get_orderbook(signal.ticker)
current_ask = orderbook.yes_ask if signal.side == "yes" else orderbook.no_ask

# Adjust limit price to be realistic
fair_value_price = signal.entry_price_cents + self._config.slippage_buffer_cents
if abs(fair_value_price - current_ask) > self._config.max_price_deviation_cents:
    logger.warning(
        "Fair value %d¢ is %d¢ away from market ask %d¢ - adjusting to market",
        fair_value_price, abs(fair_value_price - current_ask), current_ask
    )
    limit_price = current_ask - 1  # Just inside the ask
else:
    limit_price = fair_value_price  # Use calculated fair value
```

## Expected Impact

**Before (Live):**
- Fill rate: 10%
- Orders: 10 placed, 1 filled
- Strategy: Unusable

**After (With Fix):**
- Fill rate: 70-90% (realistic for latency arb)
- Orders: Some will still miss if Kalshi doesn't reprice
- Strategy: Viable but need to track slippage vs paper trading

## Next Steps

1. ✅ **Stop live trading** (done)
2. ⬜ **Implement combined solution** (Option 2 + 4)
3. ⬜ **Backtest with realistic fills** (add fill simulation to backtest adapter)
4. ⬜ **Paper trade** with new settings (verify fill rate improves)
5. ⬜ **Live trade** small size to validate
6. ⬜ **Scale up** once fill rate >70%

## Key Takeaway

**The dry run vs live performance gap is NOT due to:**
- Asyncio bugs
- Order manager issues
- Liquidity problems

**It's due to:**
- **Unrealistic fill assumptions in paper mode**
- **Limit orders placed too far from current market**
- **Insufficient timeout for Kalshi to reprice**

The strategy needs to adapt to **market reality**: Kalshi doesn't instantly reprice when spot moves - it reprices when traders trade. Your limit orders need to either:
1. Wait longer for the market to come to you (increase timeout)
2. Walk toward the market if it doesn't come (dynamic repricing)
3. Accept current market prices (market orders or realistic limits)
