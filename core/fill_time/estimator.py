"""Fill time estimator - main API for fill probability estimation."""

import logging
import math
from typing import Optional

from core.market.orderbook_manager import OrderBookState

from .config import FillTimeConfig
from .models import FillTimeEstimate, RoundTripEstimate
from .queue import QueuePositionCalculator
from .velocity import VelocityEstimator

logger = logging.getLogger(__name__)


def _kalshi_fee(rate: float, contracts: int, price_cents: int) -> float:
    """Calculate Kalshi fee: ceil(rate * C * P * (1-P) * 100) / 100."""
    p = price_cents / 100.0
    return math.ceil(rate * contracts * p * (1.0 - p) * 100.0) / 100.0


class FillTimeEstimator:
    """Estimates fill time distributions for hypothetical limit orders.

    Primary model: Exponential (fills as Poisson process)
      rate = velocity / queue_position
      P(fill within t) = 1 - exp(-rate * t)

    Secondary model: Gamma (discrete chunk consumption)
      shape = queue_position / avg_fill_size
      scale = avg_fill_size / velocity
    """

    def __init__(
        self,
        config: FillTimeConfig,
        velocity_estimator: VelocityEstimator,
        queue_calculator: Optional[QueuePositionCalculator] = None,
    ):
        self._config = config
        self._velocity = velocity_estimator
        self._queue = queue_calculator or QueuePositionCalculator(config)

    def estimate_fill_time(
        self,
        book: OrderBookState,
        side: str,
        price: int,
    ) -> FillTimeEstimate:
        """Estimate fill time distribution for a single limit order.

        Args:
            book: Current order book state
            side: "bid" or "ask"
            price: Order price in cents

        Returns:
            FillTimeEstimate with time distributions and probabilities
        """
        queue_pos = self._queue.estimate_queue_position(book, side, price)
        velocity, obs_count = self._velocity.get_velocity(
            book.ticker, side, book.spread
        )

        if self._config.model_type == "gamma":
            return self._gamma_model(side, price, queue_pos, velocity, obs_count)
        else:
            return self._exponential_model(side, price, queue_pos, velocity, obs_count)

    def _exponential_model(
        self,
        side: str,
        price: int,
        queue_pos: float,
        velocity: float,
        obs_count: int,
    ) -> FillTimeEstimate:
        """Exponential model: fills as Poisson process."""
        # rate = velocity / queue_position (contracts consumed per second / contracts ahead)
        rate = velocity / max(queue_pos, 1.0)
        rate = max(rate, 1e-10)  # prevent division by zero

        expected = 1.0 / rate
        median = math.log(2) / rate
        std = 1.0 / rate  # std of exponential = 1/rate

        # P(fill within t) = 1 - exp(-rate * t)
        p_30 = 1.0 - math.exp(-rate * 30)
        p_60 = 1.0 - math.exp(-rate * 60)
        p_120 = 1.0 - math.exp(-rate * 120)
        p_300 = 1.0 - math.exp(-rate * 300)

        # P(ever fills) - competing with price drift
        # Simple model: fills compete with exponential drift rate
        # For now, use high probability with decay for far-from-best orders
        p_ever = min(1.0, p_300 + (1.0 - p_300) * 0.5)

        confidence = self._confidence_level(obs_count)

        return FillTimeEstimate(
            side=side,
            price=price,
            queue_position=queue_pos,
            velocity=velocity,
            observation_count=obs_count,
            expected_seconds=expected,
            median_seconds=median,
            std_seconds=std,
            p_fill_30s=p_30,
            p_fill_60s=p_60,
            p_fill_120s=p_120,
            p_fill_300s=p_300,
            p_ever_fills=p_ever,
            model_type="exponential",
            confidence=confidence,
        )

    def _gamma_model(
        self,
        side: str,
        price: int,
        queue_pos: float,
        velocity: float,
        obs_count: int,
    ) -> FillTimeEstimate:
        """Gamma model: queue consumed in discrete chunks."""
        avg_fill_size = max(velocity * 3.0, 1.0)  # rough avg fill size
        shape = max(queue_pos / avg_fill_size, 1.0)
        scale = avg_fill_size / max(velocity, 1e-10)

        expected = shape * scale
        # Gamma median approximation: shape*scale * (1 - 1/(9*shape))^3
        if shape >= 1:
            median = expected * (1.0 - 1.0 / (9.0 * shape)) ** 3
        else:
            median = expected * 0.7  # rough for shape < 1
        std = math.sqrt(shape) * scale

        # CDF via incomplete gamma - use exponential approximation for simplicity
        rate = 1.0 / max(expected, 1e-10)
        p_30 = 1.0 - math.exp(-rate * 30)
        p_60 = 1.0 - math.exp(-rate * 60)
        p_120 = 1.0 - math.exp(-rate * 120)
        p_300 = 1.0 - math.exp(-rate * 300)
        p_ever = min(1.0, p_300 + (1.0 - p_300) * 0.5)

        confidence = self._confidence_level(obs_count)

        return FillTimeEstimate(
            side=side,
            price=price,
            queue_position=queue_pos,
            velocity=velocity,
            observation_count=obs_count,
            expected_seconds=expected,
            median_seconds=median,
            std_seconds=std,
            p_fill_30s=p_30,
            p_fill_60s=p_60,
            p_fill_120s=p_120,
            p_fill_300s=p_300,
            p_ever_fills=p_ever,
            model_type="gamma",
            confidence=confidence,
        )

    def _confidence_level(self, obs_count: int) -> str:
        if obs_count >= self._config.min_observations_for_estimate * 5:
            return "high"
        elif obs_count >= self._config.min_observations_for_estimate:
            return "medium"
        else:
            return "low"

    def estimate_round_trip_time(
        self,
        book: OrderBookState,
        entry_price: int,
        exit_price: int,
        size: int,
        entry_fee_rate: float = 0.0175,
        exit_fee_rate: float = 0.0175,
    ) -> RoundTripEstimate:
        """Estimate round-trip fill time for entry (bid) + exit (ask).

        Args:
            book: Current order book state
            entry_price: Buy price in cents
            exit_price: Sell price in cents
            size: Number of contracts
            entry_fee_rate: Fee rate for entry order
            exit_fee_rate: Fee rate for exit order

        Returns:
            RoundTripEstimate with combined probabilities and EV
        """
        entry_est = self.estimate_fill_time(book, "bid", entry_price)
        exit_est = self.estimate_fill_time(book, "ask", exit_price)

        # Combined probability: both legs must fill
        p_rt = entry_est.p_ever_fills * exit_est.p_ever_fills
        p_rt_60 = entry_est.p_fill_60s * exit_est.p_fill_60s
        p_rt_120 = entry_est.p_fill_120s * exit_est.p_fill_120s

        # Compute edge
        gross_edge = (exit_price - entry_price) / 100.0
        entry_fee = _kalshi_fee(entry_fee_rate, 1, entry_price)
        exit_fee = _kalshi_fee(exit_fee_rate, 1, exit_price)
        net_edge = gross_edge - entry_fee - exit_fee

        return RoundTripEstimate(
            entry=entry_est,
            exit=exit_est,
            p_round_trip_completes=p_rt,
            p_round_trip_60s=p_rt_60,
            p_round_trip_120s=p_rt_120,
            gross_edge_per_contract=gross_edge,
            expected_profit_per_contract=net_edge * p_rt,
            expected_profit_total=net_edge * p_rt * size,
            size=size,
        )
