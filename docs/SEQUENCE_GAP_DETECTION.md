# WebSocket Sequence Gap Detection

## Overview

Sequence gap detection prevents orderbook divergence from missed WebSocket messages by tracking sequence numbers and triggering reconnection when gaps are detected.

## Architecture

### Components

1. **KalshiWebSocket** (`core/exchange_client/kalshi/kalshi_websocket.py`)
   - Tracks sequence numbers per ticker
   - Detects gaps and triggers reconnection
   - Provides gap metrics and callbacks

2. **OrderBookManager** (`core/market/orderbook_manager.py`)
   - Validates delta sequence numbers
   - Invokes gap callback on detection
   - Returns `DeltaResult.GAP` when gap detected

3. **CEX Feeds** (`core/indicators/cex_feeds.py`)
   - Base `ExchangeL2Feed` class with gap detection
   - Coinbase L2 feed supports sequence validation
   - Other exchanges marked as unsupported (no seq numbers in protocol)

## Configuration

### KalshiWebSocket

```python
from core.exchange_client.kalshi.kalshi_websocket import (
    KalshiWebSocket,
    WebSocketConfig,
)

config = WebSocketConfig(
    enable_sequence_validation=True,  # Enable gap detection
    gap_tolerance=0,  # Strict mode (default)
)

ws = KalshiWebSocket(auth=auth, config=config)

# Register gap callback
ws.on_gap_detected(lambda ticker, expected, actual:
    print(f"Gap on {ticker}: expected {expected}, got {actual}")
)
```

### OrderBookManager

```python
from core.market.orderbook_manager import OrderBookManager

def on_gap(ticker, expected_seq, actual_seq):
    print(f"OrderBook gap: {ticker} expected {expected_seq}, got {actual_seq}")

manager = OrderBookManager(on_gap=on_gap)

# Apply deltas - will call on_gap if sequence gap detected
result = await manager.apply_delta(ticker, delta_msg)
if result == DeltaResult.GAP:
    # Handle gap - trigger reconnection, invalidate book, etc.
    pass
```

### CEX Feeds

```python
from core.indicators.cex_feeds import CoinbaseL2Feed

feed = CoinbaseL2Feed(
    enable_sequence_validation=True,
    gap_tolerance=2,  # Allow gaps up to 2
)

feed.start()

# Check metrics
metrics = feed.get_gap_metrics()
print(f"Total gaps: {metrics['total_gaps']}")
print(f"Average gap size: {metrics['average_gap_size']}")
```

## Behavior

### Sequence Tracking

- **First message**: Initializes tracking with the received sequence number
- **Consecutive messages**: `seq == last_seq + 1` → no gap
- **Within tolerance**: `seq - (last_seq + 1) <= gap_tolerance` → no gap
- **Gap detected**: `seq - (last_seq + 1) > gap_tolerance` → gap
- **Out-of-order**: `seq < last_seq + 1` → ignored (no gap, no seq update)

### Gap Handling

When a gap is detected:

1. **Log warning** with expected vs actual sequence numbers
2. **Update metrics** (total_gaps, last_gap_time, gap_sizes)
3. **Invoke callbacks** (if registered)
4. **Update last_seq** to the gapped value (prevents cascading false positives)
5. **Trigger reconnection** (KalshiWebSocket closes connection)

### OrderBook Invalidation

The `OrderBookManager` detects gaps but does NOT automatically invalidate the book:

```python
result = await manager.apply_delta(ticker, delta)

if result == DeltaResult.GAP:
    # Application layer decides what to do:
    # 1. Clear the orderbook
    await manager.clear(ticker)

    # 2. Trigger WebSocket reconnection (fresh snapshot)
    await ws._handle_sequence_gap(ticker)
```

## Metrics

### Gap Metrics Structure

```python
{
    "total_gaps": 5,  # Total gaps detected
    "last_gap_time": 1234567890.123,  # Unix timestamp of last gap
    "gap_sizes": [4, 2, 10, 3, 5],  # Size of each gap (capped at 100)
    "average_gap_size": 4.8,  # Average gap size (CEX feeds only)
}
```

### Retrieving Metrics

```python
# KalshiWebSocket
metrics = ws.get_gap_metrics("TICKER-1")  # Single ticker
all_metrics = ws.get_gap_metrics()  # All tickers

# CEX Feed
metrics = feed.get_gap_metrics()

# Reset metrics
ws.reset_gap_metrics("TICKER-1")  # Single ticker
ws.reset_gap_metrics()  # All tickers
```

## Limitations

### Kalshi WebSocket

**Current state**: Kalshi orderbook messages (`orderbook_snapshot`, `orderbook_delta`) **do NOT include sequence numbers** in the WebSocket protocol.

The implementation:
- Architecture is ready for when Kalshi adds `seq` field
- Currently synthesizes sequence numbers in `scripts/btc_latency_probe.py`
- Validation is **opt-in** (disabled by default)

### CEX Feeds

| Exchange   | Supports Seq | Notes |
|------------|--------------|-------|
| Coinbase   | Yes          | `level2_batch` channel includes `sequence` field |
| Kraken     | No           | WebSocket v2 does not provide sequence numbers |
| Bitstamp   | No           | No sequence field in orderbook messages |
| Gemini     | No           | No sequence field in L2 updates |
| Crypto.com | No           | No sequence field in book channel |

## Testing

Comprehensive test suite in `tests/test_sequence_gap_detection.py`:

- 28 tests covering:
  - Sequence tracking initialization
  - Gap detection with/without tolerance
  - Out-of-order message handling
  - Gap metrics tracking and limits
  - Callback invocation
  - OrderBook manager integration
  - CEX feed sequence support
  - Integration scenarios

Run tests:
```bash
python3 -m pytest tests/test_sequence_gap_detection.py -v
```

## Future Enhancements

1. **Kalshi Protocol Update**: When Kalshi adds `seq` to WS messages, remove synthetic sequence generation
2. **CEX Feed Support**: Add sequence validation if exchanges add sequence numbers to their protocols
3. **Auto-reconnect Logic**: Configurable reconnect strategies (immediate, exponential backoff, etc.)
4. **Gap Recovery**: Attempt to fetch missed deltas from REST API before full reconnection
5. **Alerting**: Integration with monitoring systems for gap detection events

## References

- WebSocket implementation: `/Users/raine/tradingutils/core/exchange_client/kalshi/kalshi_websocket.py`
- OrderBook manager: `/Users/raine/tradingutils/core/market/orderbook_manager.py`
- CEX feeds: `/Users/raine/tradingutils/core/indicators/cex_feeds.py`
- Test suite: `/Users/raine/tradingutils/tests/test_sequence_gap_detection.py`
- Example usage: `/Users/raine/tradingutils/scripts/btc_latency_probe.py` (synthetic seq for Kalshi)
