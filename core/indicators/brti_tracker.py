"""BRTI (CF Benchmarks Bitcoin Real Time Index) approximation tracker.

Connects to 5 of 7 BRTI constituent exchanges via L2 WebSocket feeds and
computes a second-by-second equal-weight mid-price average, matching the
real BRTI methodology (median filter, 25% outlier exclusion).

Usage:
    tracker = BRTITracker()
    tracker.start()
    reading = tracker.get_reading()     # full breakdown
    value = tracker.get_brti()          # just the float
    avg = tracker.get_avg(seconds=60)   # trailing average
    tracker.stop()
"""

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .cex_feeds import (
    ALL_EXCHANGES,
    FEED_CLASSES,
    ExchangeL2Feed,
    L2BookState,
)

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────


@dataclass
class BRTIConfig:
    exchanges: List[str] = field(default_factory=lambda: list(ALL_EXCHANGES))
    tick_interval_sec: float = 0.25  # 250ms for faster spot price updates (was 1.0s)
    outlier_deviation_pct: float = 25.0
    min_exchanges: int = 2
    stale_threshold_sec: float = 10.0
    history_window_sec: float = 120.0


# ── Reading ──────────────────────────────────────────────────────────────


@dataclass
class ExchangeContribution:
    exchange: str
    mid_price: float
    included: bool
    deviation_pct: float


@dataclass
class BRTIReading:
    brti: float
    timestamp: float
    n_exchanges: int
    n_available: int
    is_valid: bool
    contributions: List[ExchangeContribution]
    median_mid: float


# ── Tracker ──────────────────────────────────────────────────────────────


class BRTITracker:
    """Approximates BRTI from constituent exchange L2 mid-prices."""

    def __init__(self, config: Optional[BRTIConfig] = None):
        self._config = config or BRTIConfig()
        self._running = False
        self._feeds: Dict[str, ExchangeL2Feed] = {}
        self._tick_thread: Optional[threading.Thread] = None

        # State (protected by lock)
        self._lock = threading.Lock()
        self._latest: Optional[BRTIReading] = None
        self._history: Deque[Tuple[float, float]] = deque()  # (ts, brti_price)
        self._imbalance_history: Deque[Tuple[float, float]] = deque()  # (ts, avg_imbalance)

        # Callbacks
        self._callbacks: List[Callable[[BRTIReading], None]] = []

    # ── Public API ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        # Create and start exchange feeds
        for name in self._config.exchanges:
            cls = FEED_CLASSES.get(name)
            if cls is None:
                logger.warning("Unknown exchange: %s", name)
                continue
            feed = cls()
            feed.start()
            self._feeds[name] = feed

        # Start tick thread
        self._tick_thread = threading.Thread(
            target=self._tick_loop, name="brti-tick", daemon=True
        )
        self._tick_thread.start()
        logger.info(
            "BRTITracker started with %d exchanges: %s",
            len(self._feeds),
            list(self._feeds.keys()),
        )

    def stop(self) -> None:
        self._running = False
        if self._tick_thread:
            self._tick_thread.join(timeout=3.0)
            self._tick_thread = None
        for feed in self._feeds.values():
            feed.stop()
        self._feeds.clear()
        logger.info("BRTITracker stopped")

    def get_reading(self) -> Optional[BRTIReading]:
        with self._lock:
            return self._latest

    def get_brti(self) -> Optional[float]:
        with self._lock:
            return self._latest.brti if self._latest and self._latest.is_valid else None

    def get_avg(self, seconds: float = 60.0) -> Optional[float]:
        with self._lock:
            if not self._history:
                return None
            now = time.time()
            cutoff = now - seconds
            values = [v for ts, v in self._history if ts >= cutoff]
            if not values:
                return None
            return sum(values) / len(values)

    def get_history(self, seconds: float) -> List[Tuple[float, float]]:
        with self._lock:
            cutoff = time.time() - seconds
            return [(ts, v) for ts, v in self._history if ts >= cutoff]

    def on_update(self, callback: Callable[[BRTIReading], None]) -> None:
        self._callbacks.append(callback)

    def get_volatility(self, window_sec: float = 30.0) -> float:
        """Return std dev of BTC price over trailing window."""
        with self._lock:
            cutoff = time.time() - window_sec
            recent = [price for ts, price in self._history if ts >= cutoff]
        return statistics.stdev(recent) if len(recent) >= 10 else 0.0

    def get_acceleration(self) -> float:
        """Return 2nd derivative of price (acceleration in $/s²).

        Uses the last 20 samples (~5 seconds at 250ms tick rate) to calculate
        price acceleration via first differences.
        """
        with self._lock:
            if len(self._history) < 3:
                return 0.0
            recent = list(self._history)[-20:]  # Last 5 seconds (250ms ticks)

        if len(recent) < 3:
            return 0.0

        prices = [p for ts, p in recent]
        # First derivative (velocity)
        velocities = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
        # Second derivative (acceleration)
        accelerations = [velocities[i+1] - velocities[i] for i in range(len(velocities)-1)]
        return statistics.mean(accelerations) if accelerations else 0.0

    def get_imbalance(self) -> float:
        """Return current cross-exchange average imbalance."""
        states = [feed.get_state() for feed in self._feeds.values()]
        imbalances = [s.imbalance for s in states if s and s.imbalance is not None]
        return statistics.mean(imbalances) if imbalances else 0.0

    def get_imbalance_velocity(self, window_sec: float = 1.0) -> float:
        """Return rate of change in imbalance over the trailing window."""
        with self._lock:
            cutoff = time.time() - window_sec
            recent = [(ts, imb) for ts, imb in self._imbalance_history if ts >= cutoff]

        if len(recent) < 2:
            return 0.0

        # Linear regression slope (simple: delta / time)
        first_ts, first_imb = recent[0]
        last_ts, last_imb = recent[-1]
        time_delta = last_ts - first_ts

        if time_delta > 0:
            return (last_imb - first_imb) / time_delta
        return 0.0

    def get_cross_exchange_std(self) -> float:
        """Return std dev of current exchange prices."""
        states = [feed.get_state() for feed in self._feeds.values()]
        prices = [s.mid_price for s in states if s and s.mid_price]
        return statistics.stdev(prices) if len(prices) >= 2 else 0.0

    # ── Tick Loop ─────────────────────────────────────────────────────

    def _tick_loop(self) -> None:
        while self._running:
            try:
                reading = self._compute_tick()
                with self._lock:
                    self._latest = reading
                    if reading.is_valid:
                        self._history.append((reading.timestamp, reading.brti))
                        # Also track imbalance history
                        current_imbalance = self.get_imbalance()
                        self._imbalance_history.append((reading.timestamp, current_imbalance))
                        # Trim history
                        cutoff = reading.timestamp - self._config.history_window_sec
                        while self._history and self._history[0][0] < cutoff:
                            self._history.popleft()
                        while self._imbalance_history and self._imbalance_history[0][0] < cutoff:
                            self._imbalance_history.popleft()

                # Fire callbacks outside lock
                if reading.is_valid:
                    for cb in self._callbacks:
                        try:
                            cb(reading)
                        except Exception:
                            logger.exception("BRTI callback error")

            except Exception:
                logger.exception("BRTI tick error")

            time.sleep(self._config.tick_interval_sec)

    def _compute_tick(self) -> BRTIReading:
        now = time.time()
        cfg = self._config

        # 1. Collect mid-prices from all feeds, exclude stale
        contributions: List[ExchangeContribution] = []
        fresh_mids: List[Tuple[str, float]] = []

        for name, feed in self._feeds.items():
            state = feed.get_state()
            if state is None:
                contributions.append(ExchangeContribution(
                    exchange=name, mid_price=0.0, included=False, deviation_pct=0.0,
                ))
                continue

            age = now - state.timestamp
            if age > cfg.stale_threshold_sec:
                contributions.append(ExchangeContribution(
                    exchange=name, mid_price=state.mid_price,
                    included=False, deviation_pct=0.0,
                ))
                continue

            fresh_mids.append((name, state.mid_price))

        n_available = len(fresh_mids)

        if n_available < cfg.min_exchanges:
            # Fill in contributions for fresh but insufficient
            for name, mid in fresh_mids:
                contributions.append(ExchangeContribution(
                    exchange=name, mid_price=mid, included=False, deviation_pct=0.0,
                ))
            return BRTIReading(
                brti=0.0, timestamp=now, n_exchanges=0,
                n_available=n_available, is_valid=False,
                contributions=contributions, median_mid=0.0,
            )

        # 2. Compute median
        mid_values = [m for _, m in fresh_mids]
        median_mid = statistics.median(mid_values)

        # 3. Exclude outliers (>25% deviation from median)
        included: List[float] = []
        for name, mid in fresh_mids:
            dev_pct = abs(mid - median_mid) / median_mid * 100.0 if median_mid > 0 else 0.0
            is_included = dev_pct <= cfg.outlier_deviation_pct
            if is_included:
                included.append(mid)
            contributions.append(ExchangeContribution(
                exchange=name, mid_price=mid,
                included=is_included, deviation_pct=dev_pct,
            ))

        # 4. Equal-weight average
        if len(included) < cfg.min_exchanges:
            return BRTIReading(
                brti=0.0, timestamp=now, n_exchanges=len(included),
                n_available=n_available, is_valid=False,
                contributions=contributions, median_mid=median_mid,
            )

        brti = sum(included) / len(included)

        return BRTIReading(
            brti=brti, timestamp=now, n_exchanges=len(included),
            n_available=n_available, is_valid=True,
            contributions=contributions, median_mid=median_mid,
        )


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    duration = 60
    if len(sys.argv) > 1:
        duration = int(sys.argv[1])

    tracker = BRTITracker()
    tracker.start()

    print(f"Running for {duration}s — waiting for exchanges to connect...\n")

    try:
        start = time.time()
        while time.time() - start < duration:
            time.sleep(1.0)
            reading = tracker.get_reading()
            if reading is None:
                print("  (no data yet)")
                continue

            if not reading.is_valid:
                connected = [c.exchange for c in reading.contributions if c.mid_price > 0]
                print(f"  waiting... {reading.n_available} exchanges: {connected}")
                continue

            # Per-exchange breakdown
            parts = []
            for c in reading.contributions:
                if c.mid_price > 0:
                    mark = "+" if c.included else "x"
                    parts.append(f"{c.exchange}={c.mid_price:,.2f}({mark})")

            avg_60 = tracker.get_avg(60.0)
            avg_str = f"  avg60={avg_60:,.2f}" if avg_60 else ""

            print(
                f"  BRTI={reading.brti:,.2f}  "
                f"({reading.n_exchanges}/{reading.n_available} exchanges)  "
                f"median={reading.median_mid:,.2f}{avg_str}  "
                f"| {' '.join(parts)}"
            )

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        tracker.stop()
