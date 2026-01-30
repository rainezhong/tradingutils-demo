#!/usr/bin/env python3
"""
LIVE Trade: Late Game Blowout Strategy

Monitors live NBA games and places REAL trades on Kalshi when blowout conditions are met.

Usage:
    python scripts/live_trade_blowout.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import json
import requests
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from nba_api.live.nba.endpoints import scoreboard
from src.kalshi.auth import KalshiAuth
from src.strategies.late_game_blowout import (
    LateGameBlowoutStrategy,
    BlowoutStrategyConfig,
    BlowoutSignal,
    Side,
)


KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


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
    ):
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
        self.position_size = position_size_dollars
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

    def find_nba_market(self, home_team: str, away_team: str) -> Optional[dict]:
        """Find Kalshi market for an NBA game."""
        # Search for NBA game winner markets
        # Kalshi tickers often look like: NBAGSW-25JAN29-PHX or similar
        try:
            # Get NBA markets
            data = self._api_request("GET", "/markets?series_ticker=NBAGSW&status=open")
            markets = data.get("markets", [])

            for market in markets:
                ticker = market.get("ticker", "").upper()
                title = market.get("title", "").upper()

                # Check if this market involves our teams
                if home_team.upper() in ticker or home_team.upper() in title:
                    if away_team.upper() in ticker or away_team.upper() in title:
                        return market

            return None
        except Exception as e:
            print(f"[ERROR] Failed to find market: {e}")
            return None

    def get_orderbook(self, ticker: str) -> dict:
        """Get orderbook for a market."""
        return self._api_request("GET", f"/orderbook/{ticker}")

    def place_order(
        self,
        ticker: str,
        side: str,  # 'yes' or 'no'
        price: int,  # cents
        size: int,  # contracts
    ) -> dict:
        """Place a limit order."""
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "type": "limit",
            "yes_price": price if side == "yes" else None,
            "no_price": price if side == "no" else None,
            "count": size,
        }
        # Remove None values
        body = {k: v for k, v in body.items() if v is not None}

        return self._api_request("POST", "/portfolio/orders", body)

    def run(self):
        """Run the live trader."""
        print("=" * 60)
        print("LATE GAME BLOWOUT - LIVE TRADER")
        print("=" * 60)
        print(f"*** REAL MONEY MODE ***")
        print(f"Config:")
        print(f"  Position Size: ${self.position_size}")
        print(f"  Min Point Differential: {self.config.min_point_differential}")
        print(f"  Max Time Remaining: {self.config.max_time_remaining_seconds // 60} minutes")
        print()

        # Check balance
        try:
            balance = self.get_balance()
            print(f"Account Balance: ${balance:.2f}")
            if balance < self.position_size:
                print(f"[ERROR] Insufficient balance for ${self.position_size} trades!")
                return
        except Exception as e:
            print(f"[ERROR] Failed to get balance: {e}")
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
            games_data = board.get_dict()['scoreboard']['games']

            for game in games_data:
                game_id = game['gameId']
                status_code = game['gameStatus']

                if status_code != 2:  # Only track live games
                    continue

                home = game['homeTeam']
                away = game['awayTeam']

                self.games[game_id] = {
                    'game_id': game_id,
                    'home_team': home['teamTricode'],
                    'away_team': away['teamTricode'],
                    'home_score': int(home['score']) if home['score'] else 0,
                    'away_score': int(away['score']) if away['score'] else 0,
                    'period': game.get('period', 1),
                    'time_remaining': game.get('gameStatusText', '12:00'),
                }
        except Exception as e:
            print(f"[ERROR] Failed to fetch games: {e}")

    def _check_for_signals(self):
        """Check all live games for blowout signals."""
        for game_id, game in self.games.items():
            if game_id in self.traded_games:
                continue

            signal = self.strategy.check_entry(
                home_score=game['home_score'],
                away_score=game['away_score'],
                period=game['period'],
                time_remaining=game['time_remaining'],
                timestamp=time.time(),
                game_id=game_id,
            )

            if signal:
                self._execute_trade(game, signal)

    def _execute_trade(self, game: dict, signal: BlowoutSignal):
        """Execute a real trade on Kalshi."""
        leading_team = game['home_team'] if signal.leading_team == Side.HOME else game['away_team']

        print()
        print("!" * 60)
        print(f"  SIGNAL DETECTED")
        print(f"  {game['away_team']} @ {game['home_team']}")
        print(f"  {leading_team} leads by {signal.score_differential}")
        print(f"  Q{game['period']} {game['time_remaining']}")
        print()

        # Find the market
        market = self.find_nba_market(game['home_team'], game['away_team'])
        if not market:
            print(f"  [SKIP] No Kalshi market found for this game")
            print("!" * 60)
            self.traded_games.add(game['game_id'])  # Don't keep trying
            return

        ticker = market['ticker']
        print(f"  Found market: {ticker}")

        # Get orderbook
        try:
            ob = self.get_orderbook(ticker)
            best_ask = ob.get('yes', {}).get('asks', [[99, 1]])[0][0] if signal.leading_team == Side.HOME else ob.get('no', {}).get('asks', [[99, 1]])[0][0]
        except:
            best_ask = 95  # Default if can't get orderbook

        # Calculate contracts
        contracts = int(self.position_size / (best_ask / 100))
        if contracts < 1:
            contracts = 1

        actual_cost = contracts * best_ask / 100

        print(f"  Side: {'YES' if signal.leading_team == Side.HOME else 'NO'}")
        print(f"  Price: {best_ask}c")
        print(f"  Contracts: {contracts}")
        print(f"  Cost: ${actual_cost:.2f}")
        print()

        # Place order
        try:
            side = 'yes' if signal.leading_team == Side.HOME else 'no'
            result = self.place_order(ticker, side, best_ask, contracts)
            order_id = result.get('order', {}).get('order_id', 'unknown')

            print(f"  ORDER PLACED! ID: {order_id}")

            trade = LiveTrade(
                game_id=game['game_id'],
                order_id=order_id,
                ticker=ticker,
                timestamp=datetime.now(),
                home_team=game['home_team'],
                away_team=game['away_team'],
                leading_team=leading_team,
                score_differential=signal.score_differential,
                period=game['period'],
                time_remaining=game['time_remaining'],
                side=side,
                price=best_ask,
                size=contracts,
                cost=actual_cost,
                status='pending',
            )
            self.trades.append(trade)
            self.traded_games.add(game['game_id'])

        except Exception as e:
            print(f"  [ERROR] Failed to place order: {e}")

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
            if game['period'] >= 4:
                diff = abs(game['home_score'] - game['away_score'])
                leader = game['home_team'] if game['home_score'] > game['away_score'] else game['away_team']
                traded = "TRADED" if game['game_id'] in self.traded_games else ""
                print(f"    Q{game['period']} {game['time_remaining']}: {game['away_team']} {game['away_score']} - {game['home_score']} {game['home_team']} | {leader} +{diff} {traded}")

        sys.stdout.flush()

    def _print_final_report(self):
        """Print final report."""
        print("\n")
        print("=" * 60)
        print("FINAL REPORT")
        print("=" * 60)
        print(f"Total Trades: {len(self.trades)}")

        for t in self.trades:
            print(f"  {t.timestamp.strftime('%H:%M')} | {t.ticker} | {t.side.upper()} @ {t.price}c x{t.size} | ${t.cost:.2f}")

        print("=" * 60)


def main():
    trader = KalshiLiveTrader(
        position_size_dollars=5.0,
        min_point_differential=12,
        max_time_remaining_seconds=600,
        poll_interval=10.0,
    )
    trader.run()


if __name__ == "__main__":
    main()
