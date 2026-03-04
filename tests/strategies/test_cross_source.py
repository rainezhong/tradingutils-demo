"""Tests for CrossSourceBlender — cross-platform fair value aggregation."""

import time
from strategies.prediction_mm.cross_source import (
    CrossSourceBlender,
    CrossSourceConfig,
)


def _cfg(**kwargs) -> CrossSourceConfig:
    defaults = dict(enabled=True, external_weight=0.5, max_observation_age_sec=30.0)
    defaults.update(kwargs)
    return CrossSourceConfig(**defaults)


class TestBlenderBasics:
    def test_disabled_returns_none(self):
        blender = CrossSourceBlender(CrossSourceConfig(enabled=False))
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        assert blender.get_blended_prob("TICK-1") is None

    def test_single_source_returns_its_value(self):
        blender = CrossSourceBlender(_cfg())
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        result = blender.get_blended_prob("TICK-1")
        assert result is not None
        assert abs(result - 0.60) < 0.001

    def test_two_sources_volume_weighted(self):
        blender = CrossSourceBlender(_cfg(external_weight=1.0))
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        blender.update("polymarket", "TICK-1", mid_prob=0.64, volume=100)
        result = blender.get_blended_prob("TICK-1")
        # Equal volume, equal weight → mean of 0.60 and 0.64 = 0.62
        assert result is not None
        assert abs(result - 0.62) < 0.01

    def test_external_weight_scaling(self):
        blender = CrossSourceBlender(_cfg(external_weight=0.0))
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        blender.update("polymarket", "TICK-1", mid_prob=0.80, volume=100)
        result = blender.get_blended_prob("TICK-1")
        # external_weight=0 → polymarket ignored entirely
        assert result is not None
        assert abs(result - 0.60) < 0.01

    def test_higher_volume_gets_more_weight(self):
        blender = CrossSourceBlender(_cfg(external_weight=1.0))
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        blender.update("polymarket", "TICK-1", mid_prob=0.70, volume=400)
        result = blender.get_blended_prob("TICK-1")
        # Polymarket has 4x volume → result should be closer to 0.70
        assert result is not None
        assert result > 0.65


class TestFiltering:
    def test_stale_observation_excluded(self):
        blender = CrossSourceBlender(_cfg(max_observation_age_sec=5.0))
        old_ts = time.time() - 10.0
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100, ts=old_ts)
        blender.update("polymarket", "TICK-1", mid_prob=0.70, volume=100)
        result = blender.get_blended_prob("TICK-1")
        # Kalshi is stale, only polymarket remains
        assert result is not None
        assert abs(result - 0.70) < 0.01

    def test_low_volume_external_excluded(self):
        blender = CrossSourceBlender(_cfg(min_external_volume=50.0))
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        blender.update("polymarket", "TICK-1", mid_prob=0.80, volume=5)
        result = blender.get_blended_prob("TICK-1")
        # Polymarket volume too low → excluded
        assert result is not None
        assert abs(result - 0.60) < 0.01

    def test_wide_spread_external_excluded(self):
        blender = CrossSourceBlender(_cfg(max_external_spread_cents=5.0))
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100, spread_cents=2)
        blender.update(
            "polymarket", "TICK-1", mid_prob=0.80, volume=100, spread_cents=8
        )
        result = blender.get_blended_prob("TICK-1")
        # Polymarket spread too wide → excluded
        assert result is not None
        assert abs(result - 0.60) < 0.01


class TestTickerMapping:
    def test_register_mapping_resolves_external(self):
        blender = CrossSourceBlender(_cfg(external_weight=1.0))
        blender.register_mapping("KXBTC15M-123", "poly-btc-above-100k")
        blender.update("kalshi", "KXBTC15M-123", mid_prob=0.60, volume=100)
        blender.update("polymarket", "poly-btc-above-100k", mid_prob=0.64, volume=100)
        result = blender.get_blended_prob("KXBTC15M-123")
        assert result is not None
        assert abs(result - 0.62) < 0.01


class TestSpreadPenalty:
    def test_wider_spread_reduces_weight(self):
        blender = CrossSourceBlender(_cfg(external_weight=1.0))
        # Kalshi: tight spread (high confidence)
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100, spread_cents=1)
        # Polymarket: wider spread (lower confidence)
        blender.update(
            "polymarket", "TICK-1", mid_prob=0.80, volume=100, spread_cents=8
        )
        result = blender.get_blended_prob("TICK-1")
        # Polymarket gets penalized for wide spread → closer to Kalshi's 0.60
        assert result is not None
        assert result < 0.70  # Should be pulled toward Kalshi


class TestClear:
    def test_clear_specific_ticker(self):
        blender = CrossSourceBlender(_cfg())
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        blender.update("kalshi", "TICK-2", mid_prob=0.70, volume=100)
        blender.clear("TICK-1")
        assert blender.get_blended_prob("TICK-1") is None
        assert blender.get_blended_prob("TICK-2") is not None

    def test_clear_all(self):
        blender = CrossSourceBlender(_cfg())
        blender.update("kalshi", "TICK-1", mid_prob=0.60, volume=100)
        blender.clear()
        assert blender.get_blended_prob("TICK-1") is None
