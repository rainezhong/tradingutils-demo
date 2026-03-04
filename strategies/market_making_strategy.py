"""Market Making Strategy - Provides liquidity in high-spread markets."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .i_strategy import I_Strategy
from .strategy_types import MarketMakingConfig, Position, Quote, Signal
from core.exchange_client import I_ExchangeClient
from core.order_manager import I_OrderManager, OrderRequest, Side, Action, OrderType
from core.market import I_Market

logger = logging.getLogger(__name__)


class MarketMakingStrategy(I_Strategy):
    """Market making strategy for high-spread prediction markets.

    Generates two-sided quotes (bid and ask) to capture spread.
    Manages inventory with position limits and quote skewing.

    Market Selection:
    - Targets markets with spreads >= min_spread_cents (default 4c)
    - Avoids extremely wide markets (> max_spread_cents)
    - Filters by volume and price range

    Quote Generation:
    - Places bid below mid and ask above mid
    - Applies edge_per_side for additional profit margin
    - Skews quotes based on inventory to manage risk

    Example:
        >>> from core import KalshiExchangeClient, KalshiOrderManager
        >>> client = KalshiExchangeClient.from_env()
        >>> om = KalshiOrderManager(client)
        >>> strategy = MarketMakingStrategy(
        ...     exchange_client=client,
        ...     order_manager=om,
        ...     tickers=["KXNBAGAME-..."],
        ... )
        >>> await strategy.run()
    """

    def __init__(
        self,
        exchange_client: I_ExchangeClient,
        order_manager: I_OrderManager,
        tickers: List[str],
        config: Optional[MarketMakingConfig] = None,
        dry_run: bool = True,
    ):
        self._client = exchange_client
        self._om = order_manager
        self._tickers = tickers
        self._config = config or MarketMakingConfig()
        self._dry_run = dry_run

        self._markets: Dict[str, I_Market] = {}
        self._positions: Dict[str, Position] = {}
        self._active_quotes: Dict[str, List[Quote]] = {}
        self._selected_markets: List[I_Market] = []
        self._running = False
        self._logs: List[str] = []

        # Stats
        self._quotes_generated = 0
        self._quotes_filled = 0
        self._daily_pnl = 0.0

    # --- I_Strategy Implementation ---

    def market_filter(self, market: Any) -> bool:
        """Filter for high-spread markets suitable for market making.

        Accepts markets with:
        - Spread >= min_spread_cents (wide enough to capture)
        - Spread <= max_spread_cents (not too illiquid)
        - Volume >= min_volume
        - Price in acceptable range
        """
        # Handle both KalshiMarketData and I_Market
        if hasattr(market, "yes_bid"):
            # KalshiMarketData from scanner
            yes_bid = market.yes_bid
            yes_ask = market.yes_ask
            volume = market.volume
        else:
            # I_Market
            ob = market.get_current_orderbook("yes")
            yes_bid = ob.best_bid_yes
            yes_ask = ob.best_ask_yes
            volume = ob.current_volume

        spread = yes_ask - yes_bid

        # Spread must be in target range (high enough to profit)
        if spread < self._config.min_spread_cents:
            return False
        if spread > self._config.max_spread_cents:
            return False

        # Volume check
        if volume < self._config.min_volume:
            return False

        # Price range check (avoid extreme probabilities)
        if yes_bid < self._config.min_price_cents:
            return False
        if yes_ask > self._config.max_price_cents:
            return False

        return True

    def get_candidate_markets(self) -> List[Any]:
        """Return markets that pass spread/volume filters."""
        candidates = []
        for ticker, market in self._markets.items():
            if self.market_filter(market):
                candidates.append(market)
        return candidates

    def get_selected_markets(self) -> List[Any]:
        """Return markets currently selected for market making."""
        return self._selected_markets

    def select_markets(self, markets: List[Any]) -> None:
        """Select markets to actively quote."""
        self._selected_markets = markets
        self.log(f"Selected {len(markets)} markets for market making")

    def get_signal(self, market: I_Market) -> Signal:
        """Get signal - for MM, we generate quotes not directional signals.

        Returns a signal indicating whether we should quote this market.
        """
        if not self.market_filter(market):
            return Signal.no_signal("Market doesn't meet MM criteria")

        ob = market.get_current_orderbook("yes")
        spread = ob.spread

        # Check if spread is wide enough to quote
        if spread < self._config.min_spread_cents:
            return Signal.no_signal(f"Spread {spread}c too narrow")

        # Signal strength based on spread width (more spread = better opportunity)
        strength = min(1.0, spread / self._config.max_spread_cents)

        return Signal.buy(
            side=Side.YES,  # Placeholder - MM is two-sided
            price_cents=ob.mid_price,
            strength=strength,
            reason=f"Quoting {spread}c spread",
        )

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

    async def on_tick(self) -> None:
        """Process one tick: update markets, manage quotes, check fills."""
        # Update market data
        await self.refresh_markets()

        # Cancel stale quotes and generate new ones
        for market in self._selected_markets:
            await self._manage_quotes(market)

        # Check for fills and update positions
        await self._check_fills()

        # Check risk limits
        self._check_risk_limits()

    async def run(self) -> None:
        """Main strategy loop."""
        if not self._client.is_connected:
            await self._client.connect()

        self._running = True
        self.log(
            f"Starting MarketMakingStrategy on {len(self._tickers)} markets (dry_run={self._dry_run})"
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
            await self._cancel_all_quotes()
            self._running = False
            self.log("Strategy stopped")

    def stop(self) -> None:
        """Stop the strategy loop."""
        self._running = False

    def log(self, message: str) -> None:
        """Log a message with timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] MarketMaking: {message}"
        self._logs.append(log_msg)
        logger.info(message)

    # --- Market Making Logic ---

    def calculate_fair_value(self, market: I_Market) -> int:
        """Calculate fair value estimate (mid price in cents)."""
        ob = market.get_current_orderbook("yes")
        return ob.mid_price

    def calculate_inventory_skew(self, ticker: str) -> int:
        """Calculate price skew based on inventory position.

        When long: positive skew -> lower prices (encourage selling)
        When short: negative skew -> higher prices (encourage buying)

        Returns skew in cents.
        """
        pos = self._positions.get(ticker)
        if not pos or self._config.max_position == 0:
            return 0

        # Position as fraction of max
        position_pct = pos.quantity / self._config.max_position
        if pos.side == Side.NO:
            position_pct = -position_pct

        # Skew in cents
        skew = int(position_pct * self._config.inventory_skew_factor * 100)
        return skew

    def generate_quotes(self, market: I_Market) -> List[Quote]:
        """Generate bid and ask quotes for a market."""
        ticker = market.ticker
        fair_value = self.calculate_fair_value(market)

        # Calculate half spread in cents
        target_spread_cents = int(self._config.target_spread_pct * 100)
        half_spread = target_spread_cents // 2
        edge = int(self._config.edge_per_side_pct * 100)

        # Base prices
        bid_price = fair_value - half_spread - edge
        ask_price = fair_value + half_spread + edge

        # Apply inventory skew
        skew = self.calculate_inventory_skew(ticker)
        bid_price -= skew
        ask_price -= skew

        # Clamp to valid range
        bid_price = max(1, min(99, bid_price))
        ask_price = max(1, min(99, ask_price))

        # Ensure bid < ask
        if bid_price >= ask_price:
            mid = (bid_price + ask_price) // 2
            bid_price = mid - 1
            ask_price = mid + 1

        # Calculate sizes based on position
        bid_size, ask_size = self._calculate_quote_sizes(ticker)

        quotes = []

        # Check if we can quote bid (not at max long)
        pos = self._positions.get(ticker)
        current_qty = pos.quantity if pos else 0
        if pos and pos.side == Side.NO:
            current_qty = -current_qty

        if current_qty < self._config.max_position and bid_size > 0:
            quotes.append(Quote.bid(ticker, Side.YES, bid_price, bid_size))

        if current_qty > -self._config.max_position and ask_size > 0:
            quotes.append(Quote.ask(ticker, Side.YES, ask_price, ask_size))

        self._quotes_generated += len(quotes)
        return quotes

    def _calculate_quote_sizes(self, ticker: str) -> tuple:
        """Calculate bid and ask sizes based on position."""
        base_size = self._config.quote_size

        pos = self._positions.get(ticker)
        if not pos or self._config.max_position == 0:
            return base_size, base_size

        # Position utilization
        utilization = abs(pos.quantity) / self._config.max_position
        utilization = min(1.0, utilization)

        # Scale down as position grows
        scale = 1.0 - (utilization * 0.75)
        scaled_size = max(1, int(base_size * scale))

        # Bias sizes based on position direction
        is_long = pos.side == Side.YES
        if is_long:
            # Long: larger ask to reduce position
            bid_size = max(1, int(scaled_size * 0.5))
            ask_size = scaled_size
        else:
            # Short: larger bid to reduce position
            bid_size = scaled_size
            ask_size = max(1, int(scaled_size * 0.5))

        return bid_size, ask_size

    async def _manage_quotes(self, market: I_Market) -> None:
        """Cancel old quotes and place new ones."""
        ticker = market.ticker

        # Generate new quotes
        quotes = self.generate_quotes(market)

        if not quotes:
            return

        # Log quotes
        quote_str = ", ".join(
            f"{q.action.value} {q.side.value} {q.price_cents}c x{q.size}"
            for q in quotes
        )
        self.log(f"QUOTES: {ticker} - {quote_str}")

        if self._dry_run:
            # Simulate - just track quotes
            self._active_quotes[ticker] = quotes
            return

        # Real orders
        for quote in quotes:
            order = OrderRequest(
                ticker=ticker,
                side=quote.side,
                size=quote.size,
                action=quote.action,
                price_cents=quote.price_cents,
                order_type=OrderType.LIMIT,
            )
            await self._om.submit_order(order)

        self._active_quotes[ticker] = quotes

    async def _check_fills(self) -> None:
        """Check for filled orders and update positions."""
        if self._dry_run:
            # Simulate fills based on market movement
            await self._simulate_fills()
            return

        # Real: check order manager for fills
        # (Implementation depends on I_OrderManager fill tracking)
        pass

    async def _simulate_fills(self) -> None:
        """Simulate order fills for dry run mode."""
        for ticker, quotes in self._active_quotes.items():
            market = self._markets.get(ticker)
            if not market:
                continue

            ob = market.get_current_orderbook("yes")

            for quote in quotes:
                filled = False

                # Bid fills if market trades through our price
                if quote.action == Action.BUY and ob.best_ask_yes <= quote.price_cents:
                    filled = True

                # Ask fills if market trades through our price
                if quote.action == Action.SELL and ob.best_bid_yes >= quote.price_cents:
                    filled = True

                if filled:
                    self._on_fill(quote)

    def _on_fill(self, quote: Quote) -> None:
        """Handle a filled quote."""
        ticker = quote.ticker
        self._quotes_filled += 1

        self.log(
            f"FILL: {ticker} {quote.action.value} {quote.side.value} {quote.price_cents}c x{quote.size}"
        )

        pos = self._positions.get(ticker)

        if quote.action == Action.BUY:
            # Buying - add to position
            if pos is None:
                self._positions[ticker] = Position(
                    ticker=ticker,
                    side=quote.side,
                    quantity=quote.size,
                    avg_entry_cents=quote.price_cents,
                    entry_time=datetime.now(),
                )
            elif pos.side == quote.side:
                # Adding to same side
                total_cost = (
                    pos.avg_entry_cents * pos.quantity + quote.price_cents * quote.size
                )
                pos.quantity += quote.size
                pos.avg_entry_cents = total_cost // pos.quantity
            else:
                # Covering opposite side
                if quote.size >= pos.quantity:
                    # Closed or flipped
                    pnl = pos.quantity * (quote.price_cents - pos.avg_entry_cents)
                    pos.realized_pnl += pnl
                    self._daily_pnl += pnl
                    remaining = quote.size - pos.quantity
                    if remaining > 0:
                        pos.side = quote.side
                        pos.quantity = remaining
                        pos.avg_entry_cents = quote.price_cents
                    else:
                        del self._positions[ticker]
                else:
                    # Partial cover
                    pnl = quote.size * (quote.price_cents - pos.avg_entry_cents)
                    pos.realized_pnl += pnl
                    self._daily_pnl += pnl
                    pos.quantity -= quote.size
        else:
            # Selling
            if pos is None:
                # Opening short
                self._positions[ticker] = Position(
                    ticker=ticker,
                    side=Side.NO if quote.side == Side.YES else Side.YES,
                    quantity=quote.size,
                    avg_entry_cents=quote.price_cents,
                    entry_time=datetime.now(),
                )
            elif pos.side != quote.side:
                # Adding to short
                total_cost = (
                    pos.avg_entry_cents * pos.quantity + quote.price_cents * quote.size
                )
                pos.quantity += quote.size
                pos.avg_entry_cents = total_cost // pos.quantity
            else:
                # Closing long
                if quote.size >= pos.quantity:
                    pnl = pos.quantity * (quote.price_cents - pos.avg_entry_cents)
                    pos.realized_pnl += pnl
                    self._daily_pnl += pnl
                    remaining = quote.size - pos.quantity
                    if remaining > 0:
                        pos.side = Side.NO if quote.side == Side.YES else Side.YES
                        pos.quantity = remaining
                        pos.avg_entry_cents = quote.price_cents
                    else:
                        del self._positions[ticker]
                else:
                    pnl = quote.size * (quote.price_cents - pos.avg_entry_cents)
                    pos.realized_pnl += pnl
                    self._daily_pnl += pnl
                    pos.quantity -= quote.size

    def _check_risk_limits(self) -> None:
        """Check if risk limits have been breached."""
        # Daily loss limit
        if self._daily_pnl <= -self._config.max_daily_loss * 100:  # Convert to cents
            self.log(f"RISK: Daily loss limit reached ({self._daily_pnl}c)")
            self.stop()
            return

        # Per-position loss limits
        for ticker, pos in list(self._positions.items()):
            if pos.unrealized_pnl <= -self._config.max_loss_per_position * 100:
                self.log(f"RISK: Position loss limit on {ticker}")
                # Would trigger exit here

    async def _cancel_all_quotes(self) -> None:
        """Cancel all active quotes on shutdown."""
        if self._dry_run:
            self._active_quotes.clear()
            return

        # Real: cancel via order manager
        self.log("Cancelling all active quotes")

    # --- Properties ---

    @property
    def positions(self) -> Dict[str, Position]:
        """Current open positions."""
        return self._positions.copy()

    @property
    def is_running(self) -> bool:
        """Whether strategy is actively running."""
        return self._running

    @property
    def daily_pnl(self) -> float:
        """Daily P&L in cents."""
        return self._daily_pnl

    def get_logs(self, n: int = 50) -> List[str]:
        """Get recent log entries."""
        return self._logs[-n:]

    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status."""
        return {
            "running": self._running,
            "dry_run": self._dry_run,
            "markets_selected": len(self._selected_markets),
            "positions": len(self._positions),
            "quotes_generated": self._quotes_generated,
            "quotes_filled": self._quotes_filled,
            "daily_pnl_cents": self._daily_pnl,
            "config": self._config.to_yaml_dict(),
        }
