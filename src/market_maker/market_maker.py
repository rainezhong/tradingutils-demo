"""Market maker strategy implementation.

This module implements the core market-making logic for Kalshi prediction markets.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..market_making.config import MarketMakerConfig
from ..market_making.constants import MAX_PRICE, MIN_PRICE, SIDE_ASK, SIDE_BID
from ..market_making.models import Fill, MarketState, Position, Quote
from ..core.utils import utc_now


logger = logging.getLogger(__name__)


@dataclass
class MarketMakerState:
    """Internal state tracking for the market maker."""

    quotes_generated: int = 0
    quotes_filled: int = 0
    total_volume: int = 0
    last_quote_time: Optional[datetime] = None
    last_fill_time: Optional[datetime] = None


class MarketMaker:
    """Market maker for a single prediction market.

    Generates two-sided quotes based on market state and inventory,
    with configurable spreads and position limits.

    Attributes:
        ticker: Market identifier.
        config: Strategy configuration.
        position: Current position in the market.

    Example:
        >>> config = MarketMakerConfig(target_spread=0.04, quote_size=20)
        >>> mm = MarketMaker("AAPL-YES", config)
        >>> quotes = mm.generate_quotes(market_state)
        >>> for quote in quotes:
        ...     print(f"{quote.side}: {quote.price:.2f} x {quote.size}")
    """

    def __init__(
        self,
        ticker: str,
        config: Optional[MarketMakerConfig] = None,
    ) -> None:
        """Initialize market maker.

        Args:
            ticker: Market identifier.
            config: Strategy configuration (uses defaults if None).
        """
        if not ticker:
            raise ValueError("ticker cannot be empty")

        self.ticker = ticker
        self.config = config or MarketMakerConfig()
        self.position = Position(
            ticker=ticker,
            contracts=0,
            avg_entry_price=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
        self._state = MarketMakerState()

        logger.info(
            f"MarketMaker initialized for {ticker} with config: "
            f"spread={self.config.target_spread:.1%}, "
            f"size={self.config.quote_size}, "
            f"max_pos={self.config.max_position}"
        )

    def should_quote(self, market: MarketState) -> bool:
        """Determine if we should generate quotes for this market.

        Checks:
        - Market has valid prices (bid < ask)
        - Spread is wide enough (>= min_spread_to_quote)
        - Not at position limits on both sides

        Args:
            market: Current market state.

        Returns:
            True if quoting is appropriate.

        Example:
            >>> if mm.should_quote(market):
            ...     quotes = mm.generate_quotes(market)
        """
        # Validate market data
        if not self._validate_market(market):
            logger.debug(f"{self.ticker}: Invalid market data, not quoting")
            return False

        # Check spread is wide enough
        if market.spread_pct < self.config.min_spread_to_quote:
            logger.debug(
                f"{self.ticker}: Spread {market.spread_pct:.2%} < "
                f"min {self.config.min_spread_to_quote:.2%}, not quoting"
            )
            return False

        # Check if at position limits on both sides
        at_max_long = self.position.contracts >= self.config.max_position
        at_max_short = self.position.contracts <= -self.config.max_position

        if at_max_long and at_max_short:
            # This shouldn't happen, but handle gracefully
            logger.warning(f"{self.ticker}: Invalid position state")
            return False

        if at_max_long:
            logger.debug(
                f"{self.ticker}: At max long ({self.position.contracts}), "
                "can only quote ask"
            )
            # Can still quote ask side
            return True

        if at_max_short:
            logger.debug(
                f"{self.ticker}: At max short ({self.position.contracts}), "
                "can only quote bid"
            )
            # Can still quote bid side
            return True

        logger.debug(f"{self.ticker}: Quoting conditions met")
        return True

    def _validate_market(self, market: MarketState) -> bool:
        """Validate market data is usable.

        Args:
            market: Market state to validate.

        Returns:
            True if market data is valid.
        """
        if market.ticker != self.ticker:
            logger.warning(
                f"Market ticker mismatch: expected {self.ticker}, "
                f"got {market.ticker}"
            )
            return False

        if market.best_bid >= market.best_ask:
            logger.warning(
                f"{self.ticker}: Invalid prices bid={market.best_bid:.3f} "
                f">= ask={market.best_ask:.3f}"
            )
            return False

        if market.best_bid < MIN_PRICE or market.best_ask > MAX_PRICE:
            logger.warning(f"{self.ticker}: Prices outside valid range")
            return False

        return True

    def calculate_fair_value(self, market: MarketState) -> float:
        """Calculate fair value estimate for the market.

        Currently uses mid price. Can be extended with more
        sophisticated models (e.g., microprice, order flow).

        Args:
            market: Current market state.

        Returns:
            Fair value estimate (0-1 range).

        Example:
            >>> fv = mm.calculate_fair_value(market)
            >>> print(f"Fair value: {fv:.3f}")
        """
        fair_value = market.mid_price
        logger.debug(f"{self.ticker}: Fair value = {fair_value:.4f} (mid price)")
        return fair_value

    def calculate_inventory_skew(self) -> float:
        """Calculate price skew based on current inventory.

        When long, returns positive skew to push prices down (encourage selling).
        When short, returns negative skew to push prices up (encourage buying).

        Returns:
            Skew amount to subtract from both bid and ask.

        Example:
            >>> # Long 25 contracts with max 50
            >>> skew = mm.calculate_inventory_skew()
            >>> print(f"Skew: {skew:.4f}")  # Positive, pushes prices down
        """
        if self.config.max_position == 0:
            return 0.0

        # Position as fraction of max (-1 to 1)
        position_pct = self.position.contracts / self.config.max_position

        # Skew is proportional to position
        # Positive position -> positive skew -> lower prices
        skew = position_pct * self.config.inventory_skew_factor

        logger.debug(
            f"{self.ticker}: Inventory skew = {skew:.4f} "
            f"(position={self.position.contracts}, "
            f"max={self.config.max_position}, "
            f"factor={self.config.inventory_skew_factor})"
        )

        return skew

    def calculate_quote_prices(
        self, market: MarketState
    ) -> tuple[float, float]:
        """Calculate bid and ask quote prices.

        Prices are based on:
        - Fair value (mid price)
        - Target spread (half on each side)
        - Edge per side (additional buffer)
        - Inventory skew (adjust based on position)

        Args:
            market: Current market state.

        Returns:
            Tuple of (bid_price, ask_price).

        Example:
            >>> bid, ask = mm.calculate_quote_prices(market)
            >>> print(f"Bid: {bid:.3f}, Ask: {ask:.3f}")
        """
        fair_value = self.calculate_fair_value(market)
        half_spread = self.config.target_spread / 2

        # Base prices with edge
        bid = fair_value - half_spread - self.config.edge_per_side
        ask = fair_value + half_spread + self.config.edge_per_side

        # Apply inventory skew
        skew = self.calculate_inventory_skew()
        bid -= skew
        ask -= skew

        # Round to 3 decimal places
        bid = round(bid, 3)
        ask = round(ask, 3)

        # Clamp to valid range
        bid = max(MIN_PRICE, min(MAX_PRICE, bid))
        ask = max(MIN_PRICE, min(MAX_PRICE, ask))

        # Ensure bid < ask
        if bid >= ask:
            mid = (bid + ask) / 2
            bid = round(mid - 0.005, 3)
            ask = round(mid + 0.005, 3)
            logger.warning(
                f"{self.ticker}: Adjusted overlapping quotes to "
                f"bid={bid:.3f}, ask={ask:.3f}"
            )

        logger.debug(
            f"{self.ticker}: Quote prices bid={bid:.3f}, ask={ask:.3f} "
            f"(fair={fair_value:.3f}, spread={self.config.target_spread:.3f}, "
            f"skew={skew:.4f})"
        )

        return bid, ask

    def calculate_quote_sizes(self) -> tuple[int, int]:
        """Calculate bid and ask quote sizes.

        Sizes are adjusted based on:
        - Base quote size from config
        - Position utilization (reduce as position grows)
        - Direction bias (larger on side that reduces risk)

        Returns:
            Tuple of (bid_size, ask_size).

        Example:
            >>> bid_size, ask_size = mm.calculate_quote_sizes()
            >>> print(f"Bid: {bid_size}, Ask: {ask_size}")
        """
        base_size = self.config.quote_size

        if self.config.max_position == 0:
            return base_size, base_size

        # Position utilization (0 to 1)
        utilization = abs(self.position.contracts) / self.config.max_position
        utilization = min(1.0, utilization)  # Cap at 100%

        # Scale factor decreases as utilization increases
        # At 0% utilization: scale = 1.0
        # At 100% utilization: scale = 0.25
        scale = 1.0 - (utilization * 0.75)

        # Base scaled size
        scaled_size = max(1, int(base_size * scale))

        if self.position.contracts > 0:
            # Long position: larger ask (to sell), smaller bid
            bid_size = max(1, int(scaled_size * 0.5))
            ask_size = scaled_size
        elif self.position.contracts < 0:
            # Short position: larger bid (to buy), smaller ask
            bid_size = scaled_size
            ask_size = max(1, int(scaled_size * 0.5))
        else:
            # Flat: equal sizes
            bid_size = scaled_size
            ask_size = scaled_size

        logger.debug(
            f"{self.ticker}: Quote sizes bid={bid_size}, ask={ask_size} "
            f"(base={base_size}, util={utilization:.1%}, pos={self.position.contracts})"
        )

        return bid_size, ask_size

    def generate_quotes(self, market: MarketState) -> list[Quote]:
        """Generate bid and ask quotes for the market.

        Args:
            market: Current market state.

        Returns:
            List of Quote objects (0-2 quotes depending on conditions).

        Example:
            >>> quotes = mm.generate_quotes(market)
            >>> for q in quotes:
            ...     print(f"{q.side} {q.price:.2f} x {q.size}")
        """
        if not self.should_quote(market):
            logger.info(f"{self.ticker}: Not generating quotes")
            return []

        bid_price, ask_price = self.calculate_quote_prices(market)
        bid_size, ask_size = self.calculate_quote_sizes()

        quotes = []
        now = utc_now()

        # Check if we can quote bid (not at max long)
        can_quote_bid = self.position.contracts < self.config.max_position
        if can_quote_bid and bid_size > 0:
            quotes.append(
                Quote(
                    ticker=self.ticker,
                    side=SIDE_BID,
                    price=bid_price,
                    size=bid_size,
                    timestamp=now,
                )
            )

        # Check if we can quote ask (not at max short)
        can_quote_ask = self.position.contracts > -self.config.max_position
        if can_quote_ask and ask_size > 0:
            quotes.append(
                Quote(
                    ticker=self.ticker,
                    side=SIDE_ASK,
                    price=ask_price,
                    size=ask_size,
                    timestamp=now,
                )
            )

        self._state.quotes_generated += len(quotes)
        self._state.last_quote_time = now

        logger.info(
            f"{self.ticker}: Generated {len(quotes)} quotes: "
            + ", ".join(f"{q.side} {q.price:.3f}x{q.size}" for q in quotes)
        )

        return quotes

    def update_position(self, fill: Fill) -> None:
        """Update position based on a fill.

        Args:
            fill: Completed trade execution.

        Raises:
            ValueError: If fill ticker doesn't match.

        Example:
            >>> mm.update_position(fill)
            >>> print(f"New position: {mm.position.contracts}")
        """
        if fill.ticker != self.ticker:
            raise ValueError(
                f"Fill ticker {fill.ticker} doesn't match MM ticker {self.ticker}"
            )

        old_contracts = self.position.contracts
        old_avg_price = self.position.avg_entry_price

        if fill.side == SIDE_BID:
            # Buying: increase position
            new_contracts = old_contracts + fill.size

            if old_contracts >= 0:
                # Adding to long or going long from flat
                # Update average entry price
                if new_contracts > 0:
                    total_cost = (old_avg_price * old_contracts) + (
                        fill.price * fill.size
                    )
                    new_avg_price = total_cost / new_contracts
                else:
                    new_avg_price = 0.0
            else:
                # Covering short position
                # Realize P&L on covered contracts
                contracts_covered = min(fill.size, abs(old_contracts))
                pnl = contracts_covered * (old_avg_price - fill.price)
                self.position.realized_pnl += pnl

                if new_contracts > 0:
                    # Flipped to long
                    new_avg_price = fill.price
                elif new_contracts < 0:
                    # Still short
                    new_avg_price = old_avg_price
                else:
                    new_avg_price = 0.0

                logger.info(
                    f"{self.ticker}: Realized PnL ${pnl:.2f} "
                    f"covering {contracts_covered} contracts"
                )

        else:  # SIDE_ASK
            # Selling: decrease position
            new_contracts = old_contracts - fill.size

            if old_contracts <= 0:
                # Adding to short or going short from flat
                if new_contracts < 0:
                    total_cost = (old_avg_price * abs(old_contracts)) + (
                        fill.price * fill.size
                    )
                    new_avg_price = total_cost / abs(new_contracts)
                else:
                    new_avg_price = 0.0
            else:
                # Closing long position
                contracts_closed = min(fill.size, old_contracts)
                pnl = contracts_closed * (fill.price - old_avg_price)
                self.position.realized_pnl += pnl

                if new_contracts < 0:
                    # Flipped to short
                    new_avg_price = fill.price
                elif new_contracts > 0:
                    # Still long
                    new_avg_price = old_avg_price
                else:
                    new_avg_price = 0.0

                logger.info(
                    f"{self.ticker}: Realized PnL ${pnl:.2f} "
                    f"closing {contracts_closed} contracts"
                )

        # Update position
        self.position.contracts = new_contracts
        self.position.avg_entry_price = round(new_avg_price, 4)

        # Update state tracking
        self._state.quotes_filled += 1
        self._state.total_volume += fill.size
        self._state.last_fill_time = fill.timestamp

        logger.info(
            f"{self.ticker}: Position updated {old_contracts} -> {new_contracts} "
            f"@ avg {self.position.avg_entry_price:.3f}"
        )

    def calculate_unrealized_pnl(self, current_mid: float) -> float:
        """Calculate and update unrealized P&L.

        Args:
            current_mid: Current mid price (0-1 range).

        Returns:
            Unrealized P&L in dollars.

        Example:
            >>> pnl = mm.calculate_unrealized_pnl(0.55)
            >>> print(f"Unrealized: ${pnl:.2f}")
        """
        if self.position.contracts == 0:
            self.position.unrealized_pnl = 0.0
            return 0.0

        # P&L = contracts * (current - entry)
        # Positive contracts (long): profit if current > entry
        # Negative contracts (short): profit if current < entry
        pnl = self.position.contracts * (
            current_mid - self.position.avg_entry_price
        )

        self.position.unrealized_pnl = round(pnl, 2)

        logger.debug(
            f"{self.ticker}: Unrealized PnL = ${pnl:.2f} "
            f"({self.position.contracts} @ {self.position.avg_entry_price:.3f}, "
            f"current={current_mid:.3f})"
        )

        return self.position.unrealized_pnl

    def get_status(self, market: Optional[MarketState] = None) -> dict:
        """Get current market maker status.

        Args:
            market: Optional market state for current prices.

        Returns:
            Dictionary with status information.

        Example:
            >>> status = mm.get_status(market)
            >>> print(f"Position: {status['position']}")
        """
        if market:
            self.calculate_unrealized_pnl(market.mid_price)

        utilization = 0.0
        if self.config.max_position > 0:
            utilization = abs(self.position.contracts) / self.config.max_position

        status = {
            "ticker": self.ticker,
            "position": {
                "contracts": self.position.contracts,
                "avg_entry_price": self.position.avg_entry_price,
                "unrealized_pnl": self.position.unrealized_pnl,
                "realized_pnl": self.position.realized_pnl,
                "total_pnl": self.position.total_pnl,
            },
            "utilization": round(utilization, 3),
            "at_limit": utilization >= 1.0,
            "stats": {
                "quotes_generated": self._state.quotes_generated,
                "quotes_filled": self._state.quotes_filled,
                "total_volume": self._state.total_volume,
                "last_quote_time": (
                    self._state.last_quote_time.isoformat()
                    if self._state.last_quote_time
                    else None
                ),
                "last_fill_time": (
                    self._state.last_fill_time.isoformat()
                    if self._state.last_fill_time
                    else None
                ),
            },
            "config": {
                "target_spread": self.config.target_spread,
                "quote_size": self.config.quote_size,
                "max_position": self.config.max_position,
            },
        }

        if market:
            status["market"] = {
                "best_bid": market.best_bid,
                "best_ask": market.best_ask,
                "mid_price": market.mid_price,
                "spread_pct": round(market.spread_pct, 4),
            }

        return status

    def reset(self) -> None:
        """Reset market maker to initial state.

        Clears position and statistics.
        """
        self.position = Position(
            ticker=self.ticker,
            contracts=0,
            avg_entry_price=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
        self._state = MarketMakerState()

        logger.info(f"{self.ticker}: Market maker reset")
