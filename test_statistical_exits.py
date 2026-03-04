#!/usr/bin/env python3
"""Quick test to verify statistical exits implementation.

Tests:
1. Import all modified modules
2. Create BRTITracker and check new methods
3. Create L2BookState with new fields
4. Create ScalpPosition with new fields
5. Check config parameters
"""

import sys
from dataclasses import asdict

# Test imports
print("Testing imports...")
try:
    from core.indicators.brti_tracker import BRTITracker, BRTIConfig
    from core.indicators.cex_feeds import L2BookState
    from strategies.crypto_scalp.orchestrator import ScalpPosition
    from strategies.crypto_scalp.config import CryptoScalpConfig
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test BRTITracker new methods
print("\nTesting BRTITracker new methods...")
tracker = BRTITracker()
methods = [
    'get_volatility',
    'get_acceleration',
    'get_imbalance',
    'get_imbalance_velocity',
    'get_cross_exchange_std'
]
for method in methods:
    if hasattr(tracker, method):
        print(f"✓ {method} exists")
    else:
        print(f"✗ {method} missing")
        sys.exit(1)

# Test L2BookState new fields
print("\nTesting L2BookState new fields...")
state = L2BookState(
    exchange="test",
    mid_price=50000.0,
    best_bid=49995.0,
    best_ask=50005.0,
    spread_bps=20.0,
    timestamp=1234567890.0,
    connected=True,
    bid_depth=100.0,
    ask_depth=80.0,
    imbalance=0.111
)
fields = asdict(state)
new_fields = ['bid_depth', 'ask_depth', 'imbalance']
for field in new_fields:
    if field in fields:
        print(f"✓ {field} = {fields[field]}")
    else:
        print(f"✗ {field} missing")
        sys.exit(1)

# Test ScalpPosition new fields
print("\nTesting ScalpPosition new fields...")
position = ScalpPosition(
    ticker="TEST-TICKER",
    side="yes",
    entry_price_cents=50,
    size=10,
    entry_time=1234567890.0,
    exit_target_time=1234567910.0,
    hard_exit_time=1234567950.0,
    order_id="test123",
    spot_delta=15.5,
    signal_source="binance",
    entry_exit_depth=25,
    entry_spread_cents=3,
    entry_cex_imbalance=0.15,
    entry_cross_exchange_std=12.5
)
pos_fields = asdict(position)
new_pos_fields = [
    'entry_exit_depth',
    'entry_spread_cents',
    'entry_cex_imbalance',
    'entry_cross_exchange_std'
]
for field in new_pos_fields:
    if field in pos_fields:
        print(f"✓ {field} = {pos_fields[field]}")
    else:
        print(f"✗ {field} missing")
        sys.exit(1)

# Test config parameters
print("\nTesting CryptoScalpConfig new parameters...")
config = CryptoScalpConfig()
new_params = [
    'enable_depth_momentum_exit',
    'depth_drain_threshold',
    'enable_spread_reversion_exit',
    'spread_reversion_multiplier',
    'enable_volatility_adjusted_hold',
    'high_vol_threshold',
    'enable_imbalance_reversal_exit',
    'imbalance_reversal_threshold',
    'enable_divergence_exit',
    'divergence_std_threshold'
]
for param in new_params:
    if hasattr(config, param):
        value = getattr(config, param)
        print(f"✓ {param} = {value}")
    else:
        print(f"✗ {param} missing")
        sys.exit(1)

print("\n" + "="*60)
print("✓ All tests passed! Statistical exits implementation verified.")
print("="*60)
print("\nNext steps:")
print("1. Update backtest adapter (src/backtesting/adapters/scalp_adapter.py)")
print("2. Run backtest with statistical exits enabled")
print("3. Compare P&L vs baseline (fixed 20s holds)")
print("4. Validate exit type distribution")
