# Orderbook Callback Fix Summary

## Issues Fixed

### 1. Ticker Extraction (✅ FIXED)
- **Problem**: WebSocket using `sid` (subscription ID) instead of `market_ticker`
- **Fix**: Changed line 365 in `kalshi_websocket.py` to use `market_ticker`
- **Result**: Ticker now correctly extracted as "KXBTC15M-26MAR021645-45" instead of "2"

### 2. Callback Pattern (✅ FIXED)
- **Problem**: Async callback not being awaited (coroutine warning)
- **Fix**: Made callback synchronous with `asyncio.create_task()` to schedule async work
- **Result**: No more "coroutine was never awaited" warnings

### 3. Cache Update Registration (✅ FIXED)
- **Problem**: Sync wrapper's `_on_update_wrapper` not registered with async manager
- **Fix**: Passed `_on_update_wrapper` as `on_update` callback when creating async manager
- **Result**: Cache should update when async manager processes deltas

### 4. OrderBookManager Signature (✅ FIXED)
- **Problem**: Sync wrapper had wrong signature (individual params vs dict)
- **Fix**: Changed `apply_delta` and `apply_snapshot` to accept dict format
- **Result**: Matches async manager's expected interface

## Remaining Issue

### Snapshot Fetching (❌ BLOCKING)
- **Problem**: Cannot fetch initial orderbook snapshot due to event loop mismatch
- **Error**: "Task got Future attached to a different loop"
- **Impact**: Deltas cannot be applied without snapshot, so cache stays empty

**Root Cause**: The KalshiExchangeClient instance is being used from multiple async contexts:
1. Created in `__init__` (no loop)
2. Connected from scanner thread via `_run_async_in_main_loop()`
3. Used from WebSocket thread (main loop)

The client's internal HTTP session is bound to whichever loop was active when `connect()` was called, causing conflicts.

## Proposed Solution

**Option A** (Simple): Don't use orderbook manager - detector already handles absence gracefully
- Pros: No code changes, system runs now
- Cons: Can't use market order fallback, lower fill rate

**Option B** (Medium): Create dedicated client for WebSocket thread
- Pros: Clean separation, proper async context
- Cons: Two client instances, duplicate authentication

**Option C** (Complex): Refactor to single-threaded async architecture
- Pros: Proper event loop management, no thread sync issues
- Cons: Major refactor, high risk

## Recommendation

Proceed with **Option A** for now - the overnight test showed the system is stable and generates signals correctly. The orderbook data is "nice to have" for market order fallback but not critical for core functionality. We can add it later with proper async architecture.

The entry timing optimizations (threshold, momentum filter, regime filter) are all working and just need market conditions to validate.
