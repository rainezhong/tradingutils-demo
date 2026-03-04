#!/usr/bin/env python3
"""
Live Sub-15c Settlement Trading Strategy - CLI Runner

UPDATED based on 250-game backtest (vs original 25-game sample):
- Original "uptick exit" strategy was OVERFIT to lucky sample
- Settlement strategy has HIGHER EV than uptick exits

Strategy Rules:
- Entry: PRE-GAME only, team priced < 15c (be selective!)
- Exit: HOLD TO SETTLEMENT (don't try to scalp - it has lower EV)

Backtest Results (250 games):
  Sub-15c Settlement: 18 trades, 22% win, +9.11c EV, 80% P(profitable)
  Sub-12c Settlement: 7 trades, 29% win, +18.00c EV, 90% P(profitable)

Why uptick exits don't work:
  5c exit:  +1.87c EV (vs +9.11c settlement)
  30c exit: +4.47c EV (vs +9.11c settlement)
  The big wins (85c+) at settlement are worth more than small scalps.

Usage:
    python scripts/live_sub20c_uptick.py                    # Monitor mode
    python scripts/live_sub20c_uptick.py --dry-run          # Dry run
    python scripts/live_sub20c_uptick.py --live             # REAL MONEY
    python scripts/live_sub20c_uptick.py --entry-threshold 12  # More selective
"""

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.live.nba.endpoints import scoreboard
from kalshi_utils.client_wrapper import KalshiWrapped
from kalshi_python_sync import OrdersApi

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
class Sub20cUptickConfig:
    """Configuration for the Sub-20c Uptick strategy."""

    # Entry conditions - BE SELECTIVE (key finding from 250-game backtest)
    entry_price_threshold: float = 0.15  # Enter if < 15c (was 20c - too loose)
    entry_window_min_before: int = 30  # Enter up to 30 min before tipoff
    entry_window_max_before: int = 5  # Stop entries 5 min before tipoff

    # Exit strategy - HOLD TO SETTLEMENT (uptick exits have lower EV!)
    # Backtest: Settlement EV = +9.11c, Uptick exit EV = +1.87c to +4.47c
    exit_mode: str = "settlement"  # "settlement" (recommended) or "uptick"
    min_uptick_cents: int = 30  # Only used if exit_mode="uptick"

    # Trading parameters
    position_size_usd: float = 50.0  # USD per position
    max_positions: int = 3  # Max concurrent positions
    one_entry_per_game: bool = True  # Only one entry per game

    # Polling
    poll_interval_sec: float = 10.0  # Check every 10s

    # Fees
    fee_rate: float = 0.07  # 7% fee on profit

    # Logging
    verbose: bool = False


class TradingMode(Enum):
    MONITOR = "monitor"  # Just watch and alert
    DRY_RUN = "dry_run"  # Simulated trading, no API order calls
    PAPER = "paper"  # Same as dry_run
    LIVE = "live"  # Real money trading


class GameStatus(Enum):
    SCHEDULED = "scheduled"
    PREGAME = "pregame"
    IN_PROGRESS = "in_progress"
    FINAL = "final"
    UNKNOWN = "unknown"


class ExitType(Enum):
    NONE = "none"
    UPTICK = "uptick"
    SETTLEMENT = "settlement"
    MANUAL = "manual"


# =============================================================================
# DATA MODELS
# =============================================================================
@dataclass
class ScheduledGame:
    """A scheduled/pregame NBA game."""

    game_id: str
    home_team: str  # Tricode e.g., "LAL"
    away_team: str  # Tricode e.g., "BOS"
    scheduled_start: datetime
    status: GameStatus
    matchup_key: str  # e.g., "BOSLAL" for Kalshi matching

    @property
    def minutes_until_start(self) -> float:
        """Minutes until scheduled start."""
        delta = self.scheduled_start - datetime.now()
        return delta.total_seconds() / 60


@dataclass
class LiveGame:
    """A live (in-progress) NBA game."""

    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    quarter: int
    clock: str
    status: str
    matchup_key: str


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
    def no_mid(self) -> float:
        """Midpoint for NO."""
        return (self.no_bid + self.no_ask) / 2 if self.no_bid and self.no_ask else 0.5


@dataclass
class Sub20cPosition:
    """Tracked trading position for Sub-20c strategy."""

    id: str
    event_ticker: str
    ticker: str
    side: str  # "YES" or "NO"
    team: str  # Team we're betting on
    entry_price: float
    entry_time: datetime
    quantity: int
    game_id: str
    matchup_key: str

    # Order tracking
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    # Exit tracking
    current_bid: float = 0.0
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_type: ExitType = ExitType.NONE
    pnl: float = 0.0

    @property
    def unrealized_gain_cents(self) -> float:
        """Unrealized gain in cents."""
        return (self.current_bid - self.entry_price) * 100

    @property
    def is_open(self) -> bool:
        return self.exit_type == ExitType.NONE


@dataclass
class EntrySignal:
    """A potential entry signal."""

    game: ScheduledGame
    market: KalshiMarketState
    side: str  # "YES" or "NO"
    team: str
    entry_price: float  # Ask price for entry


# =============================================================================
# NBA API CLIENT
# =============================================================================
class NBAScheduleClient:
    """Client for fetching NBA schedule and live game data."""

    def get_todays_games(self) -> Tuple[List[ScheduledGame], List[LiveGame]]:
        """Fetch today's NBA games - both scheduled and live."""
        scheduled = []
        live = []

        try:
            board = scoreboard.ScoreBoard()
            games_data = board.get_dict()["scoreboard"]["games"]

            for game in games_data:
                home = game["homeTeam"]["teamTricode"]
                away = game["awayTeam"]["teamTricode"]
                matchup_key = f"{away}{home}"

                # Parse scheduled start time
                game_time_str = game.get("gameTimeUTC", "")
                if game_time_str:
                    try:
                        scheduled_start = datetime.fromisoformat(
                            game_time_str.replace("Z", "+00:00")
                        )
                        scheduled_start = scheduled_start.replace(tzinfo=None)
                    except:
                        scheduled_start = datetime.now() + timedelta(hours=2)
                else:
                    scheduled_start = datetime.now() + timedelta(hours=2)

                game_status = game[
                    "gameStatus"
                ]  # 1 = Not Started, 2 = In Progress, 3 = Final

                if game_status == 1:
                    # Scheduled/Not Started
                    scheduled.append(
                        ScheduledGame(
                            game_id=game["gameId"],
                            home_team=home,
                            away_team=away,
                            scheduled_start=scheduled_start,
                            status=GameStatus.SCHEDULED,
                            matchup_key=matchup_key,
                        )
                    )
                elif game_status == 2:
                    # Live/In Progress
                    status_text = game.get("gameStatusText", "")
                    quarter = self._parse_quarter(status_text)
                    clock = self._parse_clock(status_text)

                    live.append(
                        LiveGame(
                            game_id=game["gameId"],
                            home_team=home,
                            away_team=away,
                            home_score=game["homeTeam"]["score"],
                            away_score=game["awayTeam"]["score"],
                            quarter=quarter,
                            clock=clock,
                            status=status_text,
                            matchup_key=matchup_key,
                        )
                    )

        except Exception as e:
            logger.error(f"Failed to fetch NBA games: {e}")

        return scheduled, live

    def _parse_quarter(self, status_text: str) -> int:
        """Parse quarter number from status text."""
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
            return 2
        return 0

    def _parse_clock(self, status_text: str) -> str:
        """Parse clock from status text."""
        import re

        match = re.search(r"(\d{1,2}:\d{2})", status_text)
        return match.group(1) if match else "0:00"


# =============================================================================
# KALSHI MARKET CLIENT
# =============================================================================
class KalshiLiveClient:
    """Client for Kalshi market operations."""

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
        """Get best bid/ask for a market ticker. Returns (yes_bid, yes_ask)."""
        if not self.wrapper:
            self.connect()

        try:
            client = self.wrapper.GetClient()
            ob = client.get_orderbook(ticker=ticker)

            yes_bid = max([l.price for l in ob.orderbook.yes], default=0) / 100.0
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
        action: str = "buy",
    ) -> Optional[str]:
        """Place an order on Kalshi."""
        if not self.wrapper:
            self.connect()

        try:
            client = self.wrapper.GetClient()
            orders_api = OrdersApi(client)

            price_cents = int(round(price * 100))
            price_cents = max(1, min(99, price_cents))

            order_kwargs = {
                "ticker": ticker,
                "side": side.lower(),
                "action": action.lower(),
                "count": quantity,
                "type": "limit",
            }

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
class Sub20cUptickEngine:
    """Main strategy engine for Sub-20c Uptick trading."""

    def __init__(self, config: Sub20cUptickConfig, mode: TradingMode):
        self.config = config
        self.mode = mode

        # Clients
        self.nba_client = NBAScheduleClient()
        self.kalshi_client = KalshiLiveClient()

        # State tracking
        self.positions: Dict[str, Sub20cPosition] = {}  # Keyed by ticker
        self.closed_positions: List[Sub20cPosition] = []
        self.entered_games: set = set()  # matchup_keys we've entered
        self.pending_entries: set = set()  # Race condition prevention

        # Stats
        self.total_trades = 0
        self.total_pnl = 0.0
        self.uptick_exits = 0
        self.settlement_exits = 0

    def is_entry_window(self, game: ScheduledGame) -> bool:
        """Check if game is in the entry window (30 to 5 min before tipoff)."""
        if game.status != GameStatus.SCHEDULED:
            return False

        mins = game.minutes_until_start
        return (
            self.config.entry_window_max_before
            <= mins
            <= self.config.entry_window_min_before
        )

    def find_entries(
        self, scheduled_games: List[ScheduledGame], markets: List[KalshiMarketState]
    ) -> List[EntrySignal]:
        """Find entry opportunities in pre-game markets."""
        signals = []
        cfg = self.config

        # Build market lookup by matchup
        market_by_matchup: Dict[str, List[KalshiMarketState]] = {}
        for market in markets:
            ticker_upper = market.ticker.upper()
            for game in scheduled_games:
                if game.home_team in ticker_upper and game.away_team in ticker_upper:
                    if game.matchup_key not in market_by_matchup:
                        market_by_matchup[game.matchup_key] = []
                    market_by_matchup[game.matchup_key].append(market)
                    break

        for game in scheduled_games:
            # Check entry window
            if not self.is_entry_window(game):
                continue

            # One entry per game
            if cfg.one_entry_per_game and game.matchup_key in self.entered_games:
                continue

            # Check max positions
            open_positions = len([p for p in self.positions.values() if p.is_open])
            if open_positions >= cfg.max_positions:
                continue

            # Check for pending entry
            if game.matchup_key in self.pending_entries:
                continue

            # Get markets for this game
            game_markets = market_by_matchup.get(game.matchup_key, [])
            if not game_markets:
                continue

            # Use first market (should only be one per game)
            market = game_markets[0]

            # Check YES side (typically home team)
            if market.yes_ask < cfg.entry_price_threshold:
                signals.append(
                    EntrySignal(
                        game=game,
                        market=market,
                        side="YES",
                        team=market.yes_team or game.home_team,
                        entry_price=market.yes_ask,
                    )
                )
            # Check NO side (away team)
            elif market.no_ask < cfg.entry_price_threshold:
                signals.append(
                    EntrySignal(
                        game=game,
                        market=market,
                        side="NO",
                        team=game.away_team,
                        entry_price=market.no_ask,
                    )
                )

        return signals

    def check_exit(self, pos: Sub20cPosition, current_bid: float) -> Optional[ExitType]:
        """Check if position should exit based on current bid price."""
        pos.current_bid = current_bid
        cfg = self.config

        gain_cents = (current_bid - pos.entry_price) * 100

        if gain_cents >= cfg.min_uptick_cents:
            return ExitType.UPTICK

        return None

    def match_markets_to_games(
        self, live_games: List[LiveGame], markets: List[KalshiMarketState]
    ) -> Dict[str, Tuple[LiveGame, KalshiMarketState]]:
        """Match live games to their markets."""
        matched = {}

        for game in live_games:
            for market in markets:
                ticker_upper = market.ticker.upper()
                if game.home_team in ticker_upper and game.away_team in ticker_upper:
                    matched[game.matchup_key] = (game, market)
                    break

        return matched

    def execute_entry(self, signal: EntrySignal) -> Optional[Sub20cPosition]:
        """Execute an entry trade."""
        cfg = self.config
        game = signal.game

        # Safety check
        if game.matchup_key in self.pending_entries:
            return None
        if cfg.one_entry_per_game and game.matchup_key in self.entered_games:
            return None

        # Mark as pending
        self.pending_entries.add(game.matchup_key)

        try:
            return self._do_execute_entry(signal, cfg)
        finally:
            self.pending_entries.discard(game.matchup_key)

    def _do_execute_entry(
        self, signal: EntrySignal, cfg: Sub20cUptickConfig
    ) -> Optional[Sub20cPosition]:
        """Internal entry execution."""
        fill_price = signal.entry_price

        if fill_price <= 0 or fill_price >= 1:
            logger.warning(
                f"Invalid fill price {fill_price:.3f} for {signal.market.ticker}"
            )
            return None

        quantity = int(cfg.position_size_usd / fill_price) if fill_price > 0 else 0
        if quantity <= 0:
            logger.warning(f"Calculated 0 contracts for {signal.market.ticker}")
            return None

        pos_id = f"{signal.market.ticker}_{int(time.time())}"

        pos = Sub20cPosition(
            id=pos_id,
            event_ticker=signal.market.event_ticker,
            ticker=signal.market.ticker,
            side=signal.side,
            team=signal.team,
            entry_price=fill_price,
            entry_time=datetime.now(),
            quantity=quantity,
            game_id=signal.game.game_id,
            matchup_key=signal.game.matchup_key,
            current_bid=fill_price,
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
        self.entered_games.add(signal.game.matchup_key)
        self.total_trades += 1

        return pos

    def execute_exit(self, pos: Sub20cPosition, exit_type: ExitType) -> bool:
        """Execute an exit trade."""
        cfg = self.config

        # Calculate exit price
        if exit_type == ExitType.UPTICK:
            exit_price = pos.entry_price + (cfg.min_uptick_cents / 100)
        else:
            # Settlement - will be resolved by Kalshi
            exit_price = pos.current_bid

        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.exit_type = exit_type

        # Calculate P&L
        pnl_pct = (
            (exit_price - pos.entry_price) / pos.entry_price
            if pos.entry_price > 0
            else 0
        )
        if pnl_pct > 0:
            pnl_pct *= 1 - cfg.fee_rate
        pos.pnl = pnl_pct

        if self.mode == TradingMode.LIVE and exit_type == ExitType.UPTICK:
            order_id = self.kalshi_client.place_order(
                ticker=pos.ticker,
                side=pos.side,
                price=exit_price,
                quantity=pos.quantity,
                action="sell",
            )

            if order_id:
                pos.exit_order_id = order_id
            else:
                logger.error(f"Failed to place exit order for {pos.ticker}")
                return False

        elif self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]:
            logger.debug(f"[DRY RUN] Simulated exit: {pos.side} @ ${exit_price:.3f}")

        # Remove from active positions
        if pos.ticker in self.positions:
            del self.positions[pos.ticker]

        self.closed_positions.append(pos)
        self.total_pnl += pos.pnl

        if exit_type == ExitType.UPTICK:
            self.uptick_exits += 1
        else:
            self.settlement_exits += 1

        return True

    def run(self):
        """Main run loop."""
        logger.info("=" * 60)
        logger.info("SUB-20c UPTICK STRATEGY - LIVE MONITOR")
        logger.info("=" * 60)
        logger.info(f"Mode: {self.mode.value.upper()}")
        logger.info("Config:")
        logger.info(
            f"  Entry threshold: {self.config.entry_price_threshold * 100:.0f}c"
        )
        logger.info(f"  Min uptick exit: {self.config.min_uptick_cents}c")
        logger.info(
            f"  Entry window: {self.config.entry_window_min_before} to {self.config.entry_window_max_before} min before tipoff"
        )
        logger.info(f"  Position size: ${self.config.position_size_usd:.0f}")
        logger.info(f"  Max positions: {self.config.max_positions}")
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
        # Fetch game data
        try:
            scheduled_games, live_games = self.nba_client.get_todays_games()
        except Exception as e:
            logger.error(f"Failed to fetch NBA games: {e}")
            scheduled_games, live_games = [], []

        # Fetch market data
        try:
            markets = self.kalshi_client.get_nba_markets()
        except Exception as e:
            logger.error(f"Failed to fetch Kalshi markets: {e}")
            markets = []

        if self.config.verbose:
            logger.info(f"\n{'─' * 50}")
            logger.info(f"Polling at {datetime.now().strftime('%H:%M:%S')}")
            logger.info(
                f"   Scheduled: {len(scheduled_games)}, Live: {len(live_games)}, Markets: {len(markets)}"
            )

        # Log games in entry window
        if self.config.verbose:
            for game in scheduled_games:
                if self.is_entry_window(game):
                    logger.info(
                        f"   [ENTRY WINDOW] {game.away_team} @ {game.home_team} "
                        f"(starts in {game.minutes_until_start:.0f} min)"
                    )

        # 1. Check for exits on live games (positions we hold)
        if self.positions:
            matched = self.match_markets_to_games(live_games, markets)

            for pos_ticker, pos in list(self.positions.items()):
                if not pos.is_open:
                    continue

                # Find if game has started
                game_match = matched.get(pos.matchup_key)
                if game_match:
                    live_game, market = game_match

                    # Get current bid for our side
                    if pos.side == "YES":
                        current_bid = market.yes_bid
                    else:
                        current_bid = market.no_bid

                    exit_type = self.check_exit(pos, current_bid)

                    if exit_type:
                        self._log_exit(pos, exit_type, live_game)
                        self.execute_exit(pos, exit_type)

        # 2. Find new entries (pre-game)
        try:
            signals = self.find_entries(scheduled_games, markets)
        except Exception as e:
            logger.error(f"Error finding entries: {e}")
            signals = []

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

    def _log_signal(self, signal: EntrySignal):
        """Log a trading signal."""
        logger.info("")
        logger.info("SIGNAL DETECTED")
        logger.info(f"   Game: {signal.game.away_team} @ {signal.game.home_team}")
        logger.info(f"   Starts in: {signal.game.minutes_until_start:.0f} min")
        logger.info(f"   Market: {signal.market.ticker}")
        logger.info(f"   Side: BUY {signal.side} ({signal.team})")
        logger.info(f"   Entry Price: {signal.entry_price * 100:.0f}c")

    def _log_entry(self, pos: Sub20cPosition, signal: EntrySignal):
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
        logger.info(
            f"   Target exit: ${pos.entry_price + self.config.min_uptick_cents / 100:.3f} ({self.config.min_uptick_cents}c uptick)"
        )

    def _log_exit(self, pos: Sub20cPosition, exit_type: ExitType, game: LiveGame):
        """Log a trade exit."""
        exit_reason = exit_type.value.title()
        pos.pnl * 100
        mode_tag = (
            "[DRY RUN] "
            if self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]
            else ""
        )

        logger.info("")
        logger.info(f"{mode_tag}EXITING POSITION ({exit_reason})")
        logger.info(
            f"   Game: Q{game.quarter} {game.clock} - {game.away_team} {game.away_score} @ {game.home_team} {game.home_score}"
        )
        logger.info(f"   {pos.side} @ ${pos.entry_price:.3f} -> ${pos.current_bid:.3f}")
        logger.info(f"   Gain: {pos.unrealized_gain_cents:.1f}c")

    def _print_summary(self):
        """Print session summary."""
        logger.info("\n" + "=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info("=" * 60)

        total = self.uptick_exits + self.settlement_exits
        uptick_rate = (self.uptick_exits / total * 100) if total > 0 else 0

        logger.info(f"Total Trades: {self.total_trades}")
        logger.info(f"Uptick Exits: {self.uptick_exits}")
        logger.info(f"Settlement Exits: {self.settlement_exits}")
        logger.info(f"Uptick Rate: {uptick_rate:.1f}%")
        logger.info(f"Total P&L: {self.total_pnl * 100:+.2f}%")

        # Open positions
        open_pos = [p for p in self.positions.values() if p.is_open]
        if open_pos:
            logger.info(f"\nOpen Positions: {len(open_pos)}")
            for pos in open_pos:
                logger.info(
                    f"  {pos.ticker} {pos.side} ({pos.team}) @ ${pos.entry_price:.3f}"
                )
                logger.info(
                    f"    Current bid: ${pos.current_bid:.3f} | Gain: {pos.unrealized_gain_cents:.1f}c"
                )


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Live Sub-20c Uptick Trading Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/live_sub20c_uptick.py                    # Monitor mode (alerts only)
  python scripts/live_sub20c_uptick.py --dry-run          # Dry run (simulated trades)
  python scripts/live_sub20c_uptick.py --live             # REAL MONEY (be careful!)
  python scripts/live_sub20c_uptick.py --entry-threshold 15  # 15c max entry
  python scripts/live_sub20c_uptick.py --min-uptick 3     # Exit on 3c gain
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
        "--live", action="store_true", help="Live trading mode (REAL MONEY)"
    )

    # Strategy parameters
    parser.add_argument(
        "--entry-threshold",
        type=int,
        default=15,
        help="Max entry price in cents (default: 15, use 12 for more selective)",
    )
    parser.add_argument(
        "--min-uptick",
        type=int,
        default=30,
        help="Cents gain to trigger exit if using uptick mode (default: 30)",
    )
    parser.add_argument(
        "--entry-window",
        type=str,
        default="30-5",
        help="Minutes before tipoff to enter: MIN-MAX (default: 30-5)",
    )

    # Trading parameters
    parser.add_argument(
        "--position-size",
        type=float,
        default=50,
        help="Position size in USD (default: 50)",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=3,
        help="Max concurrent positions (default: 3)",
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

    # Parse entry window
    try:
        window_parts = args.entry_window.split("-")
        entry_min_before = int(window_parts[0])
        entry_max_before = int(window_parts[1])
    except:
        entry_min_before = 30
        entry_max_before = 5

    # Determine mode
    if args.live:
        mode = TradingMode.LIVE
        logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            logger.info("Aborted.")
            return
    elif args.dry_run:
        mode = TradingMode.DRY_RUN
    elif args.paper:
        mode = TradingMode.PAPER
    else:
        mode = TradingMode.MONITOR

    # Build config
    config = Sub20cUptickConfig(
        entry_price_threshold=args.entry_threshold / 100.0,
        min_uptick_cents=args.min_uptick,
        entry_window_min_before=entry_min_before,
        entry_window_max_before=entry_max_before,
        position_size_usd=args.position_size,
        max_positions=args.max_positions,
        poll_interval_sec=args.poll_interval,
        verbose=args.verbose,
    )

    # Run engine
    engine = Sub20cUptickEngine(config, mode)
    engine.run()


if __name__ == "__main__":
    main()
