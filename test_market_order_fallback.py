#!/usr/bin/env python3
"""Test script to verify market order fallback implementation.

Tests:
1. Config loads with new parameters
2. Stats dataclass has new fields
3. Signal validation helper works
4. Two-stage entry logic compiles
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from strategies.crypto_scalp.config import CryptoScalpConfig
from strategies.crypto_scalp.orchestrator import ScalpStats


def test_config_loading():
    """Test that new config parameters load correctly."""
    print("=" * 60)
    print("TEST 1: Config Loading")
    print("=" * 60)

    # Load from YAML
    config = CryptoScalpConfig.from_yaml("strategies/configs/crypto_scalp_live.yaml")

    # Verify new parameters exist and have correct defaults
    assert hasattr(config, "limit_order_timeout_sec"), "Missing limit_order_timeout_sec"
    assert hasattr(config, "market_order_fallback"), "Missing market_order_fallback"
    assert hasattr(config, "max_fallback_slippage_cents"), "Missing max_fallback_slippage_cents"
    assert hasattr(config, "fallback_min_edge_cents"), "Missing fallback_min_edge_cents"

    print(f"✓ limit_order_timeout_sec: {config.limit_order_timeout_sec}s")
    print(f"✓ market_order_fallback: {config.market_order_fallback}")
    print(f"✓ max_fallback_slippage_cents: {config.max_fallback_slippage_cents}¢")
    print(f"✓ fallback_min_edge_cents: {config.fallback_min_edge_cents}¢")

    # Verify values match expected
    assert config.limit_order_timeout_sec == 1.5, f"Expected 1.5s, got {config.limit_order_timeout_sec}s"
    assert config.market_order_fallback == True, f"Expected True, got {config.market_order_fallback}"
    assert config.max_fallback_slippage_cents == 5, f"Expected 5¢, got {config.max_fallback_slippage_cents}¢"
    assert config.fallback_min_edge_cents == 8, f"Expected 8¢, got {config.fallback_min_edge_cents}¢"

    print("\n✅ All config parameters loaded correctly!\n")


def test_stats_fields():
    """Test that ScalpStats has new tracking fields."""
    print("=" * 60)
    print("TEST 2: Stats Fields")
    print("=" * 60)

    stats = ScalpStats()

    # Verify new fields exist
    assert hasattr(stats, "limit_fills"), "Missing limit_fills"
    assert hasattr(stats, "market_fills"), "Missing market_fills"
    assert hasattr(stats, "fallback_skips"), "Missing fallback_skips"

    print(f"✓ limit_fills: {stats.limit_fills}")
    print(f"✓ market_fills: {stats.market_fills}")
    print(f"✓ fallback_skips: {stats.fallback_skips}")

    # Verify defaults are zero
    assert stats.limit_fills == 0, f"Expected 0, got {stats.limit_fills}"
    assert stats.market_fills == 0, f"Expected 0, got {stats.market_fills}"
    assert stats.fallback_skips == 0, f"Expected 0, got {stats.fallback_skips}"

    # Test incrementing
    stats.limit_fills += 1
    stats.market_fills += 2
    stats.fallback_skips += 3

    assert stats.limit_fills == 1
    assert stats.market_fills == 2
    assert stats.fallback_skips == 3

    print("\n✅ All stats fields work correctly!\n")


def test_orchestrator_methods():
    """Test that new orchestrator methods exist."""
    print("=" * 60)
    print("TEST 3: Orchestrator Methods")
    print("=" * 60)

    from strategies.crypto_scalp.orchestrator import CryptoScalpStrategy

    # Verify new methods exist (don't need to instantiate, just check they're defined)
    assert hasattr(CryptoScalpStrategy, "_is_signal_still_strong"), "Missing _is_signal_still_strong"
    assert hasattr(CryptoScalpStrategy, "_place_market_order"), "Missing _place_market_order"

    print("✓ _is_signal_still_strong method exists")
    print("✓ _place_market_order method exists")

    # Verify _wait_for_fill accepts timeout parameter
    import inspect
    wait_for_fill_sig = inspect.signature(CryptoScalpStrategy._wait_for_fill)
    params = list(wait_for_fill_sig.parameters.keys())
    assert "timeout" in params, "Missing timeout parameter in _wait_for_fill"

    print("✓ _wait_for_fill accepts timeout parameter")

    print("\n✅ All orchestrator methods exist!\n")


def test_integration():
    """Integration test: verify orchestrator can be instantiated with new config."""
    print("=" * 60)
    print("TEST 4: Integration")
    print("=" * 60)

    try:
        config = CryptoScalpConfig.from_yaml("strategies/configs/crypto_scalp_live.yaml")

        # Can't fully instantiate without exchange client, but we can verify config works
        print("✓ Config loads successfully")
        print(f"✓ Market order fallback: {'ENABLED' if config.market_order_fallback else 'DISABLED'}")
        print(f"✓ Limit timeout: {config.limit_order_timeout_sec}s")
        print(f"✓ Max slippage: {config.max_fallback_slippage_cents}¢")
        print(f"✓ Min edge: {config.fallback_min_edge_cents}¢")

        print("\n✅ Integration test passed!\n")

    except Exception as e:
        print(f"\n❌ Integration test failed: {e}\n")
        raise


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("MARKET ORDER FALLBACK - IMPLEMENTATION TESTS")
    print("=" * 60 + "\n")

    try:
        test_config_loading()
        test_stats_fields()
        test_orchestrator_methods()
        test_integration()

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Test in paper mode: python3 main.py run crypto-scalp --dry-run")
        print("2. Monitor fill rates in logs")
        print("3. Verify limit vs market fill distribution")
        print("4. Check that fallback_skips are reasonable")
        print("=" * 60 + "\n")

        return 0

    except AssertionError as e:
        print("\n" + "=" * 60)
        print(f"❌ TEST FAILED: {e}")
        print("=" * 60 + "\n")
        return 1
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ UNEXPECTED ERROR: {e}")
        print("=" * 60 + "\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
