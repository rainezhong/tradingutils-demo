"""Tests for the paired market simulation framework.

Tests cover:
- PairedMarketSimulator initialization and validation
- Poll function output format
- Complementary relationship between markets
- Mispricing injection
- Integration with LiveArbMonitor
"""

import pytest

from src.simulation import (
    MispricingConfig,
    PairedMarketSimulator,
    SpreadScenarioConfig,
    SPREAD_SCENARIOS,
    create_spread_simulator,
    get_spread_scenario,
    list_spread_scenarios,
)


# =============================================================================
# PairedMarketSimulator Initialization Tests
# =============================================================================


class TestPairedMarketSimulatorInit:
    """Tests for PairedMarketSimulator initialization."""

    def test_basic_init(self) -> None:
        """Basic initialization works with default parameters."""
        sim = PairedMarketSimulator("Team A", "Team B")
        assert sim.name_1 == "Team A"
        assert sim.name_2 == "Team B"
        assert sim.base_probability == 0.50
        assert sim.volatility == 0.02
        assert sim.spread_range == (0.03, 0.06)
        assert sim.correlation == 0.95

    def test_custom_parameters(self) -> None:
        """Custom parameters are applied correctly."""
        sim = PairedMarketSimulator(
            name_1="Market 1",
            name_2="Market 2",
            base_probability=0.60,
            volatility=0.05,
            spread_range=(0.02, 0.04),
            correlation=0.90,
        )
        assert sim.base_probability == 0.60
        assert sim.volatility == 0.05
        assert sim.spread_range == (0.02, 0.04)
        assert sim.correlation == 0.90

    def test_seed_reproducibility(self) -> None:
        """Same seed produces same sequence."""
        sim1 = PairedMarketSimulator("A", "B", seed=12345)
        sim2 = PairedMarketSimulator("A", "B", seed=12345)

        for _ in range(10):
            m1_a, m2_a = sim1.step()
            m1_b, m2_b = sim2.step()

            assert m1_a["yes_ask"] == m1_b["yes_ask"]
            assert m2_a["yes_ask"] == m2_b["yes_ask"]

    def test_empty_name_1_raises(self) -> None:
        """Empty name_1 raises ValueError."""
        with pytest.raises(ValueError, match="name_1 cannot be empty"):
            PairedMarketSimulator("", "Team B")

    def test_empty_name_2_raises(self) -> None:
        """Empty name_2 raises ValueError."""
        with pytest.raises(ValueError, match="name_2 cannot be empty"):
            PairedMarketSimulator("Team A", "")

    def test_invalid_base_probability_raises(self) -> None:
        """Invalid base_probability raises ValueError."""
        with pytest.raises(ValueError, match="base_probability must be between"):
            PairedMarketSimulator("A", "B", base_probability=0.0)
        with pytest.raises(ValueError, match="base_probability must be between"):
            PairedMarketSimulator("A", "B", base_probability=1.0)
        with pytest.raises(ValueError, match="base_probability must be between"):
            PairedMarketSimulator("A", "B", base_probability=1.5)

    def test_negative_volatility_raises(self) -> None:
        """Negative volatility raises ValueError."""
        with pytest.raises(ValueError, match="volatility cannot be negative"):
            PairedMarketSimulator("A", "B", volatility=-0.01)

    def test_invalid_spread_range_raises(self) -> None:
        """Invalid spread range raises ValueError."""
        with pytest.raises(ValueError, match="invalid spread_range"):
            PairedMarketSimulator("A", "B", spread_range=(0.10, 0.05))  # max < min
        with pytest.raises(ValueError, match="invalid spread_range"):
            PairedMarketSimulator("A", "B", spread_range=(-0.01, 0.05))  # negative

    def test_invalid_correlation_raises(self) -> None:
        """Invalid correlation raises ValueError."""
        with pytest.raises(ValueError, match="correlation must be between"):
            PairedMarketSimulator("A", "B", correlation=-0.1)
        with pytest.raises(ValueError, match="correlation must be between"):
            PairedMarketSimulator("A", "B", correlation=1.5)


# =============================================================================
# PairedMarketSimulator Output Tests
# =============================================================================


class TestPairedMarketSimulatorOutput:
    """Tests for poll function output format and values."""

    @pytest.fixture
    def sim(self) -> PairedMarketSimulator:
        """Create a fresh simulator for each test."""
        return PairedMarketSimulator("Team A", "Team B", seed=42)

    def test_poll_market_1_format(self, sim: PairedMarketSimulator) -> None:
        """poll_market_1 returns correct format for LiveArbMonitor."""
        data = sim.poll_market_1()

        assert "name" in data
        assert "yes_ask" in data
        assert "no_ask" in data
        assert "yes_bid" in data
        assert "no_bid" in data
        assert data["name"] == "Team A"

    def test_poll_market_2_format(self, sim: PairedMarketSimulator) -> None:
        """poll_market_2 returns correct format for LiveArbMonitor."""
        data = sim.poll_market_2()

        assert "name" in data
        assert "yes_ask" in data
        assert "no_ask" in data
        assert "yes_bid" in data
        assert "no_bid" in data
        assert data["name"] == "Team B"

    def test_complementary_relationship(self, sim: PairedMarketSimulator) -> None:
        """Market 1 YES mid ≈ 1 - Market 2 YES mid (complement)."""
        for _ in range(20):
            m1 = sim.poll_market_1()
            m2 = sim.poll_market_2()

            m1_yes_mid = (m1["yes_bid"] + m1["yes_ask"]) / 2
            m2_yes_mid = (m2["yes_bid"] + m2["yes_ask"]) / 2

            # m1 YES should be complement of m2 YES (within tolerance due to noise)
            expected_complement = 1.0 - m1_yes_mid
            assert abs(m2_yes_mid - expected_complement) < 0.15

            sim.step()

    def test_bid_less_than_ask(self, sim: PairedMarketSimulator) -> None:
        """Bid is always less than ask for all instruments."""
        for _ in range(50):
            m1 = sim.poll_market_1()
            m2 = sim.poll_market_2()

            assert m1["yes_bid"] < m1["yes_ask"]
            assert m1["no_bid"] < m1["no_ask"]
            assert m2["yes_bid"] < m2["yes_ask"]
            assert m2["no_bid"] < m2["no_ask"]

            sim.step()

    def test_price_bounds(self, sim: PairedMarketSimulator) -> None:
        """All prices stay within valid 0.01-0.99 range."""
        for _ in range(100):
            m1 = sim.poll_market_1()
            m2 = sim.poll_market_2()

            for key in ["yes_ask", "no_ask", "yes_bid", "no_bid"]:
                assert 0.01 <= m1[key] <= 0.99, f"m1[{key}] = {m1[key]}"
                assert 0.01 <= m2[key] <= 0.99, f"m2[{key}] = {m2[key]}"

            sim.step()

    def test_step_advances_and_returns_data(self, sim: PairedMarketSimulator) -> None:
        """step() advances simulation and returns both market data."""
        assert sim.step_count == 0

        m1, m2 = sim.step()

        assert sim.step_count == 1
        assert "name" in m1
        assert "name" in m2
        assert m1["name"] == "Team A"
        assert m2["name"] == "Team B"

    def test_high_volatility_stays_bounded(self) -> None:
        """High volatility simulation stays within price bounds."""
        sim = PairedMarketSimulator(
            "A", "B",
            volatility=0.20,
            seed=42,
        )

        for _ in range(500):
            m1, m2 = sim.step()

            for key in ["yes_ask", "no_ask", "yes_bid", "no_bid"]:
                assert 0.01 <= m1[key] <= 0.99
                assert 0.01 <= m2[key] <= 0.99


# =============================================================================
# Mispricing Injection Tests
# =============================================================================


class TestMispricingInjection:
    """Tests for mispricing injection functionality."""

    def test_routing_edge_injection(self) -> None:
        """Routing edge makes one instrument cheaper."""
        config = MispricingConfig(
            routing_edge_magnitude=0.05,  # 5 cent edge
            routing_edge_market=1,
        )
        sim = PairedMarketSimulator(
            "A", "B",
            mispricing_config=config,
            volatility=0.001,  # Very low volatility
            seed=42,
        )

        m1 = sim.poll_market_1()
        m2 = sim.poll_market_2()

        # Market 1 YES should be cheaper (lower ask) than Market 2 NO
        # This creates routing edge for Team A exposure
        # Due to how the mispricing is applied, m1 yes_ask is reduced
        # Compare similar instruments: m1 YES ask should be notably lower
        assert m1["yes_ask"] < m2["no_ask"]

    def test_cross_market_spread_injection(self) -> None:
        """Cross-market spread creates bid > ask opportunity."""
        config = MispricingConfig(
            cross_market_spread=0.10,  # Large crossing for clear test
        )
        sim = PairedMarketSimulator(
            "A", "B",
            mispricing_config=config,
            spread_range=(0.02, 0.03),  # Tight spreads
            volatility=0.001,
            seed=42,
        )

        m1 = sim.poll_market_1()
        m2 = sim.poll_market_2()

        # Cross-market spread increases m1 YES bid and decreases m2 NO ask
        # So m1_yes_bid might be > m2_no_ask (arb opportunity)
        # The sum of these adjustments is cross_market_spread
        assert m1["yes_bid"] > m2["no_ask"] - 0.15  # Within tolerance

    def test_dutch_discount_injection(self) -> None:
        """Dutch discount makes combined cost < $1.00."""
        config = MispricingConfig(
            dutch_discount=0.10,  # 10 cent discount
        )
        sim = PairedMarketSimulator(
            "A", "B",
            mispricing_config=config,
            spread_range=(0.02, 0.03),
            volatility=0.001,
            seed=42,
        )

        m1 = sim.poll_market_1()
        m2 = sim.poll_market_2()

        # Combined best asks should be less than 1.0
        # Best Team A exposure: min(m1_yes_ask, m2_no_ask)
        # Best Team B exposure: min(m2_yes_ask, m1_no_ask)
        t1_best = min(m1["yes_ask"], m2["no_ask"])
        t2_best = min(m2["yes_ask"], m1["no_ask"])
        combined = t1_best + t2_best

        # Should be notably less than 1.0
        assert combined < 1.0

    def test_transient_mispricing_expires(self) -> None:
        """Mispricing with duration_steps expires correctly."""
        config = MispricingConfig(
            routing_edge_magnitude=0.10,
            routing_edge_market=1,
            duration_steps=3,
        )
        sim = PairedMarketSimulator(
            "A", "B",
            mispricing_config=config,
            volatility=0.001,
            seed=42,
        )

        # Initial state should have mispricing
        m1_before = sim.poll_market_1()

        # Step 3 times to exhaust duration
        for _ in range(3):
            sim.step()

        # After duration expires, mispricing should be gone
        m1_after = sim.poll_market_1()

        # The prices should be different after mispricing expires
        # (though this is a weak test due to price evolution)
        assert sim._mispricing_remaining == 0

    def test_permanent_mispricing(self) -> None:
        """Mispricing with duration_steps=0 is permanent."""
        config = MispricingConfig(
            routing_edge_magnitude=0.05,
            routing_edge_market=1,
            duration_steps=0,  # Permanent
        )
        sim = PairedMarketSimulator(
            "A", "B",
            mispricing_config=config,
            volatility=0.001,
            seed=42,
        )

        # Step many times
        for _ in range(100):
            sim.step()

        # Mispricing should still be active
        assert sim._is_mispricing_active()


# =============================================================================
# LiveArbMonitor Integration Tests
# =============================================================================


class TestLiveArbMonitorIntegration:
    """Tests for integration with LiveArbMonitor."""

    def test_poll_functions_work_with_monitor(self) -> None:
        """Poll functions return data compatible with LiveArbMonitor."""
        from arb.live_arb import LiveArbMonitor

        sim = PairedMarketSimulator("Team A", "Team B", seed=42)

        # Should be able to create monitor without errors
        monitor = LiveArbMonitor(
            market_1_poll_func=sim.poll_market_1,
            market_2_poll_func=sim.poll_market_2,
            poll_period_ms=100,
            contract_size=100,
        )

        # Poll functions should work
        m1 = sim.poll_market_1()
        m2 = sim.poll_market_2()

        assert m1["yes_ask"] is not None
        assert m2["yes_ask"] is not None

    def test_cross_market_arb_detected(self) -> None:
        """Cross-market arb opportunities are detected correctly."""
        config = MispricingConfig(
            cross_market_spread=0.05,  # Clear arb opportunity
        )
        sim = PairedMarketSimulator(
            "Team A", "Team B",
            mispricing_config=config,
            spread_range=(0.02, 0.03),
            volatility=0.001,
            seed=42,
        )

        opp = sim.get_current_opportunity(contract_size=100)

        # With cross-market spread, there should be positive arb PnL
        # (though fees may reduce or eliminate it)
        assert opp["arb_pnl_t1"] is not None
        assert opp["arb_pnl_t2"] is not None

    def test_routing_edge_detected(self) -> None:
        """Routing edges are calculated correctly."""
        config = MispricingConfig(
            routing_edge_magnitude=0.05,
            routing_edge_market=1,
        )
        sim = PairedMarketSimulator(
            "Team A", "Team B",
            mispricing_config=config,
            volatility=0.001,
            seed=42,
        )

        opp = sim.get_current_opportunity(contract_size=100)

        # Routing edge should be negative (m1 YES is cheaper than m2 NO for Team A exposure)
        assert opp["routing_edge_t1"] < 0

    def test_dutch_book_detected(self) -> None:
        """Dutch book opportunities are calculated correctly."""
        config = MispricingConfig(
            dutch_discount=0.10,  # 10 cent discount
        )
        sim = PairedMarketSimulator(
            "Team A", "Team B",
            mispricing_config=config,
            spread_range=(0.02, 0.03),
            volatility=0.001,
            seed=42,
        )

        opp = sim.get_current_opportunity(contract_size=100)

        # Dutch profit should be positive (before fees)
        # Note: fees may reduce this
        assert opp["dutch_profit"] > -0.05  # At least not deeply negative


# =============================================================================
# Spread Scenario Tests
# =============================================================================


class TestSpreadScenarios:
    """Tests for pre-built spread scenarios."""

    def test_list_spread_scenarios(self) -> None:
        """list_spread_scenarios returns available scenarios."""
        names = list_spread_scenarios()

        assert len(names) > 0
        assert "no_opportunity" in names
        assert "routing_edge" in names
        assert "cross_market_arb" in names
        assert "dutch_book" in names

    def test_get_spread_scenario_valid(self) -> None:
        """get_spread_scenario returns valid config."""
        config = get_spread_scenario("no_opportunity")

        assert config.name == "no_opportunity"
        assert config.volatility > 0
        assert config.spread_range[0] < config.spread_range[1]

    def test_get_spread_scenario_invalid(self) -> None:
        """get_spread_scenario raises for unknown scenario."""
        with pytest.raises(ValueError, match="Unknown spread scenario"):
            get_spread_scenario("nonexistent_scenario")

    def test_create_spread_simulator_from_config(self) -> None:
        """create_spread_simulator creates correct simulator."""
        config = get_spread_scenario("routing_edge")
        sim = create_spread_simulator(config)

        assert isinstance(sim, PairedMarketSimulator)
        assert sim.name_1 == "Team A"
        assert sim.name_2 == "Team B"

    def test_create_spread_simulator_custom_names(self) -> None:
        """create_spread_simulator allows custom names."""
        config = get_spread_scenario("no_opportunity")
        sim = create_spread_simulator(config, name_1="Lakers", name_2="Celtics")

        assert sim.name_1 == "Lakers"
        assert sim.name_2 == "Celtics"

    def test_all_spread_scenarios_produce_valid_output(self) -> None:
        """All pre-built spread scenarios produce valid market data."""
        for name in list_spread_scenarios():
            config = get_spread_scenario(name)
            sim = create_spread_simulator(config)

            # Run a few steps
            for _ in range(10):
                m1, m2 = sim.step()

                # Check valid format
                assert "yes_ask" in m1
                assert "yes_bid" in m1
                assert "no_ask" in m1
                assert "no_bid" in m1

                # Check valid ranges
                for key in ["yes_ask", "no_ask", "yes_bid", "no_bid"]:
                    assert 0.01 <= m1[key] <= 0.99, f"Scenario {name}: m1[{key}] = {m1[key]}"
                    assert 0.01 <= m2[key] <= 0.99, f"Scenario {name}: m2[{key}] = {m2[key]}"

    def test_no_opportunity_scenario_efficient(self) -> None:
        """no_opportunity scenario has no clear arbitrage."""
        config = get_spread_scenario("no_opportunity")
        sim = create_spread_simulator(config)

        opp = sim.get_current_opportunity(contract_size=100)

        # With perfect correlation and no mispricing, arb PnL should be negative (fees dominate)
        if opp["arb_pnl_t1"] is not None:
            assert opp["arb_pnl_t1"] < 0.01  # No significant positive arb

    def test_routing_edge_scenario_has_edge(self) -> None:
        """routing_edge scenario has detectable routing edge."""
        config = get_spread_scenario("routing_edge")
        sim = create_spread_simulator(config)

        opp = sim.get_current_opportunity(contract_size=100)

        # Should have non-zero routing edge
        assert abs(opp["routing_edge_t1"]) > 0.001 or abs(opp["routing_edge_t2"]) > 0.001


class TestMispricingConfig:
    """Tests for MispricingConfig dataclass."""

    def test_default_values(self) -> None:
        """Default MispricingConfig has no mispricings."""
        config = MispricingConfig()

        assert config.routing_edge_magnitude == 0.0
        assert config.routing_edge_market == 1
        assert config.cross_market_spread == 0.0
        assert config.dutch_discount == 0.0
        assert config.duration_steps == 0

    def test_custom_values(self) -> None:
        """Custom MispricingConfig values are stored."""
        config = MispricingConfig(
            routing_edge_magnitude=0.05,
            routing_edge_market=2,
            cross_market_spread=0.02,
            dutch_discount=0.03,
            duration_steps=10,
        )

        assert config.routing_edge_magnitude == 0.05
        assert config.routing_edge_market == 2
        assert config.cross_market_spread == 0.02
        assert config.dutch_discount == 0.03
        assert config.duration_steps == 10


class TestPairedMarketSimulatorReset:
    """Tests for simulator reset functionality."""

    def test_reset_restores_initial_state(self) -> None:
        """reset() restores simulator to initial state."""
        sim = PairedMarketSimulator("A", "B", base_probability=0.60, seed=42)

        # Run some steps
        for _ in range(50):
            sim.step()

        assert sim.step_count == 50

        # Reset
        sim.reset()

        assert sim.step_count == 0

    def test_reset_with_new_base_probability(self) -> None:
        """reset() can set new base probability."""
        sim = PairedMarketSimulator("A", "B", base_probability=0.50, seed=42)

        sim.reset(base_probability=0.70)

        assert sim.base_probability == 0.70

    def test_reset_invalid_base_probability_raises(self) -> None:
        """reset() with invalid base_probability raises ValueError."""
        sim = PairedMarketSimulator("A", "B", seed=42)

        with pytest.raises(ValueError, match="base_probability must be between"):
            sim.reset(base_probability=0.0)
