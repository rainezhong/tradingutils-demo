# Parallel Market Scanning Optimization

**Date**: 2026-03-01
**Task**: Parallelize market discovery (#5)
**Impact**: 2-5x faster market scanning (500ms → <200ms for multi-series)

## Problem

Market scanning was sequential, causing cumulative latency when fetching multiple series:

```python
# BEFORE (sequential):
for series in ["KXBTC15M", "KXETH15M", "KXSOL15M"]:
    response = client._request(...)  # 50ms each
# Total: 3 × 50ms = 150ms minimum
```

For crypto latency arb with 3 series at 50ms each, this meant 150ms+ total fetch time before even starting to parse markets.

## Solution

Refactored both crypto and NBA scanners to use `asyncio.gather()` for concurrent API calls:

```python
# AFTER (parallel):
async def fetch_all_series():
    tasks = [
        client._request(..., series="KXBTC15M"),
        client._request(..., series="KXETH15M"),
        client._request(..., series="KXSOL15M"),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results

# Total: max(50ms, 50ms, 50ms) = 50ms
```

## Implementation

### Files Modified

1. **`strategies/crypto_latency/kalshi_scanner.py`**
   - `scan()`: Parallelized series fetching (lines 138-210)
   - `refresh_prices()`: Parallelized price updates (lines 219-296)
   - Added latency instrumentation (fetch, parse, total)

2. **`strategies/latency_arb/nba.py`**
   - Updated comments to clarify single-series optimization
   - Added latency instrumentation (fetch, parse, total)

3. **`tests/latency_arb/test_parallel_scanning.py`**
   - 4 tests validating parallel execution
   - Tests verify sub-200ms execution with 3 series @ 100ms each
   - Tests confirm graceful handling of partial failures

### Key Changes

#### Crypto Scanner (3 series → parallel)

**Before**:
```python
for series in self._series_to_scan:
    response = self._client._request(...)  # Sequential
    for m in response.get("markets", []):
        markets.append(self._parse_market(m, series))
```

**After**:
```python
async def fetch_all_series():
    tasks = [(series, self._client._request(...)) for series in self._series_to_scan]
    results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
    return list(zip([series for series, _ in tasks], results))

series_responses = loop.run_until_complete(fetch_all_series())
for series, response in series_responses:
    if isinstance(response, Exception):
        logger.warning("Failed to scan series %s: %s", series, response)
        continue
    # Parse markets...
```

#### Error Handling

- `return_exceptions=True` in `gather()` prevents one failed series from killing entire scan
- Individual exceptions logged with series name for debugging
- Partial results still returned (e.g., BTC + SOL succeed even if ETH fails)

#### Latency Instrumentation

Both scanners now log detailed timing breakdown:

```python
t0 = time.time()
# ... fetch markets ...
t1 = time.time()
# ... parse markets ...
t2 = time.time()

logger.info(
    f"Crypto market scan latency: "
    f"fetch={t1-t0:.3f}s, "
    f"parse={t2-t1:.3f}s, "
    f"total={t2-t0:.3f}s | "
    f"markets={len(markets)}"
)
```

## Performance Impact

### Expected Latency Reduction

| Scenario | Before (sequential) | After (parallel) | Speedup |
|----------|---------------------|------------------|---------|
| Crypto (3 series @ 50ms) | 150ms | 50ms | 3x |
| Crypto (3 series @ 100ms) | 300ms | 100ms | 3x |
| NBA (1 series) | 50ms | 50ms | 1x (no change) |

**Note**: NBA has only one series (`KXNBAGAME`), so no parallelization benefit. Code updated for consistency and instrumentation only.

### Test Validation

All 4 tests pass, confirming:
- ✅ Parallel execution completes in <200ms (vs 300ms sequential for 3×100ms calls)
- ✅ Partial failures handled gracefully (2/3 series succeed → 2 markets returned)
- ✅ Cache interval respected (no redundant API calls)
- ✅ Concurrent price refresh works correctly

```bash
$ python3 -m pytest tests/latency_arb/test_parallel_scanning.py -v
# 4 passed in 0.93s
```

### Production Logs

After deployment, check scanner logs for timing breakdown:

```
INFO Crypto market scan latency: fetch=0.045s, parse=0.003s, total=0.048s | markets=12
INFO NBA market scan latency: fetch=0.038s, parse=0.012s, total=0.050s | markets=5
```

## Backward Compatibility

- No breaking changes to scanner interfaces
- Same synchronous `scan()` method signature
- Async execution is internal implementation detail
- Existing strategy code works unchanged

## Edge Cases

1. **Single series**: No parallelization benefit, but no performance penalty either
2. **Empty series list**: Returns empty list immediately (no API calls)
3. **All series fail**: Returns empty list, logs errors for each series
4. **Mixed success/failure**: Returns partial results for successful series only

## Future Optimizations

1. **WebSocket orderbook updates**: Replace REST polling with WS (Task #3)
2. **Smart caching**: Skip refetch if market data is <100ms old
3. **Batch market details**: Fetch detailed market data in parallel after filtering
4. **Connection pooling**: Reuse HTTP connections across scans

## Related Tasks

- Task #2: ✅ Reduce Kalshi polling interval to 250ms
- Task #3: 🔄 Switch to WebSocket orderbook updates
- Task #4: 🔄 Add latency profiling instrumentation
- Task #5: ✅ Parallelize market discovery (this doc)

## Usage

No code changes needed for existing strategies. The optimization is transparent:

```python
# Existing code works unchanged
scanner = KalshiCryptoScanner(client, config)
markets = scanner.scan(force=True)  # Now 3x faster!
```

## Monitoring

Monitor scanner latency logs to verify optimization:

```bash
# Grep for scanner latency logs
grep "market scan latency" logs/latency_arb.log

# Expected output:
# Crypto market scan latency: fetch=0.045s, parse=0.003s, total=0.048s | markets=12
# NBA market scan latency: fetch=0.038s, parse=0.012s, total=0.050s | markets=5
```

If `fetch` time exceeds 100ms, investigate network/API issues.
If `parse` time exceeds 10ms, investigate market data complexity or validation overhead.
