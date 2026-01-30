#!/usr/bin/env python3
"""Backtest crypto latency strategy on recorded market data.

Uses data captured by record_crypto_markets.py to simulate trading
with realistic market dynamics including signal stability and cooldowns.

Usage:
    python scripts/backtest_recorded_crypto.py data/recordings/crypto_20260128.json
    python scripts/backtest_recorded_crypto.py data/recordings/crypto_*.json --combine
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@dataclass
class BacktestConfig:
    """Backtest configuration matching live strategy."""
    min_edge_pct: float = 0.15
    min_time_to_expiry_sec: int = 120
    max_time_to_expiry_sec: int = 900
    signal_stability_duration_sec: float = 2.0
    slippage_adjusted_edge: bool = True
    expected_slippage_cents: int = 3
    market_cooldown_enabled: bool = True
    kelly_fraction: float = 0.5
    bankroll: float = 100.0
    max_position_per_market: float = 50.0
    fee_per_contract_cents: int = 3


@dataclass
class Trade:
    """A simulated trade."""
    ticker: str
    asset: str
    side: str  # 'yes' or 'no'
    contracts: int
    entry_price_cents: int
    entry_time: str
    implied_prob: float
    market_prob: float
    edge: float
    spot_price: float
    result: Optional[str] = None  # 'yes' or 'no' (settlement)
    pnl: float = 0.0


@dataclass
class SignalState:
    """Track signal stability."""
    first_seen: float = 0.0
    direction: str = ""
    edge: float = 0.0


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp to unix time."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except:
        return 0.0


def normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2)
    return 0.5 * (1.0 + sign * y)


def calculate_implied_prob(
    spot: float,
    strike: float,
    time_sec: float,
    volatility: float = 0.5,
) -> float:
    """Calculate implied probability using Black-Scholes."""
    if spot <= 0 or strike <= 0 or time_sec <= 0:
        return 0.5

    time_years = time_sec / (365.25 * 24 * 60 * 60)
    vol_sqrt_t = volatility * math.sqrt(time_years)

    if vol_sqrt_t <= 0:
        return 1.0 if spot > strike else 0.0

    d2 = (math.log(spot / strike) - 0.5 * volatility ** 2 * time_years) / vol_sqrt_t
    return max(0.01, min(0.99, normal_cdf(d2)))


def run_backtest(
    snapshots: List[dict],
    settlements: Dict[str, str],
    markets: Dict[str, dict],
    config: BacktestConfig,
) -> Tuple[List[Trade], float]:
    """Run backtest on recorded data."""

    trades: List[Trade] = []
    positions: Dict[str, Trade] = {}  # ticker -> trade
    cooled_markets: set = set()
    signal_states: Dict[str, SignalState] = {}
    bankroll = config.bankroll

    # Sort snapshots by timestamp
    snapshots = sorted(snapshots, key=lambda s: s["timestamp"])

    for snap in snapshots:
        ticker = snap["ticker"]
        timestamp = parse_timestamp(snap["timestamp"])

        # Skip if we already have a position or market is cooled
        if ticker in positions:
            continue
        if config.market_cooldown_enabled and ticker in cooled_markets:
            continue

        # Get market info
        market_info = markets.get(ticker, {})
        close_time = parse_timestamp(snap.get("close_time", ""))

        if not close_time:
            continue

        time_to_expiry = close_time - timestamp

        # Check time bounds
        if time_to_expiry < config.min_time_to_expiry_sec:
            continue
        if time_to_expiry > config.max_time_to_expiry_sec:
            continue

        # Get prices
        spot_price = snap.get("spot_price")
        yes_bid = snap.get("yes_bid", 0)
        yes_ask = snap.get("yes_ask", 0)
        no_bid = snap.get("no_bid", 0)
        no_ask = snap.get("no_ask", 0)

        if not spot_price or not yes_ask:
            continue

        # Parse strike from title or use approximation
        # For "up" markets, strike is typically the price at market open
        # We'll approximate using the spot price and market prices
        strike = market_info.get("strike") or spot_price * 0.999  # Approximate

        # Calculate implied probability
        implied_prob = calculate_implied_prob(spot_price, strike, time_to_expiry)

        # Market probability from yes price
        market_prob = (yes_bid + yes_ask) / 200.0 if yes_ask else 0.5

        # Calculate raw edge
        raw_edge = implied_prob - market_prob

        # Determine direction
        if raw_edge > 0:
            direction = "yes"
            raw_actual_edge = raw_edge
        else:
            direction = "no"
            raw_actual_edge = -raw_edge

        # Apply slippage adjustment
        if config.slippage_adjusted_edge:
            adjusted_edge = raw_actual_edge - (config.expected_slippage_cents / 100.0)
        else:
            adjusted_edge = raw_actual_edge

        # Check minimum edge
        if adjusted_edge < config.min_edge_pct:
            # Reset signal state if edge dropped
            if ticker in signal_states:
                del signal_states[ticker]
            continue

        # Check signal stability
        if ticker not in signal_states:
            signal_states[ticker] = SignalState(
                first_seen=timestamp,
                direction=direction,
                edge=adjusted_edge,
            )
            continue  # Need to wait for stability

        state = signal_states[ticker]

        # Check direction consistency
        if state.direction != direction:
            # Direction changed, reset
            signal_states[ticker] = SignalState(
                first_seen=timestamp,
                direction=direction,
                edge=adjusted_edge,
            )
            continue

        # Check stability duration
        duration = timestamp - state.first_seen
        if duration < config.signal_stability_duration_sec:
            continue

        # Signal is stable! Execute trade
        if direction == "yes":
            entry_price = yes_ask
            win_prob = implied_prob
        else:
            entry_price = no_ask if no_ask else (100 - yes_bid)
            win_prob = 1 - implied_prob

        # Calculate position size (Kelly)
        if config.kelly_fraction > 0:
            entry_decimal = entry_price / 100.0
            if entry_decimal >= 0.99:
                continue
            kelly_f = (win_prob - entry_decimal) / (1 - entry_decimal)
            kelly_f = kelly_f * config.kelly_fraction
            kelly_f = max(0, min(kelly_f, 0.15))
            bet_dollars = bankroll * kelly_f
            bet_dollars = min(bet_dollars, config.max_position_per_market)
            contracts = int(bet_dollars / entry_decimal)
        else:
            contracts = 5  # Fixed sizing

        if contracts <= 0:
            continue

        trade = Trade(
            ticker=ticker,
            asset=snap.get("asset", ""),
            side=direction,
            contracts=contracts,
            entry_price_cents=entry_price,
            entry_time=snap["timestamp"],
            implied_prob=implied_prob,
            market_prob=market_prob,
            edge=adjusted_edge,
            spot_price=spot_price,
        )

        positions[ticker] = trade
        trades.append(trade)

        # Clear signal state
        del signal_states[ticker]

    # Settle all positions
    for ticker, trade in positions.items():
        result = settlements.get(ticker)
        trade.result = result

        if result:
            won = (trade.side == result)
            entry_decimal = trade.entry_price_cents / 100.0

            if won:
                trade.pnl = (1 - entry_decimal) * trade.contracts
            else:
                trade.pnl = -entry_decimal * trade.contracts

            # Subtract fees
            trade.pnl -= (config.fee_per_contract_cents / 100.0) * trade.contracts

            bankroll += trade.pnl

        # Add to cooldown
        if config.market_cooldown_enabled:
            cooled_markets.add(ticker)

    return trades, bankroll


def main():
    parser = argparse.ArgumentParser(description="Backtest on recorded crypto data")
    parser.add_argument("files", nargs="+", help="Recording JSON file(s)")
    parser.add_argument("--edge", type=float, default=0.15, help="Min edge (default: 0.15)")
    parser.add_argument("--stability", type=float, default=2.0, help="Signal stability seconds")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Starting bankroll")
    parser.add_argument("--kelly", type=float, default=0.5, help="Kelly fraction")
    parser.add_argument("--no-cooldown", action="store_true", help="Disable cooldown")
    parser.add_argument("--no-slippage", action="store_true", help="Disable slippage adjustment")
    args = parser.parse_args()

    # Load all data files
    all_snapshots = []
    all_settlements = {}
    all_markets = {}

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"File not found: {path}")
            continue

        with open(path) as f:
            data = json.load(f)

        all_snapshots.extend(data.get("snapshots", []))
        all_settlements.update({s["ticker"]: s["result"] for s in data.get("settlements", [])})
        all_markets.update(data.get("markets", {}))

        print(f"Loaded {path.name}: {len(data.get('snapshots', []))} snapshots")

    if not all_snapshots:
        print("No data to backtest!")
        return 1

    print()
    print("=" * 60)
    print("BACKTEST ON RECORDED DATA")
    print("=" * 60)
    print(f"Total snapshots: {len(all_snapshots)}")
    print(f"Total settlements: {len(all_settlements)}")
    print(f"Markets tracked: {len(all_markets)}")
    print()
    print(f"Min Edge: {args.edge * 100:.0f}%")
    print(f"Signal Stability: {args.stability}s")
    print(f"Slippage Adjustment: {'OFF' if args.no_slippage else 'ON (3c)'}")
    print(f"Cooldown: {'OFF' if args.no_cooldown else 'ON'}")
    print(f"Starting Bankroll: ${args.bankroll:.2f}")
    print("=" * 60)

    config = BacktestConfig(
        min_edge_pct=args.edge,
        signal_stability_duration_sec=args.stability,
        slippage_adjusted_edge=not args.no_slippage,
        market_cooldown_enabled=not args.no_cooldown,
        kelly_fraction=args.kelly,
        bankroll=args.bankroll,
    )

    trades, final_bankroll = run_backtest(
        all_snapshots,
        all_settlements,
        all_markets,
        config,
    )

    # Results
    print()
    print("RESULTS")
    print("-" * 40)

    if not trades:
        print("No trades executed!")
        return 0

    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl <= 0)
    total_pnl = sum(t.pnl for t in trades)
    settled = sum(1 for t in trades if t.result)

    print(f"Total Trades: {len(trades)}")
    print(f"Settled: {settled}")
    print(f"Wins: {wins} ({wins/settled*100:.1f}%)" if settled else "Wins: 0")
    print(f"Losses: {losses}")
    print()
    print(f"Starting: ${args.bankroll:.2f}")
    print(f"Final: ${final_bankroll:.2f}")
    print(f"P&L: ${total_pnl:+.2f}")
    print(f"Return: {(final_bankroll/args.bankroll - 1)*100:+.1f}%")

    # By asset
    print()
    print("By Asset:")
    by_asset = defaultdict(list)
    for t in trades:
        by_asset[t.asset].append(t)

    for asset in ["BTC", "ETH", "SOL"]:
        asset_trades = by_asset.get(asset, [])
        if asset_trades:
            asset_pnl = sum(t.pnl for t in asset_trades)
            asset_wins = sum(1 for t in asset_trades if t.pnl > 0)
            asset_settled = sum(1 for t in asset_trades if t.result)
            print(f"  {asset}: {len(asset_trades)} trades, {asset_wins}/{asset_settled} wins, ${asset_pnl:+.2f}")

    # Sample trades
    print()
    print("Sample Trades:")
    for t in trades[:10]:
        result = "WIN" if t.pnl > 0 else "LOSS" if t.result else "PENDING"
        print(f"  {t.asset} {t.side.upper()} {t.contracts}x @ {t.entry_price_cents}c | "
              f"edge={t.edge*100:.1f}% | {result} ${t.pnl:+.2f}")

    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
