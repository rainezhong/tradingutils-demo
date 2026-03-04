"""Tests for signal stability filter in crypto latency detector."""

import pytest
import time
from datetime import datetime, timedelta

from strategies.crypto_latency.config import CryptoLatencyConfig
from strategies.crypto_latency.detector import LatencyDetector, OpportunityType
from strategies.crypto_latency.market_scanner import CryptoMarket


@pytest.fixture
def config():
    """Create test configuration."""
    return CryptoLatencyConfig(
        min_edge_pct=0.15,
        signal_stability_enabled=True,
        signal_stability_duration_sec=3.0,
        signal_stability_direction_consistent=True,
        slippage_adjusted_edge=False,  # Disable for these tests
        min_time_to_expiry_sec=120,
        max_time_to_expiry_sec=900,
    )


@pytest.fixture
def detector(config):
    """Create detector instance."""
    return LatencyDetector(config)


@pytest.fixture
def mock_market():
    """Create a mock crypto market with 300 seconds to expiry."""
    return CryptoMarket(
        condition_id="TEST-MARKET-123",
        question="Will BTC be above $50000?",
        asset="BTC",
        strike_price=50000.0,
        expiration_time=datetime.utcnow() + timedelta(seconds=300),
        yes_token_id="yes_token",
        no_token_id="no_token",
        current_yes_price=0.40,  # Market thinks 40% chance
        current_no_price=0.60,
    )


class TestSignalStabilityRequiresDuration:
    """Test that signal stability requires edge to persist for configured duration."""

    def test_signal_not_stable_initially(self, detector, mock_market):
        """First detection should return None due to lack of stability."""
        # Spot price way above strike should give large edge
        # With strike=50000, spot=55000, implied should be > 0.60
        # Market is at 0.40, so raw edge should be ~0.20+
        result = detector.detect(
            market=mock_market,
            spot_price=55000.0,
            spot_timestamp=time.time(),
        )

        # Should return None because signal hasn't been stable for 3 seconds yet
        assert result is None

    def test_signal_becomes_stable_after_duration(self, detector, mock_market):
        """Signal should be accepted after being stable for required duration."""
        base_time = time.time()

        # Use spot=50075 which gives implied prob ~86% (within 5%-95% bounds)
        # Market is at 40%, so edge is ~46% which exceeds 15% threshold
        # Simulate 4 seconds of stable signal (every 0.5 sec)
        for i in range(8):
            result = detector.detect(
                market=mock_market,
                spot_price=50075.0,
                spot_timestamp=base_time + (i * 0.5),
            )

        # After 3+ seconds of stable signal, should get an opportunity
        assert result is not None
        assert result.opportunity_type == OpportunityType.BUY_YES


class TestSignalStabilityResetsOnDirectionChange:
    """Test that stability timer resets when direction flips."""

    def test_direction_change_resets_stability(self, detector, mock_market):
        """Direction change should reset the stability timer."""
        base_time = time.time()

        # Build up 2.5 seconds of YES edge
        for i in range(5):
            detector.detect(
                market=mock_market,
                spot_price=55000.0,  # Above strike -> YES edge
                spot_timestamp=base_time + (i * 0.5),
            )

        # Now flip to NO edge (spot below strike)
        mock_market.current_yes_price = 0.70  # Market thinks 70% YES
        for i in range(2):
            result = detector.detect(
                market=mock_market,
                spot_price=45000.0,  # Below strike -> NO edge
                spot_timestamp=base_time + 2.5 + (i * 0.5),
            )

        # Should return None because direction changed and new signal isn't stable yet
        assert result is None

    def test_direction_must_be_consistent_when_enabled(self, detector):
        """Test that direction consistency is enforced."""
        ticker = "TEST-TICKER"
        base_time = time.time()

        # Build history in YES direction
        for i in range(4):
            detector._check_signal_stability(
                ticker=ticker,
                edge=0.20,
                direction="yes",
                timestamp=base_time + (i * 0.5),
            )

        # Try to get stability with NO direction
        result = detector._check_signal_stability(
            ticker=ticker,
            edge=0.20,
            direction="no",
            timestamp=base_time + 2.5,
        )

        # Should fail because direction changed
        assert result is False


class TestSlippageAdjustedEdge:
    """Test slippage adjustment calculations."""

    def test_slippage_reduces_edge(self):
        """Slippage adjustment should reduce effective edge."""
        config = CryptoLatencyConfig(
            slippage_adjusted_edge=True,
            expected_slippage_cents=3,  # 3 cents = 0.03
        )
        detector = LatencyDetector(config)

        raw_edge = 0.15  # 15%
        adjusted = detector._calculate_slippage_adjusted_edge(raw_edge, "yes")

        # Should be 15% - 3% = 12%
        assert adjusted == pytest.approx(0.12, abs=0.001)

    def test_slippage_disabled_returns_raw_edge(self):
        """With slippage disabled, should return raw edge."""
        config = CryptoLatencyConfig(
            slippage_adjusted_edge=False,
            expected_slippage_cents=3,
        )
        detector = LatencyDetector(config)

        raw_edge = 0.15
        adjusted = detector._calculate_slippage_adjusted_edge(raw_edge, "yes")

        assert adjusted == raw_edge

    def test_edge_below_threshold_after_slippage_rejected(self):
        """Edge that falls below threshold after slippage should be rejected."""
        config = CryptoLatencyConfig(
            min_edge_pct=0.15,
            slippage_adjusted_edge=True,
            expected_slippage_cents=5,  # 5 cents = 0.05
            signal_stability_enabled=False,  # Disable for this test
            min_time_to_expiry_sec=120,
            max_time_to_expiry_sec=900,
        )
        detector = LatencyDetector(config)

        # Create market with 300 sec to expiry and low yes price
        market = CryptoMarket(
            condition_id="TEST-MARKET-123",
            question="Will BTC be above $50000?",
            asset="BTC",
            strike_price=50000.0,
            expiration_time=datetime.utcnow() + timedelta(seconds=300),
            yes_token_id="yes_token",
            no_token_id="no_token",
            current_yes_price=0.35,
            current_no_price=0.65,
        )

        # This should be rejected because raw edge - 5% slippage < 15% threshold
        result = detector.detect(
            market=market,
            spot_price=52500.0,  # Slightly above strike
            spot_timestamp=time.time(),
        )

        assert result is None
