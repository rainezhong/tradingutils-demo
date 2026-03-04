#!/usr/bin/env python3
"""
Paper Trade: Late Game Blowout Strategy

Monitors live NBA games and paper trades when blowout conditions are met:
- Q4 or OT with ≤10 minutes remaining
- Score differential ≥12 points
- Buys YES on the leading team

Usage:
    python scripts/paper_trade_blowout.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import time
import requests
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

# NBA API
try:
    from nba_api.live.nba.endpoints import scoreboard

    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    print("ERROR: nba_api not installed. Run: pip install nba_api")
    exit(1)

from src.kalshi.auth import KalshiAuth
from strategies.late_game_blowout_strategy import (
    LateGameBlowoutStrategy,
    BlowoutStrategyConfig,
    BlowoutSignal,
    BlowoutSide,
)

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class PaperTrade:
    """A paper trade record."""

    game_id: str
    timestamp: datetime
    home_team: str
    away_team: str
    leading_team: str  # 'home' or 'away'
    entry_lead: int  # Lead when we entered
    score_differential: int  # Current lead
    period: int
    time_remaining: str
    confidence: str
    estimated_price: float  # What we estimate we'd pay
    position_size: float

    # Stop loss tracking
    stopped_out: bool = False
    stop_loss_price: Optional[float] = None

    # Outcome (filled when game ends)
    result: Optional[str] = None  # 'win' or 'loss' or 'stopped'
    pnl: Optional[float] = None
    settled_at: Optional[datetime] = None


@dataclass
class GameState:
    """Current state of a game being monitored."""

    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    time_remaining: str
    game_status: str  # 'pregame', 'live', 'final'
    last_update: float


class BlowoutPaperTrader:
    """Paper trader for late game blowout strategy."""

    def __init__(
        self,
        min_point_differential: int = 12,
        max_time_remaining_seconds: int = 600,
        base_position_size: float = 5.0,
        poll_interval: float = 10.0,
    ):
        config = BlowoutStrategyConfig(
            min_point_differential=min_point_differential,
            max_time_remaining_seconds=max_time_remaining_seconds,
            base_position_size=base_position_size,
        )
        self.strategy = LateGameBlowoutStrategy(config)
        self.config = config
        self.poll_interval = poll_interval

        # Kalshi API (optional - falls back to model estimate if unavailable)
        self._kalshi_auth = None
        self._kalshi_available = False
        self._init_kalshi()

        # State
        self.games: Dict[str, GameState] = {}
        self.trades: List[PaperTrade] = []
        self.traded_games: set = set()  # Games we've already traded
        self._ticker_cache: Dict[str, Optional[str]] = {}  # game_id -> ticker

        # Stats
        self.start_time = time.time()
        self.total_pnl = 0.0

    def run(self):
        """Run the paper trader (blocking)."""
        import sys

        print("=" * 60)
        print("LATE GAME BLOWOUT - PAPER TRADER")
        print("=" * 60)
        print("Config:")
        print(f"  Min Point Differential: {self.config.min_point_differential}")
        print(
            f"  Max Time Remaining: {self.config.max_time_remaining_seconds // 60} minutes"
        )
        print(f"  Base Position Size: ${self.config.base_position_size}")
        print(f"  Poll Interval: {self.poll_interval}s")
        print(
            f"  Price Source: {'Kalshi API (live)' if self._kalshi_available else 'Model estimate (fallback)'}"
        )
        print("=" * 60)
        print()
        sys.stdout.flush()

        try:
            while True:
                self._update_games()
                self._check_for_signals()
                self._check_stop_losses()  # Check if any trades need to be stopped out
                self._check_settlements()
                self._print_status()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self._print_final_report()

    def _update_games(self):
        """Fetch latest game data from NBA API."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]

            for game in games_data:
                game_id = game["gameId"]

                # Game status: 1 = Not Started, 2 = Live, 3 = Final
                status_code = game["gameStatus"]
                if status_code == 1:
                    status = "pregame"
                elif status_code == 2:
                    status = "live"
                else:
                    status = "final"

                home = game["homeTeam"]
                away = game["awayTeam"]

                self.games[game_id] = GameState(
                    game_id=game_id,
                    home_team=home["teamTricode"],
                    away_team=away["teamTricode"],
                    home_score=int(home["score"]) if home["score"] else 0,
                    away_score=int(away["score"]) if away["score"] else 0,
                    period=game.get("period", 1),
                    time_remaining=game.get("gameStatusText", "12:00"),
                    game_status=status,
                    last_update=time.time(),
                )

        except Exception as e:
            print(f"[ERROR] Failed to fetch games: {e}")

    def _check_for_signals(self):
        """Check all live games for blowout signals."""
        for game_id, game in self.games.items():
            # Skip if not live or already traded
            if game.game_status != "live":
                continue
            if game_id in self.traded_games:
                continue

            signal = self.strategy.check_entry(
                home_score=game.home_score,
                away_score=game.away_score,
                period=game.period,
                time_remaining=game.time_remaining,
                timestamp=time.time(),
                game_id=game_id,
                home_price=None,
                away_price=None,
            )

            if signal:
                leading_team = (
                    game.home_team
                    if signal.leading_team == BlowoutSide.HOME
                    else game.away_team
                )
                score_diff = signal.score_differential
                estimated_price = self._get_price(game, leading_team, score_diff)
                self._execute_paper_trade(game, signal, estimated_price)

    def _check_stop_losses(self):
        """Check if any active trades should be stopped out."""
        for trade in self.trades:
            # Skip if already settled or stopped out
            if trade.result is not None or trade.stopped_out:
                continue

            game = self.games.get(trade.game_id)
            if not game or game.game_status != "live":
                continue

            # Calculate current lead from our perspective
            if trade.leading_team == "home":
                current_lead = game.home_score - game.away_score
            else:
                current_lead = game.away_score - game.home_score

            # Check stop loss
            if self.strategy.check_stop_loss(
                current_lead=current_lead,
                time_remaining=game.time_remaining,
                entry_lead=trade.entry_lead,
            ):
                self._execute_stop_loss(trade, game, current_lead)

    def _execute_stop_loss(self, trade: PaperTrade, game: GameState, current_lead: int):
        """Execute a stop loss - sell position at current market price."""
        import sys

        # Get current price from Kalshi if available, otherwise estimate
        leading_team = (
            game.home_team if trade.leading_team == "home" else game.away_team
        )
        current_price = self._get_price(game, leading_team, current_lead)

        # Calculate P&L from stop loss
        # We bought at estimated_price, selling at current_price
        # Loss = (entry_price - exit_price) * position_size / entry_price
        # Simplified: if we bought at 95c and sell at 60c, we lose ~37% of position
        pnl = (current_price - trade.estimated_price) * trade.position_size

        trade.stopped_out = True
        trade.stop_loss_price = current_price
        trade.result = "stopped"
        trade.pnl = pnl
        trade.settled_at = datetime.now()
        self.total_pnl += pnl

        print()
        print("X" * 60)
        print("  STOP LOSS TRIGGERED")
        print(f"  {trade.away_team} @ {trade.home_team}")
        print(
            f"  Entry: {trade.leading_team.upper()} +{trade.entry_lead} @ {trade.estimated_price:.2f}"
        )
        print(f"  Current: +{current_lead} @ {current_price:.2f}")
        print(f"  Time remaining: {game.time_remaining}")
        print(f"  P&L: ${pnl:.2f}")
        print(f"  Total P&L: ${self.total_pnl:.2f}")
        print("X" * 60)
        print()
        sys.stdout.flush()

    def _init_kalshi(self):
        """Initialize Kalshi API connection. Non-fatal if credentials missing."""
        try:
            self._kalshi_auth = KalshiAuth.from_env()
            # Quick connectivity test
            self._kalshi_api_request("GET", "/portfolio/balance")
            self._kalshi_available = True
            print("[KALSHI] API connected - using real market prices")
        except Exception as e:
            self._kalshi_available = False
            print(f"[KALSHI] API unavailable ({e}) - falling back to model estimates")

    def _kalshi_api_request(self, method: str, path: str) -> dict:
        """Make authenticated API request to Kalshi."""
        url = f"{KALSHI_API_BASE}{path}"
        full_path = f"/trade-api/v2{path}"

        headers = self._kalshi_auth.sign_request(method, full_path)
        headers["Content-Type"] = "application/json"

        if method == "GET":
            resp = requests.get(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json()

    def _find_market_ticker(self, game: GameState, leading_team: str) -> Optional[str]:
        """Find Kalshi ticker for the leading team's win market. Caches results."""
        cache_key = f"{game.game_id}:{leading_team}"
        if cache_key in self._ticker_cache:
            return self._ticker_cache[cache_key]

        if not self._kalshi_available:
            return None

        try:
            data = self._kalshi_api_request(
                "GET", "/markets?series_ticker=KXNBAGAME&status=open"
            )
            markets = data.get("markets", [])

            home_abbrev = game.home_team[:3].upper()
            away_abbrev = game.away_team[:3].upper()
            leading_abbrev = leading_team[:3].upper()

            for market in markets:
                ticker = market.get("ticker", "").upper()
                if (
                    home_abbrev in ticker
                    and away_abbrev in ticker
                    and ticker.endswith(f"-{leading_abbrev}")
                ):
                    self._ticker_cache[cache_key] = ticker
                    return ticker

            # No match found - cache the miss to avoid repeated lookups
            self._ticker_cache[cache_key] = None
            return None
        except Exception as e:
            print(f"  [KALSHI] Market lookup failed: {e}")
            return None

    def _fetch_kalshi_price(self, ticker: str) -> Optional[float]:
        """Fetch best ask price from Kalshi orderbook. Returns decimal (0-1) or None."""
        try:
            ob = self._kalshi_api_request("GET", f"/markets/{ticker}/orderbook")
            yes_asks = ob.get("orderbook", {}).get("yes", [])
            if yes_asks and len(yes_asks) > 0:
                best_ask_cents = yes_asks[0][0]
                return best_ask_cents / 100.0
            return None
        except Exception as e:
            print(f"  [KALSHI] Orderbook fetch failed for {ticker}: {e}")
            return None

    def _estimate_price_from_model(
        self, score_diff: int, period: int, time_remaining: str
    ) -> float:
        """Fallback: estimate market price from win probability model."""
        time_secs = self.strategy.parse_time_remaining(time_remaining)
        win_prob = self.strategy.calculate_win_probability(score_diff, time_secs)
        estimated_price = win_prob + 0.02  # 2 cents above fair value
        return min(0.95, estimated_price)

    def _get_price(self, game: GameState, leading_team: str, score_diff: int) -> float:
        """Get market price: real Kalshi ask if available, otherwise model estimate."""
        ticker = self._find_market_ticker(game, leading_team)
        if ticker:
            price = self._fetch_kalshi_price(ticker)
            if price is not None:
                return price

        return self._estimate_price_from_model(
            score_diff, game.period, game.time_remaining
        )

    def _execute_paper_trade(
        self, game: GameState, signal: BlowoutSignal, estimated_price: float
    ):
        """Execute a paper trade."""
        position_size = self.strategy.get_position_size(signal.confidence)

        trade = PaperTrade(
            game_id=game.game_id,
            timestamp=datetime.now(),
            home_team=game.home_team,
            away_team=game.away_team,
            leading_team=signal.leading_team.value,
            entry_lead=signal.score_differential,  # Track entry lead for stop loss
            score_differential=signal.score_differential,
            period=game.period,
            time_remaining=game.time_remaining,
            confidence=signal.confidence,
            estimated_price=estimated_price,
            position_size=position_size,
        )

        self.trades.append(trade)
        self.traded_games.add(game.game_id)

        leading = (
            game.home_team
            if signal.leading_team == BlowoutSide.HOME
            else game.away_team
        )

        print()
        print("!" * 60)
        print("  PAPER TRADE EXECUTED")
        print(f"  {game.away_team} @ {game.home_team}")
        print(
            f"  Score: {game.away_team} {game.away_score} - {game.home_score} {game.home_team}"
        )
        print(
            f"  {leading} leads by {signal.score_differential} | Q{game.period} {game.time_remaining}"
        )
        print(f"  Action: BUY YES on {leading}")
        print(f"  Est. Price: {estimated_price:.2f} ({estimated_price * 100:.0f}c)")
        print(f"  Position: ${position_size:.2f}")
        print(f"  Confidence: {signal.confidence.upper()}")
        print("!" * 60)
        print()

    def _check_settlements(self):
        """Check if any trades can be settled."""
        for trade in self.trades:
            if trade.result is not None:
                continue  # Already settled

            game = self.games.get(trade.game_id)
            if not game or game.game_status != "final":
                continue

            # Determine winner
            if game.home_score > game.away_score:
                winner = "home"
            elif game.away_score > game.home_score:
                winner = "away"
            else:
                winner = "tie"

            # Did we win?
            if trade.leading_team == winner:
                trade.result = "win"
                trade.pnl = (1.0 - trade.estimated_price) * trade.position_size
            else:
                trade.result = "loss"
                trade.pnl = -trade.estimated_price * trade.position_size

            trade.settled_at = datetime.now()
            self.total_pnl += trade.pnl

            result_icon = "✓" if trade.result == "win" else "✗"
            print()
            print(
                f"  [{result_icon}] TRADE SETTLED: {trade.away_team} @ {trade.home_team}"
            )
            print(
                f"      Final: {game.away_team} {game.away_score} - {game.home_score} {game.home_team}"
            )
            print(f"      We bet on: {trade.leading_team.upper()}")
            print(f"      Winner: {winner.upper()}")
            print(f"      P&L: ${trade.pnl:.2f}")
            print(f"      Total P&L: ${self.total_pnl:.2f}")
            print()

    def _print_status(self):
        """Print current status."""
        import sys

        now = datetime.now().strftime("%H:%M:%S")
        live_games = [g for g in self.games.values() if g.game_status == "live"]

        # Build status line
        status_parts = [f"[{now}]"]
        status_parts.append(f"Games: {len(live_games)} live")
        status_parts.append(f"Trades: {len(self.trades)}")

        pending = sum(1 for t in self.trades if t.result is None)
        settled = len(self.trades) - pending
        if settled > 0:
            wins = sum(1 for t in self.trades if t.result == "win")
            status_parts.append(f"W/L: {wins}/{settled - wins}")

        status_parts.append(f"P&L: ${self.total_pnl:.2f}")

        print(" | ".join(status_parts))

        # Show games in Q4 or OT
        for game in live_games:
            if game.period >= 4:
                diff = abs(game.home_score - game.away_score)
                leader = (
                    game.home_team
                    if game.home_score > game.away_score
                    else game.away_team
                )
                traded = "TRADED" if game.game_id in self.traded_games else ""

                print(
                    f"    Q{game.period} {game.time_remaining}: {game.away_team} {game.away_score} - {game.home_score} {game.home_team} | {leader} +{diff} {traded}"
                )

        sys.stdout.flush()

    def _print_final_report(self):
        """Print final report."""
        print("\n")
        print("=" * 60)
        print("FINAL REPORT")
        print("=" * 60)

        runtime = time.time() - self.start_time
        print(f"Runtime: {runtime / 60:.1f} minutes")
        print(f"Total Trades: {len(self.trades)}")

        if self.trades:
            settled = [t for t in self.trades if t.result is not None]
            pending = [t for t in self.trades if t.result is None]

            print(f"Settled: {len(settled)}")
            print(f"Pending: {len(pending)}")

            if settled:
                wins = sum(1 for t in settled if t.result == "win")
                losses = len(settled) - wins
                print(f"Wins: {wins}")
                print(f"Losses: {losses}")
                print(f"Win Rate: {wins / len(settled) * 100:.1f}%")

            print(f"\nTotal P&L: ${self.total_pnl:.2f}")

            print("\nTrade History:")
            for t in self.trades:
                status = "PENDING" if t.result is None else t.result.upper()
                pnl_str = f"${t.pnl:.2f}" if t.pnl is not None else "-"
                print(
                    f"  {t.timestamp.strftime('%H:%M')} | {t.away_team} @ {t.home_team} | "
                    f"{t.leading_team.upper()} +{t.score_differential} | {status} | {pnl_str}"
                )

        print("=" * 60)


def main():
    trader = BlowoutPaperTrader(
        min_point_differential=12,
        max_time_remaining_seconds=600,  # 10 minutes
        base_position_size=5.0,
        poll_interval=10.0,  # Check every 10 seconds
    )
    trader.run()


if __name__ == "__main__":
    main()
