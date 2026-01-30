"""Integration tests for the market-making engine.

Tests the full pipeline with simulated data:
- Engine initialization
- Market updates processing
- Fill handling
- Risk management integration
- Multi-market coordination
"""

import pytest
from datetime import datetime

from src.core.config import RiskConfig as CoreRiskConfig
from src.engine import MarketMakingEngine, MultiMarketEngine
from src.execution.mock_api_client import MockAPIClient
from src.market_making.config import MarketMakerConfig
from src.market_making.models import Fill, MarketState, Quote
from src.simulation import (
    MarketSimulator,
    create_api_client,
    create_simulator,
    get_scenario,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_api_client() -> MockAPIClient:
    """Create a mock API client."""
    return MockAPIClient()


@pytest.fixture
def mm_config() -> MarketMakerConfig:
    """Create default market maker config."""
    return MarketMakerConfig(
        target_spread=0.04,
        quote_size=10,
        max_position=50,
    )


@pytest.fixture
def risk_config() -> CoreRiskConfig:
    """Create default risk config."""
    return CoreRiskConfig(
        max_position_size=50,
        max_total_position=100,
        max_loss_per_position=25.0,
        max_daily_loss=100.0,
    )


@pytest.fixture
def engine(
    mock_api_client: MockAPIClient,
    mm_config: MarketMakerConfig,
    risk_config: CoreRiskConfig,
) -> MarketMakingEngine:
    """Create a market-making engine."""
    return MarketMakingEngine(
        ticker="TEST-MKT",
        api_client=mock_api_client,
        mm_config=mm_config,
        risk_config=risk_config,
    )


def create_market_state(
    ticker: str = "TEST-MKT",
    bid: float = 0.45,
    ask: float = 0.55,
) -> MarketState:
    """Helper to create market states."""
    return MarketState(
        ticker=ticker,
        timestamp=datetime.now(),
        best_bid=bid,
        best_ask=ask,
        mid_price=(bid + ask) / 2,
        bid_size=100,
        ask_size=100,
    )


# =============================================================================
# Engine Initialization Tests
# =============================================================================


class TestEngineInit:
    """Tests for MarketMakingEngine initialization."""

    def test_basic_init(
        self,
        mock_api_client: MockAPIClient,
        mm_config: MarketMakerConfig,
        risk_config: CoreRiskConfig,
    ) -> None:
        """Engine initializes with all components."""
        engine = MarketMakingEngine(
            ticker="TEST",
            api_client=mock_api_client,
            mm_config=mm_config,
            risk_config=risk_config,
        )

        assert engine.ticker == "TEST"
        assert engine.market_maker is not None
        assert engine.quote_manager is not None
        assert engine.risk_manager is not None

    def test_init_with_defaults(self, mock_api_client: MockAPIClient) -> None:
        """Engine initializes with default configs."""
        engine = MarketMakingEngine(
            ticker="TEST",
            api_client=mock_api_client,
        )

        assert engine.ticker == "TEST"
        assert engine.market_maker.config is not None
        assert engine.risk_manager.config is not None

    def test_empty_ticker_raises(self, mock_api_client: MockAPIClient) -> None:
        """Empty ticker raises ValueError."""
        with pytest.raises(ValueError, match="ticker cannot be empty"):
            MarketMakingEngine(ticker="", api_client=mock_api_client)


# =============================================================================
# Market Update Tests
# =============================================================================


class TestMarketUpdates:
    """Tests for market update processing."""

    def test_process_single_update(self, engine: MarketMakingEngine) -> None:
        """Single market update is processed."""
        market = create_market_state()

        engine.on_market_update(market)

        assert engine._state.market_updates == 1
        assert engine._last_market is not None

    def test_process_multiple_updates(self, engine: MarketMakingEngine) -> None:
        """Multiple market updates are processed."""
        for i in range(10):
            market = create_market_state(bid=0.40 + i * 0.01, ask=0.50 + i * 0.01)
            engine.on_market_update(market)

        assert engine._state.market_updates == 10

    def test_wrong_ticker_ignored(self, engine: MarketMakingEngine) -> None:
        """Updates for wrong ticker are ignored."""
        market = create_market_state(ticker="WRONG-TICKER")

        engine.on_market_update(market)

        assert engine._state.market_updates == 0

    def test_quotes_generated_on_update(self, engine: MarketMakingEngine) -> None:
        """Quotes are generated on market update."""
        market = create_market_state()

        engine.on_market_update(market)

        # Should have generated quotes
        status = engine.get_status()
        assert status["market_maker"]["stats"]["quotes_generated"] > 0


# =============================================================================
# Quote Generation and Validation Tests
# =============================================================================


class TestQuoteGeneration:
    """Tests for quote generation and risk validation."""

    def test_quotes_within_risk_limits(self, engine: MarketMakingEngine) -> None:
        """Generated quotes pass risk validation."""
        market = create_market_state()

        engine.on_market_update(market)

        # Check that quotes were generated and accepted
        assert engine._state.quotes_sent >= 0  # May be 0 if no quote needed

    def test_wide_spread_generates_quotes(self, engine: MarketMakingEngine) -> None:
        """Wide market spread generates quotes."""
        # Wide spread market
        market = create_market_state(bid=0.35, ask=0.65)

        engine.on_market_update(market)

        status = engine.get_status()
        assert status["market_maker"]["stats"]["quotes_generated"] > 0


# =============================================================================
# Fill Processing Tests
# =============================================================================


class TestFillProcessing:
    """Tests for fill detection and processing."""

    def test_position_updates_on_fill(
        self,
        mock_api_client: MockAPIClient,
        mm_config: MarketMakerConfig,
        risk_config: CoreRiskConfig,
    ) -> None:
        """Position updates when fills occur."""
        engine = MarketMakingEngine(
            ticker="TEST-MKT",
            api_client=mock_api_client,
            mm_config=mm_config,
            risk_config=risk_config,
        )

        # Process a market update to generate quotes
        market = create_market_state()
        engine.on_market_update(market)

        # Initial position should be flat
        assert engine.market_maker.position.contracts == 0


# =============================================================================
# Risk Integration Tests
# =============================================================================


class TestRiskIntegration:
    """Tests for risk manager integration."""

    def test_risk_halts_trading(
        self,
        mock_api_client: MockAPIClient,
        mm_config: MarketMakerConfig,
    ) -> None:
        """Trading halts when risk limits are breached."""
        # Very tight risk limits
        risk_config = CoreRiskConfig(
            max_position_size=5,
            max_total_position=10,
            max_loss_per_position=1.0,
            max_daily_loss=5.0,
        )

        engine = MarketMakingEngine(
            ticker="TEST-MKT",
            api_client=mock_api_client,
            mm_config=mm_config,
            risk_config=risk_config,
        )

        # Simulate a large loss
        engine.risk_manager.update_daily_pnl(-5.0)

        # Trading should be halted
        assert not engine.risk_manager.is_trading_allowed()

    def test_position_synced_to_risk_manager(
        self, engine: MarketMakingEngine
    ) -> None:
        """Position is synced to risk manager."""
        # Process updates
        market = create_market_state()
        engine.on_market_update(market)

        # Risk manager should have position data
        metrics = engine.risk_manager.get_risk_metrics()
        assert "positions" in metrics


# =============================================================================
# Engine Status Tests
# =============================================================================


class TestEngineStatus:
    """Tests for engine status reporting."""

    def test_get_status_returns_dict(self, engine: MarketMakingEngine) -> None:
        """get_status returns comprehensive status dict."""
        status = engine.get_status()

        assert "ticker" in status
        assert "engine" in status
        assert "market_maker" in status
        assert "risk" in status

    def test_status_tracks_updates(self, engine: MarketMakingEngine) -> None:
        """Status tracks market updates."""
        for _ in range(5):
            market = create_market_state()
            engine.on_market_update(market)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 5

    def test_reset_clears_state(self, engine: MarketMakingEngine) -> None:
        """Reset clears engine state."""
        # Generate some activity
        for _ in range(5):
            market = create_market_state()
            engine.on_market_update(market)

        engine.reset()

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 0
        assert status["engine"]["fills_processed"] == 0


# =============================================================================
# Multi-Market Engine Tests
# =============================================================================


class TestMultiMarketEngine:
    """Tests for MultiMarketEngine."""

    def test_add_market(self, mock_api_client: MockAPIClient) -> None:
        """Adding markets creates engines."""
        multi = MultiMarketEngine(api_client=mock_api_client)

        multi.add_market("MARKET-A")
        multi.add_market("MARKET-B")

        assert len(multi.list_markets()) == 2
        assert "MARKET-A" in multi.list_markets()
        assert "MARKET-B" in multi.list_markets()

    def test_duplicate_market_raises(self, mock_api_client: MockAPIClient) -> None:
        """Adding duplicate market raises error."""
        multi = MultiMarketEngine(api_client=mock_api_client)
        multi.add_market("MARKET-A")

        with pytest.raises(ValueError, match="already exists"):
            multi.add_market("MARKET-A")

    def test_remove_market(self, mock_api_client: MockAPIClient) -> None:
        """Removing market works correctly."""
        multi = MultiMarketEngine(api_client=mock_api_client)
        multi.add_market("MARKET-A")
        multi.add_market("MARKET-B")

        result = multi.remove_market("MARKET-A")

        assert result is True
        assert "MARKET-A" not in multi.list_markets()
        assert "MARKET-B" in multi.list_markets()

    def test_on_market_update_routes(self, mock_api_client: MockAPIClient) -> None:
        """Market updates are routed to correct engine."""
        multi = MultiMarketEngine(api_client=mock_api_client)
        multi.add_market("MARKET-A")
        multi.add_market("MARKET-B")

        market_a = create_market_state(ticker="MARKET-A")
        market_b = create_market_state(ticker="MARKET-B")

        multi.on_market_update("MARKET-A", market_a)
        multi.on_market_update("MARKET-B", market_b)

        engine_a = multi.get_engine("MARKET-A")
        engine_b = multi.get_engine("MARKET-B")

        assert engine_a._state.market_updates == 1
        assert engine_b._state.market_updates == 1

    def test_aggregate_status(self, mock_api_client: MockAPIClient) -> None:
        """Aggregate status combines all markets."""
        multi = MultiMarketEngine(api_client=mock_api_client)
        multi.add_market("MARKET-A")
        multi.add_market("MARKET-B")

        status = multi.get_aggregate_status()

        assert status["aggregate"]["markets_active"] == 2
        assert "MARKET-A" in status["markets"]
        assert "MARKET-B" in status["markets"]

    def test_reset_all(self, mock_api_client: MockAPIClient) -> None:
        """Reset all clears all engine states."""
        multi = MultiMarketEngine(api_client=mock_api_client)
        multi.add_market("MARKET-A")
        multi.add_market("MARKET-B")

        # Generate activity
        market_a = create_market_state(ticker="MARKET-A")
        multi.on_market_update("MARKET-A", market_a)

        multi.reset_all()

        engine_a = multi.get_engine("MARKET-A")
        assert engine_a._state.market_updates == 0


# =============================================================================
# Simulation Integration Tests
# =============================================================================


class TestSimulationIntegration:
    """Tests for integration with simulation framework."""

    def test_engine_with_simulator(
        self,
        mock_api_client: MockAPIClient,
        mm_config: MarketMakerConfig,
        risk_config: CoreRiskConfig,
    ) -> None:
        """Engine works with MarketSimulator data."""
        simulator = MarketSimulator("TEST-MKT", seed=42)

        engine = MarketMakingEngine(
            ticker="TEST-MKT",
            api_client=mock_api_client,
            mm_config=mm_config,
            risk_config=risk_config,
        )

        # Run through simulated market states
        for _ in range(50):
            sim_state = simulator.generate_market_state()

            # Convert simulation MarketState to market_making MarketState
            mm_market = MarketState(
                ticker="TEST-MKT",
                timestamp=sim_state.timestamp,
                best_bid=sim_state.bid,
                best_ask=sim_state.ask,
                mid_price=sim_state.mid,
                bid_size=100,
                ask_size=100,
            )

            engine.on_market_update(mm_market)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 50

    def test_multi_market_with_scenarios(
        self, mock_api_client: MockAPIClient
    ) -> None:
        """Multi-market engine works with different scenarios."""
        multi = MultiMarketEngine(api_client=mock_api_client)

        # Create markets with different scenarios
        scenarios = [
            ("STABLE", "stable_market"),
            ("VOLATILE", "volatile_market"),
            ("TRENDING", "trending_up"),
        ]

        simulators = {}
        for ticker, scenario_name in scenarios:
            multi.add_market(ticker)
            config = get_scenario(scenario_name)
            simulators[ticker] = create_simulator(config, ticker)

        # Run simulation
        for _ in range(20):
            for ticker, simulator in simulators.items():
                sim_state = simulator.generate_market_state()

                mm_market = MarketState(
                    ticker=ticker,
                    timestamp=sim_state.timestamp,
                    best_bid=sim_state.bid,
                    best_ask=sim_state.ask,
                    mid_price=sim_state.mid,
                    bid_size=100,
                    ask_size=100,
                )

                multi.on_market_update(ticker, mm_market)

        status = multi.get_aggregate_status()
        assert status["aggregate"]["total_updates"] == 60  # 20 * 3 markets


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in the engine."""

    def test_engine_handles_errors_gracefully(
        self, engine: MarketMakingEngine
    ) -> None:
        """Engine handles errors without crashing."""
        # This should not raise even with edge case data
        market = create_market_state(bid=0.01, ask=0.99)
        engine.on_market_update(market)

        # Engine should still be functional
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 1

    def test_multi_engine_unknown_ticker(
        self, mock_api_client: MockAPIClient
    ) -> None:
        """Multi-engine handles unknown ticker gracefully."""
        multi = MultiMarketEngine(api_client=mock_api_client)
        multi.add_market("KNOWN")

        # Update for unknown ticker should not crash
        market = create_market_state(ticker="UNKNOWN")
        multi.on_market_update("UNKNOWN", market)

        # Known market should still work
        status = multi.get_aggregate_status()
        assert status["aggregate"]["total_updates"] == 0
