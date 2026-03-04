"""Tests for Total Points Over/Under Strategy."""

import pytest

from strategies.total_points_strategy import (
    TotalPointsConfig,
    TotalPointsSignal,
    TotalPointsStrategy,
    extract_line_from_ticker,
)


class TestExtractLineFromTicker:
    """Tests for extract_line_from_ticker utility."""

    def test_integer_line(self):
        assert extract_line_from_ticker("KXNBATOTAL-26JAN22LALLAC-223") == 223.0

    def test_decimal_line(self):
        assert extract_line_from_ticker("KXNBATOTAL-26JAN22LALLAC-223.5") == 223.5

    def test_no_line(self):
        assert extract_line_from_ticker("KXNBATOTAL-26JAN22LALLAC") is None

    def test_different_format(self):
        assert extract_line_from_ticker("TOTAL-GAME-215") == 215.0


class TestTotalPointsConfig:
    """Tests for configuration dataclass."""

    def test_default_values(self):
        config = TotalPointsConfig()
        assert config.min_edge_cents == 3.0
        assert config.position_size == 10
        assert config.max_period == 3
        assert config.nba_avg_pace == 4.6
        # Optimized defaults from parameter sweep
        assert config.second_half_boost == 0.0
        assert not config.enable_second_half_boost
        assert config.pace_blend_end == 0.5
        assert config.slow_game_extra_boost == 10.0

    def test_custom_values(self):
        config = TotalPointsConfig(
            min_edge_cents=5.0,
            second_half_boost=15.0,
            pace_blend_end=0.9,
        )
        assert config.min_edge_cents == 5.0
        assert config.second_half_boost == 15.0
        assert config.pace_blend_end == 0.9


class TestTotalPointsStrategy:
    """Tests for TotalPointsStrategy core functionality."""

    @pytest.fixture
    def strategy(self):
        return TotalPointsStrategy()

    @pytest.fixture
    def strategy_no_boost(self):
        config = TotalPointsConfig(
            enable_second_half_boost=False,
            enable_slow_game_boost=False,
        )
        return TotalPointsStrategy(config)

    def test_time_remaining_fraction_start_of_game(self, strategy):
        # Start of Q1, 12:00 remaining
        frac = strategy.calculate_time_remaining_fraction(1, 720)
        assert frac == pytest.approx(1.0, rel=0.01)

    def test_time_remaining_fraction_halftime(self, strategy):
        # End of Q2, 0:00 remaining
        frac = strategy.calculate_time_remaining_fraction(2, 0)
        assert frac == pytest.approx(0.5, rel=0.01)

    def test_time_remaining_fraction_end_of_game(self, strategy):
        # End of Q4, 0:00 remaining
        frac = strategy.calculate_time_remaining_fraction(4, 0)
        assert frac == pytest.approx(0.001, rel=0.01)  # Clamped minimum

    def test_time_remaining_fraction_mid_q3(self, strategy):
        # Middle of Q3, 6:00 remaining
        frac = strategy.calculate_time_remaining_fraction(3, 360)
        # 2 quarters done + 6 min = 30 min elapsed, 18 min remaining
        # 18/48 = 0.375
        assert frac == pytest.approx(0.375, rel=0.01)

    def test_pace_weight_early_game(self, strategy):
        # Very early, should be 0 (use only NBA average)
        weight = strategy.calculate_pace_weight(0.05)
        assert weight == 0.0

    def test_pace_weight_late_game(self, strategy):
        # Late game, should approach pace_blend_end
        weight = strategy.calculate_pace_weight(1.0)
        assert weight == strategy.config.pace_blend_end

    def test_pace_weight_mid_game(self, strategy):
        # Mid game, interpolated
        weight = strategy.calculate_pace_weight(0.5)
        expected = (
            strategy.config.pace_blend_start
            + (strategy.config.pace_blend_end - strategy.config.pace_blend_start) * 0.5
        )
        assert weight == pytest.approx(expected, rel=0.01)

    def test_blended_pace_early_game(self, strategy):
        # Very early, should use NBA average
        pace = strategy.calculate_blended_pace(0, 0.05)
        assert pace == strategy.config.nba_avg_pace

    def test_blended_pace_fast_game(self, strategy):
        # Fast scoring game at halftime
        # 120 points in 24 min = 5.0 pts/min
        pace = strategy.calculate_blended_pace(120, 0.5)
        # Should be blend of 4.6 (NBA avg) and 5.0 (current)
        weight = strategy.calculate_pace_weight(0.5)
        expected = (1 - weight) * 4.6 + weight * 5.0
        assert pace == pytest.approx(expected, rel=0.01)

    def test_sigma_full_game(self, strategy):
        # Full game remaining: sigma = 10 * sqrt(2 * 1) = 14.14
        sigma = strategy.calculate_sigma(1.0)
        assert sigma == pytest.approx(14.14, rel=0.01)

    def test_sigma_halftime(self, strategy):
        # Half game remaining: sigma = 10 * sqrt(2 * 0.5) = 10.0
        sigma = strategy.calculate_sigma(0.5)
        assert sigma == pytest.approx(10.0, rel=0.01)

    def test_sigma_end_of_game(self, strategy):
        # Near end: sigma approaches 0
        sigma = strategy.calculate_sigma(0.01)
        assert sigma == pytest.approx(1.41, rel=0.05)

    def test_over_probability_50_50(self, strategy_no_boost):
        # Projected total == line should give 50% probability
        # At halftime with 100 points, blended pace predicts:
        # 100 + 4.6 * 24 = 100 + 110.4 = 210.4 pts (roughly)

        # Let's compute what the projection would be
        current_total = 100
        period = 2
        time_remaining_seconds = 0  # End of Q2

        time_frac = strategy_no_boost.calculate_time_remaining_fraction(
            period, time_remaining_seconds
        )
        projected, _, _ = strategy_no_boost.calculate_projected_total(
            current_total, time_frac
        )

        # Test with line equal to projected total
        prob, proj, pace, boost = strategy_no_boost.calculate_over_probability(
            current_total, projected, period, time_remaining_seconds
        )
        assert prob == pytest.approx(0.5, rel=0.05)

    def test_over_probability_high_line(self, strategy_no_boost):
        # High line (well above projected) should have low over probability
        prob, _, _, _ = strategy_no_boost.calculate_over_probability(
            current_total=100,
            line=250.0,  # Very high line
            period=2,
            time_remaining_seconds=0,
        )
        assert prob < 0.3  # Should be low

    def test_over_probability_low_line(self, strategy_no_boost):
        # Low line (well below projected) should have high over probability
        prob, _, _, _ = strategy_no_boost.calculate_over_probability(
            current_total=100,
            line=180.0,  # Low line
            period=2,
            time_remaining_seconds=0,
        )
        assert prob > 0.7  # Should be high

    def test_second_half_boost_applied(self):
        # Test that boost works when explicitly enabled
        time_frac = 0.75  # Q1 with 6 min left

        # Strategy with boost enabled
        strategy_with_boost = TotalPointsStrategy(
            TotalPointsConfig(enable_second_half_boost=True, second_half_boost=13.5)
        )

        # Strategy with boost disabled (default)
        strategy_no_boost = TotalPointsStrategy(
            TotalPointsConfig(enable_second_half_boost=False)
        )

        proj_with_boost, _, boost = strategy_with_boost.calculate_projected_total(
            50, time_frac
        )
        proj_no_boost, _, _ = strategy_no_boost.calculate_projected_total(50, time_frac)

        # With boost should be higher
        assert proj_with_boost > proj_no_boost
        assert boost > 0

    def test_second_half_boost_not_applied_late(self):
        # Boost should not apply late game (after halftime) even when enabled
        time_frac = 0.25  # Q3/Q4

        # Strategy with boost enabled
        strategy_with_boost = TotalPointsStrategy(
            TotalPointsConfig(enable_second_half_boost=True, second_half_boost=13.5)
        )

        _, _, boost = strategy_with_boost.calculate_projected_total(150, time_frac)
        # Boost is only applied when time_remaining_frac > 0.5
        assert boost == 0.0

    def test_slow_game_boost(self, strategy):
        # Slow game at halftime should get extra boost
        strategy.set_halftime_total(95)  # Below 103 threshold

        time_frac = 0.4  # After halftime
        _, _, boost = strategy.calculate_projected_total(
            95, time_frac, halftime_total=95
        )

        # Should have slow game boost applied
        assert boost >= strategy.config.slow_game_extra_boost

    def test_check_entry_generates_signal(self, strategy):
        # Simulate a scenario with edge
        signal = strategy.check_entry(
            home_score=50,
            away_score=48,
            period=2,
            time_remaining="6:30",
            timestamp=1234567890.0,
            game_id="TEST123",
            line=210.0,
            market_over_bid=0.45,  # Market thinks under is more likely
            market_over_ask=0.47,
            ticker="KXNBATOTAL-TEST-210",
        )

        # Depending on projection vs line, may or may not signal
        # At least verify it doesn't crash and returns proper type
        assert signal is None or isinstance(signal, TotalPointsSignal)

    def test_check_entry_respects_max_period(self, strategy):
        # Q4 should not generate signals with default config
        signal = strategy.check_entry(
            home_score=80,
            away_score=82,
            period=4,  # Q4
            time_remaining="6:30",
            timestamp=1234567890.0,
            game_id="TEST123",
            line=210.0,
            market_over_bid=0.40,
            market_over_ask=0.42,
            ticker="KXNBATOTAL-TEST-210",
        )

        assert signal is None

    def test_check_entry_respects_cooldown(self, strategy):
        # First signal should work
        signal1 = strategy.check_entry(
            home_score=50,
            away_score=48,
            period=2,
            time_remaining="6:30",
            timestamp=1000.0,
            game_id="TEST123",
            line=180.0,  # Very low to ensure edge
            market_over_bid=0.50,
            market_over_ask=0.52,
            ticker="KXNBATOTAL-TEST-180",
        )

        # Immediate second call should be blocked by cooldown
        signal2 = strategy.check_entry(
            home_score=50,
            away_score=48,
            period=2,
            time_remaining="6:25",
            timestamp=1005.0,  # Only 5 seconds later
            game_id="TEST123",
            line=180.0,
            market_over_bid=0.50,
            market_over_ask=0.52,
            ticker="KXNBATOTAL-TEST-180",
        )

        # If first signal was generated, second should be None due to cooldown
        if signal1 is not None:
            assert signal2 is None

    def test_reset_clears_state(self, strategy):
        # Generate some state
        strategy._last_signal_time = 1000.0
        strategy._halftime_total = 100
        strategy.signals_generated.append(None)  # dummy

        # Reset
        strategy.reset()

        assert strategy._last_signal_time == 0.0
        assert strategy._halftime_total is None
        assert len(strategy.signals_generated) == 0


class TestTotalPointsSignal:
    """Tests for TotalPointsSignal dataclass."""

    def test_under_probability(self):
        signal = TotalPointsSignal(
            timestamp=0,
            game_id="TEST",
            ticker="TEST-210",
            line=210.0,
            current_total=100,
            time_remaining_fraction=0.5,
            projected_total=215.0,
            theoretical_over_prob=0.65,
            market_over_prob=0.60,
            edge_cents=5.0,
            direction="BUY_OVER",
            blended_pace=4.6,
            boost_applied=10.0,
            sigma=10.0,
            period=2,
            time_remaining_str="0:00",
        )

        assert signal.theoretical_under_prob == pytest.approx(0.35, rel=0.01)
        assert signal.market_under_prob == pytest.approx(0.40, rel=0.01)


class TestIntegration:
    """Integration tests verifying the model against known scenarios."""

    def test_projection_accuracy_simulated(self):
        """Test that projections are reasonable for a simulated game."""
        strategy = TotalPointsStrategy()

        # Simulate a game with typical scoring
        # Q1: 25-23 after 12 min
        # Q2: 52-48 after 24 min (halftime)
        # Q3: 78-75 after 36 min
        # Final: 110-105 (215 total)

        # Check projection at halftime (100 total)
        proj, pace, boost = strategy.calculate_projected_total(
            current_total=100,
            time_remaining_frac=0.5,
        )

        # With second half boost, should project higher than linear
        # Linear would be 100 + 100 = 200
        # With boost should be ~213.5
        assert proj > 200
        assert proj < 250  # Reasonable upper bound

    def test_probability_calibration(self):
        """Test that P(over) = 0.5 when projected equals line."""
        config = TotalPointsConfig(
            enable_second_half_boost=False,
            enable_slow_game_boost=False,
        )
        strategy = TotalPointsStrategy(config)

        # At halftime with 100 points
        current_total = 100
        time_frac = 0.5

        # Get projection
        projected, _, _ = strategy.calculate_projected_total(current_total, time_frac)

        # P(over) when line = projected should be ~50%
        prob, _, _, _ = strategy.calculate_over_probability(
            current_total=current_total,
            line=projected,
            period=2,
            time_remaining_seconds=0,
        )

        assert prob == pytest.approx(0.5, rel=0.05)

    def test_edge_calculation(self):
        """Test that edge is correctly calculated."""
        config = TotalPointsConfig(min_edge_cents=0.0)  # Accept any edge
        strategy = TotalPointsStrategy(config)

        # Setup where theoretical should differ from market
        signal = strategy.check_entry(
            home_score=50,
            away_score=50,
            period=2,
            time_remaining="0:30",  # Near halftime
            timestamp=1000.0,
            game_id="TEST",
            line=200.0,  # Below typical projection
            market_over_bid=0.60,
            market_over_ask=0.62,
            ticker="TEST-200",
        )

        if signal:
            # Edge should be difference * 100 cents
            expected_edge = (
                abs(signal.theoretical_over_prob - signal.market_over_prob) * 100
            )
            assert abs(signal.edge_cents - expected_edge) < 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
