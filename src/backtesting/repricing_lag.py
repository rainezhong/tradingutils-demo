"""Kalshi repricing lag model for backtest fill simulation.

Models the observed 5-10s delay between spot price movements and Kalshi orderbook
updates. Rejects fills when the orderbook is stale relative to recent spot movements.

This is distinct from the time-delay based RepricingLagConfig in realism_config.py:
- RepricingLagConfig: models MM reaction time (time-based delay)
- KalshiRepricingConfig: models orderbook staleness vs spot velocity (velocity-based check)

Both approaches are valid and complementary for different use cases.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class KalshiRepricingConfig:
    """Configuration for modeling Kalshi's repricing lag after spot moves.

    Kalshi prices do not update instantly when spot prices move. This config
    models the 5-10s lag observed in production, rejecting fills when the
    orderbook is stale relative to recent spot movement.

    Attributes:
        enable_repricing_lag: Enable staleness filtering (default: False).
        max_staleness_sec: Maximum time since last Kalshi update before
            considering the orderbook stale (default: 5.0s).
        min_spot_velocity_threshold: Minimum spot price velocity in cents/sec
            before considering it "fast movement" (default: 0.01 cents/sec).
            Example: 0.5 cent move in 5 seconds = 0.1 cents/sec velocity.
    """

    enable_repricing_lag: bool = False
    max_staleness_sec: float = 5.0
    min_spot_velocity_threshold: float = 0.01  # cents/sec


def check_kalshi_staleness(
    context: Dict[str, Any],
    config: KalshiRepricingConfig,
    current_ts: float,
    prev_spot_price: Optional[float] = None,
    prev_spot_ts: Optional[float] = None,
) -> bool:
    """Check if Kalshi orderbook is stale relative to spot price movement.

    Args:
        context: BacktestFrame.context dict containing:
            - "kraken_spot": current spot price (float, in dollars)
            - "kraken_ts": timestamp of spot observation (float, epoch seconds)
        config: KalshiRepricingConfig instance.
        current_ts: Current frame timestamp (float, epoch seconds).
        prev_spot_price: Previous frame's spot price (optional).
        prev_spot_ts: Previous frame's spot timestamp (optional).

    Returns:
        True if fill should be allowed, False if orderbook is stale and
        fill should be rejected.

    Logic:
        1. If repricing lag is disabled, always return True.
        2. Calculate spot velocity: (price_delta / time_delta) in cents/sec.
        3. Calculate time since last Kalshi update (current_ts - kraken_ts).
        4. Reject fill if:
           - Spot velocity > threshold AND
           - Time since Kalshi update > max_staleness
    """
    if not config.enable_repricing_lag:
        return True

    # Extract spot price and timestamp from context
    current_spot = context.get("kraken_spot")
    spot_ts = context.get("kraken_ts")

    if current_spot is None or spot_ts is None:
        # No spot data available, allow fill (graceful degradation)
        return True

    # Need previous spot price to calculate velocity
    if prev_spot_price is None or prev_spot_ts is None:
        # First frame, no velocity to calculate, allow fill
        return True

    # Calculate spot velocity in cents/sec
    time_delta = current_ts - prev_spot_ts
    if time_delta <= 0:
        # Time delta is zero or negative, allow fill
        return True

    price_delta = abs(current_spot - prev_spot_price) * 100  # Convert to cents
    spot_velocity = price_delta / time_delta  # cents/sec

    # Calculate staleness: how long since Kalshi orderbook was updated
    # (approximated by time since spot observation)
    time_since_update = current_ts - spot_ts

    # Reject if spot is moving fast AND orderbook is stale
    if (
        spot_velocity > config.min_spot_velocity_threshold
        and time_since_update > config.max_staleness_sec
    ):
        return False

    return True
