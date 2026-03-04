"""Strategy Runner for the trading dashboard.

Wraps existing strategy code and publishes state updates to the dashboard
via the state aggregator. Supports WebSocket-based real-time orderbook updates.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .state import (
    state_aggregator,
    StrategySession,
    StrategyPosition,
    StrategyTrade,
    GameState,
    MarketState,
    OrderBookLevel,
    StrategyType,
)

logger = logging.getLogger(__name__)


class StrategyRunner:
    """Runs a strategy and publishes state to dashboard."""

    def __init__(self, strategy_type: str, mode: str, config: dict):
        """Initialize strategy runner.

        Args:
            strategy_type: One of "underdog_scalp", "late_game_blowout", "nba_mispricing"
            mode: "paper" or "live"
            config: Strategy-specific configuration
        """
        self.strategy_type = strategy_type
        self.mode = mode
        self.config = config
        self._stop_event = asyncio.Event()
        self._session: Optional[StrategySession] = None

        # WebSocket client for real-time orderbook
        self._ws_client = None
        self._ws_task = None
        self._current_ticker: Optional[str] = None

        # Orderbook state
        self._orderbook_bids: List[OrderBookLevel] = []
        self._orderbook_asks: List[OrderBookLevel] = []

        # Check dependency availability
        self._deps = self._check_dependencies()

    @staticmethod
    def _check_dependencies() -> Dict[str, bool]:
        """Check which optional dependencies are available."""
        deps = {}
        for name, module in [
            ("kalshi_auth", "src.kalshi.auth"),
            ("kalshi_websocket", "src.kalshi.websocket"),
            ("nba_utils", "nba_utils.fetch"),
            ("market_data", "market_data.client"),
            ("kalshi_client", "kalshi_utils.client_wrapper"),
        ]:
            try:
                __import__(module)
                deps[name] = True
            except ImportError:
                deps[name] = False
        return deps

    def _log_dependency_status(self):
        """Log which dependencies loaded and which failed."""
        missing = [name for name, ok in self._deps.items() if not ok]
        available = [name for name, ok in self._deps.items() if ok]

        if available:
            state_aggregator.log_activity(
                strategy="strategy",
                event_type="signal",
                message=f"Loaded: {', '.join(available)}",
            )

        if missing:
            state_aggregator.log_activity(
                strategy="strategy",
                event_type="error",
                message=f"Missing dependencies: {', '.join(missing)} (some features unavailable)",
                details={"missing": missing},
            )

    async def run(self):
        """Main run loop - delegates to strategy-specific implementation."""
        # Initialize session
        self._session = StrategySession(
            strategy_type=self.strategy_type,
            mode=self.mode,
            status="running",
            started_at=datetime.now().isoformat(),
            positions=[],
            trades=[],
            game=None,
            market=None,
            total_pnl=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            trade_count=0,
            win_count=0,
            loss_count=0,
        )

        state_aggregator.set_strategy_session(self._session)

        # Log dependency status
        self._log_dependency_status()

        # NBA strategies share the same poll loop (game state + orderbook)
        nba_strategies = {
            StrategyType.UNDERDOG_SCALP.value,
            StrategyType.LATE_GAME_BLOWOUT.value,
            StrategyType.NBA_MISPRICING.value,
            StrategyType.SPREAD_CAPTURE.value,
            StrategyType.LIQUIDITY_PROVIDER.value,
            StrategyType.DEPTH_SCALPER.value,
            StrategyType.TIED_GAME_SPREAD.value,
            StrategyType.TOTAL_POINTS.value,
        }

        if self.strategy_type in nba_strategies:
            await self._run_nba_strategy()
        elif self.strategy_type == StrategyType.CRYPTO_LATENCY.value:
            await self._run_crypto_latency()
        elif self.strategy_type == StrategyType.ARBITRAGE.value:
            await self._run_arbitrage()
        else:
            state_aggregator.log_activity(
                strategy="strategy",
                event_type="error",
                message=f"Unknown strategy type: {self.strategy_type}",
            )

    async def stop(self):
        """Signal the strategy to stop."""
        self._stop_event.set()
        await self._stop_websocket()
        state_aggregator.clear_strategy_session()

    async def switch_market(self, ticker: str):
        """Switch to monitoring a different market via WebSocket."""
        if ticker == self._current_ticker:
            return

        state_aggregator.log_activity(
            strategy="nba",
            event_type="signal",
            message=f"Switching to market: {ticker}",
        )

        # Stop current WebSocket subscription
        await self._stop_websocket()

        # Start new subscription
        self._current_ticker = ticker
        await self._start_websocket(ticker)

        # Immediately fetch orderbook (don't wait for poll cycle)
        await self._poll_orderbook(ticker)
        await self._poll_game_state()

    async def _start_websocket(self, ticker: str):
        """Start WebSocket subscription for orderbook updates."""
        try:
            from src.kalshi.auth import KalshiAuth
            from src.kalshi.websocket import KalshiWebSocket, WebSocketConfig

            auth = KalshiAuth.from_env()
            config = WebSocketConfig()

            self._ws_client = KalshiWebSocket(auth=auth, config=config)

            # Register callbacks
            self._ws_client.on_orderbook_snapshot(self._on_orderbook_snapshot)
            self._ws_client.on_orderbook_delta(self._on_orderbook_delta)

            # Connect and subscribe
            await self._ws_client.connect()
            await self._ws_client.subscribe("orderbook_delta", ticker)

            state_aggregator.log_activity(
                strategy="nba",
                event_type="signal",
                message=f"WebSocket connected for {ticker}",
            )

        except ImportError as e:
            logger.warning(f"WebSocket not available: {e}")
            self._ws_client = None
            state_aggregator.log_activity(
                strategy="nba",
                event_type="error",
                message="WebSocket not available - using polling",
            )
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self._ws_client = None
            state_aggregator.log_activity(
                strategy="nba",
                event_type="error",
                message=f"WebSocket failed: {str(e)} - using polling",
            )

    async def _stop_websocket(self):
        """Stop WebSocket connection."""
        if self._ws_client:
            try:
                if self._current_ticker:
                    await self._ws_client.unsubscribe(
                        "orderbook_delta", self._current_ticker
                    )
                await self._ws_client.disconnect()
            except Exception as e:
                logger.warning(f"Error stopping WebSocket: {e}")
            finally:
                self._ws_client = None

    def _on_orderbook_snapshot(self, ticker: str, data: dict):
        """Handle orderbook snapshot from WebSocket."""
        try:
            self._update_orderbook_from_data(ticker, data, is_snapshot=True)
        except Exception as e:
            logger.error(f"Error processing orderbook snapshot: {e}")

    def _on_orderbook_delta(self, ticker: str, data: dict):
        """Handle orderbook delta from WebSocket."""
        try:
            self._update_orderbook_from_data(ticker, data, is_snapshot=False)
        except Exception as e:
            logger.error(f"Error processing orderbook delta: {e}")

    def _update_orderbook_from_data(self, ticker: str, data: dict, is_snapshot: bool):
        """Update orderbook state and publish to dashboard."""
        if is_snapshot:
            # Full snapshot - replace orderbook
            yes_bids = data.get("yes", {}).get("bids", []) or []
            yes_asks = data.get("yes", {}).get("asks", []) or []

            self._orderbook_bids = [
                OrderBookLevel(price=float(b[0]), size=int(b[1])) for b in yes_bids
            ]
            self._orderbook_asks = [
                OrderBookLevel(price=float(a[0]), size=int(a[1])) for a in yes_asks
            ]
        else:
            # Delta update - apply changes
            # Kalshi deltas have format: {"price": X, "delta": Y} for each side
            for bid_delta in data.get("yes_bids", []):
                self._apply_delta(self._orderbook_bids, bid_delta, ascending=False)
            for ask_delta in data.get("yes_asks", []):
                self._apply_delta(self._orderbook_asks, ask_delta, ascending=True)

        # Sort orderbooks
        self._orderbook_bids.sort(key=lambda x: x.price, reverse=True)  # Best bid first
        self._orderbook_asks.sort(key=lambda x: x.price)  # Best ask first

        # Calculate summary stats
        best_bid = self._orderbook_bids[0].price if self._orderbook_bids else 0
        best_ask = self._orderbook_asks[0].price if self._orderbook_asks else 100
        spread = best_ask - best_bid

        # Publish to dashboard
        market = MarketState(
            ticker=ticker,
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=data.get("volume", 0),
            last_trade=data.get("last_price"),
            bids=self._orderbook_bids[:10],  # Top 10 levels
            asks=self._orderbook_asks[:10],
        )
        state_aggregator.update_strategy_market(market)

    def _apply_delta(self, book: List[OrderBookLevel], delta: dict, ascending: bool):
        """Apply a delta update to an orderbook side."""
        price = float(delta.get("price", 0))
        size_delta = int(delta.get("delta", 0))

        # Find existing level
        for i, level in enumerate(book):
            if abs(level.price - price) < 0.01:  # Price match
                new_size = level.size + size_delta
                if new_size <= 0:
                    book.pop(i)
                else:
                    book[i] = OrderBookLevel(price=price, size=new_size)
                return

        # New price level
        if size_delta > 0:
            book.append(OrderBookLevel(price=price, size=size_delta))

    # Display names for activity log
    _STRATEGY_NAMES = {
        "underdog_scalp": "Underdog Scalp",
        "late_game_blowout": "Late Game Blowout",
        "nba_mispricing": "NBA Mispricing",
        "spread_capture": "Spread Capture",
        "liquidity_provider": "Liquidity Provider",
        "depth_scalper": "Depth Scalper",
        "tied_game_spread": "Tied Game Spread",
        "total_points": "Total Points O/U",
        "crypto_latency": "Crypto Latency Arb",
        "arbitrage": "Cross-Exchange Arb",
    }

    # Map strategy types to their demo data generators
    _DEMO_DATA_MAP = {
        "late_game_blowout": "_update_demo_blowout_data",
        "nba_mispricing": "_update_demo_mispricing_data",
        "tied_game_spread": "_update_demo_tied_game_data",
        "total_points": "_update_demo_total_points_data",
    }

    async def _run_nba_strategy(self):
        """Run any NBA strategy — they all share the same poll loop."""
        name = self._STRATEGY_NAMES.get(self.strategy_type, self.strategy_type)
        state_aggregator.log_activity(
            strategy="nba",
            event_type="signal",
            message=f"{name} strategy started - select a game to monitor",
        )

        poll_interval = self.config.get("poll_interval", 5)
        demo_method_name = self._DEMO_DATA_MAP.get(
            self.strategy_type, "_update_demo_data"
        )

        while not self._stop_event.is_set():
            try:
                await self._poll_game_state()

                if not self._ws_client and self._current_ticker:
                    await self._poll_orderbook(self._current_ticker)
                elif not self._current_ticker:
                    demo_method = getattr(self, demo_method_name)
                    await demo_method()

            except Exception as e:
                state_aggregator.log_activity(
                    strategy="nba",
                    event_type="error",
                    message=f"Polling error: {str(e)}",
                )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
                break
            except asyncio.TimeoutError:
                pass

    async def _run_crypto_latency(self):
        """Run the crypto latency arbitrage strategy."""
        state_aggregator.log_activity(
            strategy="crypto",
            event_type="signal",
            message="Crypto Latency Arb started",
        )

        poll_interval = self.config.get("poll_interval", 3)

        while not self._stop_event.is_set():
            try:
                await self._update_demo_crypto_data()
            except Exception as e:
                state_aggregator.log_activity(
                    strategy="crypto",
                    event_type="error",
                    message=f"Polling error: {str(e)}",
                )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
                break
            except asyncio.TimeoutError:
                pass

    async def _run_arbitrage(self):
        """Run the cross-exchange arbitrage strategy."""
        state_aggregator.log_activity(
            strategy="arb",
            event_type="signal",
            message="Cross-Exchange Arb started",
        )

        poll_interval = self.config.get("poll_interval", 3)

        while not self._stop_event.is_set():
            try:
                await self._update_demo_arb_data()
            except Exception as e:
                state_aggregator.log_activity(
                    strategy="arb",
                    event_type="error",
                    message=f"Polling error: {str(e)}",
                )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
                break
            except asyncio.TimeoutError:
                pass

    async def _poll_game_state(self):
        """Poll NBA API for live game state."""
        if not self._current_ticker:
            return

        try:
            from nba_utils.fetch import get_nbalive_games

            games = await asyncio.get_event_loop().run_in_executor(
                None, get_nbalive_games
            )

            # Find matching game based on ticker
            # Ticker format: KXNBA-25JAN30-BOSLAL-DH or similar
            for game in games:
                matchup = game.get("matchup", "")
                if matchup in self._current_ticker:
                    # Parse score
                    score_parts = game.get("score", "0 - 0").split(" - ")
                    away_score = int(score_parts[0]) if len(score_parts) > 0 else 0
                    home_score = int(score_parts[1]) if len(score_parts) > 1 else 0

                    # Parse clock (e.g., "Q3 4:21")
                    clock = game.get("clock", "Q1 12:00")
                    quarter = 1
                    time_str = "12:00"
                    if clock.startswith("Q"):
                        parts = clock.split(" ", 1)
                        quarter = int(parts[0][1:]) if len(parts[0]) > 1 else 1
                        time_str = parts[1] if len(parts) > 1 else "12:00"

                    away_code = matchup[:3]
                    home_code = matchup[3:]

                    # Simple model probability based on score
                    diff = home_score - away_score
                    model_prob = 0.5 + (diff / 100)
                    model_prob = max(0.1, min(0.9, model_prob))

                    game_state = GameState(
                        game_id=game.get("id", ""),
                        home_team=home_code,
                        away_team=away_code,
                        home_score=home_score,
                        away_score=away_score,
                        quarter=quarter,
                        clock=time_str,
                        model_prob=model_prob,
                    )
                    state_aggregator.update_strategy_game(game_state)
                    return

        except Exception as e:
            logger.debug(f"Game state poll failed: {e}")

    async def _poll_orderbook(self, ticker: str):
        """Poll orderbook via REST API (fallback when WebSocket unavailable)."""
        try:
            from market_data.client import KalshiPublicClient

            client = KalshiPublicClient()

            # Get full orderbook
            orderbook_resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_orderbook(ticker)
            )

            bids = []
            asks = []
            volume = 0

            if orderbook_resp:
                ob = orderbook_resp.get("orderbook", {})

                # Kalshi format:
                # "yes": [[price_cents, size], ...] - bids for YES (people wanting to buy YES)
                # "no": [[price_cents, size], ...] - bids for NO (equivalent to asks for YES)
                #
                # If someone bids X cents for NO, they're effectively offering to sell YES at (100-X) cents

                yes_orders = ob.get("yes", [])
                no_orders = ob.get("no", [])

                # YES orders are bids (people buying YES)
                for order in yes_orders:
                    if isinstance(order, list) and len(order) >= 2:
                        price = float(order[0])  # Already in cents
                        size = int(order[1])
                        if size > 0:
                            bids.append(OrderBookLevel(price=price, size=size))

                # NO orders become asks for YES (100 - NO price = YES ask price)
                for order in no_orders:
                    if isinstance(order, list) and len(order) >= 2:
                        no_price = float(order[0])
                        size = int(order[1])
                        yes_ask_price = 100 - no_price  # Convert NO bid to YES ask
                        if size > 0 and yes_ask_price > 0:
                            asks.append(OrderBookLevel(price=yes_ask_price, size=size))

            # Also get market data for best bid/ask and volume
            market_resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_market(ticker)
            )

            yes_bid = 0.0
            yes_ask = 100.0

            if market_resp:
                m = market_resp.get("market", {})
                yes_bid = float(m.get("yes_bid", 0) or 0)
                yes_ask = float(m.get("yes_ask", 100) or 100)
                volume = int(m.get("volume", 0) or 0)

            # Sort orderbooks
            bids.sort(key=lambda x: x.price, reverse=True)  # Highest bid first
            asks.sort(key=lambda x: x.price)  # Lowest ask first

            # Use actual best bid/ask from market endpoint if orderbook is empty
            if not bids and yes_bid > 0:
                bids = [OrderBookLevel(price=yes_bid, size=100)]
            if not asks and yes_ask < 100:
                asks = [OrderBookLevel(price=yes_ask, size=100)]

            market_state = MarketState(
                ticker=ticker,
                yes_bid=bids[0].price if bids else yes_bid,
                yes_ask=asks[0].price if asks else yes_ask,
                spread=(asks[0].price if asks else yes_ask)
                - (bids[0].price if bids else yes_bid),
                volume=volume,
                last_trade=None,
                bids=bids[:10],
                asks=asks[:10],
            )
            state_aggregator.update_strategy_market(market_state)

            state_aggregator.log_activity(
                strategy="nba",
                event_type="signal",
                message=f"Orderbook: {ticker} bid={market_state.yes_bid:.0f}c ask={market_state.yes_ask:.0f}c spread={market_state.spread:.0f}c",
            )

        except Exception as e:
            logger.warning(f"Orderbook poll failed for {ticker}: {e}")
            state_aggregator.log_activity(
                strategy="nba",
                event_type="error",
                message=f"Orderbook fetch failed: {str(e)}",
            )

    # ==================== Demo Data Methods ====================

    async def _update_demo_data(self):
        """Update with demo data for UI testing when no live games."""
        import random

        quarter = random.choice([1, 2, 3, 4])
        minutes = random.randint(0, 11)
        seconds = random.randint(0, 59)
        home_score = random.randint(20, 30) * quarter
        away_score = random.randint(18, 32) * quarter

        diff = home_score - away_score
        base_prob = 0.5 + (diff / 100)
        model_prob = max(0.1, min(0.9, base_prob + random.uniform(-0.05, 0.05)))

        game = GameState(
            game_id="demo-game-001",
            home_team="LAL",
            away_team="BOS",
            home_score=home_score,
            away_score=away_score,
            quarter=quarter,
            clock=f"{minutes}:{seconds:02d}",
            model_prob=model_prob,
        )
        state_aggregator.update_strategy_game(game)

        market_mid = model_prob * 100
        spread = random.uniform(1, 3)
        best_bid = market_mid - spread / 2
        best_ask = market_mid + spread / 2

        bids = []
        asks = []
        for i in range(random.randint(5, 8)):
            bid_price = best_bid - i
            bid_size = random.randint(10, 200) * (1 + random.random())
            if bid_price > 0:
                bids.append(
                    OrderBookLevel(price=round(bid_price, 1), size=int(bid_size))
                )

            ask_price = best_ask + i
            ask_size = random.randint(10, 200) * (1 + random.random())
            if ask_price < 100:
                asks.append(
                    OrderBookLevel(price=round(ask_price, 1), size=int(ask_size))
                )

        market = MarketState(
            ticker="KXNBA-LAL-WIN",
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=random.randint(500, 5000),
            last_trade=market_mid + random.uniform(-1, 1),
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

    async def _update_demo_blowout_data(self):
        """Demo data simulating a Q4 blowout scenario."""
        import random

        minutes = random.randint(2, 10)
        seconds = random.randint(0, 59)
        leader_score = random.randint(95, 115)
        trailer_score = leader_score - random.randint(12, 25)

        model_prob = random.uniform(0.88, 0.97)

        game = GameState(
            game_id="demo-blowout-001",
            home_team="MIA",
            away_team="CHI",
            home_score=leader_score,
            away_score=trailer_score,
            quarter=4,
            clock=f"{minutes}:{seconds:02d}",
            model_prob=model_prob,
        )
        state_aggregator.update_strategy_game(game)

        market_mid = model_prob * 100
        spread = random.uniform(1, 2)
        best_bid = market_mid - spread / 2
        best_ask = market_mid + spread / 2

        bids = []
        asks = []
        for i in range(random.randint(4, 6)):
            bid_price = best_bid - i
            if bid_price > 0:
                bids.append(
                    OrderBookLevel(
                        price=round(bid_price, 1), size=random.randint(20, 150)
                    )
                )
            ask_price = best_ask + i
            if ask_price < 100:
                asks.append(
                    OrderBookLevel(
                        price=round(ask_price, 1), size=random.randint(10, 80)
                    )
                )

        market = MarketState(
            ticker="KXNBA-MIA-WIN",
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=random.randint(1000, 8000),
            last_trade=market_mid,
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

    async def _update_demo_mispricing_data(self):
        """Demo data for Q1/Q2 mispricing scenario."""
        import random

        quarter = random.choice([1, 2])
        minutes = random.randint(3, 11)
        seconds = random.randint(0, 59)
        home_score = random.randint(15, 35) + (quarter - 1) * 25
        away_score = random.randint(12, 38) + (quarter - 1) * 25

        diff = home_score - away_score
        model_prob = 0.5 + (diff / 80)
        model_prob = max(0.25, min(0.75, model_prob + random.uniform(-0.08, 0.08)))

        game = GameState(
            game_id="demo-mispricing-001",
            home_team="GSW",
            away_team="PHX",
            home_score=home_score,
            away_score=away_score,
            quarter=quarter,
            clock=f"{minutes}:{seconds:02d}",
            model_prob=model_prob,
        )
        state_aggregator.update_strategy_game(game)

        edge = random.uniform(-5, 5)
        market_mid = (model_prob * 100) - edge
        spread = random.uniform(2, 4)
        best_bid = market_mid - spread / 2
        best_ask = market_mid + spread / 2

        bids = []
        asks = []
        for i in range(random.randint(5, 8)):
            bid_price = best_bid - i * 1.2
            if bid_price > 0:
                bids.append(
                    OrderBookLevel(
                        price=round(bid_price, 1), size=random.randint(15, 120)
                    )
                )
            ask_price = best_ask + i * 1.2
            if ask_price < 100:
                asks.append(
                    OrderBookLevel(
                        price=round(ask_price, 1), size=random.randint(15, 120)
                    )
                )

        market = MarketState(
            ticker="KXNBA-GSW-WIN",
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=random.randint(200, 2000),
            last_trade=market_mid + random.uniform(-2, 2),
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

    async def _update_demo_tied_game_data(self):
        """Demo data for tied game spread strategy."""
        import random

        quarter = random.choice([3, 4])
        minutes = random.randint(2, 10)
        seconds = random.randint(0, 59)
        base_score = random.randint(65, 90)
        # Tied or near-tied
        home_score = base_score + random.randint(-2, 2)
        away_score = base_score + random.randint(-2, 2)

        diff = home_score - away_score
        model_prob = 0.5 + (diff / 60)
        model_prob = max(0.35, min(0.65, model_prob + random.uniform(-0.03, 0.03)))

        game = GameState(
            game_id="demo-tied-001",
            home_team="NYK",
            away_team="BKN",
            home_score=home_score,
            away_score=away_score,
            quarter=quarter,
            clock=f"{minutes}:{seconds:02d}",
            model_prob=model_prob,
        )
        state_aggregator.update_strategy_game(game)

        market_mid = model_prob * 100
        spread = random.uniform(2, 5)
        best_bid = market_mid - spread / 2
        best_ask = market_mid + spread / 2

        bids, asks = [], []
        for i in range(random.randint(4, 7)):
            bp = best_bid - i * 1.5
            if bp > 0:
                bids.append(
                    OrderBookLevel(price=round(bp, 1), size=random.randint(20, 150))
                )
            ap = best_ask + i * 1.5
            if ap < 100:
                asks.append(
                    OrderBookLevel(price=round(ap, 1), size=random.randint(20, 150))
                )

        market = MarketState(
            ticker="KXNBA-NYK-WIN",
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=random.randint(300, 3000),
            last_trade=market_mid + random.uniform(-1, 1),
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

    async def _update_demo_total_points_data(self):
        """Demo data for total points over/under strategy."""
        import random

        quarter = random.choice([2, 3])
        minutes = random.randint(3, 11)
        seconds = random.randint(0, 59)
        home_score = random.randint(20, 35) + (quarter - 1) * 26
        away_score = random.randint(18, 33) + (quarter - 1) * 26
        total = home_score + away_score

        # Over/under line
        line = 220.5
        pace_projected = (
            total * (48 * 60) / max(1, (48 - (quarter - 1) * 12 - (12 - minutes)) * 60)
        )
        over_prob = 0.5 + (pace_projected - line) / 80
        over_prob = max(0.15, min(0.85, over_prob + random.uniform(-0.05, 0.05)))

        game = GameState(
            game_id="demo-totals-001",
            home_team="ATL",
            away_team="DAL",
            home_score=home_score,
            away_score=away_score,
            quarter=quarter,
            clock=f"{minutes}:{seconds:02d}",
            model_prob=over_prob,
        )
        state_aggregator.update_strategy_game(game)

        market_mid = over_prob * 100
        spread = random.uniform(2, 4)
        best_bid = market_mid - spread / 2
        best_ask = market_mid + spread / 2

        bids, asks = [], []
        for i in range(random.randint(4, 7)):
            bp = best_bid - i
            if bp > 0:
                bids.append(
                    OrderBookLevel(price=round(bp, 1), size=random.randint(15, 120))
                )
            ap = best_ask + i
            if ap < 100:
                asks.append(
                    OrderBookLevel(price=round(ap, 1), size=random.randint(15, 120))
                )

        market = MarketState(
            ticker=f"KXNBA-ATLDAL-O{line:.0f}",
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=random.randint(200, 2500),
            last_trade=market_mid + random.uniform(-1, 1),
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

    async def _update_demo_crypto_data(self):
        """Demo data for crypto latency arb."""
        import random

        # Simulate BTC/ETH market
        btc_price = 95000 + random.uniform(-500, 500)
        3200 + random.uniform(-30, 30)

        # Polymarket binary: "BTC above $95,000 at end of day?"
        prob = 0.5 + (btc_price - 95000) / 2000
        prob = max(0.1, min(0.9, prob + random.uniform(-0.03, 0.03)))

        game = GameState(
            game_id="demo-crypto-001",
            home_team="BTC",
            away_team="$95K",
            home_score=int(btc_price),
            away_score=95000,
            quarter=1,
            clock=f"Spot: ${btc_price:,.0f}",
            model_prob=prob,
        )
        state_aggregator.update_strategy_game(game)

        # Simulated prediction market book
        market_mid = prob * 100
        spread = random.uniform(1, 3)
        best_bid = market_mid - spread / 2
        best_ask = market_mid + spread / 2

        bids, asks = [], []
        for i in range(random.randint(3, 6)):
            bp = best_bid - i * 2
            if bp > 0:
                bids.append(
                    OrderBookLevel(price=round(bp, 1), size=random.randint(50, 500))
                )
            ap = best_ask + i * 2
            if ap < 100:
                asks.append(
                    OrderBookLevel(price=round(ap, 1), size=random.randint(50, 500))
                )

        market = MarketState(
            ticker="POLY-BTC-95K-EOD",
            yes_bid=best_bid,
            yes_ask=best_ask,
            spread=spread,
            volume=random.randint(1000, 20000),
            last_trade=market_mid + random.uniform(-1, 1),
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

        # Log latency detection events occasionally
        if random.random() < 0.3:
            edge = random.uniform(0.5, 4.0)
            state_aggregator.log_activity(
                strategy="crypto",
                event_type="decision",
                message=f"Latency edge: Binance=${btc_price:,.0f} vs market={market_mid:.1f}c edge={edge:.1f}c",
                details={"btc_spot": btc_price, "market_mid": market_mid, "edge": edge},
            )

    async def _update_demo_arb_data(self):
        """Demo data for cross-exchange arbitrage."""
        import random

        # Simulate Kalshi vs Polymarket spread
        event = random.choice(
            [
                ("KXNBA-LAL-WIN", "Will Lakers win?"),
                ("KXNBA-BOS-WIN", "Will Celtics win?"),
                ("KXBTC-95K", "BTC above $95K?"),
            ]
        )
        ticker, desc = event

        kalshi_bid = random.uniform(40, 60)
        kalshi_ask = kalshi_bid + random.uniform(1, 3)
        poly_bid = kalshi_bid + random.uniform(-4, 4)
        poly_bid + random.uniform(1, 3)

        # Check for arb
        gross_edge = poly_bid - kalshi_ask
        has_arb = gross_edge > 0

        game = GameState(
            game_id="demo-arb-001",
            home_team="KAL",
            away_team="POLY",
            home_score=int(kalshi_bid),
            away_score=int(poly_bid),
            quarter=1,
            clock=f"Spread: {gross_edge:+.1f}c",
            model_prob=(kalshi_bid + poly_bid) / 200,
        )
        state_aggregator.update_strategy_game(game)

        # Show Kalshi book
        bids, asks = [], []
        for i in range(random.randint(3, 6)):
            bp = kalshi_bid - i * 1.5
            if bp > 0:
                bids.append(
                    OrderBookLevel(price=round(bp, 1), size=random.randint(30, 200))
                )
            ap = kalshi_ask + i * 1.5
            if ap < 100:
                asks.append(
                    OrderBookLevel(price=round(ap, 1), size=random.randint(30, 200))
                )

        market = MarketState(
            ticker=ticker,
            yes_bid=kalshi_bid,
            yes_ask=kalshi_ask,
            spread=kalshi_ask - kalshi_bid,
            volume=random.randint(500, 5000),
            last_trade=kalshi_bid + random.uniform(0, kalshi_ask - kalshi_bid),
            bids=bids,
            asks=asks,
        )
        state_aggregator.update_strategy_market(market)

        if has_arb and random.random() < 0.4:
            state_aggregator.log_activity(
                strategy="arb",
                event_type="signal",
                message=f"Arb detected: buy Kalshi @{kalshi_ask:.1f}c sell Poly @{poly_bid:.1f}c edge={gross_edge:.1f}c",
                details={
                    "ticker": ticker,
                    "kalshi_ask": kalshi_ask,
                    "poly_bid": poly_bid,
                    "edge": gross_edge,
                },
            )

    # ==================== Position/Trade Methods ====================

    def _add_position(
        self, ticker: str, side: str, entry_price: float, size: int
    ) -> StrategyPosition:
        """Add a new position and publish to dashboard."""
        position = StrategyPosition(
            ticker=ticker,
            side=side,
            entry_price=entry_price,
            current_price=entry_price,
            size=size,
            unrealized_pnl=0.0,
            entry_time=datetime.now().isoformat(),
        )
        state_aggregator.update_strategy_position(position)
        return position

    def _update_position_price(self, ticker: str, side: str, current_price: float):
        """Update position's current price and P&L."""
        session = state_aggregator.get_strategy_session()
        if session is None:
            return

        for pos in session.positions:
            if pos.ticker == ticker and pos.side == side:
                if side == "YES":
                    pnl = (current_price - pos.entry_price) * pos.size / 100
                else:
                    pnl = (pos.entry_price - current_price) * pos.size / 100

                updated_pos = StrategyPosition(
                    ticker=pos.ticker,
                    side=pos.side,
                    entry_price=pos.entry_price,
                    current_price=current_price,
                    size=pos.size,
                    unrealized_pnl=pnl,
                    entry_time=pos.entry_time,
                )
                state_aggregator.update_strategy_position(updated_pos)
                break

    def _record_trade(
        self,
        ticker: str,
        side: str,
        action: str,
        price: float,
        size: int,
        pnl: Optional[float] = None,
    ) -> StrategyTrade:
        """Record a trade and publish to dashboard."""
        trade = StrategyTrade(
            trade_id=str(uuid.uuid4())[:8],
            ticker=ticker,
            side=side,
            action=action,
            price=price,
            size=size,
            timestamp=datetime.now().isoformat(),
            pnl=pnl,
        )
        state_aggregator.add_strategy_trade(trade)
        return trade
