#!/usr/bin/env python3
"""
Live NBA Market Making Strategy - CLI Runner

Uses the full MarketMakingEngine infrastructure for:
- Intelligent quote updates based on market moves
- Stale quote detection and refresh
- Risk management and force close
- Fill detection and position tracking

Usage:
    python scripts/live_nba_mm.py                    # Dry run (default)
    python scripts/live_nba_mm.py --live             # Live trading (REAL MONEY)
    python scripts/live_nba_mm.py --min-spread 10    # Custom spread threshold
    python scripts/live_nba_mm.py --sport ncaab      # NCAA Basketball (wider spreads)
"""

import argparse
import json
import logging
import os
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_utils.client_wrapper import KalshiWrapped
from kalshi_python_sync import OrdersApi

from src.core.config import RiskConfig
from src.engine.market_making_engine import MarketMakingEngine
from src.market_making.config import MarketMakerConfig
from src.market_making.interfaces import APIClient, OrderError
from src.market_making.models import Fill, MarketState
from src.core.utils import utc_now

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================


class TradingMode(Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class SportType(Enum):
    NBA = "nba"
    NBA_TOTALS = "nba_totals"  # Over/under total points markets
    NCAAB = "ncaab"
    NHL = "nhl"
    UCL = "ucl"
    TENNIS = "tennis"


@dataclass
class MMStrategyConfig:
    """Configuration for live market-making strategy."""

    # Quote parameters (in cents, converted to probability internally)
    min_spread_cents: float = 10.0
    quote_edge_cents: float = 1.0
    quote_size: int = 10
    max_inventory: int = 50
    inventory_skew_factor: float = 0.01

    # Risk management
    max_loss_per_market: float = 20.0
    max_daily_loss: float = 100.0

    # Market move thresholds (used by MarketMakingEngine)
    price_move_threshold: float = 0.01  # 1% triggers requote
    quote_stale_seconds: float = 300.0  # 5 minutes

    # Polling
    poll_interval_seconds: float = 3.0

    # Sport
    sport: SportType = SportType.NBA

    # Single-market mode (Task #7)
    ticker_filter: Optional[Set[str]] = None  # If set, only trade these tickers

    # Logging
    verbose: bool = False

    def to_market_maker_config(self) -> MarketMakerConfig:
        """Convert to MarketMakerConfig."""
        return MarketMakerConfig(
            target_spread=self.min_spread_cents / 100.0,
            edge_per_side=self.quote_edge_cents / 100.0,
            quote_size=self.quote_size,
            max_position=self.max_inventory,
            inventory_skew_factor=self.inventory_skew_factor,
            min_spread_to_quote=self.min_spread_cents / 100.0,
        )

    def to_risk_config(self) -> RiskConfig:
        """Convert to RiskConfig."""
        return RiskConfig(
            max_position_size=self.max_inventory,
            max_loss_per_position=self.max_loss_per_market,
            max_daily_loss=self.max_daily_loss,
        )


# =============================================================================
# RATE LIMITER
# =============================================================================


class RateLimiter:
    """Simple rate limiter to avoid 429 errors."""

    def __init__(self, max_requests: int = 10, window_seconds: float = 1.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Wait if necessary to stay within rate limits."""
        with self._lock:
            now = time.time()

            # Remove timestamps outside the window
            while self._timestamps and self._timestamps[0] < now - self.window_seconds:
                self._timestamps.popleft()

            # If at limit, wait
            if len(self._timestamps) >= self.max_requests:
                sleep_time = self._timestamps[0] + self.window_seconds - now + 0.05
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    # Clean up again after sleeping
                    now = time.time()
                    while (
                        self._timestamps
                        and self._timestamps[0] < now - self.window_seconds
                    ):
                        self._timestamps.popleft()

            self._timestamps.append(time.time())


# =============================================================================
# KALSHI API CLIENT ADAPTER
# =============================================================================


class KalshiAPIClient(APIClient):
    """Kalshi API client implementing the APIClient interface.

    This adapter wraps the Kalshi SDK to work with MarketMakingEngine.
    """

    def __init__(
        self, wrapper: KalshiWrapped, dry_run: bool = True, max_position: int = 50
    ):
        """Initialize Kalshi API client.

        Args:
            wrapper: KalshiWrapped instance for API calls.
            dry_run: If True, simulate orders without placing.
            max_position: Maximum position size per ticker.
        """
        self._wrapper = wrapper
        self._client = wrapper.GetClient()
        self._orders_api = OrdersApi(self._client)
        self._dry_run = dry_run
        self._max_position = max_position

        # Rate limiter to avoid 429 errors (10 requests per second)
        self._rate_limiter = RateLimiter(max_requests=8, window_seconds=1.0)

        # Track orders and positions
        self._mock_orders: Dict[str, dict] = {}
        self._mock_positions: Dict[str, int] = {}
        self._mock_fills: List[Fill] = []
        self._order_counter = 0

        # Live position tracking (synced periodically)
        self._live_positions: Dict[str, int] = {}
        self._last_position_sync: float = 0
        self._position_sync_interval: float = 30.0  # Sync every 30 seconds

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place an order on Kalshi with position limit checking."""
        # Check position limits before placing order
        current_position = self.get_position(ticker)
        projected_position = current_position + (size if side == "BID" else -size)

        if abs(projected_position) > self._max_position:
            logger.warning(
                f"Position limit hit for {ticker}: current={current_position}, "
                f"projected={projected_position}, max={self._max_position}"
            )
            raise OrderError(
                f"Position limit exceeded: {abs(projected_position)} > {self._max_position}"
            )

        if self._dry_run:
            return self._place_mock_order(ticker, side, price, size)

        # Apply rate limiting
        self._rate_limiter.acquire()

        try:
            price_cents = int(round(price * 100))
            price_cents = max(1, min(99, price_cents))

            if side == "BID":
                order_kwargs = {
                    "ticker": ticker,
                    "side": "yes",
                    "action": "buy",
                    "count": size,
                    "type": "limit",
                    "yes_price": price_cents,
                }
            else:  # ASK
                order_kwargs = {
                    "ticker": ticker,
                    "side": "no",
                    "action": "buy",
                    "count": size,
                    "type": "limit",
                    "no_price": 100 - price_cents,
                }

            response = self._orders_api.create_order(**order_kwargs)
            order_id = response.order.order_id

            logger.info(
                f"[LIVE] Placed order: {order_id} {side} {size}x @ ${price:.2f}"
            )
            return order_id

        except Exception as e:
            raise OrderError(f"Failed to place order: {e}")

    def get_position(self, ticker: str) -> int:
        """Get current position for a ticker."""
        if self._dry_run:
            return self._mock_positions.get(ticker, 0)

        # Sync positions periodically
        if time.time() - self._last_position_sync > self._position_sync_interval:
            self._sync_positions()

        return self._live_positions.get(ticker, 0)

    def _sync_positions(self) -> None:
        """Sync positions from Kalshi API."""
        try:
            self._rate_limiter.acquire()
            positions = self._client.get_positions()
            pos_data = positions.model_dump()

            self._live_positions.clear()
            for p in pos_data.get("market_positions", []):
                ticker = p.get("ticker", "")
                pos = p.get("position", 0)
                if pos != 0:
                    self._live_positions[ticker] = pos

            self._last_position_sync = time.time()
            logger.debug(f"Synced {len(self._live_positions)} positions")

        except Exception as e:
            logger.warning(f"Failed to sync positions: {e}")

    def _place_mock_order(self, ticker: str, side: str, price: float, size: int) -> str:
        """Place a mock order for dry run."""
        self._order_counter += 1
        order_id = f"mock_{self._order_counter:06d}"

        self._mock_orders[order_id] = {
            "ticker": ticker,
            "side": side,
            "price": price,
            "size": size,
            "filled_size": 0,
            "status": "open",
            "created_at": datetime.now(),
        }

        logger.info(f"[DRY RUN] Mock order: {order_id} {side} {size}x @ ${price:.2f}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. Handles 404 (already filled/cancelled) gracefully."""
        if self._dry_run:
            if order_id in self._mock_orders:
                self._mock_orders[order_id]["status"] = "cancelled"
                logger.debug(f"[DRY RUN] Cancelled mock order: {order_id}")
                return True
            return False

        # Apply rate limiting
        self._rate_limiter.acquire()

        try:
            self._orders_api.cancel_order(order_id)
            logger.info(f"[LIVE] Cancelled order: {order_id}")
            return True
        except Exception as e:
            error_str = str(e).lower()
            # Handle 404 - order already filled or cancelled
            if "404" in error_str or "not found" in error_str:
                logger.debug(f"Order {order_id} already gone (filled or cancelled)")
                return True  # Not an error - order is no longer active
            # Handle 429 - rate limited
            if "429" in error_str or "too many" in error_str:
                logger.warning(f"Rate limited while cancelling {order_id}, will retry")
                time.sleep(1.0)  # Back off
                return False
            logger.warning(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> dict:
        """Get order status."""
        if self._dry_run:
            if order_id not in self._mock_orders:
                raise OrderError(f"Order not found: {order_id}")

            order = self._mock_orders[order_id]
            return {
                "status": order["status"],
                "filled_size": order["filled_size"],
                "remaining_size": order["size"] - order["filled_size"],
                "avg_fill_price": order["price"] if order["filled_size"] > 0 else None,
            }

        # Apply rate limiting
        self._rate_limiter.acquire()

        try:
            response = self._orders_api.get_order(order_id)
            order = response.order
            # Handle status as enum or string
            status = order.status
            if hasattr(status, "value"):
                status = status.value
            status = str(status).lower()
            return {
                "status": status,
                "filled_size": order.fill_count
                or 0,  # SDK uses fill_count, not filled_count
                "remaining_size": order.remaining_count or 0,
                "avg_fill_price": None,  # Kalshi doesn't provide this directly
            }
        except Exception as e:
            error_str = str(e).lower()
            # Handle 404 - order not found (may have been filled and purged)
            if "404" in error_str or "not found" in error_str:
                return {
                    "status": "unknown",
                    "filled_size": 0,
                    "remaining_size": 0,
                    "avg_fill_price": None,
                }
            # Handle 429 - rate limited
            if "429" in error_str or "too many" in error_str:
                logger.warning(f"Rate limited getting order status for {order_id}")
                time.sleep(0.5)
            raise OrderError(f"Failed to get order status: {e}")

    def get_market_data(self, ticker: str) -> MarketState:
        """Get current market state."""
        # Apply rate limiting
        self._rate_limiter.acquire()

        try:
            # Use raw API call to avoid SDK validation errors with null fields (Task #8)
            import requests
            from src.kalshi.auth import KalshiAuth

            auth = KalshiAuth.from_env()
            path = f"/trade-api/v2/markets/{ticker}"
            headers = auth.sign_request("GET", path, "")
            headers["Content-Type"] = "application/json"

            url = f"https://api.elections.kalshi.com{path}"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("market", {})

            # Extract prices (in cents, convert to 0-1)
            yes_bid = (data.get("yes_bid") or 0) / 100.0
            yes_ask = (data.get("yes_ask") or 100) / 100.0

            # Ensure bid < ask
            if yes_bid >= yes_ask:
                yes_bid = yes_ask - 0.01
            if yes_bid <= 0:
                yes_bid = 0.01

            mid_price = (yes_bid + yes_ask) / 2

            return MarketState(
                ticker=ticker,
                timestamp=utc_now(),
                best_bid=yes_bid,
                best_ask=yes_ask,
                mid_price=mid_price,
                bid_size=100,  # Not provided by API
                ask_size=100,
            )

        except Exception as e:
            raise OrderError(f"Failed to get market data for {ticker}: {e}")

    def get_positions(self) -> Dict[str, int]:
        """Get all positions."""
        if self._dry_run:
            return dict(self._mock_positions)

        # Sync if stale
        if time.time() - self._last_position_sync > self._position_sync_interval:
            self._sync_positions()

        return dict(self._live_positions)

    def get_fills(self, ticker: Optional[str] = None, limit: int = 100) -> List[Fill]:
        """Get recent fills (Task #6: fill tracking)."""
        if self._dry_run:
            fills = self._mock_fills
            if ticker:
                fills = [f for f in fills if f.ticker == ticker]
            return fills[-limit:]

        # Live mode - fetch from Kalshi fills endpoint
        self._rate_limiter.acquire()
        try:
            response = self._client.get_fills(limit=limit)
            fills_data = response.model_dump()

            fills = []
            for f in fills_data.get("fills", []):
                fill_ticker = f.get("ticker", "")
                if ticker and ticker != fill_ticker:
                    continue

                fills.append(
                    Fill(
                        order_id=f.get("order_id", ""),
                        ticker=fill_ticker,
                        side="BID" if f.get("action") == "buy" else "ASK",
                        price=(f.get("yes_price", 0) or 0) / 100.0,
                        size=f.get("count", 0),
                        timestamp=utc_now(),
                    )
                )

            return fills

        except Exception as e:
            logger.warning(f"Failed to fetch fills: {e}")
            return []

    def simulate_fill(self, order_id: str, fill_size: int) -> None:
        """Simulate a fill for dry run mode."""
        if not self._dry_run or order_id not in self._mock_orders:
            return

        order = self._mock_orders[order_id]
        remaining = order["size"] - order["filled_size"]
        actual_fill = min(fill_size, remaining)

        if actual_fill > 0:
            order["filled_size"] += actual_fill
            if order["filled_size"] >= order["size"]:
                order["status"] = "filled"
            else:
                order["status"] = "partial"

            fill = Fill(
                order_id=order_id,
                ticker=order["ticker"],
                side=order["side"],
                price=order["price"],
                size=actual_fill,
                timestamp=utc_now(),
            )
            self._mock_fills.append(fill)

            # Update mock position
            delta = actual_fill if order["side"] == "BID" else -actual_fill
            self._mock_positions[order["ticker"]] = (
                self._mock_positions.get(order["ticker"], 0) + delta
            )


# =============================================================================
# MARKET SCANNER
# =============================================================================


@dataclass
class MarketInfo:
    """Information about a tradeable market."""

    ticker: str
    event_ticker: str
    team: str
    yes_bid: float
    yes_ask: float
    spread_cents: float
    volume: int


class MarketScanner:
    """Scans for markets meeting MM criteria."""

    def __init__(self, wrapper: KalshiWrapped, sport: SportType):
        self._wrapper = wrapper
        self._sport = sport

    def get_markets(self) -> List[MarketInfo]:
        """Get all open markets for the configured sport."""
        try:
            if self._sport == SportType.NBA:
                raw_markets = self._wrapper.GetAllNBAMarkets(status="open")
            elif self._sport == SportType.NBA_TOTALS:
                raw_markets = self._wrapper.GetAllNBATotalMarkets(status="open")
            elif self._sport == SportType.NCAAB:
                raw_markets = self._wrapper.GetALLNCAAMBMarkets(status="open")
            elif self._sport == SportType.NHL:
                raw_markets = self._wrapper.GetAllNHLMarkets(status="open")
            elif self._sport == SportType.UCL:
                raw_markets = self._wrapper.GetAllUCLMarkets(status="open")
            elif self._sport == SportType.TENNIS:
                raw_markets = self._wrapper.GetALLTennisMarkets(status="open")
            else:
                raw_markets = []

            markets = []
            for m in raw_markets:
                data = m.model_dump() if hasattr(m, "model_dump") else m.__dict__

                yes_bid = (data.get("yes_bid") or 0) / 100.0
                yes_ask = (data.get("yes_ask") or 100) / 100.0
                spread = (yes_ask - yes_bid) * 100

                # Skip invalid markets
                if yes_bid <= 0 or yes_ask <= 0 or yes_bid >= yes_ask:
                    continue

                markets.append(
                    MarketInfo(
                        ticker=data.get("ticker", ""),
                        event_ticker=data.get("event_ticker", ""),
                        team=data.get("yes_sub_title", "").split()[0]
                        if data.get("yes_sub_title")
                        else "",
                        yes_bid=yes_bid,
                        yes_ask=yes_ask,
                        spread_cents=spread,
                        volume=data.get("volume", 0),
                    )
                )

            return markets

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def filter_wide_spread(
        self, markets: List[MarketInfo], min_spread: float
    ) -> List[MarketInfo]:
        """Filter markets with spread >= min_spread cents."""
        return [m for m in markets if m.spread_cents >= min_spread]

    def filter_by_tickers(
        self, markets: List[MarketInfo], tickers: Optional[Set[str]]
    ) -> List[MarketInfo]:
        """Filter markets to only include specified tickers (Task #7: single-market mode)."""
        if not tickers:
            return markets
        return [m for m in markets if m.ticker in tickers or m.event_ticker in tickers]


# =============================================================================
# MAIN ENGINE ORCHESTRATOR
# =============================================================================


class LiveMMOrchestrator:
    """Orchestrates multiple MarketMakingEngines for live trading."""

    def __init__(
        self,
        config: MMStrategyConfig,
        mode: TradingMode,
        log_dir: str = "data/mm_trades",
    ):
        self.config = config
        self.mode = mode
        self._dry_run = mode == TradingMode.DRY_RUN

        # Initialize Kalshi connection
        logger.info("Connecting to Kalshi API...")
        self._wrapper = KalshiWrapped()
        balance = self._wrapper.GetBalance()
        logger.info(f"Connected! Balance: ${balance:.2f}")

        # Create API client adapter with position limits
        self._api_client = KalshiAPIClient(
            self._wrapper, dry_run=self._dry_run, max_position=config.max_inventory
        )

        # Market scanner
        self._scanner = MarketScanner(self._wrapper, config.sport)

        # Active engines per ticker
        self._engines: Dict[str, MarketMakingEngine] = {}

        # Configs
        self._mm_config = config.to_market_maker_config()
        self._risk_config = config.to_risk_config()

        # Logging
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"mm_session_{self.session_id}.jsonl"

        # Stats
        self._poll_count = 0
        self._start_time = datetime.now()

    def _log_event(self, event_type: str, data: dict):
        """Log an event to file."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "mode": self.mode.value,
            "type": event_type,
            **data,
        }
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to log event: {e}")

    def _get_or_create_engine(self, ticker: str) -> MarketMakingEngine:
        """Get existing engine or create new one for ticker."""
        if ticker not in self._engines:
            logger.info(f"Creating MarketMakingEngine for {ticker}")

            engine = MarketMakingEngine(
                ticker=ticker,
                api_client=self._api_client,
                mm_config=self._mm_config,
                risk_config=self._risk_config,
            )

            # Configure engine thresholds
            engine.QUOTE_STALE_SECONDS = self.config.quote_stale_seconds
            engine.PRICE_MOVE_THRESHOLD = self.config.price_move_threshold

            self._engines[ticker] = engine

            self._log_event("engine_created", {"ticker": ticker})

        return self._engines[ticker]

    def _remove_engine(self, ticker: str):
        """Remove and cleanup an engine."""
        if ticker in self._engines:
            engine = self._engines[ticker]
            engine.reset()
            del self._engines[ticker]
            logger.info(f"Removed engine for {ticker}")

    def _poll_cycle(self):
        """Single polling cycle."""
        self._poll_count += 1

        # Get markets
        markets = self._scanner.get_markets()

        # Apply ticker filter if set (Task #7: single-market mode)
        if self.config.ticker_filter:
            markets = self._scanner.filter_by_tickers(
                markets, self.config.ticker_filter
            )

        wide_markets = self._scanner.filter_wide_spread(
            markets, self.config.min_spread_cents
        )

        if self.config.verbose:
            filter_msg = (
                f" (filtered to {len(self.config.ticker_filter)} tickers)"
                if self.config.ticker_filter
                else ""
            )
            logger.info(
                f"Poll #{self._poll_count}: {len(markets)} markets{filter_msg}, "
                f"{len(wide_markets)} with spread >= {self.config.min_spread_cents}c"
            )

        active_tickers = set()

        # Update engines for wide-spread markets
        for market in wide_markets:
            ticker = market.ticker
            active_tickers.add(ticker)

            try:
                # Get fresh market state
                market_state = self._api_client.get_market_data(ticker)

                # Get or create engine
                engine = self._get_or_create_engine(ticker)

                # Run the engine update cycle (handles requoting, fills, risk)
                engine.on_market_update(market_state)

                if self.config.verbose:
                    status = engine.get_status()
                    pos = status["market_maker"]["position"]["contracts"]
                    pnl = status["market_maker"]["position"]["total_pnl"]
                    quotes = status["engine"]["active_quotes"]
                    logger.debug(
                        f"  {ticker}: pos={pos} pnl=${pnl:.2f} quotes={quotes}"
                    )

            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}")

        # Remove engines for markets that no longer qualify
        stale_tickers = set(self._engines.keys()) - active_tickers
        for ticker in stale_tickers:
            logger.info(f"Market {ticker} no longer qualifies, removing engine")
            self._remove_engine(ticker)

    def print_status(self):
        """Print current status summary."""
        runtime = (datetime.now() - self._start_time).total_seconds()

        print("\n" + "=" * 70)
        print(
            f"MM Status @ {datetime.now().strftime('%H:%M:%S')} | Runtime: {runtime / 60:.1f}m"
        )
        print("=" * 70)
        print(
            f"Mode: {self.mode.value.upper()} | Sport: {self.config.sport.value.upper()}"
        )
        print(
            f"Min Spread: {self.config.min_spread_cents}c | Max Inventory: {self.config.max_inventory}"
        )
        print(f"Active Engines: {len(self._engines)}")
        print("-" * 70)

        total_pnl = 0.0
        total_position = 0

        for ticker, engine in self._engines.items():
            status = engine.get_status()
            mm = status["market_maker"]
            eng = status["engine"]

            pos = mm["position"]["contracts"]
            pnl = mm["position"]["total_pnl"]
            quotes = eng["active_quotes"]
            fills = eng["fills_processed"]

            total_pnl += pnl
            total_position += abs(pos)

            market = status.get("market", {})
            spread = market.get("spread_pct", 0) * 100 if market else 0

            print(
                f"  {ticker[:45]:<45} | "
                f"Pos: {pos:+4d} | "
                f"P&L: ${pnl:+7.2f} | "
                f"Quotes: {quotes} | "
                f"Fills: {fills} | "
                f"Spread: {spread:.1f}c"
            )

        print("-" * 70)
        print(f"TOTAL: Position={total_position} | P&L=${total_pnl:+.2f}")
        print("=" * 70 + "\n")

    def run(self):
        """Main run loop."""
        logger.info("=" * 60)
        logger.info("MARKET MAKING STRATEGY (MarketMakingEngine)")
        logger.info("=" * 60)
        logger.info(f"Mode: {self.mode.value.upper()}")
        logger.info(f"Sport: {self.config.sport.value.upper()}")
        logger.info("Config:")
        logger.info(f"  Min spread: {self.config.min_spread_cents:.1f} cents")
        logger.info(f"  Max inventory: {self.config.max_inventory}")
        logger.info(f"  Quote edge: {self.config.quote_edge_cents:.1f} cents")
        logger.info(
            f"  Price move threshold: {self.config.price_move_threshold * 100:.1f}%"
        )
        logger.info(f"  Quote stale after: {self.config.quote_stale_seconds}s")
        logger.info(f"  Max loss/market: ${self.config.max_loss_per_market:.0f}")
        logger.info("=" * 60)
        logger.info("\nStarting main loop (Ctrl+C to stop)...\n")

        last_status_time = time.time()
        status_interval = 30.0

        try:
            while True:
                self._poll_cycle()

                # Print status periodically
                if time.time() - last_status_time > status_interval:
                    self.print_status()
                    last_status_time = time.time()

                time.sleep(self.config.poll_interval_seconds)

        except KeyboardInterrupt:
            logger.info("\n\nShutting down...")
            self._shutdown()

    def _shutdown(self):
        """Graceful shutdown - cancel all quotes."""
        logger.info("Cancelling all active quotes...")

        for ticker, engine in self._engines.items():
            try:
                engine.reset()
                logger.info(f"  Reset engine for {ticker}")
            except Exception as e:
                logger.error(f"  Error resetting {ticker}: {e}")

        self.print_status()
        logger.info("Shutdown complete.")


# =============================================================================
# POSITION CLOSING (Task #5)
# =============================================================================


def close_all_positions(wrapper: KalshiWrapped) -> None:
    """Close all open positions gracefully.

    1. Cancel all resting orders first to free capital
    2. Close positions starting with smallest exposure
    3. Handle insufficient balance by waiting for fills
    """
    logger.info("=" * 60)
    logger.info("CLOSING ALL POSITIONS")
    logger.info("=" * 60)

    rate_limiter = RateLimiter(max_requests=5, window_seconds=1.0)

    # Step 1: Cancel all resting orders
    logger.info("\nStep 1: Cancelling all resting orders...")
    try:
        rate_limiter.acquire()
        orders = wrapper.GetClient().get_orders(status="resting")
        order_data = orders.model_dump()
        resting = order_data.get("orders", [])
        logger.info(f"Found {len(resting)} resting orders")

        cancelled = 0
        for o in resting:
            oid = o.get("order_id")
            if oid:
                try:
                    rate_limiter.acquire()
                    wrapper.GetClient().cancel_order(oid)
                    cancelled += 1
                except Exception:
                    pass  # Ignore errors (may already be filled)
        logger.info(f"Cancelled {cancelled} orders")
    except Exception as e:
        logger.warning(f"Error cancelling orders: {e}")

    time.sleep(2)  # Wait for cancels to process

    # Step 2: Get positions
    logger.info("\nStep 2: Fetching positions...")
    try:
        rate_limiter.acquire()
        positions = wrapper.GetClient().get_positions()
        pos_data = positions.model_dump()
    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return

    # Collect positions to close
    to_close = []
    for p in pos_data.get("market_positions", []):
        pos = p.get("position", 0)
        ticker = p.get("ticker", "")
        exposure = float(p.get("market_exposure_dollars", 0))
        if pos != 0:
            to_close.append((exposure, pos, ticker))

    if not to_close:
        logger.info("No open positions to close!")
        return

    # Sort by exposure (smallest first to free up capital)
    to_close.sort()

    logger.info(f"\nStep 3: Closing {len(to_close)} positions (smallest first)...")

    import requests
    from src.kalshi.auth import KalshiAuth

    auth = KalshiAuth.from_env()
    host = "https://api.elections.kalshi.com"

    for exposure, pos, ticker in to_close:
        size = abs(pos)

        # Get market price
        try:
            rate_limiter.acquire()
            path = f"/trade-api/v2/markets/{ticker}"
            headers = auth.sign_request("GET", path, "")
            headers["Content-Type"] = "application/json"
            resp = requests.get(f"{host}{path}", headers=headers)
            m = resp.json().get("market", {})

            if pos > 0:
                price = m.get("yes_bid", 1)
                action = "sell"
                logger.info(f"{ticker}: SELL {size} @ {price}c (closing long)")
            else:
                price = m.get("yes_ask", 99)
                action = "buy"
                logger.info(f"{ticker}: BUY {size} @ {price}c (closing short)")

            # Place close order
            import json

            order_body = {
                "ticker": ticker,
                "side": "yes",
                "action": action,
                "type": "limit",
                "count": size,
                "yes_price": price,
            }

            rate_limiter.acquire()
            body_str = json.dumps(order_body)
            path = "/trade-api/v2/portfolio/orders"
            headers = auth.sign_request("POST", path, body_str)
            headers["Content-Type"] = "application/json"

            resp = requests.post(f"{host}{path}", headers=headers, data=body_str)

            if resp.status_code in [200, 201]:
                logger.info("  -> Order placed")
            else:
                err = resp.json().get("error", {}).get("message", resp.text[:60])
                logger.warning(f"  -> Failed: {err}")

        except Exception as e:
            logger.error(f"  -> Error: {e}")

        time.sleep(0.5)

    logger.info("\nDone! Positions should close as orders fill.")
    logger.info("Run again to check remaining positions.")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Live Market Making Strategy (with MarketMakingEngine)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/live_nba_mm.py                    # Dry run NBA
  python scripts/live_nba_mm.py --live             # Live trading
  python scripts/live_nba_mm.py --sport ncaab      # NCAA Basketball (wider spreads)
  python scripts/live_nba_mm.py --min-spread 5     # Lower spread threshold

Sports with best MM potential (by avg spread):
  ncaab  - NCAA Basketball (9.1c avg, many 10-29c spreads)
  ucl    - Champions League Soccer (6.2c avg)
  nhl    - NHL (2.2c avg)
  nba    - NBA (1.6c avg)
  tennis - ATP Tennis (1.2c avg)
        """,
    )

    # Mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run", action="store_true", default=True, help="Dry run mode (default)"
    )
    mode_group.add_argument(
        "--live", action="store_true", help="Live trading mode (REAL MONEY)"
    )

    # Sport selection
    parser.add_argument(
        "--sport",
        type=str,
        default="nba",
        choices=["nba", "nba_totals", "ncaab", "nhl", "ucl", "tennis"],
        help="Sport to trade (default: nba). nba_totals = over/under point spreads",
    )

    # Single-market mode (Task #7)
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Only trade specific ticker(s), comma-separated (e.g., KXWTAMATCH-26FEB02KARBUC-KAR)",
    )

    # Strategy parameters
    parser.add_argument(
        "--min-spread",
        type=float,
        default=10.0,
        help="Minimum spread in cents (default: 10.0)",
    )
    parser.add_argument(
        "--max-inventory", type=int, default=50, help="Maximum inventory (default: 50)"
    )
    parser.add_argument(
        "--quote-edge",
        type=float,
        default=1.0,
        help="Quote edge in cents (default: 1.0)",
    )
    parser.add_argument(
        "--quote-size", type=int, default=10, help="Contracts per quote (default: 10)"
    )

    # Engine parameters
    parser.add_argument(
        "--price-move-threshold",
        type=float,
        default=1.0,
        help="Price move %% to trigger requote (default: 1.0)",
    )
    parser.add_argument(
        "--quote-stale-seconds",
        type=float,
        default=300.0,
        help="Seconds before quotes are stale (default: 300)",
    )

    # Risk
    parser.add_argument(
        "--max-loss-market",
        type=float,
        default=20.0,
        help="Max loss per market in USD (default: 20.0)",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=100.0,
        help="Max daily loss in USD (default: 100.0)",
    )

    # Polling
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="Poll interval in seconds (default: 3.0)",
    )

    # Misc
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    # Position management (Task #5)
    parser.add_argument(
        "--close-positions",
        action="store_true",
        help="Close all open positions and exit (requires --live)",
    )

    args = parser.parse_args()

    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle --close-positions (Task #5)
    if args.close_positions:
        if not args.live:
            logger.error("--close-positions requires --live mode")
            return 1
        logger.warning("CLOSING POSITIONS - REAL MONEY OPERATION!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            logger.info("Aborted.")
            return 1
        wrapper = KalshiWrapped()
        close_all_positions(wrapper)
        return 0

    # Determine mode
    if args.live:
        mode = TradingMode.LIVE
        logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            logger.info("Aborted.")
            return 1
    else:
        mode = TradingMode.DRY_RUN

    # Parse ticker filter (Task #7)
    ticker_filter = None
    if args.ticker:
        ticker_filter = set(t.strip() for t in args.ticker.split(","))
        logger.info(f"Single-market mode: filtering to {len(ticker_filter)} ticker(s)")

    # Build config
    config = MMStrategyConfig(
        min_spread_cents=args.min_spread,
        quote_edge_cents=args.quote_edge,
        quote_size=args.quote_size,
        max_inventory=args.max_inventory,
        inventory_skew_factor=0.01,
        max_loss_per_market=args.max_loss_market,
        max_daily_loss=args.max_daily_loss,
        price_move_threshold=args.price_move_threshold / 100.0,
        quote_stale_seconds=args.quote_stale_seconds,
        poll_interval_seconds=args.poll_interval,
        sport=SportType(args.sport),
        ticker_filter=ticker_filter,
        verbose=args.verbose,
    )

    # Run orchestrator
    orchestrator = LiveMMOrchestrator(config, mode)
    orchestrator.run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
