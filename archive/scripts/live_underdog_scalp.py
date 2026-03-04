#!/usr/bin/env python3
"""
Live EV-Based Trading Strategy - CLI Runner

*** WORK IN PROGRESS - LIVE TRADING DISABLED ***
This strategy is under development. Only dry-run/paper modes are allowed.
See dry run results from 2026-02-01 for performance analysis.

Strategy Rules:
- Entry: Q1-Q2 only (model accuracy is highest before halftime)
- Signal: Buy when Model Win Probability > Market Price + Min Edge
- Side: Buy whichever team is UNDERPRICED by the model
- Exit: When edge disappears (market corrects) OR hold to settlement
- Hard Stop: -50% P&L (catastrophic only)

Usage:
    python scripts/live_underdog_scalp.py                    # Monitor mode (just watch)
    python scripts/live_underdog_scalp.py --dry-run          # Dry run (simulated trades, no API calls)
    python scripts/live_underdog_scalp.py --paper            # Paper trading (simulated)
    python scripts/live_underdog_scalp.py --min-edge 5.0     # Set minimum edge to 5 cents
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.live.nba.endpoints import scoreboard
from kalshi_utils.client_wrapper import KalshiWrapped
from kalshi_python_sync import OrdersApi
from signal_extraction.data_feeds.score_feed import ScoreAnalyzer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# STRATEGY CONFIGURATION
# =============================================================================
@dataclass
class StrategyConfig:
    """Configuration for the EV-based trading strategy."""

    # EV-based entry conditions
    min_edge_cents: float = 3.0  # Minimum edge to enter (in cents)
    max_period: int = 2  # Q1-Q2 only (model accuracy)

    # Exit strategy
    exit_mode: str = "edge_exit"  # "settlement" or "edge_exit"
    edge_exit_threshold: float = 0.01  # Exit when edge < 1 cent

    # Hard stop loss (catastrophic only)
    stop_loss_pct: float = 0.50  # -50% P&L (wide stop for volatility)

    # Kalshi fee
    fee_rate: float = 0.07  # 7% fee on profit

    # Trading parameters
    position_size_usd: float = 50.0  # $ per trade
    max_positions: int = 5  # Max concurrent positions

    # Polling intervals
    poll_interval_sec: float = 10.0  # How often to check markets

    # Logging
    verbose: bool = False  # Verbose output about games being checked


class TradingMode(Enum):
    MONITOR = "monitor"  # Just watch and alert
    DRY_RUN = "dry_run"  # Simulated trading, no API order calls
    PAPER = "paper"  # Simulated trading (same as dry_run)
    LIVE = "live"  # Real money trading


class ExitType(Enum):
    NONE = "none"
    STOP_LOSS = "stop_loss"
    EDGE_GONE = "edge_gone"
    EDGE_REVERSED = "edge_reversed"
    MANUAL = "manual"
    SETTLEMENT = "settlement"


# =============================================================================
# DATA MODELS
# =============================================================================
@dataclass
class NBAGameState:
    """Live NBA game state from NBA API."""

    game_id: str
    home_team: str  # Tricode e.g., "LAL"
    away_team: str  # Tricode e.g., "BOS"
    home_score: int
    away_score: int
    quarter: int  # 1, 2, 3, 4, 5+ for OT
    clock: str  # e.g., "5:32"
    status: str  # "In Progress", "Final", etc.
    matchup_key: str  # e.g., "BOSLAL" for Kalshi matching

    @property
    def point_diff(self) -> int:
        """Absolute point differential."""
        return abs(self.home_score - self.away_score)

    @property
    def score_diff(self) -> int:
        """Signed score differential (home - away)."""
        return self.home_score - self.away_score

    @property
    def leading_team(self) -> str:
        """Which team is leading."""
        if self.home_score > self.away_score:
            return self.home_team
        elif self.away_score > self.home_score:
            return self.away_team
        return "TIED"


@dataclass
class KalshiMarketState:
    """Live Kalshi market state."""

    ticker: str
    event_ticker: str
    yes_team: str  # Team for YES side
    yes_bid: float  # Best bid for YES
    yes_ask: float  # Best ask for YES
    no_bid: float  # Best bid for NO
    no_ask: float  # Best ask for NO
    volume: int

    @property
    def yes_mid(self) -> float:
        """Midpoint for YES."""
        return (
            (self.yes_bid + self.yes_ask) / 2 if self.yes_bid and self.yes_ask else 0.5
        )

    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        return self.yes_ask - self.yes_bid if self.yes_bid and self.yes_ask else 0.0


@dataclass
class Position:
    """Tracked trading position."""

    id: str
    event_ticker: str
    ticker: str
    side: str  # "YES" or "NO"
    entry_price: float
    entry_time: datetime
    quantity: int
    game_id: str
    matchup_key: str = ""  # e.g., "CLEPHX" for cooldown tracking

    # EV tracking fields
    entry_model_prob: float = 0.0
    entry_market_price: float = 0.0
    entry_edge_cents: float = 0.0

    # Order tracking
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    # Exit tracking
    current_price: float = 0.0
    current_model_prob: float = 0.0
    current_edge_cents: float = 0.0
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_type: ExitType = ExitType.NONE
    pnl: float = 0.0

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L percentage."""
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def is_open(self) -> bool:
        return self.exit_type == ExitType.NONE


# =============================================================================
# NBA API CLIENT
# =============================================================================
class NBALiveClient:
    """Client for fetching live NBA game data."""

    def get_live_games(self) -> List[NBAGameState]:
        """Fetch all currently live NBA games."""
        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]

            live_games = []
            for game in games_data:
                # gameStatus: 1 = Not Started, 2 = In Progress, 3 = Final
                if game["gameStatus"] != 2:
                    continue

                # Parse quarter from gameStatusText (e.g., "Q2 5:32")
                status_text = game.get("gameStatusText", "")
                quarter = self._parse_quarter(status_text)
                clock = self._parse_clock(status_text)

                home = game["homeTeam"]["teamTricode"]
                away = game["awayTeam"]["teamTricode"]

                live_games.append(
                    NBAGameState(
                        game_id=game["gameId"],
                        home_team=home,
                        away_team=away,
                        home_score=game["homeTeam"]["score"],
                        away_score=game["awayTeam"]["score"],
                        quarter=quarter,
                        clock=clock,
                        status=status_text,
                        matchup_key=f"{away}{home}",  # Kalshi format: AWYHOM
                    )
                )

            return live_games

        except Exception as e:
            logger.error(f"Failed to fetch NBA games: {e}")
            return []

    def _parse_quarter(self, status_text: str) -> int:
        """Parse quarter number from status text like 'Q2 5:32'."""
        if not status_text:
            return 0

        status_text = status_text.upper()
        if "Q1" in status_text or "1ST" in status_text:
            return 1
        elif "Q2" in status_text or "2ND" in status_text:
            return 2
        elif "Q3" in status_text or "3RD" in status_text:
            return 3
        elif "Q4" in status_text or "4TH" in status_text:
            return 4
        elif "OT" in status_text:
            return 5
        elif "HALF" in status_text:
            return 2  # Halftime = still Q2 essentially
        return 0

    def _parse_clock(self, status_text: str) -> str:
        """Parse clock from status text."""
        import re

        match = re.search(r"(\d{1,2}:\d{2})", status_text)
        return match.group(1) if match else "0:00"


# =============================================================================
# KALSHI MARKET CLIENT (uses existing wrapper)
# =============================================================================
class KalshiLiveClient:
    """Client for fetching live Kalshi NBA market data."""

    def __init__(self):
        self.wrapper = None

    def connect(self):
        """Initialize the Kalshi client."""
        logger.info("Connecting to Kalshi API...")
        self.wrapper = KalshiWrapped()
        balance = self.wrapper.GetBalance()
        logger.info(f"Connected! Balance: ${balance:.2f}")

    def get_nba_markets(self) -> List[KalshiMarketState]:
        """Get all open NBA game markets."""
        if not self.wrapper:
            self.connect()

        markets = []
        try:
            raw_markets = self.wrapper.GetAllNBAMarkets(status="open")

            for m in raw_markets:
                data = m.model_dump()

                # Parse team from yes_sub_title (e.g., "Lakers win")
                yes_title = data.get("yes_sub_title", "")
                yes_team = yes_title.split()[0] if yes_title else ""

                # Extract prices (convert from cents)
                yes_bid = (data.get("yes_bid", 0) or 0) / 100.0
                yes_ask = (data.get("yes_ask", 100) or 100) / 100.0
                no_bid = (data.get("no_bid", 0) or 0) / 100.0
                no_ask = (data.get("no_ask", 100) or 100) / 100.0

                markets.append(
                    KalshiMarketState(
                        ticker=data["ticker"],
                        event_ticker=data["event_ticker"],
                        yes_team=yes_team,
                        yes_bid=yes_bid,
                        yes_ask=yes_ask,
                        no_bid=no_bid,
                        no_ask=no_ask,
                        volume=data.get("volume", 0),
                    )
                )

        except Exception as e:
            logger.error(f"Failed to fetch Kalshi markets: {e}")

        return markets

    def get_orderbook(self, ticker: str) -> Tuple[float, float]:
        """Get best bid/ask for a market ticker."""
        if not self.wrapper:
            self.connect()

        try:
            client = self.wrapper.GetClient()
            ob = client.get_orderbook(ticker=ticker)

            yes_bid = max([l.price for l in ob.orderbook.yes], default=0) / 100.0
            yes_ask = min([l.price for l in ob.orderbook.no], default=100)
            # no_ask is complement of yes_bid, calculate properly
            yes_ask = (100 - min([l.price for l in ob.orderbook.no], default=0)) / 100.0

            return yes_bid, yes_ask
        except Exception as e:
            logger.warning(f"Failed to get orderbook for {ticker}: {e}")
            return 0.0, 1.0

    def place_order(
        self,
        ticker: str,
        side: str,  # "YES" or "NO"
        price: float,  # 0.01 to 0.99
        quantity: int,
        action: str = "buy",  # "buy" or "sell"
    ) -> Optional[str]:
        """Place an order on Kalshi."""
        if not self.wrapper:
            self.connect()

        try:
            client = self.wrapper.GetClient()
            orders_api = OrdersApi(client)

            # Convert price to cents (1-99)
            price_cents = int(round(price * 100))
            price_cents = max(1, min(99, price_cents))

            # Build order kwargs
            order_kwargs = {
                "ticker": ticker,
                "side": side.lower(),
                "action": action.lower(),
                "count": quantity,
                "type": "limit",
            }

            # Set price based on side
            if side.upper() == "YES":
                order_kwargs["yes_price"] = price_cents
            else:
                order_kwargs["no_price"] = price_cents

            logger.info(
                f"Placing order: {action.upper()} {quantity} {side} @ ${price:.2f}"
            )

            response = orders_api.create_order(**order_kwargs)
            order_id = response.order.order_id

            logger.info(f"Order placed! ID: {order_id}")
            return order_id

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        if not self.wrapper:
            self.connect()

        try:
            client = self.wrapper.GetClient()
            orders_api = OrdersApi(client)
            orders_api.cancel_order(order_id)
            logger.info(f"Cancelled order: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Get status of an order."""
        if not self.wrapper:
            self.connect()

        try:
            client = self.wrapper.GetClient()
            orders_api = OrdersApi(client)
            response = orders_api.get_order(order_id)
            order = response.order
            return {
                "order_id": order.order_id,
                "status": order.status,
                "filled_count": order.fill_count,  # SDK uses fill_count
                "remaining_count": order.remaining_count,
            }
        except Exception as e:
            logger.error(f"Failed to get order status {order_id}: {e}")
            return None


# =============================================================================
# STRATEGY ENGINE
# =============================================================================
@dataclass
class EVTradeSignal:
    """An EV-based trade signal."""

    game: NBAGameState
    market: KalshiMarketState
    side: str  # "YES" or "NO"
    target_team: str  # Team we're betting on
    model_prob: float  # Model's win probability for home team
    market_price: float  # Market price for home team
    edge_cents: float  # Edge in cents (positive = home underpriced)
    signal_reason: str


class EVScalpEngine:
    """Main strategy engine for EV-based scalping."""

    def __init__(
        self,
        config: StrategyConfig,
        mode: TradingMode,
        trade_log_dir: str = "data/live_trades",
    ):
        self.config = config
        self.mode = mode

        # Clients
        self.nba_client = NBALiveClient()
        self.kalshi_client = KalshiLiveClient()

        # State tracking
        self.positions: Dict[str, Position] = {}  # Keyed by market ticker
        self.closed_positions: List[Position] = []
        self.signals_seen: Dict[str, datetime] = {}  # Avoid duplicate alerts
        self.exited_games: Dict[str, datetime] = {}  # Cooldown tracking
        self.pending_entries: set = set()  # Race condition prevention

        # Cooldown period after exit (seconds)
        self.exit_cooldown_sec: float = 300.0  # 5 minutes

        # Stats
        self.total_trades = 0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0

        # Trade logging for live vs backtest comparison
        self.trade_log_dir = Path(trade_log_dir)
        self.trade_log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.trade_log_file = self.trade_log_dir / f"trades_{self.session_id}.jsonl"
        self.trade_log: List[dict] = []

    def _record_trade(self, trade_type: str, data: dict):
        """Record a trade event to file for later comparison with backtest."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "mode": self.mode.value,
            "type": trade_type,
            "config": {
                "min_edge_cents": self.config.min_edge_cents,
                "max_period": self.config.max_period,
                "exit_mode": self.config.exit_mode,
                "stop_loss_pct": self.config.stop_loss_pct,
            },
            **data,
        }
        self.trade_log.append(log_entry)

        # Append to file
        try:
            with open(self.trade_log_file, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write trade log: {e}")

    def _record_entry(self, pos: "Position", signal: "EVTradeSignal"):
        """Record a trade entry to file."""
        self._record_trade(
            "entry",
            {
                "ticker": pos.ticker,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "game_id": pos.game_id,
                "matchup_key": pos.matchup_key,
                "model_prob": pos.entry_model_prob,
                "market_price": pos.entry_market_price,
                "edge_cents": pos.entry_edge_cents,
                "game_state": {
                    "home_team": signal.game.home_team,
                    "away_team": signal.game.away_team,
                    "home_score": signal.game.home_score,
                    "away_score": signal.game.away_score,
                    "quarter": signal.game.quarter,
                    "clock": signal.game.clock,
                },
            },
        )

    def _record_exit(self, pos: "Position"):
        """Record a trade exit to file."""
        self._record_trade(
            "exit",
            {
                "ticker": pos.ticker,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": pos.exit_price,
                "quantity": pos.quantity,
                "exit_type": pos.exit_type.value,
                "pnl_pct": pos.pnl,
                "pnl_dollars": (pos.exit_price - pos.entry_price) * pos.quantity
                if pos.exit_price
                else 0,
                "entry_edge_cents": pos.entry_edge_cents,
                "current_edge_cents": pos.current_edge_cents,
            },
        )

    def _parse_clock_to_seconds(self, clock: str) -> int:
        """Parse clock string (e.g., '5:32') to seconds remaining."""
        try:
            parts = clock.split(":")
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = int(float(parts[1]))
                return minutes * 60 + seconds
        except:
            pass
        return 0

    def _get_home_team_price(
        self, game: NBAGameState, market: KalshiMarketState
    ) -> float:
        """Get the market price for the home team winning.

        Returns the YES mid if YES represents home team, otherwise 1 - YES mid.
        """
        # The yes_team field contains the team name for YES side
        # We need to determine if home team is YES or NO
        # This is tricky - we'll use the ticker which contains team codes
        market.ticker.upper()

        # Kalshi ticker format: KXNBAGAME-26JAN30-AWAY-HOME
        # The last part after final hyphen is usually home team
        # But for simplicity, we'll check if home team appears in yes_team

        # If the yes_team looks like it matches home, use yes_mid
        # Otherwise use 1 - yes_mid (assuming NO is home)

        # Simple heuristic: check if home team tricode appears in yes_team
        # yes_team is usually like "Lakers" or team name, not tricode
        # So we'll use the market mid and assume YES = home for now
        # (This may need refinement based on actual Kalshi ticker format)

        return market.yes_mid

    def match_games_to_markets(
        self, games: List[NBAGameState], markets: List[KalshiMarketState]
    ) -> List[Tuple[NBAGameState, List[KalshiMarketState]]]:
        """Match NBA games to their Kalshi markets."""
        matched = []

        for game in games:
            game_markets = []
            for market in markets:
                ticker_upper = market.ticker.upper()
                event_upper = market.event_ticker.upper()

                # Check if both teams appear in the ticker/event
                if (
                    game.home_team in ticker_upper or game.home_team in event_upper
                ) and (game.away_team in ticker_upper or game.away_team in event_upper):
                    game_markets.append(market)
                # Also check matchup key
                elif (
                    game.matchup_key in ticker_upper or game.matchup_key in event_upper
                ):
                    game_markets.append(market)

            if game_markets:
                matched.append((game, game_markets))

        return matched

    def detect_signals(
        self, game: NBAGameState, markets: List[KalshiMarketState]
    ) -> List[EVTradeSignal]:
        """Detect EV-based trading signals for a game."""
        signals = []
        cfg = self.config

        # 1. Period filter - Q1-Q2 only (model accuracy)
        if game.quarter > cfg.max_period:
            return signals

        # 2. Calculate model probability
        score_diff = game.score_diff  # home - away
        time_seconds = self._parse_clock_to_seconds(game.clock)

        model_prob = ScoreAnalyzer.calculate_win_probability(
            score_diff=score_diff,
            period=game.quarter,
            time_remaining_seconds=time_seconds,
        )

        for market in markets:
            # Validate market data
            if market.yes_bid <= 0 or market.yes_ask <= 0:
                continue
            if market.yes_ask < market.yes_bid:
                logger.debug(f"Skipping {market.ticker}: inverted orderbook")
                continue

            # 3. Get market price for home team
            market_price = self._get_home_team_price(game, market)

            # 4. Calculate edge (model - market)
            # Positive edge means home is underpriced by market
            edge = model_prob - market_price
            edge_cents = abs(edge) * 100

            # 5. Check threshold
            if edge_cents < cfg.min_edge_cents:
                continue

            # 6. Determine trade side
            if edge > 0:
                # Model says home underpriced -> BUY YES (home)
                side = "YES"
                target_team = game.home_team
            else:
                # Model says home overpriced -> BUY NO (away)
                side = "NO"
                target_team = game.away_team

            # Check if we already have a position
            if market.ticker in self.positions:
                continue

            # Check for existing position in this game
            game_has_position = any(
                game.matchup_key in pos.event_ticker
                for pos in self.positions.values()
                if pos.is_open
            )
            if game_has_position or game.matchup_key in self.pending_entries:
                continue

            # Check cooldown
            if game.matchup_key in self.exited_games:
                time_since_exit = (
                    datetime.now() - self.exited_games[game.matchup_key]
                ).total_seconds()
                if time_since_exit < self.exit_cooldown_sec:
                    continue

            # Check max positions
            if (
                len([p for p in self.positions.values() if p.is_open])
                >= cfg.max_positions
            ):
                continue

            # Build signal
            signals.append(
                EVTradeSignal(
                    game=game,
                    market=market,
                    side=side,
                    target_team=target_team,
                    model_prob=model_prob,
                    market_price=market_price,
                    edge_cents=edge_cents,
                    signal_reason=f"Model={model_prob * 100:.1f}% vs Market={market_price * 100:.1f}% | Edge={edge_cents:.1f}c | Q{game.quarter} {game.clock}",
                )
            )

        return signals

    def check_exits(
        self, games: List[NBAGameState], markets: List[KalshiMarketState]
    ) -> List[Position]:
        """Check if any positions should be exited based on edge disappearing."""
        exits = []
        cfg = self.config

        # Build lookup maps
        market_map = {m.ticker: m for m in markets}
        game_map = {}
        for game in games:
            game_map[game.matchup_key] = game

        for pos_id, pos in list(self.positions.items()):
            if not pos.is_open:
                continue

            market = market_map.get(pos.ticker)
            game = game_map.get(pos.matchup_key) if pos.matchup_key else None

            # Update current price
            if market:
                if pos.side == "YES":
                    pos.current_price = market.yes_bid  # Exit at bid
                else:
                    pos.current_price = market.no_bid

            # Calculate current edge if we have game data
            if game and market:
                score_diff = game.score_diff
                time_seconds = self._parse_clock_to_seconds(game.clock)

                current_model_prob = ScoreAnalyzer.calculate_win_probability(
                    score_diff=score_diff,
                    period=game.quarter,
                    time_remaining_seconds=time_seconds,
                )

                current_market_price = self._get_home_team_price(game, market)
                current_edge = current_model_prob - current_market_price

                pos.current_model_prob = current_model_prob
                pos.current_edge_cents = abs(current_edge) * 100

                # Determine if we should exit (edge_exit mode)
                if cfg.exit_mode == "edge_exit":
                    # 1. Exit if edge disappeared (market corrected to fair value)
                    if abs(current_edge) < cfg.edge_exit_threshold:
                        pnl_pct = pos.unrealized_pnl_pct
                        if pnl_pct > 0:  # Only if profitable
                            pos.exit_type = ExitType.EDGE_GONE
                            pos.exit_price = pos.current_price
                            pos.exit_time = datetime.now()
                            pos.pnl = pnl_pct * (1 - cfg.fee_rate)
                            exits.append(pos)
                            continue

                    # 2. Exit if edge reversed (we're on wrong side now)
                    was_long_home = pos.side == "YES"
                    model_says_long_home = current_edge > 0
                    edge_reversed = (was_long_home != model_says_long_home) and abs(
                        current_edge
                    ) * 100 >= cfg.min_edge_cents

                    if edge_reversed:
                        pos.exit_type = ExitType.EDGE_REVERSED
                        pos.exit_price = pos.current_price
                        pos.exit_time = datetime.now()
                        pnl_pct = pos.unrealized_pnl_pct
                        pos.pnl = (
                            pnl_pct * (1 - cfg.fee_rate) if pnl_pct > 0 else pnl_pct
                        )
                        exits.append(pos)
                        continue

            # 3. Hard stop loss (catastrophic only)
            pnl_pct = pos.unrealized_pnl_pct
            if pnl_pct <= -cfg.stop_loss_pct:
                pos.exit_type = ExitType.STOP_LOSS
                pos.exit_price = pos.current_price
                pos.exit_time = datetime.now()
                pos.pnl = pnl_pct
                exits.append(pos)
                continue

        return exits

    def execute_entry(self, signal: EVTradeSignal) -> Optional[Position]:
        """Execute an entry trade."""
        cfg = self.config

        # Safety check
        game_has_position = any(
            signal.game.matchup_key in pos.event_ticker
            for pos in self.positions.values()
            if pos.is_open
        )
        if game_has_position or signal.game.matchup_key in self.pending_entries:
            return None

        # Mark as pending
        self.pending_entries.add(signal.game.matchup_key)

        try:
            return self._do_execute_entry(signal, cfg)
        finally:
            self.pending_entries.discard(signal.game.matchup_key)

    def _do_execute_entry(
        self, signal: EVTradeSignal, cfg: StrategyConfig
    ) -> Optional[Position]:
        """Internal entry execution."""
        # Calculate fill price (buy at ask)
        if signal.side == "YES":
            fill_price = signal.market.yes_ask
        else:
            fill_price = signal.market.no_ask

        if fill_price <= 0 or fill_price >= 1:
            logger.warning(
                f"Invalid fill price {fill_price:.3f} for {signal.market.ticker}"
            )
            return None

        # Calculate quantity
        quantity = int(cfg.position_size_usd / fill_price) if fill_price > 0 else 0
        if quantity <= 0:
            logger.warning(f"Calculated 0 contracts for {signal.market.ticker}")
            return None

        pos_id = f"{signal.market.ticker}_{int(time.time())}"

        pos = Position(
            id=pos_id,
            event_ticker=signal.market.event_ticker,
            ticker=signal.market.ticker,
            side=signal.side,
            entry_price=fill_price,
            entry_time=datetime.now(),
            quantity=quantity,
            game_id=signal.game.game_id,
            matchup_key=signal.game.matchup_key,
            current_price=fill_price,
            # EV tracking
            entry_model_prob=signal.model_prob,
            entry_market_price=signal.market_price,
            entry_edge_cents=signal.edge_cents,
        )

        if self.mode == TradingMode.LIVE:
            order_id = self.kalshi_client.place_order(
                ticker=signal.market.ticker,
                side=signal.side,
                price=fill_price,
                quantity=quantity,
                action="buy",
            )

            if not order_id:
                logger.error(f"Failed to place entry order for {signal.market.ticker}")
                return None

            pos.entry_order_id = order_id

            # Verify fill
            order_status = self.kalshi_client.get_order_status(order_id)
            if order_status:
                filled = order_status.get("filled_count", 0)
                if filled == 0:
                    logger.warning(f"Order {order_id} not filled yet, waiting...")
                    for _ in range(5):
                        time.sleep(1)
                        order_status = self.kalshi_client.get_order_status(order_id)
                        if order_status and order_status.get("filled_count", 0) > 0:
                            filled = order_status["filled_count"]
                            break

                if filled == 0:
                    logger.error(f"Order {order_id} not filled, cancelling")
                    self.kalshi_client.cancel_order(order_id)
                    return None

                if filled < quantity:
                    logger.warning(f"Partial fill: {filled}/{quantity}")
                    pos.quantity = filled

        elif self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]:
            logger.debug(
                f"[DRY RUN] Simulated entry: {signal.side} {quantity} @ ${fill_price:.3f}"
            )

        self.positions[signal.market.ticker] = pos
        self.total_trades += 1

        return pos

    def execute_exit(self, pos: Position) -> bool:
        """Execute an exit trade."""
        if self.mode == TradingMode.LIVE:
            order_id = self.kalshi_client.place_order(
                ticker=pos.ticker,
                side=pos.side,
                price=pos.exit_price,
                quantity=pos.quantity,
                action="sell",
            )

            if order_id:
                pos.exit_order_id = order_id
            else:
                logger.error(f"Failed to place exit order for {pos.ticker}")
                return False

        elif self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]:
            logger.debug(
                f"[DRY RUN] Simulated exit: {pos.side} @ ${pos.exit_price:.3f}"
            )

        # Remove from active positions
        if pos.ticker in self.positions:
            del self.positions[pos.ticker]

        # Track cooldown
        if pos.matchup_key:
            self.exited_games[pos.matchup_key] = datetime.now()

        self.closed_positions.append(pos)
        self.total_pnl += pos.pnl

        if pos.pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

        return True

    def run(self):
        """Main run loop."""
        logger.info("=" * 60)
        logger.info("EV-BASED TRADING STRATEGY - LIVE MONITOR")
        logger.info("=" * 60)
        logger.info(f"Mode: {self.mode.value.upper()}")
        logger.info("Config:")
        logger.info(f"  Min edge: {self.config.min_edge_cents:.1f} cents")
        logger.info(f"  Max period: Q{self.config.max_period}")
        logger.info(f"  Exit mode: {self.config.exit_mode}")
        logger.info(f"  Stop loss: -{self.config.stop_loss_pct * 100:.0f}%")
        logger.info(f"  Position size: ${self.config.position_size_usd:.0f}")
        logger.info("=" * 60)

        # Connect to Kalshi
        self.kalshi_client.connect()

        logger.info("\nStarting main loop (Ctrl+C to stop)...\n")

        try:
            while True:
                self._poll_cycle()
                time.sleep(self.config.poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("\n\nShutting down...")
            self._print_summary()

    def _poll_cycle(self):
        """Single polling cycle."""
        try:
            games = self.nba_client.get_live_games()
        except Exception as e:
            logger.error(f"Failed to fetch NBA games: {e}")
            games = []

        try:
            markets = self.kalshi_client.get_nba_markets()
        except Exception as e:
            logger.error(f"Failed to fetch Kalshi markets: {e}")
            markets = []

        if self.config.verbose:
            logger.info(f"\n{'─' * 50}")
            logger.info(f"Polling at {datetime.now().strftime('%H:%M:%S')}")
            logger.info(
                f"   Found {len(games)} live NBA games, {len(markets)} Kalshi markets"
            )

        if not games:
            if self.config.verbose:
                logger.info("   No live NBA games right now")
            return

        # Log each game
        if self.config.verbose:
            for game in games:
                q_ok = "Y" if game.quarter <= self.config.max_period else "N"
                logger.info(
                    f"   [Q{game.quarter} {q_ok}] {game.away_team} {game.away_score} @ {game.home_team} {game.home_score} ({game.clock})"
                )

        # Match games to markets
        matched = self.match_games_to_markets(games, markets)

        if not matched:
            if self.config.verbose:
                logger.info("   No matching Kalshi markets")
            return

        # Clean up expired cooldowns
        now = datetime.now()
        expired = [
            k
            for k, t in self.exited_games.items()
            if (now - t).total_seconds() > self.exit_cooldown_sec
        ]
        for k in expired:
            del self.exited_games[k]

        # Check for exits
        try:
            exits = self.check_exits(games, markets)
            for pos in exits:
                self._log_exit(pos)
                self.execute_exit(pos)
        except Exception as e:
            logger.error(f"Error checking exits: {e}")

        # Detect and process signals
        for game, game_markets in matched:
            try:
                signals = self.detect_signals(game, game_markets)
            except Exception as e:
                logger.error(f"Error detecting signals for {game.matchup_key}: {e}")
                continue

            for signal in signals:
                try:
                    self._log_signal(signal)
                    if self.mode in [
                        TradingMode.PAPER,
                        TradingMode.LIVE,
                        TradingMode.DRY_RUN,
                    ]:
                        pos = self.execute_entry(signal)
                        if pos:
                            self._log_entry(pos, signal)
                except Exception as e:
                    logger.error(f"Error processing signal: {e}")

    def _log_signal(self, signal: EVTradeSignal):
        """Log a trading signal."""
        key = f"{signal.market.event_ticker}_{signal.side}"
        if key in self.signals_seen:
            if (datetime.now() - self.signals_seen[key]).seconds < 60:
                return
        self.signals_seen[key] = datetime.now()

        logger.info("")
        logger.info("SIGNAL DETECTED")
        logger.info(f"   Game: {signal.game.away_team} @ {signal.game.home_team}")
        logger.info(f"   Score: {signal.game.away_score} - {signal.game.home_score}")
        logger.info(f"   Market: {signal.market.ticker}")
        logger.info(f"   Side: BUY {signal.side} ({signal.target_team})")
        logger.info(
            f"   Model: {signal.model_prob * 100:.1f}% | Market: {signal.market_price * 100:.1f}%"
        )
        logger.info(f"   Edge: {signal.edge_cents:.1f} cents")
        logger.info(f"   Reason: {signal.signal_reason}")

    def _log_entry(self, pos: Position, signal: EVTradeSignal):
        """Log a trade entry."""
        mode_tag = (
            "[DRY RUN] "
            if self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]
            else ""
        )
        logger.info("")
        logger.info(f"{mode_tag}ENTERED POSITION")
        logger.info(f"   {pos.side} {pos.quantity} contracts @ ${pos.entry_price:.3f}")
        logger.info(f"   Cost: ${pos.quantity * pos.entry_price:.2f}")
        logger.info(f"   Entry edge: {pos.entry_edge_cents:.1f} cents")

        # Record to file for later comparison
        self._record_entry(pos, signal)

    def _log_exit(self, pos: Position):
        """Log a trade exit."""
        exit_reason = pos.exit_type.value.replace("_", " ").title()
        pnl_str = f"{pos.pnl * 100:+.2f}%"
        mode_tag = (
            "[DRY RUN] "
            if self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]
            else ""
        )

        logger.info("")
        logger.info(f"{mode_tag}EXITED POSITION")
        logger.info(f"   {pos.side} @ ${pos.entry_price:.3f} -> ${pos.exit_price:.3f}")
        logger.info(f"   Exit reason: {exit_reason}")
        logger.info(f"   P&L: {pnl_str}")

        # Record to file for later comparison
        self._record_exit(pos)

    def _print_summary(self):
        """Print session summary."""
        logger.info("\n" + "=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info("=" * 60)

        total = self.wins + self.losses
        win_rate = (self.wins / total * 100) if total > 0 else 0

        logger.info(f"Total Trades: {total}")
        logger.info(f"Wins: {self.wins}")
        logger.info(f"Losses: {self.losses}")
        logger.info(f"Win Rate: {win_rate:.1f}%")
        logger.info(f"Total P&L: {self.total_pnl * 100:+.2f}%")

        # Open positions
        open_pos = [p for p in self.positions.values() if p.is_open]
        if open_pos:
            logger.info(f"\nOpen Positions: {len(open_pos)}")
            for pos in open_pos:
                logger.info(f"  {pos.ticker} {pos.side} @ ${pos.entry_price:.3f}")
                logger.info(
                    f"    Current: ${pos.current_price:.3f} | P&L: {pos.unrealized_pnl_pct * 100:+.2f}%"
                )
                logger.info(
                    f"    Edge: {pos.current_edge_cents:.1f}c (entry: {pos.entry_edge_cents:.1f}c)"
                )


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Live EV-Based Trading Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/live_underdog_scalp.py                    # Monitor mode (alerts only)
  python scripts/live_underdog_scalp.py --dry-run          # Dry run (simulated trades)
  python scripts/live_underdog_scalp.py --min-edge 5.0     # Conservative (5 cent edge)
  python scripts/live_underdog_scalp.py --exit-mode settlement  # Hold to settlement

NOTE: Live trading is DISABLED - this strategy is work in progress.

Presets:
  Conservative: --min-edge 5.0 --max-period 2 --exit-mode settlement
  Moderate:     --min-edge 3.0 --max-period 2 --exit-mode edge_exit
  Aggressive:   --min-edge 1.5 --max-period 2 --exit-mode edge_exit
        """,
    )

    # Mode selection
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run", action="store_true", help="Dry run mode (simulated trades)"
    )
    mode_group.add_argument(
        "--paper", action="store_true", help="Paper trading mode (same as dry-run)"
    )
    mode_group.add_argument(
        "--live", action="store_true", help="[DISABLED - WIP] Live trading mode"
    )

    # EV-based strategy parameters
    parser.add_argument(
        "--min-edge",
        type=float,
        default=3.0,
        help="Minimum edge in cents to enter (default: 3.0)",
    )
    parser.add_argument(
        "--max-period",
        type=int,
        default=2,
        help="Maximum period for entry, 1=Q1, 2=Q2 (default: 2)",
    )
    parser.add_argument(
        "--exit-mode",
        type=str,
        default="edge_exit",
        choices=["settlement", "edge_exit"],
        help="Exit mode: settlement (hold) or edge_exit (exit when edge disappears)",
    )
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=50,
        help="Hard stop loss %% (catastrophic only, default: 50)",
    )

    # Trading parameters
    parser.add_argument(
        "--position-size",
        type=float,
        default=50,
        help="Position size in USD (default: 50)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10,
        help="Polling interval in seconds (default: 10)",
    )

    # Misc
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine mode
    if args.live:
        # WORK IN PROGRESS - Live trading disabled until strategy is validated
        logger.error("=" * 60)
        logger.error("LIVE TRADING DISABLED - STRATEGY IS WORK IN PROGRESS")
        logger.error("This strategy needs further development before live use.")
        logger.error("Use --dry-run or --paper mode for testing.")
        logger.error("=" * 60)
        return
    elif args.dry_run:
        mode = TradingMode.DRY_RUN
    elif args.paper:
        mode = TradingMode.PAPER
    else:
        mode = TradingMode.MONITOR

    # Build config
    config = StrategyConfig(
        min_edge_cents=args.min_edge,
        max_period=args.max_period,
        exit_mode=args.exit_mode,
        stop_loss_pct=args.stop_loss / 100.0,
        position_size_usd=args.position_size,
        poll_interval_sec=args.poll_interval,
        verbose=args.verbose,
    )

    # Run engine
    engine = EVScalpEngine(config, mode)
    engine.run()


if __name__ == "__main__":
    main()
