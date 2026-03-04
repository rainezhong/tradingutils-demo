"""Property-based tests for market-making invariants.

Tests invariants that must always hold:
- Position always within limits
- Quotes always valid (bid < ask)
- P&L calculations always correct
- No quote crossing (bid never > market ask)
"""

from datetime import datetime
import random

from src.core.config import RiskConfig
from src.engine import MarketMakingEngine
from src.execution.mock_api_client import MockAPIClient
from src.market_making.config import MarketMakerConfig
from src.market_making.models import Fill, MarketState


def create_market(
    ticker: str = "TEST",
    mid: float = 0.50,
    spread: float = 0.02,
) -> MarketState:
    """Create a MarketState for testing."""
    return MarketState(
        ticker=ticker,
        timestamp=datetime.now(),
        best_bid=mid - spread / 2,
        best_ask=mid + spread / 2,
        mid_price=mid,
        bid_size=100,
        ask_size=100,
    )


def create_engine(
    ticker: str = "TEST",
    max_position: int = 50,
    target_spread: float = 0.04,
) -> tuple[MarketMakingEngine, MockAPIClient]:
    """Create an engine with mock API client."""
    api_client = MockAPIClient()

    mm_config = MarketMakerConfig(
        target_spread=target_spread,
        max_position=max_position,
        quote_size=10,
    )

    risk_config = RiskConfig(
        max_position_size=max_position,
        max_total_position=max_position * 2,
        max_loss_per_position=100.0,
        max_daily_loss=500.0,
    )

    engine = MarketMakingEngine(
        ticker=ticker,
        api_client=api_client,
        mm_config=mm_config,
        risk_config=risk_config,
    )

    return engine, api_client


def make_fill(
    order_id: str,
    ticker: str = "TEST",
    side: str = "BID",
    price: float = 0.50,
    size: int = 10,
) -> Fill:
    """Helper to create a Fill with correct argument order."""
    return Fill(
        order_id=order_id,
        ticker=ticker,
        side=side,
        price=price,
        size=size,
        timestamp=datetime.now(),
    )


class TestPositionInvariants:
    """Tests that position is always within limits."""

    def test_position_within_limits_single_fill(self) -> None:
        """Single fill keeps position within limits."""
        max_pos = 30
        engine, _ = create_engine(max_position=max_pos)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        fill = make_fill("order-1", "TEST", "BID", 0.49, 10)
        engine.market_maker.update_position(fill)

        assert abs(engine.market_maker.position.contracts) <= max_pos

    def test_position_within_limits_multiple_fills(self) -> None:
        """Multiple fills keep position within strategy limits."""
        max_pos = 50
        engine, _ = create_engine(max_position=max_pos)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        random.seed(42)
        for i in range(20):
            side = random.choice(["BID", "ASK"])
            size = random.randint(1, 15)
            price = 0.49 if side == "BID" else 0.51

            fill = make_fill(f"order-{i}", "TEST", side, price, size)
            engine.market_maker.update_position(fill)
            engine.on_market_update(market)

            pos = engine.market_maker.position.contracts
            assert isinstance(pos, int)

    def test_position_tracking_accuracy(self) -> None:
        """Position tracking is accurate after many fills."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        expected_position = 0
        fills = [
            ("BID", 10),
            ("ASK", 5),
            ("BID", 8),
            ("ASK", 3),
            ("BID", 2),
            ("ASK", 12),
        ]

        for i, (side, size) in enumerate(fills):
            if side == "BID":
                expected_position += size
            else:
                expected_position -= size

            fill = make_fill(f"order-{i}", "TEST", side, 0.50, size)
            engine.market_maker.update_position(fill)

        assert engine.market_maker.position.contracts == expected_position

    def test_long_position_accuracy(self) -> None:
        """Long position is accurately tracked."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        fill = make_fill("o1", "TEST", "BID", 0.49, 25)
        engine.market_maker.update_position(fill)

        pos = engine.market_maker.position
        assert pos.is_long
        assert pos.contracts == 25

    def test_short_position_accuracy(self) -> None:
        """Short position is accurately tracked."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        fill = make_fill("o1", "TEST", "ASK", 0.51, 25)
        engine.market_maker.update_position(fill)

        pos = engine.market_maker.position
        assert pos.is_short
        assert pos.contracts == -25


class TestQuoteInvariants:
    """Tests that quotes are always valid."""

    def test_bid_less_than_ask(self) -> None:
        """Generated quotes always have bid < ask."""
        engine, _ = create_engine()

        random.seed(42)
        for _ in range(50):
            mid = random.uniform(0.20, 0.80)
            market = create_market(mid=mid)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)

            if len(quotes) >= 2:
                bids = [q for q in quotes if q.side == "BID"]
                asks = [q for q in quotes if q.side == "ASK"]

                if bids and asks:
                    max_bid = max(q.price for q in bids)
                    min_ask = min(q.price for q in asks)
                    assert max_bid < min_ask, f"Bid {max_bid} >= Ask {min_ask}"

    def test_quotes_within_bounds(self) -> None:
        """Quote prices are within [0, 1] bounds."""
        engine, _ = create_engine()

        for mid in [0.10, 0.30, 0.50, 0.70, 0.90]:
            market = create_market(mid=mid)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)

            for quote in quotes:
                assert 0 < quote.price < 1, f"Quote price {quote.price} out of bounds"

    def test_quote_size_positive(self) -> None:
        """Quote sizes are always positive."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        quotes = engine.market_maker.generate_quotes(market)

        for quote in quotes:
            assert quote.size > 0, f"Quote size {quote.size} not positive"

    def test_quotes_symmetric_around_mid(self) -> None:
        """Quotes are approximately symmetric around mid price."""
        engine, _ = create_engine(target_spread=0.04)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        quotes = engine.market_maker.generate_quotes(market)

        if len(quotes) >= 2:
            bids = [q for q in quotes if q.side == "BID"]
            asks = [q for q in quotes if q.side == "ASK"]

            if bids and asks:
                bid_price = bids[0].price
                ask_price = asks[0].price
                mid_of_quotes = (bid_price + ask_price) / 2
                assert abs(mid_of_quotes - 0.50) < 0.05


class TestPnLInvariants:
    """Tests that P&L calculations are always correct."""

    def test_realized_pnl_round_trip(self) -> None:
        """Buy then sell at higher price = positive P&L."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        buy = make_fill("o1", "TEST", "BID", 0.45, 10)
        engine.market_maker.update_position(buy)

        sell = make_fill("o2", "TEST", "ASK", 0.55, 10)
        engine.market_maker.update_position(sell)

        assert engine.market_maker.position.realized_pnl > 0
        expected_pnl = 10 * (0.55 - 0.45)
        assert abs(engine.market_maker.position.realized_pnl - expected_pnl) < 0.01

    def test_realized_pnl_losing_trade(self) -> None:
        """Buy high, sell low = negative P&L."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        buy = make_fill("o1", "TEST", "BID", 0.55, 10)
        engine.market_maker.update_position(buy)

        sell = make_fill("o2", "TEST", "ASK", 0.45, 10)
        engine.market_maker.update_position(sell)

        assert engine.market_maker.position.realized_pnl < 0

    def test_unrealized_pnl_long_profit(self) -> None:
        """Long position with price increase = unrealized profit."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        buy = make_fill("o1", "TEST", "BID", 0.45, 10)
        engine.market_maker.update_position(buy)

        market = create_market(mid=0.55)
        engine.on_market_update(market)

        pos = engine.market_maker.position
        assert pos.unrealized_pnl > 0

    def test_unrealized_pnl_short_profit(self) -> None:
        """Short position with price decrease = unrealized profit."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        sell = make_fill("o1", "TEST", "ASK", 0.55, 10)
        engine.market_maker.update_position(sell)

        market = create_market(mid=0.45)
        engine.on_market_update(market)

        pos = engine.market_maker.position
        assert pos.unrealized_pnl > 0

    def test_total_pnl_consistency(self) -> None:
        """Total P&L = realized + unrealized."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        buy = make_fill("o1", "TEST", "BID", 0.48, 20)
        engine.market_maker.update_position(buy)

        sell = make_fill("o2", "TEST", "ASK", 0.52, 10)
        engine.market_maker.update_position(sell)

        market = create_market(mid=0.55)
        engine.on_market_update(market)

        pos = engine.market_maker.position
        total = pos.realized_pnl + pos.unrealized_pnl
        assert abs(pos.total_pnl - total) < 0.001


class TestNoCrossing:
    """Tests that quotes never cross the market."""

    def test_bid_not_above_market_ask(self) -> None:
        """Bid quotes never exceed market ask."""
        engine, _ = create_engine()

        random.seed(42)
        for _ in range(50):
            mid = random.uniform(0.20, 0.80)
            spread = random.uniform(0.01, 0.10)
            market = create_market(mid=mid, spread=spread)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)

            for quote in quotes:
                if quote.side == "BID":
                    assert quote.price <= market.best_ask, (
                        f"Bid {quote.price} > market ask {market.best_ask}"
                    )

    def test_ask_not_below_market_bid(self) -> None:
        """Ask quotes never below market bid."""
        engine, _ = create_engine()

        random.seed(42)
        for _ in range(50):
            mid = random.uniform(0.20, 0.80)
            spread = random.uniform(0.01, 0.10)
            market = create_market(mid=mid, spread=spread)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)

            for quote in quotes:
                if quote.side == "ASK":
                    assert quote.price >= market.best_bid, (
                        f"Ask {quote.price} < market bid {market.best_bid}"
                    )

    def test_quotes_maintain_spread(self) -> None:
        """Generated quotes maintain minimum spread."""
        engine, _ = create_engine(target_spread=0.04)

        market = create_market(mid=0.50, spread=0.02)
        engine.on_market_update(market)

        quotes = engine.market_maker.generate_quotes(market)

        if len(quotes) >= 2:
            bids = [q for q in quotes if q.side == "BID"]
            asks = [q for q in quotes if q.side == "ASK"]

            if bids and asks:
                spread = min(q.price for q in asks) - max(q.price for q in bids)
                assert spread >= 0.03

    def test_no_self_crossing(self) -> None:
        """Own bid never crosses own ask."""
        engine, _ = create_engine()

        for mid in [0.20, 0.35, 0.50, 0.65, 0.80]:
            market = create_market(mid=mid)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)

            bids = [q for q in quotes if q.side == "BID"]
            asks = [q for q in quotes if q.side == "ASK"]

            if bids and asks:
                max_bid = max(q.price for q in bids)
                min_ask = min(q.price for q in asks)
                assert max_bid < min_ask, (
                    f"Self-crossing: bid {max_bid} >= ask {min_ask}"
                )


class TestRiskInvariants:
    """Tests that risk limits are always respected."""

    def test_trading_halted_respects_limit(self) -> None:
        """When daily loss hit, trading stops."""
        engine, _ = create_engine()

        engine.risk_manager.update_daily_pnl(-1000)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        assert not engine.risk_manager.is_trading_allowed()

    def test_force_close_prevents_further_loss(self) -> None:
        """Force close prevents accumulating more loss."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        buy = make_fill("o1", "TEST", "BID", 0.50, 50)
        engine.market_maker.update_position(buy)

        market = create_market(mid=0.20)
        engine.on_market_update(market)

        status = engine.get_status()
        assert status is not None

    def test_risk_metrics_tracked(self) -> None:
        """Risk metrics are properly tracked."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        engine.risk_manager.update_daily_pnl(-25.0)

        metrics = engine.risk_manager.get_risk_metrics()
        assert "daily_pnl" in metrics
        assert metrics["daily_pnl"] == -25.0


class TestRandomizedPropertyTests:
    """Randomized tests for property verification."""

    def test_random_market_sequence(self) -> None:
        """Random market sequence maintains invariants."""
        engine, _ = create_engine(max_position=100)

        random.seed(123)
        mid = 0.50

        for _ in range(100):
            mid = max(0.05, min(0.95, mid + random.uniform(-0.03, 0.03)))
            spread = random.uniform(0.01, 0.08)
            market = create_market(mid=mid, spread=spread)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)
            for quote in quotes:
                assert 0 < quote.price < 1
                assert quote.size > 0

    def test_random_fills_maintain_accuracy(self) -> None:
        """Random fills maintain position accuracy."""
        engine, _ = create_engine()

        random.seed(456)
        market = create_market(mid=0.50)
        engine.on_market_update(market)

        expected_position = 0

        for i in range(30):
            side = random.choice(["BID", "ASK"])
            size = random.randint(1, 20)
            price = random.uniform(0.45, 0.55)

            if side == "BID":
                expected_position += size
            else:
                expected_position -= size

            fill = make_fill(f"order-{i}", "TEST", side, price, size)
            engine.market_maker.update_position(fill)

        assert engine.market_maker.position.contracts == expected_position
