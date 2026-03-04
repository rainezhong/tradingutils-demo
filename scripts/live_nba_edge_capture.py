#!/usr/bin/env python3
"""
Live NBA Edge Capture — HMM+GBM model-driven NBA trading.

Monitors live NBA games and enters positions whenever the model's win probability
exceeds the Kalshi market price by a configurable edge threshold. Sizes by
fractional Kelly. Exits when edge evaporates or stop-loss triggers.

Unlike the Q4 blowout scalper (live_scalper.py), this trades across all quarters
(after Q1) and all lead sizes — anywhere the model sees edge.

Unlike live_edge_capture.py (generic Kalshi edge capture), this is NBA-specific
and uses the v3 HMM+GBM model for probability estimation.

Usage:
    # Dry run (default):
    python3 scripts/live_nba_edge_capture.py --dry-run

    # Live with conservative settings:
    python3 scripts/live_nba_edge_capture.py --live --bankroll 500 --min-edge 0.08

    # Custom thresholds:
    python3 scripts/live_nba_edge_capture.py --min-edge 0.06 --exit-edge 0.02 --kelly 0.10
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import asyncio
import json
import os
import signal
from datetime import datetime
from typing import Dict, List, Optional

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.live.nba.endpoints import scoreboard

from src.kalshi.client import KalshiClient
from src.models.hmm_gbm_model import HMMGBMModel
from src.models.live_inference import LiveHMMTracker
from src.backtesting.models.base import GameState
from strategies.edge_capture import NBAEdgeCaptureStrategy, EdgeCaptureConfig

from scripts.nba_scalper_bot import parse_time


class LiveNBAEdgeCapture:
    """Live runner for the NBA edge capture strategy."""

    def __init__(self, config: EdgeCaptureConfig):
        self.config = config
        self.strategy = NBAEdgeCaptureStrategy(config)
        self._stop = False
        self._ml_model: Optional[HMMGBMModel] = None
        self._hmm_trackers: Dict[str, LiveHMMTracker] = {}
        self.log_path = Path("logs/live_nba_edge_capture.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_model(self) -> bool:
        """Load the HMM+GBM model."""
        try:
            self._ml_model = HMMGBMModel(model_dir="models/")
            if not self._ml_model._is_fitted:
                print("[ERROR] ML model not found. Run train_win_prob_model.py first.")
                return False
            print("[INFO] Loaded HMM+GBM model")
            return True
        except Exception as e:
            print(f"[ERROR] Cannot load ML model: {e}")
            return False

    def _fetch_games(self) -> List[dict]:
        """Fetch live NBA games from NBA API."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]
            result = []
            for game in games_data:
                if game["gameStatus"] != 2:  # Only live games
                    continue
                home = game["homeTeam"]
                away = game["awayTeam"]
                result.append(
                    {
                        "game_id": game["gameId"],
                        "home_team": home["teamTricode"],
                        "away_team": away["teamTricode"],
                        "home_score": int(home["score"]) if home["score"] else 0,
                        "away_score": int(away["score"]) if away["score"] else 0,
                        "period": game.get("period", 1),
                        "time_remaining": game.get("gameStatusText", "12:00"),
                    }
                )
            return result
        except Exception as e:
            print(f"[ERROR] Failed to fetch games: {e}")
            return []

    def _get_model_prob(self, game: dict) -> Optional[float]:
        """Get P(home_win) from HMM+GBM model for a game."""
        if self._ml_model is None:
            return None

        game_id = game["game_id"]
        period = game["period"]
        minute, sec = parse_time(game["time_remaining"])
        time_remaining_seconds = minute * 60 + sec

        # Get or create HMM tracker
        if game_id not in self._hmm_trackers:
            self._hmm_trackers[game_id] = LiveHMMTracker(
                self._ml_model.hmm, game_id, game["home_team"]
            )

        tracker = self._hmm_trackers[game_id]
        posteriors = tracker.poll()

        game_state = GameState(
            game_id=game_id,
            home_team=game["home_team"],
            away_team=game["away_team"],
            home_score=game["home_score"],
            away_score=game["away_score"],
            period=period,
            time_remaining_seconds=time_remaining_seconds,
        )

        # Compute elapsed for PBP stats
        reg_total = 2880.0
        if period <= 4:
            elapsed = reg_total - (time_remaining_seconds + (4 - period) * 720.0)
        else:
            elapsed = (
                reg_total + (period - 5) * 300.0 + (300.0 - time_remaining_seconds)
            )
        pbp_stats = tracker.get_pbp_stats(max(elapsed, 1.0))

        prediction = self._ml_model.predict(
            game_state, hmm_posteriors=posteriors, pbp_stats=pbp_stats
        )
        return prediction.home_win_prob

    def _find_ticker(self, home_team: str, away_team: str) -> str:
        """Build Kalshi ticker for the home team YES market."""
        now = datetime.now()
        month_abbrev = now.strftime("%b").upper()
        date_str = f"{now.year % 100}{month_abbrev}{now.day:02d}"
        matchup = f"{away_team}{home_team}"
        return f"KXNBAGAME-{date_str}{matchup}-{home_team}"

    def _compute_elapsed_minutes(self, game: dict) -> float:
        """Compute elapsed minutes from game state."""
        period = game["period"]
        minute, sec = parse_time(game["time_remaining"])
        trs = minute * 60 + sec
        reg_total = 2880.0
        if period <= 4:
            elapsed = reg_total - (trs + (4 - period) * 720.0)
        else:
            elapsed = reg_total + (period - 5) * 300.0 + (300.0 - trs)
        return max(elapsed / 60.0, 0.5)

    def _log_event(self, event: dict) -> None:
        """Append event to JSONL log."""
        event["timestamp"] = datetime.now().isoformat()
        event["dry_run"] = self.config.dry_run
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    async def _process_game(
        self,
        game: dict,
        kalshi_client: KalshiClient,
    ) -> None:
        """Process a single live game: get model prob, get market price, evaluate."""
        game_id = game["game_id"]

        # Get model probability
        model_prob_home = self._get_model_prob(game)
        if model_prob_home is None:
            return

        # Build ticker and get market price
        ticker = self._find_ticker(game["home_team"], game["away_team"])
        try:
            market = await kalshi_client.get_market_data_async(ticker)
            # Use ask for YES (buying price) as implied P(home_win)
            if market.ask and market.ask > 0:
                market_prob_home = market.ask
            elif market.bid and market.bid > 0:
                market_prob_home = market.bid
            else:
                return
        except Exception:
            return

        elapsed_minutes = self._compute_elapsed_minutes(game)

        # Evaluate strategy
        signal_result = self.strategy.evaluate(
            game_id=game_id,
            model_prob_home=model_prob_home,
            market_prob_home=market_prob_home,
            period=game["period"],
            elapsed_minutes=elapsed_minutes,
            ticker=ticker,
            home_team=game["home_team"],
            away_team=game["away_team"],
        )

        if signal_result is None:
            # Print monitoring info for active positions
            if game_id in self.strategy.positions:
                pos = self.strategy.positions[game_id]
                print(
                    f"    [HOLD] {ticker}: edge={pos.current_edge:.1%} "
                    f"pnl=${pos.unrealized_pnl:+.2f}"
                )
            return

        if signal_result["action"] == "enter":
            await self._execute_entry(game, signal_result, kalshi_client)
        elif signal_result["action"] == "exit":
            await self._execute_exit(game_id, signal_result, kalshi_client)

    async def _execute_entry(
        self, game: dict, sig: dict, kalshi_client: KalshiClient
    ) -> None:
        """Execute an entry trade."""
        ticker = sig.get("ticker", "")
        side = sig["side"]
        team = game["home_team"] if side == "home" else game["away_team"]

        score = (
            f"{game['away_team']} {game['away_score']} - "
            f"{game['home_score']} {game['home_team']}"
        )

        print()
        print("!" * 60)
        print("  EDGE CAPTURE SIGNAL")
        print(f"  {score} | Q{game['period']} {game['time_remaining']}")
        print(f"  Backing: {team} ({side})")
        print(
            f"  Model: {sig['model_prob']:.1%} | Market: {sig['market_prob']:.1%} "
            f"| Edge: {sig['edge']:.1%}"
        )
        print(
            f"  Kelly: {sig['kelly_capped']:.1%} | "
            f"Contracts: {sig['contracts']} | Cost: ${sig['cost']:.2f}"
        )
        print(f"  Bankroll: ${self.strategy.bankroll:.2f}")

        if self.config.dry_run:
            print(
                f"  [DRY RUN] Would buy {sig['outcome'].upper()} "
                f"{sig['contracts']}x @ {sig['entry_price']:.2f}"
            )
            self.strategy.enter_position(sig)
        else:
            try:
                order_side = "yes" if sig["outcome"] == "yes" else "no"
                print(
                    f"  Placing order: BUY {order_side.upper()} "
                    f"{sig['contracts']}x @ {sig['entry_price']:.2f}"
                )
                order_id = await kalshi_client.place_order_async(
                    ticker=ticker,
                    side=order_side,
                    price=sig["entry_price"],
                    size=sig["contracts"],
                )
                print(f"  ORDER PLACED: {order_id}")
                self.strategy.enter_position(sig)
            except Exception as e:
                print(f"  [ERROR] Order failed: {e}")

        print("!" * 60)
        print()

        self._log_event(
            {
                "event": "entry",
                "game_id": game["game_id"],
                "ticker": ticker,
                **{k: v for k, v in sig.items() if k != "action"},
            }
        )

    async def _execute_exit(
        self, game_id: str, sig: dict, kalshi_client: KalshiClient
    ) -> None:
        """Execute an exit trade."""
        pos = self.strategy.positions.get(game_id)
        if pos is None:
            return

        print(
            f"\n  [EXIT] {pos.ticker}: {sig['reason']} "
            f"edge={sig.get('current_edge', 0):.1%} "
            f"pnl=${sig.get('unrealized_pnl', 0):+.2f}"
        )

        if self.config.dry_run:
            self.strategy.exit_position(game_id, sig["reason"])
        else:
            try:
                exit_side = "no" if pos.outcome == "yes" else "yes"
                await kalshi_client.place_order_async(
                    ticker=pos.ticker,
                    side=exit_side,
                    price=pos.current_market_prob,
                    size=pos.contracts,
                )
                self.strategy.exit_position(
                    game_id, sig["reason"], pos.current_market_prob
                )
            except Exception as e:
                print(f"  [ERROR] Exit order failed: {e}")

        self._log_event(
            {
                "event": "exit",
                "game_id": game_id,
                "reason": sig["reason"],
            }
        )

    def _check_settlements(self) -> None:
        """Check if any traded games have finished."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]
        except Exception:
            return

        for game in games_data:
            if game["gameStatus"] == 3:  # Final
                game_id = game["gameId"]
                if game_id in self.strategy.positions:
                    home = game["homeTeam"]
                    away = game["awayTeam"]
                    hs = int(home["score"]) if home["score"] else 0
                    aws = int(away["score"]) if away["score"] else 0
                    home_won = hs > aws
                    pos = self.strategy.settle_position(game_id, home_won)
                    if pos:
                        self._log_event(
                            {
                                "event": "settlement",
                                "game_id": game_id,
                                **pos.to_dict(),
                            }
                        )

    def _print_status(self, games: List[dict]) -> None:
        """Print periodic status."""
        now = datetime.now().strftime("%H:%M:%S")
        open_pos = len(self.strategy.positions)
        closed = len(self.strategy.closed_positions)
        stats = self.strategy.get_stats()

        print(
            f"[{now}] Live: {len(games)} | Open: {open_pos} | Closed: {closed} | "
            f"P&L: ${stats['total_pnl']:+.2f} | Bankroll: ${self.strategy.bankroll:.2f}"
        )

        for g in games:
            elapsed = self._compute_elapsed_minutes(g)
            lead = abs(g["home_score"] - g["away_score"])
            leader = (
                g["home_team"] if g["home_score"] > g["away_score"] else g["away_team"]
            )
            tracked = " POSITION" if g["game_id"] in self.strategy.positions else ""
            print(
                f"    Q{g['period']} {g['time_remaining']}: "
                f"{g['away_team']} {g['away_score']} - {g['home_score']} {g['home_team']} "
                f"| {leader} +{lead} | {elapsed:.0f}min{tracked}"
            )

        sys.stdout.flush()

    async def run(self) -> None:
        """Main event loop."""
        print(f"\n{'=' * 60}")
        print(
            f"  NBA EDGE CAPTURE — {'DRY RUN' if self.config.dry_run else '*** LIVE ***'}"
        )
        print(f"{'=' * 60}")
        print("  Config:")
        print(
            f"    min_edge={self.config.min_edge:.0%} exit_edge={self.config.exit_edge:.0%} "
            f"floor_prob={self.config.floor_prob:.0%}"
        )
        print(
            f"    kelly={self.config.kelly_fraction:.0%} "
            f"max_pos={self.config.max_position_pct:.0%} "
            f"max_exposure={self.config.max_exposure_pct:.0%}"
        )
        print(
            f"    stop_loss={self.config.stop_loss_pct:.0%} "
            f"max_games={self.config.max_concurrent_games}"
        )
        print(f"    Bankroll=${self.config.bankroll:.0f}")
        print(f"    Poll: {self.config.poll_interval}s")

        # Load model
        print("\n  Loading HMM+GBM model...")
        if not self._load_model():
            return

        # Connect to Kalshi
        kalshi_client = KalshiClient.from_env()
        print("  Connecting to Kalshi API...")

        async with kalshi_client:
            try:
                balance = await kalshi_client.get_balance()
                print(f"  Account balance: ${balance.balance_dollars:.2f}")
                if (
                    not self.config.dry_run
                    and balance.balance_dollars < self.config.bankroll
                ):
                    print("  [WARN] Balance < bankroll, adjusting")
                    self.strategy.bankroll = balance.balance_dollars
                    self.strategy.start_bankroll = balance.balance_dollars
            except Exception as e:
                print(f"  [WARN] Could not get balance: {e}")

            print(f"\n{'=' * 60}")
            print("  Scanning for edge opportunities...")
            print(f"{'=' * 60}\n")

            while not self._stop:
                games = self._fetch_games()

                for game in games:
                    await self._process_game(game, kalshi_client)

                self._check_settlements()
                self._print_status(games)

                await asyncio.sleep(self.config.poll_interval)

    def print_final_report(self) -> None:
        """Print final session summary."""
        self.strategy.print_report()
        print(f"  Log: {self.log_path}")


def main():
    parser = argparse.ArgumentParser(
        description="NBA Edge Capture — Live Model-Driven Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Paper trade only (default: True)",
    )
    parser.add_argument("--live", action="store_true", help="Execute real trades")
    parser.add_argument("--bankroll", type=float, default=500.0)
    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.06,
        help="Minimum edge to enter (default: 0.06)",
    )
    parser.add_argument(
        "--exit-edge",
        type=float,
        default=0.01,
        help="Exit when edge drops below (default: 0.01)",
    )
    parser.add_argument(
        "--floor-prob",
        type=float,
        default=0.55,
        help="Minimum model probability (default: 0.55)",
    )
    parser.add_argument(
        "--kelly", type=float, default=0.15, help="Max Kelly fraction (default: 0.15)"
    )
    parser.add_argument(
        "--max-pos",
        type=float,
        default=0.05,
        help="Max position per game as pct of bankroll (default: 0.05)",
    )
    parser.add_argument(
        "--max-games", type=int, default=3, help="Max concurrent positions (default: 3)"
    )
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=0.03,
        help="Stop loss as pct of bankroll (default: 0.03)",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=30.0,
        help="Poll interval in seconds (default: 30)",
    )

    args = parser.parse_args()

    config = EdgeCaptureConfig(
        min_edge=args.min_edge,
        exit_edge=args.exit_edge,
        floor_prob=args.floor_prob,
        kelly_fraction=args.kelly,
        max_position_pct=args.max_pos,
        max_concurrent_games=args.max_games,
        stop_loss_pct=args.stop_loss,
        bankroll=args.bankroll,
        poll_interval=args.poll,
        dry_run=not args.live,
    )

    runner = LiveNBAEdgeCapture(config)

    def signal_handler(sig, frame):
        print("\n\nShutting down...")
        runner._stop = True
        runner.print_final_report()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
