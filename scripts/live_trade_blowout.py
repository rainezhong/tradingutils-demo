#!/usr/bin/env python3
"""
LIVE Trade: Late Game Blowout Strategy

Monitors live NBA games and places REAL trades on Kalshi when blowout conditions are met.

Usage:
    python scripts/live_trade_blowout.py [--dry-run] [--position-size 5.0]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import json
import requests
import argparse
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from nba_api.live.nba.endpoints import scoreboard
from src.kalshi.auth import KalshiAuth
from strategies.late_game_blowout_strategy import (
    LateGameBlowoutStrategy,
    BlowoutStrategyConfig,
    BlowoutSignal,
    BlowoutSide,
)

# Import unified order manager for proper YES/NO handling
try:
    from src.execution import UnifiedOrderManager, Outcome, Action

    UNIFIED_MANAGER_AVAILABLE = True
except ImportError:
    UNIFIED_MANAGER_AVAILABLE = False

logger = logging.getLogger(__name__)


KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Safety constants
MAX_PRICE_CENTS = 97  # Don't buy above 97c (only 3% max profit)
MIN_PRICE_CENTS = 80  # Don't buy below 80c (suspicious pricing)
MAX_POSITION_DOLLARS = 25.0  # Hard cap on position size


@dataclass
class LiveTrade:
    """A live trade record."""

    game_id: str
    order_id: str
    ticker: str
    timestamp: datetime
    home_team: str
    away_team: str
    leading_team: str
    score_differential: int
    period: int
    time_remaining: str
    side: str  # 'yes' or 'no'
    price: int  # in cents
    size: int  # number of contracts
    cost: float  # total cost in dollars
    status: str  # 'pending', 'filled', 'cancelled'

    # Outcome
    result: Optional[str] = None
    pnl: Optional[float] = None


class KalshiLiveTrader:
    """Live trader for late game blowout strategy on Kalshi."""

    def __init__(
        self,
        position_size_dollars: float = 5.0,
        min_point_differential: int = 12,
        max_time_remaining_seconds: int = 600,
        poll_interval: float = 10.0,
        dry_run: bool = False,
    ):
        # Mode
        self.dry_run = dry_run

        # Auth
        self.auth = KalshiAuth.from_env()

        # Strategy config
        config = BlowoutStrategyConfig(
            min_point_differential=min_point_differential,
            max_time_remaining_seconds=max_time_remaining_seconds,
            base_position_size=position_size_dollars,
        )
        self.strategy = LateGameBlowoutStrategy(config)
        self.config = config

        # Enforce position size limit
        self.position_size = min(position_size_dollars, MAX_POSITION_DOLLARS)
        if position_size_dollars > MAX_POSITION_DOLLARS:
            print(f"[SAFETY] Position size capped at ${MAX_POSITION_DOLLARS}")

        self.poll_interval = poll_interval

        # State
        self.games: Dict[str, dict] = {}
        self.trades: List[LiveTrade] = []
        self.traded_games: set = set()

        # Stats
        self.start_time = time.time()
        self.total_pnl = 0.0

    def _api_request(self, method: str, path: str, body: dict = None) -> dict:
        """Make authenticated API request to Kalshi."""
        url = f"{KALSHI_API_BASE}{path}"
        full_path = f"/trade-api/v2{path}"  # Full path for signing
        body_str = json.dumps(body) if body else ""

        headers = self.auth.sign_request(method, full_path, body_str)
        headers["Content-Type"] = "application/json"

        if method == "GET":
            resp = requests.get(url, headers=headers)
        elif method == "POST":
            resp = requests.post(url, headers=headers, data=body_str)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json()

    def get_balance(self) -> float:
        """Get current account balance."""
        data = self._api_request("GET", "/portfolio/balance")
        return data["balance"] / 100.0  # Convert cents to dollars

    def find_nba_market(
        self, home_team: str, away_team: str, leading_team: str
    ) -> Optional[dict]:
        """
        Find Kalshi market for the leading team in an NBA game.

        CRITICAL: This function must return the market where buying YES
        means betting the LEADING team wins.

        Ticker format: KXNBAGAME-26JAN30AWYHOM-TEAM
        - The suffix (-TEAM) indicates which team this market is for
        - Buying YES on this market = betting TEAM wins
        """
        try:
            # Get NBA markets with correct series ticker
            data = self._api_request(
                "GET", "/markets?series_ticker=KXNBAGAME&status=open"
            )
            markets = data.get("markets", [])

            # NBA team abbreviations (3 letters)
            home_abbrev = home_team[:3].upper()
            away_abbrev = away_team[:3].upper()
            leading_abbrev = leading_team[:3].upper()

            print(
                f"  [DEBUG] Looking for market: home={home_abbrev}, away={away_abbrev}, leading={leading_abbrev}"
            )

            matching_markets = []
            for market in markets:
                ticker = market.get("ticker", "").upper()

                # Check if both teams in ticker
                if home_abbrev in ticker and away_abbrev in ticker:
                    matching_markets.append(ticker)

                    # CRITICAL: Must end with the LEADING team's abbreviation
                    if ticker.endswith(f"-{leading_abbrev}"):
                        print(f"  [DEBUG] Found correct market: {ticker}")
                        print(f"  [DEBUG] Ticker ends with -{leading_abbrev}: YES")
                        return market

            # Debug: show what markets we found
            if matching_markets:
                print(f"  [DEBUG] Found markets for this game: {matching_markets}")
                print(f"  [DEBUG] But none end with -{leading_abbrev}")
            else:
                print(
                    f"  [DEBUG] No markets found containing both {home_abbrev} and {away_abbrev}"
                )

            return None
        except Exception as e:
            print(f"[ERROR] Failed to find market: {e}")
            import traceback

            traceback.print_exc()
            return None

    def get_orderbook(self, ticker: str) -> dict:
        """Get orderbook for a market."""
        return self._api_request("GET", f"/orderbook/{ticker}")

    def place_order(
        self,
        ticker: str,
        side: str,  # 'yes' or 'no' - which contract
        action: str,  # 'buy' or 'sell' - direction
        price: int,  # cents
        size: int,  # contracts
    ) -> dict:
        """Place a limit order with EXPLICIT side and action.

        CRITICAL: This method does NOT conflate buy/sell with yes/no.
        - side: Which contract type ("yes" or "no")
        - action: Trading direction ("buy" or "sell")

        For blowout strategy, we typically:
        - BUY YES on leading team's market (bet they win)
        """
        body = {
            "ticker": ticker,
            "action": action,  # Explicit action, not hardcoded
            "side": side,
            "type": "limit",
            "count": size,
        }

        # Set price for correct side
        if side == "yes":
            body["yes_price"] = price
        else:
            body["no_price"] = price

        logger.info(
            f"Placing order: {action.upper()} {size} {side.upper()} @ {price}c on {ticker}"
        )

        return self._api_request("POST", "/portfolio/orders", body)

    def run(self):
        """Run the live trader."""
        print("=" * 60)
        print("LATE GAME BLOWOUT - LIVE TRADER")
        print("=" * 60)
        if self.dry_run:
            print("*** DRY RUN MODE - NO REAL TRADES ***")
        else:
            print("*** REAL MONEY MODE ***")
        print("Config:")
        print(f"  Position Size: ${self.position_size}")
        print(f"  Min Point Differential: {self.config.min_point_differential}")
        print(
            f"  Max Time Remaining: {self.config.max_time_remaining_seconds // 60} minutes"
        )
        print(f"  Price Range: {MIN_PRICE_CENTS}c - {MAX_PRICE_CENTS}c")
        print()

        # Check balance
        try:
            balance = self.get_balance()
            print(f"Account Balance: ${balance:.2f}")
            if balance < self.position_size and not self.dry_run:
                print(f"[ERROR] Insufficient balance for ${self.position_size} trades!")
                return
            elif balance < self.position_size and self.dry_run:
                print("[NOTE] Low balance, but continuing in dry-run mode")
        except Exception as e:
            print(f"[ERROR] Failed to get balance: {e}")
            if not self.dry_run:
                return

        print("=" * 60)
        print("Monitoring for blowout opportunities...")
        print()
        sys.stdout.flush()

        try:
            while True:
                self._update_games()
                self._check_for_signals()
                self._print_status()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self._print_final_report()

    def _update_games(self):
        """Fetch latest NBA game data."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]

            for game in games_data:
                game_id = game["gameId"]
                status_code = game["gameStatus"]

                if status_code != 2:  # Only track live games
                    continue

                home = game["homeTeam"]
                away = game["awayTeam"]

                self.games[game_id] = {
                    "game_id": game_id,
                    "home_team": home["teamTricode"],
                    "away_team": away["teamTricode"],
                    "home_score": int(home["score"]) if home["score"] else 0,
                    "away_score": int(away["score"]) if away["score"] else 0,
                    "period": game.get("period", 1),
                    "time_remaining": game.get("gameStatusText", "12:00"),
                }
        except Exception as e:
            print(f"[ERROR] Failed to fetch games: {e}")

    def _check_for_signals(self):
        """Check all live games for blowout signals."""
        for game_id, game in self.games.items():
            if game_id in self.traded_games:
                continue

            signal = self.strategy.check_entry(
                home_score=game["home_score"],
                away_score=game["away_score"],
                period=game["period"],
                time_remaining=game["time_remaining"],
                timestamp=time.time(),
                game_id=game_id,
            )

            if signal:
                self._execute_trade(game, signal)

    def _execute_trade(self, game: dict, signal: BlowoutSignal):
        """Execute a trade on Kalshi with comprehensive validation."""
        # Determine leading team
        home_score = game["home_score"]
        away_score = game["away_score"]

        if signal.leading_team == BlowoutSide.HOME:
            leading_team = game["home_team"]
            game["away_team"]
            expected_diff = home_score - away_score
        else:
            leading_team = game["away_team"]
            game["home_team"]
            expected_diff = away_score - home_score

        leading_abbrev = leading_team[:3].upper()

        print()
        print("!" * 60)
        print("  SIGNAL DETECTED")
        print(f"  {game['away_team']} @ {game['home_team']}")
        print(f"  Score: {away_score} - {home_score}")
        print(f"  {leading_team} leads by {signal.score_differential}")
        print(f"  Q{game['period']} {game['time_remaining']}")
        print()

        # VALIDATION 1: Verify score differential makes sense
        if expected_diff != signal.score_differential:
            print(
                f"  [SAFETY ABORT] Score diff mismatch: expected {expected_diff}, got {signal.score_differential}"
            )
            print("!" * 60)
            return

        # VALIDATION 2: Verify leading team is actually leading
        if expected_diff <= 0:
            print(
                f"  [SAFETY ABORT] {leading_team} is not actually leading! Diff={expected_diff}"
            )
            print("!" * 60)
            return

        # Find the market for the LEADING team
        market = self.find_nba_market(
            game["home_team"], game["away_team"], leading_team
        )
        if not market:
            print(f"  [SKIP] No Kalshi market found for {leading_team}")
            print("!" * 60)
            self.traded_games.add(game["game_id"])
            return

        ticker = market["ticker"]

        # VALIDATION 3: Triple-check ticker ends with leading team
        if not ticker.upper().endswith(f"-{leading_abbrev}"):
            print(
                f"  [SAFETY ABORT] Ticker {ticker} does not end with -{leading_abbrev}!"
            )
            print("  [SAFETY ABORT] This would bet on the WRONG team!")
            print("!" * 60)
            return

        print(f"  Found market: {ticker}")
        print(f"  [VALIDATED] Ticker ends with -{leading_abbrev} ✓")

        # Get orderbook
        try:
            ob = self._api_request("GET", f"/markets/{ticker}/orderbook")
            yes_asks = ob.get("orderbook", {}).get("yes", [])
            if yes_asks and len(yes_asks) > 0:
                best_ask = yes_asks[0][0]
            else:
                print("  [SKIP] No asks available in orderbook")
                print("!" * 60)
                return
        except Exception as e:
            print(f"  [SKIP] Failed to get orderbook: {e}")
            print("!" * 60)
            return

        # VALIDATION 4: Price sanity check
        if best_ask > MAX_PRICE_CENTS:
            print(f"  [SKIP] Price {best_ask}c too high (max {MAX_PRICE_CENTS}c)")
            print("  [SKIP] Not enough profit margin")
            print("!" * 60)
            self.traded_games.add(game["game_id"])
            return

        if best_ask < MIN_PRICE_CENTS:
            print(
                f"  [SKIP] Price {best_ask}c suspiciously low (min {MIN_PRICE_CENTS}c)"
            )
            print("  [SKIP] Market may be stale or have issues")
            print("!" * 60)
            return

        # Calculate contracts
        contracts = int(self.position_size / (best_ask / 100))
        if contracts < 1:
            contracts = 1

        actual_cost = contracts * best_ask / 100

        print(f"  Side: YES (betting {leading_team} wins)")
        print(f"  Price: {best_ask}c")
        print(f"  Contracts: {contracts}")
        print(f"  Cost: ${actual_cost:.2f}")
        print()

        # VALIDATION 5: Final confirmation
        print("  === TRADE CONFIRMATION ===")
        print(f"  Buying YES on: {ticker}")
        print(f"  This bets that: {leading_team} wins")
        print(
            f"  {leading_team} is currently WINNING by {signal.score_differential} pts"
        )
        print("  ===========================")
        print()

        if self.dry_run:
            print("  [DRY RUN] Would place order - not executing")
            self.traded_games.add(game["game_id"])
            trade = LiveTrade(
                game_id=game["game_id"],
                order_id="DRY_RUN",
                ticker=ticker,
                timestamp=datetime.now(),
                home_team=game["home_team"],
                away_team=game["away_team"],
                leading_team=leading_team,
                score_differential=signal.score_differential,
                period=game["period"],
                time_remaining=game["time_remaining"],
                side="yes",
                price=best_ask,
                size=contracts,
                cost=actual_cost,
                status="dry_run",
            )
            self.trades.append(trade)
            print("!" * 60)
            print()
            sys.stdout.flush()
            return

        # Place order - BUY YES on leading team's market
        # This correctly uses explicit action="buy" and side="yes"
        try:
            result = self.place_order(
                ticker, side="yes", action="buy", price=best_ask, size=contracts
            )
            order = result.get("order", {})
            order_id = order.get("order_id", "unknown")
            order_status = order.get("status", "unknown")

            print(f"  ORDER PLACED! ID: {order_id}")
            print(f"  Status: {order_status}")

            # VALIDATION 6: Verify order was accepted
            if order_status not in ["resting", "executed", "pending"]:
                print(f"  [WARNING] Unexpected order status: {order_status}")

            trade = LiveTrade(
                game_id=game["game_id"],
                order_id=order_id,
                ticker=ticker,
                timestamp=datetime.now(),
                home_team=game["home_team"],
                away_team=game["away_team"],
                leading_team=leading_team,
                score_differential=signal.score_differential,
                period=game["period"],
                time_remaining=game["time_remaining"],
                side="yes",
                price=best_ask,
                size=contracts,
                cost=actual_cost,
                status=order_status,
            )
            self.trades.append(trade)
            self.traded_games.add(game["game_id"])

        except Exception as e:
            print(f"  [ERROR] Failed to place order: {e}")
            import traceback

            traceback.print_exc()

        print("!" * 60)
        print()
        sys.stdout.flush()

    def _print_status(self):
        """Print current status."""
        now = datetime.now().strftime("%H:%M:%S")
        live_count = len(self.games)

        status = f"[{now}] Games: {live_count} | Trades: {len(self.trades)}"
        print(status)

        # Show Q4 games
        for game in self.games.values():
            if game["period"] >= 4:
                diff = abs(game["home_score"] - game["away_score"])
                leader = (
                    game["home_team"]
                    if game["home_score"] > game["away_score"]
                    else game["away_team"]
                )
                traded = "TRADED" if game["game_id"] in self.traded_games else ""
                print(
                    f"    Q{game['period']} {game['time_remaining']}: {game['away_team']} {game['away_score']} - {game['home_score']} {game['home_team']} | {leader} +{diff} {traded}"
                )

        sys.stdout.flush()

    def _print_final_report(self):
        """Print final report."""
        print("\n")
        print("=" * 60)
        print("FINAL REPORT")
        print("=" * 60)
        print(f"Total Trades: {len(self.trades)}")

        for t in self.trades:
            print(
                f"  {t.timestamp.strftime('%H:%M')} | {t.ticker} | {t.side.upper()} @ {t.price}c x{t.size} | ${t.cost:.2f}"
            )

        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Late Game Blowout Live Trader")
    parser.add_argument(
        "--dry-run", action="store_true", help="Run in dry-run mode (no real trades)"
    )
    parser.add_argument(
        "--position-size",
        type=float,
        default=5.0,
        help="Position size in dollars (default: 5.0)",
    )
    parser.add_argument(
        "--min-diff",
        type=int,
        default=12,
        help="Minimum point differential (default: 12)",
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=600,
        help="Max time remaining in seconds (default: 600)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        help="Poll interval in seconds (default: 10.0)",
    )

    args = parser.parse_args()

    trader = KalshiLiveTrader(
        position_size_dollars=args.position_size,
        min_point_differential=args.min_diff,
        max_time_remaining_seconds=args.max_time,
        poll_interval=args.poll_interval,
        dry_run=args.dry_run,
    )
    trader.run()


if __name__ == "__main__":
    main()
