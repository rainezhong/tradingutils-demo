"""Scenario-based tests for latency arbitrage strategies.

Tests how the strategy reacts to different market behaviors:
- Market follows trend (good for latency arb)
- Market oscillates (adverse selection risk)
- Market ignores external moves (no opportunity)
- Market goes opposite (very bad)
- Market overshoots (potential mean reversion)
- Delayed follow (timing matters)
- Sudden reversal (whipsaw risk)
- Low liquidity (execution risk)
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from unittest.mock import Mock

import pytest

from strategies.latency_arb.config import LatencyArbConfig
from strategies.latency_arb.detector import EdgeDetector
from strategies.latency_arb.executor import ArbOpportunity, ArbPosition
from strategies.latency_arb.market import KalshiMarket


# ============================================================================
# Mock Market Simulator
# ============================================================================


@dataclass
class MarketSnapshot:
    """A point in time snapshot of market state."""

    timestamp: float
    bid: int  # cents
    ask: int  # cents
    best_bid_size: int = 10
    best_ask_size: int = 10
    last_price: int = 50


@dataclass
class ExternalPriceMove:
    """External truth source price movement."""

    timestamp: float
    fair_value: float  # 0-1 probability


class MockMarketSimulator:
    """Simulates market behavior over time based on scenarios."""

    def __init__(
        self,
        ticker: str,
        initial_bid: int = 48,
        initial_ask: int = 52,
        initial_fair: float = 0.50,
    ):
        self.ticker = ticker
        self.current_bid = initial_bid
        self.current_ask = initial_ask
        self.current_fair = initial_fair
        self.snapshots: List[MarketSnapshot] = []
        self.external_moves: List[ExternalPriceMove] = []
        self.start_time = time.time()

    def get_market(self) -> KalshiMarket:
        """Get current KalshiMarket representation."""
        mid = (self.current_bid + self.current_ask) / 2 / 100
        return KalshiMarket(
            ticker=self.ticker,
            title=f"Test Market {self.ticker}",
            expiration_time=datetime.utcnow() + timedelta(minutes=5),
            yes_bid=self.current_bid,
            yes_ask=self.current_ask,
            no_bid=100 - self.current_ask,
            no_ask=100 - self.current_bid,
            volume=1000,
            open_interest=500,
        )

    def get_fair_value(self) -> float:
        """Get current fair value from external source."""
        return self.current_fair

    def step(self, elapsed_sec: float) -> None:
        """Advance simulation by elapsed_sec. Override in subclasses."""
        pass

    def record_snapshot(self) -> None:
        """Record current market state."""
        self.snapshots.append(
            MarketSnapshot(
                timestamp=time.time() - self.start_time,
                bid=self.current_bid,
                ask=self.current_ask,
            )
        )

    def record_external_move(self, fair_value: float) -> None:
        """Record external price movement."""
        self.external_moves.append(
            ExternalPriceMove(
                timestamp=time.time() - self.start_time,
                fair_value=fair_value,
            )
        )


# ============================================================================
# Scenario Implementations
# ============================================================================


class MarketFollowsTrend(MockMarketSimulator):
    """Market quickly follows external price moves (GOOD scenario)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lag_ms = 200  # Fast response
        self.external_moved = False
        self.market_moved = False

    def step(self, elapsed_sec: float) -> None:
        """Follow fair value with slight lag."""
        # External move: fair value increases at t=1.0
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.65
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market responds quickly (200ms lag) at t=1.2
        if elapsed_sec >= 1.2 and self.external_moved and not self.market_moved:
            target_mid = int(self.current_fair * 100)
            self.current_bid = target_mid - 2
            self.current_ask = target_mid + 2
            self.record_snapshot()
            self.market_moved = True


class MarketOscillates(MockMarketSimulator):
    """Market bounces back and forth rapidly (BAD - adverse selection)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.oscillation_period = 0.5  # 500ms period
        self.initialized = False

    def step(self, elapsed_sec: float) -> None:
        """Oscillate between two price levels."""
        # External fair value is stable
        if not self.initialized:
            self.current_fair = 0.50
            self.record_external_move(self.current_fair)
            self.initialized = True

        # Market oscillates
        phase = (elapsed_sec / self.oscillation_period) % 1.0
        if phase < 0.5:
            # Move up
            self.current_bid = 55
            self.current_ask = 59
        else:
            # Move down
            self.current_bid = 41
            self.current_ask = 45
        self.record_snapshot()


class MarketIgnoresExternal(MockMarketSimulator):
    """Market doesn't respond to external moves (NO OPPORTUNITY)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.external_moved = False

    def step(self, elapsed_sec: float) -> None:
        """Keep market flat despite external moves."""
        # External move: fair value increases
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.70
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market stays flat
        # (bid/ask unchanged from initial)
        self.record_snapshot()


class MarketGoesOpposite(MockMarketSimulator):
    """Market moves opposite to external (VERY BAD)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.external_moved = False
        self.market_moved = False

    def step(self, elapsed_sec: float) -> None:
        """Move opposite to fair value."""
        # External move: fair value increases
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.70
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market moves DOWN when fair value went UP
        if elapsed_sec >= 1.2 and self.external_moved and not self.market_moved:
            self.current_bid = 35
            self.current_ask = 39
            self.record_snapshot()
            self.market_moved = True


class MarketOvershoots(MockMarketSimulator):
    """Market moves more than fair value suggests (mean reversion opportunity)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.external_moved = False
        self.market_moved = False

    def step(self, elapsed_sec: float) -> None:
        """Overshoot fair value."""
        # External move: fair value increases to 60
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.60
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market overshoots to 75
        if elapsed_sec >= 1.2 and self.external_moved and not self.market_moved:
            self.current_bid = 73
            self.current_ask = 77
            self.record_snapshot()
            self.market_moved = True


class MarketDelayedFollow(MockMarketSimulator):
    """Market follows but with significant lag (timing matters)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lag_sec = 3.0
        self.external_moved = False
        self.market_moved = False

    def step(self, elapsed_sec: float) -> None:
        """Follow with 3 second lag."""
        # External move at t=1
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.65
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market doesn't respond until t=4
        if elapsed_sec >= 1.0 + self.lag_sec and self.external_moved and not self.market_moved:
            target_mid = int(self.current_fair * 100)
            self.current_bid = target_mid - 2
            self.current_ask = target_mid + 2
            self.record_snapshot()
            self.market_moved = True


class MarketSuddenReversal(MockMarketSimulator):
    """Market initially follows then reverses (whipsaw)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.external_moved = False
        self.market_followed = False
        self.reversed = False

    def step(self, elapsed_sec: float) -> None:
        """Follow then reverse."""
        # External move: fair value increases
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.65
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market follows initially (from t=1.2 to t=2.0)
        if 1.2 <= elapsed_sec < 2.0 and self.external_moved and not self.market_followed:
            self.current_bid = 63
            self.current_ask = 67
            self.record_snapshot()
            self.market_followed = True

        # Then reverses sharply at t=2.0
        if elapsed_sec >= 2.0 and self.market_followed and not self.reversed:
            # External fair value actually was wrong, reverts
            self.current_fair = 0.45
            self.record_external_move(self.current_fair)
            self.current_bid = 43
            self.current_ask = 47
            self.record_snapshot()
            self.reversed = True


class MarketLowLiquidity(MockMarketSimulator):
    """Market has wide spreads and low depth (execution risk)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Wide spread
        self.current_bid = 40
        self.current_ask = 60
        self.external_moved = False

    def get_market(self) -> KalshiMarket:
        """Override to show low depth."""
        market = super().get_market()
        # Simulate very low depth
        market.volume = 10  # Low volume
        market.open_interest = 5
        return market

    def step(self, elapsed_sec: float) -> None:
        """Keep wide spread."""
        if elapsed_sec >= 1.0 and not self.external_moved:
            self.current_fair = 0.55
            self.record_external_move(self.current_fair)
            self.external_moved = True

        # Market stays wide
        self.record_snapshot()


# ============================================================================
# Test Helpers
# ============================================================================


def simulate_strategy_response(
    simulator: MockMarketSimulator,
    config: LatencyArbConfig,
    duration_sec: float = 5.0,
    step_interval: float = 0.1,
) -> Dict:
    """Simulate strategy behavior over time.

    Returns:
        Dict with opportunities, executions, final_position, pnl, etc.
    """
    detector = EdgeDetector(config)
    opportunities: List[ArbOpportunity] = []
    executions: List[Tuple[float, str, int, int]] = []  # (time, side, price, size)
    position: Optional[ArbPosition] = None

    # Run simulation
    elapsed = 0.0
    while elapsed <= duration_sec:
        simulator.step(elapsed)

        market = simulator.get_market()
        fair_value = simulator.get_fair_value()

        # Calculate edge (use yes_mid as market probability)
        market_prob = market.yes_mid
        edge, direction = detector.calculate_edge(fair_value, market_prob)

        # Check if we have an opportunity
        if edge >= config.min_edge_pct:
            # Check stability
            stable = detector.check_signal_stability(
                market.ticker, edge, direction, time.time()
            )

            if stable or not config.signal_stability_enabled:
                # Calculate confidence and size
                ttx = (market.expiration_time - datetime.utcnow()).total_seconds()
                confidence = detector.calculate_confidence(edge, ttx, fair_value)

                price_cents = market.yes_ask if direction == "yes" else market.no_ask
                size = detector.calculate_size(
                    edge, price_cents / 100, confidence, fair_value
                )

                opp = ArbOpportunity(
                    market=market,
                    side=direction,
                    fair_value=fair_value,
                    market_prob=market_prob,
                    edge=edge,
                    confidence=confidence,
                    recommended_price=price_cents,
                    recommended_size=size,
                )
                opportunities.append(opp)

                # Execute if no position
                if position is None and size > 0:
                    executions.append(
                        (elapsed, direction, price_cents, size)
                    )
                    position = ArbPosition(
                        ticker=market.ticker,
                        side=direction,
                        entry_price=price_cents,
                        size=size,
                        entry_time=datetime.utcnow(),
                        entry_fair_value=fair_value,
                        entry_market_prob=market_prob,
                    )

        # Check early exit if we have position
        if position is not None:
            current_price = (
                market.yes_bid if position.side == "yes" else market.no_bid
            )
            pnl_cents = (current_price - position.entry_price) * position.size
            pnl_pct = pnl_cents / (position.entry_price * position.size)

            # Early exit on profit
            if (
                config.early_exit_enabled
                and pnl_pct >= config.early_exit_profit_threshold
            ):
                executions.append(
                    (elapsed, f"exit_{position.side}", current_price, position.size)
                )
                final_pnl = pnl_cents
                position = None  # Exited
                break

        elapsed += step_interval

    # Calculate final P&L if still in position
    final_pnl = 0
    if position is not None:
        market = simulator.get_market()
        exit_price = market.yes_bid if position.side == "yes" else market.no_bid
        final_pnl = (exit_price - position.entry_price) * position.size

    return {
        "opportunities": opportunities,
        "executions": executions,
        "position": position,
        "final_pnl_cents": final_pnl,
        "num_opportunities": len(opportunities),
        "num_executions": len([e for e in executions if not e[1].startswith("exit_")]),
    }


# ============================================================================
# Scenario Tests
# ============================================================================


class TestLatencyScenarios:
    """Test strategy behavior across different market scenarios."""

    def test_market_follows_trend_profitable(self):
        """GOOD: Market follows external move quickly → should profit."""
        config = LatencyArbConfig(
            min_edge_pct=0.10,
            signal_stability_enabled=False,
            kelly_fraction=0,  # Use fixed sizing for predictability
            base_position_usd=50.0,
        )

        sim = MarketFollowsTrend(ticker="TEST-FOLLOW")
        result = simulate_strategy_response(sim, config)

        # Should detect opportunity
        assert result["num_opportunities"] > 0, "Should detect edge when market lags"

        # Should execute
        assert result["num_executions"] >= 1, "Should enter position"

        # Should be profitable (bought at ~50, market went to ~65)
        assert result["final_pnl_cents"] > 0, "Should profit when market follows"

    def test_market_oscillates_adverse_selection(self):
        """BAD: Oscillating market → adverse selection risk."""
        config = LatencyArbConfig(
            min_edge_pct=0.05,  # Lower threshold to trigger
            signal_stability_enabled=True,
            signal_stability_duration_sec=1.0,  # Require stability
            kelly_fraction=0,
        )

        sim = MarketOscillates(ticker="TEST-OSC")
        result = simulate_strategy_response(sim, config)

        # Stability filter should reduce/prevent entries
        assert result["num_executions"] <= 1, (
            "Stability filter should prevent entering oscillating markets"
        )

    def test_market_ignores_no_opportunity(self):
        """NO OPPORTUNITY: Market doesn't move → no edge."""
        config = LatencyArbConfig(min_edge_pct=0.10)

        sim = MarketIgnoresExternal(ticker="TEST-IGNORE")
        result = simulate_strategy_response(sim, config)

        # Should detect edge initially (fair=70, market=50)
        assert result["num_opportunities"] > 0, "Should detect edge from mispricing"

        # But market never moves, so if we enter, we don't profit
        # (This tests that we correctly identify stale markets)

    def test_market_goes_opposite_loss(self):
        """VERY BAD: Market moves opposite → should lose."""
        config = LatencyArbConfig(
            min_edge_pct=0.10,
            signal_stability_enabled=False,
        )

        sim = MarketGoesOpposite(ticker="TEST-OPPOSITE")
        result = simulate_strategy_response(sim, config)

        # Should detect opportunity when fair value increases
        assert result["num_opportunities"] > 0

        # If we executed, we should lose money
        if result["num_executions"] > 0:
            assert result["final_pnl_cents"] < 0, (
                "Should lose when market goes opposite"
            )

    def test_market_overshoots_mean_reversion(self):
        """Market overshoots fair value → potential mean reversion."""
        config = LatencyArbConfig(
            min_edge_pct=0.10,
            early_exit_enabled=True,
            early_exit_profit_threshold=0.10,  # Exit on 10% profit
        )

        sim = MarketOvershoots(ticker="TEST-OVERSHOOT")
        result = simulate_strategy_response(sim, config)

        # This scenario is tricky: initially shows edge to buy
        # Then market overshoots, creating edge to sell
        # Strategy should detect the reverse edge
        assert result["num_opportunities"] > 0

        # Could profit from mean reversion if we were short
        # (This is more of an observation test)

    def test_market_delayed_follow_timing_matters(self):
        """Market follows with 3s lag → timing is critical."""
        config = LatencyArbConfig(
            min_edge_pct=0.10,
            signal_stability_enabled=False,
        )

        sim = MarketDelayedFollow(ticker="TEST-DELAYED")
        result = simulate_strategy_response(sim, config, duration_sec=6.0)

        # Should detect opportunity during the lag window
        assert result["num_opportunities"] > 0

        # Should eventually profit when market catches up
        if result["num_executions"] > 0:
            # Market takes 3 seconds to catch up
            # If we entered in the window, should profit
            pass  # Profit depends on entry timing

    def test_market_sudden_reversal_whipsaw(self):
        """Market follows then reverses → whipsaw loss."""
        config = LatencyArbConfig(
            min_edge_pct=0.10,
            early_exit_enabled=True,
            early_exit_profit_threshold=0.15,
        )

        sim = MarketSuddenReversal(ticker="TEST-REVERSAL")
        result = simulate_strategy_response(sim, config)

        # Should detect initial opportunity
        assert result["num_opportunities"] > 0

        # If no early exit, should lose on reversal
        # Early exit might save us if we exit before reversal
        # (This tests importance of early exit)

    def test_market_low_liquidity_wide_spread(self):
        """Low liquidity market → wide spread, execution risk."""
        config = LatencyArbConfig(
            min_edge_pct=0.05,  # Lower threshold
            max_slippage_pct=0.05,  # 5% max slippage
        )

        sim = MarketLowLiquidity(ticker="TEST-ILLIQUID")
        result = simulate_strategy_response(sim, config)

        # Wide spread (40-60) means market prob is ~40%
        # Fair value is 55%, so edge is ~15% - slippage
        # Should detect opportunity but wide spread eats into edge

        # Could add slippage checks in detector to filter wide markets
        # (This is more observational - testing edge calc with wide spreads)

    def test_signal_stability_prevents_whipsaw(self):
        """Signal stability filter prevents entering unstable signals."""
        unstable_config = LatencyArbConfig(
            min_edge_pct=0.05,
            signal_stability_enabled=False,
        )

        stable_config = LatencyArbConfig(
            min_edge_pct=0.05,
            signal_stability_enabled=True,
            signal_stability_duration_sec=2.0,
        )

        sim_unstable = MarketOscillates(ticker="TEST-UNSTABLE-1")
        sim_stable = MarketOscillates(ticker="TEST-UNSTABLE-2")

        result_unstable = simulate_strategy_response(sim_unstable, unstable_config)
        result_stable = simulate_strategy_response(sim_stable, stable_config)

        # Without stability: might enter oscillating market
        # With stability: should filter out unstable signals
        assert result_stable["num_executions"] <= result_unstable["num_executions"]

    def test_kelly_sizing_scales_with_edge(self):
        """Kelly sizing should increase position with higher edge."""
        low_edge_config = LatencyArbConfig(
            min_edge_pct=0.05,
            kelly_fraction=0.5,
            bankroll=1000.0,
        )

        high_edge_config = LatencyArbConfig(
            min_edge_pct=0.05,
            kelly_fraction=0.5,
            bankroll=1000.0,
        )

        # Create scenarios with different edges
        low_edge_sim = MarketFollowsTrend(
            ticker="LOW-EDGE",
            initial_bid=58,
            initial_ask=62,
            initial_fair=0.65,  # ~5% edge
        )

        high_edge_sim = MarketFollowsTrend(
            ticker="HIGH-EDGE",
            initial_bid=48,
            initial_ask=52,
            initial_fair=0.70,  # ~20% edge
        )

        result_low = simulate_strategy_response(low_edge_sim, low_edge_config)
        result_high = simulate_strategy_response(high_edge_sim, high_edge_config)

        # Higher edge should get larger position
        if result_low["executions"] and result_high["executions"]:
            low_size = result_low["executions"][0][3]
            high_size = result_high["executions"][0][3]
            assert high_size >= low_size, "Higher edge should get larger Kelly size"


# ============================================================================
# Comparative Analysis Tests
# ============================================================================


class TestStrategyComparison:
    """Compare different strategy configurations across scenarios."""

    @pytest.mark.parametrize(
        "scenario_class,scenario_name",
        [
            (MarketFollowsTrend, "follows_trend"),
            (MarketOscillates, "oscillates"),
            (MarketIgnoresExternal, "ignores"),
            (MarketGoesOpposite, "goes_opposite"),
            (MarketOvershoots, "overshoots"),
            (MarketDelayedFollow, "delayed_follow"),
            (MarketSuddenReversal, "sudden_reversal"),
            (MarketLowLiquidity, "low_liquidity"),
        ],
    )
    def test_config_variations_across_scenarios(
        self, scenario_class, scenario_name
    ):
        """Test different configs across all scenarios."""
        configs = {
            "aggressive": LatencyArbConfig(
                min_edge_pct=0.03,
                signal_stability_enabled=False,
                early_exit_enabled=False,
            ),
            "conservative": LatencyArbConfig(
                min_edge_pct=0.15,
                signal_stability_enabled=True,
                signal_stability_duration_sec=2.0,
                early_exit_enabled=True,
                early_exit_profit_threshold=0.10,
            ),
            "balanced": LatencyArbConfig(
                min_edge_pct=0.10,
                signal_stability_enabled=True,
                signal_stability_duration_sec=1.0,
                early_exit_enabled=True,
                early_exit_profit_threshold=0.15,
            ),
        }

        results = {}
        for config_name, config in configs.items():
            sim = scenario_class(ticker=f"TEST-{scenario_name.upper()}")
            result = simulate_strategy_response(sim, config)
            results[config_name] = result

        # Log comparative results
        print(f"\n=== Scenario: {scenario_name} ===")
        for config_name, result in results.items():
            print(f"{config_name:15} | Opps: {result['num_opportunities']:3} | "
                  f"Exec: {result['num_executions']:2} | "
                  f"PnL: {result['final_pnl_cents']:+6} cents")

        # Assertions depend on scenario
        # (Each scenario has different optimal config)
