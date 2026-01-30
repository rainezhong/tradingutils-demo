"""Leg risk management for arbitrage execution.

Handles situations where one leg of an arbitrage trade fills but the other
does not, creating "leg risk" - exposure to directional market movement.

Provides strategies for:
- Canceling unfilled legs
- Hedging filled positions
- Tracking and reporting leg risk events
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, List, Optional

from src.core.exchange import TradableMarket
from arb.spread_detector import Platform

from .base import ExecutionConfig, LegExecution


class LegRiskAction(Enum):
    """Actions taken in response to leg risk."""
    CANCELED = "canceled"
    HEDGED = "hedged"
    TIMED_OUT = "timed_out"
    MANUAL = "manual"


@dataclass
class LegRiskEvent:
    """Record of a leg risk incident.

    Attributes:
        timestamp: When the leg risk was detected.
        filled_leg: The leg that was filled.
        unfilled_leg: The leg that was not filled.
        action_taken: What action was taken to resolve.
        resolution_price: Price at which position was resolved (if hedged).
        slippage: Difference between target and resolution price.
        pnl_impact: Estimated P&L impact from the leg risk.
    """
    timestamp: datetime
    filled_leg: LegExecution
    unfilled_leg: LegExecution
    action_taken: LegRiskAction
    resolution_price: Optional[float] = None
    slippage: float = 0.0
    pnl_impact: float = 0.0

    @property
    def filled_platform(self) -> Platform:
        """Platform where the fill occurred."""
        return self.filled_leg.platform

    @property
    def unfilled_platform(self) -> Platform:
        """Platform where no fill occurred."""
        return self.unfilled_leg.platform


class LegRiskManager:
    """Manages leg risk situations during arbitrage execution.

    Provides methods for handling partial fills and tracking leg risk events.
    """

    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        on_leg_risk: Optional[Callable[[LegRiskEvent], None]] = None,
    ):
        """Initialize the leg risk manager.

        Args:
            config: Execution configuration.
            on_leg_risk: Optional callback for leg risk events.
        """
        self.config = config or ExecutionConfig()
        self.on_leg_risk = on_leg_risk
        self._events: List[LegRiskEvent] = []

    @property
    def events(self) -> List[LegRiskEvent]:
        """Get all leg risk events."""
        return self._events.copy()

    @property
    def total_events(self) -> int:
        """Total number of leg risk events."""
        return len(self._events)

    @property
    def total_pnl_impact(self) -> float:
        """Total P&L impact from all leg risk events."""
        return sum(e.pnl_impact for e in self._events)

    def handle_leg_risk(
        self,
        filled_leg: LegExecution,
        unfilled_leg: LegExecution,
        unfilled_market: TradableMarket,
        hedge: bool = False,
    ) -> LegRiskEvent:
        """Handle a leg risk situation.

        Args:
            filled_leg: The leg that was filled.
            unfilled_leg: The leg that was not filled.
            unfilled_market: TradableMarket for the unfilled leg.
            hedge: If True, hedge on same market instead of canceling.

        Returns:
            LegRiskEvent describing what happened.
        """
        if hedge:
            event = self._hedge_position(
                filled_leg, unfilled_leg, unfilled_market
            )
        else:
            event = self._cancel_unfilled(
                filled_leg, unfilled_leg, unfilled_market
            )

        self._events.append(event)

        if self.on_leg_risk:
            self.on_leg_risk(event)

        return event

    def _cancel_unfilled(
        self,
        filled_leg: LegExecution,
        unfilled_leg: LegExecution,
        unfilled_market: TradableMarket,
    ) -> LegRiskEvent:
        """Cancel the unfilled leg order."""
        action = LegRiskAction.CANCELED

        if unfilled_leg.order and unfilled_leg.order.is_active:
            try:
                unfilled_market.cancel_order(unfilled_leg.order.order_id)
                unfilled_leg.status = "canceled"
            except Exception:
                action = LegRiskAction.MANUAL

        # Calculate P&L impact: we have a directional position now
        # This is the "opportunity cost" - we bought/sold but couldn't complete arb
        pnl_impact = self._estimate_pnl_impact(filled_leg, unfilled_leg)

        return LegRiskEvent(
            timestamp=datetime.now(),
            filled_leg=filled_leg,
            unfilled_leg=unfilled_leg,
            action_taken=action,
            pnl_impact=pnl_impact,
        )

    def _hedge_position(
        self,
        filled_leg: LegExecution,
        unfilled_leg: LegExecution,
        unfilled_market: TradableMarket,
    ) -> LegRiskEvent:
        """Hedge the filled position on the unfilled market's platform.

        This places an opposite order to flatten the position:
        - If we bought YES, sell YES on the same market
        - If we sold YES, buy YES on the same market

        Note: This is a simplified implementation. Real hedging would need
        to consider the actual market where we have the position.
        """
        action = LegRiskAction.HEDGED
        resolution_price = None
        slippage = 0.0

        try:
            # Cancel the original unfilled order first
            if unfilled_leg.order and unfilled_leg.order.is_active:
                unfilled_market.cancel_order(unfilled_leg.order.order_id)

            # Place opposite order to hedge
            ob = unfilled_market.get_orderbook()
            size = filled_leg.filled_size

            if unfilled_leg.side == "buy":
                # We needed to buy but couldn't. We have a short position.
                # Buy aggressively to flatten.
                price = min(99, (ob.best_ask or 50) + 3)
                unfilled_market.buy(price, size)
                resolution_price = price
            else:
                # We needed to sell but couldn't. We have a long position.
                # Sell aggressively to flatten.
                price = max(1, (ob.best_bid or 50) - 3)
                unfilled_market.sell(price, size)
                resolution_price = price

            slippage = abs(resolution_price - unfilled_leg.target_price)

        except Exception:
            action = LegRiskAction.MANUAL

        pnl_impact = self._estimate_pnl_impact(
            filled_leg, unfilled_leg, resolution_price
        )

        return LegRiskEvent(
            timestamp=datetime.now(),
            filled_leg=filled_leg,
            unfilled_leg=unfilled_leg,
            action_taken=action,
            resolution_price=resolution_price,
            slippage=slippage,
            pnl_impact=pnl_impact,
        )

    def _estimate_pnl_impact(
        self,
        filled_leg: LegExecution,
        unfilled_leg: LegExecution,
        resolution_price: Optional[float] = None,
    ) -> float:
        """Estimate P&L impact from leg risk.

        When we have leg risk:
        - If we bought (filled) but couldn't sell: we're long, exposed to downside
        - If we sold (filled) but couldn't buy: we're short, exposed to upside

        P&L impact is estimated as the difference between what we expected
        and what we got.
        """
        filled_price = filled_leg.actual_price or filled_leg.target_price
        target_unfilled_price = unfilled_leg.target_price
        contracts = filled_leg.filled_size

        if resolution_price is not None:
            # We hedged - calculate actual slippage
            if filled_leg.side == "buy":
                # We bought at filled_price, expected to sell at target_unfilled_price
                # Instead sold at resolution_price
                expected_edge = target_unfilled_price - filled_price
                actual_edge = resolution_price - filled_price
                return (actual_edge - expected_edge) * contracts
            else:
                # We sold at filled_price, expected to buy at target_unfilled_price
                # Instead bought at resolution_price
                expected_edge = filled_price - target_unfilled_price
                actual_edge = filled_price - resolution_price
                return (actual_edge - expected_edge) * contracts
        else:
            # We canceled - estimate potential loss as half the edge
            # (conservative estimate of directional exposure)
            expected_edge = abs(target_unfilled_price - filled_price)
            return -expected_edge * contracts * 0.5

    def clear_events(self) -> None:
        """Clear all recorded leg risk events."""
        self._events.clear()

    def get_events_by_platform(self, platform: Platform) -> List[LegRiskEvent]:
        """Get leg risk events for a specific platform."""
        return [
            e for e in self._events
            if e.filled_platform == platform or e.unfilled_platform == platform
        ]

    def get_summary(self) -> dict:
        """Get summary statistics for leg risk events."""
        if not self._events:
            return {
                "total_events": 0,
                "total_pnl_impact": 0.0,
                "events_by_action": {},
                "avg_slippage": 0.0,
            }

        events_by_action = {}
        for event in self._events:
            action = event.action_taken.value
            events_by_action[action] = events_by_action.get(action, 0) + 1

        slippages = [e.slippage for e in self._events if e.slippage > 0]
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0.0

        return {
            "total_events": len(self._events),
            "total_pnl_impact": self.total_pnl_impact,
            "events_by_action": events_by_action,
            "avg_slippage": avg_slippage,
        }
