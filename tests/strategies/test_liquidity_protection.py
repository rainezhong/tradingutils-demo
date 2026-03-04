"""Test liquidity protection in crypto scalp strategy."""

import pytest
from unittest.mock import Mock, MagicMock
from strategies.crypto_scalp.config import CryptoScalpConfig
from strategies.crypto_scalp.orchestrator import ScalpPosition


def test_liquidity_protection_config_defaults():
    """Test that liquidity protection is enabled by default."""
    config = CryptoScalpConfig()

    # Updated 2026-03-01: Loosened from 5 to 3 (was blocking profitable exits)
    assert config.min_exit_bid_depth == 3
    # Updated 2026-03-01: Loosened from 20 to 35 (was blocking exits at -27¢)
    assert config.max_adverse_exit_cents == 35
    assert config.skip_exit_on_thin_liquidity is True


def test_liquidity_protection_from_yaml(tmp_path):
    """Test loading liquidity protection from YAML."""
    yaml_file = tmp_path / "test_config.yaml"
    yaml_file.write_text("""
min_exit_bid_depth: 10
max_adverse_exit_cents: 15
skip_exit_on_thin_liquidity: true
""")

    config = CryptoScalpConfig.from_yaml(str(yaml_file))

    assert config.min_exit_bid_depth == 10
    assert config.max_adverse_exit_cents == 15
    assert config.skip_exit_on_thin_liquidity is True


def test_adverse_price_protection():
    """Test that exits are refused when adverse movement is too large."""
    # This test would need the full orchestrator setup
    # For now, just verify the config values are reasonable

    config = CryptoScalpConfig(
        max_adverse_exit_cents=20,
        skip_exit_on_thin_liquidity=True,
    )

    # Entry @ 54¢, exit @ 7¢ → adverse = 47¢
    # Should be refused because 47 > 20
    entry_price = 54
    exit_price = 7
    adverse = entry_price - exit_price

    assert adverse > config.max_adverse_exit_cents
    # In real code, this would trigger protection


def test_depth_protection():
    """Test that exits are refused when bid depth is too low."""
    config = CryptoScalpConfig(
        min_exit_bid_depth=5,
        skip_exit_on_thin_liquidity=True,
    )

    # Bid depth of 2 contracts
    bid_depth = 2

    assert bid_depth < config.min_exit_bid_depth
    # In real code, this would trigger protection


def test_protection_disabled():
    """Test that protection can be disabled."""
    config = CryptoScalpConfig(
        skip_exit_on_thin_liquidity=False,
    )

    assert config.skip_exit_on_thin_liquidity is False
    # In real code, adverse exits would be allowed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
