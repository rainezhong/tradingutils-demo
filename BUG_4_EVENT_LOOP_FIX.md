# Bug #4: Event Loop Architecture Fix

## Problem
The crypto scalp orchestrator had 3 separate event loops causing "No event loop available" errors when calling async functions from wrong threads:
1. **Main loop** (`_main_loop`) - created in `run()` method, runs main async logic
2. **Scanner loop** (`_scanner_loop`) - NEVER CREATED, but referenced in `_run_async()`
3. **Price WebSocket loop** (`_price_ws_loop`) - created in price WebSocket thread

Balance queries failed every 30s with "No event loop available" warnings because the dashboard thread tried to use the non-existent scanner loop.

## Root Cause
- `_scanner_loop` was declared but never initialized
- `_run_async()` tried to use `_scanner_loop` which was always None
- Dashboard thread called `_run_async()` → always hit the "no event loop" path
- Orderbook REST fallback also checked for `_scanner_loop` and fell back to creating temp loops

## Solution
Fixed all async calls from sync threads to use the **main event loop** (`_main_loop`):

### 1. Fixed `_run_async()` method (line 1479)
**Before:**
```python
def _run_async(self, coro):
    """Run an async coroutine from a sync thread using the scanner loop."""
    loop = self._scanner_loop  # ALWAYS None!
    if loop is None or loop.is_closed():
        logger.warning("No event loop available for async call")
        return None
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=10.0)
```

**After:**
```python
def _run_async(self, coro):
    """Run an async coroutine from a sync thread using the main event loop.

    NOTE: This is a legacy method kept for backward compatibility.
    Prefer using _run_async_in_main_loop() which has better error handling.
    """
    # FIX #4: Use main loop instead of non-existent scanner loop
    if not self._main_loop:
        logger.warning("No event loop available for async call")
        return None
    future = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
    try:
        return future.result(timeout=10.0)
    except TimeoutError:
        logger.error("Async call timed out after 10s")
        return None
    except Exception as e:
        logger.error("Async call failed: %s", e)
        return None
```

### 2. Fixed balance query in dashboard thread (line 2774)
**Before:**
```python
balance = self._run_async(self._client.get_balance())
self._last_balance_cents = balance.balance_cents
```

**After:**
```python
# Use main loop for async call from dashboard thread (FIX #4)
balance = self._run_async_in_main_loop(self._client.get_balance(), timeout=5.0)
if balance:
    self._last_balance_cents = balance.balance_cents
    self._last_balance_check = now
else:
    logger.warning("Balance query returned None")
```

### 3. Fixed orderbook REST fallback (line 1210)
**Before:**
```python
if self._scanner_loop:  # ALWAYS False!
    future = asyncio.run_coroutine_threadsafe(
        self._orderbook_manager.apply_snapshot(ticker, orderbook),
        self._scanner_loop,
    )
```

**After:**
```python
# FIX #4: Use main loop instead of non-existent scanner loop
if self._main_loop:
    future = asyncio.run_coroutine_threadsafe(
        self._orderbook_manager.apply_snapshot(ticker, orderbook),
        self._main_loop,
    )
```

## Benefits
1. **Balance queries work** - Dashboard thread can now successfully query balance every 30s
2. **No "No event loop available" warnings** - All async calls use proper main loop
3. **Orderbook REST fallback works** - Uses main loop instead of creating temp loops
4. **Better error handling** - Timeout and exception handling in `_run_async()`
5. **Thread-safe async calls** - `asyncio.run_coroutine_threadsafe()` properly schedules work in main loop

## Thread Architecture (After Fix)
```
Main Thread:
  └─ _main_loop (asyncio event loop)
     ├─ run() method
     ├─ OMS initialization
     └─ Receives async calls from other threads via run_coroutine_threadsafe()

Scanner Thread (sync):
  └─ _scanner_loop_fn()
     └─ Calls _run_async() → schedules on main loop

Detector Thread (sync):
  └─ _detector_loop()
     └─ _check_for_signals()
        └─ _place_entry() → calls _run_async() → schedules on main loop

Dashboard Thread (sync):
  └─ _dashboard_loop()
     └─ Calls _run_async_in_main_loop() → schedules on main loop

Price WebSocket Thread:
  └─ _price_ws_loop (separate event loop, isolated)
```

## Testing
To verify the fix works:
1. Run live or paper mode: `python main.py run crypto-scalp --dry-run`
2. Watch logs for balance queries every 30s (should succeed)
3. No "No event loop available" warnings should appear
4. Balance drift detection should work (see DASH logs)

## Files Modified
- `strategies/crypto_scalp/orchestrator.py` (3 changes)

## Related Bugs
- Bug #8: Balance tracking (now works with this fix)
- Bug #3: OMS WebSocket not initialized (also needs main loop)
