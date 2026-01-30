"""
Real-time orderbook feed and orderbook analysis.
Extracts imbalance, depth, and liquidity metrics.
"""

import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import numpy as np


@dataclass
class OrderbookSnapshot:
    """Snapshot of orderbook at a point in time."""
    timestamp: float
    ticker: str
    
    # Best bid/ask
    best_bid: float
    best_ask: float
    
    # Full orderbook (up to 5 levels typically)
    bids: List[Tuple[float, int]]  # [(price, quantity), ...]
    asks: List[Tuple[float, int]]
    
    # Derived metrics
    mid_price: float
    spread: float
    spread_bps: float  # basis points
    
    def __post_init__(self):
        if self.mid_price is None:
            self.mid_price = (self.best_bid + self.best_ask) / 2
        if self.spread is None:
            self.spread = self.best_ask - self.best_bid
        if self.spread_bps is None:
            self.spread_bps = (self.spread / self.mid_price) * 10000


class OrderbookAnalyzer:
    """
    Analyzes orderbook snapshots to extract predictive features.
    """
    
    @staticmethod
    def calculate_imbalance(snapshot: OrderbookSnapshot) -> float:
        """
        Calculate bid-ask imbalance.
        
        Imbalance > 0: More buying pressure
        Imbalance < 0: More selling pressure
        
        Returns value in [-1, 1]
        """
        if not snapshot.bids or not snapshot.asks:
            return 0.0
        
        # Sum quantities at best bid/ask
        bid_qty = sum(qty for _, qty in snapshot.bids[:3])  # Top 3 levels
        ask_qty = sum(qty for _, qty in snapshot.asks[:3])
        
        total = bid_qty + ask_qty
        if total == 0:
            return 0.0
        
        imbalance = (bid_qty - ask_qty) / total
        return imbalance
    
    @staticmethod
    def calculate_depth_imbalance(snapshot: OrderbookSnapshot, levels: int = 5) -> float:
        """
        Weighted depth imbalance considering distance from mid.
        
        Orders closer to mid-price are weighted more heavily.
        """
        if not snapshot.bids or not snapshot.asks:
            return 0.0
        
        mid = snapshot.mid_price
        
        # Calculate weighted bid depth
        bid_depth = 0.0
        for price, qty in snapshot.bids[:levels]:
            distance = mid - price
            weight = 1.0 / (1.0 + distance * 100)  # Inverse distance weighting
            bid_depth += qty * weight
        
        # Calculate weighted ask depth
        ask_depth = 0.0
        for price, qty in snapshot.asks[:levels]:
            distance = price - mid
            weight = 1.0 / (1.0 + distance * 100)
            ask_depth += qty * weight
        
        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        
        return (bid_depth - ask_depth) / total
    
    @staticmethod
    def calculate_liquidity_score(snapshot: OrderbookSnapshot) -> float:
        """
        Measure overall liquidity (higher is better for execution).
        """
        total_bid_qty = sum(qty for _, qty in snapshot.bids)
        total_ask_qty = sum(qty for _, qty in snapshot.asks)
        
        # Normalize by spread (tight spread + high volume = high liquidity)
        if snapshot.spread == 0:
            return 0.0
        
        liquidity = (total_bid_qty + total_ask_qty) / (snapshot.spread * 1000)
        return liquidity
    
    @staticmethod
    def calculate_microprice(snapshot: OrderbookSnapshot) -> float:
        """
        Volume-weighted mid price (more accurate than simple mid).
        
        Microprice adjusts mid-price based on orderbook imbalance.
        """
        if not snapshot.bids or not snapshot.asks:
            return snapshot.mid_price
        
        best_bid, bid_qty = snapshot.bids[0]
        best_ask, ask_qty = snapshot.asks[0]
        
        total_qty = bid_qty + ask_qty
        if total_qty == 0:
            return snapshot.mid_price
        
        # Weight prices by opposite side quantity
        microprice = (best_bid * ask_qty + best_ask * bid_qty) / total_qty
        return microprice


class OrderbookFeed:
    """
    Real-time orderbook feed with threading.
    Polls Kalshi API for orderbook updates.
    """
    
    def __init__(
        self,
        client,
        ticker: str,
        poll_interval_ms: int = 500,
        history_size: int = 1000
    ):
        """
        Initialize orderbook feed.
        
        Args:
            client: Kalshi API client
            ticker: Market ticker to monitor
            poll_interval_ms: How often to poll API
            history_size: Number of snapshots to keep
        """
        self.client = client
        self.ticker = ticker
        self.poll_interval = poll_interval_ms / 1000.0
        
        # Thread safety
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        
        # Data storage
        self.snapshots = deque(maxlen=history_size)
        self.current_snapshot: Optional[OrderbookSnapshot] = None
        
        # Derived features
        self.imbalance_history = deque(maxlen=history_size)
        self.depth_imbalance_history = deque(maxlen=history_size)
        self.microprice_history = deque(maxlen=history_size)
        
        self.analyzer = OrderbookAnalyzer()
    
    def start(self):
        """Start the feed thread."""
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            print(f"[OrderbookFeed] Started for {self.ticker}")
    
    def stop(self):
        """Stop the feed thread."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        print(f"[OrderbookFeed] Stopped for {self.ticker}")
    
    def _run(self):
        """Main polling loop."""
        while not self.stop_event.is_set():
            try:
                snapshot = self._fetch_orderbook()
                
                if snapshot:
                    with self.lock:
                        self.current_snapshot = snapshot
                        self.snapshots.append(snapshot)
                        
                        # Calculate and store features
                        imbalance = self.analyzer.calculate_imbalance(snapshot)
                        depth_imb = self.analyzer.calculate_depth_imbalance(snapshot)
                        microprice = self.analyzer.calculate_microprice(snapshot)
                        
                        self.imbalance_history.append(imbalance)
                        self.depth_imbalance_history.append(depth_imb)
                        self.microprice_history.append(microprice)
                
            except Exception as e:
                print(f"[OrderbookFeed Error] {e}")
            
            time.sleep(self.poll_interval)
    
    def _fetch_orderbook(self) -> Optional[OrderbookSnapshot]:
        """
        Fetch orderbook from Kalshi API.
        
        Note: Kalshi API returns orderbook in the market endpoint.
        """
        try:
            # Get market data (includes orderbook)
            resp = self.client.get_market(self.ticker)
            
            if hasattr(resp, "market"):
                market_data = resp.market.model_dump()
            else:
                market_data = resp.model_dump() if hasattr(resp, "model_dump") else resp.__dict__
            
            # Extract orderbook data
            # Kalshi typically provides: yes_bid, yes_ask, no_bid, no_ask
            yes_bid = market_data.get("yes_bid", 0) / 100.0
            yes_ask = market_data.get("yes_ask", 0) / 100.0
            
            # For full orderbook, would need additional API calls
            # For now, use best bid/ask
            bids = [(yes_bid, market_data.get("volume", 0))] if yes_bid > 0 else []
            asks = [(yes_ask, market_data.get("volume", 0))] if yes_ask > 0 else []
            
            snapshot = OrderbookSnapshot(
                timestamp=time.time(),
                ticker=self.ticker,
                best_bid=yes_bid,
                best_ask=yes_ask,
                bids=bids,
                asks=asks,
                mid_price=(yes_bid + yes_ask) / 2,
                spread=yes_ask - yes_bid,
                spread_bps=None  # Will be calculated
            )
            
            return snapshot
            
        except Exception as e:
            print(f"[Orderbook Fetch Error] {e}")
            return None
    
    def get_current_features(self) -> Dict[str, float]:
        """
        Get current orderbook-derived features.
        
        Returns dict with keys:
            - imbalance: Current bid-ask imbalance [-1, 1]
            - depth_imbalance: Depth-weighted imbalance
            - microprice: Volume-weighted mid
            - spread: Current spread
            - spread_bps: Spread in basis points
            - imbalance_ema: Exponential moving average of imbalance
        """
        with self.lock:
            if not self.current_snapshot:
                return self._empty_features()
            
            # Current values
            imbalance = self.imbalance_history[-1] if self.imbalance_history else 0.0
            depth_imb = self.depth_imbalance_history[-1] if self.depth_imbalance_history else 0.0
            microprice = self.microprice_history[-1] if self.microprice_history else 0.0
            
            # Calculate EMA of imbalance (smoothed signal)
            if len(self.imbalance_history) >= 10:
                recent_imbalances = list(self.imbalance_history)[-20:]
                imbalance_ema = self._calculate_ema(recent_imbalances, alpha=0.3)
            else:
                imbalance_ema = imbalance
            
            return {
                'imbalance': imbalance,
                'depth_imbalance': depth_imb,
                'microprice': microprice,
                'spread': self.current_snapshot.spread,
                'spread_bps': self.current_snapshot.spread_bps,
                'imbalance_ema': imbalance_ema,
                'liquidity_score': self.analyzer.calculate_liquidity_score(self.current_snapshot)
            }
    
    def _empty_features(self) -> Dict[str, float]:
        """Return zero features when no data available."""
        return {
            'imbalance': 0.0,
            'depth_imbalance': 0.0,
            'microprice': 0.0,
            'spread': 0.0,
            'spread_bps': 0.0,
            'imbalance_ema': 0.0,
            'liquidity_score': 0.0
        }
    
    @staticmethod
    def _calculate_ema(values: List[float], alpha: float = 0.3) -> float:
        """Calculate exponential moving average."""
        if not values:
            return 0.0
        
        ema = values[0]
        for val in values[1:]:
            ema = alpha * val + (1 - alpha) * ema
        return ema