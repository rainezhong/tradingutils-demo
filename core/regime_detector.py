"""Spot price regime detection: trending vs oscillating.

Computes an oscillation ratio over a trailing window of price ticks:
  oscillation_ratio = total_path / net_move

- Pure trend: ratio ~ 1.0
- Choppy/oscillating: ratio >> 1 (e.g. 5-10+)

Reusable across strategies — any module can instantiate a RegimeDetector,
feed it price ticks, and query the current regime.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


@dataclass
class RegimeState:
    """Snapshot of the current price regime."""

    oscillation_ratio: float  # total_path / net_move (1.0 = pure trend, high = chop)
    net_move: float  # absolute net displacement in USD
    total_path: float  # total distance traveled in USD
    window_sec: float  # the window this was computed over
    sample_count: int  # number of price ticks in window


class RegimeDetector:
    """Detects trending vs oscillating spot price regimes.

    Thread-safe: multiple feed threads can call update_price() concurrently.
    """

    def __init__(self, window_sec: float = 60.0) -> None:
        self._window_sec = window_sec
        self._prices: Dict[
            str, Deque[Tuple[float, float]]
        ] = {}  # source -> deque of (ts, price)
        self._lock = threading.Lock()

    def update_price(self, price: float, ts: float, source: str = "binance") -> None:
        """Feed a price tick. Call from the same sites as ScalpDetector.update_price()."""
        with self._lock:
            if source not in self._prices:
                self._prices[source] = deque()
            dq = self._prices[source]
            dq.append((ts, price))
            # Evict stale ticks (keep 2x window for safety)
            cutoff = ts - self._window_sec * 2
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def get_regime(self, source: Optional[str] = None) -> Optional[RegimeState]:
        """Compute current regime from trailing window.

        Args:
            source: Exchange source to use. None = first available source.

        Returns:
            RegimeState or None if insufficient data (< 2 ticks in window).
        """
        now = time.time()
        cutoff = now - self._window_sec

        with self._lock:
            if source is not None:
                dq = self._prices.get(source)
                if not dq:
                    return None
                ticks = [(ts, p) for ts, p in dq if ts >= cutoff]
            else:
                # Use the first source that has data
                ticks = []
                for dq in self._prices.values():
                    ticks = [(ts, p) for ts, p in dq if ts >= cutoff]
                    if len(ticks) >= 2:
                        break

        if len(ticks) < 2:
            return None

        # Compute path length and net displacement
        total_path = 0.0
        for i in range(1, len(ticks)):
            total_path += abs(ticks[i][1] - ticks[i - 1][1])

        net_move = abs(ticks[-1][1] - ticks[0][1])

        # Avoid division by zero: if price didn't move at all, ratio is infinite chop
        if net_move < 0.01:
            ratio = float("inf") if total_path > 0.01 else 1.0
        else:
            ratio = total_path / net_move

        return RegimeState(
            oscillation_ratio=ratio,
            net_move=net_move,
            total_path=total_path,
            window_sec=self._window_sec,
            sample_count=len(ticks),
        )

    def is_trending(self, source: Optional[str] = None, threshold: float = 5.0) -> bool:
        """Convenience: True if oscillation_ratio < threshold.

        Returns True (assume trending) if there's insufficient data.
        """
        regime = self.get_regime(source)
        if regime is None:
            return True
        return regime.oscillation_ratio < threshold
