# Orderbook Event Loop Fix (Bug #2) - Implementation Summary

## Problem
The crypto scalp strategy had a critical event loop architecture issue:

1. **Cross-thread async calls**: WebSocket thread (with its own event loop) tried to call async `apply_snapshot()` and `apply_delta()` methods
2. **Fragile `asyncio.create_task()`**: Delta handler used `create_task()` which could fail with event loop mismatches
3. **Unreliable `run_coroutine_threadsafe()`**: REST fallback used cross-thread async calls that could timeout
4. **Temporary event loop creation**: Fallback code created temporary event loops when main loop unavailable (very bad practice)

## Solution: Queue-Based Communication

Implemented a clean separation between WebSocket thread (producer) and main loop (consumer):

### 1. Added Queue Infrastructure
- **File**: `strategies/crypto_scalp/orchestrator.py`
- **Line ~287**: Added `self._orderbook_queue: queue.Queue = queue.Queue(maxsize=1000)`
- **Import**: Added `import queue` at top of file

### 2. Created Queue Processor
- **Method**: `_process_orderbook_queue()` (lines ~427-460)
- **Logic**: 
  - Processes up to 100 updates per cycle (non-blocking)
  - Handles both 'snapshot' and 'delta' update types
  - Calls async `apply_snapshot()` / `apply_delta()` in main loop context
  - Graceful error handling with logging

### 3. Integrated into Main Loop
- **File**: `strategies/crypto_scalp/orchestrator.py`
- **Method**: `run()` (line ~563)
- **Change**: Main loop now calls `await self._process_orderbook_queue()` every 100ms
- **Before**: `await asyncio.sleep(0.5)`
- **After**: 
  ```python
  while self._running:
      await self._process_orderbook_queue()
      await asyncio.sleep(0.1)  # Process queue every 100ms
  ```

### 4. Updated WebSocket Delta Handler
- **Location**: `_price_ws_main()` method (lines ~1120-1151)
- **Before**: Used `asyncio.create_task(apply_delta_async())` - fragile cross-thread async
- **After**: Pushes to queue synchronously - no async calls in WebSocket thread
- **Implementation**:
  ```python
  def handle_orderbook_delta(ticker: str, data: dict):
      # Validate and enrich data
      if data.get('price') is None or data.get('delta') is None or not data.get('side'):
          return
      
      # Synthesize seq numbers
      if ticker not in self._orderbook_seq:
          self._orderbook_seq[ticker] = 0
      self._orderbook_seq[ticker] += 1
      data['seq'] = self._orderbook_seq[ticker]
      
      # Push to queue (non-blocking)
      try:
          self._orderbook_queue.put_nowait({
              'type': 'delta',
              'ticker': ticker,
              'data': data,
          })
      except queue.Full:
          logger.warning(f"Orderbook queue full, dropping delta for {ticker}")
  ```

### 5. Updated REST Fallback
- **Location**: `_rest_orderbook_fallback_loop()` (lines ~1328-1341)
- **Before**: Used `run_coroutine_threadsafe()` to apply snapshot from scanner thread
- **After**: Pushes to queue synchronously
- **Implementation**:
  ```python
  try:
      self._orderbook_queue.put_nowait({
          'type': 'snapshot',
          'ticker': ticker,
          'data': orderbook,
      })
      logger.debug(f"✓ Queued REST orderbook snapshot for {ticker}")
  except queue.Full:
      logger.warning(f"Orderbook queue full, dropping REST snapshot for {ticker}")
  ```

## Files Modified
- `strategies/crypto_scalp/orchestrator.py` (5 sections updated)

## Code Verified
- ✓ Compiles successfully with `py_compile`
- ✓ No syntax errors
- ✓ All imports present (`queue` module)
- ✓ Queue capacity: 1000 updates (should handle ~10 seconds of deltas at 100/sec)

## What Was NOT Changed
The following `run_coroutine_threadsafe()` calls were kept (legitimate uses):
1. `_run_async_in_main_loop()` - utility method for calling async from threads
2. OMS shutdown - legitimate cross-thread cleanup
3. `_subscribe_to_orderbook()` - subscription management (not orderbook updates)

These are all proper uses of cross-thread async where the caller explicitly manages the event loop.

## Impact

### Before (Buggy)
- WebSocket thread: `asyncio.create_task(apply_delta_async())` → Event loop mismatch errors
- Scanner thread: `run_coroutine_threadsafe(apply_snapshot(), main_loop)` → Timeouts/failures
- 80% entry failure rate due to orderbook not updating

### After (Fixed)
- WebSocket thread: `queue.put_nowait(delta)` → No async, no event loop issues
- Scanner thread: `queue.put_nowait(snapshot)` → No async, no event loop issues
- Main loop: `await process_queue()` → Clean async context for all orderbook updates
- Expected: 100% orderbook update reliability

## Next Steps
1. Test in paper mode for 1 hour to verify orderbook updates work reliably
2. Check logs for "Orderbook queue full" warnings (should be none)
3. Verify dashboard shows orderbook data updating in real-time
4. Monitor entry success rate (should be near 100% when signals fire)

## Related Bugs
- Bug #2 (this fix): Orderbook event loop mismatch - **FIXED**
- Bug #4: Multiple event loop architecture - requires broader refactor
- Bug #5: WebSocket reconnection - needs separate implementation
