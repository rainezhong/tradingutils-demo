# TradingUtils System Architecture Documentation

**Version:** 2.0 (Post-WebSocket Infrastructure Fix)
**Last Updated:** March 3, 2026
**Status:** Production-Ready (after 8-hour integration test)

---

## Table of Contents

1. [High-Level Overview](#high-level-overview)
2. [Core Design Principles](#core-design-principles)
3. [System Component Hierarchy](#system-component-hierarchy)
4. [Core Components](#core-components)
5. [Crypto Scalp Strategy Architecture](#crypto-scalp-strategy-architecture)
6. [Key Design Patterns](#key-design-patterns)
7. [Critical Bug Fixes (March 2026)](#critical-bug-fixes-march-2026)
8. [Data Flow Diagrams](#data-flow-diagrams)
9. [Critical Code Paths](#critical-code-paths)
10. [Thread Safety & Concurrency](#thread-safety--concurrency)

---

## High-Level Overview

### System Purpose

TradingUtils is a **multi-strategy automated trading system** for prediction markets (Kalshi, Polymarket) that implements:

- **Latency arbitrage**: Exploit 5-10 second lags between spot exchanges (Binance, Coinbase, Kraken) and prediction markets
- **Statistical arbitrage**: NBA point spreads, election markets, crypto derivatives
- **Market making**: Provide liquidity in thinly-traded markets
- **Real-time scalping**: Capture micro-movements in highly volatile markets

### Key Goals

1. **Exchange Agnostic**: Core trading logic works across any exchange via abstract interfaces
2. **Strategy Agnostic**: Infrastructure doesn't know about specific trading strategies
3. **Type Safe**: No dicts — all data structures are typed dataclasses
4. **High Reliability**: WebSocket reconnection, position reconciliation, circuit breakers
5. **Real-Time Performance**: Sub-second order execution, microsecond price updates

---

## Core Design Principles

### 1. Interface-First Design

**Every component has an abstract interface** (`I_*` prefix):

```
I_ExchangeClient → KalshiExchangeClient, PolymarketExchangeClient
I_OrderManager   → KalshiOrderManager
I_Scanner        → KalshiScanner
I_Strategy       → CryptoScalpStrategy, NBAUnderdogStrategy
I_Market         → KalshiMarket
```

**Why?**
- **Testability**: Mock interfaces for unit tests
- **Flexibility**: Swap implementations without changing strategy code
- **Clarity**: Contract is explicit in interface, not scattered across implementation

### 2. Dependency Injection

Strategies receive dependencies via constructor, not global singletons:

```python
class CryptoScalpStrategy(I_Strategy):
    def __init__(
        self,
        exchange_client: I_ExchangeClient,  # ← Injected
        config: CryptoScalpConfig,
        dry_run: bool = False
    ):
        self._client = exchange_client
        self._om = KalshiOrderManager(exchange_client)  # ← Create with injected client
```

**Benefits**:
- Strategies don't own exchange connections (main.py does)
- Multiple strategies can share one client
- Easy to test with mock clients

### 3. No Dicts — Use Dataclasses

**Bad (fragile):**
```python
signal = {"side": "yes", "price": 65, "reason": "spot delta"}
```

**Good (typed):**
```python
@dataclass
class Signal:
    side: Side
    target_price_cents: int
    strength: float
    reason: str
```

### 4. Separate Types from Implementations

Each component has a `*_types.py` file containing **only dataclasses**:

```
core/exchange_client/
├── exchange_client_types.py  # ExchangeStatus (shared)
├── i_exchange_client.py      # Abstract interface
└── kalshi/
    ├── kalshi_types.py        # KalshiBalance, KalshiPosition
    └── kalshi_client.py       # Uses types, doesn't define them
```

---

## System Component Hierarchy

```
                        ┌─────────────────────────┐
                        │      MrClean CLI        │
                        │      (main.py)          │
                        └───────────┬─────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
            ┌───────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐
            │  I_Strategy  │ │ I_Scanner │ │ I_Exchange  │
            │  Interface   │ │ Interface │ │   Client    │
            └───────┬──────┘ └─────┬─────┘ └──────┬──────┘
                    │               │               │
        ┌───────────┼───────────────┼───────────────┘
        │           │               │
┌───────▼──────┐ ┌─▼───────────┐ ┌─▼────────────┐
│ CryptoScalp  │ │ KalshiScanner│ │ KalshiClient │
│  Strategy    │ │              │ │   (REST API) │
└───────┬──────┘ └──────────────┘ └──────┬───────┘
        │                                  │
        │         ┌────────────────────────┤
        │         │                        │
┌───────▼─────────▼──┐         ┌──────────▼──────────┐
│  I_OrderManager    │         │  KalshiWebSocket    │
│    Interface       │         │  (async streams)    │
└────────┬───────────┘         └─────────────────────┘
         │
┌────────▼───────────┐
│ KalshiOrderManager │
│  - Submit orders   │
│  - Track fills     │
│  - WebSocket fills │
└────────────────────┘
```

### Component Ownership

| Component | Owner | Lifecycle |
|-----------|-------|-----------|
| `I_ExchangeClient` | main.py | Created once, shared across strategies |
| `I_OrderManager` | Strategy | Created in strategy `__init__`, one per strategy |
| `I_Scanner` | main.py | Created once, passed to strategies |
| `I_Strategy` | main.py | Created per run, destroyed on exit |
| `OrderBookManager` | Strategy | Created in strategy, manages orderbook state |
| `TradingState` | Singleton | Global coordination (pause background tasks) |

---

## Core Components

### 1. I_ExchangeClient

**Purpose**: Abstract interface for exchange API access (REST + WebSocket)

**Interface Definition**: `/Users/raine/tradingutils/core/exchange_client/i_exchange_client.py`

**Key Methods**:
```python
class I_ExchangeClient(ABC):
    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection and verify authentication."""

    @abstractmethod
    async def get_markets(self, series_ticker: Optional[str] = None) -> List[Any]:
        """Get markets matching filters."""

    @abstractmethod
    async def get_balance(self) -> Any:
        """Get account balance."""

    @abstractmethod
    async def get_positions(self, ticker: Optional[str] = None) -> List[Any]:
        """Get current positions."""
```

**Implementations**:
- **KalshiExchangeClient**: `/Users/raine/tradingutils/core/exchange_client/kalshi/kalshi_client.py`
  - REST API: `https://api.elections.kalshi.com`
  - WebSocket: `wss://api.elections.kalshi.com/trade-api/ws/v2`
  - Authentication: RSA-PSS signing (kalshi_auth.py)

**Usage Pattern**:
```python
# In main.py
client = KalshiExchangeClient.from_env()
await client.connect()

# Pass to strategy
strategy = CryptoScalpStrategy(exchange_client=client, ...)
```

---

### 2. I_OrderManager

**Purpose**: Order lifecycle management (submit, track, fill confirmation)

**Interface Definition**: `/Users/raine/tradingutils/core/order_manager/i_order_manager.py`

**Key Methods**:
```python
class I_OrderManager(ABC):
    @abstractmethod
    async def submit_order(self, request: OrderRequest) -> str:
        """Submit order, returns order_id."""

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Poll order status."""

    @abstractmethod
    async def get_fills(self, order_id: Optional[str] = None) -> List[Fill]:
        """Get fill events (real-time or historical)."""

    def get_position(self, ticker: str, side: Side) -> int:
        """Get current position (prevents buying both YES and NO)."""
```

**Implementations**:
- **KalshiOrderManager**: `/Users/raine/tradingutils/core/order_manager/kalshi_order_manager.py`
  - Lines 1-800: Order submission, tracking, position management
  - Lines 105-182: `initialize()` — CRITICAL startup sequence
  - Lines 600-750: WebSocket fill stream (real-time)

**Critical Features (Fixed March 2026)**:
1. **WebSocket Fill Detection** (lines 600-750)
   - Subscribes to `fill` channel on Kalshi WebSocket
   - Real-time fill notifications (no polling)
   - Callbacks: `_on_fill`, `_on_partial_fill`, `_on_rejected`

2. **Position Reconciliation** (lines 105-182)
   - `initialize()` recovers positions from recent fills on startup
   - Prevents stranded positions from crashed runs
   - Cancels all resting orders (clean slate)

3. **Opposite Side Protection** (lines 300-350)
   - Tracks positions by `(ticker, side)` tuple
   - Blocks buying YES when holding NO (and vice versa)
   - Prevents perfect hedges (guaranteed fee loss)

---

### 3. I_Scanner

**Purpose**: Market discovery and filtering

**Interface Definition**: `/Users/raine/tradingutils/scanner/i_scanner.py`

**Key Methods**:
```python
class I_Scanner(ABC):
    @abstractmethod
    async def scan_for_strategy(
        self,
        strategy: I_Strategy,
        series_ticker: Optional[str] = None,
        min_volume: int = 0
    ) -> List[ScanResult]:
        """Scan using strategy's market_filter."""
```

**Implementations**:
- **KalshiScanner**: `/Users/raine/tradingutils/scanner/kalshi_scanner.py`
  - Queries markets by series (KXBTC, KXNBAGAME, etc.)
  - Applies strategy's `market_filter()` to each market
  - Returns sorted by volume descending

---

### 4. I_Strategy

**Purpose**: Trading logic (signal generation, position management)

**Interface Definition**: `/Users/raine/tradingutils/strategies/i_strategy.py`

**Key Methods**:
```python
class I_Strategy(ABC):
    @abstractmethod
    def market_filter(self, market: Any) -> bool:
        """Filter for suitable markets."""

    @abstractmethod
    def get_signal(self, market: Any) -> Signal:
        """Generate trading signal."""

    @abstractmethod
    async def load_markets(self) -> None:
        """Load initial market data."""

    @abstractmethod
    async def refresh_markets(self) -> None:
        """Refresh orderbooks/prices."""

    @abstractmethod
    async def run(self) -> None:
        """Main strategy loop."""
```

**Implementations**:
- **CryptoScalpStrategy**: `/Users/raine/tradingutils/strategies/crypto_scalp/orchestrator.py` (lines 216-2500)
- **NBAUnderdogStrategy**: `/Users/raine/tradingutils/strategies/nba_underdog.py`
- **ScalpStrategy**: `/Users/raine/tradingutils/strategies/scalp_strategy.py`

---

### 5. OrderBookManager

**Purpose**: Real-time orderbook state (snapshots + deltas)

**Location**: `/Users/raine/tradingutils/core/market/orderbook_manager.py`

**Architecture**:
```python
class OrderBookManager:
    """Async-safe orderbook manager (uses asyncio.Lock)."""

    async def apply_snapshot(self, ticker: str, snapshot: dict) -> None:
        """Replace orderbook with full snapshot."""

    async def apply_delta(self, ticker: str, delta: dict) -> DeltaResult:
        """Apply incremental update (checks sequence numbers)."""

    def get_orderbook(self, ticker: str) -> Optional[OrderBookState]:
        """Get current orderbook state (sync, cached)."""
```

**Key Types**:
```python
@dataclass
class OrderBookState:
    ticker: str
    bids: List[OrderBookLevel]  # Sorted descending
    asks: List[OrderBookLevel]  # Sorted ascending
    sequence: int               # For gap detection

    @property
    def best_bid(self) -> Optional[OrderBookLevel]: ...
    @property
    def best_ask(self) -> Optional[OrderBookLevel]: ...
    @property
    def spread(self) -> Optional[int]: ...
```

**Usage** (in crypto scalp):
```python
# Get orderbook state
orderbook = self._orderbook_manager.get_orderbook(ticker)

# Check depth before entry
if orderbook and orderbook.best_ask.size >= 5:
    # Safe to enter
```

---

### 6. TradingState (Global Coordinator)

**Purpose**: Pause background tasks during active trading (rate limit management)

**Location**: `/Users/raine/tradingutils/core/trading_state.py`

**Singleton Pattern**:
```python
state = get_trading_state()

# In strategy
state.set_active(True)   # Pause background collectors
# ... execute trades ...
state.set_active(False)  # Resume

# In background collector
if state.should_pause():
    state.wait_while_paused(timeout=5.0)
```

**Key Methods**:
- `set_active(bool)`: Set trading active/inactive
- `should_pause()`: Check if background should pause
- `wait_while_paused(timeout)`: Block until trading inactive (with timeout)

---

## Crypto Scalp Strategy Architecture

### Overview

**Strategy**: Exploit 5-10 second lag between spot exchanges (Binance, Coinbase, Kraken) and Kalshi BTC prediction markets.

**File**: `/Users/raine/tradingutils/strategies/crypto_scalp/orchestrator.py`

**Components**:
1. **Detector** (`detector.py`): Spot price analysis, momentum checks
2. **Orchestrator** (`orchestrator.py`): Main loop, order execution, exit management
3. **Config** (`config.py`): All parameters (thresholds, timeouts, feeds)

---

### Thread Model (6 Threads + Main Loop)

```
┌─────────────────────────────────────────────────────────────┐
│                        MAIN PROCESS                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [MAIN EVENT LOOP] (asyncio, owns exchange_client)         │
│   ├─ Captures main loop in run()                           │
│   ├─ Processes orderbook queue every 100ms                 │
│   ├─ Queries balance every 60s                             │
│   └─ Handles async client calls from other threads         │
│                                                             │
│  [DETECTOR THREAD] (signal detection)                      │
│   ├─ Polls spot feeds every 100ms                          │
│   ├─ Detects price deltas > threshold                      │
│   ├─ Checks momentum (not decelerating)                    │
│   └─ Emits ScalpSignal to orchestrator                     │
│                                                             │
│  [EXIT THREAD] (position monitoring)                       │
│   ├─ Scans positions every 100ms                           │
│   ├─ Triggers exits on:                                    │
│   │   • Target time reached (exit_delay_sec)               │
│   │   • Hard exit time (max_hold_sec)                      │
│   │   • Stop-loss (adverse movement > 15¢)                 │
│   │   • Circuit breaker (daily loss limit)                 │
│   └─ Calls _place_exit() to submit orders                  │
│                                                             │
│  [SCANNER THREAD] (market discovery)                       │
│   ├─ Refreshes markets every 30s                           │
│   ├─ Filters by TTX (time to expiry)                       │
│   ├─ Fetches orderbook snapshots via queue                 │
│   └─ Updates _selected_markets list                        │
│                                                             │
│  [BINANCE WS THREAD] (spot price feed)                     │
│   ├─ WebSocket to wss://data-stream.binance.vision         │
│   ├─ Subscribes to btcusdt@trade                           │
│   ├─ Pushes trades to detector (~8 trades/sec)             │
│   └─ Reconnects on disconnect (60s timeout)                │
│                                                             │
│  [COINBASE WS THREAD] (spot price feed)                    │
│   ├─ WebSocket to wss://ws-feed.exchange.coinbase.com      │
│   ├─ Subscribes to BTC-USD matches                         │
│   ├─ Pushes trades to detector (~1.7 trades/sec)           │
│   └─ Reconnects on disconnect (60s timeout)                │
│                                                             │
│  [KALSHI WS THREAD] (orderbook updates)                    │
│   ├─ WebSocket to wss://api.elections.kalshi.com/...       │
│   ├─ Subscribes to orderbook_delta for each market         │
│   ├─ Pushes updates to _orderbook_queue (thread-safe)      │
│   └─ Reconnects on disconnect (exponential backoff)        │
│                                                             │
│  [DASHBOARD THREAD] (optional, live stats display)         │
│   └─ Prints P&L, positions, stats every 2s                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### Event Loop Design (Fixed March 3, 2026)

**Problem (Before Fix)**: 3 event loops caused race conditions, deadlocks

```
BEFORE (BROKEN):
  Main Loop (main.py)        → Owns exchange_client
  Scanner Loop (scanner thread) → Tried to call client (DEADLOCK!)
  WebSocket Loop (WS thread)    → Tried to apply_delta() (RACE!)
```

**Solution (After Fix)**: Single main loop + queue-based communication

```
AFTER (FIXED):
  Main Loop (main.py)
    └─ Owns exchange_client
    └─ Processes _orderbook_queue every 100ms
    └─ Executes async operations in proper context

  Scanner Thread
    └─ Pushes snapshot requests to queue (sync)

  WebSocket Thread
    └─ Pushes delta updates to queue (sync)
```

**Implementation** (`orchestrator.py` lines 427-464):

```python
async def _process_orderbook_queue(self) -> None:
    """Process orderbook updates from queue (called in main loop).

    FIX BUG #2: Queue-based approach eliminates cross-thread async calls.
    WebSocket thread pushes updates to queue (sync), main loop processes (async).
    """
    processed = 0
    while processed < 100:  # Limit per cycle
        try:
            update = self._orderbook_queue.get_nowait()
            update_type = update.get('type')
            ticker = update.get('ticker')

            if update_type == 'snapshot':
                await self._orderbook_manager.apply_snapshot(ticker, update['data'])
            elif update_type == 'delta':
                await self._orderbook_manager.apply_delta(ticker, update['data'])

            processed += 1
        except queue.Empty:
            break
```

---

### Queue-Based Communication Pattern

**Why Queues?**
- **Thread-safe**: `queue.Queue` uses locks internally
- **Non-blocking**: `get_nowait()` doesn't block main loop
- **Backpressure**: `maxsize=1000` prevents memory overflow
- **Async-safe**: Main loop processes in proper async context

**Key Queues**:

1. **Orderbook Queue** (`_orderbook_queue: queue.Queue`)
   - Producers: WebSocket thread, Scanner thread
   - Consumer: Main loop (`_process_orderbook_queue()`)
   - Payload: `{'type': 'snapshot'|'delta', 'ticker': str, 'data': dict}`

**Example Flow (Orderbook Delta)**:

```
[Kalshi WebSocket Thread]
  1. Receive delta message
  2. queue.put_nowait({'type': 'delta', 'ticker': 'KXBTC...', 'data': {...}})

[Main Loop] (100ms tick)
  3. await _process_orderbook_queue()
  4. update = queue.get_nowait()
  5. await orderbook_manager.apply_delta(ticker, data)
  6. Orderbook state updated (cached for sync access)
```

---

### WebSocket Feeds

#### 1. Binance Spot Feed

**URL**: `wss://data-stream.binance.vision/ws/btcusdt@trade`

**Subscription**:
```json
{
  "method": "SUBSCRIBE",
  "params": ["btcusdt@trade"],
  "id": 1
}
```

**Message Format**:
```json
{
  "e": "trade",
  "s": "BTCUSDT",
  "p": "95123.45",  // Price
  "q": "0.01234",   // Quantity
  "T": 1709410000000  // Timestamp
}
```

**Thread**: `_binance_ws_thread()` (lines 1500-1600)

**Rate**: ~8 trades/sec (highest frequency)

**Reconnection**: 60s timeout, infinite retries

---

#### 2. Coinbase Spot Feed

**URL**: `wss://ws-feed.exchange.coinbase.com`

**Subscription**:
```json
{
  "type": "subscribe",
  "product_ids": ["BTC-USD"],
  "channels": ["matches"]
}
```

**Message Format**:
```json
{
  "type": "match",
  "product_id": "BTC-USD",
  "price": "95123.45",
  "size": "0.01234",
  "time": "2026-03-02T21:52:20.123456Z"
}
```

**Thread**: `_coinbase_ws_thread()` (lines 1650-1750)

**Rate**: ~1.7 trades/sec

---

#### 3. Kalshi Orderbook Feed

**URL**: `wss://api.elections.kalshi.com/trade-api/ws/v2`

**Subscription** (per ticker):
```python
await ws.subscribe(Channel.ORDERBOOK_DELTA, ticker)
```

**Message Format (Delta)**:
```json
{
  "market_ticker": "KXBTC15M-26MAR020100-00",
  "price": 45,
  "delta": -3,  // Size change (negative = removed)
  "side": "yes",
  "ts": 1709410340000
}
```

**Message Format (Snapshot)** — Not used (event loop mismatch):
```python
# DISABLED due to cross-thread async issue
# await ws.get_orderbook_snapshot(ticker)
# Now: Fetch via REST API, push to queue
```

**Thread**: `_start_kalshi_ws()` (lines 1850-2000)

**Quirks**:
- **No sequence numbers**: Must synthesize (increment counter)
- **sid field**: Subscription ID, NOT ticker (use `market_ticker`)
- **Snapshot fetch**: Not supported (event loop mismatch), use REST fallback

---

### Data Flow: Entry Signal → Order → Fill → Position

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. SIGNAL DETECTION (Detector Thread)                           │
├──────────────────────────────────────────────────────────────────┤
│ Binance trade: $95,100 → $95,150 (+$50 in 2s)                   │
│   ├─ Detector: spot_delta = +$50 > min_spot_move ($20)          │
│   ├─ Momentum check: price still rising (not decelerating)      │
│   ├─ Kalshi market: KXBTC15M-26MAR020100-00 (strike $95,100)    │
│   └─ Signal: BUY YES @ ask (expect Kalshi to reprice up)        │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. ORDER SUBMISSION (Main Thread via _run_async)                │
├──────────────────────────────────────────────────────────────────┤
│ Check orderbook:                                                 │
│   ├─ best_ask = 29¢ @ 10 contracts                              │
│   ├─ Check exit depth: best_bid_size = 8 contracts              │
│   ├─ Depth check: 8 >= min_entry_bid_depth (5) ✓                │
│   └─ Liquidity OK to enter                                      │
│                                                                  │
│ Submit limit order:                                              │
│   ├─ ticker: KXBTC15M-26MAR020100-00                            │
│   ├─ side: YES, action: BUY, size: 1, limit: 29¢                │
│   ├─ OrderManager.submit_order(request)                         │
│   └─ order_id: "a1b2c3d4-..."                                   │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. FILL CONFIRMATION (OMS WebSocket or REST Polling)            │
├──────────────────────────────────────────────────────────────────┤
│ [WebSocket Path - Real-time] (FIXED Mar 3, 2026)                │
│   ├─ OMS.initialize() called in run() → WebSocket connected     │
│   ├─ Subscribed to 'fill' channel                               │
│   ├─ Receive fill message:                                      │
│   │   {"order_id": "a1b2c3d4", "count": 1, "price": 29}         │
│   ├─ Callback: _on_fill(order, fill)                            │
│   └─ Position tracking updated                                  │
│                                                                  │
│ [REST Path - Polling Fallback]                                  │
│   ├─ Poll every 200ms: await om.get_order_status(order_id)      │
│   ├─ Status: PENDING → FILLED                                   │
│   ├─ Fetch fills: await om.get_fills(order_id)                  │
│   └─ Extract fill price (ACTUAL, not limit!)                    │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. POSITION TRACKING (Orchestrator)                             │
├──────────────────────────────────────────────────────────────────┤
│ Create ScalpPosition:                                            │
│   ├─ ticker: KXBTC15M-26MAR020100-00                            │
│   ├─ side: YES                                                   │
│   ├─ entry_price_cents: 29                                      │
│   ├─ size: 1                                                     │
│   ├─ entry_time: time.time()                                    │
│   ├─ exit_target_time: entry_time + exit_delay_sec (10s)        │
│   ├─ hard_exit_time: entry_time + max_hold_sec (30s)            │
│   ├─ spot_delta: +$50 (trigger reason)                          │
│   └─ Entry depth metrics for intelligent exit                   │
│                                                                  │
│ Store position:                                                  │
│   └─ self._positions[ticker] = position                         │
└──────────────────────────────────────────────────────────────────┘
```

---

### Data Flow: Position → Exit Trigger → Order → Fill → P&L

```
┌──────────────────────────────────────────────────────────────────┐
│ 5. EXIT MONITORING (Exit Thread)                                │
├──────────────────────────────────────────────────────────────────┤
│ Scan positions every 100ms:                                      │
│   ├─ Check exit conditions for each position:                   │
│   │   • Target time reached? (now >= exit_target_time)          │
│   │   • Hard exit time? (now >= hard_exit_time)                 │
│   │   • Stop-loss triggered? (price moved -15¢)                 │
│   │   • Circuit breaker? (daily loss > max_daily_loss)          │
│   └─ Condition met → Call _place_exit(ticker, reason)           │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 6. EXIT ORDER SUBMISSION (Main Thread via _run_async)           │
├──────────────────────────────────────────────────────────────────┤
│ Get current market state:                                        │
│   ├─ Refresh orderbook via _async_scan_markets()                │
│   ├─ Current best_bid: 25¢ @ 5 contracts                        │
│   └─ Position entry: 29¢                                        │
│                                                                  │
│ Submit exit order (SELL):                                        │
│   ├─ ticker: KXBTC15M-26MAR020100-00                            │
│   ├─ side: YES, action: SELL, size: 1, limit: 25¢               │
│   ├─ exit_order_id = await om.submit_order(request)             │
│   └─ order_id: "e5f6g7h8-..."                                   │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 7. EXIT FILL CONFIRMATION (FIXED Mar 3, 2026 - Bug #1)          │
├──────────────────────────────────────────────────────────────────┤
│ [BEFORE FIX - BROKEN]                                            │
│   ├─ Submitted exit order @ 25¢ limit                           │
│   ├─ _record_exit() called IMMEDIATELY (no wait!)               │
│   ├─ Assumed fill at 25¢ (WRONG - could be 0¢!)                 │
│   └─ Position removed from tracking (STRANDED!)                 │
│                                                                  │
│ [AFTER FIX - CORRECT]                                            │
│   ├─ Submit exit order @ 25¢ limit                              │
│   ├─ WAIT for fill via _wait_for_fill_om(order_id, timeout=3s)  │
│   ├─ If filled:                                                  │
│   │   ├─ Fetch actual fill price from OMS                       │
│   │   ├─ fills = await om.get_fills(order_id)                   │
│   │   ├─ actual_price = fills[0].price_cents (e.g., 23¢)        │
│   │   └─ _record_exit(ticker, position, actual_price, order_id) │
│   └─ If not filled:                                              │
│       ├─ Log error                                               │
│       ├─ Keep position in tracking                              │
│       └─ Retry exit (or force market order)                     │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 8. P&L CALCULATION (FIXED Mar 3, 2026 - Bug #6, #7)             │
├──────────────────────────────────────────────────────────────────┤
│ [BEFORE FIX - WRONG P&L]                                         │
│   ├─ Entry: 29¢, Exit: 25¢ (LIMIT, not FILL!)                   │
│   ├─ Gross: 25 - 29 = -4¢                                       │
│   ├─ Fee: 0¢ (no fee on losses)                                 │
│   ├─ Net: -4¢                                                    │
│   └─ Logged P&L: -4¢ (WRONG! Actual was -28¢!)                  │
│                                                                  │
│ [AFTER FIX - CORRECT P&L]                                        │
│   ├─ Entry: 29¢ (from fill record)                              │
│   ├─ Exit: 23¢ (ACTUAL fill, not limit!)                        │
│   ├─ Gross: 23 - 29 = -6¢                                       │
│   ├─ Entry fee: 2¢ (7% of 29¢, always charged)                  │
│   ├─ Exit fee: 0¢ (no fee on losses)                            │
│   ├─ Net: -6¢ - 2¢ = -8¢                                        │
│   └─ Logged P&L: -8¢ (CORRECT!)                                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Design Patterns

### 1. Interface-First Design

**Pattern**: Define abstract interface first, implement later

**Example** (`I_Strategy` → `CryptoScalpStrategy`):

```python
# strategies/i_strategy.py
class I_Strategy(ABC):
    @abstractmethod
    def get_signal(self, market: Any) -> Signal:
        """Get trading signal for a market."""
        pass

# strategies/crypto_scalp/orchestrator.py
class CryptoScalpStrategy(I_Strategy):
    def get_signal(self, market: Any) -> Signal:
        # Convert internal ScalpSignal to I_Strategy Signal
        scalp_signal = self._detector.detect(market, orderbook)
        if not scalp_signal:
            return Signal.no_signal("No scalp opportunity")

        side = Side.YES if scalp_signal.side == "yes" else Side.NO
        return Signal.buy(
            side=side,
            price_cents=scalp_signal.entry_price_cents,
            strength=1.0,
            reason=f"Spot delta ${scalp_signal.spot_delta:.1f}"
        )
```

**Benefits**:
- Strategies can be swapped without changing main.py
- Easy to add new exchanges (just implement I_ExchangeClient)
- Clear contract between components

---

### 2. Dependency Injection

**Pattern**: Pass dependencies via constructor, not globals

**Example** (Strategy depends on ExchangeClient):

```python
# main.py
client = KalshiExchangeClient.from_env()
await client.connect()

strategy = CryptoScalpStrategy(
    exchange_client=client,  # ← Injected
    config=config,
    dry_run=args.dry_run
)

# Strategy doesn't create its own client
# Strategy can't run without a client (compile-time safety)
```

**Benefits**:
- Testable (inject mocks)
- No hidden globals
- Lifecycle managed by caller (main.py)

---

### 3. Queue-Based Thread Communication

**Pattern**: Threads push to queue (sync), main loop processes (async)

**Example** (Orderbook WebSocket → Main Loop):

```python
# WebSocket thread (sync context)
def on_delta_message(msg):
    self._orderbook_queue.put_nowait({
        'type': 'delta',
        'ticker': msg['market_ticker'],
        'data': msg
    })

# Main loop (async context)
async def _process_orderbook_queue(self):
    while True:
        try:
            update = self._orderbook_queue.get_nowait()
            if update['type'] == 'delta':
                await self._orderbook_manager.apply_delta(
                    update['ticker'],
                    update['data']
                )
        except queue.Empty:
            break
```

**Benefits**:
- Thread-safe (queue.Queue has internal locks)
- No event loop mismatch
- Backpressure control (maxsize)
- Non-blocking (get_nowait)

---

### 4. Process Lock Pattern

**Pattern**: Prevent multiple instances with file-based lock

**Example** (Crypto scalp startup):

```python
# strategies/crypto_scalp/orchestrator.py lines 241-259
LOCKFILE = Path("/tmp/crypto_scalp.lock")

if LOCKFILE.exists():
    existing_pid = LOCKFILE.read_text().strip()
    raise RuntimeError(
        f"Another instance is running (PID {existing_pid}). "
        f"Delete {LOCKFILE} manually if incorrect."
    )

# Acquire lock
LOCKFILE.write_text(str(os.getpid()))
atexit.register(lambda: LOCKFILE.unlink(missing_ok=True))
```

**Benefits**:
- Prevents duplicate entries (same signal detected twice)
- Prevents position tracking conflicts
- Auto-cleanup on normal exit (atexit)
- Manual recovery if crashed (delete lock file)

---

## Critical Bug Fixes (March 2026)

### Context: March 1 Live Trading Session

**Result**: -$6.00 loss (4/5 entries failed, 1 exit unconfirmed)

**Root Cause Analysis**: 10 critical bugs identified

**Fix Timeline**:
- March 2: Investigation, bug identification
- March 3: P0 fixes implemented (4 bugs, 70 min)
- March 3: P1 fixes implemented (3 bugs, 14 hours)
- March 3: Ready for 8-hour integration test

---

### P0 Fixes (Critical — Must Fix Before Trading)

#### Bug #1: Exit Fills Not Confirmed 🚨

**Impact**: Stranded positions (exit order submitted but never confirmed)

**Location**: `orchestrator.py:2053` (`_place_exit`)

**Before (Broken)**:
```python
# Line 2050: Submit exit order
exit_order_id = self._run_async(self._om.submit_order(request))

# Line 2053: IMMEDIATELY record exit (NO CONFIRMATION!)
self._record_exit(ticker, position, actual_exit_price, exit_order_id)
```

**Problem**:
1. Exit order submitted at 25¢ limit
2. `_record_exit()` called immediately (assumes fill)
3. Position removed from `self._positions` dict
4. If order doesn't fill → **stranded position on exchange**

**After (Fixed)**:
```python
# Submit exit order
exit_order_id = self._run_async(self._om.submit_order(request))

# WAIT for fill confirmation
filled = self._run_async(
    self._wait_for_fill_om(exit_order_id, ticker, timeout=3.0)
)

if filled:
    # RETRIEVE ACTUAL FILL PRICE
    fills = await self._om.get_fills(exit_order_id)
    actual_fill_price = fills[0].price_cents  # ← ACTUAL, not limit!
    self._record_exit(ticker, position, actual_fill_price, exit_order_id)
else:
    logger.error("EXIT FAILED TO FILL: %s order %s", ticker, exit_order_id)
    # Keep position in tracking, retry exit
```

**Files Changed**: `orchestrator.py:2050-2080`

---

#### Bug #3: OMS WebSocket Not Initialized 🚨

**Impact**: Real-time fill detection unavailable → slow REST polling → missed exits

**Location**: `orchestrator.py:409-417` (`run` method)

**Before (Broken)**:
```python
# OMS created but initialize() NEVER CALLED
self._om = KalshiOrderManager(exchange_client)

# WebSocket fill stream never started
# Position reconciliation never performed
# Stale orders never canceled
```

**After (Fixed)**:
```python
# In run() method:
if not self._config.paper_mode and self._om:
    logger.info("Initializing OMS (WebSocket fill stream)...")
    try:
        await self._om.initialize()
        logger.info("✓ OMS initialized with real-time fills")
    except Exception as e:
        logger.error(f"OMS initialization failed: {e}")
        logger.warning("Falling back to REST API polling for fills")
```

**What `initialize()` Does**:
1. Cancels ALL resting orders from previous runs (clean slate)
2. Recovers positions from recent fills (prevents stranded positions)
3. Starts WebSocket fill stream (real-time order confirmations)
4. Starts order age sweeper (cancels orders > max_age_seconds)

**Files Changed**: `orchestrator.py:476-483`

---

#### Bug #8: No Balance Tracking 🚨

**Impact**: Daily loss limit not enforced → unlimited losses

**Location**: `orchestrator.py:2203-2301` (new `_check_balance_and_circuit_breaker`)

**Before (Broken)**:
```python
# Balance NEVER queried
# Circuit breaker checks daily_loss_cents but never updates it
# $6 loss went completely undetected
```

**After (Fixed)**:
```python
async def _check_balance_and_circuit_breaker(self) -> None:
    """Query balance, update daily loss, trigger circuit breaker if needed.

    Called every 60s in main loop.
    """
    if self._config.paper_mode:
        return

    now = time.time()
    if now - self._last_balance_check < 60:
        return  # Throttle to 1/min

    try:
        # Query Kalshi balance
        balance = await self._client.get_balance()
        current_balance = balance.balance_cents

        # Calculate loss since session start
        if self._initial_balance_cents is not None:
            realized_loss = self._initial_balance_cents - current_balance
            self._stats.daily_loss_cents = realized_loss

            # Circuit breaker check
            if realized_loss >= self._config.max_daily_loss_cents:
                logger.critical(
                    f"🔴 CIRCUIT BREAKER TRIGGERED! "
                    f"Loss ${realized_loss/100:.2f} >= ${self._config.max_daily_loss_cents/100:.2f}"
                )
                self.stop()  # Halt all trading

        self._last_balance_check = now
        self._last_balance_cents = current_balance

    except Exception as e:
        logger.error(f"Balance check failed: {e}")
```

**Features**:
- Queries balance every 60s (rate limit friendly)
- Compares to `_initial_balance_cents` (set in `run()`)
- Updates `_stats.daily_loss_cents` (used by exit thread)
- Triggers circuit breaker if loss >= `max_daily_loss_cents`
- Logs balance changes for visibility

**Files Changed**: `orchestrator.py:2203-2301, 485-494, 541-543`

---

#### Bug #9: No Position Reconciliation 🚨

**Impact**: Stranded positions from crashed runs go undetected

**Location**: `orchestrator.py:497-514` (`run` method)

**Before (Broken)**:
```python
# Strategy starts with empty position tracking
# Crashed runs leave positions on exchange
# No reconciliation on startup
```

**After (Fixed)**:
```python
# In run() method:
if not self._config.paper_mode:
    try:
        logger.info("Reconciling positions with Kalshi...")
        positions = await self._client.get_positions()

        for pos in positions:
            ticker = pos.ticker
            side = Side.YES if pos.position > 0 else Side.NO
            qty = abs(pos.position)

            if qty > 0:
                logger.warning(
                    f"⚠️  Stranded position detected: {ticker} {side.value} × {qty}"
                )

                # Add to position tracking
                # (exit thread will manage from here)
                # ... create ScalpPosition ...

        logger.info(f"✓ Position reconciliation complete ({len(positions)} positions)")

    except Exception as e:
        logger.error(f"Position reconciliation failed: {e}")
```

**Features**:
- Queries `get_positions()` on startup
- Compares exchange positions to internal `_positions` dict
- Logs stranded positions (positions not in tracking)
- Optionally adds to tracking for auto-exit
- Prevents "zombie" positions from accumulating

**Files Changed**: `orchestrator.py:497-514`

---

### P1 Fixes (High Priority — Reliability)

#### Bug #2: Orderbook WebSocket Unreliable 🏗️

**Impact**: 80% entry failure (4/5 trades), market orders skipped

**Root Cause**: Event loop mismatch (cross-thread async calls)

**Location**: `orchestrator.py:427-464, 850-950, 1850-2000`

**Before (Broken)**:
```
WebSocket Thread (event loop A)
  → asyncio.create_task(apply_delta(ticker, delta))  ← WRONG LOOP!
  → Fails silently, orderbook never updates

Scanner Thread (event loop B)
  → asyncio.run(apply_snapshot(ticker, snapshot))  ← Creates temp loop!
  → Race condition with main loop
```

**After (Fixed)**:
```
WebSocket Thread
  → queue.put_nowait({'type': 'delta', 'ticker': ..., 'data': ...})  ← Thread-safe!

Scanner Thread
  → queue.put_nowait({'type': 'snapshot', 'ticker': ..., 'data': ...})  ← Thread-safe!

Main Loop (every 100ms)
  → await _process_orderbook_queue()
  → for update in queue:
      → await orderbook_manager.apply_snapshot() / apply_delta()  ← Proper async context!
```

**Implementation**:

1. **Queue Infrastructure** (lines 283-284):
```python
self._orderbook_queue: queue.Queue = queue.Queue(maxsize=1000)
```

2. **Queue Processor** (lines 427-464):
```python
async def _process_orderbook_queue(self) -> None:
    """Process orderbook updates from queue (runs in main loop)."""
    processed = 0
    while processed < 100:
        try:
            update = self._orderbook_queue.get_nowait()
            ticker = update['ticker']
            if update['type'] == 'snapshot':
                await self._orderbook_manager.apply_snapshot(ticker, update['data'])
            elif update['type'] == 'delta':
                await self._orderbook_manager.apply_delta(ticker, update['data'])
            processed += 1
        except queue.Empty:
            break
```

3. **WebSocket Push** (lines 1900-1920):
```python
def on_delta(msg):
    self._orderbook_queue.put_nowait({
        'type': 'delta',
        'ticker': msg['market_ticker'],
        'data': msg
    })
```

4. **Scanner Push** (lines 850-870):
```python
# Fetch snapshot via REST
snapshot = self._run_async(self._client.request_market(ticker))

# Push to queue (don't apply directly)
self._orderbook_queue.put_nowait({
    'type': 'snapshot',
    'ticker': ticker,
    'data': snapshot['orderbook']
})
```

**Files Changed**: `orchestrator.py:283-284, 427-464, 850-950, 1900-1920`

---

#### Bug #4: Event Loop Architecture Flaw 🏗️

**Impact**: Deadlocks, race conditions, fragile cross-thread async

**Root Cause**: 3 event loops competing for same async client

**Before (Broken)**:
```
Main Loop (main.py)
  └─ Owns exchange_client (created in main.py)

Scanner Loop (scanner thread)
  └─ Tries to call client.request_market() → DEADLOCK!

WebSocket Loop (WS thread)
  └─ Tries to apply_delta() → RACE CONDITION!
```

**After (Fixed)**:
```
Main Loop (main.py)
  └─ Owns exchange_client
  └─ Processes all async operations via queue

Scanner Thread (no event loop)
  └─ Pushes work to main loop via queue.put_nowait()

WebSocket Thread (isolated event loop)
  └─ WebSocket has own loop (only for WS messages)
  └─ Pushes orderbook updates to queue (sync)
```

**Implementation**:

1. **Capture Main Loop** (lines 466-472):
```python
async def run(self) -> None:
    # Capture the main event loop (the one that owns the client)
    try:
        self._main_loop = asyncio.get_running_loop()
        logger.info("Captured main event loop for cross-thread async calls")
    except RuntimeError:
        logger.warning("No running event loop detected")
```

2. **Remove Scanner Loop** (lines 314-315):
```python
# REMOVED: self._scanner_loop
# Scanner thread now uses _run_async() to delegate to main loop
```

3. **WebSocket Isolation** (lines 1850-2000):
```python
def _start_kalshi_ws(self):
    """WebSocket thread with isolated event loop (only for WS messages)."""
    # Create new loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def ws_loop():
        # WebSocket runs in this loop
        # Orderbook updates pushed to queue (sync, no cross-thread async)
        ...

    loop.run_until_complete(ws_loop())
```

4. **Cross-Thread Async Helper** (lines 2400-2420):
```python
def _run_async(self, coro):
    """Run coroutine in main event loop (from any thread)."""
    if self._main_loop and self._main_loop.is_running():
        # Main loop exists and running → use it
        future = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
        return future.result(timeout=10.0)
    else:
        # Fallback: create temporary loop (not ideal, but works)
        return asyncio.run(coro)
```

**Files Changed**: `orchestrator.py:466-472, 314-315, 1850-2000, 2400-2420`

---

#### Bug #5: No WebSocket Reconnection 🔌

**Impact**: Single disconnect → permanent failure → no more orderbook updates

**Location**: `orchestrator.py:1850-2000` (Kalshi WS), `1500-1600` (Binance WS), `1650-1750` (Coinbase WS)

**Before (Broken)**:
```python
# Single connection attempt
ws = KalshiWebSocket(...)
await ws.connect()
await ws.subscribe(...)

# If disconnect happens:
#   → WebSocket dead forever
#   → No orderbook updates
#   → Strategy blind
```

**After (Fixed)**:
```python
async def _kalshi_ws_loop(self):
    """Kalshi WebSocket with reconnection loop."""
    reconnect_delay = 1.0
    max_delay = 60.0

    while self._running:
        try:
            # Create WebSocket
            ws = KalshiWebSocket(...)
            await ws.connect()

            # Subscribe to all tickers
            for ticker in self._selected_markets:
                await ws.subscribe(Channel.ORDERBOOK_DELTA, ticker)

            logger.info(f"✓ Kalshi WebSocket connected")
            reconnect_delay = 1.0  # Reset backoff

            # Message loop
            async for msg in ws:
                # Process message
                self._orderbook_queue.put_nowait({...})

        except Exception as e:
            logger.error(f"Kalshi WebSocket error: {e}")
            logger.info(f"Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)

            # Exponential backoff
            reconnect_delay = min(reconnect_delay * 2, max_delay)
```

**Features**:
- Infinite reconnection loop (while `_running`)
- Exponential backoff (1s → 2s → 4s → ... → 60s max)
- Re-subscribes to all tickers on reconnect
- Logs disconnects and reconnections
- Resets backoff on successful connection

**Files Changed**: `orchestrator.py:1850-2000, 1500-1600, 1650-1750`

---

## Data Flow Diagrams

### Market Discovery Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. SCANNER THREAD                                           │
├─────────────────────────────────────────────────────────────┤
│ Every 30s:                                                  │
│   ├─ await client.get_markets(series="KXBTC")              │
│   ├─ Filter by TTX: 300s < time_to_expiry < 1800s          │
│   ├─ For each market:                                       │
│   │   ├─ Fetch orderbook snapshot (REST API)               │
│   │   └─ Push to _orderbook_queue                          │
│   └─ Update _selected_markets list                         │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. MAIN LOOP (every 100ms)                                 │
├─────────────────────────────────────────────────────────────┤
│   ├─ await _process_orderbook_queue()                      │
│   ├─ For each snapshot in queue:                           │
│   │   └─ await orderbook_manager.apply_snapshot(...)       │
│   └─ Orderbook state updated (cached for sync access)      │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. KALSHI WEBSOCKET THREAD                                 │
├─────────────────────────────────────────────────────────────┤
│   ├─ Subscribe to orderbook_delta for each ticker          │
│   ├─ Receive delta messages:                               │
│   │   {"market_ticker": "...", "price": 45, "delta": -3}   │
│   └─ Push to _orderbook_queue                              │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. MAIN LOOP (every 100ms)                                 │
├─────────────────────────────────────────────────────────────┤
│   ├─ await _process_orderbook_queue()                      │
│   ├─ For each delta in queue:                              │
│   │   └─ await orderbook_manager.apply_delta(...)          │
│   └─ Orderbook state incrementally updated                 │
└─────────────────────────────────────────────────────────────┘
```

---

### Signal Detection Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. BINANCE WEBSOCKET THREAD                                │
├─────────────────────────────────────────────────────────────┤
│ Receive trade: {"p": "95150", "T": 1709410123456}          │
│   └─ Push to detector: detector.on_binance_trade(price, ts)│
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. DETECTOR THREAD (every 100ms)                           │
├─────────────────────────────────────────────────────────────┤
│ For each selected market:                                  │
│   ├─ Get spot price history (last 30s)                     │
│   ├─ Calculate delta: current - 30s_ago                    │
│   ├─ Check momentum: not decelerating                      │
│   ├─ Check threshold: abs(delta) > min_spot_move ($20)     │
│   ├─ Get Kalshi orderbook state                            │
│   ├─ Determine side: delta > 0 → BUY YES, delta < 0 → BUY NO│
│   ├─ Calculate entry price: current ask                    │
│   └─ Emit ScalpSignal to orchestrator                      │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. ORCHESTRATOR (signal received)                          │
├─────────────────────────────────────────────────────────────┤
│ Check cooldown:                                             │
│   ├─ if ticker in _cooldowns and now < cooldown_end:       │
│   │   └─ Skip (cooldown active)                            │
│   └─ else: proceed                                          │
│                                                             │
│ Check duplicate entry:                                      │
│   ├─ if ticker in _pending_entries:                        │
│   │   └─ Skip (entry already pending)                      │
│   └─ else: proceed                                          │
│                                                             │
│ Check position limit:                                       │
│   ├─ if len(_positions) >= max_positions:                  │
│   │   └─ Skip (too many open positions)                    │
│   └─ else: proceed                                          │
│                                                             │
│ Check entry liquidity (FIX: Crash Protection):              │
│   ├─ Get orderbook state                                   │
│   ├─ exit_side = opposite of entry_side                    │
│   ├─ exit_depth = orderbook.best_bid.size (if entering YES)│
│   ├─ if exit_depth < min_entry_bid_depth (5):              │
│   │   └─ Skip (illiquid, can't exit later)                 │
│   └─ else: proceed to entry                                │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. ORDER SUBMISSION (see Order Execution Flow)             │
└─────────────────────────────────────────────────────────────┘
```

---

### Order Execution Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. ENTRY ORDER SUBMISSION                                  │
├─────────────────────────────────────────────────────────────┤
│ Create OrderRequest:                                        │
│   ├─ ticker: KXBTC15M-26MAR020100-00                       │
│   ├─ side: YES, action: BUY, size: 1                       │
│   ├─ type: LIMIT, limit_price: 29¢                         │
│   └─ idempotency_key: "oms-{uuid}-{timestamp}"             │
│                                                             │
│ Submit via OrderManager:                                    │
│   ├─ order_id = await om.submit_order(request)             │
│   ├─ Add to _pending_entries set                           │
│   └─ Wait for fill (via _wait_for_fill_om)                 │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. FILL DETECTION (OMS)                                    │
├─────────────────────────────────────────────────────────────┤
│ [WebSocket Path - Real-time]                               │
│   ├─ OMS WebSocket subscribed to 'fill' channel            │
│   ├─ Receive fill message from Kalshi:                     │
│   │   {"order_id": "...", "count": 1, "price": 29}         │
│   ├─ Callback: _on_fill(order, fill)                       │
│   ├─ Update position tracking: _positions[(ticker, side)]  │
│   └─ Return success to _wait_for_fill_om                   │
│                                                             │
│ [REST Path - Polling Fallback]                             │
│   ├─ Poll every 200ms: await om.get_order_status(order_id) │
│   ├─ Status: PENDING → RESTING → FILLED                    │
│   ├─ Once FILLED: await om.get_fills(order_id)             │
│   └─ Extract fill price and return                         │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. POSITION CREATION                                       │
├─────────────────────────────────────────────────────────────┤
│ Create ScalpPosition:                                       │
│   ├─ ticker, side, entry_price_cents, size                 │
│   ├─ entry_time = time.time()                              │
│   ├─ exit_target_time = entry_time + exit_delay_sec (10s)  │
│   ├─ hard_exit_time = entry_time + max_hold_sec (30s)      │
│   ├─ spot_delta (trigger reason)                           │
│   └─ Entry depth metrics (for intelligent exit)            │
│                                                             │
│ Store position:                                             │
│   ├─ self._positions[ticker] = position                    │
│   ├─ Remove from _pending_entries                          │
│   ├─ Add to _cooldowns (prevent immediate re-entry)        │
│   └─ Log: "ENTRY CONFIRMED: ticker @ price¢"               │
└─────────────────────────────────────────────────────────────┘
```

---

### Exit Decision Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. EXIT THREAD (every 100ms)                               │
├─────────────────────────────────────────────────────────────┤
│ For each position in _positions:                           │
│   ├─ Get current time                                      │
│   ├─ Check exit conditions:                                │
│   │                                                         │
│   ├─ [TARGET TIME]                                         │
│   │   if now >= position.exit_target_time:                 │
│   │     → Trigger exit (reason: "target time")             │
│   │                                                         │
│   ├─ [HARD EXIT TIME]                                      │
│   │   if now >= position.hard_exit_time:                   │
│   │     → Trigger exit (reason: "hard exit")               │
│   │                                                         │
│   ├─ [STOP-LOSS] (Crash Protection)                        │
│   │   Get current market price (orderbook best_bid/ask)    │
│   │   Calculate unrealized P&L:                            │
│   │     pnl = current_price - entry_price                  │
│   │   if abs(pnl) >= stop_loss_cents (15¢):                │
│   │     if pnl < 0:  # Adverse movement                    │
│   │       → Trigger IMMEDIATE exit (reason: "stop-loss")   │
│   │                                                         │
│   ├─ [CIRCUIT BREAKER]                                     │
│   │   if stats.daily_loss_cents >= max_daily_loss_cents:   │
│   │     → Trigger exit ALL positions (reason: "circuit")   │
│   │     → Call self.stop() to halt strategy                │
│   │                                                         │
│   └─ If condition met:                                     │
│       └─ Call _place_exit(ticker, reason)                  │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. EXIT ORDER SUBMISSION                                   │
├─────────────────────────────────────────────────────────────┤
│ Get current orderbook:                                      │
│   ├─ orderbook = orderbook_manager.get_orderbook(ticker)   │
│   ├─ exit_price = best_bid if side=YES else best_ask       │
│   └─ Check spread (warn if > 10¢)                          │
│                                                             │
│ Create OrderRequest:                                        │
│   ├─ ticker, side, action=SELL, size                       │
│   ├─ type: LIMIT, limit_price: exit_price                  │
│   └─ idempotency_key: "exit-{uuid}-{timestamp}"            │
│                                                             │
│ Submit via OrderManager:                                    │
│   ├─ exit_order_id = await om.submit_order(request)        │
│   └─ Wait for fill (via _wait_for_fill_om, timeout=3s)     │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. FILL CONFIRMATION (CRITICAL - FIX #1)                   │
├─────────────────────────────────────────────────────────────┤
│ [BEFORE FIX - BROKEN]                                       │
│   ├─ _record_exit() called IMMEDIATELY (no wait)           │
│   ├─ Used limit_price (25¢) instead of actual fill         │
│   └─ Position removed (STRANDED if didn't fill!)           │
│                                                             │
│ [AFTER FIX - CORRECT]                                       │
│   ├─ filled = await _wait_for_fill_om(exit_order_id, 3s)   │
│   ├─ If filled:                                             │
│   │   ├─ fills = await om.get_fills(exit_order_id)         │
│   │   ├─ actual_price = fills[0].price_cents               │
│   │   └─ _record_exit(ticker, position, actual_price, ...)  │
│   └─ If not filled:                                         │
│       ├─ Log error                                          │
│       ├─ Keep position in tracking                         │
│       └─ Retry exit or force market order                  │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. P&L CALCULATION (FIXED - Bug #6, #7)                    │
├─────────────────────────────────────────────────────────────┤
│ Calculate gross P&L:                                        │
│   ├─ gross_pnl = exit_price - entry_price                  │
│   └─ Example: 23¢ - 29¢ = -6¢                              │
│                                                             │
│ Calculate fees (FIXED: always charge entry fee):           │
│   ├─ entry_fee = entry_price * 0.07 (rounded up)           │
│   ├─ exit_fee = max(0, gross_pnl) * 0.07 (only on profit)  │
│   └─ Example: entry_fee = 2¢, exit_fee = 0¢                │
│                                                             │
│ Calculate net P&L:                                          │
│   ├─ net_pnl = gross_pnl - entry_fee - exit_fee            │
│   └─ Example: -6¢ - 2¢ - 0¢ = -8¢                          │
│                                                             │
│ Update stats:                                               │
│   ├─ stats.trades_exited += 1                              │
│   ├─ stats.total_pnl_cents += net_pnl                      │
│   ├─ if net_pnl > 0: stats.trades_won += 1                 │
│   └─ position.pnl_cents = net_pnl                          │
│                                                             │
│ Remove position:                                            │
│   ├─ del self._positions[ticker]                           │
│   ├─ Add to _trade_log for analysis                        │
│   └─ Log: "EXIT FILLED: ticker @ price¢ (P&L: +/-X¢)"      │
└─────────────────────────────────────────────────────────────┘
```

---

## Critical Code Paths

### Entry Path (Line Numbers)

**File**: `strategies/crypto_scalp/orchestrator.py`

1. **Signal Detection** (Detector Thread)
   - Lines 1300-1400: `_detector_loop()` polls spot feeds every 100ms
   - Lines 1350-1370: Calls `detector.detect(market, orderbook)` → `ScalpSignal`

2. **Entry Decision** (Main Thread via `_run_async`)
   - Lines 1100-1150: Check cooldown, duplicate entry, position limit
   - Lines 228-248: **CRASH PROTECTION** — Check entry liquidity (min_entry_bid_depth)

3. **Order Submission**
   - Lines 1200-1250: Create `OrderRequest`, call `om.submit_order()`
   - Lines 1250-1280: Wait for fill via `_wait_for_fill_om()`

4. **Fill Confirmation**
   - Lines 1280-1320: Poll order status (REST) or WebSocket callback
   - Lines 600-750: OMS WebSocket fill stream (KalshiOrderManager)

5. **Position Creation**
   - Lines 1320-1380: Create `ScalpPosition`, store in `_positions` dict
   - Lines 1380-1400: Log entry, update stats

---

### Exit Path (Line Numbers)

**File**: `strategies/crypto_scalp/orchestrator.py`

1. **Exit Monitoring** (Exit Thread)
   - Lines 1450-1550: `_exit_manager_loop()` scans positions every 100ms
   - Lines 1161-1207: **STOP-LOSS CHECK** — Adverse movement > 15¢ → immediate exit

2. **Exit Trigger**
   - Lines 1500-1520: Check target time, hard exit, stop-loss, circuit breaker
   - Lines 1520-1540: Call `_place_exit(ticker, reason)`

3. **Order Submission** (Main Thread via `_run_async`)
   - Lines 2020-2050: Refresh orderbook, get current exit price
   - Lines 2050-2080: Submit exit order, **WAIT FOR FILL** (FIX #1)

4. **Fill Confirmation** (FIXED Mar 3, 2026)
   - Lines 2055-2075: `filled = await _wait_for_fill_om(exit_order_id, 3s)`
   - Lines 2075-2095: If filled → get actual fill price, else keep position

5. **P&L Calculation**
   - Lines 2160-2195: `_record_exit()` calculates gross/net P&L with fees
   - Lines 2180-2185: **FIX #7** — Always charge entry fee (was missing)
   - Lines 2185-2190: Exit fee only on profit (Kalshi fee schedule)

6. **Position Cleanup**
   - Lines 2195-2200: Remove from `_positions`, add to `_trade_log`
   - Lines 2200-2205: Update stats, log exit

---

### Balance Tracking Path (FIXED Mar 3, 2026)

**File**: `strategies/crypto_scalp/orchestrator.py`

1. **Initialize Balance** (`run` method)
   - Lines 485-494: Query initial balance on startup
   - Line 489: `self._initial_balance_cents = balance.balance_cents`

2. **Periodic Balance Check** (Main Loop, every 60s)
   - Lines 541-543: Call `_check_balance_and_circuit_breaker()`
   - Lines 2203-2301: Implementation

3. **Circuit Breaker Logic**
   - Lines 2240-2260: Calculate `realized_loss = initial - current`
   - Lines 2260-2280: If `realized_loss >= max_daily_loss_cents` → `self.stop()`
   - Lines 2280-2301: Log balance change, trigger alerts

---

### Position Reconciliation Path (FIXED Mar 3, 2026)

**File**: `strategies/crypto_scalp/orchestrator.py`

1. **Startup Reconciliation** (`run` method)
   - Lines 497-514: Query `client.get_positions()` on startup
   - Lines 505-510: Compare exchange positions to `_positions` dict
   - Lines 510-514: Log stranded positions, optionally add to tracking

2. **OMS Initialization** (`run` method)
   - Lines 476-483: Call `await om.initialize()`
   - Lines 105-182: OMS `initialize()` method (KalshiOrderManager)
   - Lines 143-155: Recover positions from recent fills
   - Lines 130-138: Cancel all resting orders (clean slate)

---

## Thread Safety & Concurrency

### Thread-Safe Data Structures

1. **`queue.Queue`** (Built-in, thread-safe)
   - `_orderbook_queue`: WebSocket/Scanner → Main loop
   - Non-blocking: `put_nowait()`, `get_nowait()`
   - Backpressure: `maxsize=1000`

2. **`threading.Lock`** (Manual locking)
   - `self._lock`: Protects `_positions`, `_markets`, `_selected_markets`
   - Usage: `with self._lock: ...`

3. **Atomic Operations** (No lock needed)
   - `set.add()`, `set.remove()`: `_pending_entries`, `_position_tickers`
   - `dict[key] = value`: Python GIL makes this atomic for simple types

---

### Async/Sync Boundaries

**Problem**: Threads need to call async methods on `exchange_client` (owned by main loop)

**Solution**: `_run_async()` helper

```python
def _run_async(self, coro):
    """Run coroutine in main event loop (from any thread)."""
    if self._main_loop and self._main_loop.is_running():
        # Main loop exists → delegate to it
        future = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
        return future.result(timeout=10.0)
    else:
        # Fallback: create temporary loop (not ideal)
        return asyncio.run(coro)
```

**Usage** (from non-async thread):
```python
# Scanner thread wants to query market
market = self._run_async(self._client.request_market(ticker))
```

---

### Event Loop Ownership

| Thread | Event Loop | Async Operations |
|--------|------------|------------------|
| Main (main.py) | ✅ Main loop | `client.get_balance()`, `om.submit_order()` |
| Scanner | ❌ None | Via `_run_async()` → delegates to main loop |
| Detector | ❌ None | No async (pure computation) |
| Exit Manager | ❌ None | Via `_run_async()` → delegates to main loop |
| Binance WS | ✅ Isolated loop | Only WebSocket messages (no client calls) |
| Coinbase WS | ✅ Isolated loop | Only WebSocket messages |
| Kalshi WS | ✅ Isolated loop | Only WebSocket messages, pushes to queue |

---

## Testing & Validation

### Unit Tests

**Location**: `/Users/raine/tradingutils/tests/`

**Coverage**:
- `test_orderbook_manager.py`: Snapshot/delta application, gap detection
- `test_opposite_side_protection.py`: Position tracking, duplicate prevention
- `test_nba_duplicate_prevention.py`: Outcome normalization
- `test_scalp_detector.py`: Signal generation, momentum checks
- `test_kelly.py`: Kelly criterion calculations
- `test_portfolio_allocation.py`: Multi-variate Kelly, correlation estimation

---

### Integration Tests

**Phase 1: Quick Validation (2 hours)** — Task #8
- Process lock (start 2 instances → 2nd fails)
- OMS initialization (check WebSocket fill stream logs)
- Balance tracking (query balance every 60s)
- Position reconciliation (stranded positions detected)

**Phase 2: 8-Hour Integration Test** — Task #9 (IN PROGRESS)
- Run crypto scalp in paper mode for 8 hours
- Monitor: entry success rate, exit fill rate, orderbook reliability
- Verify: no stranded positions, accurate P&L logging, circuit breaker triggers

**Phase 3: Stress Tests** — Task #10
- Circuit breaker (force daily loss limit)
- WebSocket reconnection (kill WebSocket, verify reconnect)
- Position reconciliation (crash mid-session, restart, check positions)

---

## File Locations Reference

| Component | File Path |
|-----------|-----------|
| **Interfaces** | |
| I_ExchangeClient | `/Users/raine/tradingutils/core/exchange_client/i_exchange_client.py` |
| I_OrderManager | `/Users/raine/tradingutils/core/order_manager/i_order_manager.py` |
| I_Scanner | `/Users/raine/tradingutils/scanner/i_scanner.py` |
| I_Strategy | `/Users/raine/tradingutils/strategies/i_strategy.py` |
| I_Market | `/Users/raine/tradingutils/core/market/i_market.py` |
| **Implementations** | |
| KalshiExchangeClient | `/Users/raine/tradingutils/core/exchange_client/kalshi/kalshi_client.py` |
| KalshiOrderManager | `/Users/raine/tradingutils/core/order_manager/kalshi_order_manager.py` |
| KalshiScanner | `/Users/raine/tradingutils/scanner/kalshi_scanner.py` |
| CryptoScalpStrategy | `/Users/raine/tradingutils/strategies/crypto_scalp/orchestrator.py` |
| OrderBookManager | `/Users/raine/tradingutils/core/market/orderbook_manager.py` |
| TradingState | `/Users/raine/tradingutils/core/trading_state.py` |
| **Config** | |
| CryptoScalpConfig | `/Users/raine/tradingutils/strategies/crypto_scalp/config.py` |
| ScalpDetector | `/Users/raine/tradingutils/strategies/crypto_scalp/detector.py` |
| **CLI** | |
| MrClean CLI | `/Users/raine/tradingutils/main.py` |
| **Docs** | |
| Architecture Guide | `/Users/raine/tradingutils/ARCHITECTURE.md` |
| P0 Fixes Summary | `/Users/raine/tradingutils/P0_FIXES_COMPLETE_2026-03-03.md` |
| P1 Fixes Summary | `/Users/raine/tradingutils/P1_FIXES_COMPLETE_2026-03-03.md` |
| WebSocket Analysis | `/Users/raine/tradingutils/WEBSOCKET_INFRASTRUCTURE_ANALYSIS.md` |
| P&L Analysis | `/Users/raine/tradingutils/PNL_LOGGING_DISCREPANCY_ANALYSIS.md` |

---

## Glossary

| Term | Definition |
|------|------------|
| **TTX** | Time To Expiry (seconds until market closes) |
| **CEX** | Centralized Exchange (Binance, Coinbase, Kraken) |
| **OMS** | Order Management System (KalshiOrderManager) |
| **Kalshi** | Prediction market exchange (primary trading venue) |
| **Spot Delta** | Price change on spot exchange (trigger for scalp) |
| **Stop-Loss** | Exit trigger on adverse price movement (15¢ default) |
| **Circuit Breaker** | Emergency shutdown on daily loss limit |
| **Process Lock** | File-based lock to prevent multiple instances |
| **Stranded Position** | Position on exchange not in strategy tracking (bug) |
| **Orderbook Queue** | Thread-safe queue for WebSocket → Main loop communication |
| **Event Loop Mismatch** | Async coroutine called from wrong event loop (deadlock) |
| **Opposite Side Protection** | Prevents buying both YES and NO on same market |
| **Position Reconciliation** | Sync strategy tracking with exchange state on startup |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Feb 27, 2026 | Initial refactor to I_Strategy interface |
| 1.5 | Mar 2, 2026 | 10 critical bugs identified (WebSocket, P&L, balance) |
| 2.0 | Mar 3, 2026 | All P0+P1 fixes implemented, ready for 8hr test |

---

**Next Steps**: Complete 8-hour integration test (Task #9), then stress tests (Task #10)

**Critical**: Do NOT resume live trading until all tests pass. March 1 session lost $6 due to bugs documented here.
