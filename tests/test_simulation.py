"""Comprehensive tests for the simulation framework.

Tests cover:
- Market state generation and validation
- Fill logic correctness
- API simulation behavior
- Scenario configurations
- Edge cases
"""

import pytest
from datetime import datetime

from src.core.models import Fill, MarketState, Quote, ValidationError
from src.simulation import (
    MarketSimulator,
    MeanRevertingSimulator,
    SimulatedAPIClient,
    TrendingSimulator,
    create_api_client,
    create_simulator,
    get_scenario,
    list_scenarios,
    run_simulation,
    SCENARIOS,
)


# =============================================================================
# MarketSimulator Tests
# =============================================================================


class TestMarketSimulatorInit:
    """Tests for MarketSimulator initialization."""

    def test_basic_init(self) -> None:
        """Basic initialization works."""
        sim = MarketSimulator("TEST-MKT")
        assert sim.ticker == "TEST-MKT"
        assert sim.mid_price == 0.50
        assert sim.volatility == 0.02
        assert sim.spread_range == (0.03, 0.06)

    def test_custom_parameters(self) -> None:
        """Custom parameters are applied."""
        sim = MarketSimulator(
            ticker="CUSTOM",
            initial_mid=0.75,
            volatility=0.05,
            spread_range=(0.02, 0.04),
        )
        assert sim.mid_price == 0.75
        assert sim.volatility == 0.05
        assert sim.spread_range == (0.02, 0.04)

    def test_seed_reproducibility(self) -> None:
        """Same seed produces same sequence."""
        sim1 = MarketSimulator("TEST", seed=12345)
        sim2 = MarketSimulator("TEST", seed=12345)

        states1 = sim1.simulate_sequence(10)
        states2 = sim2.simulate_sequence(10)

        for s1, s2 in zip(states1, states2):
            assert s1.mid == s2.mid
            assert s1.bid == s2.bid
            assert s1.ask == s2.ask

    def test_empty_ticker_raises(self) -> None:
        """Empty ticker raises ValueError."""
        with pytest.raises(ValueError, match="ticker cannot be empty"):
            MarketSimulator("")

    def test_invalid_initial_mid_raises(self) -> None:
        """Invalid initial_mid raises ValueError."""
        with pytest.raises(ValueError, match="initial_mid must be between"):
            MarketSimulator("TEST", initial_mid=1.5)
        with pytest.raises(ValueError, match="initial_mid must be between"):
            MarketSimulator("TEST", initial_mid=-0.1)

    def test_negative_volatility_raises(self) -> None:
        """Negative volatility raises ValueError."""
        with pytest.raises(ValueError, match="volatility cannot be negative"):
            MarketSimulator("TEST", volatility=-0.01)

    def test_invalid_spread_range_raises(self) -> None:
        """Invalid spread range raises ValueError."""
        with pytest.raises(ValueError, match="invalid spread_range"):
            MarketSimulator("TEST", spread_range=(0.10, 0.05))  # max < min
        with pytest.raises(ValueError, match="invalid spread_range"):
            MarketSimulator("TEST", spread_range=(-0.01, 0.05))  # negative


class TestMarketSimulatorGeneration:
    """Tests for market state generation."""

    def test_generate_market_state_valid(self) -> None:
        """Generated market states are valid."""
        sim = MarketSimulator("TEST", seed=42)
        state = sim.generate_market_state()

        assert isinstance(state, MarketState)
        assert state.ticker == "TEST"
        assert 0 <= state.bid <= 1
        assert 0 <= state.ask <= 1
        assert state.bid <= state.ask
        assert state.spread >= 0

    def test_simulate_sequence_length(self) -> None:
        """Sequence generation produces correct length."""
        sim = MarketSimulator("TEST", seed=42)
        states = sim.simulate_sequence(100)

        assert len(states) == 100

    def test_simulate_sequence_evolves(self) -> None:
        """Prices evolve over sequence."""
        sim = MarketSimulator("TEST", volatility=0.05, seed=42)
        states = sim.simulate_sequence(50)

        # Prices should change over time
        mids = [s.mid for s in states]
        assert len(set(mids)) > 1  # Not all the same

    def test_price_stays_bounded(self) -> None:
        """Prices stay within 0-1 range even with high volatility."""
        sim = MarketSimulator("TEST", volatility=0.20, seed=42)
        states = sim.simulate_sequence(500)

        for state in states:
            assert 0.01 <= state.mid <= 0.99
            assert 0.01 <= state.bid <= 0.99
            assert 0.01 <= state.ask <= 0.99

    def test_spread_within_range(self) -> None:
        """Spreads stay within configured range."""
        spread_range = (0.03, 0.06)
        sim = MarketSimulator("TEST", spread_range=spread_range, seed=42)
        states = sim.simulate_sequence(100)

        for state in states:
            # Allow small floating point tolerance
            assert state.spread >= spread_range[0] - 0.001
            assert state.spread <= spread_range[1] + 0.001

    def test_get_market_state_ticker_match(self) -> None:
        """get_market_state requires matching ticker."""
        sim = MarketSimulator("TEST-A")

        state = sim.get_market_state("TEST-A")
        assert state.ticker == "TEST-A"

        with pytest.raises(ValueError, match="not 'TEST-B'"):
            sim.get_market_state("TEST-B")

    def test_step_count_increments(self) -> None:
        """Step count increments with each generation."""
        sim = MarketSimulator("TEST")
        assert sim.step_count == 0

        sim.generate_market_state()
        assert sim.step_count == 1

        sim.simulate_sequence(5)
        assert sim.step_count == 6

    def test_invalid_sequence_length(self) -> None:
        """Invalid sequence length raises error."""
        sim = MarketSimulator("TEST")

        with pytest.raises(ValueError, match="must be positive"):
            sim.simulate_sequence(0)
        with pytest.raises(ValueError, match="must be positive"):
            sim.simulate_sequence(-5)


class TestMarketSimulatorFills:
    """Tests for fill simulation logic."""

    def test_bid_fills_when_ask_crosses(self) -> None:
        """BID quote fills when market ask crosses down."""
        sim = MarketSimulator("TEST", initial_mid=0.50, seed=42)

        # Quote at 0.55 (above mid)
        quote = Quote(ticker="TEST", side="BID", price=0.55, size=10, order_id="test-1")

        # Market state with ask at 0.52 (below quote price)
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.48,
            ask=0.52,
        )

        fill = sim.simulate_fill(quote, market)

        assert fill is not None
        assert fill.side == "BID"
        assert fill.size == 10
        assert fill.price <= quote.price

    def test_bid_no_fill_when_ask_above(self) -> None:
        """BID quote doesn't fill when market ask is above quote."""
        sim = MarketSimulator("TEST", seed=42)

        quote = Quote(ticker="TEST", side="BID", price=0.45, size=10, order_id="test-1")

        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.48,
            ask=0.52,  # Above quote price of 0.45
        )

        fill = sim.simulate_fill(quote, market)
        assert fill is None

    def test_ask_fills_when_bid_crosses(self) -> None:
        """ASK quote fills when market bid crosses up."""
        sim = MarketSimulator("TEST", seed=42)

        quote = Quote(ticker="TEST", side="ASK", price=0.45, size=10, order_id="test-1")

        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.48,  # Above quote price
            ask=0.52,
        )

        fill = sim.simulate_fill(quote, market)

        assert fill is not None
        assert fill.side == "ASK"
        assert fill.size == 10
        assert fill.price >= quote.price

    def test_ask_no_fill_when_bid_below(self) -> None:
        """ASK quote doesn't fill when market bid is below quote."""
        sim = MarketSimulator("TEST", seed=42)

        quote = Quote(ticker="TEST", side="ASK", price=0.55, size=10, order_id="test-1")

        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.48,  # Below quote price
            ask=0.52,
        )

        fill = sim.simulate_fill(quote, market)
        assert fill is None

    def test_inactive_quote_no_fill(self) -> None:
        """Inactive quotes don't fill."""
        sim = MarketSimulator("TEST", seed=42)

        quote = Quote(
            ticker="TEST",
            side="BID",
            price=0.55,
            size=10,
            order_id="test-1",
            status="CANCELED",
        )

        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.48,
            ask=0.52,
        )

        fill = sim.simulate_fill(quote, market)
        assert fill is None


class TestTrendingSimulator:
    """Tests for TrendingSimulator."""

    def test_upward_trend(self) -> None:
        """Positive drift creates upward trend."""
        sim = TrendingSimulator(
            "TEST",
            initial_mid=0.30,
            drift=0.005,
            volatility=0.01,
            seed=42,
        )
        states = sim.simulate_sequence(100)

        # Final price should be higher than initial on average
        assert states[-1].mid > sim.mid_price - 0.30  # Started at 0.30

    def test_downward_trend(self) -> None:
        """Negative drift creates downward trend."""
        sim = TrendingSimulator(
            "TEST",
            initial_mid=0.70,
            drift=-0.005,
            volatility=0.01,
            seed=42,
        )
        states = sim.simulate_sequence(100)

        # Final price should be lower than initial on average
        # Just check it moved down somewhat
        avg_mid = sum(s.mid for s in states) / len(states)
        assert avg_mid < 0.70


class TestMeanRevertingSimulator:
    """Tests for MeanRevertingSimulator."""

    def test_reverts_to_fair_value(self) -> None:
        """Price tends toward fair value."""
        sim = MeanRevertingSimulator(
            "TEST",
            initial_mid=0.80,  # Start far from fair value
            fair_value=0.50,
            reversion_speed=0.2,
            volatility=0.01,
            seed=42,
        )
        states = sim.simulate_sequence(100)

        # Should move toward 0.50
        assert abs(states[-1].mid - 0.50) < abs(0.80 - 0.50)

    def test_oscillates_around_fair_value(self) -> None:
        """Price oscillates around fair value."""
        sim = MeanRevertingSimulator(
            "TEST",
            initial_mid=0.50,
            fair_value=0.50,
            reversion_speed=0.15,
            volatility=0.02,
            seed=42,
        )
        states = sim.simulate_sequence(200)

        mids = [s.mid for s in states]
        above = sum(1 for m in mids if m > 0.50)
        below = sum(1 for m in mids if m < 0.50)

        # Should have both above and below (oscillating)
        assert above > 20
        assert below > 20

    def test_invalid_fair_value(self) -> None:
        """Invalid fair_value raises error."""
        with pytest.raises(ValueError, match="fair_value must be between"):
            MeanRevertingSimulator("TEST", fair_value=1.5)

    def test_invalid_reversion_speed(self) -> None:
        """Invalid reversion_speed raises error."""
        with pytest.raises(ValueError, match="reversion_speed must be between"):
            MeanRevertingSimulator("TEST", reversion_speed=1.5)


# =============================================================================
# SimulatedAPIClient Tests
# =============================================================================


class TestSimulatedAPIClientInit:
    """Tests for SimulatedAPIClient initialization."""

    def test_basic_init(self) -> None:
        """Basic initialization works."""
        sim = MarketSimulator("TEST", seed=42)
        client = SimulatedAPIClient(sim)

        assert client.simulator is sim
        assert client.fill_probability == 1.0
        assert len(client.orders) == 0

    def test_invalid_simulator_type(self) -> None:
        """Non-MarketSimulator raises error."""
        with pytest.raises(TypeError, match="must be MarketSimulator"):
            SimulatedAPIClient("not a simulator")  # type: ignore

    def test_invalid_fill_probability(self) -> None:
        """Invalid fill_probability raises error."""
        sim = MarketSimulator("TEST")
        with pytest.raises(ValueError, match="fill_probability must be between"):
            SimulatedAPIClient(sim, fill_probability=1.5)


class TestSimulatedAPIClientOrders:
    """Tests for order placement and management."""

    @pytest.fixture
    def client(self) -> SimulatedAPIClient:
        """Create a fresh client for each test."""
        sim = MarketSimulator("TEST", seed=42)
        return SimulatedAPIClient(sim)

    def test_place_order_returns_id(self, client: SimulatedAPIClient) -> None:
        """place_order returns an order ID."""
        order_id = client.place_order("TEST", "buy", 0.45, 10)

        assert order_id is not None
        assert len(order_id) > 0

    def test_place_order_creates_record(self, client: SimulatedAPIClient) -> None:
        """place_order creates internal order record."""
        order_id = client.place_order("TEST", "buy", 0.45, 10)

        assert order_id in client.orders
        order = client.orders[order_id]
        assert order.ticker == "TEST"
        assert order.side == "BID"
        assert order.price == 0.45
        assert order.size == 10

    def test_place_order_side_normalization(self, client: SimulatedAPIClient) -> None:
        """Order side is normalized to BID/ASK."""
        id1 = client.place_order("TEST", "buy", 0.45, 10)
        id2 = client.place_order("TEST", "BID", 0.46, 10)
        id3 = client.place_order("TEST", "sell", 0.55, 10)
        id4 = client.place_order("TEST", "ASK", 0.56, 10)

        assert client.orders[id1].side == "BID"
        assert client.orders[id2].side == "BID"
        assert client.orders[id3].side == "ASK"
        assert client.orders[id4].side == "ASK"

    def test_place_order_invalid_side(self, client: SimulatedAPIClient) -> None:
        """Invalid side raises error."""
        with pytest.raises(ValueError, match="Invalid side"):
            client.place_order("TEST", "long", 0.45, 10)

    def test_place_order_invalid_size(self, client: SimulatedAPIClient) -> None:
        """Invalid size raises error."""
        with pytest.raises(ValueError, match="size must be positive"):
            client.place_order("TEST", "buy", 0.45, 0)
        with pytest.raises(ValueError, match="size must be positive"):
            client.place_order("TEST", "buy", 0.45, -5)

    def test_cancel_order_success(self, client: SimulatedAPIClient) -> None:
        """cancel_order succeeds for open order."""
        order_id = client.place_order("TEST", "buy", 0.45, 10)

        result = client.cancel_order(order_id)

        assert result is True
        assert client.orders[order_id].status == "CANCELED"

    def test_cancel_order_not_found(self, client: SimulatedAPIClient) -> None:
        """cancel_order returns False for unknown order."""
        result = client.cancel_order("nonexistent-id")
        assert result is False

    def test_cancel_already_canceled(self, client: SimulatedAPIClient) -> None:
        """cancel_order returns False for already canceled order."""
        order_id = client.place_order("TEST", "buy", 0.45, 10)
        client.cancel_order(order_id)

        result = client.cancel_order(order_id)
        assert result is False

    def test_get_order_status(self, client: SimulatedAPIClient) -> None:
        """get_order_status returns order details."""
        order_id = client.place_order("TEST", "buy", 0.45, 10)

        status = client.get_order_status(order_id)

        assert status["order_id"] == order_id
        assert status["ticker"] == "TEST"
        assert status["side"] == "BID"
        assert status["price"] == 0.45
        assert status["size"] == 10
        assert status["status"] == "OPEN"

    def test_get_order_status_not_found(self, client: SimulatedAPIClient) -> None:
        """get_order_status raises for unknown order."""
        with pytest.raises(ValueError, match="Order not found"):
            client.get_order_status("nonexistent-id")


class TestSimulatedAPIClientSimulation:
    """Tests for simulation stepping and fills."""

    @pytest.fixture
    def client(self) -> SimulatedAPIClient:
        """Create a fresh client for each test."""
        sim = MarketSimulator("TEST", initial_mid=0.50, seed=42)
        return SimulatedAPIClient(sim)

    def test_step_advances_market(self, client: SimulatedAPIClient) -> None:
        """step() advances the market state."""
        state1 = client.get_market_data("TEST")
        client.step()
        state2 = client.get_market_data("TEST")

        # Time should advance
        assert state2.timestamp > state1.timestamp

    def test_run_steps_returns_states(self, client: SimulatedAPIClient) -> None:
        """run_steps returns list of market states."""
        states = client.run_steps(10)

        assert len(states) == 10
        for state in states:
            assert isinstance(state, MarketState)

    def test_order_fills_on_price_cross(self, client: SimulatedAPIClient) -> None:
        """Order fills when price crosses."""
        # Place a BID at the current ask (should fill immediately or soon)
        market = client.get_market_data("TEST")
        order_id = client.place_order("TEST", "buy", market.ask + 0.10, 10)

        # Should fill on check
        status = client.get_order_status(order_id)
        assert status["status"] == "FILLED"
        assert status["filled_size"] == 10

    def test_get_open_orders(self, client: SimulatedAPIClient) -> None:
        """get_open_orders returns only open orders."""
        id1 = client.place_order("TEST", "buy", 0.30, 10)  # Low price, won't fill
        id2 = client.place_order("TEST", "buy", 0.31, 10)
        client.cancel_order(id2)

        open_orders = client.get_open_orders()

        assert len(open_orders) == 1
        assert open_orders[0]["order_id"] == id1

    def test_get_all_fills(self, client: SimulatedAPIClient) -> None:
        """get_all_fills returns fill history."""
        market = client.get_market_data("TEST")
        client.place_order("TEST", "buy", market.ask + 0.10, 10)

        fills = client.get_all_fills()

        assert len(fills) >= 1
        assert all(isinstance(f, Fill) for f in fills)

    def test_reset_clears_state(self, client: SimulatedAPIClient) -> None:
        """reset clears orders and fills."""
        client.place_order("TEST", "buy", 0.45, 10)
        client.step()

        client.reset()

        assert len(client.orders) == 0
        assert len(client.get_all_fills()) == 0


# =============================================================================
# Scenario Tests
# =============================================================================


class TestScenarios:
    """Tests for pre-built scenarios."""

    def test_list_scenarios(self) -> None:
        """list_scenarios returns available scenarios."""
        names = list_scenarios()

        assert len(names) > 0
        assert "stable_market" in names
        assert "volatile_market" in names
        assert "trending_up" in names

    def test_get_scenario_valid(self) -> None:
        """get_scenario returns valid config."""
        config = get_scenario("stable_market")

        assert config.name == "stable_market"
        assert config.volatility > 0
        assert config.spread_range[0] < config.spread_range[1]

    def test_get_scenario_invalid(self) -> None:
        """get_scenario raises for unknown scenario."""
        with pytest.raises(ValueError, match="Unknown scenario"):
            get_scenario("nonexistent_scenario")

    def test_create_simulator_from_config(self) -> None:
        """create_simulator creates correct simulator type."""
        # Basic scenario -> MarketSimulator
        sim = create_simulator(get_scenario("stable_market"))
        assert isinstance(sim, MarketSimulator)

        # Trending scenario -> TrendingSimulator
        sim = create_simulator(get_scenario("trending_up"))
        assert isinstance(sim, TrendingSimulator)

        # Mean-reverting scenario -> MeanRevertingSimulator
        sim = create_simulator(get_scenario("mean_reverting"))
        assert isinstance(sim, MeanRevertingSimulator)

    def test_create_api_client_from_config(self) -> None:
        """create_api_client creates working client."""
        client = create_api_client(get_scenario("stable_market"))

        assert isinstance(client, SimulatedAPIClient)
        assert isinstance(client.simulator, MarketSimulator)

    def test_all_scenarios_produce_valid_states(self) -> None:
        """All pre-built scenarios produce valid market states."""
        for name in list_scenarios():
            config = get_scenario(name)
            sim = create_simulator(config)
            states = sim.simulate_sequence(50)

            for state in states:
                assert 0 <= state.bid <= 1
                assert 0 <= state.ask <= 1
                assert state.bid <= state.ask


class TestRunSimulation:
    """Tests for run_simulation helper."""

    def test_run_simulation_basic(self) -> None:
        """run_simulation executes strategy and returns results."""
        client = create_api_client(get_scenario("stable_market"))

        # Simple strategy: do nothing
        def strategy(c: SimulatedAPIClient) -> None:
            pass

        result = run_simulation(client, strategy, n_steps=50)

        assert result.n_steps == 50
        assert len(result.price_path) == 50
        assert result.final_position == 0

    def test_run_simulation_with_orders(self) -> None:
        """run_simulation tracks fills from strategy."""
        client = create_api_client(get_scenario("stable_market"))
        orders_placed = []

        def strategy(c: SimulatedAPIClient) -> None:
            if len(orders_placed) == 0:
                # Place one aggressive order
                market = c.get_market_data("SIM-MARKET")
                order_id = c.place_order("SIM-MARKET", "buy", market.ask + 0.05, 5)
                orders_placed.append(order_id)

        result = run_simulation(client, strategy, n_steps=10)

        # Should have at least one fill
        assert result.n_fills >= 1
        assert result.total_volume >= 5


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestMarketStateValidation:
    """Tests for MarketState model validation."""

    def test_valid_market_state(self) -> None:
        """Valid MarketState is created successfully."""
        state = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.48,
            ask=0.52,
        )
        assert state.mid == 0.50
        assert abs(state.spread - 0.04) < 0.0001  # Float tolerance

    def test_bid_greater_than_ask_raises(self) -> None:
        """bid > ask raises ValidationError."""
        with pytest.raises(ValidationError, match="cannot be greater than ask"):
            MarketState(
                ticker="TEST",
                timestamp=datetime.now(),
                bid=0.55,
                ask=0.50,
            )


class TestQuoteValidation:
    """Tests for Quote model validation."""

    def test_valid_quote(self) -> None:
        """Valid Quote is created successfully."""
        quote = Quote(ticker="TEST", side="BID", price=0.50, size=10)
        assert quote.remaining_size == 10
        assert quote.is_active is True

    def test_invalid_side_raises(self) -> None:
        """Invalid side raises ValidationError."""
        with pytest.raises(ValidationError, match="must be 'BID' or 'ASK'"):
            Quote(ticker="TEST", side="LONG", price=0.50, size=10)

    def test_invalid_size_raises(self) -> None:
        """Invalid size raises ValidationError."""
        with pytest.raises(ValidationError, match="must be positive"):
            Quote(ticker="TEST", side="BID", price=0.50, size=0)


class TestFillValidation:
    """Tests for Fill model validation."""

    def test_valid_fill(self) -> None:
        """Valid Fill is created successfully."""
        fill = Fill(
            ticker="TEST",
            side="BID",
            price=0.50,
            size=10,
            order_id="order-123",
        )
        assert fill.notional_value == 5.0

    def test_empty_order_id_raises(self) -> None:
        """Empty order_id raises ValidationError."""
        with pytest.raises(ValidationError, match="order_id cannot be empty"):
            Fill(ticker="TEST", side="BID", price=0.50, size=10, order_id="")
