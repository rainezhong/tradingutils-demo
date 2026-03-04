"""Unit tests for queue priority fill model."""

import sys
from pathlib import Path

# Add project root to path to enable proper imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import pytest

# Import using Python's module system (handles relative imports correctly)
# We need to import the package first to set up the module hierarchy
import src.backtesting.repricing_lag  # Load dependency first
from src.backtesting.fill_model import QueuePriorityConfig, apply_queue_priority


class TestQueuePriorityConfig:
    """Test QueuePriorityConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = QueuePriorityConfig()
        assert config.enable_queue_priority is False
        assert config.min_depth_multiple == 3.0
        assert config.queue_factor == 3.0
        assert config.enable_partial_fills is True

    def test_custom_config(self):
        """Test custom configuration values."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=5.0,
            queue_factor=2.0,
            enable_partial_fills=False,
        )
        assert config.enable_queue_priority is True
        assert config.min_depth_multiple == 5.0
        assert config.queue_factor == 2.0
        assert config.enable_partial_fills is False


class TestApplyQueuePriority:
    """Test apply_queue_priority function."""

    def test_disabled_returns_full_size(self):
        """When queue priority is disabled, should return full order size."""
        config = QueuePriorityConfig(enable_queue_priority=False)
        result = apply_queue_priority(order_size=10, depth=5, config=config)
        assert result == 10

    def test_high_depth_instant_fill(self):
        """When depth >= order_size * min_depth_multiple, should fill instantly."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
        )
        # 50 depth >= 10 * 3.0 = 30
        result = apply_queue_priority(order_size=10, depth=50, config=config)
        assert result == 10

    def test_exact_threshold_instant_fill(self):
        """When depth exactly equals threshold, should fill instantly."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
        )
        # 30 depth == 10 * 3.0 = 30
        result = apply_queue_priority(order_size=10, depth=30, config=config)
        assert result == 10

    def test_low_depth_partial_fill(self):
        """When depth < threshold, should return partial fill."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=3.0,
            enable_partial_fills=True,
        )
        # 15 depth < 10 * 3.0 = 30
        # fill_prob = 15 / (10 * 3.0) = 15 / 30 = 0.5
        # fill_size = int(10 * 0.5) = 5
        result = apply_queue_priority(order_size=10, depth=15, config=config)
        assert result == 5

    def test_very_low_depth_small_partial(self):
        """When depth is very low, should return small partial fill."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=3.0,
            enable_partial_fills=True,
        )
        # 6 depth < 10 * 3.0 = 30
        # fill_prob = 6 / (10 * 3.0) = 6 / 30 = 0.2
        # fill_size = int(10 * 0.2) = 2
        result = apply_queue_priority(order_size=10, depth=6, config=config)
        assert result == 2

    def test_minimal_depth_rounds_down_to_none(self):
        """When calculated fill size rounds to 0, should return None."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=3.0,
            enable_partial_fills=True,
        )
        # 1 depth < 10 * 3.0 = 30
        # fill_prob = 1 / (10 * 3.0) = 1 / 30 = 0.033
        # fill_size = int(10 * 0.033) = int(0.33) = 0
        result = apply_queue_priority(order_size=10, depth=1, config=config)
        assert result is None

    def test_zero_depth_no_fill(self):
        """When depth is 0, should return None."""
        config = QueuePriorityConfig(enable_queue_priority=True)
        result = apply_queue_priority(order_size=10, depth=0, config=config)
        assert result is None

    def test_none_depth_no_fill(self):
        """When depth is None, should return None."""
        config = QueuePriorityConfig(enable_queue_priority=True)
        result = apply_queue_priority(order_size=10, depth=None, config=config)
        assert result is None

    def test_negative_depth_no_fill(self):
        """When depth is negative, should return None."""
        config = QueuePriorityConfig(enable_queue_priority=True)
        result = apply_queue_priority(order_size=10, depth=-5, config=config)
        assert result is None

    def test_partial_fills_disabled_probabilistic(self):
        """When partial fills disabled, should return full size or None randomly."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=3.0,
            enable_partial_fills=False,
        )
        # 15 depth < 10 * 3.0 = 30
        # fill_prob = 15 / (10 * 3.0) = 15 / 30 = 0.5
        # Should return 10 or None randomly with 50% probability

        # Run multiple times to check both outcomes occur
        results = []
        for _ in range(100):
            result = apply_queue_priority(order_size=10, depth=15, config=config)
            results.append(result)

        # Should have both fills and rejections
        filled = sum(1 for r in results if r == 10)
        rejected = sum(1 for r in results if r is None)

        assert filled > 0, "Should have some fills"
        assert rejected > 0, "Should have some rejections"
        # With 100 trials and 50% probability, expect ~40-60 fills
        assert 30 < filled < 70, f"Expected ~50 fills, got {filled}"

    def test_different_queue_factors(self):
        """Test that queue_factor affects fill size as expected."""
        # Higher queue_factor = more competition = smaller fills
        config_low = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=2.0,  # Less competition
            enable_partial_fills=True,
        )
        config_high = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=5.0,  # More competition
            enable_partial_fills=True,
        )

        # Same depth and order size
        fill_low = apply_queue_priority(order_size=10, depth=15, config=config_low)
        fill_high = apply_queue_priority(order_size=10, depth=15, config=config_high)

        # Low competition should give larger fill
        # fill_low = int(10 * (15 / (10 * 2.0))) = int(10 * 0.75) = 7
        # fill_high = int(10 * (15 / (10 * 5.0))) = int(10 * 0.3) = 3
        assert fill_low == 7
        assert fill_high == 3
        assert fill_low > fill_high

    def test_different_min_depth_multiples(self):
        """Test that min_depth_multiple affects threshold as expected."""
        config_low = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=2.0,  # Lower threshold
        )
        config_high = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=4.0,  # Higher threshold
        )

        # 25 depth: 25 >= 10 * 2.0 = 20 (instant for low)
        # 25 depth: 25 < 10 * 4.0 = 40 (partial for high)
        fill_low = apply_queue_priority(order_size=10, depth=25, config=config_low)
        fill_high = apply_queue_priority(order_size=10, depth=25, config=config_high)

        assert fill_low == 10  # Instant fill
        assert fill_high < 10  # Partial fill
        assert fill_high is not None  # But still gets some fill

    def test_large_order_small_depth(self):
        """Test behavior when order size is much larger than depth."""
        config = QueuePriorityConfig(
            enable_queue_priority=True,
            min_depth_multiple=3.0,
            queue_factor=3.0,
            enable_partial_fills=True,
        )
        # Order 100, depth 10
        # fill_prob = 10 / (100 * 3.0) = 10 / 300 = 0.033
        # fill_size = int(100 * 0.033) = int(3.3) = 3
        result = apply_queue_priority(order_size=100, depth=10, config=config)
        assert result == 3
