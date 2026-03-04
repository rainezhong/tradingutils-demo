"""Velocity estimator - decomposes depth changes into fills vs cancellations."""

import bisect
import logging
import math
from typing import Dict, List, Optional, Tuple

from .config import FillTimeConfig
from .models import SnapshotRecord, VelocityObservation

logger = logging.getLogger(__name__)


def _bucket_spread(spread: int, buckets: List[int]) -> int:
    """Map a spread value to its bucket."""
    idx = bisect.bisect_right(buckets, spread) - 1
    return buckets[max(0, idx)]


class VelocityEstimator:
    """Estimates fill velocity from consecutive order book snapshots.

    Core algorithm:
    1. Align price levels between consecutive snapshots
    2. For levels with decreased depth, decompose into fills vs cancels:
       - At BBO: ~80% attributed to fills (aggressive orders consumed them)
       - Away from BBO: less fill attribution, decaying by distance
       - BBO transitions (price moves through level): 100% fills
    3. Compute velocity = estimated_fills / dt_seconds
    4. Maintain EMA per (side, spread_bucket) segment
    """

    def __init__(self, config: FillTimeConfig):
        self._config = config
        # Previous snapshot per ticker for pairing
        self._prev_snapshot: Dict[str, SnapshotRecord] = {}

        # EMA velocity per (ticker, side, spread_bucket)
        # Key: (ticker, side, spread_bucket) -> (ema_velocity, last_update_time, count)
        self._velocity_ema: Dict[Tuple[str, str, int], Tuple[float, float, int]] = {}

        # Raw observations for debugging/calibration
        self._recent_observations: List[VelocityObservation] = []
        self._max_recent: int = 5000

    def process_snapshot(self, snap: SnapshotRecord) -> List[VelocityObservation]:
        """Process a snapshot, returning velocity observations if a previous exists."""
        prev = self._prev_snapshot.get(snap.ticker)
        self._prev_snapshot[snap.ticker] = snap

        if prev is None:
            return []

        dt = snap.timestamp - prev.timestamp
        if dt <= 0 or dt > 30.0:  # skip stale or bogus pairs
            return []

        observations = []
        spread_bucket = _bucket_spread(snap.spread or 0, self._config.spread_buckets)

        # Process bid side
        bid_obs = self._analyze_side(
            prev_levels=prev.bids,
            curr_levels=snap.bids,
            prev_best=prev.best_bid,
            curr_best=snap.best_bid,
            side="bid",
            ticker=snap.ticker,
            timestamp=snap.timestamp,
            dt=dt,
            spread_bucket=spread_bucket,
        )
        observations.extend(bid_obs)

        # Process ask side
        ask_obs = self._analyze_side(
            prev_levels=prev.asks,
            curr_levels=snap.asks,
            prev_best=prev.best_ask,
            curr_best=snap.best_ask,
            side="ask",
            ticker=snap.ticker,
            timestamp=snap.timestamp,
            dt=dt,
            spread_bucket=spread_bucket,
        )
        observations.extend(ask_obs)

        # Update EMAs
        for obs in observations:
            self._update_ema(obs)

        # Store recent observations
        self._recent_observations.extend(observations)
        if len(self._recent_observations) > self._max_recent:
            self._recent_observations = self._recent_observations[-self._max_recent :]

        return observations

    def _analyze_side(
        self,
        prev_levels: List[List[int]],
        curr_levels: List[List[int]],
        prev_best: Optional[int],
        curr_best: Optional[int],
        side: str,
        ticker: str,
        timestamp: float,
        dt: float,
        spread_bucket: int,
    ) -> List[VelocityObservation]:
        """Analyze depth changes on one side between consecutive snapshots."""
        if prev_best is None or curr_best is None:
            return []

        # Build price -> size maps
        prev_map: Dict[int, int] = {p: s for p, s in prev_levels}
        curr_map: Dict[int, int] = {p: s for p, s in curr_levels}

        # Detect BBO transition
        if side == "bid":
            bbo_transition = curr_best < prev_best  # best bid dropped
        else:
            bbo_transition = curr_best > prev_best  # best ask rose

        observations = []
        all_prices = set(prev_map.keys()) | set(curr_map.keys())

        total_estimated_fills = 0.0
        total_estimated_cancels = 0.0

        for price in all_prices:
            prev_size = prev_map.get(price, 0)
            curr_size = curr_map.get(price, 0)
            depth_change = curr_size - prev_size

            if depth_change >= 0:
                continue  # depth increased or unchanged, no fills to infer

            decrease = -depth_change

            # Determine fill fraction based on position relative to BBO
            fill_fraction = self._infer_fill_fraction(
                price=price,
                prev_best=prev_best,
                curr_best=curr_best,
                side=side,
                bbo_transition=bbo_transition,
                prev_size=prev_size,
                curr_size=curr_size,
            )

            estimated_fills = decrease * fill_fraction
            estimated_cancels = decrease * (1.0 - fill_fraction)
            total_estimated_fills += estimated_fills
            total_estimated_cancels += estimated_cancels

        if total_estimated_fills > 0:
            velocity = total_estimated_fills / dt
            obs = VelocityObservation(
                ticker=ticker,
                timestamp=timestamp,
                side=side,
                price=curr_best,
                dt_seconds=dt,
                depth_change=-(int(total_estimated_fills + total_estimated_cancels)),
                estimated_fills=total_estimated_fills,
                estimated_cancels=total_estimated_cancels,
                velocity=velocity,
                spread_bucket=spread_bucket,
                bbo_transition=bbo_transition,
            )
            observations.append(obs)

        return observations

    def _infer_fill_fraction(
        self,
        price: int,
        prev_best: int,
        curr_best: int,
        side: str,
        bbo_transition: bool,
        prev_size: int,
        curr_size: int,
    ) -> float:
        """Infer what fraction of a depth decrease was fills vs cancellations.

        Heuristics:
        - Price was consumed by BBO transition (price moved through) -> 100% fills
        - At BBO with stable spread -> ~80% fills
        - Away from BBO -> fill fraction decays by distance from best
        """
        # Level was fully consumed and price moved through it -> all fills
        if (
            side == "bid"
            and bbo_transition
            and price >= curr_best
            and price <= prev_best
        ):
            if curr_size == 0 and price == prev_best:
                return 1.0
        if (
            side == "ask"
            and bbo_transition
            and price <= curr_best
            and price >= prev_best
        ):
            if curr_size == 0 and price == prev_best:
                return 1.0

        # Distance from the previous best (where fills happen)
        if side == "bid":
            distance = prev_best - price
        else:
            distance = price - prev_best

        if distance < 0:
            distance = 0

        if distance == 0:
            # At best bid/ask: high fill fraction
            return self._config.fill_fraction_at_best
        else:
            # Decay by distance: fill_fraction_at_best * (1 - decay)^distance
            decay = self._config.cancel_decay_per_cent
            return self._config.fill_fraction_at_best * ((1.0 - decay) ** distance)

    def _update_ema(self, obs: VelocityObservation) -> None:
        """Update exponential moving average for velocity."""
        key = (obs.ticker, obs.side, obs.spread_bucket)
        halflife = self._config.velocity_ema_halflife_seconds

        prev_ema, prev_time, count = self._velocity_ema.get(key, (0.0, 0.0, 0))

        if count == 0:
            self._velocity_ema[key] = (obs.velocity, obs.timestamp, 1)
            return

        dt = obs.timestamp - prev_time
        if dt <= 0:
            return

        # EMA decay factor: alpha = 1 - exp(-dt * ln2 / halflife)
        alpha = 1.0 - math.exp(-dt * math.log(2) / halflife)
        new_ema = prev_ema + alpha * (obs.velocity - prev_ema)
        self._velocity_ema[key] = (new_ema, obs.timestamp, count + 1)

    def get_velocity(
        self, ticker: str, side: str, spread: Optional[int] = None
    ) -> Tuple[float, int]:
        """Get current velocity estimate for a ticker/side.

        Returns:
            (velocity, observation_count). If no observations, returns
            (prior_velocity, 0) with Bayesian blending.
        """
        spread_bucket = _bucket_spread(spread or 0, self._config.spread_buckets)
        key = (ticker, side, spread_bucket)

        if key in self._velocity_ema:
            ema, _, count = self._velocity_ema[key]
            # Bayesian blending with prior
            prior = self._config.prior_velocity
            prior_w = self._config.prior_weight
            blended = (prior * prior_w + ema * count) / (prior_w + count)
            return blended, count

        return self._config.prior_velocity, 0

    def get_all_velocities(
        self,
    ) -> Dict[Tuple[str, str, int], Tuple[float, float, int]]:
        """Get all tracked velocity EMAs. Returns dict of key -> (ema, last_time, count)."""
        return dict(self._velocity_ema)

    @property
    def recent_observations(self) -> List[VelocityObservation]:
        return self._recent_observations
