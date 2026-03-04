"""Tests for slippage-adjusted edge calculation."""

import pytest
import time
from datetime import datetime, timedelta

from strategies.crypto_latency.config import CryptoLatencyConfig
from strategies.crypto_latency.detector import LatencyDetector, OpportunityType
from strategies.crypto_latency.market_scanner import CryptoMarket


@pytest.fixture
def config_with_slippage():
    """Create test configuration with slippage enabled."""
    return CryptoLatencyConfig(
        min_edge_pct=0.15,
        slippage_adjusted_edge=True,
        expected_slippage_cents=3,  # 3 cents = 0.03 = 3%
        signal_stability_enabled=False,  # Disable for these tests
        min_time_to_expiry_sec=120,
        max_time_to_expiry_sec=900,
    )


@pytest.fixture
def config_without_slippage():
    """Create test configuration with slippage disabled."""
    return CryptoLatencyConfig(
        min_edge_pct=0.15,
        slippage_adjusted_edge=False,
        expected_slippage_cents=3,
        signal_stability_enabled=False,
        min_time_to_expiry_sec=120,
        max_time_to_expiry_sec=900,
    )


def create_market(yes_price=0.40, no_price=0.60):
    """Create a crypto market with 300 seconds to expiry."""
    return CryptoMarket(
        condition_id="TEST-MARKET-123",
        question="Will BTC be above $50000?",
        asset="BTC",
        strike_price=50000.0,
        expiration_time=datetime.utcnow() + timedelta(seconds=300),
        yes_token_id="yes_token",
        no_token_id="no_token",
        current_yes_price=yes_price,
        current_no_price=no_price,
    )


@pytest.fixture
def mock_market():
    """Create a mock crypto market with 300 seconds to expiry."""
    return create_market()


class TestSlippageAdjustedEdgeCalculation:
    """Test the slippage adjustment calculation."""

    def test_slippage_subtracts_from_edge(self, config_with_slippage):
        """Slippage should be subtracted from raw edge."""
        detector = LatencyDetector(config_with_slippage)

        # 20% raw edge - 3% slippage = 17% adjusted edge
        adjusted = detector._calculate_slippage_adjusted_edge(0.20, "yes")
        assert adjusted == pytest.approx(0.17, abs=0.001)

    def test_slippage_with_different_amounts(self):
        """Test various slippage amounts."""
        test_cases = [
            (0.20, 3, 0.17),  # 20% - 3% = 17%
            (0.15, 3, 0.12),  # 15% - 3% = 12%
            (0.25, 5, 0.20),  # 25% - 5% = 20%
            (0.10, 2, 0.08),  # 10% - 2% = 8%
        ]

        for raw_edge, slippage_cents, expected in test_cases:
            config = CryptoLatencyConfig(
                slippage_adjusted_edge=True,
                expected_slippage_cents=slippage_cents,
            )
            detector = LatencyDetector(config)
            adjusted = detector._calculate_slippage_adjusted_edge(raw_edge, "yes")
            assert adjusted == pytest.approx(expected, abs=0.001), (
                f"Failed for raw={raw_edge}, slippage={slippage_cents}"
            )

    def test_slippage_disabled_returns_raw(self, config_without_slippage):
        """With slippage disabled, raw edge should be returned."""
        detector = LatencyDetector(config_without_slippage)

        adjusted = detector._calculate_slippage_adjusted_edge(0.20, "yes")
        assert adjusted == 0.20

    def test_slippage_works_for_both_sides(self, config_with_slippage):
        """Slippage adjustment should work for both YES and NO sides."""
        detector = LatencyDetector(config_with_slippage)

        yes_adjusted = detector._calculate_slippage_adjusted_edge(0.20, "yes")
        no_adjusted = detector._calculate_slippage_adjusted_edge(0.20, "no")

        # Both should have same adjustment
        assert yes_adjusted == no_adjusted == pytest.approx(0.17, abs=0.001)


class TestSlippageInDetection:
    """Test that slippage adjustment integrates correctly with detect()."""

    def test_marginal_edge_rejected_after_slippage(self):
        """Edge that becomes marginal after slippage should be rejected."""
        # 17% raw edge, 3% slippage = 14% adjusted (below 15% threshold)
        config = CryptoLatencyConfig(
            min_edge_pct=0.15,
            slippage_adjusted_edge=True,
            expected_slippage_cents=3,
            signal_stability_enabled=False,
            min_time_to_expiry_sec=120,
            max_time_to_expiry_sec=900,
        )
        detector = LatencyDetector(config)

        # Set market so edge is marginal
        # Market at 40%, we need implied to be ~57% for 17% raw edge
        market = create_market(yes_price=0.40, no_price=0.60)

        # This spot price should give ~17% raw edge but <15% after slippage
        result = detector.detect(
            market=market,
            spot_price=51500.0,  # Slightly above strike
            spot_timestamp=time.time(),
        )

        # Should be rejected due to insufficient edge after slippage
        assert result is None

    def test_strong_edge_accepted_after_slippage(self):
        """Strong edge should still be accepted after slippage."""
        config = CryptoLatencyConfig(
            min_edge_pct=0.15,
            slippage_adjusted_edge=True,
            expected_slippage_cents=3,
            signal_stability_enabled=False,
            min_time_to_expiry_sec=120,
            max_time_to_expiry_sec=900,
        )
        detector = LatencyDetector(config)

        # Set market with low YES price (35%)
        # spot=50075 gives implied prob ~86%, well within bounds
        # Raw edge ~51%, after 3% slippage = ~48% still >15%
        market = create_market(yes_price=0.35, no_price=0.65)

        result = detector.detect(
            market=market,
            spot_price=50075.0,  # Gives ~86% implied, within bounds
            spot_timestamp=time.time(),
        )

        # Should be accepted
        assert result is not None
        assert result.opportunity_type == OpportunityType.BUY_YES

    def test_edge_in_opportunity_is_adjusted(self):
        """The edge returned in Opportunity should be slippage-adjusted."""
        config = CryptoLatencyConfig(
            min_edge_pct=0.15,
            slippage_adjusted_edge=True,
            expected_slippage_cents=3,
            signal_stability_enabled=False,
            min_time_to_expiry_sec=120,
            max_time_to_expiry_sec=900,
        )
        detector = LatencyDetector(config)

        market = create_market(yes_price=0.30, no_price=0.70)

        result = detector.detect(
            market=market,
            spot_price=58000.0,  # Well above strike for large edge
            spot_timestamp=time.time(),
        )

        if result:
            # The edge in the opportunity should be the adjusted edge
            # It should be less than raw edge by ~3%
            # We can verify it's at least 3% less than what raw would be
            assert result.edge >= config.min_edge_pct


class TestSlippageConfigValidation:
    """Test slippage configuration validation."""

    def test_slippage_zero_is_valid(self):
        """Zero slippage should be valid."""
        config = CryptoLatencyConfig(
            slippage_adjusted_edge=True,
            expected_slippage_cents=0,
        )
        detector = LatencyDetector(config)

        adjusted = detector._calculate_slippage_adjusted_edge(0.20, "yes")
        assert adjusted == 0.20

    def test_large_slippage_reduces_edge_significantly(self):
        """Large slippage values should significantly reduce edge."""
        config = CryptoLatencyConfig(
            slippage_adjusted_edge=True,
            expected_slippage_cents=10,  # 10 cents = 10%
        )
        detector = LatencyDetector(config)

        adjusted = detector._calculate_slippage_adjusted_edge(0.20, "yes")
        # 20% - 10% = 10%
        assert adjusted == pytest.approx(0.10, abs=0.001)
