"""Redis cache for real-time trading data.

Provides caching for order books, market data, positions, and pub/sub
for price updates with configurable TTL.
"""

import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

import redis.asyncio as redis
from redis.asyncio.client import PubSub

logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def decimal_decoder(obj: Dict[str, Any]) -> Dict[str, Any]:
    """JSON decoder hook for Decimal fields."""
    for key, value in obj.items():
        if isinstance(value, str) and key in (
            "price",
            "bid",
            "ask",
            "mid",
            "spread",
            "roi",
            "pnl",
        ):
            try:
                obj[key] = Decimal(value)
            except (ValueError, TypeError):
                pass
    return obj


class MarketCache:
    """Redis cache for market data with pub/sub support.

    Provides fast caching for:
    - Order books (5 second TTL)
    - Market data snapshots
    - Position lookups
    - Price update subscriptions

    Example:
        >>> cache = MarketCache("redis://localhost:6379")
        >>> await cache.connect()
        >>> await cache.set_orderbook("KALSHI", "BTC-YES", orderbook_data)
        >>> orderbook = await cache.get_orderbook("KALSHI", "BTC-YES")
        >>> await cache.close()
    """

    # Default TTL values in seconds
    DEFAULT_ORDERBOOK_TTL = 5
    DEFAULT_MARKET_TTL = 60
    DEFAULT_POSITION_TTL = 30

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "trading:",
    ) -> None:
        """Initialize cache.

        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for all cache keys
        """
        self._url = redis_url or os.getenv(
            "REDIS_URL",
            "redis://localhost:6379",
        )
        self._prefix = key_prefix
        self._redis: Optional[redis.Redis] = None
        self._pubsub: Optional[PubSub] = None
        self._connected = False

    def _key(self, *parts: str) -> str:
        """Build cache key with prefix.

        Args:
            *parts: Key parts to join

        Returns:
            Prefixed cache key
        """
        return f"{self._prefix}{':'.join(parts)}"

    async def connect(self) -> None:
        """Connect to Redis."""
        if self._connected:
            return

        logger.info("Connecting to Redis at %s", self._url)
        self._redis = redis.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
        )

        # Test connection
        try:
            await self._redis.ping()
            self._connected = True
            logger.info("Redis connection established")
        except Exception as e:
            logger.error("Failed to connect to Redis: %s", e)
            raise

    async def close(self) -> None:
        """Close Redis connection."""
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None

        if self._redis:
            await self._redis.close()
            self._redis = None
            self._connected = False
            logger.info("Redis connection closed")

    async def health_check(self) -> bool:
        """Check Redis connectivity.

        Returns:
            True if Redis is reachable
        """
        if not self._redis:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception as e:
            logger.error("Redis health check failed: %s", e)
            return False

    # =========================================================================
    # Order Book Cache
    # =========================================================================

    async def get_orderbook(
        self,
        platform: str,
        ticker: str,
    ) -> Optional[Dict[str, Any]]:
        """Get cached order book.

        Args:
            platform: Trading platform
            ticker: Market ticker

        Returns:
            Order book data if cached
        """
        if not self._redis:
            return None

        key = self._key("orderbook", platform, ticker)
        data = await self._redis.get(key)
        if data:
            return json.loads(data, object_hook=decimal_decoder)
        return None

    async def set_orderbook(
        self,
        platform: str,
        ticker: str,
        orderbook: Dict[str, Any],
        ttl: int = DEFAULT_ORDERBOOK_TTL,
    ) -> None:
        """Cache order book.

        Args:
            platform: Trading platform
            ticker: Market ticker
            orderbook: Order book data
            ttl: Time to live in seconds
        """
        if not self._redis:
            return

        key = self._key("orderbook", platform, ticker)
        data = json.dumps(orderbook, cls=DecimalEncoder)
        await self._redis.setex(key, ttl, data)

    async def delete_orderbook(
        self,
        platform: str,
        ticker: str,
    ) -> None:
        """Delete cached order book.

        Args:
            platform: Trading platform
            ticker: Market ticker
        """
        if not self._redis:
            return

        key = self._key("orderbook", platform, ticker)
        await self._redis.delete(key)

    # =========================================================================
    # Market Data Cache
    # =========================================================================

    async def get_market(
        self,
        platform: str,
        ticker: str,
    ) -> Optional[Dict[str, Any]]:
        """Get cached market data.

        Args:
            platform: Trading platform
            ticker: Market ticker

        Returns:
            Market data if cached
        """
        if not self._redis:
            return None

        key = self._key("market", platform, ticker)
        data = await self._redis.get(key)
        if data:
            return json.loads(data, object_hook=decimal_decoder)
        return None

    async def set_market(
        self,
        platform: str,
        ticker: str,
        market: Dict[str, Any],
        ttl: int = DEFAULT_MARKET_TTL,
    ) -> None:
        """Cache market data.

        Args:
            platform: Trading platform
            ticker: Market ticker
            market: Market data
            ttl: Time to live in seconds
        """
        if not self._redis:
            return

        key = self._key("market", platform, ticker)
        data = json.dumps(market, cls=DecimalEncoder)
        await self._redis.setex(key, ttl, data)

    async def get_all_markets(
        self,
        platform: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get all cached markets.

        Args:
            platform: Filter by platform

        Returns:
            Dictionary of ticker to market data
        """
        if not self._redis:
            return {}

        pattern = self._key("market", platform or "*", "*")
        keys = await self._redis.keys(pattern)

        result = {}
        for key in keys:
            data = await self._redis.get(key)
            if data:
                # Extract ticker from key
                parts = key.split(":")
                ticker = parts[-1] if len(parts) >= 2 else key
                result[ticker] = json.loads(data, object_hook=decimal_decoder)

        return result

    # =========================================================================
    # Position Cache
    # =========================================================================

    async def get_position(
        self,
        platform: str,
        ticker: str,
    ) -> Optional[Dict[str, Any]]:
        """Get cached position.

        Args:
            platform: Trading platform
            ticker: Position ticker

        Returns:
            Position data if cached
        """
        if not self._redis:
            return None

        key = self._key("position", platform, ticker)
        data = await self._redis.get(key)
        if data:
            return json.loads(data, object_hook=decimal_decoder)
        return None

    async def set_position(
        self,
        platform: str,
        ticker: str,
        position: Dict[str, Any],
        ttl: int = DEFAULT_POSITION_TTL,
    ) -> None:
        """Cache position.

        Args:
            platform: Trading platform
            ticker: Position ticker
            position: Position data
            ttl: Time to live in seconds
        """
        if not self._redis:
            return

        key = self._key("position", platform, ticker)
        data = json.dumps(position, cls=DecimalEncoder)
        await self._redis.setex(key, ttl, data)

    async def get_all_positions(
        self,
        platform: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get all cached positions.

        Args:
            platform: Filter by platform

        Returns:
            Dictionary of ticker to position data
        """
        if not self._redis:
            return {}

        pattern = self._key("position", platform or "*", "*")
        keys = await self._redis.keys(pattern)

        result = {}
        for key in keys:
            data = await self._redis.get(key)
            if data:
                parts = key.split(":")
                ticker = parts[-1] if len(parts) >= 2 else key
                result[ticker] = json.loads(data, object_hook=decimal_decoder)

        return result

    async def delete_position(
        self,
        platform: str,
        ticker: str,
    ) -> None:
        """Delete cached position.

        Args:
            platform: Trading platform
            ticker: Position ticker
        """
        if not self._redis:
            return

        key = self._key("position", platform, ticker)
        await self._redis.delete(key)

    # =========================================================================
    # Pub/Sub for Price Updates
    # =========================================================================

    async def publish_price_update(
        self,
        ticker: str,
        price: float,
        platform: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Publish price update to channel.

        Args:
            ticker: Market ticker
            price: New price
            platform: Trading platform
            metadata: Additional data

        Returns:
            Number of subscribers that received the message
        """
        if not self._redis:
            return 0

        channel = self._key("prices", ticker)
        message = json.dumps(
            {
                "ticker": ticker,
                "price": price,
                "platform": platform,
                "timestamp": datetime.utcnow().isoformat(),
                **(metadata or {}),
            },
            cls=DecimalEncoder,
        )
        return await self._redis.publish(channel, message)

    async def subscribe_prices(
        self,
        tickers: List[str],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Subscribe to price updates.

        Args:
            tickers: Tickers to subscribe to
            callback: Function to call with price updates
        """
        if not self._redis:
            return

        self._pubsub = self._redis.pubsub()
        channels = [self._key("prices", ticker) for ticker in tickers]
        await self._pubsub.subscribe(*channels)

        async for message in self._pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"], object_hook=decimal_decoder)
                callback(data)

    async def unsubscribe_prices(self, tickers: List[str]) -> None:
        """Unsubscribe from price updates.

        Args:
            tickers: Tickers to unsubscribe from
        """
        if not self._pubsub:
            return

        channels = [self._key("prices", ticker) for ticker in tickers]
        await self._pubsub.unsubscribe(*channels)

    # =========================================================================
    # Opportunity Cache
    # =========================================================================

    async def cache_opportunity(
        self,
        opportunity_id: str,
        opportunity: Dict[str, Any],
        ttl: int = 300,
    ) -> None:
        """Cache an opportunity.

        Args:
            opportunity_id: Opportunity ID
            opportunity: Opportunity data
            ttl: Time to live in seconds
        """
        if not self._redis:
            return

        key = self._key("opportunity", opportunity_id)
        data = json.dumps(opportunity, cls=DecimalEncoder)
        await self._redis.setex(key, ttl, data)

        # Also add to the open opportunities set
        if opportunity.get("status") == "OPEN":
            await self._redis.sadd(self._key("opportunities", "open"), opportunity_id)

    async def get_cached_opportunity(
        self,
        opportunity_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get cached opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            Opportunity data if cached
        """
        if not self._redis:
            return None

        key = self._key("opportunity", opportunity_id)
        data = await self._redis.get(key)
        if data:
            return json.loads(data, object_hook=decimal_decoder)
        return None

    async def get_open_opportunity_ids(self) -> Set[str]:
        """Get IDs of open opportunities.

        Returns:
            Set of opportunity IDs
        """
        if not self._redis:
            return set()

        key = self._key("opportunities", "open")
        members = await self._redis.smembers(key)
        return set(members)

    async def remove_from_open(self, opportunity_id: str) -> None:
        """Remove opportunity from open set.

        Args:
            opportunity_id: Opportunity ID
        """
        if not self._redis:
            return

        key = self._key("opportunities", "open")
        await self._redis.srem(key, opportunity_id)

    # =========================================================================
    # Rate Limiting
    # =========================================================================

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> bool:
        """Check if action is rate limited.

        Args:
            key: Rate limit key (e.g., "orders:KALSHI")
            limit: Maximum actions per window
            window_seconds: Window size in seconds

        Returns:
            True if action is allowed, False if rate limited
        """
        if not self._redis:
            return True

        cache_key = self._key("ratelimit", key)
        current = await self._redis.get(cache_key)

        if current is None:
            await self._redis.setex(cache_key, window_seconds, "1")
            return True

        count = int(current)
        if count >= limit:
            return False

        await self._redis.incr(cache_key)
        return True

    async def get_rate_limit_remaining(
        self,
        key: str,
        limit: int,
    ) -> int:
        """Get remaining rate limit.

        Args:
            key: Rate limit key
            limit: Maximum actions per window

        Returns:
            Remaining actions allowed
        """
        if not self._redis:
            return limit

        cache_key = self._key("ratelimit", key)
        current = await self._redis.get(cache_key)

        if current is None:
            return limit

        return max(0, limit - int(current))

    # =========================================================================
    # Generic Cache Operations
    # =========================================================================

    async def get(self, key: str) -> Optional[str]:
        """Get value by key.

        Args:
            key: Cache key

        Returns:
            Value if found
        """
        if not self._redis:
            return None
        return await self._redis.get(self._key(key))

    async def set(
        self,
        key: str,
        value: str,
        ttl: Optional[int] = None,
    ) -> None:
        """Set value with optional TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
        """
        if not self._redis:
            return

        cache_key = self._key(key)
        if ttl:
            await self._redis.setex(cache_key, ttl, value)
        else:
            await self._redis.set(cache_key, value)

    async def delete(self, key: str) -> None:
        """Delete a key.

        Args:
            key: Cache key
        """
        if not self._redis:
            return
        await self._redis.delete(self._key(key))

    async def clear_all(self) -> None:
        """Clear all keys with this cache's prefix.

        WARNING: This deletes all cached data.
        """
        if not self._redis:
            return

        pattern = f"{self._prefix}*"
        keys = await self._redis.keys(pattern)
        if keys:
            await self._redis.delete(*keys)
            logger.warning("Cleared %d cache keys", len(keys))
