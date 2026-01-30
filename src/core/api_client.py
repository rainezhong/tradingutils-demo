"""Kalshi API client with rate limiting and retry logic."""

import base64
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Optional

import requests

from .config import Config, get_config
from .models import Market
from .rate_limiter import Priority, get_shared_rate_limiter
from .utils import setup_logger

logger = setup_logger(__name__)

# Optional cryptography import for authenticated requests
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.debug("cryptography not installed - authenticated requests disabled")


class RateLimiter:
    """Token bucket rate limiter for API requests."""

    def __init__(self, requests_per_second: int, requests_per_minute: int):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second
            requests_per_minute: Maximum requests per minute
        """
        self.rps = requests_per_second
        self.rpm = requests_per_minute
        self.second_timestamps: deque = deque()
        self.minute_timestamps: deque = deque()
        self.lock = Lock()

    def acquire(self) -> None:
        """
        Block until a request can be made within rate limits.

        This method is thread-safe.
        """
        with self.lock:
            now = time.time()

            # Clean old timestamps
            while self.second_timestamps and now - self.second_timestamps[0] > 1:
                self.second_timestamps.popleft()
            while self.minute_timestamps and now - self.minute_timestamps[0] > 60:
                self.minute_timestamps.popleft()

            # Wait if at per-second limit
            if len(self.second_timestamps) >= self.rps:
                sleep_time = 1 - (now - self.second_timestamps[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    now = time.time()
                    # Re-clean after sleeping
                    while self.second_timestamps and now - self.second_timestamps[0] > 1:
                        self.second_timestamps.popleft()

            # Wait if at per-minute limit
            if len(self.minute_timestamps) >= self.rpm:
                sleep_time = 60 - (now - self.minute_timestamps[0])
                if sleep_time > 0:
                    logger.warning(f"Rate limit reached, waiting {sleep_time:.1f}s")
                    time.sleep(sleep_time)
                    now = time.time()
                    while self.minute_timestamps and now - self.minute_timestamps[0] > 60:
                        self.minute_timestamps.popleft()

            # Record this request
            self.second_timestamps.append(now)
            self.minute_timestamps.append(now)

            # Add minimum spacing between requests (150ms) to avoid 429 errors
            time.sleep(0.15)


class KalshiClient:
    """Client for Kalshi market data API with rate limiting and optional authentication."""

    def __init__(
        self,
        config: Optional[Config] = None,
        priority: Priority = Priority.NORMAL,
    ):
        """
        Initialize the API client.

        Args:
            config: Configuration instance. Uses global config if not provided.
            priority: Default priority for rate limiting. Use Priority.TRADING for
                     trading bots, Priority.BACKGROUND for collectors.
        """
        self.config = config or get_config()
        self.base_url = self.config.api_base_url
        self.timeout = self.config.api_timeout
        self.max_retries = self.config.api_max_retries
        self._priority = priority

        # Authentication setup
        self.api_key_id = self.config.api_key_id
        self.private_key = None
        self._load_private_key()

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        # Use shared rate limiter for coordination across all clients
        self._rate_limiter = get_shared_rate_limiter(
            requests_per_second=self.config.rate_limits.requests_per_second,
            requests_per_minute=self.config.rate_limits.requests_per_minute,
        )

        # Keep legacy rate_limiter property for backwards compatibility
        self.rate_limiter = self._rate_limiter

    def _load_private_key(self) -> None:
        """Load private key from file if configured."""
        if not self.config.api_private_key_path or not self.config.api_key_id:
            return

        if not CRYPTO_AVAILABLE:
            logger.warning("cryptography package not installed - cannot use API authentication")
            return

        key_path = Path(self.config.api_private_key_path)
        if not key_path.exists():
            logger.warning(f"Private key file not found: {key_path}")
            return

        try:
            with open(key_path, "rb") as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend(),
                )
            logger.info("Loaded API private key - authenticated requests enabled")
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")

    def _sign_request(self, method: str, path: str) -> dict:
        """
        Generate authentication headers for a request.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path (without query params)

        Returns:
            Dict of auth headers, or empty dict if not authenticated
        """
        if not self.private_key or not self.api_key_id:
            return {}

        # Get timestamp in milliseconds
        timestamp_ms = int(time.time() * 1000)
        timestamp_str = str(timestamp_ms)

        # Create message to sign: timestamp + method + path
        message = f"{timestamp_str}{method}{path}"

        # Sign with RSA-PSS
        signature = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        }

    @property
    def is_authenticated(self) -> bool:
        """Check if client has valid authentication configured."""
        return self.private_key is not None and bool(self.api_key_id)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        """
        Make HTTP request with retry logic and rate limiting.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters (for GET requests)
            json_body: JSON body (for POST/PUT/DELETE requests)

        Returns:
            JSON response as dictionary

        Raises:
            requests.exceptions.HTTPError: On non-retryable HTTP errors
            requests.exceptions.RequestException: On network errors after retries
        """
        url = f"{self.base_url}{endpoint}"
        delays = [2, 5, 10, 20, 30]  # Longer backoff delays for rate limits

        # Build full path for signing (endpoint without query params)
        full_path = f"/trade-api/v2{endpoint}"

        for attempt in range(self.max_retries):
            # Respect rate limits with priority
            self._rate_limiter.acquire(self._priority)

            # Generate auth headers (fresh signature each attempt)
            auth_headers = self._sign_request(method, full_path)

            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=auth_headers,
                    timeout=self.timeout,
                )

                # Handle rate limit response
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", delays[attempt]))
                    logger.warning(f"Rate limited, retrying after {retry_after}s")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout (attempt {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(delays[attempt])
                    continue
                raise

            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Connection error (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(delays[attempt])
                    continue
                raise

            except requests.exceptions.HTTPError as e:
                # Retry on server errors (5xx)
                if response.status_code >= 500 and attempt < self.max_retries - 1:
                    logger.warning(f"Server error {response.status_code}, retrying...")
                    time.sleep(delays[attempt])
                    continue
                raise

        return {}

    def get_markets(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        Fetch a single page of markets.

        Args:
            status: Filter by status ('open', 'closed', etc.)
            limit: Number of results per page (max 100)
            cursor: Pagination cursor

        Returns:
            API response with 'markets' list and 'cursor' for pagination
        """
        params = {"limit": min(limit, 100)}

        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/markets", params=params)

    def get_all_markets(self, min_volume: Optional[int] = None) -> list[Market]:
        """
        Fetch all markets with pagination, optionally filtered by volume.

        Args:
            min_volume: Minimum 24h volume filter (uses config default if None)

        Returns:
            List of Market instances
        """
        min_vol = min_volume if min_volume is not None else self.config.min_volume
        all_markets = []
        cursor = None

        while True:
            response = self.get_markets(status="open", limit=100, cursor=cursor)
            markets = response.get("markets", [])

            if not markets:
                break

            for market_data in markets:
                volume = market_data.get("volume_24h", 0) or 0
                if volume >= min_vol:
                    try:
                        market = Market.from_api_response(market_data)
                        all_markets.append(market)
                    except Exception as e:
                        logger.warning(f"Failed to parse market: {e}")

            cursor = response.get("cursor")
            if not cursor:
                break

        logger.info(f"Fetched {len(all_markets)} markets with volume >= {min_vol}")
        return all_markets

    def get_all_markets_with_prices(self) -> list[dict]:
        """
        Fetch all markets with price data for snapshot creation.

        Returns:
            List of market dicts with ticker, yes_bid, yes_ask, volume, etc.
        """
        all_data = []
        cursor = None
        page = 0

        while True:
            response = self.get_markets(status="open", limit=1000, cursor=cursor)
            markets = response.get("markets", [])

            if not markets:
                break

            all_data.extend(markets)
            page += 1

            cursor = response.get("cursor")
            if not cursor:
                break

            # Brief pause between pages to avoid rate limiting
            if page % 5 == 0:
                logger.info(f"Fetched {len(all_data)} markets so far, pausing...")
                time.sleep(2)

        logger.info(f"Fetched {len(all_data)} markets with price data")
        return all_data

    def get_market(self, ticker: str) -> dict:
        """
        Fetch a single market by ticker.

        Args:
            ticker: Market ticker

        Returns:
            Market data dictionary
        """
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str) -> dict:
        """
        Fetch orderbook for a market.

        Args:
            ticker: Market ticker

        Returns:
            Orderbook data with 'yes' and 'no' price levels
        """
        return self._request("GET", f"/markets/{ticker}/orderbook")

    def get_balance(self) -> dict:
        """
        Fetch account balance.

        Returns:
            Balance data with 'balance' in cents

        Raises:
            requests.exceptions.HTTPError: If not authenticated
        """
        return self._request("GET", "/portfolio/balance")

    def get_positions(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        Fetch current positions.

        Args:
            ticker: Filter by specific ticker (optional)
            limit: Number of results per page (max 100)
            cursor: Pagination cursor

        Returns:
            Positions data with 'market_positions' list
        """
        params = {"limit": min(limit, 100)}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/portfolio/positions", params=params)

    def get_fills(
        self,
        ticker: Optional[str] = None,
        order_id: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        Fetch trade history (fills).

        Args:
            ticker: Filter by market ticker (optional)
            order_id: Filter by order ID (optional)
            min_ts: Minimum timestamp in seconds (optional)
            max_ts: Maximum timestamp in seconds (optional)
            limit: Number of results per page (max 100)
            cursor: Pagination cursor

        Returns:
            Fills data with 'fills' list
        """
        params = {"limit": min(limit, 100)}
        if ticker:
            params["ticker"] = ticker
        if order_id:
            params["order_id"] = order_id
        if min_ts:
            params["min_ts"] = min_ts
        if max_ts:
            params["max_ts"] = max_ts
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/portfolio/fills", params=params)

    def get_market_history(
        self,
        ticker: str,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 100,
    ) -> dict:
        """
        Fetch OHLC candlestick data for a market.

        Args:
            ticker: Market ticker
            min_ts: Minimum timestamp in seconds (optional)
            max_ts: Maximum timestamp in seconds (optional)
            limit: Number of results (max 1000)

        Returns:
            History data with 'history' list of OHLC candles
        """
        params = {"limit": min(limit, 1000)}
        if min_ts:
            params["min_ts"] = min_ts
        if max_ts:
            params["max_ts"] = max_ts

        return self._request("GET", f"/markets/{ticker}/history", params=params)

    def place_order(
        self,
        ticker: str,
        side: str,
        type: str = "limit",
        count: int = 1,
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        expiration_ts: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """
        Place a new order.

        Args:
            ticker: Market ticker
            side: Order side ('yes' or 'no')
            type: Order type ('limit' or 'market')
            count: Number of contracts
            yes_price: Limit price for yes side (1-99 cents)
            no_price: Limit price for no side (1-99 cents)
            expiration_ts: Order expiration timestamp in seconds (optional)
            client_order_id: Client-specified order ID (optional)

        Returns:
            Order confirmation with 'order' object

        Raises:
            requests.exceptions.HTTPError: On validation or auth error
        """
        body = {
            "ticker": ticker,
            "action": "buy",  # Kalshi uses buy/sell with yes/no side
            "side": side,
            "type": type,
            "count": count,
        }

        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        if expiration_ts is not None:
            body["expiration_ts"] = expiration_ts
        if client_order_id is not None:
            body["client_order_id"] = client_order_id

        return self._request("POST", "/portfolio/orders", json_body=body)

    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an open order.

        Args:
            order_id: The order ID to cancel

        Returns:
            Cancellation confirmation

        Raises:
            requests.exceptions.HTTPError: If order not found or already filled
        """
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def cancel_orders(
        self,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Cancel multiple open orders.

        Args:
            ticker: Cancel orders for specific ticker only (optional)

        Returns:
            Cancellation summary
        """
        body = {}
        if ticker:
            body["ticker"] = ticker

        return self._request("DELETE", "/portfolio/orders", json_body=body)

    def get_exchange_status(self) -> dict:
        """
        Get current exchange status.

        Returns:
            Exchange status with 'trading_active', 'exchange_active' flags
        """
        return self._request("GET", "/exchange/status")

    def get_events(
        self,
        status: Optional[str] = None,
        series_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        Fetch events (groups of related markets).

        Args:
            status: Filter by status ('open', 'closed', etc.)
            series_ticker: Filter by series
            limit: Number of results per page
            cursor: Pagination cursor

        Returns:
            Events data with 'events' list
        """
        params = {"limit": min(limit, 200)}
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/events", params=params)
