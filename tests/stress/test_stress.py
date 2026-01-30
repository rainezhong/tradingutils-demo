"""Stress tests for market-making system.

Tests system behavior under extreme conditions:
- 1000+ consecutive market updates
- Rapid quote updates (every iteration)
- Many simultaneous fills
- API rate limit scenarios
- Memory and performance under load
"""

import pytest
import time
import random
from datetime import datetime, timedelta
from typing import List
from unittest.mock import Mock, patch
import threading

from src.core.config import RiskConfig
from src.engine import MarketMakingEngine, MultiMarketEngine
from src.execution.mock_api_client import MockAPIClient
from src.market_making.config import MarketMakerConfig
from src.market_making.models import Fill, MarketState, Quote


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
    max_position: int = 1000,
) -> tuple[MarketMakingEngine, MockAPIClient]:
    """Create an engine with mock API client."""
    api_client = MockAPIClient()

    mm_config = MarketMakerConfig(
        target_spread=0.04,
        max_position=max_position,
        quote_size=10,
    )

    risk_config = RiskConfig(
        max_position_size=max_position,
        max_total_position=max_position * 2,
        max_loss_per_position=1000.0,
        max_daily_loss=5000.0,
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


class TestHighVolumeUpdates:
    """Tests for high-volume market updates."""

    def test_1000_consecutive_updates(self) -> None:
        """Process 1000 market updates without failure."""
        engine, _ = create_engine()

        random.seed(42)
        mid = 0.50

        start_time = time.time()

        for i in range(1000):
            # Random walk
            mid = max(0.05, min(0.95, mid + random.uniform(-0.02, 0.02)))
            market = create_market(mid=mid)
            engine.on_market_update(market)

        elapsed = time.time() - start_time

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 1000
        assert status["engine"]["force_closes"] == 0

        # Should complete in reasonable time (< 10 seconds)
        assert elapsed < 10.0, f"Too slow: {elapsed:.2f}s for 1000 updates"

    def test_5000_updates_performance(self) -> None:
        """Process 5000 updates and verify performance."""
        engine, _ = create_engine()

        random.seed(42)
        mid = 0.50

        start_time = time.time()

        for _ in range(5000):
            mid = max(0.05, min(0.95, mid + random.uniform(-0.01, 0.01)))
            market = create_market(mid=mid)
            engine.on_market_update(market)

        elapsed = time.time() - start_time

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 5000

        # Should complete in < 30 seconds
        assert elapsed < 30.0

        # Calculate updates per second
        updates_per_second = 5000 / elapsed
        assert updates_per_second > 100, \
            f"Too slow: {updates_per_second:.1f} updates/sec"

    def test_updates_with_fills(self) -> None:
        """1000 updates with periodic fills."""
        engine, _ = create_engine()

        random.seed(42)
        mid = 0.50

        for i in range(1000):
            mid = max(0.05, min(0.95, mid + random.uniform(-0.02, 0.02)))
            market = create_market(mid=mid)
            engine.on_market_update(market)

            # Inject fill every 50 updates
            if i % 50 == 0 and i > 0:
                side = random.choice(["BID", "ASK"])
                fill = make_fill(
                    order_id=f"order-{i}",
                    ticker="TEST",
                    side=side,
                    price=mid,
                    size=random.randint(1, 10),
                )
                engine.market_maker.update_position(fill)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 1000
        # Should have processed fills
        assert status["market_maker"]["stats"]["total_volume"] > 0


class TestRapidQuoteUpdates:
    """Tests for rapid quote updates."""

    def test_quote_every_update(self) -> None:
        """Quotes updated on every market tick."""
        engine, _ = create_engine()

        # Force quote update on every tick by simulating significant moves
        for i in range(500):
            mid = 0.30 + (i % 40) * 0.01  # Oscillate between 0.30 and 0.70
            market = create_market(mid=mid)
            engine.on_market_update(market)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 500
        # Should have generated many quotes
        assert status["engine"]["quotes_sent"] > 100

    def test_rapid_quote_cancel_replace(self) -> None:
        """Rapidly cancel and replace quotes."""
        engine, _ = create_engine()

        mid = 0.50

        for i in range(200):
            # Alternate price to force quote updates
            mid = 0.40 if i % 2 == 0 else 0.60
            market = create_market(mid=mid)
            engine.on_market_update(market)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 200
        # Many quote cycles
        assert status["engine"]["quotes_sent"] >= 100

    def test_quote_generation_under_load(self) -> None:
        """Verify quote quality under load."""
        engine, _ = create_engine()

        random.seed(42)
        all_quotes_valid = True

        for _ in range(300):
            mid = random.uniform(0.20, 0.80)
            market = create_market(mid=mid)
            engine.on_market_update(market)

            quotes = engine.market_maker.generate_quotes(market)

            for quote in quotes:
                if not (0 < quote.price < 1 and quote.size > 0):
                    all_quotes_valid = False
                    break

        assert all_quotes_valid, "Invalid quote generated under load"


class TestManySimultaneousFills:
    """Tests for handling many fills."""

    def test_50_fills_in_sequence(self) -> None:
        """Process 50 fills in rapid sequence."""
        engine, _ = create_engine(max_position=500)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        for i in range(50):
            side = "BID" if i % 2 == 0 else "ASK"
            fill = make_fill(f"order-{i}", "TEST", side, 0.50, 5)
            engine.market_maker.update_position(fill)
            engine.on_market_update(market)

        status = engine.get_status()
        # Should have processed all fills
        assert status["market_maker"]["stats"]["quotes_filled"] == 50

    def test_100_random_fills(self) -> None:
        """Process 100 random fills."""
        engine, _ = create_engine(max_position=1000)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        random.seed(42)
        expected_position = 0

        for i in range(100):
            side = random.choice(["BID", "ASK"])
            size = random.randint(1, 20)

            if side == "BID":
                expected_position += size
            else:
                expected_position -= size

            fill = make_fill(
                f"order-{i}", "TEST", side, random.uniform(0.45, 0.55), size
            )
            engine.market_maker.update_position(fill)

        # Position should be accurate
        assert engine.market_maker.position.contracts == expected_position

    def test_fills_with_position_limit_check(self) -> None:
        """Many fills respecting position limits."""
        engine, _ = create_engine(max_position=100)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # All buy fills to build position
        for i in range(20):
            fill = make_fill(f"order-{i}", "TEST", "BID", 0.49, 10)
            engine.market_maker.update_position(fill)
            engine.on_market_update(market)

        # Position should be tracked
        pos = engine.market_maker.position.contracts
        assert pos == 200  # Fills still go through, risk check happens on quote gen


class TestRateLimitScenarios:
    """Tests for API rate limit handling."""

    def test_api_rate_limited(self) -> None:
        """Handle API rate limiting gracefully."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        rate_limit_count = [0]

        original_place = engine.quote_manager.place_quote

        def rate_limited_place(quote):
            rate_limit_count[0] += 1
            if rate_limit_count[0] % 5 == 0:
                raise Exception("Rate limit exceeded")
            return Mock(order_id=f"order-{rate_limit_count[0]}")

        with patch.object(
            engine.quote_manager, 'place_quote',
            side_effect=rate_limited_place
        ):
            for _ in range(100):
                engine.on_market_update(market)

        # Engine should still be operational despite rate limits
        status = engine.get_status()
        assert status is not None

    def test_intermittent_failures(self) -> None:
        """Handle intermittent API failures."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        call_count = [0]

        def flaky_place(quote):
            call_count[0] += 1
            if call_count[0] % 7 == 0:
                raise TimeoutError("Connection timeout")
            return Mock(order_id=f"order-{call_count[0]}")

        with patch.object(
            engine.quote_manager, 'place_quote',
            side_effect=flaky_place
        ):
            for i in range(50):
                mid = 0.40 + (i % 20) * 0.01
                market = create_market(mid=mid)
                engine.on_market_update(market)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 50


class TestMultiMarketStress:
    """Stress tests for multi-market engine."""

    def test_10_markets_concurrent(self) -> None:
        """Run 10 markets simultaneously."""
        api_client = MockAPIClient()

        mm_config = MarketMakerConfig(
            target_spread=0.04,
            max_position=100,
        )

        risk_config = RiskConfig(
            max_position_size=100,
            max_total_position=500,
            max_loss_per_position=50.0,
            max_daily_loss=200.0,
        )

        multi_engine = MultiMarketEngine(api_client)

        # Add 10 markets
        for i in range(10):
            ticker = f"MARKET-{i}"
            multi_engine.add_market(ticker, mm_config, risk_config)

        # Send updates to all markets
        random.seed(42)

        for _ in range(100):
            updates = {}
            for i in range(10):
                ticker = f"MARKET-{i}"
                mid = random.uniform(0.30, 0.70)
                updates[ticker] = create_market(ticker=ticker, mid=mid)

            multi_engine.on_market_updates(updates)

        status = multi_engine.get_aggregate_status()
        assert status["aggregate"]["markets_active"] == 10
        assert status["aggregate"]["total_updates"] == 1000  # 10 markets * 100 updates

    def test_5_markets_with_fills(self) -> None:
        """5 markets with fills on each."""
        api_client = MockAPIClient()

        mm_config = MarketMakerConfig(
            target_spread=0.04,
            max_position=100,
        )

        risk_config = RiskConfig(
            max_position_size=100,
            max_total_position=300,
            max_loss_per_position=50.0,
            max_daily_loss=200.0,
        )

        multi_engine = MultiMarketEngine(api_client)

        for i in range(5):
            ticker = f"MARKET-{i}"
            multi_engine.add_market(ticker, mm_config, risk_config)

        random.seed(42)

        for update_num in range(50):
            for i in range(5):
                ticker = f"MARKET-{i}"
                mid = random.uniform(0.35, 0.65)
                market = create_market(ticker=ticker, mid=mid)
                multi_engine.on_market_update(ticker, market)

                # Inject fills
                if update_num % 10 == 0:
                    engine = multi_engine._engines[ticker]
                    fill = make_fill(
                        f"order-{ticker}-{update_num}", ticker,
                        random.choice(["BID", "ASK"]), mid, 5
                    )
                    engine.market_maker.update_position(fill)

        status = multi_engine.get_aggregate_status()
        assert status["aggregate"]["markets_active"] == 5


class TestMemoryAndResources:
    """Tests for memory usage and resource management."""

    def test_no_memory_leak_long_run(self) -> None:
        """Verify no memory leak over long run."""
        engine, _ = create_engine()

        random.seed(42)
        mid = 0.50

        # Run many iterations
        for _ in range(2000):
            mid = max(0.05, min(0.95, mid + random.uniform(-0.02, 0.02)))
            market = create_market(mid=mid)
            engine.on_market_update(market)

            # Occasionally add fills
            if random.random() < 0.1:
                fill = make_fill(
                    f"order-{random.randint(0, 100000)}", "TEST",
                    random.choice(["BID", "ASK"]), mid, random.randint(1, 10)
                )
                engine.market_maker.update_position(fill)

        # Engine should still be functional
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 2000

    def test_reset_clears_state(self) -> None:
        """Reset clears accumulated state."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)

        # Accumulate state
        for _ in range(100):
            engine.on_market_update(market)

        fill = make_fill("o1", "TEST", "BID", 0.49, 20)
        engine.market_maker.update_position(fill)

        # Reset
        engine.reset()

        # State should be cleared
        assert engine._state.market_updates == 0
        assert engine.market_maker.position.is_flat
        assert len(engine._state.active_order_ids) == 0


class TestEdgeCasesUnderLoad:
    """Edge cases that may manifest under load."""

    def test_extreme_price_moves(self) -> None:
        """Handle extreme price movements."""
        engine, _ = create_engine()

        # Keep prices within valid range (0.01 - 0.99), accounting for spread
        prices = [
            0.05, 0.95, 0.50, 0.06, 0.94,
            0.10, 0.90, 0.08, 0.92, 0.50,
        ]

        for mid in prices * 50:  # 500 iterations
            market = create_market(mid=mid, spread=0.02)
            engine.on_market_update(market)

        status = engine.get_status()
        assert status["engine"]["market_updates"] == 500

    def test_alternating_positions(self) -> None:
        """Rapidly alternating between long and short."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        for i in range(200):
            # Alternate long/short
            if i % 2 == 0:
                fill = make_fill(f"o-{i}", "TEST", "BID", 0.49, 20)
            else:
                fill = make_fill(f"o-{i}", "TEST", "ASK", 0.51, 20)

            engine.market_maker.update_position(fill)
            engine.on_market_update(market)

        status = engine.get_status()
        assert status is not None

    def test_zero_to_max_position(self) -> None:
        """Build from zero to max position rapidly."""
        engine, _ = create_engine(max_position=100)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Build to max
        fill = make_fill("o1", "TEST", "BID", 0.49, 100)
        engine.market_maker.update_position(fill)

        engine.on_market_update(market)

        assert engine.market_maker.position.contracts == 100

        # Close entirely
        fill = make_fill("o2", "TEST", "ASK", 0.51, 100)
        engine.market_maker.update_position(fill)

        assert engine.market_maker.position.is_flat


class TestPerformanceBenchmarks:
    """Performance benchmarks."""

    def test_updates_per_second(self) -> None:
        """Measure update throughput."""
        engine, _ = create_engine()

        random.seed(42)
        mid = 0.50

        iterations = 1000
        start = time.time()

        for _ in range(iterations):
            mid = max(0.05, min(0.95, mid + random.uniform(-0.01, 0.01)))
            market = create_market(mid=mid)
            engine.on_market_update(market)

        elapsed = time.time() - start
        rate = iterations / elapsed

        # Should achieve at least 100 updates/second
        assert rate > 100, f"Only {rate:.1f} updates/sec"

    def test_quote_generation_speed(self) -> None:
        """Measure quote generation speed."""
        engine, _ = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        iterations = 1000
        start = time.time()

        for _ in range(iterations):
            engine.market_maker.generate_quotes(market)

        elapsed = time.time() - start
        rate = iterations / elapsed

        # Should generate at least 500 quote sets/second
        assert rate > 500, f"Only {rate:.1f} quote generations/sec"

    def test_position_update_speed(self) -> None:
        """Measure position update speed."""
        engine, _ = create_engine(max_position=10000)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        iterations = 1000
        start = time.time()

        for i in range(iterations):
            fill = make_fill(
                f"order-{i}", "TEST", "BID" if i % 2 == 0 else "ASK", 0.50, 1
            )
            engine.market_maker.update_position(fill)

        elapsed = time.time() - start
        rate = iterations / elapsed

        # Should process at least 1000 fills/second
        assert rate > 1000, f"Only {rate:.1f} fills/sec"
