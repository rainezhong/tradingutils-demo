"""Global trading state coordination.

Allows background processes to detect when active trading is occurring
and pause their API calls to avoid competing for rate limits.
"""

import time
from threading import Condition, Lock
from typing import Optional


class TradingState:
    """Global trading state manager.

    Singleton that coordinates between trading algorithms and background collectors.
    When trading is active, background processes should pause their API calls.

    Example:
        >>> state = get_trading_state()
        >>> # In trading code:
        >>> state.set_active(True)
        >>> # ... execute trades ...
        >>> state.set_active(False)
        >>>
        >>> # In background collector:
        >>> if state.should_pause():
        ...     state.wait_while_paused(timeout=5.0)
        ...     continue
    """

    _instance: Optional["TradingState"] = None
    _init_lock = Lock()

    def __new__(cls):
        """Singleton pattern - return existing instance if available."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize trading state."""
        # Only initialize once
        if getattr(self, '_initialized', False):
            return

        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._active = False
        self._active_since: Optional[float] = None
        self._pause_count = 0  # Number of processes currently waiting

        self._initialized = True

    def set_active(self, active: bool) -> None:
        """Set whether trading is currently active.

        When set to True, background processes should pause.
        When set to False, waiting processes are notified to resume.

        Args:
            active: Whether trading is currently active
        """
        with self._condition:
            was_active = self._active
            self._active = active

            if active and not was_active:
                self._active_since = time.time()
            elif not active and was_active:
                self._active_since = None
                # Wake up all waiting processes
                self._condition.notify_all()

    def should_pause(self) -> bool:
        """Check if background processes should pause.

        Returns:
            True if trading is active and background work should pause
        """
        with self._lock:
            return self._active

    def wait_while_paused(self, timeout: float = 5.0) -> bool:
        """Block until trading is no longer active or timeout expires.

        Efficient blocking that doesn't spin. Processes should call this
        when should_pause() returns True.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if we can proceed (trading not active)
            False if timeout expired while still paused
        """
        with self._condition:
            if not self._active:
                return True

            self._pause_count += 1
            try:
                # Wait for notification or timeout
                start = time.time()
                remaining = timeout

                while self._active and remaining > 0:
                    self._condition.wait(timeout=remaining)
                    elapsed = time.time() - start
                    remaining = timeout - elapsed

                return not self._active
            finally:
                self._pause_count -= 1

    @property
    def is_active(self) -> bool:
        """Check if trading is currently active."""
        with self._lock:
            return self._active

    @property
    def active_duration_sec(self) -> Optional[float]:
        """Get how long trading has been active (None if not active)."""
        with self._lock:
            if not self._active or self._active_since is None:
                return None
            return time.time() - self._active_since

    @property
    def waiting_processes(self) -> int:
        """Get number of processes currently waiting."""
        with self._lock:
            return self._pause_count

    def get_stats(self) -> dict:
        """Get current trading state statistics."""
        with self._lock:
            return {
                "active": self._active,
                "active_since": self._active_since,
                "active_duration_sec": (
                    time.time() - self._active_since
                    if self._active and self._active_since
                    else None
                ),
                "waiting_processes": self._pause_count,
            }


# Module-level singleton accessor
_trading_state: Optional[TradingState] = None
_state_lock = Lock()


def get_trading_state() -> TradingState:
    """Get the global trading state singleton.

    Returns:
        TradingState singleton instance
    """
    global _trading_state

    if _trading_state is None:
        with _state_lock:
            if _trading_state is None:
                _trading_state = TradingState()

    return _trading_state


def reset_trading_state() -> None:
    """Reset the trading state singleton (for testing)."""
    global _trading_state
    with _state_lock:
        _trading_state = None
        TradingState._instance = None
