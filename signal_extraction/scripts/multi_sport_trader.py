"""
Multi-Sport Trader - Unified entry point for trading across all sports.

Supports: NBA, NHL, Soccer (UCL), College Basketball (NCAAMB), Tennis
"""

import os
import sys
import time
import signal
import argparse
from typing import Optional, List, Dict, Type
from enum import Enum

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_utils.client_wrapper import KalshiWrapped
from data_feeds.base_score_feed import BaseScoreFeed, SportType
from data_feeds.nhl_score_feed import NHLScoreFeed, get_nhl_game_info_from_ticker
from data_feeds.soccer_score_feed import SoccerScoreFeed, get_soccer_game_info_from_ticker
from data_feeds.ncaamb_score_feed import NCAAMBScoreFeed, get_ncaamb_game_info_from_ticker
from data_feeds.tennis_score_feed import TennisScoreFeed, get_tennis_match_info_from_ticker
from data_feeds.orderbook import OrderbookFeed
from models.enhanced_kalman import EnhancedKalmanFilter
from execution.position_manager import PositionManager, RiskManager
from execution.order_manager import OrderManager
from strategies.momentum_strategy import MomentumStrategy
from utils.performance_tracker import PerformanceTracker
from config.strategy_config import StrategyConfig


# Configuration
DRY_RUN = False  # Set to True for paper trading


class SportConfig:
    """Configuration for each sport."""
    
    CONFIGS = {
        SportType.NBA: {
            'series_ticker': 'KXNBAGAME',
            'feed_class': None,  # Use existing NBA feed
            'get_markets': 'GetLiveNBAMarkets',
            'get_game_info': None,  # Custom NBA logic
            'poll_interval_ms': 3000,
        },
        SportType.NHL: {
            'series_ticker': 'KXNHLGAME',
            'feed_class': NHLScoreFeed,
            'get_markets': 'GetLiveNHLMarkets',
            'get_game_info': get_nhl_game_info_from_ticker,
            'poll_interval_ms': 3000,
        },
        SportType.SOCCER: {
            'series_ticker': 'KXUCLGAME',
            'feed_class': SoccerScoreFeed,
            'get_markets': 'GetLiveUCLMarkets',
            'get_game_info': get_soccer_game_info_from_ticker,
            'poll_interval_ms': 5000,
        },
        SportType.NCAAMB: {
            'series_ticker': 'KXNCAAMBGAME',
            'feed_class': NCAAMBScoreFeed,
            'get_markets': 'GetALLNCAAMBMarkets',
            'get_game_info': get_ncaamb_game_info_from_ticker,
            'poll_interval_ms': 3000,
        },
        SportType.TENNIS: {
            'series_ticker': 'KXATPMATCH',
            'feed_class': TennisScoreFeed,
            'get_markets': 'GetALLTennisMarkets',
            'get_game_info': get_tennis_match_info_from_ticker,
            'poll_interval_ms': 5000,
        },
    }
    
    @classmethod
    def get_config(cls, sport: SportType) -> Dict:
        return cls.CONFIGS.get(sport, cls.CONFIGS[SportType.NBA])


class MultiSportTrader:
    """
    Unified trader that can trade any supported sport.
    
    Features:
        - Auto-detects sport from market ticker
        - Creates appropriate score feed
        - Uses same momentum strategy across sports
    """
    
    def __init__(
        self,
        sports: List[SportType] = None,
        strategy_preset: str = 'aggressive',
        dry_run: bool = DRY_RUN
    ):
        """
        Initialize the multi-sport trader.
        
        Args:
            sports: List of sports to trade (None = all)
            strategy_preset: Strategy configuration preset
            dry_run: If True, simulate trades only
        """
        self.dry_run = dry_run
        self.sports = sports or list(SportType)
        self.strategy_preset = strategy_preset
        
        # Kalshi client
        self.kalshi = KalshiWrapped()
        self.balance = self.kalshi.GetBalance()
        
        # Active components per market
        self.active_feeds: Dict[str, BaseScoreFeed] = {}
        self.active_strategies: Dict[str, MomentumStrategy] = {}
        self.active_orderbook_feeds: Dict[str, OrderbookFeed] = {}
        
        # Shared components
        self.pm = PositionManager(initial_capital=self.balance)
        self.rm = RiskManager(self.pm)
        self.om = OrderManager(self.kalshi.client, dry_run=dry_run)
        self.tracker = PerformanceTracker(self.pm)
        
        # Control
        self.running = False
        
        print(f"[MultiSportTrader] Initialized")
        print(f"  Sports: {[s.value for s in self.sports]}")
        print(f"  Strategy: {strategy_preset}")
        print(f"  Dry Run: {dry_run}")
        print(f"  Balance: ${self.balance:.2f}")
    
    def discover_markets(self) -> List[Dict]:
        """
        Discover live markets across all configured sports.
        
        Returns:
            List of market info dicts with sport, ticker, game_info
        """
        all_markets = []
        
        for sport in self.sports:
            config = SportConfig.get_config(sport)
            get_markets_method = config.get('get_markets')
            
            if not get_markets_method:
                continue
            
            try:
                # Get markets from Kalshi
                method = getattr(self.kalshi, get_markets_method)
                markets = method()
                
                for market in markets:
                    ticker = market.ticker
                    
                    # Get game info from ticker
                    get_game_info = config.get('get_game_info')
                    game_info = None
                    if get_game_info:
                        game_info = get_game_info(ticker)
                    
                    if game_info:
                        all_markets.append({
                            'sport': sport,
                            'ticker': ticker,
                            'game_info': game_info,
                            'market': market,
                            'config': config
                        })
                        print(f"[Discover] Found {sport.value.upper()}: {ticker}")
                
            except Exception as e:
                print(f"[Discover] Error fetching {sport.value} markets: {e}")
        
        return all_markets
    
    def setup_market(self, market_info: Dict) -> bool:
        """
        Set up trading components for a specific market.
        
        Args:
            market_info: Dict with sport, ticker, game_info
            
        Returns:
            True if setup successful
        """
        sport = market_info['sport']
        ticker = market_info['ticker']
        game_info = market_info['game_info']
        config = market_info['config']
        
        if ticker in self.active_feeds:
            return True  # Already set up
        
        try:
            # Create score feed for this sport
            feed_class = config.get('feed_class')
            
            if feed_class and game_info:
                # Create the appropriate feed
                if sport == SportType.TENNIS:
                    score_feed = feed_class(
                        match_id=game_info.get('match_id', game_info.get('game_id')),
                        player1_name=game_info.get('player1', game_info.get('home_team')),
                        player2_name=game_info.get('player2', game_info.get('away_team')),
                        poll_interval_ms=config.get('poll_interval_ms', 5000)
                    )
                else:
                    score_feed = feed_class(
                        game_id=game_info['game_id'],
                        home_team_code=game_info['home_team'],
                        away_team_code=game_info['away_team'],
                        poll_interval_ms=config.get('poll_interval_ms', 3000)
                    )
                
                score_feed.start()
                self.active_feeds[ticker] = score_feed
            
            # Create orderbook feed
            ob_feed = OrderbookFeed(
                ticker=ticker,
                kalshi_client=self.kalshi.client,
                poll_interval_ms=1000
            )
            ob_feed.start()
            self.active_orderbook_feeds[ticker] = ob_feed
            
            # Create Kalman filter
            strategy_config = StrategyConfig.get_config(self.strategy_preset)
            kf = EnhancedKalmanFilter(**strategy_config.get('kalman', {}))
            
            # Create strategy
            strategy = MomentumStrategy(
                kalman_filter=kf,
                order_manager=self.om,
                position_manager=self.pm,
                risk_manager=self.rm,
                **strategy_config.get('momentum', {})
            )
            strategy.ticker = ticker
            self.active_strategies[ticker] = strategy
            
            print(f"[Setup] {sport.value.upper()} market ready: {ticker}")
            return True
            
        except Exception as e:
            print(f"[Setup] Error setting up {ticker}: {e}")
            return False
    
    def run_trading_loop(self, duration_seconds: int = 3600):
        """
        Main trading loop.
        
        Args:
            duration_seconds: How long to run
        """
        self.running = True
        start_time = time.time()
        
        print(f"\n[MultiSportTrader] Starting trading loop for {duration_seconds}s")
        
        # Discovery phase
        markets = self.discover_markets()
        
        if not markets:
            print("[MultiSportTrader] No live markets found!")
            return
        
        # Setup all discovered markets
        for market_info in markets:
            self.setup_market(market_info)
        
        print(f"\n[MultiSportTrader] Trading {len(self.active_strategies)} markets...")
        
        loop_count = 0
        
        try:
            while self.running and (time.time() - start_time) < duration_seconds:
                loop_count += 1
                
                # Process each active market
                for ticker, strategy in list(self.active_strategies.items()):
                    try:
                        # Get latest data
                        score_feed = self.active_feeds.get(ticker)
                        ob_feed = self.active_orderbook_feeds.get(ticker)
                        
                        if not ob_feed:
                            continue
                        
                        ob_state = ob_feed.get_current_state()
                        if not ob_state or not ob_state.get('mid_price'):
                            continue
                        
                        # Get score features
                        score_features = {}
                        if score_feed:
                            score_features = score_feed.get_current_features()
                        
                        # Update strategy
                        signal = strategy.update(
                            mid_price=ob_state['mid_price'],
                            best_bid=ob_state.get('best_bid'),
                            best_ask=ob_state.get('best_ask'),
                            bid_size=ob_state.get('bid_size', 0),
                            ask_size=ob_state.get('ask_size', 0),
                            score_features=score_features
                        )
                        
                        # Execute if signal
                        if signal:
                            strategy.execute_signal(
                                signal=signal,
                                current_price=ob_state['mid_price'],
                                best_bid=ob_state.get('best_bid'),
                                best_ask=ob_state.get('best_ask')
                            )
                    
                    except Exception as e:
                        print(f"[Loop] Error processing {ticker}: {e}")
                
                # Periodic logging
                if loop_count % 30 == 0:
                    self._log_status()
                
                time.sleep(1.0)
        
        finally:
            self.shutdown()
    
    def _log_status(self):
        """Log current status."""
        total_pnl = self.pm.get_total_pnl()
        positions = [t for t, p in self.pm.positions.items() if p.quantity != 0]
        
        print(f"\n[Status] Active: {len(self.active_strategies)} markets | "
              f"Positions: {len(positions)} | PnL: ${total_pnl:.2f}")
    
    def shutdown(self):
        """Clean shutdown."""
        print("\n[MultiSportTrader] Shutting down...")
        self.running = False
        
        # Stop all feeds
        for feed in self.active_feeds.values():
            feed.stop()
        for feed in self.active_orderbook_feeds.values():
            feed.stop()
        
        # Log final performance
        self.tracker.log_summary()
        print("[MultiSportTrader] Shutdown complete")


def main():
    parser = argparse.ArgumentParser(description='Multi-Sport Trading Bot')
    parser.add_argument('--sports', nargs='+', type=str, default=['nhl', 'soccer', 'ncaamb', 'tennis'],
                        help='Sports to trade (nba, nhl, soccer, ncaamb, tennis)')
    parser.add_argument('--duration', type=int, default=3600,
                        help='Trading duration in seconds')
    parser.add_argument('--strategy', type=str, default='aggressive',
                        help='Strategy preset (moderate, aggressive)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate trades only')
    
    args = parser.parse_args()
    
    # Parse sports
    sport_map = {
        'nba': SportType.NBA,
        'nhl': SportType.NHL,
        'soccer': SportType.SOCCER,
        'ucl': SportType.SOCCER,
        'ncaamb': SportType.NCAAMB,
        'tennis': SportType.TENNIS,
    }
    sports = [sport_map[s.lower()] for s in args.sports if s.lower() in sport_map]
    
    # Create and run trader
    trader = MultiSportTrader(
        sports=sports,
        strategy_preset=args.strategy,
        dry_run=args.dry_run or DRY_RUN
    )
    
    # Handle Ctrl+C
    def signal_handler(sig, frame):
        trader.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run
    trader.run_trading_loop(duration_seconds=args.duration)


if __name__ == '__main__':
    main()
