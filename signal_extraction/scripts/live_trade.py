"""
Live NBA trading script with configurable strategy selection.
Easily switch between momentum and mean reversion strategies.
"""

import sys
import os
import time
import signal
from datetime import datetime
from typing import Optional, Dict

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from data_feeds.orderbook_feed import OrderbookFeed
from data_feeds.score_feed import NBAScoreFeed, get_nba_game_info_from_ticker
from models.enhanced_kalman import EnhancedKalmanFilter 
from execution.order_manager import OrderManager
from execution.position_manager import PositionManager, RiskManager
from execution.performance_tracker import PerformanceTracker
from strategies.momentum_strategy import MomentumStrategy
from strategies.mean_reversion_strategy import MeanReversionStrategy
from config.strategy_config import StrategyConfig, MODERATE, AGGRESSIVE, CONSERVATIVE

double_parent_dir = os.path.dirname(parent_dir)
sys.path.append(double_parent_dir)

from kalshi_utils.client_wrapper import KalshiWrapped


class ConfigurableNBATrader:
    """
    Configurable live trading system for NBA markets.
    Supports multiple strategies and easy parameter tuning.
    """
    
    def __init__(
        self,
        client,
        ticker: str,
        initial_capital: float = 10000.0,
        dry_run: bool = True,
        config: Dict = None
    ):
        """
        Initialize live NBA trading system.
        
        Args:
            client: Kalshi API client
            ticker: Market ticker to trade
            initial_capital: Starting capital
            dry_run: If True, simulate trades
            config: Strategy configuration (uses MODERATE if None)
        """
        self.client = client
        self.ticker = ticker
        self.dry_run = dry_run
        self.running = False
        
        # Use default config if none provided
        if config is None:
            config = MODERATE
            print("Using MODERATE configuration (default)")
        
        self.config = config
        
        print("="*70)
        print("INITIALIZING CONFIGURABLE NBA TRADING SYSTEM")
        print("="*70)
        print(f"Ticker: {ticker}")
        print(f"Capital: ${initial_capital:,.2f}")
        print(f"Mode: {'DRY RUN (SIMULATED)' if dry_run else 'LIVE TRADING'}")
        print(f"Strategy: {config['strategy'].upper()}")
        print("="*70 + "\n")
        
        # Get game info from ticker
        print("[0/8] Extracting game information from ticker...")
        game_info = get_nba_game_info_from_ticker(ticker)
        
        if not game_info:
            print(f"ERROR: Could not find live NBA game for ticker {ticker}")
            raise ValueError("No live game found for ticker")
        
        self.game_id = game_info['game_id']
        self.home_team = game_info['home_team']
        self.away_team = game_info['away_team']
        
        session_name = f"{self.away_team}_vs_{self.home_team}_{datetime.now().strftime('%Y%m%d_%H%M')}"
        
        print(f"  Game ID: {self.game_id}")
        print(f"  Matchup: {self.away_team} @ {self.home_team}")
        
        # Initialize performance tracker
        print("\n[1/8] Initializing performance tracker...")
        self.performance_tracker = PerformanceTracker(
            initial_capital=initial_capital,
            session_name=session_name
        )
        
        # Initialize feeds
        print("[2/8] Starting orderbook feed...")
        self.orderbook_feed = OrderbookFeed(
            client=client,
            ticker=ticker,
            poll_interval_ms=500,
            history_size=1000
        )
        self.orderbook_feed.start()
        
        print("[3/8] Starting NBA score feed...")
        self.score_feed = NBAScoreFeed(
            game_id=self.game_id,
            home_team_tricode=self.home_team,
            away_team_tricode=self.away_team,
            poll_interval_ms=3000,
            history_size=500
        )
        self.score_feed.start()
        
        # Initialize Kalman filter with config
        print("[4/8] Initializing enhanced Kalman filter...")
        kalman_params = config['kalman']
        self.kalman_filter = EnhancedKalmanFilter(**kalman_params)
        
        print(f"  Process noise: {kalman_params['process_noise']:.2e}")
        print(f"  Measurement noise: {kalman_params['measurement_noise']:.2e}")
        print(f"  Score weight: {kalman_params['score_weight']:.2f}")
        
        # Initialize execution components
        print("\n[5/8] Initializing order manager...")
        self.order_manager = OrderManager(client, dry_run=dry_run)
        
        print("[6/8] Initializing position manager...")
        self.position_manager = PositionManager(initial_capital)
        
        print("[7/8] Initializing risk manager...")
        risk_params = config['risk']
        self.risk_manager = RiskManager(
            self.position_manager,
            max_position_size=risk_params['max_position_size'],
            max_total_exposure=risk_params['max_total_exposure'],
            max_loss_per_trade=risk_params['max_loss_per_trade'],
            max_daily_loss=risk_params['max_daily_loss']
        )
        
        # Initialize strategy based on config
        print("[8/8] Initializing trading strategy...")
        if config['strategy'] == 'momentum':
            strategy_params = config['momentum']
            self.strategy = MomentumStrategy(
                kalman_filter=self.kalman_filter,
                order_manager=self.order_manager,
                position_manager=self.position_manager,
                risk_manager=self.risk_manager,
                **strategy_params
            )
        elif config['strategy'] == 'mean_reversion':
            strategy_params = config['mean_reversion']
            self.strategy = MeanReversionStrategy(
                kalman_filter=self.kalman_filter,
                order_manager=self.order_manager,
                position_manager=self.position_manager,
                risk_manager=self.risk_manager,
                **strategy_params
            )
        else:
            raise ValueError(f"Unknown strategy: {config['strategy']}")
        
        # Warmup period
        print("\n[Warmup] Collecting initial data (15 seconds)...")
        time.sleep(15)
        
        ob_features = self.orderbook_feed.get_current_features()
        score_features = self.score_feed.get_current_features()
        
        print(f"\n[Warmup Complete]")
        print(f"  Current Score: {self.away_team} {score_features['away_score']:.0f} - "
              f"{score_features['home_score']:.0f} {self.home_team}")
        print(f"  Win Probability: {score_features['win_probability']:.1%}")
        print(f"  Orderbook Imbalance: {ob_features['imbalance']:+.3f}")
        
        print("\n" + "="*70)
        print("INITIALIZATION COMPLETE - READY TO TRADE")
        print("="*70 + "\n")
    
    def run(self, max_iterations: Optional[int] = None, update_interval: float = 2.0):
        """Main trading loop."""
        self.running = True
        iteration = 0
        last_trade_id = None
        
        print("="*70)
        print(f"STARTING LIVE NBA TRADING ({self.config['strategy'].upper()})")
        print("="*70)
        print("Press Ctrl+C to stop\n")
        
        try:
            while self.running:
                iteration += 1
                
                current_price = self._get_current_price()
                if current_price is None:
                    time.sleep(1)
                    continue
                
                orderbook_features = self.orderbook_feed.get_current_features()
                score_features = self.score_feed.get_current_features()
                
                current_equity = self.position_manager.get_equity()
                self.performance_tracker.update_equity(current_equity)
                
                if not self.score_feed.current_score or \
                   self.score_feed.current_score.game_status != 'live':
                    print("\n[WARNING] Game is no longer live!")
                    break
                
                signal = self.strategy.update(
                    self.ticker,
                    current_price,
                    orderbook_features,
                    score_features
                )
                
                position = self.position_manager.get_position(self.ticker)
                
                if signal:
                    # Don't print HOLD signals every time
                    if signal.value != 'hold':
                        print(f"\n[SIGNAL] {signal.value.upper()} at ${current_price:.4f}")
                    
                    if position is None and signal.value in ['buy', 'strong_buy', 'sell', 'strong_sell']:
                        status = self.strategy.get_status()
                        context = {
                            'fair_value': status['fair_value'],
                            'mispricing': status['fair_value'] - current_price,
                            'signal_strength': status.get('velocity', 0),
                            'orderbook_imbalance': orderbook_features.get('imbalance_ema', 0),
                            'score_differential': int(score_features.get('score_differential', 0)),
                            'win_probability': score_features.get('win_probability', 0.5)
                        }
                        
                        success = self.strategy.execute_signal(signal, current_price)
                        
                        if success:
                            new_position = self.position_manager.get_position(self.ticker)
                            if new_position:
                                side = 'buy' if new_position.quantity > 0 else 'sell'
                                last_trade_id = self.performance_tracker.record_trade_entry(
                                    ticker=self.ticker,
                                    side=side,
                                    quantity=abs(new_position.quantity),
                                    entry_price=new_position.average_price,
                                    context=context
                                )
                    
                    elif position is not None and signal.value != 'hold':
                        success = self.strategy.execute_signal(signal, current_price)
                        
                        if success and last_trade_id is not None:
                            self.performance_tracker.record_trade_exit(
                                trade_id=last_trade_id,
                                exit_price=current_price
                            )
                            last_trade_id = None
                
                self.position_manager.update_market_prices({self.ticker: current_price})
                
                if iteration % 5 == 0:
                    self._print_status(iteration, current_price, orderbook_features, score_features)
                
                if self.score_feed.was_recent_score(within_seconds=5.0):
                    self._print_scoring_alert(score_features)
                
                if max_iterations and iteration >= max_iterations:
                    break
                
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            print("\n\nReceived interrupt signal...")
        
        finally:
            self.performance_tracker.end_session()
            self.shutdown()
    
    def _get_current_price(self) -> Optional[float]:
        """Get current market mid-price."""
        try:
            resp = self.client.get_market(self.ticker)
            if hasattr(resp, "market"):
                data = resp.market.model_dump()
            else:
                data = resp.model_dump() if hasattr(resp, "model_dump") else resp.__dict__
            
            yes_bid = data.get("yes_bid", 0) / 100.0
            yes_ask = data.get("yes_ask", 0) / 100.0
            
            if yes_bid > 0 and yes_ask > 0:
                return (yes_bid + yes_ask) / 2.0
        except Exception as e:
            print(f"[ERROR] Getting price: {e}")
        return None
    
    def _print_status(self, iteration: int, price: float, orderbook: Dict, score: Dict):
        """Print current status."""
        now = datetime.now().strftime("%H:%M:%S")
        status = self.strategy.get_status()
        fair_value = status['fair_value']
        velocity = status.get('velocity', 0)
        
        position = self.position_manager.get_position(self.ticker)
        pos_str = f"{position.quantity:+d} @ ${position.average_price:.4f}" if position else "FLAT"
        pnl_str = f"${position.unrealized_pnl:+.2f}" if position else "$0.00"
        
        total_pnl = self.position_manager.get_total_pnl()
        equity = self.position_manager.get_equity()
        score_str = f"{self.away_team} {score['away_score']:.0f} - {score['home_score']:.0f} {self.home_team}"
        
        print(f"\n{'='*70}")
        print(f"[{now}] Iteration {iteration} | Q{score['period']:.0f}")
        print(f"{'='*70}")
        print(f"  Score:       {score_str}")
        print(f"  Win Prob:    {score['win_probability']:.1%} (Home)")
        print(f"-"*70)
        print(f"  Price:       ${price:.4f}")
        print(f"  Fair Value:  ${fair_value:.4f}")
        print(f"  Velocity:    {velocity:+.4f}")
        print(f"-"*70)
        print(f"  Position:    {pos_str}")
        print(f"  Total P&L:   ${total_pnl:+,.2f}")
        print(f"  Equity:      ${equity:,.2f}")
        print(f"{'='*70}")
    
    def _print_scoring_alert(self, score_features: Dict):
        """Print alert when scoring happens."""
        points = score_features.get('points_in_last_event', 0)
        if points > 0:
            print(f"\n{'🏀'*20}")
            print(f"  SCORING EVENT! +{points:.0f} points")
            print(f"{'🏀'*20}\n")
    
    def shutdown(self):
        """Graceful shutdown."""
        print("\n" + "="*70)
        print("SHUTTING DOWN")
        print("="*70)
        
        self.running = False
        self.orderbook_feed.stop()
        self.score_feed.stop()
        
        current_price = self._get_current_price()
        if current_price:
            self.order_manager.cancel_all_orders()
            position = self.position_manager.get_position(self.ticker)
            if position:
                for trade in self.performance_tracker.trades:
                    if not trade.is_closed:
                        self.performance_tracker.record_trade_exit(
                            trade_id=trade.trade_id,
                            exit_price=current_price
                        )
                self.position_manager.close_position(self.ticker, current_price)
        
        print("\n" + "="*70)
        print("FINAL PERFORMANCE REPORT")
        print("="*70)
        
        self.performance_tracker.print_summary()
        
        print("\n" + "="*70)
        print("DETAILED BREAKDOWN")
        print("="*70 + "\n")
        
        self.position_manager.print_positions()
        self.order_manager.print_statistics()
        
        print("\n" + "="*70)
        print("SAVING RESULTS")
        print("="*70)
        session_file = self.performance_tracker.save_results()
        print(f"Session saved as: {session_file}")
        
        try:
            self.performance_tracker.plot_results()
        except Exception as e:
            print(f"(Plotting not available: {e})")
        
        print("\n" + "="*70)
        print("SHUTDOWN COMPLETE")
        print("="*70)


def main():
    """Main entry point."""
    
    # ⚠️ CONFIGURATION ⚠️
    DRY_RUN = False
    
    # Choose strategy preset: CONSERVATIVE, MODERATE, or AGGRESSIVE
    STRATEGY_CONFIG = AGGRESSIVE  # ← Change this!
    
    print("="*70)
    print("CONFIGURABLE NBA LIVE TRADING BOT")
    print("="*70 + "\n")
    
    # Print selected configuration
    StrategyConfig.print_config(STRATEGY_CONFIG)
    
    # Initialize client
    print("Connecting to Kalshi...")
    kalshi = KalshiWrapped()
    client = kalshi.GetClient()
    INITIAL_CAPITAL = kalshi.GetBalance()
    
    # Get live NBA markets
    print("Fetching live NBA markets...")
    live_markets = kalshi.GetLiveNBAMarkets()
    
    if not live_markets:
        print("\n❌ No live NBA markets found!")
        return
    
    print(f"\n✓ Found {len(live_markets)} live NBA markets")
    
    pairs = kalshi.GetMarketPairs(live_markets)
    if not pairs:
        print("\n❌ No market pairs found!")
        return
    
    print(f"✓ Found {len(pairs)} market pairs\n")
    
    # Display markets
    print("Available markets:")
    for i, pair in enumerate(pairs):
        m1, m2 = pair
        print(f"  [{i+1}] {m1.ticker}")
        print(f"      {m1.yes_sub_title}")
        print()
    
    selected_pair = pairs[0]
    selected_market = selected_pair[0]
    ticker = selected_market.ticker
    
    print(f"\n{'='*70}")
    print(f"SELECTED MARKET: {ticker}")
    print(f"{'='*70}\n")
    
    # Create trader
    try:
        trader = ConfigurableNBATrader(
            client=client,
            ticker=ticker,
            initial_capital=INITIAL_CAPITAL,
            dry_run=DRY_RUN,
            config=STRATEGY_CONFIG
        )
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return
    
    def signal_handler(sig, frame):
        trader.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    print("\nStarting trading loop...")
    print("(Press Ctrl+C to stop)\n")
    
    trader.run(max_iterations=None, update_interval=2.0)


if __name__ == "__main__":
    main()