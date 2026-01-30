"""Tests for Polymarket API client."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.core.models import MarketState
from src.polymarket.client import PolymarketClient
from src.polymarket.exceptions import PolymarketAPIError, PolymarketOrderError


# Test private key (DO NOT USE IN PRODUCTION)
TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


class MockResponse:
    """Mock HTTP response."""

    def __init__(self, json_data, status_code=200, headers=None):
        self._json_data = json_data
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(json_data)

    def json(self):
        return self._json_data


class TestPolymarketClient:
    """Tests for PolymarketClient."""

    @pytest.fixture
    def client(self):
        """Create client with test credentials."""
        client = PolymarketClient(
            private_key=TEST_PRIVATE_KEY,
            use_websocket=False,
        )
        # Mock HTTP client
        client._client = MagicMock()
        return client

    def test_client_initialization(self):
        """Test client initialization."""
        client = PolymarketClient(private_key=TEST_PRIVATE_KEY)

        assert client._wallet is not None
        assert client._wallet.address is not None

    def test_normalize_side(self, client):
        """Test side normalization."""
        assert client._normalize_side("BID") == "BUY"
        assert client._normalize_side("bid") == "BUY"
        assert client._normalize_side("BUY") == "BUY"
        assert client._normalize_side("ASK") == "SELL"
        assert client._normalize_side("ask") == "SELL"
        assert client._normalize_side("SELL") == "SELL"

        with pytest.raises(ValueError):
            client._normalize_side("invalid")

    def test_place_order_validation(self, client):
        """Test order placement validation."""
        with pytest.raises(ValueError, match="ticker cannot be empty"):
            client.place_order("", "BID", 0.55, 100)

        with pytest.raises(ValueError, match="price must be 0-1"):
            client.place_order("token123", "BID", 1.5, 100)

        with pytest.raises(ValueError, match="size must be positive"):
            client.place_order("token123", "BID", 0.55, 0)

    def test_place_order_success(self, client):
        """Test successful order placement."""
        client._client.request.return_value = MockResponse({
            "orderID": "order123",
        })

        # Use numeric token ID for EIP-712 signing
        order_id = client.place_order("12345678901234567890", "BID", 0.55, 100)

        assert order_id == "order123"
        client._client.request.assert_called_once()

    def test_place_order_failure(self, client):
        """Test order placement failure."""
        client._client.request.return_value = MockResponse(
            {"error": "Insufficient balance"},
            status_code=400,
        )

        with pytest.raises(PolymarketOrderError):
            # Use numeric token ID for EIP-712 signing
            client.place_order("12345678901234567890", "BID", 0.55, 100)

    def test_cancel_order_success(self, client):
        """Test successful order cancellation."""
        client._client.request.return_value = MockResponse({})

        result = client.cancel_order("order123")

        assert result is True

    def test_cancel_order_not_found(self, client):
        """Test canceling non-existent order."""
        client._client.request.return_value = MockResponse(
            {"error": "Not found"},
            status_code=404,
        )

        result = client.cancel_order("order123")

        assert result is False

    def test_get_order_status(self, client):
        """Test getting order status."""
        client._client.request.return_value = MockResponse({
            "id": "order123",
            "market": "market456",
            "asset_id": "token789",
            "side": "BUY",
            "price": "0.55",
            "original_size": "100",
            "size_matched": "50",
            "status": "LIVE",
        })

        status = client.get_order_status("order123")

        assert status["order_id"] == "order123"
        assert status["status"] == "OPEN"
        assert status["filled_size"] == 50
        assert status["remaining_size"] == 50

    def test_get_order_status_not_found(self, client):
        """Test getting status of non-existent order."""
        client._client.request.return_value = MockResponse(
            {"error": "Not found"},
            status_code=404,
        )

        with pytest.raises(ValueError, match="Order not found"):
            client.get_order_status("unknown")

    def test_get_market_data(self, client):
        """Test getting market data."""
        client._client.request.return_value = MockResponse({
            "bids": [
                {"price": "0.55", "size": "100"},
                {"price": "0.54", "size": "200"},
            ],
            "asks": [
                {"price": "0.57", "size": "150"},
                {"price": "0.58", "size": "250"},
            ],
        })

        market = client.get_market_data("token123")

        assert isinstance(market, MarketState)
        assert market.ticker == "token123"
        assert market.bid == 0.55
        assert market.ask == 0.57
        assert market.mid == pytest.approx(0.56)

    def test_get_market_data_empty_book(self, client):
        """Test getting market data with empty book."""
        client._client.request.return_value = MockResponse({
            "bids": [],
            "asks": [],
        })

        market = client.get_market_data("token123")

        # Should use defaults
        assert market.bid == 0.0
        assert market.ask == 1.0

    def test_get_markets(self, client):
        """Test getting list of markets."""
        client._client.request.return_value = MockResponse({
            "data": [
                {
                    "condition_id": "0x123",
                    "question_id": "0x456",
                    "question": "Test market 1",
                },
                {
                    "condition_id": "0x789",
                    "question_id": "0xabc",
                    "question": "Test market 2",
                },
            ]
        })

        markets = client.get_markets(limit=10)

        assert len(markets) == 2
        assert markets[0].condition_id == "0x123"
        assert markets[1].condition_id == "0x789"

    def test_get_open_orders(self, client):
        """Test getting open orders."""
        client._client.request.return_value = MockResponse({
            "data": [
                {
                    "id": "order1",
                    "market": "market1",
                    "asset_id": "token1",
                    "side": "BUY",
                    "price": "0.55",
                    "original_size": "100",
                    "size_matched": "0",
                    "status": "LIVE",
                },
            ]
        })

        orders = client.get_open_orders()

        assert len(orders) == 1
        assert orders[0].order_id == "order1"
        assert orders[0].is_active is True

    def test_build_order(self, client):
        """Test order building."""
        order = client._build_order(
            asset_id="token123",
            side="BUY",
            price=0.55,
            size=100,
        )

        assert order["maker"] == client._wallet.address
        assert order["tokenId"] == "token123"
        assert order["side"] == 0  # BUY
        assert order["makerAmount"] == int(0.55 * 100 * 10**6)  # USDC
        assert order["takerAmount"] == int(100 * 10**6)  # Shares

    def test_build_order_sell(self, client):
        """Test building sell order."""
        order = client._build_order(
            asset_id="token123",
            side="SELL",
            price=0.60,
            size=50,
        )

        assert order["side"] == 1  # SELL
        assert order["makerAmount"] == int(50 * 10**6)  # Shares
        assert order["takerAmount"] == int(0.60 * 50 * 10**6)  # USDC

    def test_rate_limiting(self, client):
        """Test rate limiting between requests."""
        import time

        client._client.request.return_value = MockResponse({"data": []})

        start = time.time()

        # Make multiple requests
        client._request("GET", "/markets")
        client._request("GET", "/markets")

        elapsed = time.time() - start

        # Should have some delay for rate limiting
        assert elapsed >= client._min_request_interval

    def test_retry_on_timeout(self, client):
        """Test retry on timeout."""
        import httpx

        client._client.request.side_effect = [
            httpx.TimeoutException("Timeout"),
            MockResponse({"data": []}),
        ]

        result = client._request("GET", "/markets")

        assert result == {"data": []}
        assert client._client.request.call_count == 2

    def test_rate_limit_handling(self, client):
        """Test handling of rate limit response."""
        client._client.request.side_effect = [
            MockResponse({}, status_code=429, headers={"Retry-After": "1"}),
            MockResponse({"data": []}),
        ]

        result = client._request("GET", "/markets")

        assert result == {"data": []}


class TestPolymarketClientIntegration:
    """Integration-style tests (with mocked HTTP)."""

    @pytest.fixture
    def client(self):
        """Create connected client."""
        client = PolymarketClient(
            private_key=TEST_PRIVATE_KEY,
            use_websocket=False,
        )
        client.connect()
        return client

    def test_connect_disconnect(self, client):
        """Test connect and disconnect."""
        assert client._client is not None

        client.disconnect()

        assert client._client is None

    def test_cancel_all_orders(self, client):
        """Test cancel all orders."""
        with patch.object(client, 'get_open_orders') as mock_get:
            with patch.object(client, 'cancel_order') as mock_cancel:
                mock_order = MagicMock()
                mock_order.order_id = "order123"
                mock_get.return_value = [mock_order]
                mock_cancel.return_value = True

                canceled = client.cancel_all_orders()

                assert canceled == 1
                mock_cancel.assert_called_once_with("order123")
