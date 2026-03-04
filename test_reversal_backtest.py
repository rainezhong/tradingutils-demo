#!/usr/bin/env python3
"""Quick test script to backtest crypto scalp with reversal exit and flip logic.

Usage:
    python3 test_reversal_backtest.py --db data/btc_probe_20260227.db --reversal --flip
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.backtesting.adapters.scalp_adapter import CryptoScalpDataFeed, CryptoScalpAdapter
from src.backtesting.engine import BacktestEngine, BacktestConfig


def run_backtest(
    db_path: str,
    enable_reversal: bool = False,
    enable_flip: bool = False,
    enable_stop_loss: bool = True,
):
    """Run backtest with specified features."""

    print("=" * 80)
    print("CRYPTO SCALP BACKTEST - REVERSAL DETECTION")
    print("=" * 80)
    print(f"Database: {db_path}")
    print(f"Reversal Exit: {'ENABLED' if enable_reversal else 'DISABLED'}")
    print(f"Position Flip: {'ENABLED' if enable_flip else 'DISABLED'}")
    print(f"Stop-Loss: {'ENABLED' if enable_stop_loss else 'DISABLED'}")
    print("=" * 80)

    # Create data feed
    feed = CryptoScalpDataFeed(
        db_path=db_path,
        lookback_sec=5.0,
        regime_window_sec=60.0,
    )

    # Create adapter with reversal features
    adapter = CryptoScalpAdapter(
        signal_feed="all",
        min_spot_move_usd=10.0,
        min_ttx_sec=900,
        max_ttx_sec=900,
        min_entry_price_cents=20,
        max_entry_price_cents=70,
        contracts_per_trade=5,
        exit_delay_sec=20.0,
        max_hold_sec=35.0,
        cooldown_sec=15.0,
        min_window_volume={"binance": 0.7, "coinbase": 0.4, "kraken": 0.15},
        require_multi_exchange_confirm=True,
        regime_osc_threshold=0.0,
        slippage_cents=1,
        # Crash protection
        min_entry_bid_depth=5,
        enable_entry_liquidity_check=True,
        stop_loss_cents=15,
        stop_loss_delay_sec=0.0,
        enable_stop_loss=enable_stop_loss,
        # Fill simulation
        enable_fill_simulation=True,
        base_fill_rate=0.65,
        adverse_selection_factor=0.3,
    )

    # Manually enable reversal features (will be added to __init__ later)
    adapter._enable_reversal_exit = enable_reversal
    adapter._reversal_exit_delay = 2.0
    adapter._min_reversal_strength = 10.0
    adapter._enable_position_flip = enable_flip
    adapter._flip_min_profit_cents = 5
    adapter._flip_min_reversal_usd = 15.0
    adapter._flip_min_time_to_expiry = 300

    # Create backtest config
    config = BacktestConfig(
        initial_bankroll=1000.0,
        fill_probability=1.0,
        slippage=0.0,
        fee_model=lambda gross_pnl: max(1, int(abs(gross_pnl) * 0.07)) if gross_pnl > 0 else 0,
    )

    # Run backtest
    engine = BacktestEngine(config)
    result = engine.run(feed, adapter, verbose=True)

    # Print results
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)
    m = result.metrics
    print(f"Total Fills: {m.total_fills}")
    print(f"Wins: {m.winning_fills} ({m.win_rate_pct:.1f}%)")
    print(f"Losses: {m.losing_fills}")
    print(f"Total P&L: ${m.net_pnl:.2f} ({m.return_pct:+.1f}%)")
    if m.total_fills > 0:
        print(f"Avg P&L/Trade: ${m.net_pnl/m.total_fills:.2f}")
    print(f"Max Drawdown: {m.max_drawdown_pct:.2f}%")
    print(f"Final Bankroll: ${m.final_bankroll:.2f}")
    print()
    print(f"Reversal Exits: {adapter.reversal_exits} ({100*adapter.reversal_exits/max(1,adapter.exits):.1f}%)")
    print(f"Position Flips: {adapter.position_flips}")
    print(f"Stop-Loss Exits: {adapter.stop_loss_exits} ({100*adapter.stop_loss_exits/max(1,adapter.exits):.1f}%)")
    print()
    print(f"Entry Attempts: {adapter.entry_attempts}")
    print(f"Entry Fills: {adapter.entry_fills} ({100*adapter.entry_fills/max(1,adapter.entry_attempts):.1f}%)")
    print(f"Entry Rejections: {adapter.entry_rejections}")
    print("=" * 80)

    return result, adapter


def main():
    parser = argparse.ArgumentParser(description="Backtest crypto scalp with reversal detection")
    parser.add_argument("--db", required=True, help="Path to backtest database")
    parser.add_argument("--reversal", action="store_true", help="Enable reversal exit")
    parser.add_argument("--flip", action="store_true", help="Enable position flip")
    parser.add_argument("--no-stop-loss", action="store_true", help="Disable stop-loss")

    args = parser.parse_args()

    # Run baseline (no reversal)
    print("\n### BASELINE (No Reversal) ###\n")
    baseline_result, baseline_adapter = run_backtest(
        db_path=args.db,
        enable_reversal=False,
        enable_flip=False,
        enable_stop_loss=not args.no_stop_loss,
    )

    if args.reversal:
        # Run with reversal exit
        print("\n\n### WITH REVERSAL EXIT ###\n")
        reversal_result, reversal_adapter = run_backtest(
            db_path=args.db,
            enable_reversal=True,
            enable_flip=False,
            enable_stop_loss=not args.no_stop_loss,
        )

        # Compare
        print("\n" + "=" * 80)
        print("COMPARISON")
        print("=" * 80)
        bm = baseline_result.metrics
        rm = reversal_result.metrics
        pnl_diff = rm.net_pnl - bm.net_pnl
        pnl_pct = 100 * pnl_diff / abs(bm.net_pnl) if bm.net_pnl != 0 else 0
        wr_diff = rm.win_rate_pct - bm.win_rate_pct
        print(f"P&L Improvement: ${pnl_diff:.2f} ({pnl_pct:+.1f}%)")
        print(f"Win Rate: {bm.win_rate_pct:.1f}% → {rm.win_rate_pct:.1f}% ({wr_diff:+.1f}pp)")
        print(f"Reversal Exits: {reversal_adapter.reversal_exits}/{reversal_adapter.exits} ({100*reversal_adapter.reversal_exits/max(1,reversal_adapter.exits):.1f}%)")
        print("=" * 80)

    if args.flip:
        # Run with position flip
        print("\n\n### WITH POSITION FLIP ###\n")
        flip_result, flip_adapter = run_backtest(
            db_path=args.db,
            enable_reversal=True,
            enable_flip=True,
            enable_stop_loss=not args.no_stop_loss,
        )

        # Compare
        print("\n" + "=" * 80)
        print("FLIP vs REVERSAL-ONLY")
        print("=" * 80)
        fm = flip_result.metrics
        if args.reversal:
            pnl_diff = fm.net_pnl - reversal_result.metrics.net_pnl
            print(f"P&L: ${reversal_result.metrics.net_pnl:.2f} → ${fm.net_pnl:.2f} (Δ ${pnl_diff:.2f})")
        print(f"Position Flips: {flip_adapter.position_flips}/{flip_adapter.exits} ({100*flip_adapter.position_flips/max(1,flip_adapter.exits):.1f}%)")
        print(f"Win Rate: {fm.win_rate_pct:.1f}%")
        print("=" * 80)


if __name__ == "__main__":
    main()
