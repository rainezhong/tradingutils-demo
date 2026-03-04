"""VPIN (Volume-Synchronized Probability of Informed Trading) indicator.

Detects toxic order flow by measuring the imbalance between buyer-initiated
and seller-initiated volume across equal-volume buckets.

Algorithm:
    1. Classify each trade as buy/sell using the tick rule (Lee-Ready):
       - Trade at ask or above prior price → buyer-initiated
       - Trade at bid or below prior price → seller-initiated
    2. Accumulate trades into equal-volume buckets (bucket_volume each).
    3. For each bucket, compute |V_buy - V_sell| / bucket_volume.
    4. VPIN = mean of the last N bucket imbalances.

Usage:
    vpin = VPINCalculator()
    vpin.on_trade(price=100.50, size=0.5, bid=100.49, ask=100.51)
    reading = vpin.get_reading()
    if reading and reading.is_toxic:
        # widen spreads or pull quotes
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass
class VPINConfig:
    """Configuration for VPIN calculation."""

    bucket_volume: float = 10.0     # Volume per bucket (e.g. 10 BTC, or 100 contracts)
    num_buckets: int = 50           # Rolling window of buckets for VPIN average
    toxic_threshold: float = 0.70   # VPIN above this → toxic flow
    warning_threshold: float = 0.50 # VPIN above this → elevated risk


@dataclass
class VPINReading:
    """Output of the VPIN calculator."""

    vpin: float                     # VPIN value [0, 1]
    num_buckets: int                # Number of completed buckets in window
    is_toxic: bool                  # vpin >= toxic_threshold
    is_warning: bool                # vpin >= warning_threshold
    buy_volume_pct: float           # Buy fraction of recent volume
    last_bucket_imbalance: float    # Imbalance of the most recent bucket
    timestamp: float


@dataclass
class _Bucket:
    """A single equal-volume bucket."""

    buy_volume: float = 0.0
    sell_volume: float = 0.0
    total_volume: float = 0.0


class VPINCalculator:
    """Computes VPIN from a stream of trades.

    Feed trades via on_trade(). Read the current VPIN via get_reading().
    """

    def __init__(self, config: Optional[VPINConfig] = None) -> None:
        self._config = config or VPINConfig()

        # Completed bucket imbalances: |V_buy - V_sell| / bucket_volume
        self._bucket_imbalances: Deque[float] = deque(
            maxlen=self._config.num_buckets
        )

        # Current in-progress bucket
        self._current_bucket = _Bucket()

        # Last trade price for tick rule fallback
        self._last_trade_price: Optional[float] = None

        # Running totals for buy percentage
        self._total_buy_volume: float = 0.0
        self._total_sell_volume: float = 0.0

    def on_trade(
        self,
        price: float,
        size: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        is_buy: Optional[bool] = None,
    ) -> None:
        """Record a trade and update VPIN buckets.

        Trade classification priority:
            1. Explicit is_buy if provided
            2. Lee-Ready: price >= ask → buy, price <= bid → sell
            3. Tick rule: price > last_price → buy, price < last_price → sell
            4. Default: split 50/50

        Args:
            price: Trade execution price
            size: Trade volume (contracts, BTC, etc.)
            bid: Current best bid at time of trade (for Lee-Ready)
            ask: Current best ask at time of trade (for Lee-Ready)
            is_buy: Explicit classification (overrides Lee-Ready/tick rule)
        """
        # Classify trade
        if is_buy is not None:
            trade_is_buy = is_buy
        elif bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            if price >= ask:
                trade_is_buy = True
            elif price <= bid:
                trade_is_buy = False
            elif price > mid:
                trade_is_buy = True
            elif price < mid:
                trade_is_buy = False
            elif self._last_trade_price is not None:
                # Tick rule fallback for trades exactly at mid
                trade_is_buy = price >= self._last_trade_price
            else:
                trade_is_buy = True  # Default: split evenly handled below
        elif self._last_trade_price is not None:
            # Pure tick rule
            trade_is_buy = price >= self._last_trade_price
        else:
            trade_is_buy = True  # First trade, no info

        self._last_trade_price = price

        # Add to current bucket, potentially filling multiple buckets
        remaining = size
        while remaining > 0:
            space = self._config.bucket_volume - self._current_bucket.total_volume
            fill = min(remaining, space)

            if trade_is_buy:
                self._current_bucket.buy_volume += fill
                self._total_buy_volume += fill
            else:
                self._current_bucket.sell_volume += fill
                self._total_sell_volume += fill
            self._current_bucket.total_volume += fill
            remaining -= fill

            # Bucket full → compute imbalance and start new bucket
            if self._current_bucket.total_volume >= self._config.bucket_volume:
                bv = self._config.bucket_volume
                imbalance = abs(
                    self._current_bucket.buy_volume - self._current_bucket.sell_volume
                ) / bv
                self._bucket_imbalances.append(imbalance)
                self._current_bucket = _Bucket()

    def get_reading(self) -> Optional[VPINReading]:
        """Current VPIN reading. Returns None if no buckets completed yet."""
        if not self._bucket_imbalances:
            return None

        vpin = sum(self._bucket_imbalances) / len(self._bucket_imbalances)
        total = self._total_buy_volume + self._total_sell_volume
        buy_pct = self._total_buy_volume / total if total > 0 else 0.5

        return VPINReading(
            vpin=vpin,
            num_buckets=len(self._bucket_imbalances),
            is_toxic=vpin >= self._config.toxic_threshold,
            is_warning=vpin >= self._config.warning_threshold,
            buy_volume_pct=buy_pct,
            last_bucket_imbalance=self._bucket_imbalances[-1],
            timestamp=time.time(),
        )

    def reset(self) -> None:
        """Clear all state."""
        self._bucket_imbalances.clear()
        self._current_bucket = _Bucket()
        self._last_trade_price = None
        self._total_buy_volume = 0.0
        self._total_sell_volume = 0.0
