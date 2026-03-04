#!/usr/bin/env python3
"""
Late Game Blowout Strategy - Full Evaluation Suite

Backtests the blowout strategy against all complete game recordings:
1. Scans Q4+ for blowout conditions (lead >= threshold, <= 10 min remaining)
2. Simulates trades using actual market prices from recordings
3. Tracks stop losses and outcomes
4. Generates comprehensive metrics and reports

Usage:
    python scripts/backtest_blowout.py
    python scripts/backtest_blowout.py --min-lead 15 --verbose
    python scripts/backtest_blowout.py --compare-thresholds
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import json
import glob
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np

from src.kalshi.fees import calculate_fee
from strategies.late_game_blowout_strategy import (
    LateGameBlowoutStrategy,
    BlowoutStrategyConfig,
    BlowoutSide,
    calculate_stop_loss_from_stats,
)


@dataclass
class BlowoutTrade:
    """A simulated blowout trade."""

    game_id: str
    recording_path: str
    home_team: str
    away_team: str

    # Entry conditions
    entry_timestamp: float
    entry_period: int
    entry_time_remaining: str
    entry_time_seconds: int
    leading_team: str  # 'home' or 'away'
    entry_lead: int
    entry_home_score: int
    entry_away_score: int

    # Prices at entry
    entry_price: float  # Price we paid
    confidence: str
    position_size: float

    # Contracts (for fee calculation)
    contracts: int = 0

    # Fee tracking
    entry_fee: float = 0.0
    exit_fee: float = 0.0

    # Price-based exit tracking
    high_water_price: float = 0.0
    low_water_price: float = 1.0
    exit_price: Optional[float] = None
    exit_reason: str = ""  # 'resolution', 'take_profit', 'trailing_stop', 'price_stop'

    # Stop loss tracking (legacy score-based)
    stopped_out: bool = False
    stop_loss_lead: Optional[int] = None
    stop_loss_price: Optional[float] = None
    stop_loss_time: Optional[str] = None

    # Final outcome
    final_home_score: int = 0
    final_away_score: int = 0
    winner: str = ""  # 'home' or 'away'

    # P&L
    result: str = ""  # 'win', 'loss', 'stopped'
    pnl: float = 0.0


@dataclass
class BlowoutBacktestResult:
    """Complete result from blowout strategy backtest."""

    config: BlowoutStrategyConfig

    # Games processed
    total_recordings: int = 0
    complete_games: int = 0
    games_with_opportunities: int = 0

    # Trades
    trades: List[BlowoutTrade] = field(default_factory=list)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    stopped_out: int = 0

    # P&L
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    # Rates
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0

    # By confidence tier
    by_confidence: Dict[str, Dict] = field(default_factory=dict)

    # By lead size
    by_lead_range: Dict[str, Dict] = field(default_factory=dict)

    # Fee totals
    total_fees: float = 0.0

    # Exit reason counts
    exit_reasons: Dict[str, int] = field(default_factory=dict)

    # Calculated stop loss recommendation
    stop_loss_calc: Optional[Dict] = None

    def calculate_metrics(self):
        """Calculate all derived metrics."""
        self.total_trades = len(self.trades)
        self.wins = sum(1 for t in self.trades if t.result == "win")
        self.losses = sum(1 for t in self.trades if t.result == "loss")
        self.stopped_out = sum(1 for t in self.trades if t.result == "stopped")

        self.total_fees = sum(t.entry_fee + t.exit_fee for t in self.trades)

        # Count exit reasons
        self.exit_reasons = {}
        for t in self.trades:
            reason = t.exit_reason or t.result
            self.exit_reasons[reason] = self.exit_reasons.get(reason, 0) + 1

        self.total_pnl = sum(t.pnl for t in self.trades)
        self.gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        self.gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))

        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades

        winning_trades = [t for t in self.trades if t.pnl > 0]
        losing_trades = [t for t in self.trades if t.pnl < 0]

        if winning_trades:
            self.avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades)
        if losing_trades:
            self.avg_loss = abs(sum(t.pnl for t in losing_trades) / len(losing_trades))

        if self.gross_loss > 0:
            self.profit_factor = self.gross_profit / self.gross_loss

        # By confidence
        for conf in ["medium", "high", "very_high"]:
            conf_trades = [t for t in self.trades if t.confidence == conf]
            if conf_trades:
                self.by_confidence[conf] = {
                    "count": len(conf_trades),
                    "wins": sum(1 for t in conf_trades if t.result == "win"),
                    "pnl": sum(t.pnl for t in conf_trades),
                    "win_rate": sum(1 for t in conf_trades if t.result == "win")
                    / len(conf_trades),
                }

        # By lead range
        for range_name, (low, high) in [
            ("12-14", (12, 14)),
            ("15-19", (15, 19)),
            ("20+", (20, 100)),
        ]:
            range_trades = [t for t in self.trades if low <= t.entry_lead <= high]
            if range_trades:
                self.by_lead_range[range_name] = {
                    "count": len(range_trades),
                    "wins": sum(1 for t in range_trades if t.result == "win"),
                    "pnl": sum(t.pnl for t in range_trades),
                    "win_rate": sum(1 for t in range_trades if t.result == "win")
                    / len(range_trades),
                }

        # Calculate recommended stop loss from NATURAL win rate
        # Check what would have happened at resolution for ALL trades (including
        # stopped ones) using leading_team vs winner, so the stop doesn't
        # degrade the stats the calculation depends on.
        if self.trades:
            # Natural outcome: did the leading team win the game?
            natural_wins = sum(1 for t in self.trades if t.leading_team == t.winner)
            natural_win_rate = natural_wins / len(self.trades)
            # Avg win P&L at resolution: (1.0 - entry_price) * contracts - entry_fee
            would_win_trades = [t for t in self.trades if t.leading_team == t.winner]
            natural_avg_win = (
                np.mean(
                    [
                        (1.0 - t.entry_price) * t.contracts - t.entry_fee
                        for t in would_win_trades
                    ]
                )
                if would_win_trades
                else 0
            )
            avg_contracts = np.mean([t.contracts for t in self.trades])
            avg_entry_fee = np.mean([t.entry_fee for t in self.trades])
            # Estimate exit fee at a typical stop price
            avg_entry_price = np.mean([t.entry_price for t in self.trades])
            est_exit_fee = calculate_fee(
                max(0.01, avg_entry_price - 0.05), int(avg_contracts), maker=True
            )
            self.stop_loss_calc = calculate_stop_loss_from_stats(
                win_rate=natural_win_rate,
                avg_win=natural_avg_win,
                avg_contracts=avg_contracts,
                avg_entry_price=avg_entry_price,
                avg_entry_fee=avg_entry_fee,
                avg_exit_fee=est_exit_fee,
            )


class BlowoutBacktester:
    """Backtester for late game blowout strategy."""

    def __init__(
        self,
        config: Optional[BlowoutStrategyConfig] = None,
        price_stop_loss: int = 5,
        trailing_stop: int = 0,
        take_profit: int = 0,
    ):
        self.config = config or BlowoutStrategyConfig()
        self.strategy = LateGameBlowoutStrategy(self.config)
        self.price_stop_loss = price_stop_loss  # cents
        self.trailing_stop = trailing_stop  # cents
        self.take_profit = take_profit  # cents

    def run(
        self, recording_paths: List[str], verbose: bool = False
    ) -> BlowoutBacktestResult:
        """Run backtest across all recordings."""
        result = BlowoutBacktestResult(config=self.config)
        result.total_recordings = len(recording_paths)

        for path in recording_paths:
            try:
                game_trades = self._backtest_game(path, verbose)
                if game_trades is not None:
                    result.complete_games += 1
                    if game_trades:
                        result.games_with_opportunities += 1
                        result.trades.extend(game_trades)
            except Exception as e:
                if verbose:
                    print(f"  Error processing {Path(path).name}: {e}")

        result.calculate_metrics()
        return result

    def _backtest_game(
        self, recording_path: str, verbose: bool = False
    ) -> Optional[List[BlowoutTrade]]:
        """Backtest a single game recording."""
        with open(recording_path) as f:
            data = json.load(f)

        frames = data.get("frames", [])
        metadata = data.get("metadata", {})

        if not frames:
            return None

        # Check if game is complete
        final = frames[-1]
        if (
            final.get("period", 0) < 4
            or "final" not in str(final.get("game_status", "")).lower()
        ):
            return None

        home_team = metadata.get("home_team", "HOME")
        away_team = metadata.get("away_team", "AWAY")
        game_id = metadata.get("game_id", recording_path)

        final_home = final.get("home_score", 0)
        final_away = final.get("away_score", 0)
        winner = "home" if final_home > final_away else "away"

        if verbose:
            print(
                f"  {away_team} @ {home_team}: {final_away}-{final_home} ({winner} won)"
            )

        trades = []
        traded = False  # Only trade once per game
        active_trade: Optional[BlowoutTrade] = None

        for frame in frames:
            period = frame.get("period", 0)

            # Only look at Q4 or later
            if period < 4:
                continue

            home_score = frame.get("home_score", 0)
            away_score = frame.get("away_score", 0)
            time_remaining = frame.get("time_remaining", "12:00")
            timestamp = frame.get("timestamp", 0)

            # Get prices
            frame.get("home_ask", 0.95)
            frame.get("away_ask", 0.95)
            home_bid = frame.get("home_bid", 0.05)
            away_bid = frame.get("away_bid", 0.05)

            # Check for price-based exits on active trade
            if active_trade and not active_trade.stopped_out:
                if active_trade.leading_team == "home":
                    current_bid = home_bid
                else:
                    current_bid = away_bid

                # Update high water mark
                active_trade.high_water_price = max(
                    active_trade.high_water_price, current_bid
                )
                active_trade.low_water_price = min(
                    active_trade.low_water_price, current_bid
                )

                exit_reason = None

                # Take profit
                if (
                    self.take_profit > 0
                    and current_bid >= active_trade.entry_price + self.take_profit / 100
                ):
                    exit_reason = "take_profit"

                # Trailing stop
                if not exit_reason and self.trailing_stop > 0:
                    if (
                        active_trade.high_water_price
                        >= active_trade.entry_price + self.trailing_stop / 100
                    ):
                        trail_level = (
                            active_trade.high_water_price - self.trailing_stop / 100
                        )
                        if current_bid <= trail_level:
                            exit_reason = "trailing_stop"

                # Fixed price stop
                if not exit_reason and self.price_stop_loss > 0:
                    if (
                        current_bid
                        <= active_trade.entry_price - self.price_stop_loss / 100
                    ):
                        exit_reason = "price_stop"

                if exit_reason:
                    active_trade.stopped_out = True
                    active_trade.exit_price = current_bid
                    active_trade.exit_reason = exit_reason
                    active_trade.stop_loss_price = current_bid
                    active_trade.stop_loss_time = time_remaining
                    active_trade.result = "stopped"
                    # Exit fee: selling at bid = maker
                    active_trade.exit_fee = calculate_fee(
                        current_bid, active_trade.contracts, maker=True
                    )
                    # P&L: (sell - buy) * contracts - fees
                    active_trade.pnl = (
                        (current_bid - active_trade.entry_price)
                        * active_trade.contracts
                        - active_trade.entry_fee
                        - active_trade.exit_fee
                    )

                    if verbose:
                        print(
                            f"    {exit_reason.upper()} @ {current_bid:.2f} ({time_remaining}) | P&L: ${active_trade.pnl:.2f}"
                        )

            # Skip if already traded this game
            if traded:
                continue

            # Check for entry signal
            signal = self.strategy.check_entry(
                home_score=home_score,
                away_score=away_score,
                period=period,
                time_remaining=time_remaining,
                timestamp=timestamp,
                game_id=game_id,
            )

            if signal:
                traded = True

                # Get entry price (bid side for maker order)
                if signal.leading_team == BlowoutSide.HOME:
                    entry_price = home_bid
                    leading = "home"
                else:
                    entry_price = away_bid
                    leading = "away"

                # Skip if price too high
                if entry_price > self.config.max_buy_price:
                    if verbose:
                        print(
                            f"    Skip: price too high ({entry_price:.2f} > {self.config.max_buy_price})"
                        )
                    continue

                position_size = self.strategy.get_position_size(signal.confidence)
                contracts = int(position_size / entry_price) if entry_price > 0 else 0

                # Entry fee: placing a bid = maker
                entry_fee = calculate_fee(entry_price, contracts, maker=True)

                trade = BlowoutTrade(
                    game_id=game_id,
                    recording_path=recording_path,
                    home_team=home_team,
                    away_team=away_team,
                    entry_timestamp=timestamp,
                    entry_period=period,
                    entry_time_remaining=time_remaining,
                    entry_time_seconds=signal.time_remaining_seconds,
                    leading_team=leading,
                    entry_lead=signal.score_differential,
                    entry_home_score=home_score,
                    entry_away_score=away_score,
                    entry_price=entry_price,
                    confidence=signal.confidence,
                    position_size=position_size,
                    contracts=contracts,
                    entry_fee=entry_fee,
                    high_water_price=entry_price,
                    low_water_price=entry_price,
                    final_home_score=final_home,
                    final_away_score=final_away,
                    winner=winner,
                )

                active_trade = trade
                trades.append(trade)

                if verbose:
                    print(
                        f"    ENTRY: {leading.upper()} +{signal.score_differential} @ {entry_price:.2f} x{contracts} ({signal.confidence}) fee=${entry_fee:.2f}"
                    )

        # Finalize trade outcomes (resolution — no exit fee on settlement)
        for trade in trades:
            if trade.result:  # Already set (stopped out)
                continue

            trade.exit_reason = "resolution"
            if trade.leading_team == trade.winner:
                trade.result = "win"
                # Settlement at 1.0, no fee on settlement
                trade.pnl = (
                    1.0 - trade.entry_price
                ) * trade.contracts - trade.entry_fee
            else:
                trade.result = "loss"
                # Contract expires worthless
                trade.pnl = -trade.entry_price * trade.contracts - trade.entry_fee

        return trades


def print_report(
    result: BlowoutBacktestResult,
    title: str = "",
    price_stop_loss: int = 5,
    trailing_stop: int = 0,
    take_profit: int = 0,
):
    """Print comprehensive backtest report."""
    print()
    print("=" * 70)
    if title:
        print(f"  {title}")
    print("  LATE GAME BLOWOUT STRATEGY - BACKTEST RESULTS")
    print("=" * 70)

    # Config
    print("\nConfiguration:")
    print(f"  Min Lead Required:      {result.config.min_point_differential} points")
    print(
        f"  Max Time Remaining:     {result.config.max_time_remaining_seconds // 60} minutes"
    )
    print(f"  Max Buy Price:          {result.config.max_buy_price:.0%}")
    print("  Entry:                  Bid (maker)")
    print(
        f"  Price Stop Loss:        {price_stop_loss}c"
        if price_stop_loss > 0
        else "  Price Stop Loss:        disabled"
    )
    print(
        f"  Trailing Stop:          {trailing_stop}c"
        if trailing_stop > 0
        else "  Trailing Stop:          disabled"
    )
    print(
        f"  Take Profit:            {take_profit}c"
        if take_profit > 0
        else "  Take Profit:            disabled"
    )

    # Summary
    print("\nData Summary:")
    print(f"  Total Recordings:       {result.total_recordings}")
    print(f"  Complete Games:         {result.complete_games}")
    print(f"  Games w/ Opportunities: {result.games_with_opportunities}")

    print("\nTrade Summary:")
    print(f"  Total Trades:           {result.total_trades}")
    print(f"  Wins:                   {result.wins}")
    print(f"  Losses:                 {result.losses}")
    print(f"  Stopped Out:            {result.stopped_out}")
    print(f"  Win Rate:               {result.win_rate:.1%}")

    print("\nP&L Summary:")
    print(f"  Total P&L:              ${result.total_pnl:+.2f}")
    print(f"  Gross Profit:           ${result.gross_profit:.2f}")
    print(f"  Gross Loss:             ${result.gross_loss:.2f}")
    print(f"  Total Fees:             ${result.total_fees:.2f}")
    print(f"  Profit Factor:          {result.profit_factor:.2f}")
    print(f"  Average Win:            ${result.avg_win:.2f}")
    print(f"  Average Loss:           ${result.avg_loss:.2f}")

    if result.exit_reasons:
        print("\nExit Reasons:")
        for reason, count in sorted(result.exit_reasons.items()):
            print(f"  {reason:<20} {count:>5}")

    # By confidence
    if result.by_confidence:
        print("\nBy Confidence Tier:")
        print(f"  {'Tier':<12} {'Trades':>8} {'Wins':>8} {'Win%':>8} {'P&L':>10}")
        print(f"  {'-' * 50}")
        for conf, stats in result.by_confidence.items():
            print(
                f"  {conf:<12} {stats['count']:>8} {stats['wins']:>8} "
                f"{stats['win_rate']:>7.1%} ${stats['pnl']:>9.2f}"
            )

    # By lead range
    if result.by_lead_range:
        print("\nBy Lead Size:")
        print(f"  {'Range':<12} {'Trades':>8} {'Wins':>8} {'Win%':>8} {'P&L':>10}")
        print(f"  {'-' * 50}")
        for range_name, stats in result.by_lead_range.items():
            print(
                f"  {range_name:<12} {stats['count']:>8} {stats['wins']:>8} "
                f"{stats['win_rate']:>7.1%} ${stats['pnl']:>9.2f}"
            )

    # Calculated stop loss recommendation
    if result.stop_loss_calc and "error" not in result.stop_loss_calc:
        sl = result.stop_loss_calc
        print("\nCalculated Stop Loss (from natural win rate, all trades):")
        print(f"  Natural Win Rate:       {sl['win_rate']:.1%}")
        print(f"  Avg Win (resolution):   ${sl['avg_win']:.2f}")
        print(f"  Avg Entry Price:        {sl['avg_entry_price']:.2f}")
        print(f"  Avg Contracts:          {sl['avg_contracts']:.1f}")
        print(f"  Avg Fees/Trade:         ${sl['avg_fees_per_trade']:.2f}")
        print("  ---")
        if sl.get("breakeven_capped"):
            max_cents = sl["avg_entry_price"] * 100
            print(
                f"  Breakeven Max Loss:     > max possible (capped at {max_cents:.0f}c per contract)"
            )
            print(
                "  Recommendation:         No stop needed — win rate supports riding to resolution"
            )
        else:
            print(
                f"  Breakeven Max Loss:     ${sl['breakeven_loss_dollars']:.2f}  ({sl['breakeven_cents']:.0f}c per contract)"
            )
            print(
                f"  Recommended Stop:       ${sl['recommended_loss_dollars']:.2f}  ({sl['recommended_cents']:.0f}c per contract)"
            )
        print(f"  Safety Margin:          {sl['safety_margin']:.0%}")

    # Trade details
    if result.trades:
        print("\nTrade Details:")
        print(
            f"  {'Game':<20} {'Lead':>6} {'Time':>8} {'Price':>7} {'Ct':>4} {'Exit':>14} {'Fees':>6} {'P&L':>8}"
        )
        print(f"  {'-' * 78}")
        for t in result.trades:
            game = f"{t.away_team}@{t.home_team}"
            lead = f"+{t.entry_lead}"
            time = t.entry_time_remaining.replace(" ", "")[:7]
            exit_str = t.exit_reason or t.result
            fees = t.entry_fee + t.exit_fee
            print(
                f"  {game:<20} {lead:>6} {time:>8} {t.entry_price:>6.2f} {t.contracts:>4} "
                f"{exit_str:>14} ${fees:>5.2f} ${t.pnl:>7.2f}"
            )

    print()
    print("=" * 70)


def compare_thresholds(recording_paths: List[str]):
    """Compare different threshold configurations."""
    print("\n" + "=" * 70)
    print("  THRESHOLD COMPARISON")
    print("=" * 70)

    configs = [
        ("Lead 10+", BlowoutStrategyConfig(min_point_differential=10)),
        ("Lead 12+", BlowoutStrategyConfig(min_point_differential=12)),
        ("Lead 15+", BlowoutStrategyConfig(min_point_differential=15)),
        ("Lead 20+", BlowoutStrategyConfig(min_point_differential=20)),
        (
            "Lead 12+, 5min max",
            BlowoutStrategyConfig(
                min_point_differential=12, max_time_remaining_seconds=300
            ),
        ),
        (
            "Lead 15+, 5min max",
            BlowoutStrategyConfig(
                min_point_differential=15, max_time_remaining_seconds=300
            ),
        ),
    ]

    print(f"\n{'Config':<22} {'Trades':>7} {'Win%':>7} {'P&L':>10} {'PF':>6}")
    print("-" * 60)

    results = []
    for name, config in configs:
        backtester = BlowoutBacktester(config)
        result = backtester.run(recording_paths, verbose=False)

        pf = f"{result.profit_factor:.2f}" if result.profit_factor > 0 else "N/A"
        print(
            f"{name:<22} {result.total_trades:>7} {result.win_rate:>6.1%} "
            f"${result.total_pnl:>9.2f} {pf:>6}"
        )

        results.append((name, result))

    # Find best config
    best = max(results, key=lambda x: x[1].total_pnl)
    print(f"\nBest by P&L: {best[0]} (${best[1].total_pnl:.2f})")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Backtest Late Game Blowout Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--min-lead",
        type=int,
        default=12,
        help="Minimum point lead required (default: 12)",
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=600,
        help="Max time remaining in seconds (default: 600)",
    )
    parser.add_argument(
        "--max-price", type=float, default=0.92, help="Max buy price (default: 0.92)"
    )
    parser.add_argument(
        "--position-size",
        type=float,
        default=5.0,
        help="Base position size in dollars (default: 5.0)",
    )

    parser.add_argument(
        "--price-stop-loss",
        type=int,
        default=5,
        help="Exit if bid drops this many cents below entry (default: 5, 0=disabled)",
    )
    parser.add_argument(
        "--trailing-stop",
        type=int,
        default=0,
        help="Trail stop this many cents below high water mark (default: 0=disabled)",
    )
    parser.add_argument(
        "--take-profit",
        type=int,
        default=0,
        help="Exit if bid rises this many cents above entry (default: 0=disabled)",
    )

    parser.add_argument(
        "--recordings",
        "-r",
        type=str,
        nargs="+",
        default=None,
        help="Specific recording files (glob patterns supported)",
    )
    parser.add_argument(
        "--compare-thresholds",
        "-c",
        action="store_true",
        help="Compare different threshold configurations",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--output", "-o", type=str, default=None, help="Save results to JSON file"
    )

    args = parser.parse_args()

    # Collect recordings
    if args.recordings:
        recording_paths = []
        for pattern in args.recordings:
            recording_paths.extend(glob.glob(pattern))
    else:
        recording_paths = glob.glob("data/recordings/*.json")

    print(f"Found {len(recording_paths)} recordings")

    if args.compare_thresholds:
        compare_thresholds(recording_paths)
        return

    # Build config
    config = BlowoutStrategyConfig(
        min_point_differential=args.min_lead,
        max_time_remaining_seconds=args.max_time,
        max_buy_price=args.max_price,
        base_position_size=args.position_size,
    )

    # Run backtest
    backtester = BlowoutBacktester(
        config,
        price_stop_loss=args.price_stop_loss,
        trailing_stop=args.trailing_stop,
        take_profit=args.take_profit,
    )
    result = backtester.run(recording_paths, verbose=args.verbose)

    # Print report
    print_report(
        result,
        price_stop_loss=args.price_stop_loss,
        trailing_stop=args.trailing_stop,
        take_profit=args.take_profit,
    )

    # Save if requested
    if args.output:
        output_data = {
            "config": {
                "min_lead": config.min_point_differential,
                "max_time_seconds": config.max_time_remaining_seconds,
                "max_buy_price": config.max_buy_price,
                "price_stop_loss_cents": args.price_stop_loss,
                "trailing_stop_cents": args.trailing_stop,
                "take_profit_cents": args.take_profit,
            },
            "summary": {
                "total_recordings": result.total_recordings,
                "complete_games": result.complete_games,
                "total_trades": result.total_trades,
                "wins": result.wins,
                "losses": result.losses,
                "stopped_out": result.stopped_out,
                "win_rate": result.win_rate,
                "total_pnl": result.total_pnl,
                "total_fees": result.total_fees,
                "profit_factor": result.profit_factor,
            },
            "exit_reasons": result.exit_reasons,
            "by_confidence": result.by_confidence,
            "by_lead_range": result.by_lead_range,
            "trades": [
                {
                    "game": f"{t.away_team}@{t.home_team}",
                    "lead": t.entry_lead,
                    "time_remaining": t.entry_time_remaining,
                    "entry_price": t.entry_price,
                    "contracts": t.contracts,
                    "confidence": t.confidence,
                    "result": t.result,
                    "exit_reason": t.exit_reason,
                    "entry_fee": t.entry_fee,
                    "exit_fee": t.exit_fee,
                    "pnl": t.pnl,
                }
                for t in result.trades
            ],
        }

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
