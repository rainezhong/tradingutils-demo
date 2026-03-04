"""Fill simulation models for the unified backtest framework.

A FillModel decides whether a Signal converts into a Fill and at what
price / fee.  The default ImmediateFillModel fills every signal at its
limit price (optionally applying slippage and a probabilistic fill rate).
"""

import random
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from src.core.models import Fill, MarketState
from strategies.base import Signal

from .repricing_lag import KalshiRepricingConfig, check_kalshi_staleness


def kalshi_taker_fee(price: float) -> float:
    """Kalshi taker fee per contract in dollars.

    Formula: min(0.0175, 0.07 * P * (1 - P))
    where P is the price in decimal (0-1).
    """
    if price <= 0 or price >= 1:
        return 0.0
    return min(0.0175, 0.07 * price * (1.0 - price))


def kalshi_maker_fee(price: float) -> float:
    """Kalshi maker fee per contract in dollars.

    Formula: min(0.0175, 0.0175 * P * (1 - P))
    where P is the price in decimal (0-1).
    """
    if price <= 0 or price >= 1:
        return 0.0
    return min(0.0175, 0.0175 * price * (1.0 - price))


# ---------------------------------------------------------------------------
# Network Latency Model
# ---------------------------------------------------------------------------


def apply_network_latency(
    signal: Signal,
    market_at_signal: MarketState,
    latency_config: object,  # NetworkLatencyConfig from realism_config
    get_delayed_state_fn: object,  # Callable[[str, datetime], Optional[MarketState]]
) -> Tuple[MarketState, float]:
    """Apply network latency to simulate delayed execution.

    Args:
        signal: The signal at time T.
        market_at_signal: Market state at signal time T.
        latency_config: NetworkLatencyConfig from realism_config.
        get_delayed_state_fn: Function to get market state at T + latency.
            Signature: (ticker, timestamp) -> Optional[MarketState]

    Returns:
        Tuple of (adjusted_market_state, latency_ms):
            - adjusted_market_state: Market state adjusted for latency and
              adverse selection. If no delayed state is available, returns
              the original market state.
            - latency_ms: The sampled latency in milliseconds.

    The function:
    1. Samples latency from config
    2. Fetches market state at T + latency
    3. If spot price moved, applies adverse selection:
       - For BUY orders (BID): if spot up, ask worsens proportionally
       - For SELL orders (ASK): if spot down, bid worsens proportionally
    """
    if not latency_config.enabled:
        return market_at_signal, 0.0

    # Sample latency based on mode
    if latency_config.mode == "fixed":
        latency_ms = latency_config.latency_ms
    elif latency_config.mode == "optimistic":
        latency_ms = max(
            latency_config.min_latency_ms,
            latency_config.latency_ms - latency_config.std_ms
        )
    elif latency_config.mode == "pessimistic":
        latency_ms = min(
            latency_config.max_latency_ms,
            latency_config.latency_ms + latency_config.std_ms
        )
    else:  # sampled
        latency_ms = random.gauss(latency_config.latency_ms, latency_config.std_ms)
        latency_ms = max(latency_config.min_latency_ms, latency_ms)
        latency_ms = min(latency_config.max_latency_ms, latency_ms)

    # Calculate delayed timestamp
    if signal.timestamp is None:
        # No timestamp, can't apply latency
        return market_at_signal, latency_ms

    delayed_ts = signal.timestamp + timedelta(milliseconds=latency_ms)

    # Fetch delayed market state
    delayed_market = get_delayed_state_fn(signal.ticker, delayed_ts)

    if delayed_market is None:
        # No data at delayed time, return original state
        return market_at_signal, latency_ms

    # Apply adverse selection based on spot movement
    # We use mid price as a proxy for "fair value" movement
    spot_move = delayed_market.mid - market_at_signal.mid
    adverse_move = spot_move * latency_config.adverse_selection_factor

    # Clone the delayed market and adjust prices for adverse selection
    if signal.side == "BID":
        # Buying: if spot moved up, we pay more (ask increases)
        adjusted_ask = delayed_market.ask
        if adverse_move > 0:
            adjusted_ask = min(1.0, delayed_market.ask + adverse_move)
        adjusted_market = MarketState(
            ticker=delayed_market.ticker,
            timestamp=delayed_market.timestamp,
            bid=delayed_market.bid,
            ask=adjusted_ask,
            last_price=delayed_market.last_price,
            volume=delayed_market.volume,
        )
    else:  # ASK
        # Selling: if spot moved down, we get less (bid decreases)
        adjusted_bid = delayed_market.bid
        if adverse_move < 0:
            adjusted_bid = max(0.0, delayed_market.bid + adverse_move)
        adjusted_market = MarketState(
            ticker=delayed_market.ticker,
            timestamp=delayed_market.timestamp,
            bid=adjusted_bid,
            ask=delayed_market.ask,
            last_price=delayed_market.last_price,
            volume=delayed_market.volume,
        )

    return adjusted_market, latency_ms



# ---------------------------------------------------------------------------
# Queue Priority Fill Model
# ---------------------------------------------------------------------------


@dataclass
class QueuePriorityConfig:
    """Configuration for queue priority fill simulation.

    Models the fact that limit orders compete with others at the same price,
    so we may not fill instantly even if depth exists.

    Attributes:
        enable_queue_priority: Enable queue priority logic.
        min_depth_multiple: Require N× depth for instant fill (e.g., 3.0 = 3× order size).
        queue_factor: Assume we're at position 1/N in queue (e.g., 3.0 = middle of queue).
        enable_partial_fills: Allow partial fills when depth is insufficient.
    """
    enable_queue_priority: bool = False
    min_depth_multiple: float = 3.0
    queue_factor: float = 3.0
    enable_partial_fills: bool = True


def apply_queue_priority(
    order_size: int,
    depth: Optional[int],
    config: QueuePriorityConfig,
) -> Optional[int]:
    """Calculate fill size accounting for queue priority.

    Models limit order competition at the same price level. When placing a
    limit order, we don't know our position in the queue. This function
    estimates fill probability based on available depth.

    Logic:
    - If depth >= order_size * min_depth_multiple: instant full fill
    - Otherwise: partial fill based on queue position estimate
    - If depth is 0 or None: no fill

    Args:
        order_size: Number of contracts we want to fill.
        depth: Available contracts at our price level (None if unknown).
        config: Queue priority configuration.

    Returns:
        Fill size (0 to order_size), or None if no fill.

    Examples:
        >>> cfg = QueuePriorityConfig(enable_queue_priority=True, min_depth_multiple=3.0, queue_factor=3.0)
        >>> apply_queue_priority(10, 50, cfg)  # High depth: instant full fill
        10
        >>> apply_queue_priority(10, 15, cfg)  # Low depth: partial fill (~5 contracts)
        5
        >>> apply_queue_priority(10, 0, cfg)   # No depth: no fill
        None
    """
    if not config.enable_queue_priority:
        return order_size

    # Edge case: no depth available
    if depth is None or depth <= 0:
        return None

    # High depth: instant full fill
    if depth >= order_size * config.min_depth_multiple:
        return order_size

    # Low depth: estimate queue position
    # If queue_factor=3.0, we assume we're at position 1/3 in the queue
    # So we need 3× our size in depth to guarantee a fill
    fill_prob = depth / (order_size * config.queue_factor)

    if config.enable_partial_fills:
        # Partial fill: expect to get fill_prob fraction of our order
        fill_size = int(order_size * min(fill_prob, 1.0))
        return fill_size if fill_size > 0 else None
    else:
        # Binary: fill or don't fill based on probability
        if random.random() < fill_prob:
            return order_size
        else:
            return None


# ---------------------------------------------------------------------------
# Orderbook Staleness Model
# ---------------------------------------------------------------------------


@dataclass
class OrderbookStalenessConfig:
    """Configuration for orderbook staleness penalty model.

    Accounts for orderbook snapshots being up to max_staleness_ms stale
    when signals fire, widening the spread based on spot price velocity.
    """

    enable_staleness_penalty: bool = False
    max_staleness_ms: float = 200.0  # Reject if snapshot older than this
    velocity_penalty_factor: float = 1.0  # Scale penalty by spot velocity


def get_effective_spread(
    signal_ts: datetime,
    snapshot_ts: float,
    bid: float,
    ask: float,
    spot_price: Optional[float] = None,
    prev_spot_price: Optional[float] = None,
    prev_spot_ts: Optional[float] = None,
    config: Optional[OrderbookStalenessConfig] = None,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Calculate effective bid/ask prices accounting for orderbook staleness.

    When orderbook snapshots are stale relative to signal timestamp, the
    true spread may have widened due to price movement. This function estimates
    the effective spread by penalizing based on spot price velocity and staleness.

    Args:
        signal_ts: When the signal fired (datetime)
        snapshot_ts: When the orderbook snapshot was taken (unix timestamp)
        bid: Raw bid price from snapshot (dollars, 0-1)
        ask: Raw ask price from snapshot (dollars, 0-1)
        spot_price: Current spot price (dollars)
        prev_spot_price: Previous spot price for velocity calculation (dollars)
        prev_spot_ts: Timestamp of previous spot price (unix timestamp)
        config: Staleness configuration (optional, defaults disabled)

    Returns:
        (effective_bid, effective_ask, rejection_reason)
        Returns (None, None, reason) if snapshot is too stale.
    """
    if config is None or not config.enable_staleness_penalty:
        return bid, ask, None

    # Calculate staleness in seconds
    signal_unix = signal_ts.timestamp()
    staleness_sec = signal_unix - snapshot_ts
    staleness_ms = staleness_sec * 1000.0

    # Reject if snapshot is too old
    if staleness_ms > config.max_staleness_ms:
        return None, None, f"snapshot_stale_{staleness_ms:.0f}ms"

    # If no spot velocity data, return raw spread
    if spot_price is None or prev_spot_price is None or prev_spot_ts is None:
        return bid, ask, None

    # Calculate spot velocity ($/sec)
    dt = signal_unix - prev_spot_ts
    if dt <= 0:
        return bid, ask, None

    spot_velocity = abs(spot_price - prev_spot_price) / dt

    # Compute staleness penalty in dollars
    # Formula: velocity ($/s) * staleness (s) * factor
    penalty_dollars = spot_velocity * staleness_sec * config.velocity_penalty_factor

    # Widen spread: subtract from bid, add to ask
    effective_bid = max(0.0, bid - penalty_dollars)
    effective_ask = min(1.0, ask + penalty_dollars)

    return effective_bid, effective_ask, None

# ---------------------------------------------------------------------------
# Fill Models
# ---------------------------------------------------------------------------


class FillModel(ABC):
    """Determines whether and how a signal gets filled."""

    @abstractmethod
    def simulate_fill(
        self,
        signal: Signal,
        market: MarketState,
    ) -> Optional[Fill]:
        """Attempt to fill *signal* given current *market* state.

        Returns a Fill if the order would execute, otherwise None.
        """
        ...


class ImmediateFillModel(FillModel):
    """Fills every signal immediately at the ask (BID) or bid (ASK).

    Optionally applies:
    - *fill_probability*: random rejection to simulate partial fills.
    - *slippage*: additive price worsening (in the same units as price).
    - *fee_fn*: callable(price) -> per-contract fee in dollars.
      Defaults to Kalshi taker formula when fee_fn is None.
    - *repricing_config*: KalshiRepricingConfig to model orderbook staleness.
    """

    def __init__(
        self,
        fill_probability: float = 1.0,
        slippage: float = 0.0,
        fee_fn: Optional[object] = None,  # Callable[[float], float]
        repricing_config: Optional[KalshiRepricingConfig] = None,
        impact_config: Optional[object] = None,  # MarketImpactConfig
        queue_config: Optional[object] = None,  # QueuePriorityConfig
        latency_config: Optional[object] = None,  # NetworkLatencyConfig
        staleness_config: Optional[object] = None,  # OrderbookStalenessConfig
    ):
        self._fill_prob = fill_probability
        self._slippage = slippage
        self._fee_fn = fee_fn
        self._repricing_config = repricing_config or KalshiRepricingConfig()
        self._impact_config = impact_config
        self._queue_config = queue_config
        self._latency_config = latency_config
        self._staleness_config = staleness_config
        self._prev_spot_price: Optional[float] = None
        self._prev_spot_ts: Optional[float] = None

    def simulate_fill(
        self,
        signal: Signal,
        market: MarketState,
        context: Optional[dict] = None,
    ) -> Optional[Fill]:
        if self._fill_prob < 1.0 and random.random() > self._fill_prob:
            return None

        # Check Kalshi repricing lag (orderbook staleness)
        if context is not None:
            current_ts = market.timestamp.timestamp()
            if not check_kalshi_staleness(
                context=context,
                config=self._repricing_config,
                current_ts=current_ts,
                prev_spot_price=self._prev_spot_price,
                prev_spot_ts=self._prev_spot_ts,
            ):
                return None

            # Update state
            current_spot = context.get("kraken_spot")
            spot_ts = context.get("kraken_ts")
            if current_spot is not None and spot_ts is not None:
                self._prev_spot_price = current_spot
                self._prev_spot_ts = spot_ts

        # Determine execution price
        if signal.side == "BID":
            exec_price = market.ask + self._slippage
        else:
            exec_price = max(0.0, market.bid - self._slippage)

        # Calculate fee
        if self._fee_fn is not None:
            fee = self._fee_fn(exec_price) * signal.size
        else:
            fee = kalshi_taker_fee(exec_price) * signal.size

        return Fill(
            ticker=signal.ticker,
            side=signal.side,
            price=exec_price,
            size=signal.size,
            order_id=uuid.uuid4().hex[:12],
            fill_id=uuid.uuid4().hex[:12],
            timestamp=signal.timestamp,
            fee=fee,
        )


class MakerFillModel(FillModel):
    """Fills at the signal's stated price (resting quote) with maker fees.

    Designed for market-maker backtests where the adapter controls fill
    decisions (queue priority, depth-weighted probability) and the fill
    model just stamps the execution price and fee.
    """

    def simulate_fill(
        self,
        signal: Signal,
        market: MarketState,
    ) -> Optional[Fill]:
        exec_price = signal.price
        fee = kalshi_maker_fee(exec_price) * signal.size

        return Fill(
            ticker=signal.ticker,
            side=signal.side,
            price=exec_price,
            size=signal.size,
            order_id=uuid.uuid4().hex[:12],
            fill_id=uuid.uuid4().hex[:12],
            timestamp=signal.timestamp,
            fee=fee,
        )
