# P&L Logging Discrepancy Analysis - $6 Actual Loss vs $0.04 Logged

## Date: 2026-03-02

## Critical Finding

**Actual account change**: $46.00 → $40.00 = **-$6.00 loss**
**Logged P&L**: -4¢ = **-$0.04**
**Discrepancy**: **$5.96 UNACCOUNTED FOR** 🚨

---

## Root Cause Analysis

### Issue #6: EXIT PRICE LOGGED ≠ ACTUAL FILL PRICE 🚨 CRITICAL

**Location**: `strategies/crypto_scalp/orchestrator.py:2053`

**The Bug**:
```python
# Line 2050-2053 in _place_exit()
exit_order_id = self._run_async(self._om.submit_order(request))

actual_exit_price = limit_price if limit_price else exit_price
self._record_exit(ticker, position, actual_exit_price, exit_order_id)
```

**What this does**:
1. Submit exit order at `limit_price` (e.g., 25¢)
2. **IMMEDIATELY record exit** using the **LIMIT PRICE** (25¢)
3. **NO CONFIRMATION** that order actually filled at 25¢
4. If order fills later at worse price → **NOT LOGGED**

**Example from March 1 session**:
```
Entry: 29¢ (1 contract)
Exit order submitted: 25¢ limit
Logged P&L: 29¢ - 25¢ = +4¢ (WRONG!)

Actual fill price: UNKNOWN (could be 0¢, 1¢, 23¢, etc.)
Actual P&L: 29¢ - ??? = UNKNOWN
```

---

### Issue #7: NO ACTUAL FILL PRICE RETRIEVAL 🚨 CRITICAL

**Location**: `strategies/crypto_scalp/orchestrator.py:2160-2195` (`_record_exit`)

**The Bug**:
```python
def _record_exit(
    self,
    ticker: str,
    position: ScalpPosition,
    exit_price_cents: int,  # ← This is the LIMIT price, not FILL price!
    exit_order_id: str,
) -> None:
    """Record exit and update stats."""
    now = time.time()

    # Using LIMIT PRICE for P&L calculation (WRONG!)
    gross_pnl_per_contract = exit_price_cents - position.entry_price_cents

    fee_per_contract = 0
    if gross_pnl_per_contract > 0:
        fee_per_contract = max(1, int(gross_pnl_per_contract * KALSHI_FEE_RATE))

    net_pnl_per_contract = gross_pnl_per_contract - fee_per_contract
    total_pnl = net_pnl_per_contract * position.size

    # Saving WRONG P&L to trade log
    position.pnl_cents = total_pnl

    self._stats.trades_exited += 1
    self._stats.total_pnl_cents += total_pnl  # ← WRONG P&L added to stats!
```

**What should happen**:
```python
# AFTER submitting exit order:
exit_order_id = self._run_async(self._om.submit_order(request))

# WAIT for fill
filled = self._run_async(
    self._wait_for_fill_om(exit_order_id, ticker, timeout=5.0)
)

if filled:
    # RETRIEVE ACTUAL FILL PRICE from OMS
    fills = await self._om.get_fills(exit_order_id)
    if fills:
        actual_fill_price = fills[0].price_cents  # ← ACTUAL FILL PRICE
        self._record_exit(ticker, position, actual_fill_price, exit_order_id)
    else:
        logger.error("Exit filled but no fill records found!")
else:
    logger.error("Exit order did not fill!")
    # Keep position in tracking, retry
```

---

## Possible Explanations for $6 Loss

### Scenario 1: Exit Filled at Much Worse Price 🎯 MOST LIKELY

**What happened**:
```
Entry: 29¢ (1 contract) = $0.29 spent
Exit order: 25¢ limit submitted
Market crashed rapidly
Order filled at: 1¢ (or worse)

Actual P&L: 1¢ - 29¢ = -28¢ per contract

If position size was accidentally 20+ contracts:
-28¢ × 21 contracts = -$5.88 ≈ -$6
```

**Evidence**:
- BTC market near expiry can crash to 0¢ in seconds
- Stop-loss protection was set to 15¢ but may not have triggered
- Exit order at 25¢ might not have filled if market gapped down
- Order could have filled at settlement (0¢ if market closed below strike)

---

### Scenario 2: Multiple Positions Not Tracked

**What happened**:
```
Strategy tracking: 1 position
Actual Kalshi account: Multiple positions due to tracking bugs

Example:
- Trade #1: Entry filled but not tracked → position stranded
- Trade #2: Entry filled but not tracked → position stranded
- Trade #3: Entry filled and tracked → logged as only position
- Trade #4: Entry filled but not tracked → position stranded
- Trade #5: Entry attempt canceled

Actual: 4 positions entered @ 29¢ each = $1.16 spent
All 4 exited at 0¢ (expiry or crash) = -$1.16 loss

If repeated across 5 markets:
-$1.16 × 5 = -$5.80 ≈ -$6
```

**Evidence**:
- 4 entry attempts "failed" but maybe some actually filled after cancel attempt
- Cancel order bug (Fix #1 in STALE_ORDER_BUG_FIXED.md) - orders not actually canceled
- Position tracking desync between strategy and exchange

---

### Scenario 3: Settlement at Expiry

**What happened**:
```
Position opened: YES @ 29¢
Exit order: 25¢ limit (didn't fill)
Market closed: BTC below strike
Settlement: 0¢ (YES contracts worthless)

Loss: 29¢ - 0¢ = -29¢ per contract

If 20 contracts somehow entered:
-29¢ × 20 = -$5.80 ≈ -$6
```

**Evidence**:
- Markets near expiry (3-15min window)
- Exit order may not have filled before expiry
- Settlement at 0¢ would cause full loss of entry cost

---

### Scenario 4: Fees Not Accounted For

**Location**: Line 2172-2174

```python
fee_per_contract = 0
if gross_pnl_per_contract > 0:
    fee_per_contract = max(1, int(gross_pnl_per_contract * KALSHI_FEE_RATE))
```

**Bug**: Fees only calculated on WINNING trades!

**What should happen**:
```python
# Fees charged on BOTH sides of trade (entry + exit)
entry_fee = max(1, int(position.entry_price_cents * KALSHI_FEE_RATE))
exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))
total_fees = entry_fee + exit_fee

net_pnl_per_contract = gross_pnl_per_contract - total_fees
```

**Impact**:
```
If 1 contract @ 29¢ entry:
Entry fee: 29¢ × 7% = 2¢
Exit fee: 25¢ × 7% = 2¢
Total fees: 4¢ (NOT LOGGED!)

Logged P&L: -4¢
Actual P&L: -4¢ - 4¢ = -8¢

Still doesn't explain $6 loss...
```

---

## Investigation Steps

### Step 1: Check Kalshi Account for Actual Fills

```bash
# Get all fills from March 1 session
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.elections.kalshi.com/trade-api/v2/portfolio/fills?min_ts=2026-03-01T21:30:00Z&max_ts=2026-03-01T22:15:00Z"
```

**Look for**:
- Number of fills (should match logged trades)
- Actual fill prices (compare to logged prices)
- Position sizes (should all be 1 contract)
- Tickers (should match logged tickers)

---

### Step 2: Check for Resting/Executed Orders

```bash
# Check for any resting orders (unfilled)
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders?status=resting"

# Check for executed orders
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders?status=executed"
```

**Look for**:
- Orders submitted but not logged
- Orders filled after strategy stopped
- Duplicate orders (multiple entries for same market)

---

### Step 3: Check Settlement History

```bash
# Get market settlements
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.elections.kalshi.com/trade-api/v2/portfolio/settlements?min_ts=2026-03-01T21:30:00Z"
```

**Look for**:
- Markets that settled at 0¢ (YES contracts worthless)
- Positions held to expiry (not exited in time)

---

### Step 4: Analyze Account Balance History

```bash
# Get balance changes
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.elections.kalshi.com/trade-api/v2/portfolio/balance"
```

**Look for**:
- All transactions during session
- Balance snapshots (before/after trades)
- Hidden fees or charges

---

## Additional Logging Bugs Found

### Issue #8: Entry Fee Not Logged

**Location**: `orchestrator.py:1385-1393`

```python
logger.info(
    "LIMIT FILL [%s]: %s %s %d @ %dc (order %s)",
    signal.source,
    signal.side.upper(),
    signal.ticker,
    position.size,
    position.entry_price_cents,
    order_id,
)
```

**Missing**:
- No fee calculation on entry
- No gross vs net cost distinction
- No total capital deployed tracking

**Fix**:
```python
entry_fee = max(1, int(position.entry_price_cents * KALSHI_FEE_RATE))
gross_cost = position.entry_price_cents * position.size
net_cost = (position.entry_price_cents + entry_fee) * position.size

logger.info(
    "LIMIT FILL [%s]: %s %s %d @ %dc (fee=%dc, cost=%dc) | order %s",
    signal.source,
    signal.side.upper(),
    signal.ticker,
    position.size,
    position.entry_price_cents,
    entry_fee,
    net_cost,
    order_id,
)
```

---

### Issue #9: No Real-Time Account Balance Tracking

**Location**: Dashboard (line 2210-2280)

**Missing**:
- No query for actual Kalshi account balance
- No comparison to internal P&L tracking
- No drift detection (internal vs actual balance)

**Fix**: Add to dashboard:
```python
# Query actual balance from Kalshi
actual_balance = await self._client.get_balance()

# Compare to internal tracking
expected_balance = INITIAL_BALANCE + (self._stats.total_pnl_cents / 100.0)
drift = actual_balance - expected_balance

logger.info(
    "BALANCE: actual=$%.2f | expected=$%.2f | drift=$%.2f %s",
    actual_balance,
    expected_balance,
    drift,
    "⚠️ MISMATCH!" if abs(drift) > 0.10 else "✓"
)
```

---

### Issue #10: No Position Reconciliation

**Location**: Startup (no reconciliation logic exists)

**Missing**:
- No check for open positions on Kalshi at startup
- No comparison to internal tracking
- No recovery from stranded positions

**Fix**: Add to `run()`:
```python
async def run(self):
    """Main strategy loop."""
    # ... existing startup ...

    # Reconcile positions with exchange
    logger.info("Reconciling positions with Kalshi...")
    kalshi_positions = await self._client.get_positions()

    if kalshi_positions:
        logger.warning(f"Found {len(kalshi_positions)} open positions on Kalshi!")
        for pos in kalshi_positions:
            logger.warning(
                f"  - {pos.ticker} {pos.side}: {pos.quantity} @ {pos.avg_price}¢"
            )

            # Add to internal tracking if missing
            ticker = pos.ticker
            if ticker not in self._positions:
                logger.warning(f"  → NOT IN INTERNAL TRACKING! Adding now...")
                # ... create ScalpPosition from Kalshi position ...
                self._positions[ticker] = recovered_position

    logger.info("Position reconciliation complete")
```

---

## Updated Critical Issues List

| # | Issue | Impact | Severity |
|---|-------|--------|----------|
| 1 | Exit fills not confirmed | Stranded positions | 🚨 CRITICAL |
| 2 | Orderbook WebSocket broken | 80% entry failure | 🚨 CRITICAL |
| 3 | OMS WebSocket not initialized | Delayed fill detection | ⚠️ HIGH |
| 4 | Event loop architecture flaw | Can't fetch snapshots | 🏗️ ARCHITECTURAL |
| 5 | No WebSocket reconnection | Permanent failure | ⚠️ MEDIUM |
| **6** | **Exit price = limit price, not fill price** | **P&L completely wrong** | **🚨 CRITICAL** |
| **7** | **No actual fill price retrieval** | **Can't verify real losses** | **🚨 CRITICAL** |
| **8** | **Entry fees not logged** | **P&L understated** | **⚠️ HIGH** |
| **9** | **No real-time balance tracking** | **Can't detect drift** | **⚠️ HIGH** |
| **10** | **No position reconciliation** | **Stranded positions undetected** | **🚨 CRITICAL** |

---

## Immediate Actions Required

### BEFORE investigating further:

1. ✅ **Query Kalshi API for actual fills** (Investigation Step 1)
   - Get ground truth of what actually happened
   - Compare to logged trades
   - Identify missing/incorrect data

2. ✅ **Check for resting/stranded orders** (Investigation Step 2)
   - Cancel any unfilled orders
   - Close any open positions
   - Prevent further losses

3. ✅ **Check settlement history** (Investigation Step 3)
   - See if positions settled at 0¢
   - Explains large losses

### BEFORE resuming trading:

4. ✅ **Fix Issue #6** - Record actual fill price, not limit price
   - Wait for fill confirmation
   - Retrieve fill price from OMS
   - Calculate P&L from ACTUAL fills

5. ✅ **Fix Issue #9** - Add balance tracking to dashboard
   - Query Kalshi balance every 30s
   - Compare to internal P&L
   - Alert on drift >$0.10

6. ✅ **Fix Issue #10** - Add position reconciliation
   - Check Kalshi positions at startup
   - Sync with internal tracking
   - Log any discrepancies

---

## Risk Assessment

### Current State

**If $6 loss is from**:
- **Exit at worse price**: Exit fill confirmation (Fix #1) solves it
- **Multiple positions**: Position reconciliation (Fix #10) detects it
- **Settlement losses**: Normal risk, but should be logged correctly
- **Fees**: Fee tracking (Fix #8) makes it visible

### Worst Case

**If logging is THIS broken**, what else could be wrong?
- Entry fills at worse prices than logged?
- Multiple positions per market?
- Orders filling after strategy stopped?
- Settlements not being tracked?

**Recommendation**: DO NOT trade until ALL 10 issues are fixed and verified with actual Kalshi API queries.

---

## Testing Plan

### After fixes, validate:

1. **P&L accuracy test**:
   - Place 1 test trade in paper mode
   - Record: entry limit, entry fill, exit limit, exit fill
   - Calculate: expected P&L (with fees)
   - Verify: logged P&L matches expected
   - Query Kalshi API to confirm

2. **Balance drift test**:
   - Record starting balance from Kalshi API
   - Run 5 trades
   - Query ending balance from Kalshi API
   - Compare: actual Δbalance vs logged total_pnl_cents
   - Verify: drift <$0.01

3. **Position reconciliation test**:
   - Manually create position on Kalshi (outside strategy)
   - Start strategy
   - Verify: position detected and logged
   - Verify: position added to internal tracking

---

## Conclusion

The **$5.96 discrepancy** is a smoking gun that proves P&L logging is fundamentally broken. The strategy is recording **LIMIT PRICES** as if they were **FILL PRICES**, which means:

- **All logged P&L is fiction**
- **Cannot trust any reported metrics**
- **Actual losses could be much larger than reported**

This is even more critical than the exit fill confirmation bug, because it affects:
- Live trading decisions (based on fake P&L)
- Risk management (based on fake loss totals)
- Strategy evaluation (based on fake performance)

**DO NOT RESUME TRADING** until:
1. Actual fills are retrieved from Kalshi API (Steps 1-4)
2. Root cause of $6 loss identified
3. ALL 10 logging/tracking issues fixed (not just #1-5)
4. P&L accuracy validated with test trades
