"""Kalshi Exchange Client - Implementation of I_ExchangeClient for Kalshi API."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from ..i_exchange_client import I_ExchangeClient
from .kalshi_auth import KalshiAuth
from .kalshi_types import (
    KalshiBalance,
    KalshiPosition,
    KalshiMarketData,
    KalshiOrderResponse,
)
from .kalshi_exceptions import (
    KalshiAuthError,
    KalshiNotFoundError,
    KalshiBadRequestError,
    KalshiTimeoutError,
    KalshiMaxRetriesError,
)

logger = logging.getLogger(__name__)

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx package not installed. Install with: pip install httpx")


class RateLimiter:
    """Token bucket rate limiter.

    Thread-safe rate limiting using token bucket algorithm.
    Tokens refill continuously at the configured rate.
    """

    def __init__(self, requests_per_second: float = 10.0):
        self._rps = requests_per_second
        self._tokens = requests_per_second
        self._last_update = time.monotonic()
        # Initialize lock immediately to avoid race condition
        # Previously, lazy initialization could create multiple locks
        # if multiple coroutines called _get_lock() simultaneously
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request can be made."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self._rps, self._tokens + elapsed * self._rps)
            self._last_update = now

            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self._rps
                await asyncio.sleep(wait_time)
                self._tokens = 0
            else:
                self._tokens -= 1


class KalshiExchangeClient(I_ExchangeClient):
    """Kalshi API client implementing I_ExchangeClient interface.

    Provides async access to Kalshi trading API with:
    - RSA-PSS authentication
    - Rate limiting
    - Automatic retry with exponential backoff

    Example:
        >>> client = KalshiExchangeClient.from_env()
        >>> await client.connect()
        >>> markets = await client.get_markets(series_ticker="KXNBAGAME")
        >>> balance = await client.get_balance()
        >>> await client.exit()
    """

    PRODUCTION_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"

    def __init__(
        self,
        auth: KalshiAuth,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        requests_per_second: float = 10.0,
    ):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx package required. Install with: pip install httpx")

        self._auth = auth
        self._base_url = base_url or self.PRODUCTION_URL
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_limiter = RateLimiter(requests_per_second)
        self._connected = False

    @classmethod
    def from_env(cls, demo: bool = False, **kwargs) -> "KalshiExchangeClient":
        """Create client from environment variables or demo credentials.

        Args:
            demo: If True, use demo credentials from keys/demo/ folder
        """
        if demo:
            # Load demo credentials from keys/demo/ folder
            from pathlib import Path

            keys_dir = Path(__file__).parent.parent.parent.parent / "keys" / "demo"

            # Read demo API key ID
            demo_id_file = keys_dir / "demo_id.txt"
            if not demo_id_file.exists():
                raise ValueError(f"Demo API ID file not found: {demo_id_file}")
            api_key = demo_id_file.read_text().strip()

            # Read demo private key
            demo_key_file = keys_dir / "demo_key.pem"
            if not demo_key_file.exists():
                raise ValueError(f"Demo private key file not found: {demo_key_file}")
            api_secret = demo_key_file.read_text()

            auth = KalshiAuth(api_key, api_secret)
            url = cls.DEMO_URL
        else:
            auth = KalshiAuth.from_env()
            url = cls.PRODUCTION_URL
        return cls(auth=auth, base_url=url, **kwargs)

    @classmethod
    def from_user(cls, username: str, **kwargs) -> "KalshiExchangeClient":
        """Create client from a user's key folder (keys/{username}/).

        Args:
            username: User profile name (e.g., "liam", "demo")

        Returns:
            KalshiExchangeClient configured for that user.
            Uses DEMO_URL for "demo" user, PRODUCTION_URL for all others.
        """
        auth = KalshiAuth.from_user(username)
        url = cls.DEMO_URL if username == "demo" else cls.PRODUCTION_URL
        return cls(auth=auth, base_url=url, **kwargs)

    # --- I_ExchangeClient Properties ---

    @property
    def name(self) -> str:
        return "kalshi"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    # --- I_ExchangeClient Methods ---

    async def connect(self) -> None:
        if self._client is None:
            # Configure connection pooling for better HFT performance
            limits = httpx.Limits(
                max_keepalive_connections=100,
                max_connections=200,
                keepalive_expiry=30.0,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=limits,
                http2=True,  # Enable HTTP/2 for multiplexing and faster requests
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

        try:
            await self._request("GET", "/exchange/status")
            self._connected = True
            logger.info("Connected to Kalshi API")
        except Exception as e:
            logger.error(f"Failed to connect to Kalshi: {e}")
            raise

    async def exit(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        logger.info("Disconnected from Kalshi API")

    async def request_market(self, ticker: str) -> KalshiMarketData:
        data = await self._request("GET", f"/markets/{ticker}")
        return KalshiMarketData.from_api(data.get("market", data))

    async def get_markets(
        self,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        tickers: Optional[List[str]] = None,
        status: str = "open",
        limit: int = 1000,
    ) -> List[KalshiMarketData]:
        """Get markets with optional filters.

        Args:
            series_ticker: Filter by series (e.g., "KXNBASPREAD")
            event_ticker: Filter by event (e.g., "KXNBASPREAD-26FEB10SASLAL")
            tickers: List of specific tickers to fetch
            status: Filter by status ("open", "closed", etc.)
            limit: Max results per page

        Returns:
            List of KalshiMarketData objects
        """
        params = {"limit": limit, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if tickers:
            params["tickers"] = ",".join(tickers)

        data = await self._request("GET", "/markets", params=params)
        return [KalshiMarketData.from_api(m) for m in data.get("markets", [])]

    async def get_markets_for_event(
        self,
        event_ticker: str,
        status: str = "open",
    ) -> List[KalshiMarketData]:
        """Get all markets for a specific event.

        Faster than fetching all markets - uses API filtering.

        Args:
            event_ticker: Event ticker (e.g., "KXNBASPREAD-26FEB10SASLAL")
            status: Filter by status

        Returns:
            List of markets for this event
        """
        return await self.get_markets(event_ticker=event_ticker, status=status)

    async def get_nba_game_markets(
        self,
        team1: str,
        team2: str,
        date: str,
        series_types: Optional[List[str]] = None,
    ) -> List[KalshiMarketData]:
        """Get all markets for a specific NBA game.

        Much faster than fetching all markets - queries each series directly.

        Args:
            team1: Away team code (e.g., "SAS")
            team2: Home team code (e.g., "LAL")
            date: Game date in "YYMMMDD" format (e.g., "26FEB10")
            series_types: List of series to fetch. Defaults to spread/total/game.

        Returns:
            List of markets for this game
        """
        if series_types is None:
            series_types = [
                "KXNBASPREAD",
                "KXNBA1HSPREAD",
                "KXNBATOTAL",
                "KXNBA1HTOTAL",
                "KXNBAGAME",
            ]

        event_suffix = f"{date}{team1}{team2}"
        all_markets = []

        for series in series_types:
            event_ticker = f"{series}-{event_suffix}"
            try:
                markets = await self.get_markets_for_event(event_ticker)
                all_markets.extend(markets)
            except Exception:
                pass  # Series may not exist for this game

        return all_markets

    async def get_balance(self) -> KalshiBalance:
        data = await self._request("GET", "/portfolio/balance")
        return KalshiBalance(
            balance_cents=data.get("balance", 0),
            portfolio_value_cents=data.get("portfolio_value", 0),
            available_balance_cents=data.get(
                "available_balance", data.get("balance", 0)
            ),
        )

    async def get_positions(self, ticker: Optional[str] = None) -> List[KalshiPosition]:
        params = {}
        if ticker:
            params["ticker"] = ticker

        data = await self._request(
            "GET", "/portfolio/positions", params=params if params else None
        )
        return [KalshiPosition.from_api(p) for p in data.get("market_positions", [])]

    # --- Additional Methods ---

    async def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}
        )

    async def get_fills(
        self, ticker: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = await self._request("GET", "/portfolio/fills", params=params)
        return data.get("fills", [])

    async def get_orders(
        self, ticker: Optional[str] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        data = await self._request(
            "GET", "/portfolio/orders", params=params if params else None
        )
        return data.get("orders", [])

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get details of a specific order.

        Args:
            order_id: Order ID

        Returns:
            Order details
        """
        data = await self._request("GET", f"/portfolio/orders/{order_id}")
        return data.get("order", data)

    async def create_order(
        self,
        ticker: str,
        action: str,
        side: str,
        count: int,
        type: str = "limit",
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        expiration_ts: Optional[int] = None,
        sell_position_floor: Optional[int] = None,
        buy_max_cost: Optional[int] = None,
    ) -> KalshiOrderResponse:
        """Create a new order.

        Args:
            ticker: Market ticker
            action: 'buy' or 'sell'
            side: 'yes' or 'no'
            count: Number of contracts
            type: 'limit' or 'market'
            yes_price: Price for YES orders (cents)
            no_price: Price for NO orders (cents)

        Returns:
            Order response object
        """
        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": type,
            "client_order_id": str(uuid.uuid4()),
        }

        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        if expiration_ts is not None:
            body["expiration_ts"] = expiration_ts
        if sell_position_floor is not None:
            body["sell_position_floor"] = sell_position_floor
        if buy_max_cost is not None:
            body["buy_max_cost"] = buy_max_cost

        data = await self._request("POST", "/portfolio/orders", json_body=body)
        # API returns { "order": { ... } }
        return KalshiOrderResponse.from_api(data.get("order", data))

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an existing order.

        Args:
            order_id: Order ID to cancel

        Returns:
            API response
        """
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")

    # --- Internal ---

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make authenticated request with retry logic.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint (without /trade-api/v2 prefix)
            params: Optional query parameters
            json_body: Optional JSON body for POST requests

        Returns:
            Parsed JSON response

        Raises:
            KalshiAuthError: Authentication failed
            KalshiNotFoundError: Resource not found
            KalshiRateLimitError: Rate limit exceeded (after retries)
            KalshiBadRequestError: Invalid request
            KalshiTimeoutError: Request timed out
            KalshiMaxRetriesError: Max retries exceeded
        """
        if self._client is None:
            await self.connect()

        # Generate request ID for log correlation
        request_id = str(uuid.uuid4())[:8]
        full_path = f"/trade-api/v2{endpoint}"
        body_str = json.dumps(json_body) if json_body else ""

        # Exponential backoff delays based on max_retries
        delays = [min(2**i, 10) for i in range(self._max_retries)]

        for attempt in range(self._max_retries):
            await self._rate_limiter.acquire()
            auth_headers = self._auth.sign_request(method, full_path, body_str)

            try:
                logger.debug(
                    f"[{request_id}] {method} {endpoint} (attempt {attempt + 1})"
                )

                response = await self._client.request(
                    method,
                    endpoint,
                    params=params,
                    json=json_body,
                    headers=auth_headers,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(
                        response.headers.get("Retry-After", delays[attempt])
                    )
                    logger.warning(
                        f"[{request_id}] Rate limited, waiting {retry_after}s"
                    )
                    await asyncio.sleep(retry_after)
                    continue

                # Handle authentication errors (don't retry)
                if response.status_code == 401:
                    logger.error(f"[{request_id}] Authentication failed")
                    raise KalshiAuthError(
                        "Authentication failed - check API key and signature"
                    )

                # Handle not found (don't retry)
                if response.status_code == 404:
                    raise KalshiNotFoundError(endpoint)

                # Handle bad request (don't retry)
                if response.status_code == 400:
                    error_msg = "Bad request"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", {}).get(
                            "message", error_msg
                        )
                    except Exception:
                        pass
                    raise KalshiBadRequestError(error_msg)

                response.raise_for_status()
                return response.json() if response.content else {}

            except httpx.TimeoutException:
                logger.warning(f"[{request_id}] Timeout on attempt {attempt + 1}")
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(delays[attempt])
                    continue
                raise KalshiTimeoutError(self._timeout)

            except (KalshiAuthError, KalshiNotFoundError, KalshiBadRequestError):
                # Don't retry these errors
                raise

        raise KalshiMaxRetriesError(method, endpoint, self._max_retries)

    # --- Context Manager ---

    async def __aenter__(self) -> "KalshiExchangeClient":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.exit()
