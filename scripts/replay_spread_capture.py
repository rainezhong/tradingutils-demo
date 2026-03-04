#!/usr/bin/env python3
"""
Replay spread capture strategy on recorded NCAAB games.

Runs the strategy's full async execution path (entry -> fill -> exit ->
stuck management -> stop-loss -> force exit) driven by recorded frame data
instead of live Kalshi API, using a simulated clock.

Usage:
    python scripts/replay_spread_capture.py
    python scripts/replay_spread_capture.py --recording data/recordings/synthetic_ncaab/synthetic_ncaab_FAU_vs_SDSU_1152.json
    python scripts/replay_spread_capture.py --all  # Run on all recordings
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.orderbook_manager import OrderBookLevel, OrderBookState
from strategies.sim_clock import SimulatedClock, make_sim_wait_for_event, sim_sleep
from strategies.spread_capture_strategy import (
    SpreadCaptureConfig,
    SpreadCaptureState,
    SpreadCaptureStrategy,
)
from signal_extraction.models.markov_win_model import (
    GameState as MarkovGameState,
    MarkovWinModel,
    SportType,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Data Loading & Conversion (kept from original)
# =============================================================================


@dataclass
class ReplayStats:
    """Statistics from replaying a recording."""

    recording_file: str
    total_frames: int

    # Opportunity stats
    opportunities_found: int = 0
    fair_value_filtered: int = 0

    # Trade stats (read from strategy after replay)
    total_trades: int = 0
    entries_filled: int = 0
    exits_filled: int = 0
    force_exits: int = 0
    stop_loss_exits: int = 0

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
# Fair-Value Filter (kept from original — uses frame-level data)
# =============================================================================


def _parse_time_remaining(time_str: str) -> float:
    """Parse 'MM:SS' time string to seconds remaining in current period."""
    try:
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return float(time_str)
    except (ValueError, AttributeError):
        return 0.0


def _game_time_remaining_seconds(period: int, period_time_str: str) -> float:
    """Calculate total game time remaining in seconds for NCAAB (2 x 20min halves)."""
    period_seconds = _parse_time_remaining(period_time_str)
    if period <= 1:
        return period_seconds + 20 * 60
    elif period <= 2:
        return period_seconds
    else:
        return period_seconds


def _check_fair_value(
    frame: dict,
    ticker: str,
    entry_price: int,
    home_ticker: str,
    model: MarkovWinModel,
    min_edge_cents: float,
) -> Tuple[bool, float]:
    """Check if entry price is favorable vs model fair value.

    Returns (should_trade, model_prob).
    """
    home_score = frame.get("home_score", 0)
    away_score = frame.get("away_score", 0)
    period = frame.get("period", 1)
    time_str = frame.get("time_remaining", "20:00")

    time_remaining = _game_time_remaining_seconds(period, time_str)

    state = MarkovGameState(
        score_diff=home_score - away_score,
        time_remaining=time_remaining,
        period=period,
        home_possession=True,
        momentum=0.0,
    )

    home_prob = model.get_win_probability(state)

    is_home = ticker == home_ticker
    model_prob = home_prob if is_home else (1.0 - home_prob)

    market_implied = entry_price / 100.0
    edge = model_prob - market_implied

    return edge >= (min_edge_cents / 100.0), model_prob


# =============================================================================
# Core Replay — drives the strategy's async execution path
# =============================================================================


async def replay_recording(
    filepath: Path,
    config: SpreadCaptureConfig,
    verbose: bool = False,
    fair_value_model: Optional[MarkovWinModel] = None,
    min_model_edge_cents: float = 3.0,
) -> ReplayStats:
    """Replay a single recording through the strategy's full execution path.

    Instead of reimplementing fill/P&L/stop-loss logic, we:
      1. Create a SimulatedClock + strategy in dry-run mode
      2. For each frame: advance clock, inject orderbook, check fills,
         check opportunities, yield to event loop for execution tasks
      3. At end: strategy.stop() force-exits remaining positions
      4. Read stats from strategy._trades
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

    logger.info(
        f"Replaying {filepath.name}: {metadata.get('away_team', '?')} @ {metadata.get('home_team', '?')}"
    )
    logger.info(f"  Tickers: {home_ticker}, {away_ticker}")
    logger.info(f"  Frames: {len(frames)}")

    # --- Build simulated-time strategy ---
    base_ts = frames[0].get("timestamp", 0)
    clock = SimulatedClock(start_time=base_ts)

    strategy = SpreadCaptureStrategy(
        config=config,
        dry_run=True,
        clock=clock,
        sleep=sim_sleep,
        wait_for_event=make_sim_wait_for_event(clock),
    )
    strategy._running = True
    strategy._subscribed_tickers = set(tickers)

    # Wrap analyze_opportunity to inject fair-value filter.
    # The fair-value model uses frame-level data (scores, period) which
    # isn't available to the strategy itself — so we filter here.
    _original_analyze = strategy.analyze_opportunity
    _current_frame = {}  # mutable ref updated each frame

    if fair_value_model is not None:

        def _filtered_analyze(ticker, book):
            opp = _original_analyze(ticker, book)
            if opp is None:
                return None
            should_trade, model_prob = _check_fair_value(
                _current_frame,
                ticker,
                opp.entry_price,
                home_ticker,
                fair_value_model,
                min_model_edge_cents,
            )
            if not should_trade:
                stats.fair_value_filtered += 1
                if verbose:
                    logger.info(
                        f"  FV FILTERED {ticker}: "
                        f"model={model_prob:.3f}, bid={opp.entry_price}c "
                        f"(need {min_model_edge_cents}c edge)"
                    )
                return None
            return opp

        strategy.analyze_opportunity = _filtered_analyze

    # --- Main replay loop ---
    for i, frame in enumerate(frames):
        ts = frame.get("timestamp", base_ts + i * 3)
        clock.advance_to(ts)

        # Update current frame ref for fair-value filter
        _current_frame.clear()
        _current_frame.update(frame)

        # Inject orderbooks
        for ticker in tickers:
            book = frame_to_orderbook(frame, ticker)
            strategy.update_orderbook(ticker, book)

        # Check fills against current book (uses strategy's _check_dry_run_fills)
        strategy.check_fills()

        # Check for new opportunities
        for ticker in tickers:
            book = strategy.get_orderbook(ticker)
            if book:
                strategy._check_opportunity(ticker, book)

        # Yield to event loop — lets execution tasks (entry waits, exit waits,
        # stuck management) run cooperatively with the simulated clock.
        for _ in range(10):
            await asyncio.sleep(0)

    # Force-exit remaining positions (same as live stop())
    await strategy.stop()

    # --- Collect stats from strategy ---
    for trade in strategy._trades.values():
        if trade.state == SpreadCaptureState.CLOSED:
            stats.total_trades += 1
            if trade.entry_fill_size > 0:
                stats.entries_filled += 1
            if trade.exit_fill_size > 0:
                if trade.was_taker_exit:
                    stats.force_exits += 1
                else:
                    stats.exits_filled += 1
            stats.total_pnl += trade.net_pnl
            if trade.net_pnl >= 0:
                stats.winning_trades += 1
            else:
                stats.losing_trades += 1

    stats.opportunities_found = strategy._stats.get("opportunities_found", 0)

    # Summary
    logger.info(
        f"  Opportunities: {stats.opportunities_found}"
        + (
            f" ({stats.fair_value_filtered} filtered by fair-value)"
            if stats.fair_value_filtered
            else ""
        )
    )
    logger.info(f"  Entries filled: {stats.entries_filled}")
    logger.info(f"  Exits: {stats.exits_filled} filled, {stats.force_exits} forced")
    logger.info(f"  Trades: {stats.winning_trades}W / {stats.losing_trades}L")
    logger.info(f"  P&L: ${stats.total_pnl:.2f}")

    return stats


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Replay spread capture on NCAAB recordings"
    )
    parser.add_argument(
        "--recording", type=Path, help="Single recording file to replay"
    )
    parser.add_argument("--all", action="store_true", help="Replay all recordings")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--no-activity-filter", action="store_true", help="Disable activity filter"
    )
    parser.add_argument(
        "--fair-value",
        action="store_true",
        help="Enable fair-value model filter (Markov win probability)",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=3.0,
        help="Minimum model edge in cents for fair-value filter (default: 3)",
    )
    parser.add_argument(
        "--rng-seed", type=int, default=42, help="RNG seed for reproducibility"
    )

    args = parser.parse_args()

    config = SpreadCaptureConfig(
        min_spread_cents=5,
        max_spread_cents=25,
        min_depth_at_best=1,
        require_live_activity=not args.no_activity_filter,
        live_activity_window_seconds=30.0,
        min_price_changes=3,
        min_total_movement_cents=5,
        max_concurrent_positions=3,
        entry_timeout_seconds=60.0,
        exit_timeout_seconds=120.0,
        use_undercut_exit=True,
        undercut_profit_cents=5,
    )

    fv_model = None
    if args.fair_value:
        fv_model = MarkovWinModel(SportType.COLLEGE_BB)

    logger.info("=" * 60)
    logger.info("SPREAD CAPTURE REPLAY")
    logger.info("=" * 60)
    logger.info("Fill model: STRATEGY DRY-RUN (uses strategy's _check_dry_run_fills)")
    logger.info(
        f"Fair-value filter: {'ENABLED (edge >= ' + str(args.min_edge) + 'c)' if args.fair_value else 'DISABLED'}"
    )
    logger.info(
        f"Activity filter: {'ENABLED' if config.require_live_activity else 'DISABLED'}"
    )
    if config.require_live_activity:
        logger.info(f"  Window: {config.live_activity_window_seconds}s")
        logger.info(f"  Min changes: {config.min_price_changes}")
        logger.info(f"  Min movement: {config.min_total_movement_cents}c")
    logger.info("")

    recordings_dir = (
        Path(__file__).parent.parent / "data" / "recordings" / "synthetic_ncaab"
    )

    if args.recording:
        recordings = [args.recording]
    elif args.all:
        recordings = sorted(recordings_dir.glob("*.json"))
    else:
        recordings = sorted(recordings_dir.glob("*.json"))[:3]

    all_stats: List[ReplayStats] = []

    for recording in recordings:
        stats = asyncio.run(
            replay_recording(
                recording,
                config,
                verbose=args.verbose,
                fair_value_model=fv_model,
                min_model_edge_cents=args.min_edge,
            )
        )
        all_stats.append(stats)
        logger.info("")

    # Summary across all recordings
    logger.info("=" * 60)
    logger.info("OVERALL SUMMARY")
    logger.info("=" * 60)

    total_opportunities = sum(s.opportunities_found for s in all_stats)
    total_fv_filtered = sum(s.fair_value_filtered for s in all_stats)
    total_entries_filled = sum(s.entries_filled for s in all_stats)
    total_exits_filled = sum(s.exits_filled for s in all_stats)
    total_force_exits = sum(s.force_exits for s in all_stats)
    total_wins = sum(s.winning_trades for s in all_stats)
    total_losses = sum(s.losing_trades for s in all_stats)
    total_pnl = sum(s.total_pnl for s in all_stats)

    logger.info(f"Recordings: {len(all_stats)}")
    logger.info("Fill model: STRATEGY DRY-RUN")
    logger.info(
        f"Opportunities: {total_opportunities}"
        + (
            f" ({total_fv_filtered} filtered by fair-value)"
            if total_fv_filtered
            else ""
        )
    )
    logger.info(f"Entry fills: {total_entries_filled}")
    logger.info(
        f"Exit fills: {total_exits_filled} profitable, {total_force_exits} forced"
    )
    logger.info(
        f"Win rate: {total_wins}/{total_wins + total_losses} "
        f"({100 * total_wins / max(1, total_wins + total_losses):.1f}%)"
    )
    logger.info(f"Total P&L: ${total_pnl:.2f}")
    if total_wins + total_losses > 0:
        logger.info(
            f"Avg P&L per trade: ${total_pnl / (total_wins + total_losses):.3f}"
        )


if __name__ == "__main__":
    main()
