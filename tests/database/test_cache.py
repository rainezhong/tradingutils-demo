"""Tests for Redis cache layer."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.database.cache import MarketCache


class MockRedis:
    """Mock Redis client for testing."""

    def __init__(self):
        self._data = {}
        self._sets = {}
        self._ttls = {}

    async def ping(self):
        return True

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value):
        self._data[key] = value

    async def setex(self, key, ttl, value):
        self._data[key] = value
        self._ttls[key] = ttl

    async def delete(self, *keys):
        for key in keys:
            self._data.pop(key, None)
            self._ttls.pop(key, None)

    async def keys(self, pattern):
        import fnmatch
        return [k for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]

    async def incr(self, key):
        self._data[key] = str(int(self._data.get(key, 0)) + 1)
        return int(self._data[key])

    async def sadd(self, key, *values):
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].update(values)

    async def smembers(self, key):
        return self._sets.get(key, set())

    async def srem(self, key, *values):
        if key in self._sets:
            self._sets[key] -= set(values)

    async def publish(self, channel, message):
        return 1

    def pubsub(self):
        return AsyncMock()

    async def close(self):
        pass


@pytest.fixture
def mock_redis():
    """Create mock Redis instance."""
    return MockRedis()


@pytest_asyncio.fixture
async def cache(mock_redis):
    """Create cache with mock Redis."""
    cache = MarketCache()
    cache._redis = mock_redis
    cache._connected = True
    yield cache


class TestMarketCache:
    """Tests for MarketCache."""

    @pytest.mark.asyncio
    async def test_set_and_get_orderbook(self, cache):
        """Test caching orderbook."""
        orderbook = {
            "bids": [[0.45, 100], [0.44, 200]],
            "asks": [[0.46, 150], [0.47, 250]],
        }

        await cache.set_orderbook("KALSHI", "BTC-YES", orderbook)
        fetched = await cache.get_orderbook("KALSHI", "BTC-YES")

        assert fetched is not None
        assert fetched["bids"] == [[0.45, 100], [0.44, 200]]
        assert fetched["asks"] == [[0.46, 150], [0.47, 250]]

    @pytest.mark.asyncio
    async def test_get_orderbook_not_found(self, cache):
        """Test getting non-existent orderbook."""
        fetched = await cache.get_orderbook("KALSHI", "NONEXISTENT")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_orderbook(self, cache):
        """Test deleting orderbook."""
        await cache.set_orderbook("KALSHI", "BTC-YES", {"bids": [], "asks": []})
        await cache.delete_orderbook("KALSHI", "BTC-YES")

        fetched = await cache.get_orderbook("KALSHI", "BTC-YES")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_set_and_get_market(self, cache):
        """Test caching market data."""
        market = {
            "ticker": "BTC-YES",
            "bid": "0.45",
            "ask": "0.46",
            "volume": 1000,
        }

        await cache.set_market("KALSHI", "BTC-YES", market)
        fetched = await cache.get_market("KALSHI", "BTC-YES")

        assert fetched is not None
        assert fetched["ticker"] == "BTC-YES"
        # Decimal fields should be converted
        assert fetched["bid"] == Decimal("0.45")

    @pytest.mark.asyncio
    async def test_set_and_get_position(self, cache):
        """Test caching position data."""
        position = {
            "ticker": "BTC-YES",
            "size": 50,
            "entry_price": "0.45",
            "pnl": "2.50",
        }

        await cache.set_position("KALSHI", "BTC-YES", position)
        fetched = await cache.get_position("KALSHI", "BTC-YES")

        assert fetched is not None
        assert fetched["size"] == 50
        assert fetched["pnl"] == Decimal("2.50")

    @pytest.mark.asyncio
    async def test_delete_position(self, cache):
        """Test deleting position."""
        await cache.set_position("KALSHI", "BTC-YES", {"size": 50})
        await cache.delete_position("KALSHI", "BTC-YES")

        fetched = await cache.get_position("KALSHI", "BTC-YES")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_cache_opportunity(self, cache):
        """Test caching opportunity."""
        opportunity = {
            "id": "opp-123",
            "spread": "0.07",
            "roi": "0.11",
            "status": "OPEN",
        }

        await cache.cache_opportunity("opp-123", opportunity)
        fetched = await cache.get_cached_opportunity("opp-123")

        assert fetched is not None
        assert fetched["roi"] == Decimal("0.11")

    @pytest.mark.asyncio
    async def test_open_opportunity_set(self, cache):
        """Test tracking open opportunities."""
        opportunity = {
            "id": "opp-456",
            "status": "OPEN",
        }

        await cache.cache_opportunity("opp-456", opportunity)
        open_ids = await cache.get_open_opportunity_ids()

        assert "opp-456" in open_ids

        await cache.remove_from_open("opp-456")
        open_ids = await cache.get_open_opportunity_ids()

        assert "opp-456" not in open_ids

    @pytest.mark.asyncio
    async def test_publish_price_update(self, cache):
        """Test publishing price update."""
        count = await cache.publish_price_update(
            ticker="BTC-YES",
            price=0.48,
            platform="KALSHI",
        )

        assert count == 1

    @pytest.mark.asyncio
    async def test_rate_limit_allowed(self, cache):
        """Test rate limiting - allowed."""
        allowed = await cache.check_rate_limit("orders:KALSHI", limit=5)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_rate_limit_blocked(self, cache):
        """Test rate limiting - blocked after limit."""
        for _ in range(5):
            await cache.check_rate_limit("orders:test", limit=5)

        # Next request should be blocked
        allowed = await cache.check_rate_limit("orders:test", limit=5)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_rate_limit_remaining(self, cache):
        """Test getting remaining rate limit."""
        await cache.check_rate_limit("api:test", limit=10)
        await cache.check_rate_limit("api:test", limit=10)

        remaining = await cache.get_rate_limit_remaining("api:test", limit=10)
        assert remaining == 8

    @pytest.mark.asyncio
    async def test_generic_get_set(self, cache):
        """Test generic get/set operations."""
        await cache.set("custom:key", "custom_value", ttl=60)
        value = await cache.get("custom:key")

        assert value == "custom_value"

    @pytest.mark.asyncio
    async def test_generic_delete(self, cache):
        """Test generic delete operation."""
        await cache.set("temp:key", "temp_value")
        await cache.delete("temp:key")

        value = await cache.get("temp:key")
        assert value is None

    @pytest.mark.asyncio
    async def test_health_check_connected(self, cache):
        """Test health check when connected."""
        healthy = await cache.health_check()
        assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_disconnected(self, cache):
        """Test health check when disconnected."""
        cache._redis = None
        healthy = await cache.health_check()
        assert healthy is False

    @pytest.mark.asyncio
    async def test_operations_when_disconnected(self, cache):
        """Test operations gracefully handle disconnection."""
        cache._redis = None

        # All operations should return None/empty without error
        assert await cache.get_orderbook("KALSHI", "X") is None
        assert await cache.get_market("KALSHI", "X") is None
        assert await cache.get_position("KALSHI", "X") is None
        assert await cache.get_all_markets() == {}
        assert await cache.get_all_positions() == {}

        # Write operations should be no-ops
        await cache.set_orderbook("KALSHI", "X", {})
        await cache.set_market("KALSHI", "X", {})
        await cache.set_position("KALSHI", "X", {})


class TestDecimalHandling:
    """Tests for Decimal serialization/deserialization."""

    @pytest.mark.asyncio
    async def test_decimal_in_nested_data(self, cache):
        """Test Decimal handling in nested structures."""
        data = {
            "ticker": "TEST",
            "prices": {
                "bid": "0.4512",
                "ask": "0.4678",
            },
            "spread": "0.0166",
        }

        await cache.set_market("KALSHI", "TEST", data)
        fetched = await cache.get_market("KALSHI", "TEST")

        # spread should be converted to Decimal
        assert fetched["spread"] == Decimal("0.0166")
        # Nested values with price field names are also converted
        assert fetched["prices"]["bid"] == Decimal("0.4512")

    @pytest.mark.asyncio
    async def test_price_field_conversion(self, cache):
        """Test specific price field conversion."""
        position = {
            "size": 100,
            "price": "0.50",  # Should be converted
            "bid": "0.49",  # Should be converted
            "ask": "0.51",  # Should be converted
            "mid": "0.50",  # Should be converted
            "name": "0.50",  # Should NOT be converted (not a price field)
        }

        await cache.set_position("KALSHI", "POS", position)
        fetched = await cache.get_position("KALSHI", "POS")

        assert fetched["price"] == Decimal("0.50")
        assert fetched["bid"] == Decimal("0.49")
        assert fetched["ask"] == Decimal("0.51")
        assert fetched["mid"] == Decimal("0.50")
        assert fetched["name"] == "0.50"  # String, not converted
