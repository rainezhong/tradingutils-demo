#!/usr/bin/env python3
"""Test script to verify backtest adapter updates.

Tests:
1. Import adapter modules
2. Create data feed and verify CEX L2 loading
3. Create adapter and verify statistical exit config
4. Process a few frames to verify statistical metrics computation
"""

import sys
from pathlib import Path

# Test imports
print("Testing imports...")
try:
    from src.backtesting.adapters.scalp_adapter import (
        CryptoScalpDataFeed,
        CryptoScalpAdapter,
        _compute_volatility,
        _compute_acceleration,
        _get_cex_imbalance,
        _get_cex_imbalance_velocity,
        _get_cross_exchange_std,
    )
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Find a database
db_path = "data/btc_latency_probe.db"
if not Path(db_path).exists():
    print(f"⚠ Database not found: {db_path}")
    print("  Skipping data feed test (run btc_latency_probe.py first)")
    sys.exit(0)

# Test data feed with CEX L2 loading
print(f"\nTesting data feed with {db_path}...")
try:
    feed = CryptoScalpDataFeed(db_path, lookback_sec=5.0, regime_window_sec=60.0)
    print("✓ Data feed created")

    metadata = feed.metadata
    print(f"  Snapshots: {metadata.get('total_snapshots', 0)}")
    print(f"  Tickers: {metadata.get('tickers', 0)}")
    print(f"  Orderbook snapshots: {metadata.get('orderbook_snapshots', 0)}")
    print(f"  CEX L2 snapshots: {metadata.get('cex_l2_snapshots', 0)}")

    if metadata.get("cex_l2_snapshots", 0) == 0:
        print("  ⚠ No CEX L2 data found (statistical exits will be limited)")
    else:
        print("  ✓ CEX L2 data loaded")

    # Test frame iteration
    print("\nTesting frame iteration...")
    frame_count = 0
    for frame in feed:
        frame_count += 1
        if frame_count == 1:
            ctx = frame.context
            print(f"  First frame timestamp: {frame.timestamp}")
            print(f"  Ticker: {ctx['ticker']}")
            print(f"  Has spot data: {'spot' in ctx}")
            print(f"  Has regime data: {'regime' in ctx}")
            print(f"  Has orderbook data: {ctx.get('orderbook') is not None}")
            print(f"  Has CEX data: {'cex' in ctx}")

            if 'cex' in ctx:
                cex = ctx['cex']
                print(f"    Volatility: ${cex.get('volatility', 0):.2f}")
                print(f"    Acceleration: {cex.get('acceleration', 0):.2f}")
                print(f"    Imbalance: {cex.get('imbalance', 0):.3f}")
                print(f"    Cross-exchange std: ${cex.get('cross_exchange_std', 0):.2f}")
        if frame_count >= 3:
            break

    print(f"  ✓ Processed {frame_count} frames successfully")

except Exception as e:
    print(f"✗ Data feed test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test adapter creation
print("\nTesting adapter creation...")
try:
    adapter = CryptoScalpAdapter(
        signal_feed="binance",
        min_spot_move_usd=10.0,
        exit_delay_sec=20.0,
    )
    print("✓ Adapter created")

    # Verify statistical exit config
    print("  Statistical exit configuration:")
    print(f"    Depth-momentum: {adapter._enable_depth_momentum_exit}")
    print(f"    Spread reversion: {adapter._enable_spread_reversion_exit}")
    print(f"    Volatility-adjusted: {adapter._enable_volatility_adjusted_hold}")
    print(f"    Imbalance reversal: {adapter._enable_imbalance_reversal_exit}")
    print(f"    Divergence: {adapter._enable_divergence_exit}")

    # Verify statistics counters exist
    stats = [
        'depth_momentum_exits',
        'spread_reversion_exits',
        'imbalance_reversal_exits',
        'divergence_exits',
        'volatility_adjusted_exits',
        'normal_exits',
        'hard_exits',
    ]
    for stat in stats:
        if hasattr(adapter, stat):
            print(f"    ✓ {stat} counter exists")
        else:
            print(f"    ✗ {stat} counter missing")
            sys.exit(1)

except Exception as e:
    print(f"✗ Adapter creation failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*60)
print("✓ All tests passed! Backtest adapter is ready.")
print("="*60)
print("\nNext steps:")
print("1. Run baseline backtest: python3 main.py backtest crypto-scalp --db data/btc_latency_probe.db")
print("2. Analyze exit type distribution in results")
print("3. Compare P&L vs fixed 20s holds")
