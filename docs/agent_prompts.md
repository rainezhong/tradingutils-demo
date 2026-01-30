# Parallel Agent Build Prompts for Arbitrage System

> These prompts are designed to be run in parallel across multiple Claude Code instances.
> They integrate with the existing codebase in `/Users/raine/tradingutils/`.

---

## Phase 1: Foundations (Run in Parallel)

---

### AGENT 1: Kalshi API Client

```
Build a production-ready Kalshi API client that implements the existing APIClient interface.

EXISTING CODE TO INTEGRATE WITH:
- src/core/interfaces.py: APIClient abstract class with place_order(), cancel_order(), get_order_status(), get_market_data()
- src/core/models.py: MarketState, Fill, Quote, Position dataclasses
- src/simulation/paper_trading.py: Reference implementation of APIClient

REQUIREMENTS:

1. REST API Client (src/kalshi/client.py):
   - Implement APIClient interface from src/core/interfaces.py
   - Authentication using API key/secret (HMAC signature)
   - Endpoints: markets, orders, portfolio, balance, positions
   - Automatic retry with exponential backoff (max 3 retries)
   - Rate limiting (10 requests/second)
   - Return MarketState from get_market_data()
   - Return Fill objects for order fills

2. WebSocket Client (src/kalshi/websocket.py):
   - Subscribe to orderbook updates, trades, order status
   - Automatic reconnection with backoff
   - Parse messages into MarketState updates
   - Heartbeat handling (ping every 30s)
   - Callback system for market updates

3. Order Book Manager (src/kalshi/orderbook.py):
   - Maintain real-time order book state per ticker
   - Handle snapshot + delta updates
   - Calculate best bid/ask, mid, spread
   - Compute depth at price levels
   - Thread-safe updates

4. Models (src/kalshi/models.py):
   - KalshiOrder: extends/maps to Quote
   - KalshiMarket: market metadata
   - KalshiBalance: account balance info
   - Use pydantic for validation

FILE STRUCTURE:
src/kalshi/
    __init__.py
    client.py          # KalshiClient(APIClient)
    websocket.py       # KalshiWebSocket
    orderbook.py       # OrderBookManager
    models.py          # Kalshi-specific models
    auth.py            # Authentication helpers
    exceptions.py      # KalshiAPIError, etc.

AUTHENTICATION:
- API Key + Secret from environment variables (KALSHI_API_KEY, KALSHI_API_SECRET)
- HMAC-SHA256 signature for requests
- Timestamp in headers

API BASE URLs:
- Production: https://trading-api.kalshi.com/trade-api/v2
- Demo: https://demo-api.kalshi.com/trade-api/v2

KEY ENDPOINTS:
- GET /markets - list markets
- GET /markets/{ticker} - single market
- GET /markets/{ticker}/orderbook - order book
- POST /portfolio/orders - place order
- DELETE /portfolio/orders/{order_id} - cancel order
- GET /portfolio/orders/{order_id} - order status
- GET /portfolio/positions - positions
- GET /portfolio/balance - balance

EXAMPLE IMPLEMENTATION PATTERN (match existing style):
```python
from src.core.interfaces import APIClient
from src.core.models import MarketState, Fill
from datetime import datetime

class KalshiClient(APIClient):
    def __init__(self, api_key: str, api_secret: str, base_url: str = None):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url or "https://trading-api.kalshi.com/trade-api/v2"
        self._session: Optional[httpx.AsyncClient] = None

    async def get_market_data(self, ticker: str) -> MarketState:
        data = await self._request("GET", f"/markets/{ticker}")
        return MarketState(
            ticker=ticker,
            timestamp=datetime.now(),
            bid=data["yes_bid"] / 100,  # Convert cents to decimal
            ask=data["yes_ask"] / 100,
            last_price=data.get("last_price", 0) / 100,
            volume=data.get("volume", 0),
        )

    def place_order(self, ticker: str, side: str, price: float, size: int) -> str:
        # Implementation...
```

TESTING:
- Unit tests with mocked HTTP responses
- WebSocket reconnection tests
- Order book consistency tests
- Use pytest-asyncio for async tests

SUCCESS CRITERIA:
- Implements APIClient interface completely
- All REST endpoints working
- WebSocket maintains connection for 1+ hour
- Order book updates < 50ms latency
- Proper error handling with custom exceptions
- Works with existing PaperTradingClient pattern

DELIVERABLES:
- Complete src/kalshi/ module
- tests/kalshi/ with unit tests
- Usage examples in docstrings
```

---

### AGENT 2: Polymarket API Client

```
Build a production-ready Polymarket CLOB API client that implements the existing APIClient interface.

EXISTING CODE TO INTEGRATE WITH:
- src/core/interfaces.py: APIClient abstract class
- src/core/models.py: MarketState, Fill, Quote, Position dataclasses
- src/simulation/paper_trading.py: Reference implementation

REQUIREMENTS:

1. CLOB API Client (src/polymarket/client.py):
   - Implement APIClient interface from src/core/interfaces.py
   - Authentication via wallet signature (EIP-712)
   - Endpoints: markets, orders, trades, book
   - Rate limiting compliance
   - Return MarketState from get_market_data()

2. Wallet/Signing (src/polymarket/wallet.py):
   - Load private key from environment (POLYMARKET_PRIVATE_KEY)
   - EIP-712 typed data signing for API auth
   - Order signing for CLOB
   - Address derivation
   - NEVER log or expose private key

3. Blockchain Integration (src/polymarket/blockchain.py):
   - Connect to Polygon RPC
   - Check USDC balance
   - Check token approvals
   - Estimate gas prices
   - Monitor transaction status
   - Contract interaction helpers

4. WebSocket Client (src/polymarket/websocket.py):
   - Subscribe to order book updates
   - Trade feed subscription
   - Automatic reconnection
   - Message parsing

5. Order Book Manager (src/polymarket/orderbook.py):
   - Same interface as Kalshi for consistency
   - Handle CLOB-specific message format
   - Price/size aggregation

FILE STRUCTURE:
src/polymarket/
    __init__.py
    client.py          # PolymarketClient(APIClient)
    websocket.py       # PolymarketWebSocket
    wallet.py          # Wallet signing
    blockchain.py      # Polygon interactions
    orderbook.py       # OrderBookManager
    models.py          # Polymarket-specific models
    exceptions.py      # Custom exceptions

API ENDPOINTS:
- CLOB API: https://clob.polymarket.com
- WebSocket: wss://ws-subscriptions-clob.polymarket.com/ws/market

KEY CLOB ENDPOINTS:
- GET /markets - list markets
- GET /book - order book
- POST /order - place order (signed)
- DELETE /order/{id} - cancel order
- GET /orders - user orders

POLYGON DETAILS:
- Chain ID: 137 (mainnet), 80001 (mumbai testnet)
- USDC Contract: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
- CTF Exchange: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E

WALLET IMPLEMENTATION:
```python
from eth_account import Account
from eth_account.messages import encode_typed_data

class PolymarketWallet:
    def __init__(self, private_key: str):
        self._account = Account.from_key(private_key)

    @property
    def address(self) -> str:
        return self._account.address

    def sign_order(self, order_data: dict) -> str:
        # EIP-712 signing for CLOB orders
        typed_data = self._build_typed_data(order_data)
        signed = self._account.sign_message(encode_typed_data(typed_data))
        return signed.signature.hex()
```

SECURITY REQUIREMENTS:
- Private key from environment variable only
- Never log private key or signatures
- Validate all RPC responses
- Gas price limits to prevent drain

TESTING:
- Mock all HTTP/RPC calls
- Test signing with known test vectors
- WebSocket reconnection tests
- Use pytest-asyncio

SUCCESS CRITERIA:
- Implements APIClient interface
- Secure key management
- All API endpoints functional
- WebSocket stable for 1+ hour
- Gas estimation within 20% of actual
- Works alongside KalshiClient

DELIVERABLES:
- Complete src/polymarket/ module
- tests/polymarket/ with unit tests
- Security documentation
```

---

### AGENT 3: Database Layer & Extended Models

```
Build the database layer with SQLAlchemy and Redis caching, extending existing models.

EXISTING CODE TO INTEGRATE WITH:
- src/core/models.py: MarketState, Fill, Quote, Position dataclasses
- src/risk/risk_manager.py: Uses Position for tracking
- src/strategies/base.py: Signal, StrategyConfig, StrategyState

REQUIREMENTS:

1. SQLAlchemy Models (src/database/models.py):
   - Map existing dataclasses to database tables
   - Add database-specific fields (id, created_at, updated_at)
   - Platform enum (KALSHI, POLYMARKET)
   - Relationships between tables

2. Database Tables:
   - markets: unified market data from both platforms
   - opportunities: detected arbitrage opportunities
   - orders: all orders with status tracking
   - trades: completed arbitrage trade pairs
   - positions: current open positions
   - fills: execution records
   - balances: capital per platform over time
   - system_events: audit log

3. Repository Layer (src/database/repository.py):
   - CRUD operations for all entities
   - Async SQLAlchemy 2.0 patterns
   - Connection pooling
   - Transaction support
   - Query methods (get_open_positions, get_recent_opportunities, etc.)

4. Redis Cache (src/database/cache.py):
   - Real-time order book caching
   - Market data TTL caching (5 second TTL)
   - Position cache for fast lookups
   - Pub/sub for price updates

5. Migrations (src/database/migrations/):
   - Alembic setup
   - Initial migration with all tables
   - Migration for indexes

FILE STRUCTURE:
src/database/
    __init__.py
    models.py          # SQLAlchemy ORM models
    schemas.py         # Pydantic schemas for API
    repository.py      # Database operations
    cache.py           # Redis operations
    connection.py      # DB connection management
    migrations/
        env.py
        versions/
            001_initial.py

DATABASE SCHEMA:
```sql
-- markets table
CREATE TABLE markets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(20) NOT NULL,  -- 'KALSHI' or 'POLYMARKET'
    external_id VARCHAR(255) NOT NULL,
    ticker VARCHAR(100),
    title TEXT NOT NULL,
    category VARCHAR(100),
    close_time TIMESTAMP,
    status VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(platform, external_id)
);

-- opportunities table
CREATE TABLE opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kalshi_market_id UUID REFERENCES markets(id),
    polymarket_market_id UUID REFERENCES markets(id),
    kalshi_price DECIMAL(10,4),
    polymarket_price DECIMAL(10,4),
    spread DECIMAL(10,4),
    net_spread DECIMAL(10,4),
    roi DECIMAL(10,4),
    confidence DECIMAL(5,4),
    detected_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,
    status VARCHAR(50) DEFAULT 'OPEN'
);

-- orders table
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID REFERENCES opportunities(id),
    platform VARCHAR(20) NOT NULL,
    external_order_id VARCHAR(255),
    ticker VARCHAR(100) NOT NULL,
    side VARCHAR(10) NOT NULL,
    price DECIMAL(10,4) NOT NULL,
    size INTEGER NOT NULL,
    filled_size INTEGER DEFAULT 0,
    status VARCHAR(50) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- trades table (completed arb pairs)
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID REFERENCES opportunities(id),
    kalshi_order_id UUID REFERENCES orders(id),
    polymarket_order_id UUID REFERENCES orders(id),
    gross_profit DECIMAL(12,4),
    fees DECIMAL(12,4),
    net_profit DECIMAL(12,4),
    opened_at TIMESTAMP,
    closed_at TIMESTAMP
);
```

REPOSITORY PATTERN:
```python
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import MarketModel, OpportunityModel

class MarketRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_ticker(self, platform: str, ticker: str) -> Optional[MarketModel]:
        result = await self._session.execute(
            select(MarketModel).where(
                MarketModel.platform == platform,
                MarketModel.ticker == ticker
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, market: MarketModel) -> MarketModel:
        # Insert or update logic
        ...

class OpportunityRepository:
    async def get_open_opportunities(self, min_roi: float = 0.0) -> List[OpportunityModel]:
        ...

    async def create_with_orders(self, opportunity: OpportunityModel, orders: List[OrderModel]) -> OpportunityModel:
        # Atomic transaction
        ...
```

CACHE IMPLEMENTATION:
```python
import redis.asyncio as redis

class MarketCache:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._redis = redis.from_url(redis_url)

    async def get_orderbook(self, platform: str, ticker: str) -> Optional[dict]:
        key = f"orderbook:{platform}:{ticker}"
        data = await self._redis.get(key)
        return json.loads(data) if data else None

    async def set_orderbook(self, platform: str, ticker: str, orderbook: dict, ttl: int = 5):
        key = f"orderbook:{platform}:{ticker}"
        await self._redis.setex(key, ttl, json.dumps(orderbook))

    async def publish_price_update(self, ticker: str, price: float):
        await self._redis.publish(f"prices:{ticker}", json.dumps({"price": price}))
```

TESTING:
- Use test database (PostgreSQL in Docker)
- Test all CRUD operations
- Test transactions and rollbacks
- Test cache operations
- Test concurrent access

SUCCESS CRITERIA:
- All migrations run successfully
- Repository queries < 10ms
- Cache operations < 1ms
- Works with existing Position, Fill models
- Proper connection pooling
- Audit trail for all changes

DELIVERABLES:
- Complete src/database/ module
- Alembic migrations
- tests/database/ with tests
- Docker compose for local PostgreSQL/Redis
```

---

### AGENT 4: Monitoring & Observability

```
Build comprehensive monitoring infrastructure with structured logging, metrics, and alerting.

EXISTING CODE TO INTEGRATE WITH:
- All modules will import logging from this module
- src/risk/risk_manager.py: Emit alerts on limit breaches
- src/strategies/base.py: Log strategy events

REQUIREMENTS:

1. Structured Logging (src/monitoring/logger.py):
   - JSON formatted logs using structlog
   - Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
   - Contextual fields (trade_id, opportunity_id, ticker, platform)
   - Request ID tracking
   - Automatic exception formatting

2. Metrics Collection (src/monitoring/metrics.py):
   - Prometheus client metrics
   - Trading metrics: opportunities/hour, trades/hour, win rate, fill rate
   - System metrics: API latency (p50/p95/p99), WebSocket uptime, error rates
   - Business metrics: capital deployed, P&L, positions
   - Histogram buckets for latencies

3. Alert Manager (src/monitoring/alerts.py):
   - Multi-channel: Slack, email (via SMTP)
   - Severity levels: INFO, WARNING, CRITICAL
   - Alert deduplication (5 minute cooldown)
   - Alert templates

4. Health Checks (src/monitoring/health.py):
   - HTTP endpoint for health status
   - Component health aggregation
   - Readiness vs liveness probes

FILE STRUCTURE:
src/monitoring/
    __init__.py
    logger.py          # Structured logging setup
    metrics.py         # Prometheus metrics
    alerts.py          # Alert manager
    health.py          # Health check endpoint
    middleware.py      # Request logging middleware

LOGGING SETUP:
```python
import structlog
from typing import Any

def setup_logging(log_level: str = "INFO", json_output: bool = True):
    """Configure structured logging for the application."""

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a configured logger instance."""
    return structlog.get_logger(name)

# Context manager for adding context
@contextmanager
def log_context(**kwargs):
    """Add context to all logs within this block."""
    token = structlog.contextvars.bind_contextvars(**kwargs)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*kwargs.keys())
```

METRICS DEFINITIONS:
```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Trading metrics
OPPORTUNITIES_DETECTED = Counter(
    'arbitrage_opportunities_detected_total',
    'Total arbitrage opportunities detected',
    ['category', 'platform_pair']
)

TRADES_EXECUTED = Counter(
    'arbitrage_trades_executed_total',
    'Total trades executed',
    ['platform', 'status', 'strategy']
)

TRADE_PROFIT = Histogram(
    'arbitrage_trade_profit_dollars',
    'Profit per trade in dollars',
    buckets=[0, 5, 10, 25, 50, 100, 250, 500, 1000]
)

TRADE_LATENCY = Histogram(
    'arbitrage_trade_latency_seconds',
    'End-to-end trade execution latency',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

# System metrics
API_LATENCY = Histogram(
    'api_request_latency_seconds',
    'API request latency by platform and endpoint',
    ['platform', 'endpoint', 'method'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

WEBSOCKET_CONNECTED = Gauge(
    'websocket_connected',
    'WebSocket connection status (1=connected, 0=disconnected)',
    ['platform']
)

ERROR_COUNT = Counter(
    'errors_total',
    'Total errors by component and type',
    ['component', 'error_type']
)

# Business metrics
CAPITAL_DEPLOYED = Gauge(
    'capital_deployed_dollars',
    'Capital currently deployed',
    ['platform']
)

CURRENT_PNL = Gauge(
    'current_pnl_dollars',
    'Current profit/loss in dollars',
    ['type']  # 'realized', 'unrealized', 'total'
)

OPEN_POSITIONS = Gauge(
    'open_positions_count',
    'Number of open positions',
    ['platform']
)

class MetricsCollector:
    def __init__(self, port: int = 9090):
        self.port = port

    def start(self):
        """Start Prometheus metrics HTTP server."""
        start_http_server(self.port)

    def record_opportunity(self, category: str, platforms: str = "kalshi_polymarket"):
        OPPORTUNITIES_DETECTED.labels(category=category, platform_pair=platforms).inc()

    def record_trade(self, platform: str, status: str, strategy: str, profit: float, latency: float):
        TRADES_EXECUTED.labels(platform=platform, status=status, strategy=strategy).inc()
        TRADE_PROFIT.observe(profit)
        TRADE_LATENCY.observe(latency)

    def record_api_latency(self, platform: str, endpoint: str, method: str, latency: float):
        API_LATENCY.labels(platform=platform, endpoint=endpoint, method=method).observe(latency)
```

ALERT MANAGER:
```python
import aiosmtplib
from slack_sdk.web.async_client import AsyncWebClient
from datetime import datetime, timedelta

class AlertManager:
    def __init__(self, config: dict):
        self.config = config
        self._alert_history: dict[str, datetime] = {}
        self._cooldown = timedelta(minutes=5)

        if config.get("slack_token"):
            self._slack = AsyncWebClient(token=config["slack_token"])
        else:
            self._slack = None

    async def send_alert(
        self,
        name: str,
        severity: str,  # 'info', 'warning', 'critical'
        message: str,
        context: dict = None
    ):
        """Send alert if not in cooldown period."""

        # Check cooldown
        if name in self._alert_history:
            if datetime.now() - self._alert_history[name] < self._cooldown:
                return  # Skip duplicate

        self._alert_history[name] = datetime.now()

        # Format message
        full_message = self._format_message(name, severity, message, context)

        # Send to configured channels
        if severity == "critical":
            await self._send_slack(full_message, severity)
            await self._send_email(full_message, severity)
        elif severity == "warning":
            await self._send_slack(full_message, severity)
        else:
            await self._send_slack(full_message, severity)

    async def _send_slack(self, message: str, severity: str):
        if not self._slack:
            return

        color = {"critical": "danger", "warning": "warning", "info": "good"}[severity]

        await self._slack.chat_postMessage(
            channel=self.config.get("slack_channel", "#trading-alerts"),
            text=message,
            attachments=[{"color": color, "text": message}]
        )
```

HEALTH CHECKS:
```python
from fastapi import FastAPI
from enum import Enum

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

class HealthChecker:
    def __init__(self):
        self._checks: dict[str, callable] = {}

    def register(self, name: str, check_fn: callable):
        """Register a health check function."""
        self._checks[name] = check_fn

    async def check_all(self) -> dict:
        """Run all health checks."""
        results = {}
        overall = HealthStatus.HEALTHY

        for name, check_fn in self._checks.items():
            try:
                status = await check_fn()
                results[name] = status
                if status == HealthStatus.UNHEALTHY:
                    overall = HealthStatus.UNHEALTHY
                elif status == HealthStatus.DEGRADED and overall == HealthStatus.HEALTHY:
                    overall = HealthStatus.DEGRADED
            except Exception as e:
                results[name] = HealthStatus.UNHEALTHY
                overall = HealthStatus.UNHEALTHY

        return {"status": overall, "components": results}

# FastAPI health endpoint
app = FastAPI()
health_checker = HealthChecker()

@app.get("/health")
async def health():
    return await health_checker.check_all()

@app.get("/health/live")
async def liveness():
    return {"status": "ok"}

@app.get("/health/ready")
async def readiness():
    result = await health_checker.check_all()
    if result["status"] == HealthStatus.UNHEALTHY:
        raise HTTPException(status_code=503, detail=result)
    return result
```

TESTING:
- Test log formatting (valid JSON)
- Test metrics increment/observe
- Test alert deduplication
- Test health check aggregation
- Mock Slack/email for alert tests

SUCCESS CRITERIA:
- All logs are valid JSON with context
- Metrics exposed on /metrics endpoint
- Alerts delivered within 30 seconds
- Health checks return accurate status
- No performance impact from logging

DELIVERABLES:
- Complete src/monitoring/ module
- Grafana dashboard JSON configs
- Alert runbook documentation
- tests/monitoring/ with tests
```

---

## Phase 2: Core Logic (Run in Parallel after Phase 1)

---

### AGENT 5: Opportunity Detection Engine

```
Build the arbitrage opportunity detection system with market matching and spread calculation.

EXISTING CODE TO INTEGRATE WITH:
- src/core/models.py: MarketState for price data
- src/strategies/base.py: Signal for output format
- src/kalshi/client.py: KalshiClient (from Agent 1)
- src/polymarket/client.py: PolymarketClient (from Agent 2)
- src/database/: For persisting opportunities (from Agent 3)
- src/monitoring/: For logging and metrics (from Agent 4)

REQUIREMENTS:

1. Market Matcher (src/detection/matcher.py):
   - Match equivalent markets across Kalshi/Polymarket
   - Fuzzy string matching for market titles
   - Manual override mappings from JSON config
   - Confidence scoring (0-1) for matches
   - Handle edge cases (same event, different question framing)

2. Spread Calculator (src/detection/calculator.py):
   - Real-time spread calculation
   - Fee modeling:
     * Kalshi: 7% on profit (only if you win)
     * Polymarket: 2% taker fee + gas (~$0.01-0.10)
   - Slippage estimation based on order book depth
   - Net profit calculation after all costs
   - Break-even analysis

3. Opportunity Ranker (src/detection/ranker.py):
   - ROI calculation: (net_profit / capital_required) * (365 / days_to_settlement)
   - Risk-adjusted scoring
   - Liquidity score (can we actually fill the size?)
   - Time decay factor (closer events = higher priority)

4. Filter Engine (src/detection/filters.py):
   - Minimum net profit threshold ($5 default)
   - Minimum ROI threshold (10% annualized default)
   - Minimum liquidity (100 contracts default)
   - Maximum time to expiry (30 days default)
   - Category filters (politics, crypto, sports, etc.)

5. Detection Orchestrator (src/detection/detector.py):
   - Main detection loop
   - Scan all matched markets every 1 second
   - Emit opportunities to queue/callback
   - Deduplication (don't re-emit same opportunity)
   - Integration with monitoring metrics

FILE STRUCTURE:
src/detection/
    __init__.py
    matcher.py         # Market matching logic
    calculator.py      # Spread and fee calculations
    ranker.py          # Opportunity ranking
    filters.py         # Filtering logic
    detector.py        # Main orchestrator
    config/
        market_mappings.json   # Manual market matches
        filters.yaml           # Filter defaults

MARKET MATCHING:
```python
from rapidfuzz import fuzz, process
from typing import Optional, Tuple
import json

class MarketMatcher:
    def __init__(self, mappings_path: str = "src/detection/config/market_mappings.json"):
        self._manual_mappings = self._load_mappings(mappings_path)
        self._match_cache: dict[str, str] = {}

    def find_match(
        self,
        kalshi_market: dict,
        polymarket_markets: list[dict]
    ) -> Optional[Tuple[dict, float]]:
        """Find matching Polymarket market for a Kalshi market.

        Returns:
            Tuple of (matched_market, confidence) or None
        """
        kalshi_id = kalshi_market["ticker"]

        # Check manual mappings first
        if kalshi_id in self._manual_mappings:
            poly_id = self._manual_mappings[kalshi_id]
            for pm in polymarket_markets:
                if pm["condition_id"] == poly_id:
                    return (pm, 1.0)  # Perfect confidence for manual

        # Fuzzy match on title
        kalshi_title = self._normalize_title(kalshi_market["title"])

        best_match = None
        best_score = 0.0

        for pm in polymarket_markets:
            poly_title = self._normalize_title(pm["question"])

            # Try multiple matching strategies
            token_score = fuzz.token_set_ratio(kalshi_title, poly_title) / 100
            partial_score = fuzz.partial_ratio(kalshi_title, poly_title) / 100

            score = max(token_score, partial_score)

            if score > best_score and score >= 0.70:  # 70% minimum
                best_score = score
                best_match = pm

        if best_match:
            return (best_match, best_score)

        return None

    def _normalize_title(self, title: str) -> str:
        """Normalize market title for comparison."""
        # Remove common prefixes/suffixes, lowercase, etc.
        title = title.lower()
        title = title.replace("will ", "").replace("?", "")
        return title.strip()
```

SPREAD CALCULATION:
```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class SpreadAnalysis:
    gross_spread: float          # Raw price difference
    kalshi_fee: float            # Kalshi profit fee (7% of profit)
    polymarket_fee: float        # Polymarket taker fee (2%)
    gas_estimate: float          # Polygon gas cost
    slippage_kalshi: float       # Expected slippage on Kalshi
    slippage_polymarket: float   # Expected slippage on Polymarket
    net_spread: float            # After all costs
    break_even_spread: float     # Minimum spread to profit
    is_profitable: bool

class SpreadCalculator:
    KALSHI_PROFIT_FEE = 0.07     # 7% on profit
    POLYMARKET_TAKER_FEE = 0.02  # 2% on notional
    DEFAULT_GAS_USD = 0.05       # ~$0.05 gas on Polygon

    def __init__(self, min_profit: float = 5.0):
        self.min_profit = min_profit

    def calculate(
        self,
        kalshi_price: float,      # 0-1 probability
        polymarket_price: float,  # 0-1 probability
        size: int,                # Number of contracts
        kalshi_orderbook: dict,
        polymarket_orderbook: dict,
        gas_price_gwei: float = 30.0
    ) -> SpreadAnalysis:
        """Calculate net spread after all fees and slippage."""

        # Determine direction (which side to buy/sell on each platform)
        # If kalshi_price > polymarket_price: Buy on Poly, Sell on Kalshi
        # If polymarket_price > kalshi_price: Buy on Kalshi, Sell on Poly

        gross_spread = abs(kalshi_price - polymarket_price)

        # Estimate slippage from order books
        slippage_k = self._estimate_slippage(kalshi_orderbook, size)
        slippage_p = self._estimate_slippage(polymarket_orderbook, size)

        # Effective prices after slippage
        effective_spread = gross_spread - slippage_k - slippage_p

        # Calculate fees
        # Kalshi: 7% on profit (max we can make is the spread * size)
        max_profit = effective_spread * size
        kalshi_fee = max_profit * self.KALSHI_PROFIT_FEE

        # Polymarket: 2% on the trade value
        trade_value = polymarket_price * size
        polymarket_fee = trade_value * self.POLYMARKET_TAKER_FEE

        # Gas estimate
        gas_estimate = self._estimate_gas(gas_price_gwei)

        # Net profit
        total_fees = kalshi_fee + polymarket_fee + gas_estimate
        net_profit = max_profit - total_fees

        # Break-even calculation
        break_even = total_fees / size if size > 0 else 0

        return SpreadAnalysis(
            gross_spread=gross_spread,
            kalshi_fee=kalshi_fee,
            polymarket_fee=polymarket_fee,
            gas_estimate=gas_estimate,
            slippage_kalshi=slippage_k,
            slippage_polymarket=slippage_p,
            net_spread=net_profit / size if size > 0 else 0,
            break_even_spread=break_even,
            is_profitable=net_profit >= self.min_profit
        )

    def _estimate_slippage(self, orderbook: dict, size: int) -> float:
        """Estimate slippage based on order book depth."""
        # Walk the book to fill 'size' contracts
        # Return average price - best price
        ...
```

OPPORTUNITY DETECTOR:
```python
from src.strategies.base import Signal
from src.monitoring.logger import get_logger
from src.monitoring.metrics import OPPORTUNITIES_DETECTED
import asyncio

logger = get_logger(__name__)

class OpportunityDetector:
    def __init__(
        self,
        kalshi_client,
        polymarket_client,
        matcher: MarketMatcher,
        calculator: SpreadCalculator,
        ranker: OpportunityRanker,
        filters: FilterEngine,
        callback: callable = None
    ):
        self._kalshi = kalshi_client
        self._polymarket = polymarket_client
        self._matcher = matcher
        self._calculator = calculator
        self._ranker = ranker
        self._filters = filters
        self._callback = callback
        self._running = False
        self._seen_opportunities: set[str] = set()

    async def start(self):
        """Start the detection loop."""
        self._running = True
        logger.info("opportunity_detector_started")

        while self._running:
            try:
                opportunities = await self._scan_markets()

                for opp in opportunities:
                    opp_key = f"{opp.kalshi_ticker}:{opp.polymarket_id}"

                    if opp_key not in self._seen_opportunities:
                        self._seen_opportunities.add(opp_key)
                        OPPORTUNITIES_DETECTED.labels(
                            category=opp.category,
                            platform_pair="kalshi_polymarket"
                        ).inc()

                        if self._callback:
                            await self._callback(opp)

                        logger.info(
                            "opportunity_detected",
                            kalshi_ticker=opp.kalshi_ticker,
                            spread=opp.net_spread,
                            roi=opp.roi,
                            size=opp.recommended_size
                        )

                await asyncio.sleep(1.0)  # 1 second scan interval

            except Exception as e:
                logger.error("detection_error", error=str(e))
                await asyncio.sleep(5.0)  # Back off on error

    async def stop(self):
        self._running = False
        logger.info("opportunity_detector_stopped")
```

TESTING:
- Unit test matcher with various title formats
- Test spread calculation accuracy (compare to manual)
- Test slippage estimation
- Test filter logic
- Integration test full detection pipeline
- Performance: scan 500 markets in <1 second

SUCCESS CRITERIA:
- Market matching >90% accuracy on known pairs
- Spread calculation matches manual verification
- Detection latency <100ms from price update
- False positive rate <5%
- Handles 500+ markets efficiently

DELIVERABLES:
- Complete src/detection/ module
- Market mappings config file
- Filter defaults config
- tests/detection/ with tests
- Backtest results on sample data
```

---

### AGENT 6: Order Execution System

```
Build the order execution engine with smart routing and partial fill handling.

EXISTING CODE TO INTEGRATE WITH:
- src/core/interfaces.py: APIClient for order placement
- src/core/models.py: Fill, Quote for order tracking
- src/strategies/base.py: TradingStrategy, Signal
- src/kalshi/client.py: KalshiClient (from Agent 1)
- src/polymarket/client.py: PolymarketClient (from Agent 2)
- src/database/: For persisting orders (from Agent 3)
- src/monitoring/: For logging and metrics (from Agent 4)

REQUIREMENTS:

1. Execution Orchestrator (src/execution/orchestrator.py):
   - Consume opportunities from detection
   - Determine execution sequence (which leg first)
   - Manage order lifecycle
   - Handle timeouts and retries
   - Atomic execution or rollback

2. Smart Order Router (src/execution/router.py):
   - Choose execution strategy based on:
     * Order book depth/liquidity
     * Platform latency characteristics
     * Fee optimization
   - Strategy selection: Market, Limit, TWAP

3. Execution Strategies (src/execution/strategies/):
   - MarketOrderStrategy: Immediate execution at best available
   - LimitOrderStrategy: Price guarantee, may not fill
   - HybridStrategy: Market on liquid side, limit on thin side

4. Leg Risk Manager (src/execution/leg_manager.py):
   - Track unhedged exposure during execution
   - Decision tree for partial fills:
     * >90% filled: Complete at market
     * 50-90% filled: Wait with timeout
     * <50% filled: Cancel and retry
   - Exposure calculations and limits

5. Order State Machine (src/execution/state_machine.py):
   - States: NEW -> PENDING -> PARTIALLY_FILLED -> FILLED -> CANCELLED -> FAILED
   - Transition validation
   - State persistence to database
   - Recovery from process crashes

FILE STRUCTURE:
src/execution/
    __init__.py
    orchestrator.py    # Main execution coordinator
    router.py          # Smart order routing
    leg_manager.py     # Leg risk management
    state_machine.py   # Order state tracking
    recovery.py        # Crash recovery
    strategies/
        __init__.py
        base.py        # ExecutionStrategy ABC
        market.py      # Market order strategy
        limit.py       # Limit order strategy
        hybrid.py      # Hybrid strategy

EXECUTION ORCHESTRATOR:
```python
from src.core.interfaces import APIClient
from src.strategies.base import Signal
from src.monitoring.logger import get_logger, log_context
from src.monitoring.metrics import TRADES_EXECUTED, TRADE_LATENCY
import asyncio
import time

logger = get_logger(__name__)

class ExecutionOrchestrator:
    def __init__(
        self,
        kalshi_client: APIClient,
        polymarket_client: APIClient,
        router: SmartOrderRouter,
        leg_manager: LegRiskManager,
        config: dict
    ):
        self._kalshi = kalshi_client
        self._polymarket = polymarket_client
        self._router = router
        self._leg_manager = leg_manager
        self._config = config
        self._running = False

    async def execute_opportunity(self, opportunity) -> ExecutionResult:
        """Execute an arbitrage opportunity."""

        start_time = time.time()

        with log_context(opportunity_id=str(opportunity.id)):
            logger.info(
                "execution_started",
                kalshi_ticker=opportunity.kalshi_ticker,
                spread=opportunity.net_spread
            )

            try:
                # Determine execution sequence
                sequence = self._determine_sequence(opportunity)

                # Select strategy
                strategy = self._router.select_strategy(opportunity)

                # Execute first leg
                first_platform, first_side = sequence[0]
                first_result = await self._execute_leg(
                    platform=first_platform,
                    ticker=opportunity.get_ticker(first_platform),
                    side=first_side,
                    price=opportunity.get_price(first_platform),
                    size=opportunity.recommended_size,
                    strategy=strategy
                )

                if first_result.status == "FAILED":
                    return ExecutionResult(status="FAILED", reason="First leg failed")

                # Track exposure
                self._leg_manager.add_exposure(first_result)

                # Execute second leg
                second_platform, second_side = sequence[1]
                second_result = await self._execute_leg(
                    platform=second_platform,
                    ticker=opportunity.get_ticker(second_platform),
                    side=second_side,
                    price=opportunity.get_price(second_platform),
                    size=first_result.filled_size,  # Match first leg size
                    strategy=strategy
                )

                # Handle partial fill scenarios
                if second_result.filled_size < first_result.filled_size:
                    await self._handle_partial_fill(
                        first_result, second_result, opportunity
                    )

                elapsed = time.time() - start_time

                TRADES_EXECUTED.labels(
                    platform="both",
                    status="completed",
                    strategy=strategy.name
                ).inc()
                TRADE_LATENCY.observe(elapsed)

                logger.info(
                    "execution_completed",
                    first_filled=first_result.filled_size,
                    second_filled=second_result.filled_size,
                    latency_seconds=elapsed
                )

                return ExecutionResult(
                    status="COMPLETED",
                    first_order=first_result,
                    second_order=second_result,
                    latency=elapsed
                )

            except Exception as e:
                logger.error("execution_failed", error=str(e))
                await self._handle_execution_failure(opportunity, e)
                raise

    def _determine_sequence(self, opportunity) -> list:
        """Decide which leg to execute first.

        Factors:
        1. Liquidity - execute deeper book first (safer)
        2. Latency - execute slower platform first (blockchain)
        3. Spread stability - execute more volatile side first
        """

        kalshi_depth = opportunity.kalshi_orderbook_depth
        poly_depth = opportunity.polymarket_orderbook_depth

        # Default: execute Polymarket first (slower due to blockchain)
        # But if Kalshi is much less liquid, execute it first

        if kalshi_depth < poly_depth * 0.5:
            # Kalshi is thin, execute first to ensure fill
            return [
                ("KALSHI", opportunity.kalshi_side),
                ("POLYMARKET", opportunity.polymarket_side)
            ]
        else:
            # Default: Polymarket first (slower)
            return [
                ("POLYMARKET", opportunity.polymarket_side),
                ("KALSHI", opportunity.kalshi_side)
            ]
```

LEG RISK MANAGER:
```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

@dataclass
class Exposure:
    platform: str
    ticker: str
    side: str
    size: int
    price: float
    opened_at: datetime
    order_id: str

class LegRiskManager:
    MAX_UNHEDGED_SECONDS = 60
    MAX_UNHEDGED_VALUE = 1000  # $1000 max unhedged

    def __init__(self):
        self._exposures: list[Exposure] = []

    def add_exposure(self, order_result) -> None:
        """Track new unhedged exposure from first leg."""
        exposure = Exposure(
            platform=order_result.platform,
            ticker=order_result.ticker,
            side=order_result.side,
            size=order_result.filled_size,
            price=order_result.fill_price,
            opened_at=datetime.now(),
            order_id=order_result.order_id
        )
        self._exposures.append(exposure)

    def remove_exposure(self, order_id: str) -> None:
        """Remove exposure when hedged."""
        self._exposures = [e for e in self._exposures if e.order_id != order_id]

    def get_total_exposure(self) -> float:
        """Get total unhedged exposure value."""
        return sum(e.size * e.price for e in self._exposures)

    def get_stale_exposures(self) -> list[Exposure]:
        """Get exposures that have been open too long."""
        cutoff = datetime.now() - timedelta(seconds=self.MAX_UNHEDGED_SECONDS)
        return [e for e in self._exposures if e.opened_at < cutoff]

    async def handle_partial_fill(
        self,
        first_order,
        second_order,
        opportunity
    ) -> str:
        """Handle partial fill scenario. Returns action taken."""

        fill_pct = second_order.filled_size / first_order.filled_size
        unhedged_size = first_order.filled_size - second_order.filled_size
        unhedged_value = unhedged_size * first_order.fill_price

        if fill_pct >= 0.90:
            # Almost complete - finish at market price
            return "complete_at_market"

        elif fill_pct >= 0.50 and unhedged_value < self.MAX_UNHEDGED_VALUE:
            # Significant fill, acceptable risk - wait with timeout
            return "wait_for_fill"

        elif fill_pct < 0.30:
            # Minimal fill - cancel and unwind first leg
            return "cancel_and_unwind"

        else:
            # Medium fill - try to complete at worse price
            return "complete_at_worse_price"
```

ORDER STATE MACHINE:
```python
from enum import Enum
from typing import Optional
from datetime import datetime

class OrderStatus(str, Enum):
    NEW = "NEW"
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

VALID_TRANSITIONS = {
    OrderStatus.NEW: [OrderStatus.PENDING, OrderStatus.FAILED],
    OrderStatus.PENDING: [OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED],
    OrderStatus.OPEN: [OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED],
    OrderStatus.PARTIALLY_FILLED: [OrderStatus.FILLED, OrderStatus.CANCELLED],
    OrderStatus.FILLED: [],  # Terminal
    OrderStatus.CANCELLED: [],  # Terminal
    OrderStatus.FAILED: [],  # Terminal
}

class OrderStateMachine:
    def __init__(self, order_id: str, repository):
        self.order_id = order_id
        self._repository = repository
        self._status = OrderStatus.NEW
        self._history: list[tuple[OrderStatus, datetime]] = []

    def transition(self, new_status: OrderStatus) -> bool:
        """Attempt to transition to new status."""

        if new_status not in VALID_TRANSITIONS[self._status]:
            logger.warning(
                "invalid_state_transition",
                order_id=self.order_id,
                from_status=self._status,
                to_status=new_status
            )
            return False

        old_status = self._status
        self._status = new_status
        self._history.append((new_status, datetime.now()))

        # Persist to database
        self._repository.update_order_status(
            self.order_id,
            new_status,
            transitioned_from=old_status
        )

        logger.info(
            "order_status_changed",
            order_id=self.order_id,
            from_status=old_status,
            to_status=new_status
        )

        return True
```

EXECUTION STRATEGIES:
```python
from abc import ABC, abstractmethod
from src.core.interfaces import APIClient

class ExecutionStrategy(ABC):
    name: str

    @abstractmethod
    async def execute(
        self,
        client: APIClient,
        ticker: str,
        side: str,
        price: float,
        size: int,
        timeout: float = 30.0
    ) -> OrderResult:
        pass

class MarketOrderStrategy(ExecutionStrategy):
    name = "market"

    async def execute(self, client, ticker, side, price, size, timeout=30.0):
        """Execute immediately at best available price."""

        # Place order at aggressive price to ensure fill
        aggressive_price = price * 1.02 if side == "BID" else price * 0.98

        order_id = await client.place_order(
            ticker=ticker,
            side=side,
            price=aggressive_price,
            size=size
        )

        # Wait for fill with timeout
        filled = await self._wait_for_fill(client, order_id, timeout)

        return OrderResult(
            order_id=order_id,
            status="FILLED" if filled else "TIMEOUT",
            filled_size=filled.size if filled else 0
        )

class LimitOrderStrategy(ExecutionStrategy):
    name = "limit"

    async def execute(self, client, ticker, side, price, size, timeout=30.0):
        """Execute at specified limit price."""

        order_id = await client.place_order(
            ticker=ticker,
            side=side,
            price=price,
            size=size
        )

        # Wait for fill with longer timeout
        filled = await self._wait_for_fill(client, order_id, timeout * 2)

        if not filled:
            await client.cancel_order(order_id)

        return OrderResult(
            order_id=order_id,
            status="FILLED" if filled else "CANCELLED",
            filled_size=filled.size if filled else 0
        )
```

TESTING:
- Mock both platform APIs
- Test full execution flow
- Test partial fill handling at various percentages
- Test state machine transitions
- Test timeout handling
- Test recovery from crashes
- Test concurrent execution

SUCCESS CRITERIA:
- Order placement latency <500ms
- State transitions logged correctly
- 100% recovery from crashes
- Partial fill handling works correctly
- Maximum unhedged time <60 seconds
- All strategies execute correctly

DELIVERABLES:
- Complete src/execution/ module
- Execution strategies
- tests/execution/ with tests
- Recovery procedures documentation
```

---

### AGENT 7: Risk Management Extensions

```
Extend the existing RiskManager with circuit breakers, alerts, and real-time position tracking.

EXISTING CODE TO INTEGRATE WITH:
- src/risk/risk_manager.py: Existing RiskManager with position limits, daily loss
- src/core/models.py: Position dataclass
- src/core/config.py: RiskConfig
- src/monitoring/: For alerts (from Agent 4)
- src/database/: For persistence (from Agent 3)

REQUIREMENTS:

1. Circuit Breaker (src/risk/circuit_breaker.py):
   - States: CLOSED (normal) -> OPEN (halted) -> HALF_OPEN (testing)
   - Triggers:
     * Daily loss > threshold
     * Error rate > threshold
     * Latency > threshold
     * Fill rate < threshold
   - Manual override capability with audit logging
   - Automatic recovery after cooldown

2. Position Tracker Extension (src/risk/position_tracker.py):
   - Real-time aggregate positions across platforms
   - Per-market exposure tracking
   - Unhedged leg tracking
   - Historical snapshots (every 1 minute)

3. Pre-Trade Risk Checks (src/risk/pre_trade.py):
   - Fast validation before order execution
   - Check all limits in parallel
   - Return detailed rejection reasons
   - Integration with execution system

4. Risk Alerts (src/risk/risk_alerts.py):
   - Warning levels: APPROACHING (80%), BREACH (100%), CRITICAL (120%)
   - Integration with monitoring AlertManager
   - Escalation procedures

5. Extend Existing RiskManager:
   - Add circuit breaker integration
   - Add pre-trade check method
   - Add alert triggering

FILE STRUCTURE:
src/risk/
    __init__.py          # Update exports
    risk_manager.py      # Extend existing
    circuit_breaker.py   # New: Circuit breaker
    position_tracker.py  # New: Enhanced tracking
    pre_trade.py         # New: Pre-trade checks
    risk_alerts.py       # New: Alert integration

CIRCUIT BREAKER:
```python
from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, Callable
from src.monitoring.logger import get_logger
from src.monitoring.alerts import AlertManager

logger = get_logger(__name__)

class CircuitState(str, Enum):
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Trading halted
    HALF_OPEN = "HALF_OPEN"  # Testing recovery

class CircuitBreaker:
    def __init__(
        self,
        alert_manager: AlertManager,
        config: dict
    ):
        self._alert_manager = alert_manager
        self._config = config
        self._state = CircuitState.CLOSED
        self._opened_at: Optional[datetime] = None
        self._failure_count = 0
        self._success_count = 0

        # Thresholds from config
        self._daily_loss_threshold = config.get("max_daily_loss", 500)
        self._error_rate_threshold = config.get("max_error_rate", 0.10)
        self._latency_threshold_ms = config.get("max_latency_ms", 2000)
        self._fill_rate_threshold = config.get("min_fill_rate", 0.70)
        self._cooldown_seconds = config.get("cooldown_seconds", 300)

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_trading_allowed(self) -> bool:
        return self._state == CircuitState.CLOSED

    def check_daily_loss(self, current_loss: float) -> bool:
        """Check if daily loss threshold breached."""
        if current_loss >= self._daily_loss_threshold:
            self._trip(f"Daily loss ${current_loss:.2f} >= ${self._daily_loss_threshold:.2f}")
            return False

        # Warning at 80%
        if current_loss >= self._daily_loss_threshold * 0.8:
            self._warn(f"Daily loss approaching limit: ${current_loss:.2f}")

        return True

    def check_error_rate(self, error_rate: float) -> bool:
        """Check if error rate threshold breached."""
        if error_rate >= self._error_rate_threshold:
            self._trip(f"Error rate {error_rate:.1%} >= {self._error_rate_threshold:.1%}")
            return False
        return True

    def check_latency(self, latency_ms: float) -> bool:
        """Check if latency threshold breached."""
        if latency_ms >= self._latency_threshold_ms:
            self._trip(f"Latency {latency_ms}ms >= {self._latency_threshold_ms}ms")
            return False
        return True

    def check_fill_rate(self, fill_rate: float) -> bool:
        """Check if fill rate threshold breached."""
        if fill_rate < self._fill_rate_threshold:
            self._trip(f"Fill rate {fill_rate:.1%} < {self._fill_rate_threshold:.1%}")
            return False
        return True

    def _trip(self, reason: str):
        """Trip the circuit breaker."""
        if self._state == CircuitState.OPEN:
            return  # Already open

        self._state = CircuitState.OPEN
        self._opened_at = datetime.now()

        logger.critical("circuit_breaker_tripped", reason=reason)

        self._alert_manager.send_alert(
            name="circuit_breaker_tripped",
            severity="critical",
            message=f"CIRCUIT BREAKER TRIPPED: {reason}",
            context={"reason": reason, "time": self._opened_at.isoformat()}
        )

    def _warn(self, message: str):
        """Send warning alert."""
        logger.warning("circuit_breaker_warning", message=message)

        self._alert_manager.send_alert(
            name="circuit_breaker_warning",
            severity="warning",
            message=message
        )

    def attempt_recovery(self) -> bool:
        """Attempt to recover from open state."""
        if self._state != CircuitState.OPEN:
            return True

        # Check cooldown
        if self._opened_at:
            elapsed = (datetime.now() - self._opened_at).total_seconds()
            if elapsed < self._cooldown_seconds:
                return False

        # Move to half-open for testing
        self._state = CircuitState.HALF_OPEN
        logger.info("circuit_breaker_half_open", message="Attempting recovery")

        return True

    def record_success(self):
        """Record successful operation in half-open state."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= 3:  # 3 successes to close
                self._close()

    def record_failure(self):
        """Record failed operation."""
        if self._state == CircuitState.HALF_OPEN:
            self._trip("Failure during recovery")

    def _close(self):
        """Close the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._success_count = 0

        logger.info("circuit_breaker_closed", message="Trading resumed")

        self._alert_manager.send_alert(
            name="circuit_breaker_closed",
            severity="info",
            message="Circuit breaker closed - trading resumed"
        )

    def manual_override(self, authorized_by: str, reason: str):
        """Manual override to close circuit breaker."""
        logger.warning(
            "circuit_breaker_manual_override",
            authorized_by=authorized_by,
            reason=reason
        )

        self._alert_manager.send_alert(
            name="circuit_breaker_override",
            severity="warning",
            message=f"Circuit breaker manually overridden by {authorized_by}: {reason}"
        )

        self._close()
```

PRE-TRADE CHECKS:
```python
from dataclasses import dataclass
from typing import List, Tuple
from src.risk.risk_manager import RiskManager
from src.risk.circuit_breaker import CircuitBreaker

@dataclass
class RiskCheckResult:
    approved: bool
    violations: List[str]
    warnings: List[str]

class PreTradeRiskChecker:
    def __init__(
        self,
        risk_manager: RiskManager,
        circuit_breaker: CircuitBreaker
    ):
        self._risk_manager = risk_manager
        self._circuit_breaker = circuit_breaker

    def check(
        self,
        ticker: str,
        side: str,
        size: int,
        price: float
    ) -> RiskCheckResult:
        """Run all pre-trade risk checks."""

        violations = []
        warnings = []

        # Check circuit breaker
        if not self._circuit_breaker.is_trading_allowed:
            violations.append("Circuit breaker is open - trading halted")
            return RiskCheckResult(approved=False, violations=violations, warnings=[])

        # Check position limits via existing RiskManager
        allowed, reason = self._risk_manager.can_trade(
            ticker=ticker,
            side="buy" if side == "BID" else "sell",
            size=size
        )

        if not allowed:
            violations.append(reason)

        # Check daily loss utilization
        metrics = self._risk_manager.get_risk_metrics()

        if metrics["daily_loss_utilization"] >= 0.80:
            warnings.append(f"Daily loss at {metrics['daily_loss_utilization']:.0%} of limit")

        if metrics["position_limit_utilization"] >= 0.80:
            warnings.append(f"Position limit at {metrics['position_limit_utilization']:.0%}")

        return RiskCheckResult(
            approved=len(violations) == 0,
            violations=violations,
            warnings=warnings
        )
```

EXTEND EXISTING RISK MANAGER:
```python
# Add to existing src/risk/risk_manager.py

class RiskManager:
    # ... existing code ...

    def __init__(self, config: RiskConfig) -> None:
        # ... existing init ...
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._pre_trade_checker: Optional[PreTradeRiskChecker] = None

    def set_circuit_breaker(self, circuit_breaker: CircuitBreaker):
        """Attach circuit breaker."""
        self._circuit_breaker = circuit_breaker
        self._pre_trade_checker = PreTradeRiskChecker(self, circuit_breaker)

    def pre_trade_check(self, ticker: str, side: str, size: int, price: float) -> RiskCheckResult:
        """Run pre-trade risk checks."""
        if self._pre_trade_checker:
            return self._pre_trade_checker.check(ticker, side, size, price)

        # Fallback to basic check
        allowed, reason = self.can_trade(ticker, side, size)
        return RiskCheckResult(
            approved=allowed,
            violations=[] if allowed else [reason],
            warnings=[]
        )

    # Update update_daily_pnl to check circuit breaker
    def update_daily_pnl(self, realized_pnl: float) -> None:
        # ... existing code ...

        # Check circuit breaker thresholds
        if self._circuit_breaker:
            self._circuit_breaker.check_daily_loss(-self.daily_pnl)
```

TESTING:
- Test circuit breaker state transitions
- Test all trigger conditions
- Test manual override with audit
- Test pre-trade checks (fast path)
- Test integration with existing RiskManager
- Test alert delivery

SUCCESS CRITERIA:
- Circuit breaker trips within 1 second
- Pre-trade checks run in <50ms
- All limits enforced correctly
- Alerts delivered within 30 seconds
- Manual override audit logged
- No false trips

DELIVERABLES:
- Extended src/risk/ module
- Circuit breaker implementation
- Pre-trade risk checker
- tests/risk/ with extended tests
- Alert templates
```

---

## Phase 3: Support Systems (Run in Parallel)

---

### AGENT 8: Capital Management System

```
Build capital allocation, balance tracking, and P&L calculation system.

EXISTING CODE TO INTEGRATE WITH:
- src/core/models.py: Position for P&L calculation
- src/database/: For balance persistence (from Agent 3)
- src/monitoring/: For metrics (from Agent 4)
- src/kalshi/client.py: Get Kalshi balance
- src/polymarket/client.py: Get Polymarket balance

REQUIREMENTS:

1. Balance Tracker (src/capital/balance_tracker.py):
   - Real-time balance per platform
   - Reconciliation with platform APIs (every 1 minute)
   - Historical balance tracking
   - In-flight capital tracking (orders pending)

2. Capital Allocator (src/capital/allocator.py):
   - Position sizing based on available capital
   - Reserve capital for hedging (20% default)
   - Kelly criterion-based sizing (optional)
   - Max position limits

3. P&L Calculator (src/capital/pnl.py):
   - Per-trade P&L with fee breakdown
   - Realized vs unrealized P&L
   - Daily/weekly/monthly aggregations
   - ROI calculations

4. Rebalancer (src/capital/rebalancer.py):
   - Detect imbalances between platforms
   - Suggest rebalancing actions
   - Model settlement times

FILE STRUCTURE:
src/capital/
    __init__.py
    balance_tracker.py
    allocator.py
    pnl.py
    rebalancer.py
    reconciler.py

BALANCE TRACKER:
```python
from datetime import datetime
from typing import Dict, Optional
from src.monitoring.logger import get_logger
from src.monitoring.metrics import CAPITAL_DEPLOYED

logger = get_logger(__name__)

class BalanceTracker:
    def __init__(
        self,
        kalshi_client,
        polymarket_client,
        repository
    ):
        self._kalshi = kalshi_client
        self._polymarket = polymarket_client
        self._repository = repository
        self._balances: Dict[str, float] = {}
        self._in_flight: Dict[str, float] = {}  # Capital in pending orders

    async def sync_balances(self):
        """Sync balances from platform APIs."""

        try:
            kalshi_balance = await self._kalshi.get_balance()
            self._balances["kalshi"] = kalshi_balance
            CAPITAL_DEPLOYED.labels(platform="kalshi").set(kalshi_balance)
        except Exception as e:
            logger.error("balance_sync_failed", platform="kalshi", error=str(e))

        try:
            polymarket_balance = await self._polymarket.get_balance()
            self._balances["polymarket"] = polymarket_balance
            CAPITAL_DEPLOYED.labels(platform="polymarket").set(polymarket_balance)
        except Exception as e:
            logger.error("balance_sync_failed", platform="polymarket", error=str(e))

        # Persist snapshot
        await self._repository.save_balance_snapshot(
            self._balances,
            timestamp=datetime.now()
        )

        logger.info(
            "balances_synced",
            kalshi=self._balances.get("kalshi", 0),
            polymarket=self._balances.get("polymarket", 0)
        )

    def get_available(self, platform: str) -> float:
        """Get available capital (balance - in-flight)."""
        balance = self._balances.get(platform, 0)
        in_flight = self._in_flight.get(platform, 0)
        return max(0, balance - in_flight)

    def reserve_capital(self, platform: str, amount: float, order_id: str):
        """Reserve capital for pending order."""
        self._in_flight[platform] = self._in_flight.get(platform, 0) + amount
        logger.debug("capital_reserved", platform=platform, amount=amount, order_id=order_id)

    def release_capital(self, platform: str, amount: float, order_id: str):
        """Release reserved capital after order completes."""
        self._in_flight[platform] = max(0, self._in_flight.get(platform, 0) - amount)
        logger.debug("capital_released", platform=platform, amount=amount, order_id=order_id)
```

CAPITAL ALLOCATOR:
```python
from dataclasses import dataclass

@dataclass
class PositionSizing:
    size: int
    capital_required: float
    platform_allocation: Dict[str, float]
    reason: str

class CapitalAllocator:
    RESERVE_PCT = 0.20  # Keep 20% in reserve
    MAX_POSITION_PCT = 0.10  # Max 10% of capital per position

    def __init__(self, balance_tracker: BalanceTracker, config: dict):
        self._tracker = balance_tracker
        self._config = config

    def calculate_position_size(
        self,
        opportunity,
        target_size: Optional[int] = None
    ) -> PositionSizing:
        """Calculate optimal position size for an opportunity."""

        # Get available capital per platform
        kalshi_available = self._tracker.get_available("kalshi")
        poly_available = self._tracker.get_available("polymarket")

        # Apply reserve
        kalshi_usable = kalshi_available * (1 - self.RESERVE_PCT)
        poly_usable = poly_available * (1 - self.RESERVE_PCT)

        # Max by capital
        max_by_capital = min(kalshi_usable, poly_usable)

        # Max by position limit
        total_capital = kalshi_available + poly_available
        max_by_limit = total_capital * self.MAX_POSITION_PCT

        # Max by liquidity (from opportunity)
        max_by_liquidity = min(
            opportunity.kalshi_liquidity * 0.5,  # Only take 50% of available
            opportunity.polymarket_liquidity * 0.5
        )

        # Use minimum
        max_size_dollars = min(max_by_capital, max_by_limit, max_by_liquidity)

        # Convert to contracts
        price = max(opportunity.kalshi_price, opportunity.polymarket_price)
        max_contracts = int(max_size_dollars / price) if price > 0 else 0

        # Apply target if specified
        if target_size:
            size = min(target_size, max_contracts)
        else:
            size = max_contracts

        return PositionSizing(
            size=size,
            capital_required=size * price,
            platform_allocation={
                "kalshi": size * opportunity.kalshi_price,
                "polymarket": size * opportunity.polymarket_price
            },
            reason=f"Limited by: capital={max_by_capital:.0f}, limit={max_by_limit:.0f}, liquidity={max_by_liquidity:.0f}"
        )
```

P&L CALCULATOR:
```python
from dataclasses import dataclass
from datetime import datetime
from typing import List

@dataclass
class TradePnL:
    trade_id: str
    gross_profit: float
    kalshi_fee: float
    polymarket_fee: float
    gas_fee: float
    net_profit: float
    roi_pct: float
    holding_days: float
    annualized_roi_pct: float

class PnLCalculator:
    KALSHI_PROFIT_FEE = 0.07
    POLYMARKET_TAKER_FEE = 0.02

    def calculate_trade_pnl(self, trade) -> TradePnL:
        """Calculate P&L for a completed trade."""

        # Gross profit from spread
        gross_profit = trade.spread * trade.size

        # Fees
        kalshi_fee = max(0, gross_profit * self.KALSHI_PROFIT_FEE)
        polymarket_fee = trade.polymarket_value * self.POLYMARKET_TAKER_FEE
        gas_fee = trade.gas_paid

        total_fees = kalshi_fee + polymarket_fee + gas_fee
        net_profit = gross_profit - total_fees

        # ROI calculation
        capital_used = max(trade.kalshi_cost, trade.polymarket_cost)
        roi_pct = (net_profit / capital_used * 100) if capital_used > 0 else 0

        # Annualized
        holding_days = (trade.closed_at - trade.opened_at).total_seconds() / 86400
        annualized_roi = roi_pct * (365 / holding_days) if holding_days > 0 else roi_pct

        return TradePnL(
            trade_id=str(trade.id),
            gross_profit=gross_profit,
            kalshi_fee=kalshi_fee,
            polymarket_fee=polymarket_fee,
            gas_fee=gas_fee,
            net_profit=net_profit,
            roi_pct=roi_pct,
            holding_days=holding_days,
            annualized_roi_pct=annualized_roi
        )

    def calculate_portfolio_pnl(self, trades: List, positions: List) -> dict:
        """Calculate overall portfolio P&L."""

        realized = sum(self.calculate_trade_pnl(t).net_profit for t in trades)
        unrealized = sum(p.unrealized_pnl for p in positions)

        return {
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": realized + unrealized,
            "trade_count": len(trades),
            "open_positions": len(positions)
        }
```

TESTING:
- Test balance syncing
- Test position sizing limits
- Test P&L calculations
- Test reconciliation
- Test rebalancing logic

SUCCESS CRITERIA:
- Balance tracking 99.99% accurate
- Position sizing never exceeds limits
- P&L matches manual calculation
- Reconciliation runs every minute

DELIVERABLES:
- Complete src/capital/ module
- tests/capital/ with tests
- P&L report queries
```

---

### AGENT 9: Testing Infrastructure

```
Build comprehensive testing infrastructure with mocks, fixtures, and simulation framework.

EXISTING CODE TO INTEGRATE WITH:
- All modules need tests
- Existing patterns in src/core/models.py

REQUIREMENTS:

1. Mock Servers (tests/mocks/):
   - MockKalshiServer: REST API + WebSocket
   - MockPolymarketServer: REST API + WebSocket
   - Configurable responses, errors, latencies
   - Order fill simulation

2. Test Fixtures (tests/fixtures/):
   - Market data fixtures
   - Opportunity fixtures
   - Order book fixtures
   - Factory classes using factory_boy

3. Integration Test Framework (tests/integration/):
   - Full pipeline tests
   - Database transaction tests
   - WebSocket tests

4. Simulation Framework (tests/simulation/):
   - Historical data replay
   - Backtest runner
   - Performance benchmarks

FILE STRUCTURE:
tests/
    conftest.py           # Pytest configuration
    mocks/
        __init__.py
        kalshi_server.py
        polymarket_server.py
        websocket_mock.py
    fixtures/
        __init__.py
        factories.py
        sample_data/
            markets.json
            orderbooks.json
    unit/
        test_detection/
        test_execution/
        test_risk/
        test_capital/
    integration/
        test_full_pipeline.py
        test_database.py
    simulation/
        backtest.py
        data_loader.py

PYTEST CONFIGURATION:
```python
# tests/conftest.py
import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def db_engine():
    """Create test database engine."""
    engine = create_async_engine(
        "postgresql+asyncpg://localhost/arbitrage_test",
        echo=False
    )
    yield engine
    await engine.dispose()

@pytest.fixture
async def db_session(db_engine):
    """Create fresh session for each test."""
    async_session = sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session
        await session.rollback()

@pytest.fixture
def mock_kalshi_server():
    """Provide mock Kalshi server."""
    from tests.mocks.kalshi_server import MockKalshiServer
    server = MockKalshiServer()
    yield server
    server.reset()

@pytest.fixture
def mock_polymarket_server():
    """Provide mock Polymarket server."""
    from tests.mocks.polymarket_server import MockPolymarketServer
    server = MockPolymarketServer()
    yield server
    server.reset()

@pytest.fixture
def sample_opportunity():
    """Provide sample opportunity for testing."""
    from tests.fixtures.factories import OpportunityFactory
    return OpportunityFactory()
```

MOCK SERVERS:
```python
# tests/mocks/kalshi_server.py
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import uuid
from typing import Dict, List, Optional
from datetime import datetime

class MockKalshiServer:
    def __init__(self):
        self.app = FastAPI()
        self.orders: Dict[str, dict] = {}
        self.markets: Dict[str, dict] = {}
        self.balance = 10000.0
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/trade-api/v2/markets")
        async def get_markets():
            return {"markets": list(self.markets.values())}

        @self.app.get("/trade-api/v2/markets/{ticker}")
        async def get_market(ticker: str):
            if ticker not in self.markets:
                raise HTTPException(404, "Market not found")
            return {"market": self.markets[ticker]}

        @self.app.get("/trade-api/v2/markets/{ticker}/orderbook")
        async def get_orderbook(ticker: str):
            if ticker not in self.markets:
                raise HTTPException(404, "Market not found")
            return {
                "orderbook": {
                    "yes": [[55, 100], [54, 200]],  # [price, size]
                    "no": [[45, 100], [44, 200]]
                }
            }

        @self.app.post("/trade-api/v2/portfolio/orders")
        async def place_order(request: dict):
            order_id = str(uuid.uuid4())
            order = {
                "order_id": order_id,
                "ticker": request["ticker"],
                "side": request["side"],
                "price": request["price"],
                "size": request["count"],
                "filled_count": 0,
                "status": "resting",
                "created_time": datetime.now().isoformat()
            }
            self.orders[order_id] = order

            # Simulate immediate fill for testing
            if self._should_fill(order):
                order["status"] = "executed"
                order["filled_count"] = order["size"]

            return {"order": order}

        @self.app.delete("/trade-api/v2/portfolio/orders/{order_id}")
        async def cancel_order(order_id: str):
            if order_id not in self.orders:
                raise HTTPException(404, "Order not found")
            self.orders[order_id]["status"] = "canceled"
            return {"order": self.orders[order_id]}

        @self.app.get("/trade-api/v2/portfolio/balance")
        async def get_balance():
            return {"balance": self.balance}

    def _should_fill(self, order: dict) -> bool:
        """Determine if order should fill."""
        # Default: fill if price is aggressive
        return True

    def add_market(self, ticker: str, data: dict):
        """Add market for testing."""
        self.markets[ticker] = {
            "ticker": ticker,
            "title": data.get("title", f"Test Market {ticker}"),
            "yes_bid": data.get("yes_bid", 55),
            "yes_ask": data.get("yes_ask", 57),
            "status": "open",
            **data
        }

    def set_fill_rate(self, rate: float):
        """Set probability of orders filling."""
        self._fill_rate = rate

    def simulate_partial_fill(self, order_id: str, fill_pct: float):
        """Simulate partial fill."""
        if order_id in self.orders:
            order = self.orders[order_id]
            order["filled_count"] = int(order["size"] * fill_pct)
            order["status"] = "resting" if fill_pct < 1.0 else "executed"

    def reset(self):
        """Reset all state."""
        self.orders.clear()
        self.markets.clear()
        self.balance = 10000.0

    def get_client(self):
        """Get test client for this server."""
        return TestClient(self.app)
```

FACTORIES:
```python
# tests/fixtures/factories.py
import factory
from datetime import datetime, timedelta
import uuid

class MarketFactory(factory.Factory):
    class Meta:
        model = dict

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    platform = factory.Iterator(["KALSHI", "POLYMARKET"])
    ticker = factory.Sequence(lambda n: f"MARKET-{n}")
    title = factory.Faker("sentence", nb_words=6)
    category = factory.Iterator(["politics", "economics", "sports", "crypto"])
    close_time = factory.LazyFunction(
        lambda: (datetime.now() + timedelta(days=30)).isoformat()
    )
    yes_bid = factory.Faker("pyint", min_value=40, max_value=60)
    yes_ask = factory.LazyAttribute(lambda o: o.yes_bid + 2)

class OpportunityFactory(factory.Factory):
    class Meta:
        model = dict

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    kalshi_ticker = factory.Sequence(lambda n: f"KALSHI-{n}")
    polymarket_id = factory.Sequence(lambda n: f"POLY-{n}")
    kalshi_price = factory.Faker("pyfloat", min_value=0.50, max_value=0.60)
    polymarket_price = factory.LazyAttribute(lambda o: o.kalshi_price - 0.03)
    spread = factory.LazyAttribute(lambda o: abs(o.kalshi_price - o.polymarket_price))
    net_spread = factory.LazyAttribute(lambda o: o.spread - 0.01)
    roi = factory.LazyAttribute(lambda o: o.net_spread * 365 / 30)
    recommended_size = 100
    category = factory.Iterator(["politics", "economics"])
    detected_at = factory.LazyFunction(datetime.now)

class OrderBookFactory(factory.Factory):
    class Meta:
        model = dict

    bids = factory.LazyFunction(
        lambda: [
            {"price": 0.55, "size": 500},
            {"price": 0.54, "size": 300},
            {"price": 0.53, "size": 200}
        ]
    )
    asks = factory.LazyFunction(
        lambda: [
            {"price": 0.57, "size": 500},
            {"price": 0.58, "size": 300},
            {"price": 0.59, "size": 200}
        ]
    )
```

INTEGRATION TESTS:
```python
# tests/integration/test_full_pipeline.py
import pytest
from tests.fixtures.factories import OpportunityFactory

@pytest.mark.asyncio
async def test_full_arbitrage_execution(
    mock_kalshi_server,
    mock_polymarket_server,
    db_session
):
    """Test complete arbitrage from detection to execution."""

    # Setup markets
    mock_kalshi_server.add_market("PRES-2024", {
        "title": "Will Trump win 2024?",
        "yes_bid": 57,
        "yes_ask": 58
    })

    mock_polymarket_server.add_market("PRES-TRUMP", {
        "title": "Trump to win 2024 election",
        "yes_bid": 54,
        "yes_ask": 55
    })

    # Create clients
    from src.kalshi.client import KalshiClient
    from src.polymarket.client import PolymarketClient

    kalshi = KalshiClient(test_client=mock_kalshi_server.get_client())
    polymarket = PolymarketClient(test_client=mock_polymarket_server.get_client())

    # Run detection
    from src.detection.detector import OpportunityDetector
    detector = OpportunityDetector(kalshi, polymarket)
    opportunities = await detector.scan_once()

    assert len(opportunities) >= 1
    opp = opportunities[0]
    assert opp.spread >= 0.02

    # Execute
    from src.execution.orchestrator import ExecutionOrchestrator
    executor = ExecutionOrchestrator(kalshi, polymarket)
    result = await executor.execute_opportunity(opp)

    assert result.status == "COMPLETED"
    assert result.first_order.status == "FILLED"
    assert result.second_order.status == "FILLED"

@pytest.mark.asyncio
async def test_partial_fill_handling(mock_kalshi_server, mock_polymarket_server):
    """Test handling of partial fills."""

    opp = OpportunityFactory()

    # First leg fills completely
    mock_kalshi_server.set_fill_rate(1.0)
    # Second leg only 50% fills
    mock_polymarket_server.set_fill_rate(0.5)

    from src.execution.orchestrator import ExecutionOrchestrator
    executor = ExecutionOrchestrator(
        mock_kalshi_server.get_client(),
        mock_polymarket_server.get_client()
    )

    result = await executor.execute_opportunity(opp)

    # Should have handled partial fill
    assert result.partial_fill_handled
```

BACKTEST FRAMEWORK:
```python
# tests/simulation/backtest.py
import pandas as pd
from datetime import datetime
from typing import List
import numpy as np

class Backtester:
    def __init__(
        self,
        strategy,
        initial_capital: float = 10000.0
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades: List[dict] = []

    def run(
        self,
        historical_data: pd.DataFrame,
        start_date: datetime,
        end_date: datetime
    ) -> dict:
        """Run backtest on historical data."""

        filtered = historical_data[
            (historical_data['timestamp'] >= start_date) &
            (historical_data['timestamp'] <= end_date)
        ]

        for _, row in filtered.iterrows():
            opportunities = self.strategy.detect(row)

            for opp in opportunities:
                if self._can_execute(opp):
                    result = self._simulate_execution(opp)
                    self.trades.append(result)
                    self.capital += result['net_profit']

        return self._generate_report()

    def _simulate_execution(self, opportunity) -> dict:
        """Simulate trade execution."""

        fill_probability = min(1.0, opportunity.liquidity / opportunity.size)

        if np.random.random() > fill_probability:
            return {'status': 'unfilled', 'net_profit': 0}

        # Apply slippage
        slippage = np.random.uniform(0.001, 0.005)
        actual_spread = opportunity.spread - slippage

        gross_profit = actual_spread * opportunity.size
        fees = gross_profit * 0.07 + opportunity.size * 0.02
        net_profit = gross_profit - fees

        return {
            'status': 'filled',
            'gross_profit': gross_profit,
            'net_profit': net_profit,
            'fees': fees
        }

    def _generate_report(self) -> dict:
        """Generate backtest report."""

        filled_trades = [t for t in self.trades if t['status'] == 'filled']
        winning = [t for t in filled_trades if t['net_profit'] > 0]

        return {
            'total_trades': len(self.trades),
            'filled_trades': len(filled_trades),
            'winning_trades': len(winning),
            'win_rate': len(winning) / len(filled_trades) if filled_trades else 0,
            'total_profit': sum(t['net_profit'] for t in filled_trades),
            'avg_profit': np.mean([t['net_profit'] for t in filled_trades]) if filled_trades else 0,
            'max_drawdown': self._calculate_max_drawdown(),
            'sharpe_ratio': self._calculate_sharpe(),
            'final_capital': self.capital,
            'roi_pct': (self.capital - self.initial_capital) / self.initial_capital * 100
        }
```

TESTING:
- Run pytest with coverage: `pytest --cov=src --cov-report=html`
- All unit tests pass
- Integration tests pass
- Backtest produces valid results

SUCCESS CRITERIA:
- >90% code coverage
- All tests pass
- Mock servers behave realistically
- Backtest matches manual validation

DELIVERABLES:
- Complete tests/ directory
- Mock API servers
- Fixtures and factories
- Backtest framework
- CI configuration (GitHub Actions)
```

---

## Phase 4: Integration (Run Last)

---

### AGENT 10: Configuration & Orchestration

```
Build configuration management, CLI, and system orchestration to tie all components together.

EXISTING CODE TO INTEGRATE WITH:
- All modules from Agents 1-9
- src/core/config.py: Existing RiskConfig pattern

REQUIREMENTS:

1. Configuration Management (src/config/):
   - Environment-based configs (dev, staging, prod)
   - YAML configuration files
   - Environment variable overrides
   - Secrets management (from env vars)
   - Pydantic settings for validation

2. Application Orchestrator (src/orchestrator.py):
   - Startup sequence for all components
   - Graceful shutdown handling
   - Component lifecycle management
   - Health check aggregation

3. CLI Interface (src/cli.py):
   - Start/stop trading
   - View status and metrics
   - Manual circuit breaker control
   - Configuration validation

4. Main Entry Point (src/main.py):
   - Application bootstrap
   - Signal handlers (SIGTERM, SIGINT)
   - Logging initialization

5. Docker Setup (docker/):
   - Dockerfile for application
   - docker-compose.yml for local dev

FILE STRUCTURE:
src/
    main.py              # Entry point
    orchestrator.py      # Component orchestration
    cli.py               # CLI interface
    config/
        __init__.py
        settings.py      # Pydantic settings
        dev.yaml
        staging.yaml
        prod.yaml
docker/
    Dockerfile
    docker-compose.yml

CONFIGURATION:
```python
# src/config/settings.py
from pydantic import Field
from pydantic_settings import BaseSettings
from typing import Optional
import yaml

class PlatformSettings(BaseSettings):
    api_key: str = ""
    api_secret: str = ""
    api_url: str
    websocket_url: str
    rate_limit_rps: int = 10

class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    name: str = "arbitrage"
    user: str = "postgres"
    password: str = ""
    pool_size: int = 20

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

class RedisSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"

class RiskSettings(BaseSettings):
    max_position_size: int = 100
    max_total_position: int = 500
    max_daily_loss: float = 500.0
    max_loss_per_position: float = 100.0
    circuit_breaker_enabled: bool = True

class DetectionSettings(BaseSettings):
    scan_interval_seconds: float = 1.0
    min_spread: float = 0.01
    min_roi: float = 0.10
    min_liquidity: int = 100

class Settings(BaseSettings):
    environment: str = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    enable_trading: bool = True

    kalshi: PlatformSettings
    polymarket: PlatformSettings
    database: DatabaseSettings
    redis: RedisSettings
    risk: RiskSettings
    detection: DetectionSettings

    class Config:
        env_prefix = "ARBITRAGE_"
        env_nested_delimiter = "__"

    @classmethod
    def load(cls, env: str = "dev") -> "Settings":
        """Load settings from YAML file and environment."""

        config_path = f"src/config/{env}.yaml"

        with open(config_path) as f:
            yaml_config = yaml.safe_load(f)

        # Environment variables override YAML
        return cls(**yaml_config)

def get_settings() -> Settings:
    """Get current settings singleton."""
    import os
    env = os.getenv("ARBITRAGE_ENV", "dev")
    return Settings.load(env)
```

YAML CONFIG:
```yaml
# src/config/dev.yaml
environment: dev
log_level: DEBUG
log_json: false
enable_trading: false  # Paper trading only in dev

kalshi:
  api_url: "https://demo-api.kalshi.com/trade-api/v2"
  websocket_url: "wss://demo-api.kalshi.com/trade-api/v2/ws"
  rate_limit_rps: 10

polymarket:
  api_url: "https://clob.polymarket.com"
  websocket_url: "wss://ws-subscriptions-clob.polymarket.com/ws/market"
  rate_limit_rps: 10

database:
  host: localhost
  port: 5432
  name: arbitrage_dev
  pool_size: 10

redis:
  host: localhost
  port: 6379
  db: 0

risk:
  max_position_size: 50
  max_total_position: 200
  max_daily_loss: 100.0
  circuit_breaker_enabled: true

detection:
  scan_interval_seconds: 5.0
  min_spread: 0.02
  min_roi: 0.15
  min_liquidity: 50
```

ORCHESTRATOR:
```python
# src/orchestrator.py
import asyncio
from typing import Dict, Any, Optional
from src.config.settings import Settings
from src.monitoring.logger import setup_logging, get_logger
from src.monitoring.health import HealthChecker, HealthStatus

logger = get_logger(__name__)

class ApplicationOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.components: Dict[str, Any] = {}
        self.health_checker = HealthChecker()
        self._running = False

    async def start(self):
        """Start all components in order."""

        logger.info("starting_application", environment=self.settings.environment)

        try:
            # 1. Database
            await self._start_database()

            # 2. Redis cache
            await self._start_cache()

            # 3. Platform clients
            await self._start_platform_clients()

            # 4. Risk manager
            await self._start_risk_manager()

            # 5. Detection engine
            await self._start_detection()

            # 6. Execution engine
            await self._start_execution()

            # 7. Monitoring
            await self._start_monitoring()

            self._running = True
            logger.info("application_started")

            # Run health check
            health = await self.health_checker.check_all()
            logger.info("initial_health_check", **health)

        except Exception as e:
            logger.error("startup_failed", error=str(e))
            await self.shutdown()
            raise

    async def shutdown(self):
        """Graceful shutdown."""

        logger.info("shutting_down")

        # Reverse order
        for component_name in reversed(list(self.components.keys())):
            component = self.components[component_name]

            if hasattr(component, 'stop'):
                logger.info(f"stopping_{component_name}")
                try:
                    await component.stop()
                except Exception as e:
                    logger.error(f"error_stopping_{component_name}", error=str(e))

        self._running = False
        logger.info("shutdown_complete")

    async def _start_database(self):
        from src.database.connection import DatabaseManager

        db = DatabaseManager(self.settings.database.url)
        await db.connect()
        self.components['database'] = db

        self.health_checker.register('database', db.health_check)
        logger.info("database_connected")

    async def _start_cache(self):
        from src.database.cache import MarketCache

        cache = MarketCache(self.settings.redis.url)
        await cache.connect()
        self.components['cache'] = cache

        self.health_checker.register('cache', cache.health_check)
        logger.info("cache_connected")

    async def _start_platform_clients(self):
        from src.kalshi.client import KalshiClient
        from src.polymarket.client import PolymarketClient
        import os

        # Kalshi
        kalshi = KalshiClient(
            api_key=os.getenv("KALSHI_API_KEY", ""),
            api_secret=os.getenv("KALSHI_API_SECRET", ""),
            base_url=self.settings.kalshi.api_url
        )
        await kalshi.connect()
        self.components['kalshi'] = kalshi
        self.health_checker.register('kalshi', kalshi.health_check)

        # Polymarket
        polymarket = PolymarketClient(
            private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
            api_url=self.settings.polymarket.api_url
        )
        await polymarket.connect()
        self.components['polymarket'] = polymarket
        self.health_checker.register('polymarket', polymarket.health_check)

        logger.info("platform_clients_connected")

    async def _start_risk_manager(self):
        from src.risk.risk_manager import RiskManager
        from src.risk.circuit_breaker import CircuitBreaker
        from src.core.config import RiskConfig

        risk_config = RiskConfig(
            max_position_size=self.settings.risk.max_position_size,
            max_total_position=self.settings.risk.max_total_position,
            max_loss_per_position=self.settings.risk.max_loss_per_position,
            max_daily_loss=self.settings.risk.max_daily_loss
        )

        risk_manager = RiskManager(risk_config)

        if self.settings.risk.circuit_breaker_enabled:
            circuit_breaker = CircuitBreaker(
                alert_manager=self.components.get('alerts'),
                config=self.settings.risk.model_dump()
            )
            risk_manager.set_circuit_breaker(circuit_breaker)

        self.components['risk_manager'] = risk_manager
        logger.info("risk_manager_started")

    async def _start_detection(self):
        from src.detection.detector import OpportunityDetector

        detector = OpportunityDetector(
            kalshi_client=self.components['kalshi'],
            polymarket_client=self.components['polymarket'],
            config=self.settings.detection.model_dump()
        )

        if self.settings.enable_trading:
            await detector.start()

        self.components['detector'] = detector
        logger.info("detection_engine_started")

    async def _start_execution(self):
        from src.execution.orchestrator import ExecutionOrchestrator

        executor = ExecutionOrchestrator(
            kalshi_client=self.components['kalshi'],
            polymarket_client=self.components['polymarket'],
            risk_manager=self.components['risk_manager']
        )

        self.components['executor'] = executor
        logger.info("execution_engine_started")

    async def _start_monitoring(self):
        from src.monitoring.metrics import MetricsCollector

        metrics = MetricsCollector(port=9090)
        metrics.start()

        self.components['metrics'] = metrics
        logger.info("monitoring_started")
```

CLI:
```python
# src/cli.py
import click
import asyncio
from src.config.settings import Settings
from src.orchestrator import ApplicationOrchestrator

@click.group()
def cli():
    """Arbitrage Trading System CLI"""
    pass

@cli.command()
@click.option('--env', default='dev', help='Environment (dev/staging/prod)')
def start(env):
    """Start the trading system."""

    from src.monitoring.logger import setup_logging

    settings = Settings.load(env)
    setup_logging(settings.log_level, settings.log_json)

    orchestrator = ApplicationOrchestrator(settings)

    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(orchestrator.start())
        click.echo(f"System started in {env} mode")
        loop.run_forever()
    except KeyboardInterrupt:
        click.echo("Shutting down...")
        loop.run_until_complete(orchestrator.shutdown())

@cli.command()
def status():
    """Show system status."""
    # Connect to running system via health endpoint
    import httpx

    try:
        response = httpx.get("http://localhost:8080/health")
        data = response.json()

        click.echo("System Status:")
        click.echo(f"  Overall: {data['status']}")
        click.echo("  Components:")
        for name, status in data['components'].items():
            color = 'green' if status == 'healthy' else 'red'
            click.secho(f"    {name}: {status}", fg=color)
    except Exception as e:
        click.secho(f"Error: {e}", fg='red')

@cli.command()
@click.option('--env', default='dev')
def validate_config(env):
    """Validate configuration file."""
    try:
        settings = Settings.load(env)
        click.secho(f"Configuration for {env} is valid", fg='green')
        click.echo(f"  Trading enabled: {settings.enable_trading}")
        click.echo(f"  Log level: {settings.log_level}")
    except Exception as e:
        click.secho(f"Configuration error: {e}", fg='red')

if __name__ == '__main__':
    cli()
```

MAIN ENTRY POINT:
```python
# src/main.py
import asyncio
import signal
import sys
from src.config.settings import Settings, get_settings
from src.orchestrator import ApplicationOrchestrator
from src.monitoring.logger import setup_logging, get_logger

logger = get_logger(__name__)

async def main():
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)

    orchestrator = ApplicationOrchestrator(settings)

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("received_shutdown_signal")
        asyncio.create_task(orchestrator.shutdown())
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await orchestrator.start()

        # Keep running
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error("fatal_error", error=str(e))
        await orchestrator.shutdown()
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
```

DOCKER:
```dockerfile
# docker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health/live')"

ENV ARBITRAGE_ENV=prod

CMD ["python", "-m", "src.main"]
```

```yaml
# docker/docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: arbitrage
      POSTGRES_USER: arbitrage
      POSTGRES_PASSWORD: ${DB_PASSWORD:-dev_password}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  arbitrage:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    depends_on:
      - postgres
      - redis
    environment:
      - ARBITRAGE_ENV=dev
      - KALSHI_API_KEY=${KALSHI_API_KEY}
      - KALSHI_API_SECRET=${KALSHI_API_SECRET}
      - POLYMARKET_PRIVATE_KEY=${POLYMARKET_PRIVATE_KEY}
      - ARBITRAGE_DATABASE__HOST=postgres
      - ARBITRAGE_REDIS__HOST=redis
    ports:
      - "8080:8080"
      - "9090:9090"

volumes:
  postgres_data:
```

TESTING:
- Test configuration loading for all environments
- Test orchestrator startup/shutdown
- Test CLI commands
- Test Docker build

SUCCESS CRITERIA:
- All configurations load correctly
- Clean startup/shutdown
- CLI commands work
- Docker builds and runs
- Health checks accurate

DELIVERABLES:
- Complete configuration system
- Orchestrator
- CLI
- Docker setup
- Documentation
```

---

## Execution Commands

To run these agents in parallel:

```bash
# Phase 1 (run all 4 in parallel)
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 1:/,/AGENT 2:/p')" &
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 2:/,/AGENT 3:/p')" &
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 3:/,/AGENT 4:/p')" &
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 4:/,/Phase 2:/p')" &
wait

# Phase 2 (run after Phase 1 completes)
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 5:/,/AGENT 6:/p')" &
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 6:/,/AGENT 7:/p')" &
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 7:/,/Phase 3:/p')" &
wait

# Phase 3 (run after Phase 2 completes)
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 8:/,/AGENT 9:/p')" &
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 9:/,/Phase 4:/p')" &
wait

# Phase 4 (run last)
claude --prompt "$(cat agent_prompts.md | sed -n '/AGENT 10:/,/Execution Commands/p')"
```
