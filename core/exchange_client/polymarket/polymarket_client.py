"""Polymarket Exchange Client - Implementation of I_ExchangeClient for Polymarket CLOB API."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from ..i_exchange_client import I_ExchangeClient
from .polymarket_auth import PolymarketAuth
from .polymarket_types import (
    PolymarketBalance,
    PolymarketPosition,
    PolymarketMarketData,
    PolymarketOrderResponse,
)
from .polymarket_exceptions import (
    PolymarketAuthError,
    PolymarketNotFoundError,
    PolymarketBadRequestError,
    PolymarketTimeoutError,
    PolymarketMaxRetriesError,
)

logger = logging.getLogger(__name__)

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx package not installed. Install with: pip install httpx")


CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


class RateLimiter:
    """Token bucket rate limiter.

    Thread-safe rate limiting using token bucket algorithm.
    Tokens refill continuously at the configured rate.
    """

    def __init__(self, requests_per_second: float = 10.0):
        self._rps = requests_per_second
        self._tokens = requests_per_second
        self._last_update = time.monotonic()
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


class PolymarketExchangeClient(I_ExchangeClient):
    """Polymarket CLOB API client implementing I_ExchangeClient interface.

    Provides async access to Polymarket trading API with:
    - Wallet-based L2 authentication
    - EIP-712 order signing
    - Rate limiting
    - Automatic retry with exponential backoff

    Uses two base URLs:
    - CLOB (clob.polymarket.com) for orders/trading
    - Gamma (gamma-api.polymarket.com) for market data

    Example:
        >>> client = PolymarketExchangeClient.from_env()
        >>> await client.connect()
        >>> markets = await client.get_markets()
        >>> balance = await client.get_balance()
        >>> await client.exit()
    """

    def __init__(
        self,
        auth: PolymarketAuth,
        base_url: Optional[str] = None,
        gamma_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        requests_per_second: float = 10.0,
    ):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx package required. Install with: pip install httpx")

        self._auth = auth
        self._base_url = base_url or CLOB_BASE_URL
        self._gamma_url = gamma_url or GAMMA_BASE_URL
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
        self._gamma_client: Optional[httpx.AsyncClient] = None
        self._rate_limiter = RateLimiter(requests_per_second)
        self._connected = False

    @classmethod
    def from_env(cls, **kwargs) -> "PolymarketExchangeClient":
        """Create client from environment variables.

        Requires POLYMARKET_PRIVATE_KEY to be set.
        """
        auth = PolymarketAuth.from_env()
        return cls(auth=auth, **kwargs)

    # --- I_ExchangeClient Properties ---

    @property
    def name(self) -> str:
        return "polymarket"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    # --- I_ExchangeClient Methods ---

    async def connect(self) -> None:
        limits = httpx.Limits(
            max_keepalive_connections=100,
            max_connections=200,
            keepalive_expiry=30.0,
        )
        common_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=limits,
                headers=common_headers,
            )

        if self._gamma_client is None:
            self._gamma_client = httpx.AsyncClient(
                base_url=self._gamma_url,
                timeout=self._timeout,
                limits=limits,
                headers=common_headers,
            )

        try:
            # Verify connectivity with a lightweight Gamma API call
            response = await self._gamma_client.get("/markets", params={"limit": 1})
            response.raise_for_status()
            self._connected = True
            logger.info("Connected to Polymarket API")
        except Exception as e:
            logger.error(f"Failed to connect to Polymarket: {e}")
            raise

    async def exit(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._gamma_client:
            await self._gamma_client.aclose()
            self._gamma_client = None
        self._connected = False
        logger.info("Disconnected from Polymarket API")

    async def request_market(self, ticker: str) -> PolymarketMarketData:
        """Get market data by condition_id.

        Args:
            ticker: Polymarket condition_id

        Returns:
            PolymarketMarketData
        """
        data = await self._request("GET", f"/markets/{ticker}", use_gamma=True)
        return PolymarketMarketData.from_api(data)

    async def get_markets(
        self,
        series_ticker: Optional[str] = None,
        status: str = "open",
        limit: int = 100,
    ) -> List[PolymarketMarketData]:
        """Get markets with optional filters.

        Args:
            series_ticker: Filter by slug/tag (mapped to Gamma API)
            status: Filter by status ("open", "closed")
            limit: Max results

        Returns:
            List of PolymarketMarketData objects
        """
        params: Dict[str, Any] = {"limit": limit}
        if status == "open":
            params["active"] = True
            params["closed"] = False
        elif status == "closed":
            params["closed"] = True
        if series_ticker:
            params["slug"] = series_ticker

        data = await self._request("GET", "/markets", params=params, use_gamma=True)
        # Gamma API returns a list directly
        markets_list = (
            data
            if isinstance(data, list)
            else data.get("markets", data.get("data", []))
        )
        return [PolymarketMarketData.from_api(m) for m in markets_list]

    async def get_balance(self) -> PolymarketBalance:
        """Get USDC balance via on-chain query.

        Uses asyncio.to_thread to wrap the sync web3 call.
        """
        from src.polymarket.blockchain import PolygonClient

        polygon = PolygonClient(wallet=self._auth.wallet)
        balance = await asyncio.to_thread(polygon.get_usdc_balance)
        return PolymarketBalance(balance_usdc=balance)

    async def get_positions(
        self, ticker: Optional[str] = None
    ) -> List[PolymarketPosition]:
        """Get current positions.

        Args:
            ticker: Optional condition_id filter

        Returns:
            List of positions
        """
        params: Dict[str, Any] = {}
        if ticker:
            params["condition_id"] = ticker

        # Positions are queried via the CLOB API
        data = await self._request(
            "GET", "/positions", params=params if params else None
        )
        positions_list = data if isinstance(data, list) else data.get("positions", [])
        return [PolymarketPosition.from_api(p) for p in positions_list]

    # --- Extended Methods (beyond interface) ---

    async def create_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> PolymarketOrderResponse:
        """Create a new order on the CLOB.

        Args:
            token_id: Token ID for the outcome
            side: "BUY" or "SELL"
            price: Price as probability (0-1)
            size: Number of shares

        Returns:
            PolymarketOrderResponse
        """
        order_data = {
            "tokenID": token_id,
            "price": str(price),
            "size": str(size),
            "side": side.upper(),
            "feeRateBps": "0",
            "nonce": "0",
            "expiration": "0",
            "taker": "0x0000000000000000000000000000000000000000",
        }

        # Sign the order with EIP-712
        signature = self._auth.sign_order(order_data)
        order_data["signature"] = signature

        data = await self._request("POST", "/order", json_body=order_data)
        return PolymarketOrderResponse.from_api(data)

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an existing order.

        Args:
            order_id: Order ID to cancel

        Returns:
            API response
        """
        return await self._request("DELETE", f"/order/{order_id}")

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get details of a specific order.

        Args:
            order_id: Order ID

        Returns:
            Order details
        """
        return await self._request("GET", f"/order/{order_id}")

    async def get_orders(self) -> List[Dict[str, Any]]:
        """Get all orders.

        Returns:
            List of order dicts
        """
        data = await self._request("GET", "/orders")
        return data if isinstance(data, list) else data.get("orders", [])

    async def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        """Get orderbook for a token.

        Args:
            token_id: Token ID

        Returns:
            Orderbook data
        """
        return await self._request("GET", "/book", params={"token_id": token_id})

    async def get_fills(self) -> List[Dict[str, Any]]:
        """Get recent trades/fills.

        Returns:
            List of trade dicts
        """
        data = await self._request("GET", "/trades")
        return data if isinstance(data, list) else data.get("trades", [])

    # --- Internal ---

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        use_gamma: bool = False,
    ) -> Any:
        """Make authenticated request with retry logic.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint
            params: Optional query parameters
            json_body: Optional JSON body for POST requests
            use_gamma: If True, use Gamma API client instead of CLOB

        Returns:
            Parsed JSON response

        Raises:
            PolymarketAuthError: Authentication failed
            PolymarketNotFoundError: Resource not found
            PolymarketRateLimitError: Rate limit exceeded (after retries)
            PolymarketBadRequestError: Invalid request
            PolymarketTimeoutError: Request timed out
            PolymarketMaxRetriesError: Max retries exceeded
        """
        if self._client is None:
            await self.connect()

        client = self._gamma_client if use_gamma else self._client
        request_id = str(uuid.uuid4())[:8]

        # Serialize body for signing
        body_str = json.dumps(json_body, separators=(",", ":")) if json_body else ""

        # Exponential backoff delays
        delays = [min(2**i, 10) for i in range(self._max_retries)]

        for attempt in range(self._max_retries):
            await self._rate_limiter.acquire()

            # Auth headers only for CLOB API (Gamma is public)
            headers = {}
            if not use_gamma:
                headers = self._auth.sign_request(method, endpoint, body_str)

            try:
                logger.debug(
                    f"[{request_id}] {method} {endpoint} (attempt {attempt + 1})"
                )

                response = await client.request(
                    method,
                    endpoint,
                    params=params,
                    json=json_body,
                    headers=headers,
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
                    raise PolymarketAuthError(
                        "Authentication failed - check private key and signature"
                    )

                # Handle not found (don't retry)
                if response.status_code == 404:
                    raise PolymarketNotFoundError(endpoint)

                # Handle bad request (don't retry)
                if response.status_code == 400:
                    error_msg = "Bad request"
                    try:
                        error_data = response.json()
                        if isinstance(error_data, dict):
                            error_msg = error_data.get(
                                "error", error_data.get("message", error_msg)
                            )
                    except Exception:
                        pass
                    raise PolymarketBadRequestError(error_msg)

                response.raise_for_status()
                return response.json() if response.content else {}

            except httpx.TimeoutException:
                logger.warning(f"[{request_id}] Timeout on attempt {attempt + 1}")
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(delays[attempt])
                    continue
                raise PolymarketTimeoutError(self._timeout)

            except (
                PolymarketAuthError,
                PolymarketNotFoundError,
                PolymarketBadRequestError,
            ):
                raise

        raise PolymarketMaxRetriesError(method, endpoint, self._max_retries)

    # --- Context Manager ---

    async def __aenter__(self) -> "PolymarketExchangeClient":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.exit()
