# Code Analysis: Bug Trace Through Execution Flow
## March 3, 2026

This document traces the execution flow of the crypto scalp strategy to identify the root causes of the observed failures (98% exit failure rate, 56 duplicate entries, $44 loss).

---

## Architecture Overview

### Component Hierarchy
```
CryptoScalpStrategy (orchestrator.py)
├── I_ExchangeClient (KalshiExchangeClient)
├── KalshiOrderManager (order_manager/kalshi_order_manager.py)
├── OrderBookManager (market/orderbook_manager.py)
├── ScalpDetector (detector.py)
└── WebSocket Feeds
    ├── Kalshi WebSocket (orderbook + fills)
    ├── Binance WebSocket (BTC trades)
    └── Coinbase WebSocket (BTC trades)
```

### Thread Architecture
- **Main Thread**: Runs main event loop, owns exchange client
- **Scanner Thread**: Discovers markets every 60s
- **Detector Thread**: Checks for entry signals every 100ms
- **Exit Thread**: Checks for exit signals every 100ms
- **Price WS Thread**: Runs Binance/Coinbase WebSocket (has own event loop!)
- **Kalshi WS Thread**: Runs Kalshi WebSocket (if initialized)

---

## Bug #1: Exit Fills Not Confirmed (98% Exit Failure)

### ❌ INITIAL HYPOTHESIS WAS WRONG
Memory claimed: "_place_exit():2053 calls _record_exit() immediately, stranded position risk"

### ✅ ACTUAL CODE BEHAVIOR (Lines 2565-2620)

```python
# orchestrator.py:2565-2620
# WAIT for fill confirmation
filled = self._run_async(
    self._wait_for_fill_om(exit_order_id, ticker, timeout=5.0)
)

if filled:
    # Retrieve ACTUAL fill price from OrderManager
    fills = self._run_async(self._om.get_fills(exit_order_id))
    if fills:
        actual_fill_price = fills[0].price_cents
        # ONLY NOW record the exit
        self._record_exit(ticker, position, actual_fill_price, exit_order_id)
else:
    logger.error(
        "Exit order %s failed to fill for %s (timeout after 5s)",
        exit_order_id, ticker,
    )
    # Keep position in tracking, will retry on next loop
    # Do NOT record exit since it didn't fill
```

**The code correctly waits for fill confirmation before recording exits.**

### Root Cause: OrderManager Not Initialized (Bug #3)

The REAL problem is `_wait_for_fill_om()` returns False 98% of the time.

**Why?**

```python
# orchestrator.py:2115-2147
async def _wait_for_fill_om(self, order_id: str, ticker: str, timeout: float = None) -> bool:
    timeout_sec = timeout if timeout is not None else self._config.fill_timeout_sec
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        try:
            status = await self._om.get_order_status(order_id)  # ← REST API poll

            if status == OrderStatus.FILLED:
                await self._om.get_fills(order_id)
                return True
            elif status == OrderStatus.CANCELED:
                return False

        except Exception as e:
            logger.debug(f"Fill check failed for {order_id}: {e}")

        await asyncio.sleep(0.2)  # Poll every 200ms

    return False
```

This polls REST API every 200ms for up to 5 seconds (exit timeout).

**The OrderManager.get_order_status() path:**

```python
# order_manager/kalshi_order_manager.py:677-718
async def get_order_status(self, order_id: str) -> OrderStatus:
    try:
        # Use client's get_order method
        order_data = await self._client.get_order(order_id)  # ← REST API call

        status_str = order_data.get("status", "pending")

        # Map API status to enum
        status_map = {
            "pending": OrderStatus.PENDING,
            "resting": OrderStatus.RESTING,
            "active": OrderStatus.RESTING,
            "executed": OrderStatus.FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELED,
            "cancelled": OrderStatus.CANCELED,
        }

        status = status_map.get(status_str.lower(), OrderStatus.PENDING)
        return status

    except Exception as e:
        logger.error(f"Failed to get status for {order_id}: {e}")
        return OrderStatus.PENDING  # ← Returns PENDING on error!
```

### The WebSocket Alternative (Not Being Used)

```python
# order_manager/kalshi_order_manager.py:102-178
async def initialize(self) -> None:
    """Initialize OMS - MUST be called on startup!"""

    # STEP 1: Cancel ALL resting orders
    canceled = await self.cancel_all_orders()

    # STEP 2: Recover positions from recent fills
    fills = await self.get_fills()

    # STEP 3: Start order age sweeper
    self._sweeper_running = True
    self._sweeper_task = asyncio.create_task(self._order_age_sweeper())

    # STEP 4: Start WebSocket fill stream (CRITICAL!)
    if self._enable_websocket:
        await self._start_websocket_fills()  # ← Never called!

    self._initialized = True
```

**OrderManager is created but NEVER initialized:**

```python
# orchestrator.py:243
self._om = KalshiOrderManager(exchange_client)
# ❌ Missing: await self._om.initialize()
```

### Impact

1. **No WebSocket fill stream** → fills only detected via REST API polling
2. **No position reconciliation** → can't detect stranded positions from previous runs
3. **No order age sweeper** → stale orders never auto-canceled
4. **REST API polling is unreliable** → subject to:
   - Network latency
   - Rate limiting
   - Kalshi API lag
   - 5 second timeout too short for illiquid markets

### Evidence from Fill Data

Out of 100 fills:
- **98 entries**: All succeeded (filled within timeout)
- **2 exits**: Only 2 out of 98 attempts succeeded (2% success rate)

**Why do entries succeed but exits fail?**

1. **Entries are taker orders** (cross the spread) → fill immediately
2. **Exits are limit orders** (try to get best price) → may not fill in 5s
3. **Illiquid markets** (30-74¢ spreads) → exits hard to fill
4. **Markets near expiry** → liquidity drained, no buyers

---

## Bug #3: OrderManager WebSocket Not Initialized

### File: `strategies/crypto_scalp/orchestrator.py`

**Line 243 - OrderManager created:**
```python
self._om = KalshiOrderManager(exchange_client)
```

**Missing:** No call to `await self._om.initialize()`

### Where It Should Be Called

```python
# orchestrator.py:401-407 (in async def run())
async def run(self) -> None:
    """Async entry point for MrClean CLI."""
    # Capture the main event loop
    self._main_loop = asyncio.get_running_loop()

    # ❌ MISSING: await self._om.initialize()

    # ... rest of setup
```

### Fix Required

```python
async def run(self) -> None:
    """Async entry point for MrClean CLI."""
    self._main_loop = asyncio.get_running_loop()

    # ✅ Initialize OrderManager BEFORE starting threads
    logger.info("Initializing OrderManager...")
    await self._om.initialize()
    logger.info("OrderManager initialized")

    # ... rest of setup
```

---

## Bug #10: No Duplicate Entry Prevention (56 Simultaneous Entries)

### ❌ INITIAL HYPOTHESIS WAS WRONG
Memory claimed: "No duplicate entry prevention → 4 'failed' entries might have filled"

### ✅ ACTUAL CODE HAS DUPLICATE PREVENTION (Lines 1651-1682)

```python
# orchestrator.py:1651-1682
def _place_entry(self, signal: ScalpSignal) -> None:
    """Place an entry order for a scalp signal."""

    # DUPLICATE POSITION CHECK + OPPOSITE-SIDE PROTECTION (Fix #10, #16)
    with self._lock:
        # Check for existing position
        if signal.ticker in self._positions:
            existing = self._positions[signal.ticker]
            if existing.side == signal.side:
                # Same side - just a duplicate entry attempt
                logger.warning(
                    "Already have %s position on %s, skipping duplicate entry",
                    signal.side.upper(), signal.ticker
                )
                return
            else:
                # Opposite side - CRITICAL ERROR!
                logger.error(
                    "🚨 OPPOSITE SIDE BLOCKED: Have %s position on %s, "
                    "attempted to enter %s (would hedge and lose fees!)",
                    existing.side.upper(), signal.ticker, signal.side.upper()
                )
                return

        # Check for pending entry order (prevents race condition)
        if signal.ticker in self._pending_entries:
            logger.warning(
                "Entry order already pending for %s, skipping duplicate attempt",
                signal.ticker
            )
            return

        # Mark this ticker as having a pending entry
        self._pending_entries.add(signal.ticker)
```

**The code HAS duplicate prevention!**

### Root Cause: Multiple Processes Running Simultaneously

The duplicate prevention works WITHIN a single process, but **6 separate processes** were running:

```bash
PID 83191 - process 1
PID 32589 - process 2
PID 89797 - process 3
PID 24748 - process 4
PID 51353 - process 5
PID 77194 - process 6
```

Each process has its own:
- `self._positions` dict
- `self._pending_entries` set
- Memory space

**They don't share state!**

### Evidence

From fill analysis:
- **56 fills < 1s apart** on same market
- **2 fills at SAME TIMESTAMP** (06:36:04.748)
- **2 fills 0.02s apart** (06:36:00.166 → 06:36:00.186)

This is physically impossible with a single process. Multiple processes all saw the same signal and tried to enter simultaneously.

### No Process Lock

```python
# orchestrator.py - Missing at startup
LOCKFILE = "/tmp/crypto_scalp.lock"

# Should have:
if os.path.exists(LOCKFILE):
    raise RuntimeError(f"Another instance is already running")

Path(LOCKFILE).touch()
atexit.register(lambda: Path(LOCKFILE).unlink(missing_ok=True))
```

**No such code exists in the current implementation.**

---

## Bug #2: Orderbook WebSocket Broken (80% Entry Failure)

### Memory Claim
"ORDERBOOK WEBSOCKET BROKEN - 80% entry failure (4/5), snapshot disabled, event loop mismatch"

### Investigation

**Orderbook manager IS created:**
```python
# orchestrator.py:298
if OrderBookManager:
    self._orderbook_manager = OrderBookManager()
```

**Snapshots ARE fetched:**
```python
# orchestrator.py:942-948
await self._orderbook_manager.apply_snapshot(ticker, orderbook)
logger.info(f"✓ Fetched and applied orderbook snapshot for {ticker}")
```

**But there's an event loop mismatch:**

```python
# orchestrator.py:1251-1270
# FIX #4: Use main loop instead of non-existent scanner loop
if self._main_loop:
    future = asyncio.run_coroutine_threadsafe(
        self._orderbook_manager.apply_snapshot(ticker, orderbook),
        self._main_loop,  # ← Cross-thread async call
    )
    future.result(timeout=1.0)
else:
    # Fallback: create temporary event loop
    loop = asyncio.new_event_loop()  # ← NEW LOOP EVERY TIME!
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            self._orderbook_manager.apply_snapshot(ticker, orderbook)
        )
    finally:
        loop.close()
```

**The problem:** When Kalshi WebSocket receives snapshot/delta messages (in WebSocket thread), it needs to update the orderbook manager (which uses async). But it's calling from a sync thread.

The code tries to use `run_coroutine_threadsafe()` but this has issues when called from non-async contexts.

### Impact

Without reliable orderbook data:
- Can't check pre-entry liquidity (Bug #2)
- Can't adjust entry prices to current market
- Can't detect thin liquidity before exit
- Fall back to stale REST API market data

### Evidence from Fill Data

- **90+ taker fills** (crossing spread, 10% fee)
- **Only ~6 maker fills** (posting liquidity, 7% fee)
- **Wide spreads crossed** (20-35¢)

This suggests orderbook data is stale or unavailable, so strategy is crossing spreads aggressively instead of posting competitive limit orders.

---

## Bug #4: Event Loop Architecture (3 Loops = Chaos)

### Current State

```python
# orchestrator.py has 3 event loops:

# 1. Main loop (owns exchange client)
self._main_loop = asyncio.get_running_loop()  # Line 404

# 2. Price WebSocket loop (Binance/Coinbase)
self._price_ws_loop = asyncio.new_event_loop()  # Line 971

# 3. Temporary loops created on demand
loop = asyncio.new_event_loop()  # Line 1261 (when main loop unavailable)
```

### Problems

1. **Cross-thread async calls are fragile**
   - `run_coroutine_threadsafe()` can deadlock
   - Timeout errors are common
   - Error handling is complex

2. **Orderbook updates from WebSocket thread**
   - WebSocket runs in separate thread
   - Tries to call async `apply_snapshot()` from sync context
   - Has to use `run_coroutine_threadsafe()` → unreliable

3. **OrderManager calls from detector thread**
   - Detector runs in separate thread (line 634)
   - Calls `_run_async()` which uses `run_coroutine_threadsafe()` (line 1523)
   - Can timeout, can fail

### Architecture Mismatch

```
Thread 1 (Main)     Thread 2 (Detector)   Thread 3 (Price WS)   Thread 4 (Kalshi WS)
   [Main Loop]           [No Loop]         [Price WS Loop]          [WS Loop?]
       │                      │                   │                     │
       │                      ├─ submit_order ────┼─> coroutine_threadsafe
       │                      │                   │                     │
       │                      │                   ├─ apply_snapshot ────┼─> coroutine_threadsafe
       │                      │                   │                     │
       └──────────────── All trying to use Main Loop ──────────────────┘
                         (Race conditions, deadlocks, timeouts)
```

### Solution Needed

All async operations should run in ONE event loop (main loop). Worker threads should use queues to communicate, not cross-thread async calls.

---

## Bug #6: Exit Price = Limit Not Fill (P&L Wrong)

### ❌ INITIAL HYPOTHESIS WAS WRONG
Memory claimed: "Records 25¢ limit instead of actual fill → P&L completely wrong"

### ✅ ACTUAL CODE GETS FILL PRICE (Lines 2571-2581)

```python
# orchestrator.py:2571-2581
if filled:
    # Retrieve ACTUAL fill price from OrderManager
    fills = self._run_async(self._om.get_fills(exit_order_id))
    if fills:
        actual_fill_price = fills[0].price_cents  # ← ACTUAL fill price
        logger.info(
            "✓ EXIT FILLED: %s @ %d¢ (limit was %d¢)",
            ticker,
            actual_fill_price,  # ← Logs both
            limit_price,
        )
        self._record_exit(ticker, position, actual_fill_price, exit_order_id)
```

**The code correctly retrieves actual fill price when exit succeeds.**

### Real Problem: Exits Don't Fill (98% Failure)

Since only 2 exits out of 98 succeeded, there's no P&L to record for the 96 that failed.

The code would have used actual fill prices IF the exits had filled. But they didn't fill because:
- OrderManager WebSocket not initialized (Bug #3)
- REST API polling timed out after 5s
- Illiquid markets with no buyers

---

## Bug #7: Entry Fees Not Logged

### Code Inspection

```python
# orchestrator.py:2583-2603 (EXIT FEE CALCULATION)
# Calculate and log fee breakdown
gross_pnl_per_contract = actual_fill_price - position.entry_price_cents
entry_fee = max(1, int(position.entry_price_cents * KALSHI_FEE_RATE))
exit_fee = max(1, int(actual_fill_price * KALSHI_FEE_RATE))
total_fees = entry_fee + exit_fee
net_pnl_per_contract = gross_pnl_per_contract - total_fees
total_net_pnl = net_pnl_per_contract * position.size

logger.info(
    "EXIT%s: %s %s %d @ %dc (was %dc) | gross=%+d¢ fees=%d¢ net=%+d¢ | order %s",
    " [FORCE]" if force else "",
    position.side.upper(),
    ticker,
    position.size,
    actual_fill_price,
    position.entry_price_cents,
    gross_pnl_per_contract,
    total_fees,
    net_pnl_per_contract,
    exit_order_id,
)
```

**Fees ARE calculated and logged... but only for EXITS!**

### Entry Fee Logging (Missing)

```python
# orchestrator.py:1846-1855 (ENTRY LOGGING)
self._stats.trades_entered += 1
logger.info(
    "LIMIT FILL [%s]: %s %s %d @ %dc (order %s)",
    signal.source,
    signal.side.upper(),
    signal.ticker,
    position.size,
    position.entry_price_cents,
    order_id,
)
# ❌ NO FEE CALCULATION OR LOGGING FOR ENTRIES
```

### Impact

- Entry fees not tracked in logs
- Can't calculate true cost basis
- P&L calculation incomplete until exit
- Since 98% of exits never happened, no fee logging for 98% of trades

---

## Bug #8: No Balance Tracking

### Code Search

```bash
$ grep -n "get_balance\|_balance" orchestrator.py
283:self._last_balance_cents: Optional[int] = None
```

**Only 1 reference to balance, never used.**

### Missing Code

```python
# Should exist but doesn't:

# On startup
self._starting_balance = await self._client.get_balance()

# After each trade
current_balance = await self._client.get_balance()
expected_balance = self._starting_balance + self._cumulative_pnl
drift = abs(current_balance.balance_cents - expected_balance)
if drift > 100:  # > $1 drift
    logger.error(f"BALANCE DRIFT: ${drift/100:.2f}")
```

### Impact

- Lost $44 without detection
- No circuit breaker triggered
- No balance reconciliation
- Could lose entire account without notification

---

## Bug #9: No Position Reconciliation

### On Startup

**Missing code:**
```python
# Should check positions on startup
positions = await self._client.get_positions()
if positions:
    logger.warning(f"Found {len(positions)} open positions from previous run!")
    for pos in positions:
        logger.warning(f"  - {pos.ticker}: {pos.position} contracts @ {pos.avg_price_cents}¢")
```

**Actual code:** Nothing. Strategy starts with empty `self._positions = {}` dict.

### Impact

- 94 stranded positions never detected
- No warning about open positions on restart
- If processes crashed and restarted, would create MORE duplicate positions
- No recovery mechanism

---

## Bug #5: No WebSocket Reconnection

### OrderManager WebSocket

The OrderManager has WebSocket support but:
1. Never initialized (Bug #3)
2. No reconnection logic visible

### Price Feed WebSocket

```python
# orchestrator.py:971-974
def _run_price_ws(self) -> None:
    """Run WebSocket price feed in background thread."""
    self._price_ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self._price_ws_loop)
    try:
        self._price_ws_loop.run_until_complete(self._price_ws_main())
```

No try/except to retry on disconnect. If WebSocket drops, feed is dead until restart.

### Impact

- Single disconnect → permanent failure
- No automatic recovery
- Would need manual process restart

---

## Summary: Bug Interaction Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 6 PROCESSES RUNNING SIMULTANEOUSLY                          │
│ (Bug #10: No process lock)                                  │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ├─> All see same signal
                  ├─> All try to enter
                  └─> 56 simultaneous entries
                        │
                        ├─> Orders fill (taker, crosses spread)
                        │   (Bug #2: Orderbook unreliable, wide spreads crossed)
                        │
                        └─> Try to exit after delay
                              │
                              ├─> Submit exit order
                              │
                              └─> Wait for fill (Bug #1/3)
                                    │
                                    ├─> No WebSocket fills (Bug #3)
                                    ├─> REST polling times out after 5s
                                    ├─> Illiquid market, limit order doesn't fill
                                    └─> Exit fails, position stranded
                                          │
                                          ├─> Market expires
                                          ├─> Position goes to $0
                                          └─> $44 loss (Bug #8: no balance tracking)
                                                │
                                                └─> Undetected (Bug #9: no reconciliation)
```

---

## Complete Bug List with File/Line References

| # | Bug | File | Lines | Severity | Fix Difficulty |
|---|-----|------|-------|----------|----------------|
| 1 | Exit fills not confirmed | orchestrator.py | N/A (not actual bug) | N/A | N/A |
| 2 | Orderbook WebSocket unreliable | orchestrator.py | 1251-1270 | HIGH | HARD |
| 3 | OrderManager never initialized | orchestrator.py | 243, 401 | CRITICAL | EASY |
| 4 | Event loop architecture chaos | orchestrator.py | 404, 971, 1261 | HIGH | HARD |
| 5 | No WebSocket reconnection | orchestrator.py | 971-974 | MEDIUM | MEDIUM |
| 6 | Exit price = limit not fill | orchestrator.py | N/A (not actual bug) | N/A | N/A |
| 7 | Entry fees not logged | orchestrator.py | 1846-1855 | LOW | EASY |
| 8 | No balance tracking | orchestrator.py | MISSING | CRITICAL | EASY |
| 9 | No position reconciliation | orchestrator.py | MISSING | CRITICAL | EASY |
| 10 | No process lock | orchestrator.py | MISSING | CRITICAL | TRIVIAL |

---

## Bugs That Are Actually Bugs vs. Memory Errors

### Real Bugs (7)
- **Bug #2**: Orderbook WebSocket unreliable (event loop mismatch)
- **Bug #3**: OrderManager never initialized (**CRITICAL - causes 98% exit failure**)
- **Bug #4**: Event loop architecture (3 loops, cross-thread calls)
- **Bug #5**: No WebSocket reconnection
- **Bug #7**: Entry fees not logged
- **Bug #8**: No balance tracking (**CRITICAL - $44 loss undetected**)
- **Bug #9**: No position reconciliation (**CRITICAL - 94 stranded positions**)
- **Bug #10**: No process lock (**CRITICAL - caused 6 simultaneous instances**)

### Not Actually Bugs (3)
- **Bug #1**: Exit fills ARE confirmed before recording (code is correct)
- **Bug #6**: Exit price IS retrieved from actual fills (code is correct)
- **Bug #10 (duplicate prevention)**: Code HAS duplicate prevention (but useless with 6 processes)

---

## Priority Fix Order

### P0 (Critical - Fix Before ANY Trading)
1. **Bug #10**: Add process lock (5 minutes to implement)
2. **Bug #3**: Initialize OrderManager (5 minutes to implement)
3. **Bug #8**: Add balance tracking + circuit breaker (30 minutes)
4. **Bug #9**: Add position reconciliation on startup (30 minutes)

### P1 (High - Fix Before Live Trading)
5. **Bug #2**: Fix orderbook event loop mismatch (4 hours)
6. **Bug #4**: Refactor to single event loop architecture (8 hours)

### P2 (Medium - Fix Soon)
7. **Bug #5**: Add WebSocket reconnection (2 hours)

### P3 (Low - Fix When Convenient)
8. **Bug #7**: Log entry fees (15 minutes)

---

## Next Steps

1. **Immediate**: Implement P0 fixes (process lock, OMS init, balance tracking, position reconciliation)
2. **Testing**: Run 8-hour paper mode with fixes
3. **Validation**: Verify no duplicate processes, all fills detected, balance matches
4. **Long-term**: Refactor event loop architecture (P1 bugs)

---

*Analysis Date: March 3, 2026*
*Analyst: Claude Sonnet 4.5*
*Files Analyzed: orchestrator.py (2779 lines), kalshi_order_manager.py (1200+ lines), config.py (200 lines)*
