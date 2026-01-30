#!/usr/bin/env python3
"""
Live Underdog Scalp Strategy - CLI Runner

Strategy Rules:
- Entry: 1st or 2nd quarter of NBA game
- Close game: Point differential below threshold OR underdog price >= close_threshold
- Side: Buy underdog (price in [0, 0.5])
- Stop Loss: -5% P&L
- Take Profit: +5.5% P&L (after ~7% fees)

Usage:
    python scripts/live_underdog_scalp.py                    # Monitor mode (just watch)
    python scripts/live_underdog_scalp.py --dry-run          # Dry run (simulated trades, no API calls)
    python scripts/live_underdog_scalp.py --paper            # Paper trading (simulated)
    python scripts/live_underdog_scalp.py --live             # Live trading (REAL MONEY)
    python scripts/live_underdog_scalp.py --close-thresh 45  # Set close threshold to 45%
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.live.nba.endpoints import scoreboard, boxscore
from kalshi_utils.client_wrapper import KalshiWrapped
from kalshi_python_sync import CreateOrderRequest, OrdersApi

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# =============================================================================
# STRATEGY CONFIGURATION
# =============================================================================
@dataclass
class StrategyConfig:
    """Configuration for the underdog scalp strategy."""
    
    # Close game detection (price threshold scales with game progress)
    # threshold = early + (late - early) * x^2 where x = game_progress [0,1]
    close_threshold_price_early: float = 0.20   # Q1: accept underdogs as low as 20%
    close_threshold_price_late: float = 0.45    # Q4: need underdogs >= 45%
    close_threshold_score: float = 0.15  # Score ratio threshold: 1 - |underdog_score/favorite_score| <= 0.15 = close
    
    # Entry conditions
    allowed_quarters: List[int] = field(default_factory=lambda: [1, 2, 3, 4])
    min_underdog_price: float = 0.01     # Don't buy underdogs below 1%
    max_underdog_price: float = 0.50     # By definition, underdog <= 50%
    
    # Exit conditions
    stop_loss_pct: float = 0.05          # -5% P&L
    take_profit_pct: float = 0.055       # +5.5% P&L (net of fees)
    
    # Kalshi fee
    fee_rate: float = 0.07               # 7% fee on profit
    
    # Trading parameters
    position_size_usd: float = 50.0      # $ per trade
    max_positions: int = 5               # Max concurrent positions
    
    # Polling intervals
    poll_interval_sec: float = 10.0      # How often to check markets
    
    # Logging
    verbose: bool = False                # Verbose output about games being checked


class TradingMode(Enum):
    MONITOR = "monitor"   # Just watch and alert
    DRY_RUN = "dry_run"   # Simulated trading, no API order calls
    PAPER = "paper"       # Simulated trading (same as dry_run)
    LIVE = "live"         # Real money trading


class ExitType(Enum):
    NONE = "none"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    MANUAL = "manual"
    SETTLEMENT = "settlement"


# =============================================================================
# DATA MODELS
# =============================================================================
@dataclass
class NBAGameState:
    """Live NBA game state from NBA API."""
    game_id: str
    home_team: str          # Tricode e.g., "LAL"
    away_team: str          # Tricode e.g., "BOS"
    home_score: int
    away_score: int
    quarter: int            # 1, 2, 3, 4, 5+ for OT
    clock: str              # e.g., "5:32"
    status: str             # "In Progress", "Final", etc.
    matchup_key: str        # e.g., "BOSLAL" for Kalshi matching
    
    @property
    def point_diff(self) -> int:
        """Absolute point differential."""
        return abs(self.home_score - self.away_score)
    
    @property
    def leading_team(self) -> str:
        """Which team is leading."""
        if self.home_score > self.away_score:
            return self.home_team
        elif self.away_score > self.home_score:
            return self.away_team
        return "TIED"
    
    @property
    def favorite_score(self) -> int:
        """Score of the leading team."""
        return max(self.home_score, self.away_score)
    
    @property
    def underdog_score(self) -> int:
        """Score of the trailing team."""
        return min(self.home_score, self.away_score)
    
    @property
    def closeness(self) -> float:
        """Game closeness as 1 - |underdog_score / favorite_score|.
        
        Returns a value between 0 and 1:
        - 0.0 = tied game (perfectly close)
        - 0.1 = 90% score ratio (very close, e.g., 45-50)
        - 0.5 = 50% score ratio (blowout, e.g., 25-50)
        - 1.0 = shutout (underdog has 0 points)
        
        Lower values = closer games.
        """
        if self.favorite_score == 0:
            return 0.0  # Both teams at 0, tied
        return 1 - (self.underdog_score / self.favorite_score)
    
    @property
    def is_close(self) -> bool:
        """Is this a close game? (closeness <= 0.15)"""
        return self.closeness <= 0.15
    
    @property
    def game_progress(self) -> float:
        """Game progress as fraction [0, 1] where 0=start, 1=end.
        
        Q1 start = 0.0, Q2 = 0.25, Q3 = 0.5, Q4 end = 1.0
        """
        # Each quarter is 12 min, game is 48 min total
        # quarter 1 = 0-0.25, quarter 2 = 0.25-0.5, etc.
        base = (self.quarter - 1) * 0.25
        # Could refine with clock, but quarter is sufficient
        return min(1.0, base + 0.125)  # Midpoint of quarter


@dataclass
class KalshiMarketState:
    """Live Kalshi market state."""
    ticker: str
    event_ticker: str
    yes_team: str           # Team for YES side
    yes_bid: float          # Best bid for YES
    yes_ask: float          # Best ask for YES
    no_bid: float           # Best bid for NO
    no_ask: float           # Best ask for NO
    volume: int
    
    @property
    def yes_mid(self) -> float:
        """Midpoint for YES."""
        return (self.yes_bid + self.yes_ask) / 2 if self.yes_bid and self.yes_ask else 0.5
    
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
    side: str               # "YES" or "NO"
    entry_price: float
    entry_time: datetime
    quantity: int
    game_id: str
    
    # Order tracking
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    
    # Exit tracking
    current_price: float = 0.0
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
            games_data = board.get_dict()['scoreboard']['games']
            
            live_games = []
            for game in games_data:
                # gameStatus: 1 = Not Started, 2 = In Progress, 3 = Final
                if game['gameStatus'] != 2:
                    continue
                
                # Parse quarter from gameStatusText (e.g., "Q2 5:32")
                status_text = game.get('gameStatusText', '')
                quarter = self._parse_quarter(status_text)
                clock = self._parse_clock(status_text)
                
                home = game['homeTeam']['teamTricode']
                away = game['awayTeam']['teamTricode']
                
                live_games.append(NBAGameState(
                    game_id=game['gameId'],
                    home_team=home,
                    away_team=away,
                    home_score=game['homeTeam']['score'],
                    away_score=game['awayTeam']['score'],
                    quarter=quarter,
                    clock=clock,
                    status=status_text,
                    matchup_key=f"{away}{home}",  # Kalshi format: AWYHOM
                ))
            
            return live_games
            
        except Exception as e:
            logger.error(f"Failed to fetch NBA games: {e}")
            return []
    
    def _parse_quarter(self, status_text: str) -> int:
        """Parse quarter number from status text like 'Q2 5:32'."""
        if not status_text:
            return 0
        
        status_text = status_text.upper()
        if 'Q1' in status_text or '1ST' in status_text:
            return 1
        elif 'Q2' in status_text or '2ND' in status_text:
            return 2
        elif 'Q3' in status_text or '3RD' in status_text:
            return 3
        elif 'Q4' in status_text or '4TH' in status_text:
            return 4
        elif 'OT' in status_text:
            return 5
        elif 'HALF' in status_text:
            return 2  # Halftime = still Q2 essentially
        return 0
    
    def _parse_clock(self, status_text: str) -> str:
        """Parse clock from status text."""
        import re
        match = re.search(r'(\d{1,2}:\d{2})', status_text)
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
                yes_title = data.get('yes_sub_title', '')
                yes_team = yes_title.split()[0] if yes_title else ''
                
                # Extract prices (convert from cents)
                yes_bid = (data.get('yes_bid', 0) or 0) / 100.0
                yes_ask = (data.get('yes_ask', 100) or 100) / 100.0
                no_bid = (data.get('no_bid', 0) or 0) / 100.0
                no_ask = (data.get('no_ask', 100) or 100) / 100.0
                
                markets.append(KalshiMarketState(
                    ticker=data['ticker'],
                    event_ticker=data['event_ticker'],
                    yes_team=yes_team,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=no_bid,
                    no_ask=no_ask,
                    volume=data.get('volume', 0),
                ))
            
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
        """Place an order on Kalshi.
        
        Args:
            ticker: Market ticker (e.g., KXNBAGAME-26JAN28-BOS-LAL)
            side: "YES" or "NO" - which side to trade
            price: Price as decimal (0.01 to 0.99)
            quantity: Number of contracts
            action: "buy" or "sell"
        
        Returns:
            Order ID if successful, None otherwise
        """
        if not self.wrapper:
            self.connect()
        
        try:
            client = self.wrapper.GetClient()
            orders_api = OrdersApi(client)
            
            # Convert price to cents (1-99)
            price_cents = int(round(price * 100))
            price_cents = max(1, min(99, price_cents))
            
            # Build order kwargs - API expects fields directly, not wrapped in CreateOrderRequest
            order_kwargs = {
                "ticker": ticker,
                "side": side.lower(),  # "yes" or "no"
                "action": action.lower(),  # "buy" or "sell"
                "count": quantity,
                "type": "limit",
            }
            
            # Set price based on side
            if side.upper() == "YES":
                order_kwargs["yes_price"] = price_cents
            else:
                order_kwargs["no_price"] = price_cents
            
            logger.info(f"📤 Placing order: {action.upper()} {quantity} {side} @ ${price:.2f}")
            
            response = orders_api.create_order(**order_kwargs)
            order_id = response.order.order_id
            
            logger.info(f"✅ Order placed! ID: {order_id}")
            return order_id
            
        except Exception as e:
            logger.error(f"❌ Failed to place order: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        if not self.wrapper:
            self.connect()
        
        try:
            client = self.wrapper.GetClient()
            orders_api = OrdersApi(client)
            orders_api.cancel_order(order_id)
            logger.info(f"🗑️ Cancelled order: {order_id}")
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
                'order_id': order.order_id,
                'status': order.status,
                'filled_count': order.filled_count,
                'remaining_count': order.remaining_count,
            }
        except Exception as e:
            logger.error(f"Failed to get order status {order_id}: {e}")
            return None


# =============================================================================
# STRATEGY ENGINE
# =============================================================================
@dataclass
class TradeSignal:
    """A potential trade signal."""
    game: NBAGameState
    market: KalshiMarketState
    side: str                # "YES" or "NO"
    underdog_team: str
    underdog_price: float
    signal_reason: str


class UnderdogScalpEngine:
    """Main strategy engine for underdog scalping."""
    
    def __init__(self, config: StrategyConfig, mode: TradingMode):
        self.config = config
        self.mode = mode
        
        # Clients
        self.nba_client = NBALiveClient()
        self.kalshi_client = KalshiLiveClient()
        
        # State tracking
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Position] = []
        self.signals_seen: Dict[str, datetime] = {}  # Avoid duplicate alerts
        
        # Stats
        self.total_trades = 0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
    
    def match_games_to_markets(
        self,
        games: List[NBAGameState],
        markets: List[KalshiMarketState]
    ) -> List[Tuple[NBAGameState, List[KalshiMarketState]]]:
        """Match NBA games to their Kalshi markets."""
        matched = []
        
        for game in games:
            # Kalshi tickers often contain team tricodes
            game_markets = []
            for market in markets:
                ticker_upper = market.ticker.upper()
                event_upper = market.event_ticker.upper()
                
                # Check if both teams appear in the ticker/event
                if (game.home_team in ticker_upper or game.home_team in event_upper) and \
                   (game.away_team in ticker_upper or game.away_team in event_upper):
                    game_markets.append(market)
                # Also check matchup key
                elif game.matchup_key in ticker_upper or game.matchup_key in event_upper:
                    game_markets.append(market)
            
            if game_markets:
                matched.append((game, game_markets))
        
        return matched
    
    def get_dynamic_price_threshold(self, game: NBAGameState) -> float:
        """Compute dynamic price threshold based on game progress.
        
        Formula: threshold = early + (late - early) * x^2
        where x = game_progress [0, 1]
        
        This makes early game gaps more acceptable (lower threshold)
        and late game requires tighter games (higher threshold).
        """
        x = game.game_progress
        early = self.config.close_threshold_price_early
        late = self.config.close_threshold_price_late
        return early + (late - early) * (x ** 2)
    
    def detect_signals(
        self,
        game: NBAGameState,
        markets: List[KalshiMarketState]
    ) -> List[TradeSignal]:
        """Detect trading signals for a game."""
        signals = []
        cfg = self.config
        
        # Check quarter constraint
        if game.quarter not in cfg.allowed_quarters:
            return signals
        
        # Get dynamic price threshold based on game progress
        dynamic_price_threshold = self.get_dynamic_price_threshold(game)
        
        for market in markets:
            # Determine underdog from market prices
            yes_mid = market.yes_mid
            
            if yes_mid <= 0.5:
                underdog_side = "YES"
                underdog_price = yes_mid
                underdog_team = market.yes_team
            else:
                underdog_side = "NO"
                underdog_price = 1 - yes_mid
                underdog_team = f"Not {market.yes_team}"
            
            # Check if game is close
            # Score ratio: 1 - |underdog_score / favorite_score| <= threshold
            is_close_by_score = game.closeness <= cfg.close_threshold_score
            is_close_by_price = underdog_price >= dynamic_price_threshold
            
            if not (is_close_by_score or is_close_by_price):
                continue
            
            # Check price bounds
            if underdog_price < cfg.min_underdog_price:
                continue
            if underdog_price > cfg.max_underdog_price:
                continue
            
            # Check if we already have a position
            if market.event_ticker in self.positions:
                continue
            
            # Check max positions
            if len([p for p in self.positions.values() if p.is_open]) >= cfg.max_positions:
                continue
            
            # Build signal reason
            reason_parts = []
            if is_close_by_score:
                reason_parts.append(f"Score: {game.away_score}-{game.home_score} (closeness={game.closeness:.1%})")
            if is_close_by_price:
                reason_parts.append(f"Underdog @{underdog_price*100:.1f}% (thresh={dynamic_price_threshold*100:.0f}%)")
            reason_parts.append(f"Q{game.quarter} {game.clock}")
            
            signals.append(TradeSignal(
                game=game,
                market=market,
                side=underdog_side,
                underdog_team=underdog_team,
                underdog_price=underdog_price,
                signal_reason=" | ".join(reason_parts),
            ))
        
        return signals
    
    def check_exits(self) -> List[Position]:
        """Check if any positions should be exited."""
        exits = []
        cfg = self.config
        
        for pos_id, pos in list(self.positions.items()):
            if not pos.is_open:
                continue
            
            # Calculate current P&L
            pnl_pct = pos.unrealized_pnl_pct
            
            # Apply fee on profit only for comparison
            if pnl_pct > 0:
                net_pnl = pnl_pct * (1 - cfg.fee_rate)
            else:
                net_pnl = pnl_pct
            
            # Check stop loss
            if pnl_pct <= -cfg.stop_loss_pct:
                pos.exit_type = ExitType.STOP_LOSS
                pos.exit_price = pos.current_price
                pos.exit_time = datetime.now()
                pos.pnl = net_pnl
                exits.append(pos)
                continue
            
            # Check take profit
            if net_pnl >= cfg.take_profit_pct:
                pos.exit_type = ExitType.TAKE_PROFIT
                pos.exit_price = pos.current_price
                pos.exit_time = datetime.now()
                pos.pnl = net_pnl
                exits.append(pos)
                continue
        
        return exits
    
    def execute_entry(self, signal: TradeSignal) -> Optional[Position]:
        """Execute an entry trade."""
        cfg = self.config
        
        # Calculate quantity (in contracts)
        price = signal.underdog_price
        quantity = int(cfg.position_size_usd / price) if price > 0 else 0
        
        if quantity <= 0:
            logger.warning(f"Calculated 0 contracts for {signal.market.ticker}")
            return None
        
        pos_id = f"{signal.market.event_ticker}_{int(time.time())}"
        
        pos = Position(
            id=pos_id,
            event_ticker=signal.market.event_ticker,
            ticker=signal.market.ticker,
            side=signal.side,
            entry_price=price,
            entry_time=datetime.now(),
            quantity=quantity,
            game_id=signal.game.game_id,
            current_price=price,
        )
        
        if self.mode == TradingMode.LIVE:
            # Place real order via Kalshi API
            # Use the ask price to ensure fill (market taking)
            if signal.side == "YES":
                fill_price = signal.market.yes_ask
            else:
                fill_price = signal.market.no_ask
            
            order_id = self.kalshi_client.place_order(
                ticker=signal.market.ticker,
                side=signal.side,
                price=fill_price,
                quantity=quantity,
                action="buy",
            )
            
            if order_id:
                pos.entry_order_id = order_id
                pos.entry_price = fill_price  # Update to actual fill price
            else:
                logger.error(f"Failed to place entry order for {signal.market.ticker}")
                return None
                
        elif self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER]:
            logger.debug(f"[DRY RUN] Would place order: {signal.side} {quantity} @ ${price:.3f}")
        
        self.positions[signal.market.event_ticker] = pos
        self.total_trades += 1
        
        return pos
    
    def execute_exit(self, pos: Position) -> bool:
        """Execute an exit trade."""
        if self.mode == TradingMode.LIVE:
            # Place real sell order via Kalshi API
            # Use the bid price to ensure fill (market taking)
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
            logger.debug(f"[DRY RUN] Would exit: {pos.side} @ ${pos.exit_price:.3f}")
        
        # Move to closed positions
        if pos.event_ticker in self.positions:
            del self.positions[pos.event_ticker]
        
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
        logger.info("UNDERDOG SCALP STRATEGY - LIVE MONITOR")
        logger.info("=" * 60)
        logger.info(f"Mode: {self.mode.value.upper()}")
        logger.info(f"Config:")
        logger.info(f"  Close threshold (price): {self.config.close_threshold_price_early*100:.0f}% (Q1) → {self.config.close_threshold_price_late*100:.0f}% (Q4) [quadratic]")
        logger.info(f"  Close threshold (score): {self.config.close_threshold_score*100:.0f}% (1 - underdog/favorite)")
        logger.info(f"  Allowed quarters: {self.config.allowed_quarters}")
        logger.info(f"  Stop loss: -{self.config.stop_loss_pct*100:.0f}%")
        logger.info(f"  Take profit: +{self.config.take_profit_pct*100:.1f}%")
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
        # Fetch live data
        games = self.nba_client.get_live_games()
        markets = self.kalshi_client.get_nba_markets()
        
        if self.config.verbose:
            logger.info(f"\n{'─'*50}")
            logger.info(f"📡 Polling at {datetime.now().strftime('%H:%M:%S')}")
            logger.info(f"   Found {len(games)} live NBA games, {len(markets)} Kalshi markets")
        
        if not games:
            if self.config.verbose:
                logger.info("   ⚪ No live NBA games right now")
            return
        
        # Log each game being checked
        if self.config.verbose:
            for game in games:
                close_tag = "🔥 CLOSE" if game.closeness <= self.config.close_threshold_score else ""
                q_ok = "✓" if game.quarter in self.config.allowed_quarters else "✗"
                logger.info(f"   [{q_ok} Q{game.quarter}] {game.away_team} {game.away_score} @ {game.home_team} {game.home_score} (closeness={game.closeness:.1%}) {close_tag}")
        
        # Match games to markets
        matched = self.match_games_to_markets(games, markets)
        
        if not matched:
            if self.config.verbose:
                logger.info(f"   ⚠️  No matching Kalshi markets found for {len(games)} games")
            return
        
        if self.config.verbose:
            logger.info(f"   Matched {len(matched)} games to Kalshi markets")
        
        # Update positions with current prices
        self._update_positions(markets)
        
        # Check for exits
        exits = self.check_exits()
        for pos in exits:
            self._log_exit(pos)
            self.execute_exit(pos)
        
        # Detect and process signals
        for game, game_markets in matched:
            signals = self.detect_signals(game, game_markets)
            
            if self.config.verbose and not signals:
                # Log why no signal
                dyn_thresh = self.get_dynamic_price_threshold(game)
                for mkt in game_markets:
                    underdog_price = min(mkt.yes_mid, 1 - mkt.yes_mid)
                    reasons = []
                    if game.quarter not in self.config.allowed_quarters:
                        reasons.append(f"Q{game.quarter} not allowed")
                    if underdog_price < dyn_thresh and game.closeness > self.config.close_threshold_score:
                        reasons.append(f"Not close (price={underdog_price*100:.0f}%<{dyn_thresh*100:.0f}%, closeness={game.closeness:.1%})")
                    if underdog_price < self.config.min_underdog_price:
                        reasons.append(f"Underdog too low ({underdog_price*100:.0f}%)")
                    if mkt.event_ticker in self.positions:
                        reasons.append("Already have position")
                    if reasons:
                        logger.info(f"   ⏭️  {game.away_team}@{game.home_team}: No signal - {', '.join(reasons)}")
            
            for signal in signals:
                self._log_signal(signal)
                
                if self.mode in [TradingMode.PAPER, TradingMode.LIVE, TradingMode.DRY_RUN]:
                    pos = self.execute_entry(signal)
                    if pos:
                        self._log_entry(pos, signal)
    
    def _update_positions(self, markets: List[KalshiMarketState]):
        """Update current prices for all open positions."""
        market_map = {m.event_ticker: m for m in markets}
        
        for pos in self.positions.values():
            if not pos.is_open:
                continue
            
            market = market_map.get(pos.event_ticker)
            if market:
                if pos.side == "YES":
                    pos.current_price = market.yes_mid
                else:
                    pos.current_price = 1 - market.yes_mid
    
    def _log_signal(self, signal: TradeSignal):
        """Log a trading signal."""
        # Avoid duplicate alerts within 60 seconds
        key = f"{signal.market.event_ticker}_{signal.side}"
        if key in self.signals_seen:
            if (datetime.now() - self.signals_seen[key]).seconds < 60:
                return
        self.signals_seen[key] = datetime.now()
        
        logger.info("")
        logger.info("🎯 SIGNAL DETECTED")
        logger.info(f"   Game: {signal.game.away_team} @ {signal.game.home_team}")
        logger.info(f"   Score: {signal.game.away_score} - {signal.game.home_score}")
        logger.info(f"   Market: {signal.market.ticker}")
        logger.info(f"   Side: BUY {signal.side} (Underdog: {signal.underdog_team})")
        logger.info(f"   Price: ${signal.underdog_price:.3f}")
        logger.info(f"   Reason: {signal.signal_reason}")
    
    def _log_entry(self, pos: Position, signal: TradeSignal):
        """Log a trade entry."""
        mode_tag = '[DRY RUN] ' if self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER] else ''
        logger.info("")
        logger.info(f"✅ {mode_tag}ENTERED POSITION")
        logger.info(f"   {pos.side} {pos.quantity} contracts @ ${pos.entry_price:.3f}")
        logger.info(f"   Cost: ${pos.quantity * pos.entry_price:.2f}")
        logger.info(f"   Stop: ${pos.entry_price * (1 - self.config.stop_loss_pct):.3f}")
        logger.info(f"   Target: ${pos.entry_price * (1 + self.config.take_profit_pct / (1 - self.config.fee_rate)):.3f}")
    
    def _log_exit(self, pos: Position):
        """Log a trade exit."""
        icon = "🛑" if pos.exit_type == ExitType.STOP_LOSS else "🎉"
        color_pnl = f"{pos.pnl*100:+.2f}%"
        mode_tag = '[DRY RUN] ' if self.mode in [TradingMode.DRY_RUN, TradingMode.PAPER] else ''
        
        logger.info("")
        logger.info(f"{icon} {mode_tag}EXITED POSITION")
        logger.info(f"   {pos.side} @ ${pos.entry_price:.3f} → ${pos.exit_price:.3f}")
        logger.info(f"   Exit Type: {pos.exit_type.value}")
        logger.info(f"   P&L: {color_pnl}")
    
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
        logger.info(f"Total P&L: {self.total_pnl*100:+.2f}%")
        
        # Open positions
        open_pos = [p for p in self.positions.values() if p.is_open]
        if open_pos:
            logger.info(f"\nOpen Positions: {len(open_pos)}")
            for pos in open_pos:
                logger.info(f"  {pos.ticker} {pos.side} @ ${pos.entry_price:.3f} (current: ${pos.current_price:.3f}, P&L: {pos.unrealized_pnl_pct*100:+.2f}%)")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Live Underdog Scalp Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/live_underdog_scalp.py                    # Monitor mode (alerts only)
  python scripts/live_underdog_scalp.py --dry-run          # Dry run (simulated trades)
  python scripts/live_underdog_scalp.py --paper            # Paper trading (same as dry-run)
  python scripts/live_underdog_scalp.py --live             # REAL MONEY (be careful!)
  python scripts/live_underdog_scalp.py --close-thresh 42  # 42% close threshold
  python scripts/live_underdog_scalp.py --quarters 1       # Q1 only
        """
    )
    
    # Mode selection
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--dry-run', action='store_true', help='Dry run mode (simulated trades, no API orders)')
    mode_group.add_argument('--paper', action='store_true', help='Paper trading mode (same as dry-run)')
    mode_group.add_argument('--live', action='store_true', help='Live trading mode (REAL MONEY)')
    
    # Strategy parameters
    parser.add_argument('--close-thresh-early', type=float, default=20,
                        help='Close game price threshold at Q1 %% (default: 20)')
    parser.add_argument('--close-thresh-late', type=float, default=45,
                        help='Close game price threshold at Q4 %% (default: 45)')
    parser.add_argument('--close-score', type=float, default=15,
                        help='Close game threshold as score ratio %% (1 - underdog/favorite <= X%%) (default: 15)')
    parser.add_argument('--quarters', type=int, nargs='+', default=[1, 2, 3],
                        help='Allowed quarters for entry (default: 1 2 3)')
    parser.add_argument('--stop-loss', type=float, default=5,
                        help='Stop loss %% (default: 5)')
    parser.add_argument('--take-profit', type=float, default=5.5,
                        help='Take profit %% after fees (default: 5.5)')
    parser.add_argument('--position-size', type=float, default=50,
                        help='Position size in USD (default: 50)')
    parser.add_argument('--poll-interval', type=float, default=10,
                        help='Polling interval in seconds (default: 10)')
    
    # Misc
    parser.add_argument('-v', '--verbose', action='store_true', 
                        help='Verbose output showing each game being checked')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Determine mode
    if args.live:
        mode = TradingMode.LIVE
        logger.warning("⚠️  LIVE TRADING MODE - REAL MONEY AT RISK!")
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
    config = StrategyConfig(
        close_threshold_price_early=args.close_thresh_early / 100.0,
        close_threshold_price_late=args.close_thresh_late / 100.0,
        close_threshold_score=args.close_score / 100.0,
        allowed_quarters=args.quarters,
        stop_loss_pct=args.stop_loss / 100.0,
        take_profit_pct=args.take_profit / 100.0,
        position_size_usd=args.position_size,
        poll_interval_sec=args.poll_interval,
        verbose=args.verbose,
    )
    
    # Run engine
    engine = UnderdogScalpEngine(config, mode)
    engine.run()


if __name__ == "__main__":
    main()
