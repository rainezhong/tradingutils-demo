# OMS Critical Issues Analysis - March 1, 2026

## Session Context
During live crypto-scalp trading, we discovered the OrderType import bug which left a stranded position. User identified a critical architectural issue: **stale entry orders stay resting on exchange and can fill when we no longer want them**.

## Critical Issues (Must Fix)

### 1. **STALE ENTRY ORDERS - No Order TTL** ⚠️ CRITICAL
**User-identified issue**

**Problem:**
- Entry orders stay resting on exchange indefinitely
- If market moves away then returns hours/days later, order fills when signal is stale
- Example: Submit BUY at 65¢ → market goes to 80¢ → 2 days later market drops to 65¢ → order fills (but signal expired)

**Current code:**
```python
# Line 156-166: Tracks order but no TTL
tracked = TrackedOrder(
    order_id=order_id,
    ticker=request.ticker,
    ...
    status=OrderStatus.SUBMITTED,
)
self._orders[order_id] = tracked  # No expiry time set
```

**Impact:**
- Accumulates unwanted positions
- Violates max_open_positions limit
- Trades on stale signals (low edge, high risk)

**Fix needed:**
- Add `expiry_time: Optional[datetime]` to `TrackedOrder`
- Add `max_order_age_seconds` to config (e.g., 30s for scalp, 300s for other strategies)
- Periodic sweep to cancel orders older than threshold
- Cancel on startup (all resting orders from previous runs)

**Files:**
- `core/order_manager/order_manager_types.py` (add expiry_time field)
- `core/order_manager/kalshi_order_manager.py` (add TTL enforcement)

---

### 2. **NO STARTUP CLEANUP** ⚠️ CRITICAL

**Problem:**
- When strategy restarts, old resting orders from previous run stay on exchange
- No automatic cancellation of stale orders on OMS initialization
- Led to stranded position issue (strategy didn't know about existing position)

**Current code:**
```python
# Line 52-70: __init__ doesn't sync with exchange
def __init__(self, exchange_client: Any):
    self._client = exchange_client
    self._orders: Dict[str, TrackedOrder] = {}  # Empty on restart!
    self._fills: List[Fill] = []
    self._positions: Dict[Tuple[str, Side], int] = {}  # No recovery!
```

**Impact:**
- Stranded positions not tracked
- Duplicate position accumulation
- Orders from crashed runs stay on exchange

**Fix needed:**
- Add `async def initialize()` method
- Call `cancel_all_orders()` on startup
- Call `get_fills()` to recover positions
- Call `sync_orders()` to hydrate `_orders` dict

**Files:**
- `core/order_manager/kalshi_order_manager.py` (add initialize method)
- Strategy orchestrators (call initialize on startup)

---

### 3. **NO POSITION RECOVERY ON STARTUP** ⚠️ CRITICAL

**Problem:**
- OMS starts with empty `_positions` dict on restart
- Doesn't query exchange for existing positions
- Leads to position tracking desync (exactly what we fixed in refactor!)

**Current code:**
```python
# Line 63: Starts empty
self._positions: Dict[Tuple[str, Side], int] = {}
```

**Impact:**
- Duplicate position accumulation
- Opposite side protection doesn't work after restart
- Max position limits not enforced

**Fix needed:**
- In `initialize()`: call `get_fills(limit=200)` to recover recent fills
- Reconstruct `_positions` from fills
- Alternative: add `get_positions()` to exchange client (if Kalshi API supports it)

**Files:**
- `core/order_manager/kalshi_order_manager.py` (position recovery in initialize)

---

### 4. **CANCEL DOESN'T VALIDATE ACTUAL CANCELLATION** ⚠️ HIGH

**Problem:**
- `cancel_order()` returns `True` after API call succeeds (line 231)
- Doesn't verify order actually canceled (could be already filled)
- Race condition: order fills between cancel request and cancel confirmation

**Current code:**
```python
# Lines 220-231
await self._client.cancel_order(order_id)

# Update tracked order - ASSUMES cancellation succeeded!
if order_id in self._orders:
    self._orders[order_id].status = OrderStatus.CANCELED
    ...
logger.info(f"Order canceled: {order_id}")
return True  # But was it really canceled?
```

**Impact:**
- OMS thinks order canceled but it filled
- Position tracking desync
- Missed fill callbacks

**Fix needed:**
- After cancel API call, poll `get_order_status()` to verify
- If status is FILLED, trigger fill callback
- Only return True if status is actually CANCELED

**Files:**
- `core/order_manager/kalshi_order_manager.py` (lines 204-246)

---

### 5. **PARTIAL FILLS NOT HANDLED BEFORE CANCEL** ⚠️ HIGH

**Problem:**
- When canceling, doesn't check if order partially filled first
- `filled_quantity > 0` ignored before marking as CANCELED
- Loses partial fill position tracking

**Current code:**
```python
# Line 224: Marks as CANCELED without checking filled_quantity
self._orders[order_id].status = OrderStatus.CANCELED
```

**Impact:**
- Partial fills not reflected in `_positions`
- Position tracking desync
- Risk limit violations

**Fix needed:**
- Before marking CANCELED, check `order.filled_quantity > 0`
- If partial fill, set status to PARTIALLY_FILLED then CANCELED
- Ensure fill callback triggered for partial quantity

**Files:**
- `core/order_manager/kalshi_order_manager.py` (cancel_order method)

---

### 6. **NO CONCURRENT BUY CHECK** ⚠️ MEDIUM

**Problem:**
- Lines 111-125: Only validates concurrent SELL orders
- Doesn't prevent multiple pending BUY orders on same ticker+side
- Can accumulate position beyond `max_open_positions`

**Current code:**
```python
# Lines 113-125: Only checks SELL
if request.action == Action.SELL:
    pending_sells = [o for o in self.get_open_orders() ...]
    if pending_sells:
        raise ValueError(...)
# No check for BUY!
```

**Impact:**
- Position accumulation beyond limits
- Capital overuse
- Violates position sizing rules

**Fix needed:**
- Add similar check for BUY orders
- Configurable: some strategies may want to scale in (multiple BUY orders)
- Add `allow_concurrent_buys: bool` to config

**Files:**
- `core/order_manager/kalshi_order_manager.py` (lines 111-125)
- Strategy configs (add allow_concurrent_buys flag)

---

### 7. **GET_FILLS LIMITED TO 100, NO PAGINATION** ⚠️ MEDIUM

**Problem:**
- Line 302: `get_fills(limit=100)` hardcoded
- If >100 fills happen between polls, misses fills
- No cursor/pagination support

**Current code:**
```python
# Line 302
response_fills = await self._client.get_fills(ticker=None, limit=100)
```

**Impact:**
- Missed fills in high-frequency trading
- Position tracking desync
- Lost fill callbacks

**Fix needed:**
- Add pagination loop to fetch all fills since last known fill
- Track `last_fill_timestamp` to query incrementally
- Alternative: use WebSocket fill stream (already implemented in `fill_notifier.py`!)

**Files:**
- `core/order_manager/kalshi_order_manager.py` (get_fills method)
- Consider integrating `src/oms/fill_notifier.py` for real-time fills

---

### 8. **POSITION EXPIRY NOT HANDLED** ⚠️ LOW

**Problem:**
- Positions stay in `_positions` dict forever
- Doesn't know about market close times
- Expired positions never cleaned up

**Current code:**
```python
# Line 435-443: Removes position only when qty <= 0
if new_pos <= 0:
    self._positions.pop(key, None)
# No market expiry check
```

**Impact:**
- Memory leak (minor)
- Stale position data
- Confusing position reports

**Fix needed:**
- Add `market_close_time` to position tracking
- Periodic sweep to remove expired positions
- Call `clear_position()` on market close

**Files:**
- `core/order_manager/kalshi_order_manager.py` (position tracking)
- Need market metadata (close times) from exchange client

---

### 9. **NO PERIODIC ORDER AGE SWEEPER** ⚠️ HIGH

**Problem:**
- No background task to cancel old orders
- Relies on manual cleanup
- Orders can sit on exchange for days

**Current code:**
- No periodic cleanup mechanism

**Impact:**
- Stale order accumulation
- Capital tied up in old orders
- Risk of unintended fills

**Fix needed:**
- Add background thread/asyncio task
- Check order age every 30s
- Cancel orders older than `max_order_age_seconds`
- Log canceled stale orders

**Files:**
- `core/order_manager/kalshi_order_manager.py` (new sweeper task)

---

### 10. **LIMITED CALLBACK SYSTEM** ⚠️ LOW

**Problem:**
- Only has `on_fill` and `on_cancel` callbacks
- No callbacks for:
  - Order going stale
  - Partial fills
  - Order rejection
  - Order expiry

**Current code:**
```python
# Lines 69-70: Only 2 callbacks
self._on_fill: Optional[Callable] = None
self._on_cancel: Optional[Callable] = None
```

**Impact:**
- Strategies can't react to edge cases
- Limited observability
- Manual polling needed

**Fix needed:**
- Add callbacks: `on_stale`, `on_partial_fill`, `on_rejected`, `on_expired`
- Add `on_order_aged` for TTL warnings

**Files:**
- `core/order_manager/order_manager_types.py` (callback type aliases)
- `core/order_manager/kalshi_order_manager.py` (callback registration)

---

## Medium Priority Issues

### 11. **INEFFICIENT FILL POLLING**

**Problem:**
- Strategies poll `get_fills()` every N seconds
- Wastes API quota
- Delays fill detection by polling interval

**Current approach:**
- REST API polling in loops

**Better approach:**
- Use WebSocket fill stream (already implemented in `src/oms/fill_notifier.py`!)
- Real-time fill callbacks
- Zero polling overhead

**Fix needed:**
- Integrate `FillNotifier` into `KalshiOrderManager`
- Subscribe to fill stream on `initialize()`
- Trigger `on_fill` callback from WebSocket messages

**Files:**
- `src/oms/fill_notifier.py` (existing implementation)
- `core/order_manager/kalshi_order_manager.py` (integrate WebSocket)

---

### 12. **NO ORDER REJECTION HANDLING**

**Problem:**
- When exchange rejects order, OMS doesn't update status
- No callback triggered
- Strategies don't know order failed

**Current code:**
```python
# Lines 174-176: Raises exception, but doesn't update order status
except Exception as e:
    logger.error(f"Order submission failed: {e}")
    raise RuntimeError(f"Order submission failed: {e}")
# Order not added to _orders dict!
```

**Impact:**
- Lost order tracking
- No rejection callback
- Strategies retry blindly

**Fix needed:**
- Add order to `_orders` with status REJECTED before raising
- Trigger `on_rejected` callback
- Return `OrderResult` instead of raising (more graceful)

**Files:**
- `core/order_manager/kalshi_order_manager.py` (submit_order method)

---

## Implementation Priority

### Phase 1: Critical (Fix Immediately)
1. ✅ **Startup cleanup** - Cancel all orders on initialize
2. ✅ **Position recovery** - Sync positions from fills on startup
3. ✅ **Order TTL** - Add expiry_time, periodic sweeper
4. ✅ **Cancel validation** - Verify actual cancellation

### Phase 2: High (Fix This Week)
5. ✅ **Partial fill handling** - Check filled_quantity before cancel
6. ✅ **Concurrent BUY check** - Prevent accumulation
7. ✅ **WebSocket fills** - Integrate FillNotifier

### Phase 3: Medium (Nice to Have)
8. ⏸️ **Fill pagination** - Handle >100 fills
9. ⏸️ **Order rejection** - Better error handling
10. ⏸️ **Enhanced callbacks** - Stale, partial, rejection events

### Phase 4: Low (Future)
11. ⏸️ **Position expiry** - Market close cleanup
12. ⏸️ **Order metadata** - Strategy name, signal ID, tags

---

## Recommended Fix for Stale Orders (User's Issue)

**Immediate mitigation:**
```python
async def initialize(self):
    """Initialize OMS - call this on startup!"""
    # 1. Cancel ALL resting orders (clean slate)
    logger.info("Canceling all resting orders from previous runs...")
    canceled = await self.cancel_all_orders()
    logger.info(f"Canceled {canceled} stale orders")

    # 2. Recover positions from recent fills
    logger.info("Recovering positions from recent fills...")
    fills = await self.get_fills()
    logger.info(f"Recovered {len(self._positions)} positions from {len(fills)} fills")
```

**Long-term fix:**
```python
# Add to TrackedOrder
@dataclass
class TrackedOrder:
    ...
    expiry_time: Optional[datetime] = None  # NEW
    max_age_seconds: float = 60.0  # NEW - configurable

# Add sweeper task
async def _order_age_sweeper(self):
    """Background task to cancel old orders."""
    while self._running:
        await asyncio.sleep(30)  # Check every 30s

        now = datetime.now()
        for order_id, order in list(self._orders.items()):
            if order.status not in (OrderStatus.RESTING, OrderStatus.PENDING):
                continue

            age = (now - order.created_at).total_seconds()
            if age > order.max_age_seconds:
                logger.warning(f"Order {order_id} aged out ({age:.1f}s > {order.max_age_seconds}s)")
                await self.cancel_order(order_id)
```

---

## Files to Modify

1. **core/order_manager/order_manager_types.py**
   - Add `expiry_time`, `max_age_seconds` to TrackedOrder
   - Add callback type aliases

2. **core/order_manager/kalshi_order_manager.py**
   - Add `initialize()` method
   - Add `_order_age_sweeper()` background task
   - Fix `cancel_order()` validation
   - Add concurrent BUY check
   - Integrate WebSocket fills

3. **strategies/crypto_scalp/orchestrator.py**
   - Call `self._om.initialize()` on startup
   - Set `max_age_seconds` based on config

4. **strategies/configs/crypto_scalp_live.yaml**
   - Add `max_entry_order_age_seconds: 30`

---

## Testing Plan

1. **Unit tests:**
   - `test_order_ttl.py` - verify orders canceled after age threshold
   - `test_startup_cleanup.py` - verify all resting orders canceled on init
   - `test_position_recovery.py` - verify positions reconstructed from fills
   - `test_cancel_validation.py` - verify cancel doesn't lie about success

2. **Integration tests:**
   - Start strategy → stop → start again → verify no duplicate orders
   - Submit order → wait 2x TTL → verify order canceled
   - Submit order → fill → restart → verify position recovered

3. **Live testing:**
   - Run crypto-scalp for 1 hour
   - Manually check Kalshi for stale orders (should be 0)
   - Restart strategy mid-run → verify positions sync correctly

---

## Summary

**Root cause of stale orders:**
- OMS has no concept of order age or TTL
- No cleanup on startup
- Orders live forever until manually canceled

**Impact:**
- Fills on stale signals (low edge)
- Position accumulation beyond limits
- Capital inefficiency

**Solution:**
- Add order TTL with periodic sweeper
- Cancel all orders on startup
- Recover positions from fills
- Validate cancellations actually succeed

**Estimated effort:**
- Phase 1 (critical): 4-6 hours
- Phase 2 (high): 4-6 hours
- Phase 3 (medium): 8-10 hours
- Total: ~20 hours for complete OMS hardening
