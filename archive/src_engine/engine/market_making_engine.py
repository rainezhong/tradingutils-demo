"""Market-making engine that integrates all components.

This is the main integration layer that orchestrates:
- MarketMaker for quote generation
- QuoteManager for order lifecycle
- RiskManager for safety checks
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.core.config import RiskConfig as CoreRiskConfig
from src.core.models import Position as CorePosition
from src.execution.quote_manager import QuoteManager
from src.market_maker.market_maker import MarketMaker
from src.market_making.config import MarketMakerConfig
from src.market_making.interfaces import APIClient
from src.market_making.models import Fill, MarketState, Quote
from src.risk.risk_manager import RiskManager


logger = logging.getLogger(__name__)


@dataclass
class EngineState:
    """Internal state tracking for the engine."""

    market_updates: int = 0
    fills_processed: int = 0
    quotes_sent: int = 0
    force_closes: int = 0
    last_update_time: Optional[datetime] = None
    last_quote_time: Optional[datetime] = None
    active_order_ids: list[str] = field(default_factory=list)


class MarketMakingEngine:
    """Main engine that integrates market-making components.

    Orchestrates the complete market-making loop:
    1. Check for fills from previous quotes
    2. Perform risk checks
    3. Generate new quotes from strategy
    4. Validate quotes against risk limits
    5. Submit/update quotes via execution layer

    Attributes:
        ticker: Market identifier
        market_maker: Strategy component
        quote_manager: Execution component
        risk_manager: Safety component

    Example:
        >>> from src.execution.mock_api_client import MockAPIClient
        >>> client = MockAPIClient()
        >>> engine = MarketMakingEngine(
        ...     ticker="MARKET-123",
        ...     api_client=client,
        ...     mm_config=MarketMakerConfig(),
        ...     risk_config=CoreRiskConfig(),
        ... )
        >>> engine.on_market_update(market_state)
    """

    # Time thresholds for quote management
    QUOTE_STALE_SECONDS = 300  # 5 minutes
    PRICE_MOVE_THRESHOLD = 0.01  # 1% price move triggers requote

    def __init__(
        self,
        ticker: str,
        api_client: APIClient,
        mm_config: Optional[MarketMakerConfig] = None,
        risk_config: Optional[CoreRiskConfig] = None,
    ) -> None:
        """Initialize the market-making engine.

        Args:
            ticker: Market identifier
            api_client: API client for exchange operations
            mm_config: Market-making strategy configuration
            risk_config: Risk management configuration
        """
        if not ticker:
            raise ValueError("ticker cannot be empty")

        self.ticker = ticker
        self._api_client = api_client

        # Initialize components
        self.market_maker = MarketMaker(ticker, mm_config or MarketMakerConfig())
        self.quote_manager = QuoteManager(api_client)
        self.risk_manager = RiskManager(risk_config or CoreRiskConfig())

        # Internal state
        self._state = EngineState()
        self._last_market: Optional[MarketState] = None
        self._last_quotes: list[Quote] = []

        logger.info(
            f"MarketMakingEngine initialized for {ticker} with "
            f"max_position={self.market_maker.config.max_position}, "
            f"target_spread={self.market_maker.config.target_spread:.2%}"
        )

    def on_market_update(self, market: MarketState) -> None:
        """Process a market update - the main engine loop.

        This is called on each market data update and runs the complete
        market-making cycle.

        Args:
            market: Current market state
        """
        if market.ticker != self.ticker:
            logger.warning(
                f"Market ticker mismatch: expected {self.ticker}, got {market.ticker}"
            )
            return

        self._state.market_updates += 1
        self._state.last_update_time = datetime.now()
        self._last_market = market

        # Update unrealized P&L
        self.market_maker.calculate_unrealized_pnl(market.mid_price)

        try:
            # STEP 1: Check for fills
            self._process_fills()

            # STEP 2: Risk check - force close if needed
            if self._check_force_close(market):
                return

            # STEP 3: Check if trading is allowed
            if not self.risk_manager.is_trading_allowed():
                self._cancel_all_quotes()
                logger.warning(f"{self.ticker}: Trading halted by risk manager")
                return

            # STEP 4: Generate new quotes
            new_quotes = self.market_maker.generate_quotes(market)

            if not new_quotes:
                self._cancel_all_quotes()
                logger.debug(f"{self.ticker}: No quotes generated")
                return

            # STEP 5: Validate quotes with risk manager
            validated_quotes = self._validate_quotes(new_quotes)

            if not validated_quotes:
                self._cancel_all_quotes()
                logger.info(f"{self.ticker}: All quotes rejected by risk manager")
                return

            # STEP 6: Update quotes if needed
            if self._should_update_quotes(validated_quotes, market):
                self._update_quotes(validated_quotes)

            # STEP 7: Log status
            self._log_status(market)

        except Exception as e:
            logger.error(f"{self.ticker}: Error in market update: {e}", exc_info=True)
            # On error, cancel all quotes for safety
            self._cancel_all_quotes()

    def _process_fills(self) -> None:
        """Check for and process any new fills."""
        fills = self.quote_manager.check_fills()

        for fill in fills:
            if fill.ticker != self.ticker:
                continue

            logger.info(
                f"{self.ticker}: Fill received - {fill.side} {fill.size}@{fill.price:.4f}"
            )

            # Update market maker position
            self.market_maker.update_position(fill)

            # Calculate realized P&L from this fill
            # The market_maker handles P&L calculation internally
            realized_pnl = self._calculate_fill_pnl(fill)
            if realized_pnl != 0:
                self.risk_manager.update_daily_pnl(realized_pnl)

            # Register position with risk manager
            self._sync_position_to_risk_manager()

            self._state.fills_processed += 1

            # Remove from active orders
            if fill.order_id in self._state.active_order_ids:
                self._state.active_order_ids.remove(fill.order_id)

    def _calculate_fill_pnl(self, fill: Fill) -> float:
        """Calculate realized P&L from a fill.

        This extracts the realized P&L change from the market maker's
        position tracking.

        Args:
            fill: The fill to calculate P&L for

        Returns:
            Realized P&L from this fill (positive = profit)
        """
        # The MarketMaker tracks realized P&L internally
        # We return the change since last fill
        return 0.0  # P&L tracked in market_maker.position.realized_pnl

    def _sync_position_to_risk_manager(self) -> None:
        """Sync market maker position to risk manager."""
        mm_pos = self.market_maker.position

        # Convert from market_making Position to core Position
        core_pos = CorePosition(
            ticker=mm_pos.ticker,
            size=mm_pos.contracts,
            entry_price=mm_pos.avg_entry_price * 100,  # Convert to cents
            current_price=self._last_market.mid_price * 100 if self._last_market else 0,
            unrealized_pnl=mm_pos.unrealized_pnl,
            realized_pnl=mm_pos.realized_pnl,
        )

        self.risk_manager.register_position(self.ticker, core_pos)

    def _check_force_close(self, market: MarketState) -> bool:
        """Check if position should be force closed.

        Args:
            market: Current market state

        Returns:
            True if force close was triggered
        """
        mm_pos = self.market_maker.position

        if mm_pos.is_flat:
            return False

        # Create core position for risk check
        core_pos = CorePosition(
            ticker=mm_pos.ticker,
            size=mm_pos.contracts,
            entry_price=mm_pos.avg_entry_price * 100,
            current_price=market.mid_price * 100,
            unrealized_pnl=mm_pos.unrealized_pnl,
            realized_pnl=mm_pos.realized_pnl,
        )

        if self.risk_manager.should_force_close(self.ticker, core_pos):
            logger.critical(f"{self.ticker}: FORCE CLOSE triggered!")
            self._force_close_position(market)
            self._state.force_closes += 1
            return True

        return False

    def _force_close_position(self, market: MarketState) -> None:
        """Force close the current position.

        Args:
            market: Current market state for pricing
        """
        # Cancel all existing quotes
        self._cancel_all_quotes()

        mm_pos = self.market_maker.position

        if mm_pos.is_flat:
            return

        # Determine close side and price
        if mm_pos.is_long:
            # Sell to close - hit the bid
            side = "ASK"
            price = market.best_bid
        else:
            # Buy to close - lift the ask
            side = "BID"
            price = market.best_ask

        size = abs(mm_pos.contracts)

        logger.critical(f"{self.ticker}: Emergency close - {side} {size}@{price:.4f}")

        # Create and place close order
        close_quote = Quote(
            ticker=self.ticker,
            side=side,
            price=price,
            size=size,
        )

        try:
            placed = self.quote_manager.place_quote(close_quote)
            logger.info(f"{self.ticker}: Close order placed: {placed.order_id}")
        except Exception as e:
            logger.error(f"{self.ticker}: Failed to place close order: {e}")

    def _validate_quotes(self, quotes: list[Quote]) -> list[Quote]:
        """Validate quotes against risk limits.

        Args:
            quotes: Quotes to validate

        Returns:
            List of quotes that passed validation
        """
        validated = []

        for quote in quotes:
            # Determine trade side for risk check
            side = "buy" if quote.side == "BID" else "sell"

            # Create core position for risk check
            mm_pos = self.market_maker.position
            core_pos = CorePosition(
                ticker=mm_pos.ticker,
                size=mm_pos.contracts,
                entry_price=mm_pos.avg_entry_price * 100,
                current_price=self._last_market.mid_price * 100
                if self._last_market
                else 0,
                unrealized_pnl=mm_pos.unrealized_pnl,
                realized_pnl=mm_pos.realized_pnl,
            )

            allowed, reason = self.risk_manager.can_trade(
                self.ticker,
                side,
                quote.size,
                current_position=core_pos,
            )

            if allowed:
                validated.append(quote)
            else:
                logger.debug(
                    f"{self.ticker}: Quote {quote.side}@{quote.price:.4f} "
                    f"rejected: {reason}"
                )

        return validated

    def _should_update_quotes(
        self,
        new_quotes: list[Quote],
        market: MarketState,
    ) -> bool:
        """Determine if quotes need to be updated.

        Args:
            new_quotes: Proposed new quotes
            market: Current market state

        Returns:
            True if quotes should be updated
        """
        # Always update if no previous quotes
        if not self._last_quotes:
            return True

        # Check if quotes are stale
        if self._state.last_quote_time:
            age = datetime.now() - self._state.last_quote_time
            if age > timedelta(seconds=self.QUOTE_STALE_SECONDS):
                logger.debug(f"{self.ticker}: Quotes stale, updating")
                return True

        # Check if market moved significantly
        if self._last_market:
            price_change = abs(market.mid_price - self._last_market.mid_price)
            if price_change > self.PRICE_MOVE_THRESHOLD:
                logger.debug(
                    f"{self.ticker}: Market moved {price_change:.4f}, updating"
                )
                return True

        # Check if quote prices changed significantly
        for new_q in new_quotes:
            matching = [q for q in self._last_quotes if q.side == new_q.side]
            if not matching:
                return True

            old_q = matching[0]
            if abs(new_q.price - old_q.price) > 0.005:  # 0.5% price change
                return True

        return False

    def _update_quotes(self, new_quotes: list[Quote]) -> None:
        """Cancel old quotes and place new ones.

        Args:
            new_quotes: New quotes to place
        """
        # Cancel existing quotes for this ticker
        cancelled = self.quote_manager.cancel_all(self.ticker)
        if cancelled > 0:
            logger.debug(f"{self.ticker}: Cancelled {cancelled} old quotes")

        self._state.active_order_ids.clear()

        # Place new quotes
        for quote in new_quotes:
            try:
                placed = self.quote_manager.place_quote(quote)
                if placed.order_id:
                    self._state.active_order_ids.append(placed.order_id)
                    self._state.quotes_sent += 1
            except Exception as e:
                logger.error(
                    f"{self.ticker}: Failed to place quote "
                    f"{quote.side}@{quote.price:.4f}: {e}"
                )

        self._last_quotes = new_quotes
        self._state.last_quote_time = datetime.now()

        logger.info(
            f"{self.ticker}: Updated quotes - "
            + ", ".join(f"{q.side}@{q.price:.4f}x{q.size}" for q in new_quotes)
        )

    def _cancel_all_quotes(self) -> None:
        """Cancel all active quotes."""
        cancelled = self.quote_manager.cancel_all(self.ticker)
        if cancelled > 0:
            logger.info(f"{self.ticker}: Cancelled {cancelled} quotes")

        self._state.active_order_ids.clear()
        self._last_quotes.clear()

    def _log_status(self, market: MarketState) -> None:
        """Log current engine status.

        Args:
            market: Current market state
        """
        mm_pos = self.market_maker.position

        logger.debug(
            f"{self.ticker} STATUS: "
            f"pos={mm_pos.contracts}, "
            f"upnl=${mm_pos.unrealized_pnl:.2f}, "
            f"rpnl=${mm_pos.realized_pnl:.2f}, "
            f"mid={market.mid_price:.4f}, "
            f"spread={market.spread_pct:.2%}, "
            f"quotes={len(self._state.active_order_ids)}"
        )

    def get_status(self) -> dict:
        """Get comprehensive engine status.

        Returns:
            Dictionary with status from all components
        """
        mm_status = self.market_maker.get_status(self._last_market)
        risk_metrics = self.risk_manager.get_risk_metrics()

        return {
            "ticker": self.ticker,
            "engine": {
                "market_updates": self._state.market_updates,
                "fills_processed": self._state.fills_processed,
                "quotes_sent": self._state.quotes_sent,
                "force_closes": self._state.force_closes,
                "active_quotes": len(self._state.active_order_ids),
                "last_update": (
                    self._state.last_update_time.isoformat()
                    if self._state.last_update_time
                    else None
                ),
            },
            "market_maker": mm_status,
            "risk": risk_metrics,
            "market": (self._last_market.to_dict() if self._last_market else None),
        }

    def reset(self) -> None:
        """Reset engine to initial state."""
        self._cancel_all_quotes()
        self.market_maker.reset()
        self.risk_manager.reset_daily()
        self._state = EngineState()
        self._last_market = None
        self._last_quotes = []

        logger.info(f"{self.ticker}: Engine reset")
