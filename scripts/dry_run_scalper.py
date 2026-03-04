#!/usr/bin/env python3
"""
Dry-Run Scalper Daemon

Continuously monitors live NBA games, records them, and runs the scalper
strategy on each completed recording. No real money is risked.

Flow:
  1. Poll NBA API for live/upcoming games every 60s
  2. Auto-start recording when a game goes live
  3. When a recording finishes (game final), run the scalper backtest
  4. Log whether the strategy would have traded and the simulated P&L

The probability table is built from ALL existing recordings in data/recordings/
(growing over time as more games are recorded).

Usage:
    python scripts/dry_run_scalper.py
    python scripts/dry_run_scalper.py --demo          # Use Kalshi demo API
    python scripts/dry_run_scalper.py --poll 30       # Poll every 30s
    python scripts/dry_run_scalper.py --no-record     # Backtest-only on new files
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import asyncio
import json
import glob
import os
import signal
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.simulation.nba_recorder import NBAGameRecorder, list_live_games
from src.kalshi.client import KalshiClient
from src.kalshi.fees import calculate_fee

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
#  STRATEGY CONFIG (safest high-ROI from grid search)
# ============================================================================


@dataclass
class DryRunConfig:
    # Strategy params — "safest high-ROI" from grid search
    kelly_fraction: float = 0.75
    max_bet_pct: float = 0.25
    min_win_prob: float = 0.95
    stop_loss: float = 1.0  # 1.0 = effectively no stop loss
    min_lead: int = 8
    max_lead: int = 25
    max_entry_minutes: int = 8
    max_entry_price: float = 1.0
    min_period: int = 4
    min_sample_count: int = 3
    prob_haircut: float = 0.02
    prob_cap: float = 0.98

    # Dry run params
    starting_bankroll: float = 1000.00
    poll_interval: int = 60  # seconds between game list polls
    output_dir: str = "data/recordings"
    record_poll_ms: int = 2000  # recording frame interval


# ============================================================================
#  PROBABILITY ENGINE (reused from nba_scalper_bot.py)
# ============================================================================


def parse_time(time_str: str) -> Tuple[int, int]:
    """Parse time_remaining string -> (minutes, seconds)."""
    try:
        time_str = time_str.split()[-1]
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]), int(float(parts[1]))
        return int(float(parts[0])), 0
    except Exception:
        return 12, 0


def build_win_rate_table(
    games: List[dict],
    config: DryRunConfig,
) -> Dict[Tuple[int, int], float]:
    """Build empirical win-rate lookup: (lead, minute) -> safe_win_rate."""
    import numpy as np
    import pandas as pd

    rows = []
    for game in games:
        winner = game["winner"]
        for frame in game["frames"]:
            period = frame.get("period", 0)
            if period < config.min_period:
                continue
            if frame.get("game_status") == "final":
                continue

            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            lead = abs(hs - as_)
            if lead < config.min_lead or lead > config.max_lead:
                continue

            minute, _ = parse_time(frame.get("time_remaining", "12:00"))
            leading = "home" if hs > as_ else "away"
            rows.append(
                {
                    "lead": lead,
                    "minute": minute,
                    "is_win": int(leading == winner),
                }
            )

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    lookup = (
        df.groupby(["lead", "minute"])["is_win"].agg(["mean", "count"]).reset_index()
    )
    lookup = lookup[lookup["count"] >= config.min_sample_count]
    lookup["safe_win_rate"] = np.clip(
        lookup["mean"] - config.prob_haircut,
        0.0,
        config.prob_cap,
    )

    return {
        (int(r["lead"]), int(r["minute"])): r["safe_win_rate"]
        for _, r in lookup.iterrows()
    }


# ============================================================================
#  RECORDING LOADER
# ============================================================================


def load_recordings(directory: str = "data/recordings") -> List[dict]:
    """Load all completed game recordings for probability table building."""
    paths = glob.glob(os.path.join(directory, "*.json"))
    paths += glob.glob(os.path.join(directory, "synthetic", "*.json"))

    games = []
    for path in sorted(paths):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            continue

        frames = data.get("frames", [])
        metadata = data.get("metadata", {})
        if not frames:
            continue

        final = frames[-1]
        if final.get("period", 0) < 4:
            continue
        if "final" not in str(final.get("game_status", "")).lower():
            continue

        final_home = final.get("home_score", 0)
        final_away = final.get("away_score", 0)
        if final_home == final_away:
            continue

        games.append(
            {
                "home": metadata.get("home_team", "???"),
                "away": metadata.get("away_team", "???"),
                "winner": "home" if final_home > final_away else "away",
                "final_home": final_home,
                "final_away": final_away,
                "frames": frames,
                "path": path,
                "synthetic": metadata.get("synthetic", False),
            }
        )

    return games


def load_single_recording(path: str) -> Optional[dict]:
    """Load a single recording file and return as a game dict, or None."""
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None

    frames = data.get("frames", [])
    metadata = data.get("metadata", {})
    if not frames:
        return None

    final = frames[-1]
    if final.get("period", 0) < 4:
        return None
    if "final" not in str(final.get("game_status", "")).lower():
        return None

    final_home = final.get("home_score", 0)
    final_away = final.get("away_score", 0)
    if final_home == final_away:
        return None

    return {
        "home": metadata.get("home_team", "???"),
        "away": metadata.get("away_team", "???"),
        "winner": "home" if final_home > final_away else "away",
        "final_home": final_home,
        "final_away": final_away,
        "frames": frames,
        "path": path,
        "synthetic": metadata.get("synthetic", False),
    }


# ============================================================================
#  SINGLE-GAME BACKTEST
# ============================================================================


def backtest_single_game(
    game: dict,
    win_rate_map: Dict[Tuple[int, int], float],
    config: DryRunConfig,
    bankroll: float,
) -> dict:
    """Run the scalper strategy on a single game. Returns trade details."""
    frames = game["frames"]
    entered = False
    entry_idx = 0
    trade_side = None
    trade_price = 0.0
    trade_contracts = 0
    trade_cost = 0.0
    trade_lead = 0
    trade_minute = 0
    trade_prob = 0.0

    # --- ENTRY SCAN ---
    for i, frame in enumerate(frames):
        if entered:
            break
        if frame.get("game_status") == "final":
            continue
        period = frame.get("period", 0)
        if period < config.min_period:
            continue

        hs = frame.get("home_score", 0)
        as_ = frame.get("away_score", 0)
        lead = abs(hs - as_)
        minute, _ = parse_time(frame.get("time_remaining", "12:00"))

        if minute > config.max_entry_minutes:
            continue

        prob = win_rate_map.get((lead, minute))
        if prob is None or prob < config.min_win_prob:
            continue

        side = "home" if hs > as_ else "away"
        price = frame.get("home_bid") if side == "home" else frame.get("away_bid")
        if not price or price <= 0 or price >= prob:
            continue
        if price > config.max_entry_price:
            continue

        edge = prob - price
        kelly_pct = (edge / (1.0 - price)) * config.kelly_fraction
        bet_pct = max(0.0, min(kelly_pct, config.max_bet_pct))
        wager = bankroll * bet_pct
        contracts = int(wager / price)
        if contracts <= 0:
            continue

        entered = True
        entry_idx = i
        trade_side = side
        trade_price = price
        trade_contracts = contracts
        trade_cost = contracts * price
        trade_lead = lead
        trade_minute = minute
        trade_prob = prob

    if not entered:
        return {
            "traded": False,
            "game": f"{game['away']}@{game['home']}",
            "winner": game["winner"],
            "final_score": f"{game['final_away']}-{game['final_home']}",
        }

    # --- EXIT LOGIC ---
    stop_price = trade_price - config.stop_loss
    result = "open"
    exit_price = None
    exit_reason = ""

    for frame in frames[entry_idx + 1 :]:
        if frame.get("game_status") == "final":
            break

        cur_bid = (
            frame.get("home_bid") if trade_side == "home" else frame.get("away_bid")
        )
        if cur_bid is None:
            continue

        hs = frame.get("home_score", 0)
        as_ = frame.get("away_score", 0)
        cur_leading = "home" if hs > as_ else "away"
        if cur_leading != trade_side:
            other_bid = (
                frame.get("away_bid") if trade_side == "home" else frame.get("home_bid")
            )
            if other_bid and other_bid > 0.80:
                cur_bid = 0.01

        if cur_bid <= stop_price:
            exit_price = cur_bid
            exit_reason = "stop_loss"
            result = "loss"
            break

    if result == "open":
        if trade_side == game["winner"]:
            revenue = trade_contracts * 1.0
            exit_fee = calculate_fee(1.0, trade_contracts, maker=False)
            exit_price = 1.0
            exit_reason = "resolution_win"
            result = "win"
            pnl = (
                revenue
                - trade_cost
                - calculate_fee(trade_price, trade_contracts, maker=True)
                - exit_fee
            )
        else:
            exit_price = 0.0
            exit_reason = "resolution_loss"
            result = "loss"
            pnl = -trade_cost - calculate_fee(trade_price, trade_contracts, maker=True)
    else:
        # Stopped out
        revenue = trade_contracts * exit_price
        entry_fee = calculate_fee(trade_price, trade_contracts, maker=True)
        exit_fee = calculate_fee(exit_price, trade_contracts, maker=True)
        pnl = revenue - trade_cost - entry_fee - exit_fee

    return {
        "traded": True,
        "game": f"{game['away']}@{game['home']}",
        "winner": game["winner"],
        "final_score": f"{game['final_away']}-{game['final_home']}",
        "side": trade_side,
        "lead": trade_lead,
        "minute": trade_minute,
        "prob": trade_prob,
        "entry_price": trade_price,
        "contracts": trade_contracts,
        "cost": trade_cost,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "result": result,
        "pnl": pnl,
    }


# ============================================================================
#  RECORDING MANAGER
# ============================================================================


class GameRecordingTask:
    """Manages async recording of a single game."""

    def __init__(self, game_info: dict, config: DryRunConfig):
        self.game_id = game_info["game_id"]
        self.home_team = game_info["home_team"]
        self.away_team = game_info["away_team"]
        self.config = config
        self.recorder: Optional[NBAGameRecorder] = None
        self.filepath: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        self.done = False

    def _build_tickers(self) -> Tuple[str, str]:
        """Build Kalshi ticker names for this game."""
        now = datetime.now()
        month_abbrev = now.strftime("%b").upper()
        date_str = f"{now.year % 100}{month_abbrev}{now.day:02d}"
        matchup = f"{self.away_team}{self.home_team}"
        prefix = f"KXNBAGAME-{date_str}{matchup}"
        return f"{prefix}-{self.home_team}", f"{prefix}-{self.away_team}"

    async def start(self, kalshi_client: KalshiClient):
        """Start recording this game."""
        home_ticker, away_ticker = self._build_tickers()

        self.recorder = NBAGameRecorder(
            game_id=self.game_id,
            home_team=self.home_team,
            away_team=self.away_team,
            home_ticker=home_ticker,
            away_ticker=away_ticker,
        )

        try:
            await self.recorder.start_async(
                kalshi_client=kalshi_client,
                poll_interval_ms=self.config.record_poll_ms,
            )
        except Exception as e:
            print(
                f"[ERROR] Recording failed for {self.away_team}@{self.home_team}: {e}"
            )

        # Save recording
        if self.recorder.frames:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.away_team}_vs_{self.home_team}_{date_str}.json"
            self.filepath = str(Path(self.config.output_dir) / filename)
            self.recorder.save(self.filepath)

        self.done = True


# ============================================================================
#  DRY RUN LOG
# ============================================================================


class DryRunLog:
    """Persistent log of dry-run results."""

    def __init__(self, path: str = "logs/dry_run_scalper.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_trades: List[dict] = []
        self.session_bankroll: float = 1000.0

    def append(self, result: dict):
        """Append a backtest result to the log."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            **result,
        }
        self.session_trades.append(entry)

        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def print_session_summary(self):
        """Print summary of this session's results."""
        traded = [t for t in self.session_trades if t.get("traded")]
        if not traded:
            print("\n  No trades this session.")
            return

        wins = sum(1 for t in traded if t["result"] == "win")
        losses = len(traded) - wins
        total_pnl = sum(t["pnl"] for t in traded)

        print(f"\n{'=' * 60}")
        print("  DRY RUN SESSION SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Games analyzed: {len(self.session_trades)}")
        print(f"  Trades taken:   {len(traded)}")
        print(
            f"  Wins: {wins}  Losses: {losses}  "
            f"Win Rate: {wins / len(traded) * 100:.0f}%"
        )
        print(f"  Total P&L: ${total_pnl:+.2f}")
        print(f"  Bankroll: ${self.session_bankroll:.2f}")
        print(f"{'=' * 60}")


# ============================================================================
#  MAIN DAEMON
# ============================================================================


class DryRunDaemon:
    """Main daemon that records games and backtests them."""

    def __init__(
        self, config: DryRunConfig, demo: bool = False, no_record: bool = False
    ):
        self.config = config
        self.demo = demo
        self.no_record = no_record
        self.log = DryRunLog()
        self.active_recordings: Dict[str, GameRecordingTask] = {}
        self.completed_games: set = set()  # game_ids we've already processed
        self.known_files: set = set()  # recording files present at startup
        self._stop = False

        # Track bankroll across the session
        self.log.session_bankroll = config.starting_bankroll

    def _rebuild_prob_table(self) -> Dict[Tuple[int, int], float]:
        """Rebuild probability table from all existing recordings."""
        all_games = load_recordings(self.config.output_dir)
        if not all_games:
            print("[WARN] No completed recordings found for probability table")
            return {}
        table = build_win_rate_table(all_games, self.config)
        print(
            f"[INFO] Probability table rebuilt: {len(table)} cells from {len(all_games)} games"
        )
        return table

    def _run_backtest_on_file(self, filepath: str, win_rate_map: Dict):
        """Run the scalper backtest on a single recording file."""
        game = load_single_recording(filepath)
        if game is None:
            print(f"[WARN] Could not load recording: {filepath}")
            return

        result = backtest_single_game(
            game,
            win_rate_map,
            self.config,
            self.log.session_bankroll,
        )

        # Update session bankroll
        if result["traded"]:
            self.log.session_bankroll += result["pnl"]

        self.log.append(result)
        self._print_result(result, filepath)

    def _print_result(self, result: dict, filepath: str):
        """Print backtest result for a single game."""
        fname = Path(filepath).name
        game = result["game"]
        score = result["final_score"]

        if not result["traded"]:
            print(f"\n  [{fname}] {game} ({score}) -- NO TRADE (no qualifying entry)")
            return

        pnl = result["pnl"]
        icon = "W" if result["result"] == "win" else "L"
        print(f"\n{'=' * 60}")
        print(f"  DRY RUN RESULT: {game} ({score})")
        print(f"  Recording: {fname}")
        print(
            f"  Entry: {result['side'].upper()} +{result['lead']}pts "
            f"@ {result['minute']}min | price={result['entry_price']:.2f} "
            f"| prob={result['prob']:.2f}"
        )
        print(f"  Contracts: {result['contracts']} | Cost: ${result['cost']:.2f}")
        print(f"  Exit: {result['exit_reason']} @ {result['exit_price']:.2f}")
        print(
            f"  [{icon}] P&L: ${pnl:+.2f} | Bankroll: ${self.log.session_bankroll:.2f}"
        )
        print(f"{'=' * 60}")

    async def run(self):
        """Main event loop."""
        print(f"\n{'=' * 60}")
        print("  NBA SCALPER DRY RUN DAEMON")
        print(f"{'=' * 60}")
        print("  Config:")
        print(
            f"    Kelly={self.config.kelly_fraction} MaxBet={self.config.max_bet_pct:.0%} "
            f"MinProb={self.config.min_win_prob:.0%}"
        )
        print(
            f"    MinLead={self.config.min_lead}+ MaxEntry={self.config.max_entry_minutes}min "
            f"StopLoss={'None' if self.config.stop_loss >= 1.0 else self.config.stop_loss}"
        )
        print(f"    Bankroll=${self.config.starting_bankroll:.0f}")
        print(f"    Recording: {'DISABLED' if self.no_record else 'ENABLED'}")
        print(f"    Kalshi API: {'demo' if self.demo else 'production'}")
        print(f"    Poll interval: {self.config.poll_interval}s")
        print(f"    Output: {self.config.output_dir}/")
        print(f"{'=' * 60}\n")

        # Snapshot existing files so we don't backtest old ones
        existing = set(glob.glob(os.path.join(self.config.output_dir, "*.json")))
        self.known_files = existing
        print(f"[INFO] {len(existing)} existing recordings (will skip)")

        # Build initial probability table
        win_rate_map = self._rebuild_prob_table()

        if self.no_record:
            await self._watch_mode(win_rate_map)
        else:
            await self._record_and_backtest_mode(win_rate_map)

    async def _watch_mode(self, win_rate_map: Dict):
        """Watch for new recording files and backtest them (no recording)."""
        print("[MODE] Watch-only: monitoring for new recording files...\n")

        while not self._stop:
            # Check for new files
            current = set(glob.glob(os.path.join(self.config.output_dir, "*.json")))
            new_files = current - self.known_files
            if new_files:
                # Rebuild prob table with fresh data
                win_rate_map = self._rebuild_prob_table()
                for fpath in sorted(new_files):
                    self._run_backtest_on_file(fpath, win_rate_map)
                self.known_files = current

            await asyncio.sleep(self.config.poll_interval)

    async def _record_and_backtest_mode(self, win_rate_map: Dict):
        """Record live games and backtest when they finish."""
        print("[MODE] Record + backtest: monitoring NBA schedule...\n")

        kalshi_client = KalshiClient.from_env(demo=self.demo)

        async with kalshi_client:
            while not self._stop:
                # 1. Check for live/upcoming games
                try:
                    games = list_live_games()
                except Exception as e:
                    print(f"[ERROR] Failed to fetch game list: {e}")
                    await asyncio.sleep(self.config.poll_interval)
                    continue

                now_str = datetime.now().strftime("%H:%M:%S")

                if not games:
                    print(f"[{now_str}] No games on schedule")
                else:
                    live = [g for g in games if g["status"] == "live"]
                    pre = [g for g in games if g["status"] == "pregame"]
                    final = [g for g in games if g["status"] == "final"]
                    recording_ids = set(self.active_recordings.keys())

                    print(
                        f"[{now_str}] Games: {len(live)} live, {len(pre)} upcoming, "
                        f"{len(final)} final | Recording: {len(recording_ids)} | "
                        f"Bankroll: ${self.log.session_bankroll:.2f}"
                    )

                    # 2. Start recording new live games
                    for game in live:
                        gid = game["game_id"]
                        if gid in self.active_recordings or gid in self.completed_games:
                            continue

                        print(
                            f"\n[REC] Starting recording: {game['matchup']} (ID: {gid})"
                        )
                        task = GameRecordingTask(game, self.config)
                        self.active_recordings[gid] = task
                        task.task = asyncio.create_task(task.start(kalshi_client))

                # 3. Check for completed recordings
                done_ids = []
                for gid, rec_task in self.active_recordings.items():
                    if rec_task.done:
                        done_ids.append(gid)

                for gid in done_ids:
                    rec_task = self.active_recordings.pop(gid)
                    self.completed_games.add(gid)

                    if rec_task.filepath:
                        print(
                            f"\n[REC] Recording complete: {rec_task.away_team}@{rec_task.home_team}"
                        )
                        print(f"      Saved: {rec_task.filepath}")

                        # Rebuild probability table with the new recording included
                        win_rate_map = self._rebuild_prob_table()

                        # Run backtest
                        self._run_backtest_on_file(rec_task.filepath, win_rate_map)
                    else:
                        print(f"[WARN] Recording for {gid} produced no frames")

                await asyncio.sleep(self.config.poll_interval)


# ============================================================================
#  CLI
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="NBA Scalper Dry-Run Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--demo", action="store_true", help="Use Kalshi demo API")
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Watch-only mode: backtest new files without recording",
    )
    parser.add_argument(
        "--poll", type=int, default=60, help="Poll interval in seconds (default: 60)"
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=1000.0,
        help="Starting bankroll for simulation (default: 1000)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/recordings",
        help="Output directory for recordings",
    )

    # Strategy overrides
    parser.add_argument("--kelly", type=float, default=0.75)
    parser.add_argument("--max-bet", type=float, default=0.25)
    parser.add_argument("--min-prob", type=float, default=0.95)
    parser.add_argument("--min-lead", type=int, default=8)
    parser.add_argument("--max-entry-min", type=int, default=8)
    parser.add_argument(
        "--stop-loss", type=float, default=1.0, help="Stop loss (1.0 = none)"
    )

    args = parser.parse_args()

    config = DryRunConfig(
        kelly_fraction=args.kelly,
        max_bet_pct=args.max_bet,
        min_win_prob=args.min_prob,
        min_lead=args.min_lead,
        max_entry_minutes=args.max_entry_min,
        stop_loss=args.stop_loss,
        starting_bankroll=args.bankroll,
        poll_interval=args.poll,
        output_dir=args.output,
    )

    daemon = DryRunDaemon(config, demo=args.demo, no_record=args.no_record)

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        print("\n\nShutting down...")
        daemon._stop = True
        daemon.log.print_session_summary()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
