"""Tests for Kalshi API client."""

import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.kalshi.auth import KalshiAuth
from src.kalshi.client import KalshiClient, RateLimiter
from src.kalshi.exceptions import (
    AuthenticationError,
    KalshiAPIError,
    MarketNotFoundError,
)


class TestRateLimiter(unittest.TestCase):
    """Tests for RateLimiter."""

    def test_acquire_under_limit(self):
        """Test acquiring under rate limit."""
        limiter = RateLimiter(requests_per_second=10.0)

        async def run():
            start = asyncio.get_event_loop().time()
            for _ in range(5):
                await limiter.acquire()
            elapsed = asyncio.get_event_loop().time() - start
            self.assertLess(elapsed, 1.0)

        asyncio.get_event_loop().run_until_complete(run())

    def test_acquire_rate_limited(self):
        """Test rate limiting kicks in."""
        limiter = RateLimiter(requests_per_second=2.0)

        async def run():
            start = asyncio.get_event_loop().time()
            for _ in range(4):
                await limiter.acquire()
            elapsed = asyncio.get_event_loop().time() - start
            self.assertGreater(elapsed, 0.5)

        asyncio.get_event_loop().run_until_complete(run())


@pytest.fixture
def auth():
    """Create test auth."""
    return KalshiAuth("test-key", "test-secret")


@pytest.fixture
def mock_response():
    """Create mock response."""
    response = MagicMock()
    response.status_code = 200
    response.content = True
    return response


@pytest.mark.asyncio
class TestKalshiClientAsync:
    """Async tests for KalshiClient."""

    async def test_get_market_data(self, auth, mock_response):
        """Test get_market_data returns MarketState."""
        mock_response.json.return_value = {
            "market": {
                "ticker": "TEST",
                "title": "Test Market",
                "yes_bid": 45,
                "yes_ask": 55,
                "last_price": 50,
                "volume": 1000,
                "volume_24h": 500,
            }
        }

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                market = await client.get_market_data_async("TEST")

                assert market.ticker == "TEST"
                assert market.bid == 0.45
                assert market.ask == 0.55
                assert market.volume == 500

    async def test_place_order(self, auth, mock_response):
        """Test place_order returns order ID."""
        mock_response.json.return_value = {
            "order": {
                "order_id": "order-123",
                "ticker": "TEST",
                "side": "yes",
                "count": 10,
            }
        }

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                order_id = await client.place_order_async("TEST", "buy", 0.45, 10)
                assert order_id == "order-123"

    async def test_cancel_order(self, auth, mock_response):
        """Test cancel_order returns True on success."""
        mock_response.json.return_value = {}

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                result = await client.cancel_order_async("order-123")
                assert result is True

    async def test_cancel_order_not_found(self, auth):
        """Test cancel_order returns False when not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.content = True
        mock_response.json.return_value = {"message": "Order not found"}

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                result = await client.cancel_order_async("order-123")
                assert result is False

    async def test_authentication_error(self, auth):
        """Test 401 raises AuthenticationError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = True
        mock_response.json.return_value = {"message": "Invalid signature"}

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                with pytest.raises(AuthenticationError):
                    await client.get_market_data_async("TEST")

    async def test_market_not_found(self, auth):
        """Test 404 raises MarketNotFoundError."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.content = True
        mock_response.json.return_value = {"message": "Market not found"}

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                with pytest.raises(MarketNotFoundError):
                    await client.get_market_data_async("INVALID")

    async def test_get_balance(self, auth, mock_response):
        """Test get_balance returns KalshiBalance."""
        mock_response.json.return_value = {
            "balance": 10000,
            "portfolio_value": 15000,
        }

        with patch("src.kalshi.client.HTTPX_AVAILABLE", True):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                client = KalshiClient(auth)
                client._client = mock_client

                balance = await client.get_balance()
                assert balance.balance == 10000
                assert balance.balance_dollars == 100.0


class TestKalshiClientInit(unittest.TestCase):
    """Tests for KalshiClient initialization."""

    def setUp(self):
        """Set up test fixtures."""
        self.auth = KalshiAuth("test-key", "test-secret")

    @patch("src.kalshi.client.HTTPX_AVAILABLE", True)
    def test_init(self):
        """Test client initialization."""
        client = KalshiClient(self.auth)
        self.assertEqual(client._base_url, KalshiClient.PRODUCTION_URL)

    @patch("src.kalshi.client.HTTPX_AVAILABLE", True)
    def test_init_demo(self):
        """Test client with demo URL."""
        client = KalshiClient(self.auth, base_url=KalshiClient.DEMO_URL)
        self.assertEqual(client._base_url, KalshiClient.DEMO_URL)


class TestKalshiClientSideNormalization(unittest.TestCase):
    """Tests for order side normalization."""

    def setUp(self):
        """Set up test fixtures."""
        self.auth = KalshiAuth("test-key", "test-secret")

    @patch("src.kalshi.client.HTTPX_AVAILABLE", True)
    def test_invalid_side(self):
        """Test invalid side raises ValueError."""
        client = KalshiClient(self.auth)

        async def run():
            with self.assertRaises(ValueError):
                await client.place_order_async("TEST", "invalid", 0.50, 10)

        asyncio.get_event_loop().run_until_complete(run())

    @patch("src.kalshi.client.HTTPX_AVAILABLE", True)
    def test_invalid_price(self):
        """Test invalid price raises ValueError."""
        client = KalshiClient(self.auth)

        async def run():
            with self.assertRaises(ValueError):
                await client.place_order_async("TEST", "buy", 1.5, 10)

        asyncio.get_event_loop().run_until_complete(run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
