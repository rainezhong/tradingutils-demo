"""Abstract interfaces for trading system components.

These protocols define the contracts that implementations must follow,
enabling dependency injection and easy testing with mocks/simulators.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .models import Fill, MarketState, Quote


@dataclass
class SpreadQuote:
    """Quote data for spread/market-making bots."""
    bid: Optional[float]     # dollars, None => do not quote bid
    ask: Optional[float]     # dollars, None => do not quote ask
    ref: float               # s_t reference price
    reservation: float       # r_t
    half_spread: float       # Δ_t
    sigma: float             # instantaneous vol estimate (per sqrt(second))
    tau: float               # time-to-horizon in seconds
    position: int            # q_t


class DataProvider(ABC):
    """Abstract interface for market data providers.

    Implementations can be live API connections, historical data replayers,
    or market simulators for testing.
    """

    @abstractmethod
    def get_market_state(self, ticker: str) -> MarketState:
        """Get current market state for a ticker.

        Args:
            ticker: Market identifier

        Returns:
            Current MarketState

        Raises:
            ValueError: If ticker is invalid or not found
        """
        pass

    @abstractmethod
    def generate_market_state(self) -> MarketState:
        """Generate or fetch the next market state.

        For simulators, this advances the simulation.
        For live feeds, this fetches fresh data.

        Returns:
            New MarketState
        """
        pass


class APIClient(ABC):
    """Abstract interface for trading API clients.

    Implementations can be live API connections or simulated APIs for testing.
    """

    @abstractmethod
    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place an order in the market.

        Args:
            ticker: Market identifier
            side: Order side ('buy' or 'sell', or 'BID' or 'ASK')
            price: Limit price
            size: Number of contracts

        Returns:
            Order ID string

        Raises:
            ValueError: If parameters are invalid
            RuntimeError: If order placement fails
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order.

        Args:
            order_id: ID of order to cancel

        Returns:
            True if canceled successfully, False if order not found or already filled

        Raises:
            RuntimeError: If cancellation fails
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Get current status of an order.

        Args:
            order_id: ID of order to check

        Returns:
            Dictionary with order status:
            {
                'order_id': str,
                'status': 'OPEN' | 'FILLED' | 'PARTIALLY_FILLED' | 'CANCELED',
                'filled_size': int,
                'remaining_size': int,
                'fills': List[dict]  # Fill details if any
            }

        Raises:
            ValueError: If order_id not found
        """
        pass

    @abstractmethod
    def get_market_data(self, ticker: str) -> MarketState:
        """Get current market data for a ticker.

        Args:
            ticker: Market identifier

        Returns:
            Current MarketState

        Raises:
            ValueError: If ticker is invalid or not found
        """
        pass


class OrderManager(ABC):
    """Abstract interface for order management.

    Handles quote lifecycle, fill tracking, and position updates.
    """

    @abstractmethod
    def submit_quote(self, quote: Quote) -> str:
        """Submit a new quote to the market.

        Args:
            quote: Quote to submit

        Returns:
            Order ID assigned to the quote
        """
        pass

    @abstractmethod
    def cancel_quote(self, order_id: str) -> bool:
        """Cancel an existing quote.

        Args:
            order_id: ID of quote to cancel

        Returns:
            True if canceled, False if not found or already filled
        """
        pass

    @abstractmethod
    def get_active_quotes(self, ticker: Optional[str] = None) -> list[Quote]:
        """Get all active quotes, optionally filtered by ticker.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            List of active Quote objects
        """
        pass

    @abstractmethod
    def get_fills(self, ticker: Optional[str] = None) -> list[Fill]:
        """Get all fills, optionally filtered by ticker.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            List of Fill objects
        """
        pass

    @abstractmethod
    def process_fill(self, fill: Fill) -> None:
        """Process a fill event.

        Updates quote status and position tracking.

        Args:
            fill: Fill to process
        """
        pass


class AbstractBot(ABC):
    """Abstract base class for market-making/spread bots.

    Defines the interface for bots that quote markets and handle fills.
    """

    @abstractmethod
    def update_market(self, bid: float, ask: float, V_bid: float, V_ask: float, ts: float) -> None:
        """Update market state.

        Args:
            bid: Current best bid price
            ask: Current best ask price
            V_bid: Current best bid volume
            V_ask: Current best ask volume
            ts: Current timestamp
        """
        pass

    @abstractmethod
    def compute_quotes(self, ts: float) -> SpreadQuote:
        """Compute quotes for the current market state.

        Args:
            ts: Current timestamp

        Returns:
            SpreadQuote with bid/ask prices and metadata
        """
        pass

    @abstractmethod
    def execute_quote(self, quote: SpreadQuote) -> tuple[bool, int, bool, int]:
        """Execute a quote in the market.

        Args:
            quote: SpreadQuote to execute

        Returns:
            Tuple of (bid_success, bids_filled, ask_success, asks_filled)
        """
        pass

    @abstractmethod
    def on_buy_fill(self, price: float, quantity: int, is_taker: bool = False) -> None:
        """Handle a buy fill event.

        Args:
            price: Fill price
            quantity: Number of contracts filled
            is_taker: Whether this was a taker fill (crossed the spread)
        """
        pass

    @abstractmethod
    def on_sell_fill(self, price: float, quantity: int, is_taker: bool = False) -> None:
        """Handle a sell fill event.

        Args:
            price: Fill price
            quantity: Number of contracts filled
            is_taker: Whether this was a taker fill (crossed the spread)
        """
        pass

    @abstractmethod
    def mtm_pnl(self, ref: float) -> float:
        """Calculate mark-to-market PnL.

        Args:
            ref: Reference price for MTM calculation

        Returns:
            Current MTM PnL in dollars
        """
        pass

    @abstractmethod
    def handle_bid_failure(self, bids_filled: int) -> None:
        """Handle a bid execution failure.

        Args:
            bids_filled: Number of bids that were filled before failure
        """
        pass

    @abstractmethod
    def handle_ask_failure(self, asks_filled: int) -> None:
        """Handle an ask execution failure.

        Args:
            asks_filled: Number of asks that were filled before failure
        """
        pass

    @abstractmethod
    def start(self) -> None:
        """Start the bot."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the bot."""
        pass

    def loop(self, bid: float, ask: float, V_bid: float, V_ask: float) -> None:
        """Main bot loop iteration.

        Updates market state, computes quotes, executes them, and handles fills.

        Args:
            bid: Current best bid price
            ask: Current best ask price
            V_bid: Current best bid volume
            V_ask: Current best ask volume
        """
        ts = time.time()
        self.update_market(bid, ask, V_bid, V_ask, ts)
        quote = self.compute_quotes(ts)

        if quote.bid is not None or quote.ask is not None:
            bid_success, bids_filled, ask_success, asks_filled = self.execute_quote(quote)
            if not bid_success:
                self.handle_bid_failure(bids_filled)
            if not ask_success:
                self.handle_ask_failure(asks_filled)

            # Note: on_buy_fill and on_sell_fill are called inside execute_quote
            # when fills occur, so we don't call them again here to avoid double-counting
