"""
Multi-market NBA trading with automatic market selection.
Trades on the best available markets based on opportunity scoring.
"""

import sys
import os
import time
import signal
import threading
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

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
from config.strategy_config import StrategyConfig, AGGRESSIVE

double_parent_dir = os.path.dirname(parent_dir)
sys.path.append(double_parent_dir)

from kalshi_utils.client_wrapper import KalshiWrapped


@dataclass
class MarketScore:
    """Scoring for market opportunity."""
    ticker: str
    title: str
    price: float
    spread: float
    score: float
    reason: str


class MarketScorer:
    """Scores and ranks markets by trading opportunity."""
    
    @staticmethod
    def score_market(market) -> MarketScore:
        """
        Score a market for trading opportunity.
        
        Higher score = better opportunity.
        Returns 0 for markets that should be filtered out.
        """
        yes_bid = getattr(market, 'yes_bid', 0) / 100.0
        yes_ask = getattr(market, 'yes_ask', 0) / 100.0
        
        if yes_bid <= 0 or yes_ask <= 0:
            return MarketScore(
                ticker=market.ticker,
                title=getattr(market, 'yes_sub_title', ''),
                price=0,
                spread=0,
                score=0,
                reason="No valid bid/ask"
            )
        
        price = (yes_bid + yes_ask) / 2.0
        spread = yes_ask - yes_bid
        
        # Filter out extreme prices (>97% or <3%)
        if price > 0.97:
            return MarketScore(
                ticker=market.ticker,
                title=getattr(market, 'yes_sub_title', ''),
                price=price,
                spread=spread,
                score=0,
                reason=f"Price too high ({price:.1%})"
            )
        
        if price < 0.03:
            return MarketScore(
                ticker=market.ticker,
                title=getattr(market, 'yes_sub_title', ''),
                price=price,
                spread=spread,
                score=0,
                reason=f"Price too low ({price:.1%})"
            )
        
        # Score components:
        # 1. Volatility potential (prefer mid-range 30-70%)
        if 0.30 <= price <= 0.70:
            volatility_score = 1.0
        else:
            # Linear decay outside optimal range
            volatility_score = max(0, 1 - abs(price - 0.5) * 1.5)
        
        # 2. Spread score (tighter = better, max 5 cents)
        spread_score = max(0, 1 - spread * 10)
        
        # 3. Liquidity (prefer prices that indicate active trading)
        # Mid-range prices suggest contested outcome
        liquidity_score = 1 - abs(price - 0.5) * 0.5
        
        # Combined score
        final_score = (
            volatility_score * 0.4 +
            spread_score * 0.35 +
            liquidity_score * 0.25
        )
        
        return MarketScore(
            ticker=market.ticker,
            title=getattr(market, 'yes_sub_title', ''),
            price=price,
            spread=spread,
            score=final_score,
            reason=f"Vol:{volatility_score:.2f} Spread:{spread_score:.2f} Liq:{liquidity_score:.2f}"
        )
    
    @classmethod
    def select_best_markets(cls, pairs: List, max_markets: int = 3) -> List[Tuple]:
        """
        Select the best markets to trade from available pairs.
        
        Args:
            pairs: List of market pairs
            max_markets: Maximum number of markets to select
            
        Returns:
            List of best market pairs sorted by score
        """
        scored = []
        
        for pair in pairs:
            m1, m2 = pair
            score1 = cls.score_market(m1)
            score2 = cls.score_market(m2)
            
            # Use the higher-scored market from the pair
            best_score = max(score1, score2, key=lambda s: s.score)
            if best_score.score > 0.1:  # Minimum threshold
                scored.append((best_score, pair))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[0].score, reverse=True)
        
        return [(s, p) for s, p in scored[:max_markets]]


class SharedMarketPoller:
    """
    Single polling thread for multiple markets to respect rate limits.
    Polls markets in round-robin fashion.
    """
    
    def __init__(self, client, tickers: List[str], max_rps: float = 6.0):
        """
        Args:
            client: Kalshi API client
            tickers: List of market tickers to poll
            max_rps: Maximum requests per second (leave buffer for other calls)
        """
        self.client = client
        self.tickers = tickers
        self.max_rps = max_rps
        
        # Calculate poll interval to stay under rate limit
        # With N tickers and max_rps, each ticker gets polled every N/max_rps seconds
        self.poll_interval = len(tickers) / max_rps
        
        # Data storage
        self.lock = threading.Lock()
        self.market_data: Dict[str, dict] = {}
        self.last_update: Dict[str, float] = {}
        
        # Thread control
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
        print(f"[SharedPoller] Initialized for {len(tickers)} markets")
        print(f"  Poll interval: {self.poll_interval:.2f}s per market")
        print(f"  Effective rate: {1/self.poll_interval:.1f} req/s")
    
    def start(self):
        """Start the polling thread."""
        if self.thread is None or not self.thread.is_alive():
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
    
    def stop(self):
        """Stop the polling thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
    
    def _run(self):
        """Main polling loop - round robin through all tickers."""
        ticker_index = 0
        
        while self.running:
            ticker = self.tickers[ticker_index]
            
            try:
                resp = self.client.get_market(ticker)
                if hasattr(resp, "market"):
                    data = resp.market.model_dump()
                else:
                    data = resp.model_dump() if hasattr(resp, "model_dump") else resp.__dict__
                
                with self.lock:
                    self.market_data[ticker] = data
                    self.last_update[ticker] = time.time()
                    
            except Exception as e:
                print(f"[SharedPoller] Error fetching {ticker}: {e}")
            
            # Move to next ticker
            ticker_index = (ticker_index + 1) % len(self.tickers)
            
            # Sleep to maintain rate limit
            time.sleep(self.poll_interval)
    
    def get_price(self, ticker: str) -> Optional[float]:
        """Get current mid-price for a ticker."""
        with self.lock:
            data = self.market_data.get(ticker)
            if not data:
                return None
            
            yes_bid = data.get("yes_bid", 0) / 100.0
            yes_ask = data.get("yes_ask", 0) / 100.0
            
            if yes_bid > 0 and yes_ask > 0:
                return (yes_bid + yes_ask) / 2.0
        return None
    
    def get_orderbook_features(self, ticker: str) -> Dict[str, float]:
        """Get orderbook features for a ticker."""
        with self.lock:
            data = self.market_data.get(ticker)
            if not data:
                return {'imbalance': 0, 'imbalance_ema': 0, 'microprice': 0, 'spread': 0}
            
            yes_bid = data.get("yes_bid", 0) / 100.0
            yes_ask = data.get("yes_ask", 0) / 100.0
            mid = (yes_bid + yes_ask) / 2.0 if yes_bid > 0 and yes_ask > 0 else 0
            
            return {
                'imbalance': 0,
                'imbalance_ema': 0,
                'microprice': mid,
                'spread': yes_ask - yes_bid,
                'spread_bps': ((yes_ask - yes_bid) / mid * 10000) if mid > 0 else 0
            }


class MultiMarketTrader:
    """
    Trades on multiple NBA markets simultaneously.
    Uses shared polling to respect rate limits.
    """
    
    def __init__(
        self,
        client,
        market_pairs: List[Tuple],
        initial_capital: float = 10000.0,
        dry_run: bool = True,
        config: Dict = None
    ):
        self.client = client
        self.dry_run = dry_run
        self.running = False
        self.config = config or AGGRESSIVE
        
        # Capital split across markets
        self.num_markets = len(market_pairs)
        self.capital_per_market = initial_capital / self.num_markets
        
        print("="*70)
        print("INITIALIZING MULTI-MARKET NBA TRADING SYSTEM")
        print("="*70)
        print(f"Markets: {self.num_markets}")
        print(f"Capital per market: ${self.capital_per_market:,.2f}")
        print(f"Total Capital: ${initial_capital:,.2f}")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
        print("="*70 + "\n")
        
        # Extract tickers and game info
        self.tickers = []
        self.game_info = {}
        
        for pair in market_pairs:
            m1, m2 = pair
            ticker = m1.ticker
            self.tickers.append(ticker)
            
            game_info = get_nba_game_info_from_ticker(ticker)
            if game_info:
                self.game_info[ticker] = game_info
                print(f"  ✓ {ticker}")
                print(f"    {game_info['away_team']} @ {game_info['home_team']}")
        
        if not self.tickers:
            raise ValueError("No valid markets found")
        
        # Shared market poller (respects rate limits)
        print("\n[1/5] Starting shared market poller...")
        self.shared_poller = SharedMarketPoller(client, self.tickers, max_rps=6.0)
        self.shared_poller.start()
        
        # Individual score feeds (lower rate, 3s each is fine)
        print("[2/5] Starting score feeds...")
        self.score_feeds = {}
        for ticker in self.tickers:
            info = self.game_info.get(ticker)
            if info:
                self.score_feeds[ticker] = NBAScoreFeed(
                    game_id=info['game_id'],
                    home_team_tricode=info['home_team'],
                    away_team_tricode=info['away_team'],
                    poll_interval_ms=5000,  # 5s to reduce rate
                    history_size=200
                )
                self.score_feeds[ticker].start()
        
        # Shared execution components
        print("[3/5] Initializing execution components...")
        self.order_manager = OrderManager(client, dry_run=dry_run)
        self.position_manager = PositionManager(initial_capital)
        self.risk_manager = RiskManager(
            self.position_manager,
            max_position_size=config['risk']['max_position_size'] / self.num_markets,
            max_total_exposure=config['risk']['max_total_exposure'],
            max_loss_per_trade=config['risk']['max_loss_per_trade'],
            max_daily_loss=config['risk']['max_daily_loss']
        )
        
        # Per-market strategies
        print("[4/5] Initializing strategies...")
        self.strategies = {}
        self.kalman_filters = {}
        self.performance_trackers = {}
        
        for ticker in self.tickers:
            info = self.game_info.get(ticker, {})
            session_name = f"{info.get('away_team', 'UNK')}_vs_{info.get('home_team', 'UNK')}_{datetime.now().strftime('%Y%m%d_%H%M')}"
            
            self.kalman_filters[ticker] = EnhancedKalmanFilter(**config['kalman'])
            
            self.strategies[ticker] = MomentumStrategy(
                kalman_filter=self.kalman_filters[ticker],
                order_manager=self.order_manager,
                position_manager=self.position_manager,
                risk_manager=self.risk_manager,
                **config['momentum']
            )
            
            self.performance_trackers[ticker] = PerformanceTracker(
                initial_capital=self.capital_per_market,
                session_name=session_name
            )
        
        # Warmup
        print("\n[5/5] Warming up (10 seconds)...")
        time.sleep(10)
        
        print("\n" + "="*70)
        print("MULTI-MARKET INITIALIZATION COMPLETE")
        print("="*70 + "\n")
    
    def run(self, max_iterations: Optional[int] = None, update_interval: float = 2.0):
        """Main trading loop for all markets."""
        self.running = True
        iteration = 0
        last_trade_ids = {t: None for t in self.tickers}
        
        print("="*70)
        print(f"STARTING MULTI-MARKET TRADING ({len(self.tickers)} markets)")
        print("="*70)
        print("Press Ctrl+C to stop\n")
        
        try:
            while self.running:
                iteration += 1
                
                # Process each market
                for ticker in self.tickers:
                    self._process_market(ticker, iteration, last_trade_ids)
                
                # Print summary every 5 iterations
                if iteration % 5 == 0:
                    self._print_summary(iteration)
                
                if max_iterations and iteration >= max_iterations:
                    break
                
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            print("\n\nReceived interrupt signal...")
        
        finally:
            self.shutdown()
    
    def _process_market(self, ticker: str, iteration: int, last_trade_ids: dict):
        """Process a single market iteration."""
        current_price = self.shared_poller.get_price(ticker)
        if current_price is None:
            return
        
        orderbook_features = self.shared_poller.get_orderbook_features(ticker)
        
        score_feed = self.score_feeds.get(ticker)
        if score_feed:
            score_features = score_feed.get_current_features()
        else:
            score_features = {'win_probability': 0.5, 'momentum': 0}
        
        strategy = self.strategies[ticker]
        tracker = self.performance_trackers[ticker]
        
        # Update strategy
        signal = strategy.update(ticker, current_price, orderbook_features, score_features)
        
        position = self.position_manager.get_position(ticker)
        
        if signal and signal.value != 'hold':
            # Entry
            if position is None and signal.value in ['buy', 'strong_buy', 'sell', 'strong_sell']:
                success = strategy.execute_signal(signal, current_price)
                if success:
                    new_pos = self.position_manager.get_position(ticker)
                    if new_pos:
                        info = self.game_info.get(ticker, {})
                        print(f"\n[{info.get('away_team', '')}@{info.get('home_team', '')}] "
                              f"{signal.value.upper()} {abs(new_pos.quantity)} @ ${current_price:.4f}")
                        last_trade_ids[ticker] = tracker.record_trade_entry(
                            ticker=ticker,
                            side='buy' if new_pos.quantity > 0 else 'sell',
                            quantity=abs(new_pos.quantity),
                            entry_price=new_pos.average_price,
                            context={}
                        )
            
            # Exit
            elif position is not None:
                success = strategy.execute_signal(signal, current_price)
                if success and last_trade_ids[ticker] is not None:
                    tracker.record_trade_exit(
                        trade_id=last_trade_ids[ticker],
                        exit_price=current_price
                    )
                    last_trade_ids[ticker] = None
        
        self.position_manager.update_market_prices({ticker: current_price})
    
    def _print_summary(self, iteration: int):
        """Print summary of all markets."""
        now = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n{'='*70}")
        print(f"[{now}] Iteration {iteration} | {len(self.tickers)} Markets")
        print(f"{'='*70}")
        
        total_pnl = 0
        for ticker in self.tickers:
            info = self.game_info.get(ticker, {})
            matchup = f"{info.get('away_team', '?')}@{info.get('home_team', '?')}"
            
            price = self.shared_poller.get_price(ticker) or 0
            position = self.position_manager.get_position(ticker)
            
            pos_str = f"{position.quantity:+d}" if position else "FLAT"
            
            score_feed = self.score_feeds.get(ticker)
            if score_feed and score_feed.current_score:
                score = f"{score_feed.current_score.away_score}-{score_feed.current_score.home_score}"
            else:
                score = "?-?"
            
            print(f"  {matchup:12} | {score:7} | ${price:.2f} | {pos_str:6}")
        
        print(f"{'-'*70}")
        print(f"  Total P&L: ${self.position_manager.get_total_pnl():+,.2f}")
        print(f"  Equity:    ${self.position_manager.get_equity():,.2f}")
        print(f"{'='*70}")
    
    def shutdown(self):
        """Graceful shutdown."""
        print("\n" + "="*70)
        print("SHUTTING DOWN MULTI-MARKET TRADER")
        print("="*70)
        
        self.running = False
        self.shared_poller.stop()
        
        for ticker, feed in self.score_feeds.items():
            feed.stop()
        
        self.order_manager.cancel_all_orders()
        
        # Close any open positions
        for ticker in self.tickers:
            position = self.position_manager.get_position(ticker)
            if position:
                price = self.shared_poller.get_price(ticker)
                if price:
                    self.position_manager.close_position(ticker, price)
        
        # Print final summary
        print("\n" + "="*70)
        print("FINAL PERFORMANCE")
        print("="*70)
        
        for ticker in self.tickers:
            info = self.game_info.get(ticker, {})
            tracker = self.performance_trackers[ticker]
            stats = tracker.get_statistics()
            
            matchup = f"{info.get('away_team', '?')} @ {info.get('home_team', '?')}"
            trades = stats.get('total_trades', 0)
            pnl = stats.get('total_pnl', 0)
            
            print(f"  {matchup}: {trades} trades, ${pnl:+,.2f}")
        
        print(f"\n  TOTAL P&L: ${self.position_manager.get_total_pnl():+,.2f}")
        print("="*70)


def main():
    """Main entry point for multi-market trading."""
    
    DRY_RUN = False
    MAX_MARKETS = 3
    INITIAL_CAPITAL = 10000
    
    print("="*70)
    print("MULTI-MARKET NBA TRADING BOT")
    print("="*70 + "\n")
    
    # Initialize client
    print("Connecting to Kalshi...")
    kalshi = KalshiWrapped()
    client = kalshi.GetClient()
    
    # Get live NBA markets
    print("Fetching live NBA markets...")
    live_markets = kalshi.GetLiveNBAMarkets()
    
    if not live_markets:
        print("\n❌ No live NBA markets found!")
        return
    
    print(f"✓ Found {len(live_markets)} live markets")
    
    pairs = kalshi.GetMarketPairs(live_markets)
    if not pairs:
        print("\n❌ No market pairs found!")
        return
    
    print(f"✓ Found {len(pairs)} market pairs\n")
    
    # Score and select best markets
    print("Scoring markets for opportunity...")
    scored_markets = MarketScorer.select_best_markets(pairs, max_markets=MAX_MARKETS)
    
    if not scored_markets:
        print("\n❌ No tradeable markets found (all filtered out)")
        return
    
    print(f"\n✓ Selected {len(scored_markets)} best markets:\n")
    
    selected_pairs = []
    for score, pair in scored_markets:
        print(f"  [{score.score:.2f}] {score.title}")
        print(f"        Price: {score.price:.1%} | Spread: ${score.spread:.2f}")
        print(f"        {score.reason}")
        print()
        selected_pairs.append(pair)
    
    if len(selected_pairs) < 2:
        print(f"⚠️ Only {len(selected_pairs)} market available, need at least 2")
        # Still proceed with 1 market if that's all we have
    
    # Create multi-market trader
    try:
        trader = MultiMarketTrader(
            client=client,
            market_pairs=selected_pairs,
            initial_capital=INITIAL_CAPITAL,
            dry_run=DRY_RUN,
            config=AGGRESSIVE
        )
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return
    
    def signal_handler(sig, frame):
        trader.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    print("\nStarting multi-market trading...")
    print("(Press Ctrl+C to stop)\n")
    
    trader.run(max_iterations=None, update_interval=2.0)


if __name__ == "__main__":
    main()
