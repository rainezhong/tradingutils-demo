"""Avellaneda-Stoikov Market-Making Bot.

This module implements a market-making bot using the Avellaneda-Stoikov model
for optimal quote placement with inventory risk management.

Key Features:
- Reservation price skewing based on inventory
- Volatility-adaptive spread sizing
- Configurable risk aversion and horizon
- Dry-run mode for testing without real trades
"""

import math
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from ..core.interfaces import AbstractBot, SpreadQuote
from ..core.exchange import ExchangeClient, Order, OrderBook
from ..core.models import Position, Fill
from ..core.utils import setup_logger

# Dashboard integration (optional - fails gracefully if not available)
try:
    from dashboard.state import state_aggregator
    _DASHBOARD_AVAILABLE = True
except ImportError:
    _DASHBOARD_AVAILABLE = False


logger = setup_logger(__name__)


@dataclass
class ASConfig:
    """Configuration for the Avellaneda-Stoikov bot.
    
    Attributes:
        ticker: Market ticker to trade
        tick: Minimum price increment (in dollars, e.g., 0.01)
        min_price: Minimum valid price (clamp lower bound)
        max_price: Maximum valid price (clamp upper bound)
        max_position: Maximum allowed inventory (contracts)
        gamma: Risk aversion parameter (higher = more inventory-averse)
        k: Liquidity slope in exponential intensity λ(δ) = A * e^(-k*δ)
        horizon_s: Time horizon in seconds for tau calculation
        ewma_alpha: EWMA weight for volatility estimation
        sigma2_floor: Minimum allowed variance estimate
        sigma2_cap: Maximum allowed variance estimate
        quote_size: Size of each quote (contracts)
        dry_run: If True, no real orders are placed
        poll_interval_s: Seconds between market polls
    """
    ticker: str
    tick: float = 0.01
    min_price: float = 0.01
    max_price: float = 0.99
    max_position: int = 10
    gamma: float = 0.05
    k: float = 25.0
    horizon_s: float = 1000.0
    ewma_alpha: float = 0.10
    sigma2_floor: float = 1e-6
    sigma2_cap: float = 1.0
    quote_size: int = 1
    dry_run: bool = True
    poll_interval_s: float = 0.7


@dataclass
class BotState:
    """Internal state tracking for the AS bot."""
    
    # Volatility estimation
    sigma2: float = 1e-6
    last_ref: float = 0.50
    last_ts: Optional[float] = None
    start_ts: Optional[float] = None
    
    # Position and P&L
    position: int = 0
    cash: float = 0.0
    total_fees: float = 0.0  # Track total fees paid
    
    # Order tracking
    active_bid_order_id: Optional[str] = None
    active_ask_order_id: Optional[str] = None
    active_bid_price: Optional[float] = None  # Track our quote prices
    active_ask_price: Optional[float] = None
    
    # Current market state for dry run simulation
    market_bid: Optional[float] = None
    market_ask: Optional[float] = None
    
    # Statistics
    total_buys: int = 0
    total_sells: int = 0
    total_volume: int = 0
    quotes_sent: int = 0
    fills_received: int = 0
    simulated_fills: int = 0  # Track simulated fills in dry run


class ASBot(AbstractBot):
    """Avellaneda-Stoikov market-making bot.
    
    Implements the AbstractBot interface for market-making using the AS model.
    
    The AS model computes:
    - Reservation price: r_t = s_t - q_t * γ * σ² * τ
    - Half spread: Δ_t = (γ * σ² * τ)/2 + (1/γ) * ln(1 + γ/k)
    - Bid: r_t - Δ_t
    - Ask: r_t + Δ_t
    
    Example:
        config = ASConfig(ticker="KXNBAGAME-XXX", dry_run=True)
        exchange = KalshiExchange()
        bot = ASBot(config, exchange)
        bot.start()
        try:
            while bot.running:
                orderbook = exchange.get_market(config.ticker).get_orderbook()
                bot.loop(
                    bid=orderbook.best_bid / 100,
                    ask=orderbook.best_ask / 100,
                    V_bid=orderbook.bid_depth,
                    V_ask=orderbook.ask_depth
                )
                time.sleep(config.poll_interval_s)
        finally:
            bot.stop()
    """
    
    def __init__(
        self,
        config: ASConfig,
        exchange: ExchangeClient,
    ):
        """Initialize the AS bot.
        
        Args:
            config: Bot configuration
            exchange: Exchange client for order placement
        """
        self.config = config
        self.exchange = exchange
        self.state = BotState(
            sigma2=max(config.sigma2_floor, 0.0),
            last_ref=0.50,
        )
        self.running = False
        self._market = None
        
        logger.info(
            f"ASBot initialized for {config.ticker} | "
            f"gamma={config.gamma}, k={config.k}, horizon={config.horizon_s}s, "
            f"max_pos={config.max_position}, dry_run={config.dry_run}"
        )
    
    # ==================== Reference Price ====================
    
    def _compute_microprice(
        self,
        bid: float,
        ask: float,
        V_bid: float,
        V_ask: float,
    ) -> float:
        """Compute microprice from bid/ask and volumes.
        
        Microprice: s_t ≈ (ask * V_bid + bid * V_ask) / (V_bid + V_ask)
        """
        if V_bid + V_ask <= 0:
            return 0.5 * (bid + ask)
        return (ask * V_bid + bid * V_ask) / (V_bid + V_ask)
    
    def _compute_reference(
        self,
        bid: Optional[float],
        ask: Optional[float],
        V_bid: Optional[float],
        V_ask: Optional[float],
    ) -> float:
        """Compute reference price from market data.
        
        Uses microprice if volumes available, else mid, else last reference.
        """
        if bid is None or ask is None or not (ask > bid > 0):
            return self.state.last_ref
        
        if V_bid is not None and V_ask is not None and (V_bid + V_ask) > 0:
            return self._compute_microprice(bid, ask, V_bid, V_ask)
        
        return 0.5 * (bid + ask)
    
    # ==================== Volatility Estimation ====================
    
    def _update_sigma2(self, ref: float, ts: float) -> None:
        """Update volatility estimate using EWMA on instantaneous variance.
        
        σ² ← (1 - α) * σ² + α * (Δs)² / Δt
        """
        if self.state.last_ts is None:
            self.state.last_ts = ts
            self.state.last_ref = ref
            self.state.sigma2 = max(self.config.sigma2_floor, self.state.sigma2)
            return
        
        dt = ts - self.state.last_ts
        if dt <= 0:
            self.state.last_ref = ref
            return
        
        ds = ref - self.state.last_ref
        inst_sigma2 = (ds * ds) / dt
        inst_sigma2 = min(max(inst_sigma2, self.config.sigma2_floor), self.config.sigma2_cap)
        
        a = self.config.ewma_alpha
        self.state.sigma2 = (1.0 - a) * self.state.sigma2 + a * inst_sigma2
        self.state.sigma2 = min(max(self.state.sigma2, self.config.sigma2_floor), self.config.sigma2_cap)
        
        self.state.last_ts = ts
        self.state.last_ref = ref
    
    # ==================== AS Formulas ====================
    
    def _tau(self, ts: float) -> float:
        """Calculate remaining time to horizon."""
        if self.state.start_ts is None:
            self.state.start_ts = ts
        elapsed = ts - self.state.start_ts
        return max(0.0, self.config.horizon_s - elapsed)
    
    def _reservation_price(self, s: float, q: int, sigma2: float, tau: float) -> float:
        """Calculate reservation price: r_t = s_t - q_t * γ * σ² * τ"""
        return s - (q * self.config.gamma * sigma2 * tau)
    
    def _liquidity_term(self) -> float:
        """Calculate liquidity term: (1/γ) * ln(1 + γ/k), with γ→0 limit = 1/k."""
        if self.config.k <= 0:
            return 0.0
        g = self.config.gamma
        if abs(g) < 1e-9:
            return 1.0 / self.config.k
        return (1.0 / g) * math.log(1.0 + (g / self.config.k))
    
    def _half_spread(self, sigma2: float, tau: float) -> float:
        """Calculate half spread: Δ_t = (γ * σ² * τ)/2 + (1/γ)*ln(1 + γ/k)"""
        inv_risk = 0.5 * self.config.gamma * sigma2 * tau
        liq = self._liquidity_term()
        return max(0.0, inv_risk + liq)
    
    # ==================== Price Rounding/Clamping ====================
    
    def _clamp(self, p: float) -> float:
        """Clamp price to valid range."""
        return max(self.config.min_price, min(self.config.max_price, p))
    
    def _floor_to_tick(self, p: float) -> float:
        """Floor to tick (for bids)."""
        t = self.config.tick
        return math.floor(p / t + 1e-12) * t
    
    def _ceil_to_tick(self, p: float) -> float:
        """Ceil to tick (for asks)."""
        t = self.config.tick
        return math.ceil(p / t - 1e-12) * t
    
    # ==================== Fee Calculations ====================
    
    @staticmethod
    def calculate_taker_fee(price: float, contracts: int) -> float:
        """Calculate Kalshi taker fee.
        
        Formula: round_up(0.07 × C × P × (1-P))
        
        Args:
            price: Contract price in dollars (0-1)
            contracts: Number of contracts
            
        Returns:
            Fee in dollars (rounded up to nearest cent)
        """
        fee = 0.07 * contracts * price * (1.0 - price)
        # Round up to nearest cent
        return math.ceil(fee * 100) / 100
    
    @staticmethod
    def calculate_maker_fee(price: float, contracts: int) -> float:
        """Calculate Kalshi maker fee.
        
        Formula: round_up(0.0175 × C × P × (1-P))
        
        Args:
            price: Contract price in dollars (0-1)
            contracts: Number of contracts
            
        Returns:
            Fee in dollars (rounded up to nearest cent)
        """
        fee = 0.0175 * contracts * price * (1.0 - price)
        # Round up to nearest cent
        return math.ceil(fee * 100) / 100
    
    # ==================== Dry Run Fill Simulation ====================
    
    def _simulate_fills(self) -> None:
        """Simulate fills for dry run mode based on market movement.
        
        Simulates fills when:
        1. Our bid >= market ask (crossed spread, immediate fill)
        2. Our ask <= market bid (crossed spread, immediate fill)
        3. Market moved through our quote (our bid >= new ask or our ask <= new bid)
        """
        if not self.config.dry_run:
            return
        
        market_bid = self.state.market_bid
        market_ask = self.state.market_ask
        
        if market_bid is None or market_ask is None:
            return
        
        # Check for bid fill (we get filled when someone sells to us)
        # This happens when the market ask drops to or below our bid
        if (self.state.active_bid_price is not None and 
            self.state.active_bid_order_id is not None):
            if market_ask <= self.state.active_bid_price:
                # Simulated fill at our bid price
                fill_price = self.state.active_bid_price
                fill_qty = self.config.quote_size
                fee = self.calculate_maker_fee(fill_price, fill_qty)
                logger.info(
                    f"[DRY-RUN] SIMULATED BID FILL: {fill_qty} @ {fill_price:.2f} "
                    f"(market ask {market_ask:.2f} <= our bid, fee: ${fee:.4f})"
                )
                self.on_buy_fill(fill_price, fill_qty, is_taker=False)  # Maker fill
                self.state.simulated_fills += 1
                self.state.active_bid_order_id = None
                self.state.active_bid_price = None
        
        # Check for ask fill (we get filled when someone buys from us)
        # This happens when the market bid rises to or above our ask
        if (self.state.active_ask_price is not None and 
            self.state.active_ask_order_id is not None):
            if market_bid >= self.state.active_ask_price:
                # Simulated fill at our ask price
                fill_price = self.state.active_ask_price
                fill_qty = self.config.quote_size
                fee = self.calculate_maker_fee(fill_price, fill_qty)
                logger.info(
                    f"[DRY-RUN] SIMULATED ASK FILL: {fill_qty} @ {fill_price:.2f} "
                    f"(market bid {market_bid:.2f} >= our ask, fee: ${fee:.4f})"
                )
                self.on_sell_fill(fill_price, fill_qty, is_taker=False)  # Maker fill
                self.state.simulated_fills += 1
                self.state.active_ask_order_id = None
                self.state.active_ask_price = None
    
    # ==================== AbstractBot Interface ====================
    
    def update_market(
        self,
        bid: float,
        ask: float,
        V_bid: float,
        V_ask: float,
        ts: float,
    ) -> None:
        """Update market state and volatility estimate.
        
        Args:
            bid: Current best bid price (in dollars, 0-1)
            ask: Current best ask price (in dollars, 0-1)
            V_bid: Current best bid volume
            V_ask: Current best ask volume
            ts: Current timestamp (epoch seconds)
        """
        # Store market state for dry run fill simulation
        self.state.market_bid = bid
        self.state.market_ask = ask
        
        # Check for simulated fills in dry run mode
        if self.config.dry_run:
            self._simulate_fills()
        
        s = self._compute_reference(bid, ask, V_bid, V_ask)
        self._update_sigma2(s, ts)
        
        logger.debug(
            f"Market update: bid={bid:.4f}, ask={ask:.4f}, "
            f"ref={s:.4f}, σ={math.sqrt(self.state.sigma2):.6f}"
        )
    
    def compute_quotes(self, ts: float) -> SpreadQuote:
        """Compute AS quotes for current market state.
        
        Args:
            ts: Current timestamp
            
        Returns:
            SpreadQuote with computed bid/ask prices
        """
        s = self.state.last_ref
        tau = self._tau(ts)
        sigma2 = self.state.sigma2
        sigma = math.sqrt(max(sigma2, 0.0))
        
        q = self.state.position
        r = self._reservation_price(s, q, sigma2, tau)
        d = self._half_spread(sigma2, tau)
        
        raw_bid = r - d
        raw_ask = r + d
        
        # Clamp to valid range
        raw_bid = self._clamp(raw_bid)
        raw_ask = self._clamp(raw_ask)
        
        # Round to tick: bid floors, ask ceils
        bid = self._floor_to_tick(raw_bid)
        ask = self._ceil_to_tick(raw_ask)
        
        # Enforce at least one tick of spread
        if ask <= bid:
            mid_tick = self._clamp(0.5 * (bid + ask))
            bid = self._floor_to_tick(max(self.config.min_price, mid_tick - self.config.tick))
            ask = self._ceil_to_tick(min(self.config.max_price, mid_tick + self.config.tick))
            if ask <= bid:
                bid = max(self.config.min_price, min(self.config.max_price - self.config.tick, bid))
                ask = max(bid + self.config.tick, min(self.config.max_price, ask))
        
        # Inventory constraints: disable one side at limits
        final_bid: Optional[float] = bid
        final_ask: Optional[float] = ask
        
        if self.state.position >= self.config.max_position:
            final_bid = None
            logger.info(f"At max long position ({self.state.position}), disabling bids")
        if self.state.position <= -self.config.max_position:
            final_ask = None
            logger.info(f"At max short position ({self.state.position}), disabling asks")
        
        logger.debug(
            f"Quote computed: bid={final_bid}, ask={final_ask}, "
            f"r={r:.4f}, Δ={d:.4f}, τ={tau:.1f}s, q={q}"
        )
        
        return SpreadQuote(
            bid=final_bid,
            ask=final_ask,
            ref=s,
            reservation=r,
            half_spread=d,
            sigma=sigma,
            tau=tau,
            position=q,
        )
    
    def execute_quote(self, quote: SpreadQuote) -> Tuple[bool, int, bool, int]:
        """Execute the quote by placing/updating orders.
        
        Args:
            quote: SpreadQuote to execute
            
        Returns:
            Tuple of (bid_success, bids_filled, ask_success, asks_filled)
        """
        bid_success = True
        ask_success = True
        bids_filled = 0
        asks_filled = 0
        
        # Cancel existing orders first
        self._cancel_existing_orders()
        
        # Get tradable market
        if self._market is None:
            self._market = self.exchange.get_market(self.config.ticker)
        
        # Place new orders
        if quote.bid is not None:
            bid_success, bids_filled = self._place_bid(quote.bid)

        if quote.ask is not None:
            ask_success, asks_filled = self._place_ask(quote.ask)

        self.state.quotes_sent += (1 if quote.bid else 0) + (1 if quote.ask else 0)

        # Publish state to dashboard
        self.publish_to_dashboard(quote)

        return bid_success, bids_filled, ask_success, asks_filled
    
    def _cancel_existing_orders(self) -> None:
        """Cancel any existing active orders."""
        if self.state.active_bid_order_id:
            try:
                if not self.config.dry_run:
                    self._market.cancel_order(self.state.active_bid_order_id)
                logger.debug(f"Canceled bid order {self.state.active_bid_order_id}")
            except Exception as e:
                # 404 means order was already filled or doesn't exist
                if "404" in str(e):
                    logger.info(f"Bid order {self.state.active_bid_order_id} may have been filled (404 on cancel)")
                    # Check if it was filled by looking at the price
                    if self.state.active_bid_price is not None:
                        self.on_buy_fill(self.state.active_bid_price, self.config.quote_size, is_taker=False)
                else:
                    logger.warning(f"Failed to cancel bid order: {e}")
            self.state.active_bid_order_id = None
            self.state.active_bid_price = None
        
        if self.state.active_ask_order_id:
            try:
                if not self.config.dry_run:
                    self._market.cancel_order(self.state.active_ask_order_id)
                logger.debug(f"Canceled ask order {self.state.active_ask_order_id}")
            except Exception as e:
                # 404 means order was already filled or doesn't exist
                if "404" in str(e):
                    logger.info(f"Ask order {self.state.active_ask_order_id} may have been filled (404 on cancel)")
                    # Check if it was filled by looking at the price
                    if self.state.active_ask_price is not None:
                        self.on_sell_fill(self.state.active_ask_price, self.config.quote_size, is_taker=False)
                else:
                    logger.warning(f"Failed to cancel ask order: {e}")
            self.state.active_ask_order_id = None
            self.state.active_ask_price = None
    
    def _place_bid(self, price: float) -> Tuple[bool, int]:
        """Place a bid order.
        
        Returns:
            Tuple of (success, fills_from_crossing)
        """
        price_cents = int(price * 100)
        size = self.config.quote_size
        
        if self.config.dry_run:
            # Check for immediate fill (crossing the spread)
            simulated_fill = 0
            if self.state.market_ask is not None and price >= self.state.market_ask:
                # Our bid crosses the ask - immediate fill (TAKER)
                fee = self.calculate_taker_fee(price, size)
                logger.info(
                    f"[DRY-RUN] BID CROSSES SPREAD: {price_cents}¢ >= ask {int(self.state.market_ask*100)}¢ -> TAKER FILL (fee: ${fee:.4f})"
                )
                simulated_fill = size
                self.on_buy_fill(price, size, is_taker=True)
                self.state.simulated_fills += 1
            else:
                logger.info(f"[DRY-RUN] Would place BID: {price_cents}¢ x {size}")
                self.state.active_bid_order_id = f"dry-bid-{time.time()}"
                self.state.active_bid_price = price
            return True, simulated_fill
        
        try:
            order = self._market.buy(price=price_cents, size=size)
            self.state.active_bid_order_id = order.order_id
            self.state.active_bid_price = price  # Track for fill detection on 404 cancel
            logger.info(f"Placed BID: {price_cents}¢ x {size} -> {order.order_id}")
            return True, order.filled_size
        except Exception as e:
            logger.error(f"Failed to place bid: {e}")
            return False, 0
    
    def _place_ask(self, price: float) -> Tuple[bool, int]:
        """Place an ask order.
        
        Returns:
            Tuple of (success, fills_from_crossing)
        """
        price_cents = int(price * 100)
        size = self.config.quote_size
        
        if self.config.dry_run:
            # Check for immediate fill (crossing the spread)
            simulated_fill = 0
            if self.state.market_bid is not None and price <= self.state.market_bid:
                # Our ask crosses the bid - immediate fill (TAKER)
                fee = self.calculate_taker_fee(price, size)
                logger.info(
                    f"[DRY-RUN] ASK CROSSES SPREAD: {price_cents}¢ <= bid {int(self.state.market_bid*100)}¢ -> TAKER FILL (fee: ${fee:.4f})"
                )
                simulated_fill = size
                self.on_sell_fill(price, size, is_taker=True)
                self.state.simulated_fills += 1
            else:
                logger.info(f"[DRY-RUN] Would place ASK: {price_cents}¢ x {size}")
                self.state.active_ask_order_id = f"dry-ask-{time.time()}"
                self.state.active_ask_price = price
            return True, simulated_fill
        
        try:
            order = self._market.sell(price=price_cents, size=size)
            self.state.active_ask_order_id = order.order_id
            self.state.active_ask_price = price  # Track for fill detection on 404 cancel
            logger.info(f"Placed ASK: {price_cents}¢ x {size} -> {order.order_id}")
            return True, order.filled_size
        except Exception as e:
            logger.error(f"Failed to place ask: {e}")
            return False, 0
    
    def on_buy_fill(self, price: float, quantity: int, is_taker: bool = False) -> None:
        """Handle a buy fill event.
        
        Args:
            price: Fill price (dollars)
            quantity: Number of contracts filled
            is_taker: Whether this was a taker fill (crossed the spread)
        """
        if quantity <= 0 or price is None:
            return
        
        # Calculate fee (maker by default for limit orders, taker if crossing)
        if is_taker:
            fee = self.calculate_taker_fee(price, quantity)
        else:
            fee = self.calculate_maker_fee(price, quantity)
        
        self.state.position += quantity
        self.state.cash -= quantity * price
        self.state.cash -= fee  # Deduct fee
        self.state.total_fees += fee
        self.state.total_buys += quantity
        self.state.total_volume += quantity
        self.state.fills_received += 1
        
        logger.info(
            f"BUY FILL: {quantity} @ {price:.4f} (fee: ${fee:.4f}) | "
            f"Position: {self.state.position}, Cash: ${self.state.cash:.2f}"
        )
    
    def on_sell_fill(self, price: float, quantity: int, is_taker: bool = False) -> None:
        """Handle a sell fill event.
        
        Args:
            price: Fill price (dollars)
            quantity: Number of contracts filled
            is_taker: Whether this was a taker fill (crossed the spread)
        """
        if quantity <= 0 or price is None:
            return
        
        # Calculate fee (maker by default for limit orders, taker if crossing)
        if is_taker:
            fee = self.calculate_taker_fee(price, quantity)
        else:
            fee = self.calculate_maker_fee(price, quantity)
        
        self.state.position -= quantity
        self.state.cash += quantity * price
        self.state.cash -= fee  # Deduct fee
        self.state.total_fees += fee
        self.state.total_sells += quantity
        self.state.total_volume += quantity
        self.state.fills_received += 1
        
        logger.info(
            f"SELL FILL: {quantity} @ {price:.4f} (fee: ${fee:.4f}) | "
            f"Position: {self.state.position}, Cash: ${self.state.cash:.2f}"
        )
    
    def mtm_pnl(self, ref: Optional[float] = None) -> float:
        """Calculate mark-to-market P&L (after fees).
        
        Args:
            ref: Reference price (uses last known if not provided)
            
        Returns:
            MTM P&L in dollars (fees already deducted from cash)
        """
        s = self.state.last_ref if ref is None else float(ref)
        return self.state.cash + self.state.position * s
    
    def handle_bid_failure(self, bids_filled: int) -> None:
        """Handle a bid execution failure.
        
        Args:
            bids_filled: Number of bids that were filled before failure
        """
        logger.warning(f"Bid execution failed, {bids_filled} filled before failure")
        self.state.active_bid_order_id = None
    
    def handle_ask_failure(self, asks_filled: int) -> None:
        """Handle an ask execution failure.
        
        Args:
            asks_filled: Number of asks that were filled before failure
        """
        logger.warning(f"Ask execution failed, {asks_filled} filled before failure")
        self.state.active_ask_order_id = None
    
    def start(self) -> None:
        """Start the bot."""
        self.running = True
        self.state.start_ts = time.time()
        logger.info(f"ASBot started for {self.config.ticker}")
    
    def stop(self) -> None:
        """Stop the bot and cancel all orders."""
        self.running = False
        self._cancel_existing_orders()
        
        logger.info(
            f"ASBot stopped | "
            f"Buys: {self.state.total_buys}, Sells: {self.state.total_sells}, "
            f"Volume: {self.state.total_volume}, "
            f"Final Position: {self.state.position}, "
            f"MTM P&L: ${self.mtm_pnl():.2f}"
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current bot statistics.

        Returns:
            Dict with current stats
        """
        return {
            "ticker": self.config.ticker,
            "position": self.state.position,
            "cash": self.state.cash,
            "total_fees": self.state.total_fees,
            "mtm_pnl": self.mtm_pnl(),
            "gross_pnl": self.mtm_pnl() + self.state.total_fees,  # P&L before fees
            "total_buys": self.state.total_buys,
            "total_sells": self.state.total_sells,
            "total_volume": self.state.total_volume,
            "quotes_sent": self.state.quotes_sent,
            "fills_received": self.state.fills_received,
            "simulated_fills": self.state.simulated_fills,
            "sigma": math.sqrt(self.state.sigma2),
            "last_ref": self.state.last_ref,
            "running": self.running,
            "dry_run": self.config.dry_run,
        }

    def publish_to_dashboard(self, quote: Optional[SpreadQuote] = None) -> None:
        """Publish current state to the dashboard.

        Args:
            quote: Optional SpreadQuote with reservation price and half spread
        """
        if not _DASHBOARD_AVAILABLE:
            return

        try:
            state_aggregator.publish_mm_state(
                ticker=self.config.ticker,
                position=self.state.position,
                cash=self.state.cash,
                total_fees=self.state.total_fees,
                mtm_pnl=self.mtm_pnl(),
                gross_pnl=self.mtm_pnl() + self.state.total_fees,
                sigma=math.sqrt(self.state.sigma2),
                last_ref=self.state.last_ref,
                active_bid=self.state.active_bid_price,
                active_ask=self.state.active_ask_price,
                reservation_price=quote.reservation if quote else None,
                half_spread=quote.half_spread if quote else None,
                total_volume=self.state.total_volume,
                fills_received=self.state.fills_received,
                running=self.running,
                dry_run=self.config.dry_run,
            )
        except Exception:
            pass
    
    def sync_position_from_exchange(self) -> None:
        """Sync position from exchange (useful on startup)."""
        if self._market is None:
            self._market = self.exchange.get_market(self.config.ticker)
        
        position = self._market.get_position()
        if position:
            self.state.position = position.size
            logger.info(f"Synced position from exchange: {self.state.position}")
        else:
            logger.info("No existing position found on exchange")
    
    def reset_horizon(self) -> None:
        """Reset the time horizon (useful for continuous operation)."""
        self.state.start_ts = time.time()
        logger.info(f"Horizon reset, τ = {self.config.horizon_s}s")
