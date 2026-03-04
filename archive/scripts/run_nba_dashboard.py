#!/usr/bin/env python3
"""
Run NBA Probability Strategy with the live dashboard.

Usage:
    python scripts/run_nba_dashboard.py

This starts:
1. Dashboard web server on http://localhost:8080
2. Simulated NBA games with win probability calculations
3. Full activity logging to see algorithm decisions

Open http://localhost:8080 in your browser to see live updates.
Press Ctrl+C to stop.
"""

import sys
import os
import threading
import time
import signal
import random
import math
from dataclasses import dataclass
from typing import Optional, Dict

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.state import state_aggregator


@dataclass
class SimulatedOrder:
    """A simulated order."""

    order_id: str
    game_id: str
    side: str  # "YES" or "NO"
    price: float
    size: int
    placed_at: float
    filled: bool = False
    fill_price: Optional[float] = None


@dataclass
class SimulatedPosition:
    """Tracks position and P&L for a game."""

    game_id: str
    yes_contracts: int = 0
    no_contracts: int = 0
    total_cost: float = 0.0
    realized_pnl: float = 0.0


class MockNBAGame:
    """Simulates an NBA game with realistic score progression."""

    def __init__(
        self, game_id: str, home_team: str, away_team: str, home_strength: float = None
    ):
        self.game_id = game_id
        self.home_team = home_team
        self.away_team = away_team

        # Game state
        self.home_score = 0
        self.away_score = 0
        self.period = 1
        self.time_remaining = 12 * 60  # seconds in period
        self.is_live = True

        # Underlying team strength (hidden - this is what we're trying to predict)
        self.home_strength = home_strength or random.uniform(0.45, 0.55)

    def tick(self, seconds: int = 10):
        """Advance game by specified seconds."""
        if not self.is_live:
            return

        self.time_remaining -= seconds

        # Period transitions
        if self.time_remaining <= 0:
            if self.period < 4:
                self.period += 1
                self.time_remaining = 12 * 60
            else:
                # Game over
                self.is_live = False
                return

        # Score updates (roughly 100 points per team per game)
        points_per_second = 2.5 / 60

        for _ in range(seconds):
            if random.random() < points_per_second * self.home_strength * 2:
                self.home_score += random.choice([2, 2, 2, 3])
            if random.random() < points_per_second * (1 - self.home_strength) * 2:
                self.away_score += random.choice([2, 2, 2, 3])

    @property
    def time_remaining_str(self) -> str:
        minutes = self.time_remaining // 60
        seconds = self.time_remaining % 60
        return f"{minutes}:{seconds:02d}"

    @property
    def score_differential(self) -> int:
        """Positive = home leading."""
        return self.home_score - self.away_score


def calculate_win_probability(
    score_diff: int, period: int, time_remaining_sec: int
) -> float:
    """
    Calculate win probability for home team based on current game state.

    Uses a simplified model based on:
    - Current score differential
    - Time remaining
    - Historical NBA data patterns
    """
    period_seconds = 12 * 60
    periods_remaining = 4 - period
    total_seconds_remaining = periods_remaining * period_seconds + time_remaining_sec
    max_seconds = 4 * period_seconds
    time_factor = total_seconds_remaining / max_seconds

    if total_seconds_remaining <= 0:
        return 1.0 if score_diff > 0 else (0.5 if score_diff == 0 else 0.0)

    sigma = 12 * math.sqrt(time_factor)
    if sigma < 0.1:
        sigma = 0.1

    z = score_diff / sigma

    def normal_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    prob = normal_cdf(z)
    return max(0.01, min(0.99, prob))


def simulate_market_price(true_prob: float, staleness: float = 0.0) -> float:
    """Simulate a market price that may be stale/mispriced."""
    noise = random.gauss(0, 0.02)
    stale_anchor = 0.5
    price = true_prob * (1 - staleness) + stale_anchor * staleness + noise
    return max(0.05, min(0.95, price))


class NBAStrategySimulator:
    """Simulates the NBA probability strategy with full logging."""

    def __init__(self):
        self.games: Dict[str, MockNBAGame] = {}
        self.positions: Dict[str, SimulatedPosition] = {}
        self.orders: Dict[str, SimulatedOrder] = {}
        self.order_counter = 0
        self.total_pnl = 0.0
        self.signals_generated = 0

        # Strategy parameters
        self.min_edge_cents = 3.0
        self.position_size = 10
        self.max_period_for_trading = 2  # Only trade in first half

        # Market staleness per game (simulates inefficient markets)
        self.market_staleness = {}

        # Track last logged state to avoid spam
        self._last_logged_period: Dict[str, int] = {}
        self._last_logged_position_limit: Dict[str, bool] = {}

    def add_game(self, game: MockNBAGame, staleness: float = 0.0):
        """Add a game to track."""
        self.games[game.game_id] = game
        self.positions[game.game_id] = SimulatedPosition(game_id=game.game_id)
        self.market_staleness[game.game_id] = staleness

        state_aggregator.log_activity(
            strategy="nba",
            event_type="decision",
            message=f"Started tracking {game.away_team} @ {game.home_team}",
            details={
                "game_id": game.game_id,
                "market_staleness": f"{staleness * 100:.0f}%",
            },
        )

    def tick(self, game_seconds: int = 30):
        """Advance all games and evaluate strategy."""
        for game_id, game in self.games.items():
            if not game.is_live:
                continue

            # Advance game clock
            game.tick(game_seconds)

            # Check if score changed significantly

            # Calculate probabilities
            true_prob = calculate_win_probability(
                game.score_differential, game.period, game.time_remaining
            )

            staleness = self.market_staleness[game.game_id]
            market_price = simulate_market_price(true_prob, staleness)

            # Calculate edge
            edge_cents = (true_prob - market_price) * 100

            # Determine if we should trade
            is_trading_allowed = game.period <= self.max_period_for_trading
            has_edge = abs(edge_cents) >= self.min_edge_cents

            # Generate signal and log decision
            last_signal = None
            if has_edge and is_trading_allowed:
                if edge_cents > 0:
                    last_signal = f"BUY YES ({edge_cents:.1f}c edge)"
                    self._place_order(game_id, "YES", market_price, edge_cents)
                else:
                    last_signal = f"BUY NO ({-edge_cents:.1f}c edge)"
                    self._place_order(game_id, "NO", 1 - market_price, -edge_cents)
            elif has_edge and not is_trading_allowed:
                # Only log once per period transition to avoid spam
                if self._last_logged_period.get(game_id) != game.period:
                    self._last_logged_period[game_id] = game.period
                    state_aggregator.log_activity(
                        strategy="nba",
                        event_type="decision",
                        message=f"{game.away_team}@{game.home_team}: Trading now blocked (entered Q{game.period})",
                        details={
                            "edge_cents": round(edge_cents, 1),
                            "period": game.period,
                            "reason": "Past first half - will resume tracking but not trading",
                        },
                    )
                # Update signal text to be clearer
                last_signal = (
                    f"BLOCKED Q{game.period} ({abs(edge_cents):.1f}c edge exists)"
                )

            # Update position P&L if game ended
            if not game.is_live:
                self._settle_game(game_id, game)

            # Get current position and calculate unrealized P&L
            pos = self.positions[game_id]
            net_position = pos.yes_contracts - pos.no_contracts

            # Calculate unrealized P&L based on current market price
            # YES contracts worth market_price, NO contracts worth (1 - market_price)
            yes_value = pos.yes_contracts * market_price
            no_value = pos.no_contracts * (1 - market_price)
            (yes_value + no_value) - pos.total_cost

            # Publish state to dashboard
            state_aggregator.publish_nba_state(
                game_id=game_id,
                home_team=game.home_team,
                away_team=game.away_team,
                home_score=game.home_score,
                away_score=game.away_score,
                period=game.period,
                time_remaining=game.time_remaining_str,
                home_win_prob=true_prob,
                market_price=market_price,
                edge_cents=edge_cents,
                is_trading_allowed=is_trading_allowed,
                last_signal=last_signal,
                position=net_position,
            )

        # After processing all games, update total P&L
        self._update_total_pnl()

    def _update_total_pnl(self):
        """Calculate and publish total P&L (realized + unrealized)."""
        total_unrealized = 0.0
        for game_id, pos in self.positions.items():
            game = self.games[game_id]
            if game.is_live:
                # Calculate current market value
                true_prob = calculate_win_probability(
                    game.score_differential, game.period, game.time_remaining
                )
                market_price = simulate_market_price(
                    true_prob, self.market_staleness[game_id]
                )

                yes_value = pos.yes_contracts * market_price
                no_value = pos.no_contracts * (1 - market_price)
                total_unrealized += (yes_value + no_value) - pos.total_cost

        total_pnl = self.total_pnl + total_unrealized
        state_aggregator.set_total_profit(total_pnl)

    def _place_order(self, game_id: str, side: str, price: float, edge_cents: float):
        """Place a simulated order."""
        self.order_counter += 1
        order_id = f"ORD-{self.order_counter:04d}"

        game = self.games[game_id]
        pos = self.positions[game_id]

        # Check position limits (max 50 contracts per side)
        current = pos.yes_contracts if side == "YES" else pos.no_contracts
        limit_key = f"{game_id}_{side}"
        if current >= 50:
            # Only log once per side when limit is first hit
            if not self._last_logged_position_limit.get(limit_key):
                self._last_logged_position_limit[limit_key] = True
                state_aggregator.log_activity(
                    strategy="nba",
                    event_type="decision",
                    message=f"{game.away_team}@{game.home_team}: Position limit reached for {side}",
                    details={
                        "side": side,
                        "current_position": current,
                        "limit": 50,
                        "note": "Further orders blocked",
                    },
                )
            return

        order = SimulatedOrder(
            order_id=order_id,
            game_id=game_id,
            side=side,
            price=price,
            size=self.position_size,
            placed_at=time.time(),
        )
        self.orders[order_id] = order
        self.signals_generated += 1

        state_aggregator.log_activity(
            strategy="nba",
            event_type="signal",
            message=f"{game.away_team}@{game.home_team}: {side} signal generated",
            details={
                "order_id": order_id,
                "side": side,
                "price": round(price, 3),
                "size": self.position_size,
                "edge_cents": round(abs(edge_cents), 1),
                "score": f"{game.away_score}-{game.home_score}",
                "period": f"Q{game.period}",
            },
        )

        # Simulate immediate fill (for simplicity)
        self._fill_order(order_id)

    def _fill_order(self, order_id: str):
        """Simulate order fill."""
        order = self.orders[order_id]
        order.filled = True
        order.fill_price = order.price + random.uniform(-0.005, 0.005)  # Small slippage

        game = self.games[order.game_id]
        pos = self.positions[order.game_id]

        # Update position
        cost = order.fill_price * order.size
        if order.side == "YES":
            pos.yes_contracts += order.size
        else:
            pos.no_contracts += order.size
        pos.total_cost += cost

        state_aggregator.log_activity(
            strategy="nba",
            event_type="fill",
            message=f"{game.away_team}@{game.home_team}: Order filled",
            details={
                "order_id": order_id,
                "side": order.side,
                "fill_price": round(order.fill_price, 3),
                "size": order.size,
                "cost": round(cost, 2),
                "position_yes": pos.yes_contracts,
                "position_no": pos.no_contracts,
            },
        )

    def _settle_game(self, game_id: str, game: MockNBAGame):
        """Settle positions when game ends."""
        pos = self.positions[game_id]
        home_won = game.home_score > game.away_score

        # Calculate settlement
        # YES contracts pay $1 if home wins, $0 otherwise
        # NO contracts pay $1 if home loses, $0 otherwise
        if home_won:
            yes_payout = pos.yes_contracts * 1.0
            no_payout = 0
        else:
            yes_payout = 0
            no_payout = pos.no_contracts * 1.0

        total_payout = yes_payout + no_payout
        pnl = total_payout - pos.total_cost
        pos.realized_pnl = pnl
        self.total_pnl += pnl

        result = "HOME WIN" if home_won else "AWAY WIN"
        state_aggregator.log_activity(
            strategy="nba",
            event_type="decision",
            message=f"{game.away_team}@{game.home_team}: Game ended - {result}",
            details={
                "final_score": f"{game.away_score}-{game.home_score}",
                "yes_contracts": pos.yes_contracts,
                "no_contracts": pos.no_contracts,
                "total_cost": round(pos.total_cost, 2),
                "payout": round(total_payout, 2),
                "pnl": round(pnl, 2),
                "cumulative_pnl": round(self.total_pnl, 2),
            },
        )


def main():
    print("=" * 60)
    print("  NBA Probability Strategy - Live Simulation")
    print("=" * 60)
    print()

    # 1. Start dashboard server
    print("[1/3] Starting dashboard server...")

    import uvicorn
    from dashboard.app import create_app

    app = create_app()

    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(2)

    print("      Dashboard running at http://127.0.0.1:8080")
    print()

    # 2. Initialize strategy simulator
    print("[2/3] Initializing NBA strategy simulator...")

    simulator = NBAStrategySimulator()

    # Add games with different market conditions
    games = [
        (
            MockNBAGame("game_1", "LAL", "BOS", home_strength=0.52),
            0.3,
            "Moderate staleness",
        ),
        (
            MockNBAGame("game_2", "GSW", "MIA", home_strength=0.55),
            0.0,
            "Efficient market",
        ),
        (
            MockNBAGame("game_3", "NYK", "CHI", home_strength=0.60),
            0.5,
            "Very stale market",
        ),
    ]

    for game, staleness, description in games:
        simulator.add_game(game, staleness)
        print(f"      {game.away_team} @ {game.home_team}: {description}")

    print()

    # 3. Log strategy parameters
    print("[3/3] Strategy parameters:")
    print(f"      Min edge: {simulator.min_edge_cents} cents")
    print(f"      Position size: {simulator.position_size} contracts")
    print(f"      Trading allowed: Q1-Q{simulator.max_period_for_trading} only")
    print()

    state_aggregator.log_activity(
        strategy="nba",
        event_type="decision",
        message="Strategy initialized",
        details={
            "min_edge_cents": simulator.min_edge_cents,
            "position_size": simulator.position_size,
            "max_trading_period": simulator.max_period_for_trading,
            "games_tracked": len(games),
        },
    )

    print("=" * 60)
    print("  Dashboard: http://127.0.0.1:8080")
    print("  Uncheck 'Arb' and 'MM' to focus on NBA")
    print("  Watch the Activity Log for algorithm decisions")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    # Handle Ctrl+C
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nShutting down...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)

    # Main simulation loop
    tick_interval = 2  # Real seconds between updates
    game_speed = 30  # Game seconds per tick

    while running:
        simulator.tick(game_speed)

        # Check if all games ended
        all_ended = all(not g.is_live for g in simulator.games.values())
        if all_ended:
            state_aggregator.log_activity(
                strategy="nba",
                event_type="decision",
                message="All games completed",
                details={
                    "total_signals": simulator.signals_generated,
                    "total_pnl": round(simulator.total_pnl, 2),
                },
            )
            print(f"\nAll games ended. Total P&L: ${simulator.total_pnl:.2f}")
            print("Restarting games in 5 seconds...")
            time.sleep(5)

            # Reset games
            simulator = NBAStrategySimulator()
            for game, staleness, _ in games:
                new_game = MockNBAGame(game.game_id, game.home_team, game.away_team)
                simulator.add_game(new_game, staleness)

        time.sleep(tick_interval)

    print("Simulation stopped.")


if __name__ == "__main__":
    main()
