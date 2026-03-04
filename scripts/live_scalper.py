#!/usr/bin/env python3
"""
Live Scalper — Grid Search Best Config

Monitors live NBA games and executes the Q4 blowout scalper strategy using
the empirical probability engine built from historical recordings.

Strategy (from grid search):
  - Entry: Q4, lead >= 8, <= 8 min remaining, win_prob >= 95%, price < prob
  - Sizing: Kelly=0.75, max 25% of bankroll
  - Exit: Hold to settlement (no stop loss)

Usage:
    # Set credentials:
    export KALSHI_API_KEY="your-key-id"
    export KALSHI_API_SECRET="/path/to/private_key.pem"

    # Dry run (no real trades):
    python scripts/live_scalper.py --dry-run

    # Live with conservative sizing:
    python scripts/live_scalper.py --max-bet 0.10

    # Full config:
    python scripts/live_scalper.py --bankroll 500 --max-bet 0.25 --kelly 0.75
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import asyncio
import json
import os
import signal
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.live.nba.endpoints import scoreboard

from src.kalshi.client import KalshiClient
from src.kalshi.fees import calculate_fee

# Reuse probability engine from scalper bot
from scripts.nba_scalper_bot import (
    ScalperConfig,
    load_recordings,
    build_win_rate_table,
    parse_time,
)

# ML model imports (lazy — only loaded if --model ml)
_ml_model = None
_hmm_trackers = {}


# ============================================================================
#  CONFIGURATION
# ============================================================================


@dataclass
class LiveScalperConfig:
    # Strategy params — grid search best
    kelly_fraction: float = 0.75
    max_bet_pct: float = 0.05
    min_win_prob: float = 0.95
    min_lead: int = 8
    max_lead: int = 25
    max_entry_minutes: int = 8
    max_entry_price: float = 0.97  # Safety: don't buy above 97c
    min_entry_price: float = 0.80  # Safety: suspiciously low
    min_period: int = 4

    # Operational
    bankroll: float = 500.0
    poll_interval: float = 5.0  # seconds between checks
    dry_run: bool = True

    # Prob engine
    prob_haircut: float = 0.02
    prob_cap: float = 0.98
    min_sample_count: int = 3
    train_pct: float = 0.60

    # Model selection
    model_type: str = "lookup"  # 'lookup' or 'ml'


# ============================================================================
#  LIVE TRADE RECORD
# ============================================================================


@dataclass
class ScalperTrade:
    game_id: str
    ticker: str
    timestamp: datetime
    home_team: str
    away_team: str
    side: str  # 'home' or 'away'
    lead: int
    minute: int
    win_prob: float
    entry_price: float  # decimal 0-1
    contracts: int
    cost: float
    order_id: str = ""
    status: str = "pending"  # pending, filled, settled_win, settled_loss
    pnl: float = 0.0


# ============================================================================
#  LIVE SCALPER
# ============================================================================


class LiveScalper:
    def __init__(self, config: LiveScalperConfig):
        self.config = config
        self.trades: List[ScalperTrade] = []
        self.traded_games: set = set()
        self.bankroll = config.bankroll
        self.start_bankroll = config.bankroll
        self.win_rate_map: Dict[Tuple[int, int], float] = {}
        self._stop = False
        self._ml_model = None
        self._hmm_trackers: Dict[str, object] = {}

        # Log file
        self.log_path = Path("logs/live_scalper.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _build_prob_table(self):
        """Build probability table from training set only (60/40 split).
        Or load ML model if configured."""
        if self.config.model_type == "ml":
            return self._load_ml_model()
        scalper_config = ScalperConfig(
            min_lead=self.config.min_lead,
            max_lead=self.config.max_lead,
            min_sample_count=self.config.min_sample_count,
            prob_haircut=self.config.prob_haircut,
            prob_cap=self.config.prob_cap,
            train_pct=self.config.train_pct,
        )
        all_games = load_recordings(scalper_config)
        if not all_games:
            print("[ERROR] No recordings found for probability table!")
            return

        # Split: only use training set for probability table
        import numpy as np

        np.random.seed(42)
        indices = np.random.permutation(len(all_games))
        split = int(len(all_games) * self.config.train_pct)
        train_games = [all_games[i] for i in indices[:split]]

        self.win_rate_map = build_win_rate_table(train_games, scalper_config)
        print(
            f"[INFO] Probability table: {len(self.win_rate_map)} cells "
            f"from {len(train_games)}/{len(all_games)} games (train split)"
        )

    def _load_ml_model(self):
        """Load the HMM+GBM ML model."""
        try:
            from src.models.hmm_gbm_model import HMMGBMModel

            self._ml_model = HMMGBMModel(model_dir="models/")
            if not self._ml_model._is_fitted:
                print("[ERROR] ML model not found. Run train_win_prob_model.py first.")
                print("[INFO] Falling back to lookup table.")
                self._ml_model = None
                self._build_prob_table_lookup()
            else:
                print("[INFO] Loaded HMM+GBM ML model for probability predictions")
                # Still build a minimal lookup table as fallback
                self.win_rate_map = {}
        except ImportError as e:
            print(f"[ERROR] Cannot load ML model: {e}")
            print("[INFO] Falling back to lookup table.")
            self.config.model_type = "lookup"
            self._build_prob_table()

    def _build_prob_table_lookup(self):
        """Build lookup probability table (fallback)."""
        self.config.model_type = "lookup"
        scalper_config = ScalperConfig(
            min_lead=self.config.min_lead,
            max_lead=self.config.max_lead,
            min_sample_count=self.config.min_sample_count,
            prob_haircut=self.config.prob_haircut,
            prob_cap=self.config.prob_cap,
            train_pct=self.config.train_pct,
        )
        all_games = load_recordings(scalper_config)
        if all_games:
            import numpy as np

            np.random.seed(42)
            indices = np.random.permutation(len(all_games))
            split = int(len(all_games) * self.config.train_pct)
            train_games = [all_games[i] for i in indices[:split]]
            self.win_rate_map = build_win_rate_table(train_games, scalper_config)

    def _find_ticker(self, home_team: str, away_team: str, side: str) -> str:
        """Build Kalshi ticker for the team we want to buy YES on."""
        now = datetime.now()
        month_abbrev = now.strftime("%b").upper()
        date_str = f"{now.year % 100}{month_abbrev}{now.day:02d}"
        matchup = f"{away_team}{home_team}"
        team = home_team if side == "home" else away_team
        return f"KXNBAGAME-{date_str}{matchup}-{team}"

    def _fetch_games(self) -> List[dict]:
        """Fetch live NBA games."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]
            result = []
            for game in games_data:
                status_code = game["gameStatus"]
                if status_code != 2:  # Only live games
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

    def _check_entry(self, game: dict) -> Optional[dict]:
        """Check if a game qualifies for entry. Returns trade params or None."""
        if game["game_id"] in self.traded_games:
            return None

        period = game["period"]
        if period < self.config.min_period:
            return None

        hs = game["home_score"]
        as_ = game["away_score"]
        lead = abs(hs - as_)
        if lead < self.config.min_lead or lead > self.config.max_lead:
            return None

        minute, _ = parse_time(game["time_remaining"])
        if minute > self.config.max_entry_minutes:
            return None

        if self.config.model_type == "ml" and self._ml_model is not None:
            prob = self._ml_predict(game, minute)
        else:
            prob = self.win_rate_map.get((lead, minute))

        if prob is None or prob < self.config.min_win_prob:
            return None

        side = "home" if hs > as_ else "away"

        return {
            "side": side,
            "lead": lead,
            "minute": minute,
            "prob": prob,
        }

    def _ml_predict(self, game: dict, minute: int) -> Optional[float]:
        """Get win probability for the leading side using ML model."""
        from src.backtesting.models.base import GameState
        from src.models.live_inference import LiveHMMTracker

        game_id = game["game_id"]

        # Get or create HMM tracker
        if game_id not in self._hmm_trackers:
            self._hmm_trackers[game_id] = LiveHMMTracker(
                self._ml_model.hmm, game_id, game.get("home_team", "HOME")
            )

        # Poll for latest PBP actions
        tracker = self._hmm_trackers[game_id]
        posteriors = tracker.poll()

        _, sec = parse_time(game["time_remaining"])
        time_remaining_seconds = minute * 60 + sec

        game_state = GameState(
            game_id=game_id,
            home_team=game.get("home_team", "HOME"),
            away_team=game.get("away_team", "AWAY"),
            home_score=game["home_score"],
            away_score=game["away_score"],
            period=game["period"],
            time_remaining_seconds=time_remaining_seconds,
        )

        # Compute PBP-derived stats from tracker's accumulated actions
        period_val = game["period"]
        reg_total = 2880.0
        if period_val <= 4:
            elapsed = reg_total - (time_remaining_seconds + (4 - period_val) * 720.0)
        else:
            elapsed = (
                reg_total + (period_val - 5) * 300.0 + (300.0 - time_remaining_seconds)
            )
        pbp_stats = tracker.get_pbp_stats(max(elapsed, 1.0))

        prediction = self._ml_model.predict(
            game_state, hmm_posteriors=posteriors, pbp_stats=pbp_stats
        )
        prob = prediction.home_win_prob

        # If away is leading, flip
        if game["away_score"] > game["home_score"]:
            prob = 1.0 - prob

        import numpy as np

        return float(
            np.clip(prob - self.config.prob_haircut, 0.0, self.config.prob_cap)
        )

    async def _execute_trade(
        self,
        game: dict,
        entry: dict,
        kalshi_client: KalshiClient,
    ):
        """Execute a trade (or paper trade) on Kalshi."""
        side = entry["side"]
        prob = entry["prob"]

        # Build ticker
        ticker = self._find_ticker(game["home_team"], game["away_team"], side)
        leading_team = game["home_team"] if side == "home" else game["away_team"]

        # Get market price
        try:
            market = await kalshi_client.get_market_data_async(ticker)
            bid_price = market.bid  # 0-1 decimal
        except Exception as e:
            print(f"  [SKIP] Can't get market data for {ticker}: {e}")
            return

        if bid_price is None or bid_price <= 0:
            print(f"  [SKIP] No bid for {ticker}")
            return

        # Validate price
        if bid_price >= prob:
            print(f"  [SKIP] No edge: bid={bid_price:.2f} >= prob={prob:.2f}")
            return
        if bid_price > self.config.max_entry_price:
            print(
                f"  [SKIP] Price too high: {bid_price:.2f} > {self.config.max_entry_price}"
            )
            return
        if bid_price < self.config.min_entry_price:
            print(
                f"  [SKIP] Price too low: {bid_price:.2f} < {self.config.min_entry_price}"
            )
            return

        # Kelly sizing
        edge = prob - bid_price
        kelly_pct = (edge / (1.0 - bid_price)) * self.config.kelly_fraction
        bet_pct = max(0.0, min(kelly_pct, self.config.max_bet_pct))
        wager = self.bankroll * bet_pct
        contracts = int(wager / bid_price)
        if contracts <= 0:
            print(
                f"  [SKIP] Position too small (wager=${wager:.2f}, price={bid_price:.2f})"
            )
            return

        cost = contracts * bid_price
        entry_fee = calculate_fee(bid_price, contracts, maker=True)

        # Print signal
        score = f"{game['away_team']} {game['away_score']} - {game['home_score']} {game['home_team']}"
        print()
        print("!" * 60)
        print("  SCALPER SIGNAL")
        print(f"  {score} | Q{game['period']} {game['time_remaining']}")
        print(f"  {leading_team} leads by {entry['lead']} | WinProb={prob:.1%}")
        print(f"  Ticker: {ticker}")
        print(f"  Bid: {bid_price:.2f} | Edge: {edge:.2f} | Kelly%: {kelly_pct:.1%}")
        print(f"  Contracts: {contracts} | Cost: ${cost:.2f} | Fee: ${entry_fee:.2f}")
        print(f"  Bankroll: ${self.bankroll:.2f} | Bet: {bet_pct:.1%}")

        order_id = ""

        if self.config.dry_run:
            print(f"  [DRY RUN] Would buy {contracts} YES @ {bid_price:.2f}")
            order_id = "DRY_RUN"
        else:
            # Place real order
            try:
                print(f"  Placing order: BUY {contracts} YES @ {bid_price:.2f}")
                order_id = await kalshi_client.place_order_async(
                    ticker=ticker,
                    side="yes",
                    price=bid_price,
                    size=contracts,
                )
                print(f"  ORDER PLACED: {order_id}")
            except Exception as e:
                print(f"  [ERROR] Order failed: {e}")
                print("!" * 60)
                return

        print("!" * 60)
        print()

        # Record trade
        trade = ScalperTrade(
            game_id=game["game_id"],
            ticker=ticker,
            timestamp=datetime.now(),
            home_team=game["home_team"],
            away_team=game["away_team"],
            side=side,
            lead=entry["lead"],
            minute=entry["minute"],
            win_prob=prob,
            entry_price=bid_price,
            contracts=contracts,
            cost=cost,
            order_id=order_id,
            status="filled",
        )
        self.trades.append(trade)
        self.traded_games.add(game["game_id"])

        # Log to file
        self._log_trade(trade)

    def _log_trade(self, trade: ScalperTrade):
        """Append trade to JSONL log."""
        entry = {
            "timestamp": trade.timestamp.isoformat(),
            "game_id": trade.game_id,
            "ticker": trade.ticker,
            "side": trade.side,
            "lead": trade.lead,
            "minute": trade.minute,
            "win_prob": trade.win_prob,
            "entry_price": trade.entry_price,
            "contracts": trade.contracts,
            "cost": trade.cost,
            "order_id": trade.order_id,
            "status": trade.status,
            "dry_run": self.config.dry_run,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _check_settlements(self):
        """Check if any traded games have finished."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]
        except Exception:
            return

        final_games = {}
        for game in games_data:
            if game["gameStatus"] == 3:  # Final
                home = game["homeTeam"]
                away = game["awayTeam"]
                hs = int(home["score"]) if home["score"] else 0
                aws = int(away["score"]) if away["score"] else 0
                winner = "home" if hs > aws else "away"
                final_games[game["gameId"]] = {
                    "winner": winner,
                    "home_score": hs,
                    "away_score": aws,
                }

        for trade in self.trades:
            if trade.status != "filled":
                continue
            if trade.game_id not in final_games:
                continue

            result = final_games[trade.game_id]
            if trade.side == result["winner"]:
                revenue = trade.contracts * 1.0
                exit_fee = calculate_fee(1.0, trade.contracts, maker=False)
                trade.pnl = (
                    revenue
                    - trade.cost
                    - calculate_fee(trade.entry_price, trade.contracts, maker=True)
                    - exit_fee
                )
                trade.status = "settled_win"
            else:
                trade.pnl = -trade.cost - calculate_fee(
                    trade.entry_price, trade.contracts, maker=True
                )
                trade.status = "settled_loss"

            self.bankroll += trade.pnl

            icon = "W" if "win" in trade.status else "L"
            score = f"{result['away_score']}-{result['home_score']}"
            print(
                f"\n  [{icon}] SETTLED: {trade.ticker} | Score: {score} | "
                f"P&L: ${trade.pnl:+.2f} | Bankroll: ${self.bankroll:.2f}"
            )

            # Log settlement
            self._log_trade(trade)

    def _print_status(self, games: List[dict]):
        """Print periodic status."""
        now = datetime.now().strftime("%H:%M:%S")
        live_q4 = [g for g in games if g["period"] >= 4]
        pending = sum(1 for t in self.trades if t.status == "filled")
        settled = sum(1 for t in self.trades if "settled" in t.status)

        print(
            f"[{now}] Live: {len(games)} | Q4+: {len(live_q4)} | "
            f"Trades: {len(self.trades)} (pending={pending}, settled={settled}) | "
            f"Bankroll: ${self.bankroll:.2f}"
        )

        for g in live_q4:
            lead = abs(g["home_score"] - g["away_score"])
            leader = (
                g["home_team"] if g["home_score"] > g["away_score"] else g["away_team"]
            )
            minute, _ = parse_time(g["time_remaining"])
            prob = self.win_rate_map.get((lead, minute), 0)
            traded = " TRADED" if g["game_id"] in self.traded_games else ""
            prob_str = f" prob={prob:.0%}" if prob > 0 else ""
            print(
                f"    Q{g['period']} {g['time_remaining']}: "
                f"{g['away_team']} {g['away_score']} - {g['home_score']} {g['home_team']} "
                f"| {leader} +{lead}{prob_str}{traded}"
            )

        sys.stdout.flush()

    async def run(self):
        """Main event loop."""
        print(f"\n{'=' * 60}")
        print(
            f"  NBA SCALPER — LIVE {'(DRY RUN)' if self.config.dry_run else '*** REAL MONEY ***'}"
        )
        print(f"{'=' * 60}")
        print("  Config:")
        print(
            f"    Kelly={self.config.kelly_fraction} MaxBet={self.config.max_bet_pct:.0%} "
            f"MinProb={self.config.min_win_prob:.0%}"
        )
        print(
            f"    Lead>={self.config.min_lead} MaxEntry={self.config.max_entry_minutes}min "
            f"MaxPrice={self.config.max_entry_price:.0%}"
        )
        print(f"    Bankroll=${self.config.bankroll:.0f}")
        print(f"    Poll: {self.config.poll_interval}s")

        # Build probability engine
        model_label = (
            "ML (HMM+GBM)" if self.config.model_type == "ml" else "Lookup table"
        )
        print(f"\n  Building probability engine ({model_label})...")
        self._build_prob_table()
        if not self.win_rate_map and self._ml_model is None:
            print("[FATAL] No probability engine available. Cannot trade.")
            return

        # Connect to Kalshi
        kalshi_client = KalshiClient.from_env()
        print("  Connecting to Kalshi API...")

        async with kalshi_client:
            # Check balance
            try:
                balance = await kalshi_client.get_balance()
                print(f"  Account balance: ${balance.balance_dollars:.2f}")
                if (
                    not self.config.dry_run
                    and balance.balance_dollars < self.config.bankroll
                ):
                    print(
                        f"  [WARN] Balance (${balance.balance_dollars:.2f}) < "
                        f"bankroll (${self.config.bankroll:.2f})"
                    )
                    self.bankroll = balance.balance_dollars
                    self.start_bankroll = balance.balance_dollars
                    print(f"  [WARN] Adjusted bankroll to ${self.bankroll:.2f}")
            except Exception as e:
                print(f"  [WARN] Could not get balance: {e}")

            print(f"\n{'=' * 60}")
            print("  Monitoring for Q4 blowouts...")
            print(f"{'=' * 60}\n")

            while not self._stop:
                # Fetch games
                games = self._fetch_games()

                # Check for entries
                for game in games:
                    entry = self._check_entry(game)
                    if entry:
                        await self._execute_trade(game, entry, kalshi_client)

                # Check for settlements
                self._check_settlements()

                # Status
                self._print_status(games)

                await asyncio.sleep(self.config.poll_interval)

    def print_final_report(self):
        """Print session summary."""
        print(f"\n{'=' * 60}")
        print("  SESSION REPORT")
        print(f"{'=' * 60}")
        print(f"  Mode: {'DRY RUN' if self.config.dry_run else 'LIVE'}")
        print(f"  Trades: {len(self.trades)}")

        settled = [t for t in self.trades if "settled" in t.status]
        pending = [t for t in self.trades if t.status == "filled"]

        if settled:
            wins = sum(1 for t in settled if t.status == "settled_win")
            losses = len(settled) - wins
            total_pnl = sum(t.pnl for t in settled)
            print(f"  Settled: {len(settled)} (W={wins} L={losses})")
            print(f"  P&L: ${total_pnl:+.2f}")
            print(f"  Win Rate: {wins / len(settled) * 100:.0f}%")

        if pending:
            print(f"  Pending: {len(pending)} (awaiting settlement)")

        print(f"  Starting bankroll: ${self.start_bankroll:.2f}")
        print(f"  Current bankroll: ${self.bankroll:.2f}")
        print(
            f"  Return: {(self.bankroll - self.start_bankroll) / self.start_bankroll * 100:+.1f}%"
        )

        if self.trades:
            print("\n  Trade History:")
            for t in self.trades:
                pnl_str = f"${t.pnl:+.2f}" if t.pnl else "pending"
                print(
                    f"    {t.timestamp.strftime('%H:%M')} | {t.ticker} | "
                    f"+{t.lead}pts {t.minute}min | {t.entry_price:.2f} x{t.contracts} | "
                    f"{t.status} {pnl_str}"
                )

        print(f"  Log: {self.log_path}")
        print(f"{'=' * 60}\n")


# ============================================================================
#  CLI
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="NBA Scalper — Live Execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Paper trade only (default: True)",
    )
    parser.add_argument(
        "--live", action="store_true", help="Execute real trades (overrides --dry-run)"
    )
    parser.add_argument(
        "--bankroll", type=float, default=500.0, help="Starting bankroll (default: 500)"
    )
    parser.add_argument(
        "--kelly", type=float, default=0.75, help="Kelly fraction (default: 0.75)"
    )
    parser.add_argument(
        "--max-bet",
        type=float,
        default=0.05,
        help="Max bet as fraction of bankroll (default: 0.05)",
    )
    parser.add_argument(
        "--min-prob",
        type=float,
        default=0.95,
        help="Min win probability (default: 0.95)",
    )
    parser.add_argument(
        "--min-lead", type=int, default=8, help="Min point lead (default: 8)"
    )
    parser.add_argument(
        "--max-entry-min",
        type=int,
        default=8,
        help="Max entry minutes remaining (default: 8)",
    )
    parser.add_argument(
        "--train-pct",
        type=float,
        default=0.60,
        help="Fraction of data for probability table (default: 0.60)",
    )
    parser.add_argument(
        "--poll", type=float, default=5.0, help="Poll interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--model",
        choices=["lookup", "ml"],
        default="lookup",
        help="Probability engine: 'lookup' or 'ml' (HMM+GBM)",
    )

    args = parser.parse_args()

    config = LiveScalperConfig(
        kelly_fraction=args.kelly,
        max_bet_pct=args.max_bet,
        min_win_prob=args.min_prob,
        min_lead=args.min_lead,
        max_entry_minutes=args.max_entry_min,
        bankroll=args.bankroll,
        train_pct=args.train_pct,
        poll_interval=args.poll,
        dry_run=not args.live,
        model_type=args.model,
    )

    scalper = LiveScalper(config)

    def signal_handler(sig, frame):
        print("\n\nShutting down...")
        scalper._stop = True
        scalper.print_final_report()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    asyncio.run(scalper.run())


if __name__ == "__main__":
    main()
