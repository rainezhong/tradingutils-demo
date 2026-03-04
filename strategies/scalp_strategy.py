"""Scalp Strategy - Scalps on the strong side of selected markets."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .i_strategy import I_Strategy
from .strategy_types import ScalpConfig, Position, Signal
from core.exchange_client import I_ExchangeClient
from core.order_manager import I_OrderManager, OrderRequest, Side, Action, OrderType
from core.market import I_Market

logger = logging.getLogger(__name__)


class ScalpStrategy(I_Strategy):
    """Simple scalping strategy that trades on the strong side.

    "Strong side" is defined as:
    - YES side when yes_bid > strong_side_threshold (default 60%)
    - NO side when no_bid > strong_side_threshold (default 60%)

    The strategy:
    1. Finds markets with a clear directional bias
    2. Joins the bid on the strong side
    3. Takes profit at target or stops out

    Example:
        >>> from core import KalshiExchangeClient, KalshiOrderManager
        >>> client = KalshiExchangeClient.from_env()
        >>> om = KalshiOrderManager(client)
        >>> strategy = ScalpStrategy(client, om, tickers=["KXNBAGAME-..."])
        >>> await strategy.run()
    """

    def __init__(
        self,
        exchange_client: I_ExchangeClient,
        order_manager: I_OrderManager,
        tickers: List[str],
        config: Optional[ScalpConfig] = None,
        dry_run: bool = True,
    ):
        self._client = exchange_client
        self._om = order_manager
        self._tickers = tickers
        self._config = config or ScalpConfig()
        self._dry_run = dry_run

        self._markets: Dict[str, I_Market] = {}
        self._positions: Dict[str, Position] = {}
        self._selected_markets: List[I_Market] = []
        self._running = False
        self._logs: List[str] = []

    # --- I_Strategy Implementation ---

    def market_filter(self, market: Any) -> bool:
        """Filter markets for scalping suitability.

        Checks:
        - Volume >= min_volume
        - Spread <= max_spread_cents
        - Has a strong side (YES or NO >= threshold)
        """
        # Handle both KalshiMarketData and I_Market
        if hasattr(market, "yes_bid"):
            # KalshiMarketData from scanner
            volume = market.volume
            spread = market.yes_ask - market.yes_bid
            yes_bid = market.yes_bid
            yes_ask = market.yes_ask
        else:
            # I_Market from loaded markets
            ob = market.get_current_orderbook("yes")
            volume = ob.current_volume
            spread = ob.spread
            yes_bid = ob.best_bid_yes
            yes_ask = ob.best_ask_yes

        # Volume check
        if volume < self._config.min_volume:
            return False

        # Spread check
        if spread > self._config.max_spread_cents:
            return False

        # Strong side check
        threshold = self._config.strong_side_threshold * 100
        no_bid = 100 - yes_ask
        if yes_bid < threshold and no_bid < threshold:
            return False  # No strong side

        return True

    def get_candidate_markets(self) -> List[Any]:
        """Return all markets that pass volume/spread filters."""
        candidates = []
        for ticker, market in self._markets.items():
            ob = market.get_current_orderbook("yes")
            if ob.current_volume >= self._config.min_volume:
                if ob.spread <= self._config.max_spread_cents:
                    candidates.append(market)
        return candidates

    def get_selected_markets(self) -> List[Any]:
        """Return markets currently selected for trading."""
        return self._selected_markets

    def select_markets(self, markets: List[Any]) -> None:
        """Select markets to actively trade."""
        self._selected_markets = markets
        self.log(f"Selected {len(markets)} markets for trading")

    def get_signal(self, market: I_Market) -> Signal:
        """Determine trading signal for a market."""
        ob = market.get_current_orderbook("yes")
        threshold = self._config.strong_side_threshold * 100  # Convert to cents

        # Check YES side strength
        if ob.best_bid_yes >= threshold:
            edge = ob.best_bid_yes - 50  # Edge over 50%
            strength = min(1.0, edge / 25.0)  # Normalize to 0-1
            return Signal.buy(
                side=Side.YES,
                price_cents=ob.best_bid_yes,
                strength=strength,
                reason=f"YES strong at {ob.best_bid_yes}c",
            )

        # Check NO side strength (100 - yes_ask is the no_bid)
        no_bid = 100 - ob.best_ask_yes
        if no_bid >= threshold:
            edge = no_bid - 50
            strength = min(1.0, edge / 25.0)
            return Signal.buy(
                side=Side.NO,
                price_cents=no_bid,
                strength=strength,
                reason=f"NO strong at {no_bid}c",
            )

        return Signal.no_signal("No strong side detected")

    async def on_tick(self) -> None:
        """Process one tick: update markets, check exits, enter new positions."""
        # Update market data
        await self.refresh_markets()

        # Check existing positions for exit
        await self._check_exits()

        # Look for new entries
        await self._check_entries()

    async def run(self) -> None:
        """Main strategy loop."""
        if not self._client.is_connected:
            await self._client.connect()

        self._running = True
        self.log(
            f"Starting ScalpStrategy on {len(self._tickers)} markets (dry_run={self._dry_run})"
        )

        # CRITICAL: Initialize OMS (cancel stale orders, recover positions)
        if self._om:
            try:
                await self._om.initialize()
                self.log("OMS initialized: stale orders cancelled, positions recovered")
            except Exception as e:
                logger.error(f"OMS initialization failed: {e}")
                # Continue anyway - OMS will track positions from new fills

        # Initial market load
        await self.load_markets()

        # Select markets
        candidates = self.get_candidate_markets()
        self.select_markets(candidates)

        try:
            while self._running:
                await self.on_tick()
                await asyncio.sleep(self._config.tick_interval_seconds)
        except asyncio.CancelledError:
            self.log("Strategy cancelled")
        finally:
            self._running = False
            self.log("Strategy stopped")

    def stop(self) -> None:
        """Stop the strategy loop."""
        self._running = False

    def log(self, message: str) -> None:
        """Log a message with timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] ScalpStrategy: {message}"
        self._logs.append(log_msg)
        logger.info(message)

    # --- Internal Methods ---

    async def load_markets(self) -> None:
        """Load initial market data for all tickers."""
        from core.market import KalshiMarket
        from core.exchange_client import KalshiMarketData

        for ticker in self._tickers:
            try:
                market_data: KalshiMarketData = await self._client.request_market(
                    ticker
                )
                self._markets[ticker] = KalshiMarket(market_data, self._client)
                self.log(f"Loaded market: {ticker}")
            except Exception as e:
                self.log(f"Failed to load {ticker}: {e}")

    async def refresh_markets(self) -> None:
        """Refresh market data for selected markets."""
        for market in self._selected_markets:
            try:
                await market.update_orderbook()
            except Exception as e:
                self.log(f"Failed to update {market.ticker}: {e}")

    async def _check_exits(self) -> None:
        """Check if any positions should be exited."""
        for ticker, pos in list(self._positions.items()):
            market = self._markets.get(ticker)
            if not market:
                continue

            ob = market.get_current_orderbook("yes" if pos.side == Side.YES else "no")
            current_bid = ob.best_bid_yes

            # Calculate P&L
            pnl_cents = current_bid - pos.avg_entry_cents

            # Take profit
            if pnl_cents >= self._config.take_profit_cents:
                await self._exit_position(ticker, pos, "TAKE_PROFIT", current_bid)
                continue

            # Stop loss
            if pnl_cents <= -self._config.stop_loss_cents:
                await self._exit_position(ticker, pos, "STOP_LOSS", current_bid)
                continue

            # Timeout
            if pos.hold_time_seconds >= self._config.max_hold_seconds:
                await self._exit_position(ticker, pos, "TIMEOUT", current_bid)

    async def _check_entries(self) -> None:
        """Check for new entry opportunities."""
        for market in self._selected_markets:
            ticker = market.ticker

            # Skip if already have position
            if ticker in self._positions:
                continue

            # Get signal
            signal = self.get_signal(market)

            if not signal.has_signal:
                continue

            # Check edge meets minimum
            edge = abs(signal.target_price_cents - 50)
            if edge < self._config.min_edge_cents:
                continue

            # Enter position
            await self._enter_position(
                ticker=ticker,
                side=signal.side,
                price_cents=signal.target_price_cents,
                reason=signal.reason,
            )

    async def _enter_position(
        self,
        ticker: str,
        side: Side,
        price_cents: int,
        reason: str,
    ) -> None:
        """Enter a new position."""
        self.log(f"ENTRY: {ticker} {side.value.upper()} @ {price_cents}c - {reason}")

        if self._dry_run:
            # Simulate fill
            self._positions[ticker] = Position(
                ticker=ticker,
                side=side,
                quantity=self._config.order_size,
                avg_entry_cents=price_cents,
                entry_time=datetime.now(),
            )
            return

        # Real order
        order = OrderRequest(
            ticker=ticker,
            side=side,
            size=self._config.order_size,
            action=Action.BUY,
            price_cents=price_cents,
            order_type=OrderType.LIMIT,
        )

        result = await self._om.submit_order(order)
        if result.success:
            self._positions[ticker] = Position(
                ticker=ticker,
                side=side,
                quantity=self._config.order_size,
                avg_entry_cents=price_cents,
                entry_time=datetime.now(),
            )

    async def _exit_position(
        self,
        ticker: str,
        pos: Position,
        reason: str,
        exit_price: int,
    ) -> None:
        """Exit an existing position."""
        pnl = (exit_price - pos.avg_entry_cents) * pos.quantity
        self.log(
            f"EXIT: {ticker} {pos.side.value.upper()} @ {exit_price}c - {reason} (PnL: {pnl}c)"
        )

        if self._dry_run:
            del self._positions[ticker]
            return

        # Real order - sell the position
        order = OrderRequest(
            ticker=ticker,
            side=pos.side,
            size=pos.quantity,
            action=Action.SELL,
            price_cents=exit_price,
            order_type=OrderType.LIMIT,
        )

        result = await self._om.submit_order(order)
        if result.success:
            del self._positions[ticker]

    # --- Properties ---

    @property
    def positions(self) -> Dict[str, Position]:
        """Current open positions."""
        return self._positions.copy()

    @property
    def is_running(self) -> bool:
        """Whether strategy is actively running."""
        return self._running

    def get_logs(self, n: int = 50) -> List[str]:
        """Get recent log entries."""
        return self._logs[-n:]
