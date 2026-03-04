#!/usr/bin/env python3
"""
Detailed edge capture backtest analysis.

Replays all recordings (like replay_edge_capture.py) but collects rich
per-trade data and produces analysis tables covering:
  a) P&L by exit reason
  b) P&L by direction
  c) P&L by edge bucket
  d) P&L by spread at entry
  e) P&L by game phase (time remaining)
  f) Markov model calibration
  g) Hold time by exit reason
  h) Price-direction correctness (did price eventually move toward FV?)
"""

import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Quiet down the strategy logger — we only want our analysis output
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ═══════════════════════════════════════════════════════════════════════════════
# Per-trade detail record
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TradeDetail:
    """Everything we want to know about a single trade for analysis."""

    # Identifiers
    game_file: str = ""
    trade_id: str = ""
    ticker: str = ""

    # Direction and prices
    direction: str = ""  # buy_yes / buy_no
    entry_price: int = 0
    exit_price: int = 0
    fair_value_at_entry: float = 0.0  # 0-1 probability
    edge_at_entry_cents: float = 0.0
    spread_at_entry: int = 0  # ask - bid at the moment of entry

    # Game context at entry
    home_score_at_entry: int = 0
    away_score_at_entry: int = 0
    period_at_entry: int = 0
    game_time_remaining_at_entry: float = 0.0  # seconds total

    # Outcome
    exit_reason: str = ""
    hold_time_seconds: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    size: int = 0

    # Game outcome
    home_won: Optional[bool] = None  # True if home won
    ticker_resolved_yes: Optional[bool] = None  # True if this ticker's YES side won

    # Post-entry price movement: did the market eventually move toward fair value?
    price_moved_toward_fv: Optional[bool] = None
    best_price_in_fv_direction: Optional[int] = (
        None  # best bid (buy_yes) or best ask (buy_no)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Frame helpers (copied from replay_edge_capture.py)
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_time_remaining(time_str: str) -> float:
    try:
        if " " in time_str:
            time_str = time_str.split(" ", 1)[1]
        if ":" not in time_str:
            return 0.0
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return float(time_str)
    except (ValueError, AttributeError):
        return 0.0


def _nba_game_time_remaining(period: int, period_time_str: str) -> float:
    period_seconds = _parse_time_remaining(period_time_str)
    if period <= 0:
        period = 1
    remaining_full_periods = max(0, 4 - period)
    return period_seconds + remaining_full_periods * 12 * 60


def frame_to_game_state(frame: dict) -> MarkovGameState:
    home_score = frame.get("home_score", 0)
    away_score = frame.get("away_score", 0)
    period = frame.get("period", 1)
    time_str = frame.get("time_remaining", "12:00")
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


def frame_to_orderbook(frame: dict, ticker: str) -> OrderBookState:
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
            bid_price, ask_price = 98, 99
    bids = [OrderBookLevel(price=bid_price, size=50)]
    asks = [OrderBookLevel(price=ask_price, size=50)]
    return OrderBookState(
        ticker=ticker,
        bids=bids,
        asks=asks,
        sequence=0,
        volume_24h=volume,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Replay a single recording
# ═══════════════════════════════════════════════════════════════════════════════


async def replay_and_collect(
    filepath: Path,
    config: EdgeCaptureConfig,
) -> List[TradeDetail]:
    """Replay one game, return detailed trade records."""

    with open(filepath) as f:
        data = json.load(f)
    metadata = data.get("metadata", {})
    frames = data.get("frames", [])

    if not frames:
        return []

    home_ticker = metadata.get("home_ticker", frames[0].get("home_ticker", "HOME"))
    away_ticker = metadata.get("away_ticker", frames[0].get("away_ticker", "AWAY"))
    tickers = [home_ticker, away_ticker]

    final_home = metadata.get("final_home_score")
    final_away = metadata.get("final_away_score")
    home_won = None
    if final_home is not None and final_away is not None:
        home_won = final_home > final_away

    base_ts = frames[0].get("timestamp", 0)
    clock = SimulatedClock(start_time=base_ts)
    provider = MarkovProbabilityProvider(SportType.NBA)

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

    # ---------- Track frame-level data at each trade entry ----------
    # We store a snapshot of the frame whenever a new trade appears
    trade_entry_frames: Dict[str, dict] = {}  # trade_id -> frame at entry
    known_trade_ids: set = set()

    # Track per-ticker price history after each trade entry (for direction check)
    # trade_id -> list of (bid, ask) tuples after entry
    trade_post_prices: Dict[str, list] = defaultdict(list)
    active_trade_tickers: Dict[str, str] = {}  # trade_id -> ticker

    # ---------- Replay loop ----------
    for i, frame in enumerate(frames):
        ts = frame.get("timestamp", base_ts + i * 2)
        clock.advance_to(ts)

        game_state = frame_to_game_state(frame)
        for ticker in tickers:
            if ticker == home_ticker:
                provider.set_game_state(ticker, game_state)
            else:
                away_state = MarkovGameState(
                    score_diff=-game_state.score_diff,
                    time_remaining=game_state.time_remaining,
                    period=game_state.period,
                    home_possession=game_state.home_possession,
                    momentum=-game_state.momentum,
                )
                provider.set_game_state(ticker, away_state)

        for ticker in tickers:
            book = frame_to_orderbook(frame, ticker)
            strategy.update_orderbook(ticker, book)

        strategy.check_fills()

        for ticker in tickers:
            book = strategy.get_orderbook(ticker)
            if book:
                strategy._check_opportunity(ticker, book)

        # Detect newly created trades and snapshot the frame
        for tid, trade in strategy._trades.items():
            if tid not in known_trade_ids:
                known_trade_ids.add(tid)
                trade_entry_frames[tid] = dict(frame)  # snapshot
                active_trade_tickers[tid] = trade.ticker

        # Record post-entry prices for active trades
        for tid, ticker in list(active_trade_tickers.items()):
            trade = strategy._trades.get(tid)
            if trade and trade.state == EdgeCaptureState.CLOSED:
                active_trade_tickers.pop(tid, None)
                continue
            if ticker == home_ticker:
                bid = int(frame.get("home_bid", 0.5) * 100)
                ask = int(frame.get("home_ask", 0.5) * 100)
            else:
                bid = int(frame.get("away_bid", 0.5) * 100)
                ask = int(frame.get("away_ask", 0.5) * 100)
            trade_post_prices[tid].append((bid, ask))

        for _ in range(10):
            await asyncio.sleep(0)

    await strategy.stop()

    # ---------- Build TradeDetail records ----------
    results: List[TradeDetail] = []

    for tid, trade in strategy._trades.items():
        if trade.state != EdgeCaptureState.CLOSED:
            continue
        if trade.entry_fill_size <= 0:
            continue

        eframe = trade_entry_frames.get(tid, {})

        # Determine spread at entry from the frame
        if trade.ticker == home_ticker:
            e_bid = int(eframe.get("home_bid", 0.5) * 100)
            e_ask = int(eframe.get("home_ask", 0.5) * 100)
        else:
            e_bid = int(eframe.get("away_bid", 0.5) * 100)
            e_ask = int(eframe.get("away_ask", 0.5) * 100)
        spread_at_entry = max(1, e_ask - e_bid)

        # Game time remaining at entry
        gs_entry = frame_to_game_state(eframe)
        game_time_remaining = gs_entry.time_remaining

        # Did ticker resolve YES?
        ticker_resolved_yes = None
        if home_won is not None:
            if trade.ticker == home_ticker:
                ticker_resolved_yes = home_won
            else:
                ticker_resolved_yes = not home_won

        # Did price eventually move toward fair value after entry?
        trade.fair_value_at_entry * 100
        post_prices = trade_post_prices.get(tid, [])
        price_moved_toward_fv = None
        best_in_fv_dir = None

        if trade.direction == "buy_yes" and post_prices:
            # We bought YES; want price to go UP toward fv
            best_bid_after = max(bp for bp, _ in post_prices) if post_prices else 0
            best_in_fv_dir = best_bid_after
            price_moved_toward_fv = best_bid_after > (trade.entry_fill_price or 0)
        elif trade.direction == "buy_no" and post_prices:
            # We bought NO (sold YES); want YES price to go DOWN toward fv
            best_ask_after = min(ap for _, ap in post_prices) if post_prices else 99
            best_in_fv_dir = best_ask_after
            price_moved_toward_fv = best_ask_after < (trade.entry_fill_price or 99)

        hold_time = 0.0
        if trade.entry_fill_time:
            # Use the exit frame's timestamp minus entry fill time
            # We stored it on the trade object via _clock
            hold_time = trade.hold_time()

        td = TradeDetail(
            game_file=filepath.name,
            trade_id=tid,
            ticker=trade.ticker,
            direction=trade.direction,
            entry_price=trade.entry_fill_price or 0,
            exit_price=trade.exit_fill_price or 0,
            fair_value_at_entry=trade.fair_value_at_entry,
            edge_at_entry_cents=trade.edge_at_entry_cents,
            spread_at_entry=spread_at_entry,
            home_score_at_entry=eframe.get("home_score", 0),
            away_score_at_entry=eframe.get("away_score", 0),
            period_at_entry=eframe.get("period", 0),
            game_time_remaining_at_entry=game_time_remaining,
            exit_reason=trade.exit_reason or "unknown",
            hold_time_seconds=hold_time,
            gross_pnl=trade.gross_pnl,
            net_pnl=trade.net_pnl,
            entry_fee=trade.entry_fee,
            exit_fee=trade.exit_fee,
            size=trade.entry_fill_size,
            home_won=home_won,
            ticker_resolved_yes=ticker_resolved_yes,
            price_moved_toward_fv=price_moved_toward_fv,
            best_price_in_fv_direction=best_in_fv_dir,
        )
        results.append(td)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Analysis helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _fmt_dollar(x: float) -> str:
    return f"${x:+.2f}" if x != 0 else "$0.00"


def _fmt_pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}%" if total > 0 else "N/A"


def _edge_bucket(edge: float) -> str:
    if edge < 5:
        return "<5c"
    elif edge < 10:
        return "5-10c"
    elif edge < 20:
        return "10-20c"
    else:
        return "20+c"


def _spread_bucket(spread: int) -> str:
    if spread <= 1:
        return "1c"
    elif spread == 2:
        return "2c"
    elif spread <= 5:
        return "3-5c"
    else:
        return "6+c"


def _phase_bucket(time_remaining: float) -> str:
    minutes = time_remaining / 60.0
    if minutes > 30:
        return ">30min"
    elif minutes > 15:
        return "15-30min"
    elif minutes > 5:
        return "5-15min"
    else:
        return "<5min"


def _fv_bucket(fv: float) -> str:
    """Bucket fair value for calibration analysis."""
    if fv < 0.2:
        return "0-20%"
    elif fv < 0.4:
        return "20-40%"
    elif fv < 0.6:
        return "40-60%"
    elif fv < 0.8:
        return "60-80%"
    else:
        return "80-100%"


def _print_table(
    title: str,
    headers: List[str],
    rows: List[List[Any]],
    col_widths: Optional[List[int]] = None,
):
    """Print a nicely formatted table."""
    if not col_widths:
        col_widths = []
        for ci in range(len(headers)):
            w = len(str(headers[ci]))
            for row in rows:
                if ci < len(row):
                    w = max(w, len(str(row[ci])))
            col_widths.append(w + 2)

    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")

    header_str = ""
    for h, w in zip(headers, col_widths):
        header_str += str(h).rjust(w)
    print(header_str)
    print("-" * sum(col_widths))

    for row in rows:
        row_str = ""
        for val, w in zip(row, col_widths):
            row_str += str(val).rjust(w)
        print(row_str)


def _group_stats(trades: List[TradeDetail], key_fn) -> Dict[str, dict]:
    """Group trades by key_fn and compute summary stats per group."""
    groups: Dict[str, List[TradeDetail]] = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)

    result = {}
    for k, ts in groups.items():
        n = len(ts)
        total_pnl = sum(t.net_pnl for t in ts)
        wins = sum(1 for t in ts if t.net_pnl >= 0)
        losses = n - wins
        avg_pnl = total_pnl / n if n > 0 else 0
        avg_edge = sum(t.edge_at_entry_cents for t in ts) / n if n > 0 else 0
        avg_hold = sum(t.hold_time_seconds for t in ts) / n if n > 0 else 0
        total_size = sum(t.size for t in ts)
        avg_entry_fee = sum(t.entry_fee for t in ts) / n if n > 0 else 0
        avg_exit_fee = sum(t.exit_fee for t in ts) / n if n > 0 else 0
        result[k] = {
            "count": n,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / n if n > 0 else 0,
            "avg_edge": avg_edge,
            "avg_hold": avg_hold,
            "total_size": total_size,
            "avg_entry_fee": avg_entry_fee,
            "avg_exit_fee": avg_exit_fee,
        }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Main analysis output
# ═══════════════════════════════════════════════════════════════════════════════


def print_analysis(all_trades: List[TradeDetail]):
    n = len(all_trades)
    if n == 0:
        print("No trades to analyze.")
        return

    total_pnl = sum(t.net_pnl for t in all_trades)
    total_gross = sum(t.gross_pnl for t in all_trades)
    total_fees = sum(t.entry_fee + t.exit_fee for t in all_trades)
    wins = sum(1 for t in all_trades if t.net_pnl >= 0)
    losses = n - wins

    print("\n" + "#" * 80)
    print("#  EDGE CAPTURE BACKTEST — DETAILED ANALYSIS")
    print("#" * 80)

    print(f"\nTotal trades:     {n}")
    print(f"Win / Loss:       {wins}W / {losses}L  ({_fmt_pct(wins, n)} win rate)")
    print(f"Total gross P&L:  {_fmt_dollar(total_gross)}")
    print(
        f"Total fees:       {_fmt_dollar(-total_fees)} (entry: {_fmt_dollar(-sum(t.entry_fee for t in all_trades))}, exit: {_fmt_dollar(-sum(t.exit_fee for t in all_trades))})"
    )
    print(f"Total net P&L:    {_fmt_dollar(total_pnl)}")
    print(f"Avg net P&L/trade:{_fmt_dollar(total_pnl / n)}")
    print(
        f"Avg edge at entry:{sum(t.edge_at_entry_cents for t in all_trades) / n:.2f}c"
    )
    print(f"Avg hold time:    {sum(t.hold_time_seconds for t in all_trades) / n:.0f}s")
    print(f"Avg size:         {sum(t.size for t in all_trades) / n:.1f} contracts")

    # ── (a) P&L by exit reason ──
    stats = _group_stats(all_trades, lambda t: t.exit_reason)
    headers = [
        "Exit Reason",
        "Count",
        "Win%",
        "Total P&L",
        "Avg P&L",
        "Avg Hold(s)",
        "Avg Edge(c)",
    ]
    rows = []
    for reason in sorted(stats.keys(), key=lambda k: -stats[k]["count"]):
        s = stats[reason]
        rows.append(
            [
                reason,
                s["count"],
                f"{s['win_rate'] * 100:.0f}%",
                _fmt_dollar(s["total_pnl"]),
                _fmt_dollar(s["avg_pnl"]),
                f"{s['avg_hold']:.0f}",
                f"{s['avg_edge']:.1f}",
            ]
        )
    _print_table("(a) P&L by Exit Reason", headers, rows)

    # ── (b) P&L by direction ──
    stats = _group_stats(all_trades, lambda t: t.direction)
    headers = ["Direction", "Count", "Win%", "Total P&L", "Avg P&L", "Avg Edge(c)"]
    rows = []
    for d in ["buy_yes", "buy_no"]:
        if d in stats:
            s = stats[d]
            rows.append(
                [
                    d,
                    s["count"],
                    f"{s['win_rate'] * 100:.0f}%",
                    _fmt_dollar(s["total_pnl"]),
                    _fmt_dollar(s["avg_pnl"]),
                    f"{s['avg_edge']:.1f}",
                ]
            )
    _print_table("(b) P&L by Direction", headers, rows)

    # ── (c) P&L by edge bucket ──
    stats = _group_stats(all_trades, lambda t: _edge_bucket(t.edge_at_entry_cents))
    headers = [
        "Edge Bucket",
        "Count",
        "Win%",
        "Total P&L",
        "Avg P&L",
        "Avg Actual Edge(c)",
    ]
    rows = []
    for bucket in ["<5c", "5-10c", "10-20c", "20+c"]:
        if bucket in stats:
            s = stats[bucket]
            rows.append(
                [
                    bucket,
                    s["count"],
                    f"{s['win_rate'] * 100:.0f}%",
                    _fmt_dollar(s["total_pnl"]),
                    _fmt_dollar(s["avg_pnl"]),
                    f"{s['avg_edge']:.1f}",
                ]
            )
    _print_table("(c) P&L by Edge Bucket at Entry", headers, rows)

    # ── (d) P&L by spread at entry ──
    stats = _group_stats(all_trades, lambda t: _spread_bucket(t.spread_at_entry))
    headers = ["Spread", "Count", "Win%", "Total P&L", "Avg P&L", "Avg Edge(c)"]
    rows = []
    for bucket in ["1c", "2c", "3-5c", "6+c"]:
        if bucket in stats:
            s = stats[bucket]
            rows.append(
                [
                    bucket,
                    s["count"],
                    f"{s['win_rate'] * 100:.0f}%",
                    _fmt_dollar(s["total_pnl"]),
                    _fmt_dollar(s["avg_pnl"]),
                    f"{s['avg_edge']:.1f}",
                ]
            )
    _print_table("(d) P&L by Spread at Entry", headers, rows)

    # ── (e) P&L by game phase ──
    stats = _group_stats(
        all_trades, lambda t: _phase_bucket(t.game_time_remaining_at_entry)
    )
    headers = [
        "Game Phase",
        "Count",
        "Win%",
        "Total P&L",
        "Avg P&L",
        "Avg Edge(c)",
        "Avg Hold(s)",
    ]
    rows = []
    for bucket in [">30min", "15-30min", "5-15min", "<5min"]:
        if bucket in stats:
            s = stats[bucket]
            rows.append(
                [
                    bucket,
                    s["count"],
                    f"{s['win_rate'] * 100:.0f}%",
                    _fmt_dollar(s["total_pnl"]),
                    _fmt_dollar(s["avg_pnl"]),
                    f"{s['avg_edge']:.1f}",
                    f"{s['avg_hold']:.0f}",
                ]
            )
    _print_table("(e) P&L by Game Phase (time remaining at entry)", headers, rows)

    # ── (f) Model calibration ──
    # For each trade, we have fair_value_at_entry (home-team perspective for home
    # ticker, away perspective for away ticker). Compare against actual outcome.
    print(f"\n{'=' * 80}")
    print("  (f) Markov Model Calibration")
    print(f"{'=' * 80}")
    print("  FV bucket = model's estimated win probability for this ticker's YES side")
    print("  Actual % = fraction of trades where that ticker's YES side actually won")
    print()

    # Only use trades where we know the outcome
    cal_trades = [t for t in all_trades if t.ticker_resolved_yes is not None]
    fv_groups: Dict[str, List[TradeDetail]] = defaultdict(list)
    for t in cal_trades:
        fv_groups[_fv_bucket(t.fair_value_at_entry)].append(t)

    headers = ["FV Bucket", "Trades", "Avg FV", "Actual Win%", "Delta"]
    rows = []
    for bucket in ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]:
        ts = fv_groups.get(bucket, [])
        if not ts:
            rows.append([bucket, 0, "N/A", "N/A", "N/A"])
            continue
        avg_fv = sum(t.fair_value_at_entry for t in ts) / len(ts)
        actual_win = sum(1 for t in ts if t.ticker_resolved_yes) / len(ts)
        delta = actual_win - avg_fv
        rows.append(
            [
                bucket,
                len(ts),
                f"{avg_fv * 100:.1f}%",
                f"{actual_win * 100:.1f}%",
                f"{delta * 100:+.1f}pp",
            ]
        )
    _print_table("Model Calibration: FV vs Actual Outcome", headers, rows)

    # Also do finer-grained calibration (10% buckets)
    print("\n  Fine-grained calibration (10% buckets):")
    fine_groups: Dict[str, List[TradeDetail]] = defaultdict(list)
    for t in cal_trades:
        b = int(t.fair_value_at_entry * 10) * 10
        label = f"{b}-{b + 10}%"
        fine_groups[label].append(t)
    print(f"  {'Bucket':>12} {'N':>5} {'Avg FV':>8} {'Actual':>8} {'Delta':>8}")
    for b in range(0, 100, 10):
        label = f"{b}-{b + 10}%"
        ts = fine_groups.get(label, [])
        if not ts:
            print(f"  {label:>12} {0:>5}      N/A      N/A      N/A")
            continue
        avg_fv = sum(t.fair_value_at_entry for t in ts) / len(ts)
        actual = sum(1 for t in ts if t.ticker_resolved_yes) / len(ts)
        d = actual - avg_fv
        print(
            f"  {label:>12} {len(ts):>5} {avg_fv * 100:>7.1f}% {actual * 100:>7.1f}% {d * 100:>+7.1f}pp"
        )

    # ── (g) Hold time by exit reason ──
    stats = _group_stats(all_trades, lambda t: t.exit_reason)
    headers = [
        "Exit Reason",
        "Count",
        "Avg Hold(s)",
        "Min Hold(s)",
        "Max Hold(s)",
        "Median Hold(s)",
    ]
    rows = []
    reason_trades: Dict[str, List[TradeDetail]] = defaultdict(list)
    for t in all_trades:
        reason_trades[t.exit_reason].append(t)
    for reason in sorted(reason_trades.keys(), key=lambda k: -len(reason_trades[k])):
        ts = reason_trades[reason]
        holds = sorted(t.hold_time_seconds for t in ts)
        median_hold = holds[len(holds) // 2] if holds else 0
        rows.append(
            [
                reason,
                len(ts),
                f"{sum(holds) / len(holds):.0f}",
                f"{min(holds):.0f}",
                f"{max(holds):.0f}",
                f"{median_hold:.0f}",
            ]
        )
    _print_table("(g) Hold Time by Exit Reason", headers, rows)

    # ── (h) Price direction correctness ──
    print(f"\n{'=' * 80}")
    print("  (h) Price Direction Correctness")
    print(f"{'=' * 80}")
    print(
        "  'Correct' = after entry, the market price eventually moved toward fair value"
    )
    print("  (even if the trade was stopped out before it got there)")
    print()

    dir_known = [t for t in all_trades if t.price_moved_toward_fv is not None]
    if dir_known:
        correct = sum(1 for t in dir_known if t.price_moved_toward_fv)
        print(f"  Trades with post-entry data: {len(dir_known)}")
        print(
            f"  Price moved toward FV:       {correct} ({_fmt_pct(correct, len(dir_known))})"
        )
        print(
            f"  Price did NOT move toward FV:{len(dir_known) - correct} ({_fmt_pct(len(dir_known) - correct, len(dir_known))})"
        )

        # Break down: of the ones that moved toward FV, how many were still losses?
        correct_but_lost = sum(
            1 for t in dir_known if t.price_moved_toward_fv and t.net_pnl < 0
        )
        wrong_but_won = sum(
            1 for t in dir_known if not t.price_moved_toward_fv and t.net_pnl >= 0
        )
        print(
            f"\n  Direction correct but still lost (stopped out too early): {correct_but_lost}"
        )
        print(
            f"  Direction wrong but still won (lucky exit):               {wrong_but_won}"
        )

        # By exit reason
        print("\n  Direction correctness by exit reason:")
        for reason in sorted(
            reason_trades.keys(), key=lambda k: -len(reason_trades[k])
        ):
            ts = [
                t for t in reason_trades[reason] if t.price_moved_toward_fv is not None
            ]
            if not ts:
                continue
            c = sum(1 for t in ts if t.price_moved_toward_fv)
            print(f"    {reason:25s}: {c}/{len(ts)} ({_fmt_pct(c, len(ts))})")

    # ── Additional: fee impact analysis ──
    print(f"\n{'=' * 80}")
    print("  Fee Impact Analysis")
    print(f"{'=' * 80}")
    print(f"  Total entry fees:  {_fmt_dollar(-sum(t.entry_fee for t in all_trades))}")
    print(f"  Total exit fees:   {_fmt_dollar(-sum(t.exit_fee for t in all_trades))}")
    print(f"  Total fees:        {_fmt_dollar(-total_fees)}")
    print(f"  Gross P&L:         {_fmt_dollar(total_gross)}")
    print(f"  Net P&L:           {_fmt_dollar(total_pnl)}")
    print(
        f"  Fees as % of gross:{100 * total_fees / abs(total_gross) if total_gross else 0:.1f}%"
    )
    print(f"  Avg fee per trade: {_fmt_dollar(total_fees / n)}")

    # Trades that were gross-profitable but net-losing
    gross_win_net_loss = sum(1 for t in all_trades if t.gross_pnl > 0 and t.net_pnl < 0)
    print(
        f"\n  Trades gross-profitable but net-losing (fees killed it): {gross_win_net_loss} ({_fmt_pct(gross_win_net_loss, n)})"
    )

    # ── Sample trades ──
    print(f"\n{'=' * 80}")
    print("  Sample Trades (first 20)")
    print(f"{'=' * 80}")
    headers = [
        "Game",
        "Dir",
        "Entry",
        "Exit",
        "FV",
        "Edge",
        "Spread",
        "Reason",
        "Hold(s)",
        "NetP&L",
        "Size",
    ]
    rows = []
    for t in all_trades[:20]:
        game_short = t.game_file[:25]
        rows.append(
            [
                game_short,
                t.direction[:7],
                f"{t.entry_price}c",
                f"{t.exit_price}c",
                f"{t.fair_value_at_entry:.2f}",
                f"{t.edge_at_entry_cents:.1f}c",
                f"{t.spread_at_entry}c",
                t.exit_reason[:15],
                f"{t.hold_time_seconds:.0f}",
                _fmt_dollar(t.net_pnl),
                t.size,
            ]
        )
    _print_table("Sample Trades", headers, rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    config = EdgeCaptureConfig(
        min_edge_cents=5,
        min_confidence=0.1,
        entry_timeout_seconds=120.0,
        max_entry_size=10,
        min_entry_size=1,
        entry_aggressiveness=0.5,
        exit_mode="model",
        stop_loss_cents=10,
        take_profit_cents=0,
        max_hold_time_seconds=3600.0,
        exit_on_edge_reversal=True,
        edge_reversal_threshold_cents=3,
        exit_timeout_seconds=60.0,
        use_kelly_sizing=True,
        kelly_fraction=0.25,
        kelly_max_bankroll_pct=0.05,
        bankroll_override=10000.0,
        max_concurrent_positions=5,
        max_positions_per_ticker=1,
        max_daily_loss_dollars=100.0,
        max_loss_per_trade_dollars=10.0,
        circuit_breaker_consecutive_losses=20,
        cooldown_between_trades_seconds=10.0,
        probability_update_interval_seconds=30.0,
        allowed_ticker_prefixes=None,
        min_volume_24h=0,
        min_spread_cents=1,
        max_spread_cents=50,
        min_mid_price_cents=5.0,
        max_mid_price_cents=95.0,
        kalshi_maker_rate=0.0175,
        kalshi_taker_rate=0.07,
        enable_alerts=False,
    )

    recordings_dir = Path(__file__).parent.parent / "data" / "recordings"
    recordings = sorted(recordings_dir.glob("*.json"))

    if not recordings:
        print(f"No recordings found in {recordings_dir}")
        return 1

    print(f"Replaying {len(recordings)} games...")
    print(
        f"Config: min_edge={config.min_edge_cents}c, stop_loss={config.stop_loss_cents}c, "
        f"exit_mode={config.exit_mode}, kelly_frac={config.kelly_fraction}"
    )
    print()

    all_trades: List[TradeDetail] = []
    games_ok = 0
    games_err = 0

    for i, rec in enumerate(recordings):
        try:
            trades = asyncio.run(replay_and_collect(rec, config))
            all_trades.extend(trades)
            games_ok += 1
            pnl = sum(t.net_pnl for t in trades)
            sys.stdout.write(
                f"\r  [{i + 1}/{len(recordings)}] {rec.name[:40]:40s} -> {len(trades):3d} trades, P&L {_fmt_dollar(pnl)}"
            )
            sys.stdout.flush()
        except Exception as e:
            games_err += 1
            sys.stdout.write(
                f"\r  [{i + 1}/{len(recordings)}] {rec.name[:40]:40s} -> ERROR: {e}"
            )
            sys.stdout.flush()
        print()  # newline

    print(f"\nGames replayed: {games_ok} ok, {games_err} errors")
    print(f"Total trades collected: {len(all_trades)}")

    if all_trades:
        print_analysis(all_trades)

    return 0


if __name__ == "__main__":
    sys.exit(main())
