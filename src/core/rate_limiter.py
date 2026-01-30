"""Shared rate limiter with priority support for Kalshi API.

Provides a singleton rate limiter that coordinates API calls across all clients.
Background tasks automatically pause when trading is active.
"""

import time
from collections import deque
from enum import IntEnum
from threading import Condition, Lock
from typing import Optional


class Priority(IntEnum):
    """Priority levels for rate limiting.

    Higher values = higher priority.
    Background tasks wait when TRADING priority is active.
    """
    BACKGROUND = 1   # Background collectors - pauses when trading active
    NORMAL = 10      # Standard API calls
    TRADING = 100    # Active trading - highest priority


class SharedRateLimiter:
    """Singleton thread-safe rate limiter with priority support.

    Features:
    - Token bucket algorithm for per-second and per-minute limits
    - Priority-based access: background tasks wait when trading is active
    - Singleton pattern ensures all clients share one limiter

    Example:
        >>> limiter = get_shared_rate_limiter()
        >>> limiter.acquire(Priority.TRADING)  # High priority request
        >>> limiter.acquire(Priority.BACKGROUND)  # Waits if trading active
    """

    _instance: Optional["SharedRateLimiter"] = None
    _init_lock = Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern - return existing instance if available."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        requests_per_second: int = 10,
        requests_per_minute: int = 600,
    ):
        """Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second
            requests_per_minute: Maximum requests per minute
        """
        # Only initialize once
        if getattr(self, '_initialized', False):
            return

        self.rps = requests_per_second
        self.rpm = requests_per_minute

        self.second_timestamps: deque = deque()
        self.minute_timestamps: deque = deque()

        self._lock = Lock()
        self._condition = Condition(self._lock)

        # Track active trading requests
        self._trading_active = False
        self._trading_count = 0

        self._initialized = True

    def acquire(self, priority: Priority = Priority.NORMAL) -> None:
        """Block until a request can be made within rate limits.

        Background priority requests wait when trading is active.
        This method is thread-safe.

        Args:
            priority: Request priority level
        """
        with self._condition:
            # Background tasks wait when trading is active
            if priority == Priority.BACKGROUND:
                while self._trading_active:
                    # Wait up to 5 seconds, then re-check
                    self._condition.wait(timeout=5.0)

            # Track trading activity
            if priority == Priority.TRADING:
                self._trading_count += 1
                self._trading_active = True

            # Standard rate limiting
            self._wait_for_capacity()

            # Record this request
            now = time.time()
            self.second_timestamps.append(now)
            self.minute_timestamps.append(now)

            # Add minimum spacing between requests (150ms)
            time.sleep(0.15)

            # Decrement trading count
            if priority == Priority.TRADING:
                self._trading_count -= 1
                if self._trading_count <= 0:
                    self._trading_active = False
                    self._condition.notify_all()

    def _wait_for_capacity(self) -> None:
        """Wait until we have capacity for another request.

        Must be called with lock held.
        """
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
                # Release lock while sleeping
                self._condition.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    self._condition.acquire()
                now = time.time()
                # Re-clean after sleeping
                while self.second_timestamps and now - self.second_timestamps[0] > 1:
                    self.second_timestamps.popleft()

        # Wait if at per-minute limit
        if len(self.minute_timestamps) >= self.rpm:
            sleep_time = 60 - (now - self.minute_timestamps[0])
            if sleep_time > 0:
                # Release lock while sleeping
                self._condition.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    self._condition.acquire()
                now = time.time()
                while self.minute_timestamps and now - self.minute_timestamps[0] > 60:
                    self.minute_timestamps.popleft()

    def set_trading_active(self, active: bool) -> None:
        """Explicitly set trading mode (alternative to using TRADING priority).

        When active, background tasks will pause their API calls.

        Args:
            active: Whether trading is currently active
        """
        with self._condition:
            self._trading_active = active
            if not active:
                self._condition.notify_all()

    @property
    def is_trading_active(self) -> bool:
        """Check if trading mode is currently active."""
        with self._lock:
            return self._trading_active

    def get_stats(self) -> dict:
        """Get current rate limiter statistics."""
        with self._lock:
            now = time.time()
            # Count recent requests
            recent_second = sum(1 for t in self.second_timestamps if now - t <= 1)
            recent_minute = sum(1 for t in self.minute_timestamps if now - t <= 60)

            return {
                "requests_last_second": recent_second,
                "requests_last_minute": recent_minute,
                "rps_limit": self.rps,
                "rpm_limit": self.rpm,
                "trading_active": self._trading_active,
                "trading_count": self._trading_count,
            }


# Module-level singleton accessor
_shared_limiter: Optional[SharedRateLimiter] = None
_limiter_lock = Lock()


def get_shared_rate_limiter(
    requests_per_second: int = 10,
    requests_per_minute: int = 600,
) -> SharedRateLimiter:
    """Get the shared rate limiter singleton.

    Args:
        requests_per_second: Max RPS (only used on first call)
        requests_per_minute: Max RPM (only used on first call)

    Returns:
        SharedRateLimiter singleton instance
    """
    global _shared_limiter

    if _shared_limiter is None:
        with _limiter_lock:
            if _shared_limiter is None:
                _shared_limiter = SharedRateLimiter(
                    requests_per_second=requests_per_second,
                    requests_per_minute=requests_per_minute,
                )

    return _shared_limiter


def reset_shared_rate_limiter() -> None:
    """Reset the shared rate limiter singleton (for testing)."""
    global _shared_limiter
    with _limiter_lock:
        _shared_limiter = None
        SharedRateLimiter._instance = None
