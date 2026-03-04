#!/usr/bin/env python3
"""
Replay edge capture strategy on recorded NBA games.

Runs the strategy's full async execution path (entry -> monitor -> exit)
driven by recorded frame data using a simulated clock. The Markov win
probability model provides fair-value estimates from frame-level score data.

Usage:
    python scripts/replay_edge_capture.py                        # First 5 games
    python scripts/replay_edge_capture.py --all                  # All recordings
    python scripts/replay_edge_capture.py --recording FILE       # Single game
    python scripts/replay_edge_capture.py --all --min-edge 3 -v  # Lower edge, verbose
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.orderbook_manager import OrderBookLevel, OrderBookState
from strategies.sim_clock import SimulatedClock, make_sim_wait_for_event, sim_sleep
from strategies.edge_capture_strategy import (
    EdgeCaptureConfig,
    EdgeCaptureState,
    EdgeCaptureStrategy,
    MarkovProbabilityProvider,
)
from signal_extraction.models.markov_win_model import (
    GameState as MarkovGameState,
    SportType,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Data Loading & Conversion
# =============================================================================


@dataclass
class ReplayStats:
    """Statistics from replaying a recording."""

    recording_file: str
    total_frames: int

    # Trade stats
    total_trades: int = 0
    entries_filled: int = 0
    buy_yes_trades: int = 0
    buy_no_trades: int = 0

    # Exit reasons
    exit_reasons: dict = field(default_factory=dict)

    # P&L
    total_pnl: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0


def load_recording(filepath: Path) -> Tuple[dict, List[dict]]:
    """Load a recording file and return metadata + frames."""
    with open(filepath) as f:
        data = json.load(f)
    return data.get("metadata", {}), data.get("frames", [])


def frame_to_orderbook(frame: dict, ticker: str) -> OrderBookState:
    """Convert a recording frame to an OrderBookState."""
    if "home_ticker" in frame and frame["home_ticker"] == ticker:
        prefix = "home_"
    elif "away_ticker" in frame and frame["away_ticker"] == ticker:
        prefix = "away_"
    else:
        prefix = "home_" if "home_bid" in frame else ""

    bid_price = int(frame.get(f"{prefix}bid", 0.5) * 100)
    ask_price = int(frame.get(f"{prefix}ask", 0.5) * 100)
    volume = frame.get("volume", 0)

    bid_price = max(1, min(99, bid_price))
    ask_price = max(1, min(99, ask_price))

    if bid_price >= ask_price:
        ask_price = bid_price + 1
        if ask_price > 99:
            bid_price = 98
            ask_price = 99

    bids = [OrderBookLevel(price=bid_price, size=50)]
    asks = [OrderBookLevel(price=ask_price, size=50)]

    return OrderBookState(
        ticker=ticker,
        bids=bids,
        asks=asks,
        sequence=0,
        volume_24h=volume,
    )


# =============================================================================
# Frame -> GameState conversion for NBA
# =============================================================================


def _parse_time_remaining(time_str: str) -> float:
    """Parse time string to seconds remaining in current period."""
    try:
        # Handle "Q3 2:25" format
        if " " in time_str:
            time_str = time_str.split(" ", 1)[1]
        # Handle "Half", "End", "Final" etc.
        if ":" not in time_str:
            return 0.0
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return float(time_str)
    except (ValueError, AttributeError):
        return 0.0


def _nba_game_time_remaining(period: int, period_time_str: str) -> float:
    """Calculate total game time remaining in seconds for NBA (4 x 12min)."""
    period_seconds = _parse_time_remaining(period_time_str)
    if period <= 0:
        period = 1
    remaining_full_periods = max(0, 4 - period)
    return period_seconds + remaining_full_periods * 12 * 60


def frame_to_game_state(frame: dict) -> MarkovGameState:
    """Convert a recording frame to a MarkovGameState."""
    home_score = frame.get("home_score", 0)
    away_score = frame.get("away_score", 0)
    period = frame.get("period", 1)
    time_str = frame.get("time_remaining", "12:00")

    # Extract period from time_remaining like "Q3 2:25"
    if time_str.startswith("Q") and " " in time_str:
        try:
            period = int(time_str[1])
        except (ValueError, IndexError):
            pass

    time_remaining = _nba_game_time_remaining(period, time_str)

    return MarkovGameState(
        score_diff=home_score - away_score,
        time_remaining=time_remaining,
        period=period,
        home_possession=True,
        momentum=0.0,
    )


# =============================================================================
# Core Replay
# =============================================================================


async def replay_recording(
    filepath: Path,
    config: EdgeCaptureConfig,
    verbose: bool = False,
    calibration: str = "shrink",
    shrink_factor: float = 0.70,
) -> ReplayStats:
    """Replay a single recording through the edge capture strategy.

    For each frame:
      1. Advance simulated clock
      2. Compute GameState from frame scores -> update MarkovProbabilityProvider
      3. Inject orderbooks
      4. Check fills + check opportunities
      5. Yield to event loop for execution/monitor tasks
    """
    metadata, frames = load_recording(filepath)
    stats = ReplayStats(
        recording_file=filepath.name,
        total_frames=len(frames),
    )

    if not frames:
        logger.warning(f"No frames in {filepath}")
        return stats

    home_ticker = metadata.get("home_ticker", frames[0].get("home_ticker", "HOME"))
    away_ticker = metadata.get("away_ticker", frames[0].get("away_ticker", "AWAY"))
    tickers = [home_ticker, away_ticker]

    home_team = metadata.get("home_team", "?")
    away_team = metadata.get("away_team", "?")
    final_home = metadata.get("final_home_score", "?")
    final_away = metadata.get("final_away_score", "?")

    logger.info(
        f"Replaying {filepath.name}: {away_team} @ {home_team} "
        f"(final: {final_away}-{final_home})"
    )
    logger.info(f"  Tickers: {home_ticker}, {away_ticker}")
    logger.info(f"  Frames: {len(frames)}")

    # --- Build simulated-time strategy ---
    base_ts = frames[0].get("timestamp", 0)
    clock = SimulatedClock(start_time=base_ts)

    provider = MarkovProbabilityProvider(
        SportType.NBA,
        calibration=calibration,
        shrink_factor=shrink_factor,
    )

    strategy = EdgeCaptureStrategy(
        config=config,
        provider=provider,
        dry_run=True,
        clock=clock,
        sleep=sim_sleep,
        wait_for_event=make_sim_wait_for_event(clock),
    )
    strategy._running = True
    strategy._subscribed_tickers = set(tickers)

    # --- Main replay loop ---
    for i, frame in enumerate(frames):
        ts = frame.get("timestamp", base_ts + i * 2)
        clock.advance_to(ts)

        # Update game state -> probability provider
        game_state = frame_to_game_state(frame)
        for ticker in tickers:
            # For the home ticker, use home_score - away_score (positive = home leading)
            # For the away ticker, flip the score diff
            if ticker == home_ticker:
                provider.set_game_state(ticker, game_state)
            else:
                # Away ticker: flip perspective
                away_state = MarkovGameState(
                    score_diff=-game_state.score_diff,
                    time_remaining=game_state.time_remaining,
                    period=game_state.period,
                    home_possession=game_state.home_possession,
                    momentum=-game_state.momentum,
                )
                provider.set_game_state(ticker, away_state)

        # Inject orderbooks
        for ticker in tickers:
            book = frame_to_orderbook(frame, ticker)
            strategy.update_orderbook(ticker, book)

        # Check fills
        strategy.check_fills()

        # Check for new opportunities
        for ticker in tickers:
            book = strategy.get_orderbook(ticker)
            if book:
                strategy._check_opportunity(ticker, book)

        # Yield to event loop for execution/monitor tasks
        for _ in range(10):
            await asyncio.sleep(0)

    # Force-exit remaining positions
    await strategy.stop()

    # --- Collect stats ---
    for trade in strategy._trades.values():
        if trade.state == EdgeCaptureState.CLOSED:
            stats.total_trades += 1
            if trade.entry_fill_size > 0:
                stats.entries_filled += 1
            if trade.direction == "buy_yes":
                stats.buy_yes_trades += 1
            else:
                stats.buy_no_trades += 1

            reason = trade.exit_reason or "unknown"
            stats.exit_reasons[reason] = stats.exit_reasons.get(reason, 0) + 1

            stats.total_pnl += trade.net_pnl
            if trade.net_pnl >= 0:
                stats.winning_trades += 1
            else:
                stats.losing_trades += 1

    # Summary
    logger.info(
        f"  Trades: {stats.total_trades} "
        f"(YES:{stats.buy_yes_trades} NO:{stats.buy_no_trades})"
    )
    logger.info(f"  Win/Loss: {stats.winning_trades}W / {stats.losing_trades}L")
    logger.info(f"  P&L: ${stats.total_pnl:.2f}")
    if stats.exit_reasons:
        reasons_str = ", ".join(
            f"{k}:{v}" for k, v in sorted(stats.exit_reasons.items())
        )
        logger.info(f"  Exit reasons: {reasons_str}")

    return stats


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Replay edge capture strategy on recorded NBA games",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/replay_edge_capture.py                        # First 5 games
  python scripts/replay_edge_capture.py --all                  # All 83 games
  python scripts/replay_edge_capture.py --all --min-edge 3     # Lower edge threshold
  python scripts/replay_edge_capture.py --recording FILE -v    # Single game, verbose
        """,
    )
    parser.add_argument(
        "--recording", type=Path, help="Single recording file to replay"
    )
    parser.add_argument("--all", action="store_true", help="Replay all recordings")
    parser.add_argument("-n", type=int, default=5, help="Number of games (default: 5)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    # Edge capture parameters
    parser.add_argument(
        "--min-edge", type=int, default=5, help="Minimum edge in cents (default: 5)"
    )
    parser.add_argument(
        "--aggressiveness",
        type=float,
        default=0.5,
        help="Entry aggressiveness 0-1 (default: 0.5)",
    )
    parser.add_argument(
        "--exit-mode",
        type=str,
        default="model",
        choices=["model", "target", "resolution"],
        help="Exit mode (default: model)",
    )
    parser.add_argument(
        "--stop-loss", type=int, default=10, help="Stop loss cents (default: 10)"
    )
    parser.add_argument(
        "--take-profit", type=int, default=0, help="Take profit cents (default: 0)"
    )
    parser.add_argument(
        "--kelly-fraction",
        type=float,
        default=0.25,
        help="Kelly fraction (default: 0.25)",
    )
    parser.add_argument(
        "--max-hold-time",
        type=float,
        default=3600.0,
        help="Max hold time seconds (default: 3600)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Max concurrent positions (default: 5)",
    )
    parser.add_argument(
        "--prob-update-interval",
        type=float,
        default=30.0,
        help="Probability update interval seconds (default: 30)",
    )
    # Calibration
    parser.add_argument(
        "--calibration",
        type=str,
        default="shrink",
        choices=["none", "shrink", "platt"],
        help="Model calibration mode (default: shrink)",
    )
    parser.add_argument(
        "--shrink-factor",
        type=float,
        default=0.70,
        help="Shrink calibration factor (default: 0.70)",
    )
    # Market filters
    parser.add_argument(
        "--min-spread",
        type=int,
        default=3,
        help="Minimum spread in cents (default: 3)",
    )
    # Edge reversal
    parser.add_argument(
        "--edge-reversal-threshold",
        type=int,
        default=8,
        help="Edge reversal threshold cents (default: 8)",
    )
    # Score-change trigger
    parser.add_argument(
        "--min-score-change",
        type=int,
        default=0,
        help="Min score_diff change to trade (0=disabled, default: 0)",
    )
    parser.add_argument(
        "--buy-yes-only",
        action="store_true",
        help="Only allow buy_yes trades (back favorites)",
    )
    parser.add_argument(
        "--min-fv",
        type=float,
        default=0.0,
        help="Min fair value for buy_yes entry (e.g. 0.60)",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Recordings directory (default: data/recordings/)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("src.strategies").setLevel(logging.DEBUG)

    config = EdgeCaptureConfig(
        min_edge_cents=args.min_edge,
        min_confidence=0.1,  # Low threshold for backtest (Markov is ~0.6)
        entry_timeout_seconds=120.0,
        max_entry_size=10,
        min_entry_size=1,
        entry_aggressiveness=args.aggressiveness,
        exit_mode=args.exit_mode,
        stop_loss_cents=args.stop_loss,
        take_profit_cents=args.take_profit,
        max_hold_time_seconds=args.max_hold_time,
        exit_on_edge_reversal=True,
        edge_reversal_threshold_cents=args.edge_reversal_threshold,
        exit_timeout_seconds=60.0,
        use_kelly_sizing=True,
        kelly_fraction=args.kelly_fraction,
        kelly_max_bankroll_pct=0.05,
        bankroll_override=10000.0,
        max_concurrent_positions=args.max_concurrent,
        max_positions_per_ticker=1,
        max_daily_loss_dollars=100.0,  # High limit for backtest
        max_loss_per_trade_dollars=10.0,
        circuit_breaker_consecutive_losses=20,  # Lenient for backtest
        cooldown_between_trades_seconds=10.0,
        probability_update_interval_seconds=args.prob_update_interval,
        # Market selection
        allowed_ticker_prefixes=None,
        min_volume_24h=0,
        min_spread_cents=args.min_spread,
        max_spread_cents=50,
        min_mid_price_cents=5.0,
        max_mid_price_cents=95.0,
        # Score-change trigger
        min_score_change_to_trade=args.min_score_change,
        # Direction filter
        buy_yes_only=args.buy_yes_only,
        min_fair_value_for_entry=args.min_fv,
        # Fees
        kalshi_maker_rate=0.0175,
        kalshi_taker_rate=0.07,
        enable_alerts=False,
    )

    logger.info("=" * 60)
    logger.info("EDGE CAPTURE REPLAY BACKTEST")
    logger.info("=" * 60)
    logger.info(f"Model: Markov (NBA), calibration={args.calibration}")
    if args.calibration == "shrink":
        logger.info(f"Shrink factor: {args.shrink_factor}")
    logger.info(f"Min edge: {config.min_edge_cents}c")
    logger.info(f"Min spread: {config.min_spread_cents}c")
    logger.info(f"Entry aggressiveness: {config.entry_aggressiveness}")
    logger.info(f"Exit mode: {config.exit_mode}")
    logger.info(f"Stop loss: {config.stop_loss_cents}c (0=disabled)")
    logger.info(f"Edge reversal threshold: {config.edge_reversal_threshold_cents}c")
    if config.take_profit_cents > 0:
        logger.info(f"Take profit: {config.take_profit_cents}c")
    logger.info(f"Max hold: {config.max_hold_time_seconds:.0f}s")
    logger.info(f"Kelly: fraction={config.kelly_fraction}")
    logger.info(f"Bankroll: ${config.bankroll_override:.0f}")
    logger.info(f"Max concurrent: {config.max_concurrent_positions}")
    if config.min_score_change_to_trade > 0:
        logger.info(f"Score change trigger: {config.min_score_change_to_trade}+ pts")
    logger.info("")

    # Find recordings
    if args.dir:
        recordings_dir = Path(args.dir)
    else:
        recordings_dir = Path(__file__).parent.parent / "data" / "recordings"

    if args.recording:
        recordings = [args.recording]
    elif args.all:
        recordings = sorted(recordings_dir.glob("*.json"))
    else:
        recordings = sorted(recordings_dir.glob("*.json"))[: args.n]

    if not recordings:
        logger.error(f"No recordings found in {recordings_dir}")
        return 1

    logger.info(f"Games to replay: {len(recordings)}")
    logger.info("")

    all_stats: List[ReplayStats] = []

    for recording in recordings:
        try:
            stats = asyncio.run(
                replay_recording(
                    recording,
                    config,
                    verbose=args.verbose,
                    calibration=args.calibration,
                    shrink_factor=args.shrink_factor,
                )
            )
            all_stats.append(stats)
        except Exception as e:
            logger.error(f"Error replaying {recording}: {e}", exc_info=args.verbose)
        logger.info("")

    # === Overall Summary ===
    logger.info("=" * 60)
    logger.info("OVERALL SUMMARY")
    logger.info("=" * 60)

    total_trades = sum(s.total_trades for s in all_stats)
    total_entries = sum(s.entries_filled for s in all_stats)
    total_yes = sum(s.buy_yes_trades for s in all_stats)
    total_no = sum(s.buy_no_trades for s in all_stats)
    total_wins = sum(s.winning_trades for s in all_stats)
    total_losses = sum(s.losing_trades for s in all_stats)
    total_pnl = sum(s.total_pnl for s in all_stats)

    # Aggregate exit reasons
    all_reasons = {}
    for s in all_stats:
        for k, v in s.exit_reasons.items():
            all_reasons[k] = all_reasons.get(k, 0) + v

    logger.info(f"Games replayed: {len(all_stats)}")
    logger.info(f"Total trades: {total_trades} (YES:{total_yes} NO:{total_no})")
    logger.info(f"Entries filled: {total_entries}")
    n_total = total_wins + total_losses
    win_rate = 100 * total_wins / max(1, n_total)
    logger.info(f"Win rate: {total_wins}/{n_total} ({win_rate:.1f}%)")
    logger.info(f"Total P&L: ${total_pnl:.2f}")
    if n_total > 0:
        logger.info(f"Avg P&L per trade: ${total_pnl / n_total:.3f}")
    if all_reasons:
        reasons_str = ", ".join(
            f"{k}:{v}" for k, v in sorted(all_reasons.items(), key=lambda x: -x[1])
        )
        logger.info(f"Exit reasons: {reasons_str}")

    # Per-game breakdown
    if len(all_stats) > 1:
        logger.info("")
        logger.info(f"{'Game':<50} {'Trades':>6} {'W/L':>7} {'P&L':>10}")
        logger.info("-" * 75)
        for s in all_stats:
            name = s.recording_file[:48]
            wl = f"{s.winning_trades}/{s.losing_trades}"
            logger.info(f"{name:<50} {s.total_trades:>6} {wl:>7} ${s.total_pnl:>9.2f}")
        logger.info("-" * 75)
        logger.info(
            f"{'TOTAL':<50} {total_trades:>6} "
            f"{total_wins}/{total_losses:>4} ${total_pnl:>9.2f}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
