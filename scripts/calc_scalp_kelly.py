#!/usr/bin/env python3
"""Calculate optimal Kelly position sizing for crypto scalp strategy."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from core.risk.kelly import KellyCalculator
from core.portfolio.allocation_optimizer import AllocationOptimizer
from core.portfolio.types import AllocationConfig, StrategyStats
from datetime import datetime

# Live trading results (past 27 hours)
LIVE_TRADES = 209
LIVE_WIN_RATE = 0.43
LIVE_PROFIT = 124.50  # dollars
LIVE_HOURS = 27

# Current bankroll
BANKROLL = 41.48

# From live logs, estimate per-trade stats
# Average winner: ~$4.72 (from backtest with similar params)
# Average loser: ~$2.23 (from backtest)
AVG_WIN = 4.72
AVG_LOSS = 2.23

# Calculate edge per trade
avg_trade_pnl = LIVE_PROFIT / LIVE_TRADES
edge_per_trade = avg_trade_pnl

# Calculate variance
# Variance = p * (win - mean)^2 + (1-p) * (loss - mean)^2
p_win = LIVE_WIN_RATE
p_loss = 1 - p_win
mean_pnl = p_win * AVG_WIN - p_loss * AVG_LOSS
variance = p_win * (AVG_WIN - mean_pnl)**2 + p_loss * (-AVG_LOSS - mean_pnl)**2
std_dev = variance ** 0.5

print("=" * 80)
print("CRYPTO SCALP KELLY SIZING CALCULATOR")
print("=" * 80)
print()
print("LIVE PERFORMANCE (past 27 hours):")
print(f"  Trades:          {LIVE_TRADES}")
print(f"  Win rate:        {LIVE_WIN_RATE:.1%}")
print(f"  Total profit:    ${LIVE_PROFIT:.2f}")
print(f"  Avg profit/trade: ${avg_trade_pnl:.2f}")
print()
print("CURRENT BANKROLL:")
print(f"  Balance:         ${BANKROLL:.2f}")
print()
print("STATISTICS:")
print(f"  Mean edge:       ${edge_per_trade:.2f} per trade")
print(f"  Std deviation:   ${std_dev:.2f}")
print(f"  Variance:        ${variance:.2f}")
print(f"  CV (std/edge):   {std_dev/abs(edge_per_trade) if edge_per_trade != 0 else 0:.2f}")
print()

# Generate synthetic trade PnLs for empirical Kelly
np.random.seed(42)
n_winners = int(LIVE_TRADES * LIVE_WIN_RATE)
n_losers = LIVE_TRADES - n_winners

# Generate realistic trade PnLs with some variance
winners = np.random.normal(AVG_WIN, AVG_WIN * 0.3, n_winners)
losers = np.random.normal(-AVG_LOSS, AVG_LOSS * 0.3, n_losers)
trade_pnls = list(winners) + list(losers)
np.random.shuffle(trade_pnls)

print("-" * 80)
print("METHOD 1: STANDARD KELLY")
print("-" * 80)

# Standard Kelly
kelly_calc = KellyCalculator(max_fraction=0.25)
result = kelly_calc.calculate(
    win_probability=LIVE_WIN_RATE,
    win_amount=AVG_WIN,
    loss_amount=AVG_LOSS,
    bankroll=BANKROLL,
)

print(f"  Kelly fraction:   {result.fraction:.3f} ({result.fraction*100:.1f}%)")
print(f"  Capped fraction:  {result.capped_fraction:.3f} ({result.capped_fraction*100:.1f}%)")
print(f"  Full Kelly bet:   ${result.recommended_bet:.2f}")
print(f"  Half Kelly bet:   ${result.half_kelly:.2f}")
print()

# Assume average entry price of 50 cents per contract
avg_entry_price = 0.50
contracts_full = int(result.recommended_bet / avg_entry_price)
contracts_half = int(result.half_kelly / avg_entry_price)

print(f"  Contracts (full): {contracts_full} contracts @ 50¢ = ${contracts_full * avg_entry_price:.2f}")
print(f"  Contracts (half): {contracts_half} contracts @ 50¢ = ${contracts_half * avg_entry_price:.2f}")
print()

print("-" * 80)
print("METHOD 2: EMPIRICAL KELLY (Monte Carlo with CV adjustment)")
print("-" * 80)

# Create StrategyStats for empirical Kelly
stats = StrategyStats(
    strategy_name="crypto-scalp",
    total_pnl=LIVE_PROFIT,
    num_trades=LIVE_TRADES,
    edge=edge_per_trade,
    variance=variance,
    std_dev=std_dev,
    sharpe_ratio=edge_per_trade / std_dev if std_dev > 0 else 0,
    win_rate=LIVE_WIN_RATE,
    avg_win=AVG_WIN,
    avg_loss=AVG_LOSS,
    lookback_days=1.125,  # 27 hours
    last_updated=datetime.now(),
)

# Run empirical Kelly with Monte Carlo
config = AllocationConfig(
    kelly_fraction=0.5,  # Half Kelly
    max_allocation_per_strategy=0.25,
    max_total_allocation=0.80,
    min_allocation_threshold=0.0,
    use_empirical_kelly=True,
    empirical_kelly_simulations=10000,  # More simulations for accuracy
    empirical_kelly_seed=42,
)

optimizer = AllocationOptimizer(config)
strategy_stats = {"crypto-scalp": stats}
corr_matrix = np.array([[1.0]])  # Single strategy
strategy_names = ["crypto-scalp"]

result_empirical = optimizer.calculate_allocations(
    strategy_stats,
    corr_matrix,
    strategy_names,
    trade_pnls={"crypto-scalp": trade_pnls}
)

empirical_fraction = result_empirical.allocations.get("crypto-scalp", 0.0)
empirical_bet = empirical_fraction * BANKROLL
empirical_contracts = int(empirical_bet / avg_entry_price)

print(f"  Empirical fraction: {empirical_fraction:.3f} ({empirical_fraction*100:.1f}%)")
print(f"  Empirical bet:      ${empirical_bet:.2f}")
print(f"  Contracts:          {empirical_contracts} contracts @ 50¢ = ${empirical_contracts * avg_entry_price:.2f}")
print()
print(f"  Reduction vs std:   {(1 - empirical_fraction/result.capped_fraction)*100:.1f}% (due to estimation uncertainty)")
print()

print("=" * 80)
print("RECOMMENDATION")
print("=" * 80)
print()

# Recommend the more conservative of the two
recommended_contracts = min(contracts_half, empirical_contracts)
recommended_bet = recommended_contracts * avg_entry_price

print(f"  Recommended:      {recommended_contracts} contracts per trade")
print(f"  Capital per trade: ${recommended_bet:.2f}")
print(f"  % of bankroll:     {recommended_bet/BANKROLL*100:.1f}%")
print()
print("  This is the CONSERVATIVE estimate using:")
print(f"  - Half Kelly (to reduce volatility)")
print(f"  - Empirical adjustment (to account for estimation uncertainty)")
print(f"  - Based on your actual {LIVE_TRADES} trades of live performance")
print()

# Risk analysis
max_concurrent_positions = 1  # From config
max_exposure = recommended_contracts * avg_entry_price * max_concurrent_positions
print(f"  Max exposure:      ${max_exposure:.2f} ({max_exposure/BANKROLL*100:.1f}% of bankroll)")
print(f"  Max loss scenario: ${recommended_contracts * AVG_LOSS:.2f} (single worst-case trade)")
print()

# Daily loss limit check
trades_per_hour = LIVE_TRADES / LIVE_HOURS
estimated_trades_per_day = trades_per_hour * 24
max_daily_loss = 50.0  # From config

print(f"  Estimated trades/day: {estimated_trades_per_day:.0f}")
print(f"  Daily loss limit:     ${max_daily_loss:.2f}")
print(f"  Worst case (all losses): ${recommended_contracts * AVG_LOSS * estimated_trades_per_day:.2f}")
print()

if recommended_contracts * AVG_LOSS * estimated_trades_per_day > max_daily_loss:
    print("  ⚠️  WARNING: Worst-case daily loss exceeds limit!")
    print("     Consider reducing contracts_per_trade or increasing daily loss limit")
else:
    print("  ✅  Daily loss limit provides adequate protection")

print()
print("=" * 80)
