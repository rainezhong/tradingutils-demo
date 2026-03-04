#!/usr/bin/env python3
"""Backtest crypto scalp strategy with optimized config parameters.

Compares baseline (old config) vs optimized (new config) on the same dataset.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtesting.engine import BacktestEngine, BacktestConfig
from src.backtesting.adapters.scalp_adapter import CryptoScalpDataFeed, CryptoScalpAdapter


def run_backtest(
    db_path: str,
    min_entry_price: int = 25,
    max_entry_price: int = 75,
    min_window_volume: dict = None,
    label: str = "Baseline"
):
    """Run crypto scalp backtest with specified parameters."""

    if min_window_volume is None:
        min_window_volume = {
            "binance": 0.5,
            "coinbase": 0.3,
            "kraken": 0.1,
        }

    print(f"\n{'='*80}")
    print(f"{label} Configuration")
    print(f"{'='*80}")
    print(f"Min entry price:   {min_entry_price}¢")
    print(f"Max entry price:   {max_entry_price}¢")
    print(f"Volume thresholds: {min_window_volume}")
    print(f"{'='*80}\n")

    # Create data feed
    feed = CryptoScalpDataFeed(
        db_path=db_path,
        lookback_sec=5.0,
        regime_window_sec=60.0,
    )

    # Create adapter with config
    adapter = CryptoScalpAdapter(
        signal_feed="all",  # Use all exchanges
        min_spot_move_usd=10.0,
        min_ttx_sec=120,
        max_ttx_sec=900,
        min_entry_price_cents=min_entry_price,
        max_entry_price_cents=max_entry_price,
        contracts_per_trade=1,
        exit_delay_sec=20.0,
        max_hold_sec=35.0,
        cooldown_sec=15.0,
        min_window_volume=min_window_volume,
        min_volume_concentration=0.0,
        require_multi_exchange_confirm=True,
        regime_osc_threshold=0.0,  # Disabled
        slippage_cents=1,
    )

    # Create backtest config
    config = BacktestConfig(
        initial_bankroll=100.0,
        fill_probability=1.0,
        slippage=0.01,  # 1 cent slippage
    )

    # Run backtest
    engine = BacktestEngine(config)
    result = engine.run(feed, adapter, verbose=False)

    return result


def main():
    """Run baseline vs optimized comparison."""

    db_path = "data/btc_ob_48h.db"  # Use 48h database (2GB, comprehensive)

    print("\n" + "="*80)
    print("CRYPTO SCALP BACKTEST - BASELINE VS OPTIMIZED")
    print("="*80)
    print(f"Database: {db_path}")
    print(f"Duration: 48 hours of BTC 15-min markets")
    print("="*80)

    # Baseline (old config)
    print("\n[1/2] Running BASELINE backtest...")
    baseline = run_backtest(
        db_path=db_path,
        min_entry_price=25,
        max_entry_price=75,
        min_window_volume={
            "binance": 0.5,
            "coinbase": 0.3,
            "kraken": 0.1,
        },
        label="BASELINE (Old Config)"
    )

    # Optimized (new config)
    print("\n[2/2] Running OPTIMIZED backtest...")
    optimized = run_backtest(
        db_path=db_path,
        min_entry_price=20,
        max_entry_price=70,
        min_window_volume={
            "binance": 0.7,
            "coinbase": 0.4,
            "kraken": 0.15,
        },
        label="OPTIMIZED (New Config)"
    )

    # Comparison report
    print("\n" + "="*80)
    print("COMPARISON REPORT")
    print("="*80)

    print(f"\n{'Metric':<30} {'Baseline':<20} {'Optimized':<20} {'Change':<15}")
    print("-" * 85)

    def pct_change(old, new):
        if old == 0:
            return "N/A"
        return f"{((new - old) / abs(old)) * 100:+.1f}%"

    # Total trades
    baseline_trades = baseline.num_trades
    optimized_trades = optimized.num_trades
    print(f"{'Total Trades':<30} {baseline_trades:<20} {optimized_trades:<20} {pct_change(baseline_trades, optimized_trades):<15}")

    # Win rate
    baseline_wr = baseline.win_rate * 100 if baseline.win_rate else 0
    optimized_wr = optimized.win_rate * 100 if optimized.win_rate else 0
    print(f"{'Win Rate':<30} {f'{baseline_wr:.1f}%':<20} {f'{optimized_wr:.1f}%':<20} {f'{optimized_wr - baseline_wr:+.1f}pp':<15}")

    # Net P&L
    baseline_pnl = baseline.net_pnl
    optimized_pnl = optimized.net_pnl
    print(f"{'Net P&L':<30} {f'${baseline_pnl:.2f}':<20} {f'${optimized_pnl:.2f}':<20} {pct_change(baseline_pnl, optimized_pnl):<15}")

    # Avg P&L per trade
    baseline_avg = baseline_pnl / baseline_trades if baseline_trades > 0 else 0
    optimized_avg = optimized_pnl / optimized_trades if optimized_trades > 0 else 0
    print(f"{'Avg P&L per Trade':<30} {f'${baseline_avg:.3f}':<20} {f'${optimized_avg:.3f}':<20} {pct_change(baseline_avg, optimized_avg):<15}")

    # Max drawdown
    baseline_dd = baseline.max_drawdown
    optimized_dd = optimized.max_drawdown
    print(f"{'Max Drawdown':<30} {f'${baseline_dd:.2f}':<20} {f'${optimized_dd:.2f}':<20} {pct_change(baseline_dd, optimized_dd):<15}")

    # Sharpe ratio
    baseline_sharpe = baseline.sharpe_ratio or 0
    optimized_sharpe = optimized.sharpe_ratio or 0
    print(f"{'Sharpe Ratio':<30} {f'{baseline_sharpe:.2f}':<20} {f'{optimized_sharpe:.2f}':<20} {f'{optimized_sharpe - baseline_sharpe:+.2f}':<15}")

    print("-" * 85)

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)

    if optimized_wr > baseline_wr and optimized_pnl > baseline_pnl:
        print("✅ OPTIMIZED CONFIG WINS: Higher win rate AND higher P&L")
    elif optimized_pnl > baseline_pnl:
        print("✅ OPTIMIZED CONFIG WINS: Higher P&L (despite lower win rate)")
    elif optimized_wr > baseline_wr:
        print("⚠️  MIXED RESULTS: Higher win rate but lower P&L")
    else:
        print("❌ BASELINE WINS: Optimized config underperformed")

    print(f"\nTrade volume change: {optimized_trades - baseline_trades:+d} trades ({pct_change(baseline_trades, optimized_trades)})")
    print(f"P&L improvement: ${optimized_pnl - baseline_pnl:+.2f} ({pct_change(baseline_pnl, optimized_pnl)})")
    print(f"Win rate improvement: {optimized_wr - baseline_wr:+.1f} percentage points")

    print("="*80)


if __name__ == "__main__":
    main()
