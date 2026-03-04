"""Unit tests for Kalshi repricing lag model.

Tests the velocity-based orderbook staleness check that rejects fills when
Kalshi prices are stale relative to rapid spot price movements.
"""

import pytest
from datetime import datetime, timezone
from src.backtesting.repricing_lag import (
    KalshiRepricingConfig,
    check_kalshi_staleness,
)
from src.backtesting.fill_model import ImmediateFillModel
from src.core.models import MarketState, Fill
from strategies.base import Signal


class TestKalshiRepricingConfig:
    """Test KalshiRepricingConfig dataclass defaults and creation."""

    def test_default_config(self):
        """Test default configuration values."""
        config = KalshiRepricingConfig()
        assert config.enable_repricing_lag is False
        assert config.max_staleness_sec == 5.0
        assert config.min_spot_velocity_threshold == 0.01

    def test_custom_config(self):
        """Test custom configuration values."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=10.0,
            min_spot_velocity_threshold=0.05,
        )
        assert config.enable_repricing_lag is True
        assert config.max_staleness_sec == 10.0
        assert config.min_spot_velocity_threshold == 0.05


class TestCheckKalshiStaleness:
    """Test the check_kalshi_staleness function."""

    def test_disabled_always_allows_fill(self):
        """When disabled, always return True (allow fill)."""
        config = KalshiRepricingConfig(enable_repricing_lag=False)
        context = {
            "kraken_spot": 67800.0,
            "kraken_ts": 1000.0,
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1010.0,
            prev_spot_price=67700.0,
            prev_spot_ts=995.0,
        )
        assert result is True

    def test_no_spot_data_allows_fill(self):
        """When spot data is missing, allow fill (graceful degradation)."""
        config = KalshiRepricingConfig(enable_repricing_lag=True)
        context = {}  # No kraken_spot or kraken_ts
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1000.0,
            prev_spot_price=67700.0,
            prev_spot_ts=995.0,
        )
        assert result is True

    def test_first_frame_allows_fill(self):
        """First frame (no prev price) should allow fill."""
        config = KalshiRepricingConfig(enable_repricing_lag=True)
        context = {
            "kraken_spot": 67800.0,
            "kraken_ts": 1000.0,
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1000.0,
            prev_spot_price=None,
            prev_spot_ts=None,
        )
        assert result is True

    def test_slow_spot_movement_allows_fill(self):
        """Slow spot movement (below threshold) should allow fill."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,  # 0.1 cents/sec threshold
        )
        # Spot moved from $67800 to $67800.005 (0.5 cents) in 10 seconds
        # Velocity = 0.05 cents/sec, below 0.1 threshold
        context = {
            "kraken_spot": 67800.005,
            "kraken_ts": 1010.0,
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1010.0,
            prev_spot_price=67800.0,
            prev_spot_ts=1000.0,
        )
        assert result is True

    def test_fast_movement_fresh_orderbook_allows_fill(self):
        """Fast spot movement but fresh orderbook should allow fill."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,  # 0.1 cents/sec
        )
        # Spot moved $5 (500 cents) in 10 seconds = 50 cents/sec (fast!)
        # But orderbook is fresh (kraken_ts == current_ts)
        context = {
            "kraken_spot": 67805.0,
            "kraken_ts": 1010.0,  # Same as current_ts
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1010.0,
            prev_spot_price=67800.0,
            prev_spot_ts=1000.0,
        )
        assert result is True

    def test_fast_movement_stale_orderbook_rejects_fill(self):
        """Fast spot movement with stale orderbook should reject fill."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,  # 0.1 cents/sec
        )
        # Spot moved $5 (500 cents) in 10 seconds = 50 cents/sec (fast!)
        # Orderbook is stale (kraken_ts is 7s behind current_ts)
        context = {
            "kraken_spot": 67805.0,
            "kraken_ts": 1003.0,  # 7 seconds stale (> 5s max)
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1010.0,
            prev_spot_price=67800.0,
            prev_spot_ts=1000.0,
        )
        assert result is False  # Reject fill

    def test_borderline_velocity_threshold(self):
        """Test velocity exactly at threshold."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=1.0,  # 1.0 cent/sec
        )
        # Spot moved $1 (100 cents) in 100 seconds = 1.0 cent/sec (exactly at threshold)
        # Orderbook is 6s stale (> 5s max)
        context = {
            "kraken_spot": 67801.0,
            "kraken_ts": 1094.0,  # 6s stale
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1100.0,
            prev_spot_price=67800.0,
            prev_spot_ts=1000.0,
        )
        # Velocity > threshold (1.0 > 1.0 is False), so should allow
        assert result is True

    def test_borderline_staleness_threshold(self):
        """Test staleness exactly at threshold."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,
        )
        # Fast movement: $5 (500 cents) in 10s = 50 cents/sec
        # Staleness exactly at threshold (5.0s)
        context = {
            "kraken_spot": 67805.0,
            "kraken_ts": 1005.0,  # Exactly 5s stale
        }
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1010.0,
            prev_spot_price=67800.0,
            prev_spot_ts=1000.0,
        )
        # Staleness > threshold (5.0 > 5.0 is False), so should allow
        assert result is True

    def test_zero_time_delta_allows_fill(self):
        """Zero or negative time delta should allow fill."""
        config = KalshiRepricingConfig(enable_repricing_lag=True)
        context = {
            "kraken_spot": 67805.0,
            "kraken_ts": 1000.0,
        }
        # Same timestamp as previous
        result = check_kalshi_staleness(
            context=context,
            config=config,
            current_ts=1000.0,
            prev_spot_price=67800.0,
            prev_spot_ts=1000.0,
        )
        assert result is True


class TestImmediateFillModelWithRepricing:
    """Test ImmediateFillModel integration with repricing lag."""

    def test_disabled_by_default(self):
        """Repricing lag should be disabled by default."""
        model = ImmediateFillModel()
        assert model._repricing_config.enable_repricing_lag is False

    def test_allows_fill_when_disabled(self):
        """Should allow fill when repricing lag is disabled."""
        model = ImmediateFillModel()

        signal = Signal(
            ticker="KXBTC15M-26FEB180045-45",
            side="BID",
            price=0.50,
            size=10,
            confidence=1.0,
            reason="test signal",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
        )

        market = MarketState(
            ticker="KXBTC15M-26FEB180045-45",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
            bid=0.48,
            ask=0.50,
        )

        context = {
            "kraken_spot": 67805.0,
            "kraken_ts": 1003.0,  # Stale
        }

        # Even with stale orderbook, should fill because repricing is disabled
        fill = model.simulate_fill(signal, market, context)
        assert fill is not None

    def test_rejects_fill_when_stale(self):
        """Should reject fill when orderbook is stale and spot moving fast."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,
        )
        model = ImmediateFillModel(repricing_config=config)

        signal = Signal(
            ticker="KXBTC15M-26FEB180045-45",
            side="BID",
            price=0.50,
            size=10,
            confidence=1.0,
            reason="test signal",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
        )

        market = MarketState(
            ticker="KXBTC15M-26FEB180045-45",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
            bid=0.48,
            ask=0.50,
        )

        # First call: establish prev_spot_price
        context1 = {
            "kraken_spot": 67800.0,
            "kraken_ts": 1000.0,
        }
        fill1 = model.simulate_fill(signal, market, context1)
        assert fill1 is not None  # First frame always allows

        # Second call: fast movement + stale orderbook
        market2 = MarketState(
            ticker="KXBTC15M-26FEB180045-45",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
            bid=0.48,
            ask=0.50,
        )
        context2 = {
            "kraken_spot": 67805.0,  # Moved $5 in 10s = 50 cents/sec
            "kraken_ts": 1003.0,  # 7s stale (> 5s max)
        }
        fill2 = model.simulate_fill(signal, market2, context2)
        assert fill2 is None  # Should reject

    def test_allows_fill_when_fresh(self):
        """Should allow fill when orderbook is fresh despite fast movement."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,
        )
        model = ImmediateFillModel(repricing_config=config)

        signal = Signal(
            ticker="KXBTC15M-26FEB180045-45",
            side="BID",
            price=0.50,
            size=10,
            confidence=1.0,
            reason="test signal",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
        )

        market = MarketState(
            ticker="KXBTC15M-26FEB180045-45",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
            bid=0.48,
            ask=0.50,
        )

        # First call
        context1 = {
            "kraken_spot": 67800.0,
            "kraken_ts": 1000.0,
        }
        fill1 = model.simulate_fill(signal, market, context1)
        assert fill1 is not None

        # Second call: fast movement but fresh orderbook
        market2 = MarketState(
            ticker="KXBTC15M-26FEB180045-45",
            timestamp=datetime.fromtimestamp(1010.0, tz=timezone.utc),
            bid=0.48,
            ask=0.50,
        )
        context2 = {
            "kraken_spot": 67805.0,  # Moved $5 in 10s = 50 cents/sec
            "kraken_ts": 1010.0,  # Fresh (0s stale)
        }
        fill2 = model.simulate_fill(signal, market2, context2)
        assert fill2 is not None  # Should allow

    def test_state_preservation_across_calls(self):
        """Test that prev_spot_price state is preserved across calls."""
        config = KalshiRepricingConfig(
            enable_repricing_lag=True,
            max_staleness_sec=5.0,
            min_spot_velocity_threshold=0.1,
        )
        model = ImmediateFillModel(repricing_config=config)

        signal = Signal(
            ticker="KXBTC15M-26FEB180045-45",
            side="BID",
            price=0.50,
            size=10,
            confidence=1.0,
            reason="test signal",
            timestamp=datetime.fromtimestamp(1000.0, tz=timezone.utc),
        )

        market = MarketState(
            ticker="KXBTC15M-26FEB180045-45",
            timestamp=datetime.fromtimestamp(1000.0, tz=timezone.utc),
            bid=0.48,
            ask=0.50,
        )

        # First call: set prev_spot_price = 67800
        context1 = {"kraken_spot": 67800.0, "kraken_ts": 1000.0}
        model.simulate_fill(signal, market, context1)
        assert model._prev_spot_price == 67800.0
        assert model._prev_spot_ts == 1000.0

        # Second call: update prev_spot_price = 67805
        context2 = {"kraken_spot": 67805.0, "kraken_ts": 1010.0}
        model.simulate_fill(signal, market, context2)
        assert model._prev_spot_price == 67805.0
        assert model._prev_spot_ts == 1010.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
