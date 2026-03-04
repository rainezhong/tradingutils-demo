# Kalshi Module Documentation

## Overview

The Kalshi module (`core.exchange_client.kalshi`) provides a production-ready, object-oriented interface for interacting with the Kalshi prediction market exchange. It is designed for high-frequency trading (HFT) with optimized authentication, connection pooling, and asynchronous architecture.

## Structure

The module is organized as follows:

- **`kalshi_client.py`**: The main `KalshiExchangeClient` implementation. Handles REST API interactions.
- **`kalshi_auth.py`**: Handles authentication (ECDSA signing) with key caching for performance.
- **`kalshi_websocket.py`**: Async WebSocket client for real-time market data.
- **`kalshi_websocket_sync.py`**: Synchronous wrapper for the WebSocket client.
- **`kalshi_order_manager.py`**: High-level order management system (OMS) for tracking order lifecycle.
- **`kalshi_types.py`**: Data classes for API responses (e.g., `KalshiMarketData`, `KalshiOrderResponse`).
- **`kalshi_exceptions.py`**: Custom exceptions for precise error handling.

## Usage

### 1. Exchange Client

The `KalshiExchangeClient` is the primary entry point for API interactions.

```python
from core.exchange_client.kalshi import KalshiExchangeClient

async with KalshiExchangeClient.from_env(demo=True) as client:
    # Get Balance
    balance = await client.get_balance()
    print(f"Balance: ${balance.balance}")

    # Get Markets
    markets = await client.get_markets(status="open")
    
    # Place Order
    order_resp = await client.create_order(
        ticker="KXQUICKSETTLE...",
        action="buy",
        side="yes",
        count=10,
        type="limit",
        yes_price=50
    )
    print(f"Order ID: {order_resp.order_id}")
```

### 2. Order Manager

The `KalshiOrderManager` provides a higher-level abstraction for managing orders, including tracking state and handling idempotency.

```python
from core.order_manager.kalshi_order_manager import KalshiOrderManager, OrderRequest, Action, Side

# Initialize with client
oms = KalshiOrderManager(client)

# Submit Order
req = OrderRequest(
    ticker="KXQUICKSETTLE...",
    action=Action.BUY,
    side=Side.YES,
    size=10,
    price_cents=50
)
order_id = await oms.submit_order(req)

# Check Status
status = await oms.get_order_status(order_id)
```

### 3. WebSocket

For real-time data, use `KalshiWebSocket`.

```python
from core.exchange_client.kalshi import KalshiWebSocket

async with KalshiWebSocket(client) as ws:
    await ws.subscribe_orderbook("KXQUICKSETTLE...")
    
    async for msg in ws:
        print(msg)
```

## Key Features

- **Performance**: Private keys are cached to minimize signing latency (~1ms overhead).
- **Reliability**: Automatic retries with exponential backoff for rate limits.
- **Safety**: Custom exceptions (`KalshiRateLimitError`, `KalshiAuthError`) for robust error handling.
- **Modularity**: Separation of concerns between low-level client (API) and high-level OMS (Logic).

## Configuration

Set the following environment variables:
- `KALSHI_API_KEY`
- `KALSHI_API_SECRET` (RSA Private Key)
