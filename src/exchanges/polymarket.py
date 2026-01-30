"""Polymarket exchange implementation.

This module provides the PolymarketExchange class which implements the ExchangeClient
interface for trading on the Polymarket prediction market exchange.
"""

import ast
import os
from datetime import datetime
from typing import Dict, List, Optional
import uuid

try:
    from py_clob_client.clob_types import OrderArgs
    from py_clob_client.order_builder.constants import BUY, SELL

    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    BUY = "BUY"
    SELL = "SELL"

from ..core.exchange import ExchangeClient, Order, OrderBook, TradableMarket
from ..core.models import Fill, Market, Position
from ..core.utils import setup_logger, utc_now

logger = setup_logger(__name__)


class PolymarketExchange(ExchangeClient):
    """Polymarket implementation of ExchangeClient.

    This class wraps the PolymarketWrapped client to provide the exchange-agnostic
    interface for trading on Polymarket.

    Example:
        exchange = PolymarketExchange()
        market = exchange.get_market("TOKEN_ID")
        order = market.buy(price=0.45, size=10)
    """

    def __init__(self, poly_client=None):
        """Initialize the Polymarket exchange client.

        Args:
            poly_client: Optional PolymarketWrapped instance. If not provided,
                         will attempt to create one.
        """
        self._client = poly_client
        self._initialized = False

        # Cache for positions and orders
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}

        # Try to initialize if no client provided
        if self._client is None:
            self._try_init_client()
        else:
            self._initialized = True

        if self._initialized:
            logger.info("PolymarketExchange initialized")
        else:
            logger.warning(
                "PolymarketExchange initialized without client - "
                "trading disabled (missing poly_utils or credentials)"
            )

    def _try_init_client(self) -> None:
        """Try to initialize the Polymarket client."""
        try:
            # Check if required environment variables are set
            if not os.getenv("POLYGON_WALLET_PRIVATE_KEY"):
                logger.warning("POLYGON_WALLET_PRIVATE_KEY not set")
                return

            # Try to import and initialize
            from poly_utils.poly_wrapper import PolymarketWrapped

            self._client = PolymarketWrapped()
            self._initialized = True
        except ImportError as e:
            logger.warning(f"Could not import PolymarketWrapped: {e}")
        except Exception as e:
            logger.warning(f"Could not initialize PolymarketWrapped: {e}")

    @property
    def name(self) -> str:
        """Exchange name."""
        return "polymarket"

    def get_market(self, ticker: str) -> TradableMarket:
        """Get a tradable market by token ID.

        Args:
            ticker: The token ID (used as market identifier on Polymarket)

        Returns:
            TradableMarket instance
        """
        if not self._initialized:
            raise RuntimeError("Polymarket client not initialized")

        market_data = self._client.get_market(ticker)
        market = self._convert_to_market(market_data, ticker)
        return TradableMarket(market, self)

    def get_markets(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List[TradableMarket]:
        """Get multiple tradable markets.

        Args:
            status: Filter by status ('active' for open markets)
            limit: Maximum number of markets to return

        Returns:
            List of TradableMarket instances
        """
        if not self._initialized:
            raise RuntimeError("Polymarket client not initialized")

        try:
            all_markets = self._client.get_all_markets()

            # Filter by status if requested
            if status == "open" or status == "active":
                all_markets = [m for m in all_markets if getattr(m, "active", False)]

            tradable_markets = []
            for market_data in all_markets[:limit]:
                try:
                    # Extract token ID from clob_token_ids
                    token_id = ""
                    if hasattr(market_data, "clob_token_ids") and market_data.clob_token_ids:
                        token_ids = ast.literal_eval(market_data.clob_token_ids)
                        if token_ids:
                            token_id = token_ids[0]

                    market = self._convert_to_market(market_data, token_id)
                    tradable_markets.append(TradableMarket(market, self))
                except Exception as e:
                    logger.warning(f"Failed to parse market: {e}")

            return tradable_markets
        except Exception as e:
            logger.error(f"Failed to get markets: {e}")
            return []

    def get_balance(self) -> float:
        """Get account USDC balance.

        Returns:
            USDC balance in dollars.
        """
        if not self._initialized:
            logger.warning("Cannot get balance - client not initialized")
            return 0.0

        try:
            return self._client.get_usdc_balance()
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0.0

    def get_all_positions(self) -> Dict[str, Position]:
        """Get all positions across all markets.

        Returns:
            Dict mapping token_id to Position
        """
        if not self._initialized:
            logger.warning("Cannot get positions - client not initialized")
            return {}

        # Polymarket positions are tracked per-token
        # This would require iterating through known markets
        # For now, return cached positions
        return self._positions

    def get_all_orders(self, status: Optional[str] = None) -> List[Order]:
        """Get all orders across all markets.

        Args:
            status: Filter by status

        Returns:
            List of Order instances
        """
        if not self._initialized:
            logger.warning("Cannot get orders - client not initialized")
            return []

        # Return cached orders, filtered by status if requested
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def _place_order(
        self, ticker: str, side: str, price: float, size: int
    ) -> Order:
        """Place an order on Polymarket.

        Args:
            ticker: Token ID
            side: 'buy' or 'sell'
            price: Order price (0.0-1.0 as probability)
            size: Number of contracts

        Returns:
            Order instance with order_id populated
        """
        if not self._initialized:
            raise RuntimeError("Cannot place order - client not initialized")

        if not CLOB_AVAILABLE:
            raise RuntimeError("py_clob_client not available")

        try:
            # Map side to Polymarket format
            poly_side = BUY if side == "buy" else SELL

            # Execute order through CLOB client
            response = self._client.execute_order(
                price=price,
                size=size,
                side=poly_side,
                token_id=ticker,
            )

            order_id = response.get("orderID", str(uuid.uuid4())) if response else str(uuid.uuid4())

            order = Order(
                order_id=order_id,
                ticker=ticker,
                side=side,
                price=price,
                size=size,
                filled_size=0,
                status="pending",
                created_at=utc_now(),
                exchange=self.name,
            )

            self._orders[order.order_id] = order
            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            raise

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: The order to cancel

        Returns:
            True if successfully canceled
        """
        if not self._initialized:
            raise RuntimeError("Cannot cancel order - client not initialized")

        try:
            # Polymarket cancellation through CLOB client
            self._client.client.cancel(order_id)

            if order_id in self._orders:
                self._orders[order_id].status = "canceled"

            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def _get_orderbook(self, ticker: str) -> OrderBook:
        """Get orderbook for a market.

        Args:
            ticker: Token ID

        Returns:
            OrderBook instance
        """
        if not self._initialized:
            raise RuntimeError("Cannot get orderbook - client not initialized")

        try:
            ob = self._client.get_orderbook(ticker)

            # Parse bids and asks from OrderBookSummary
            bids = []
            asks = []

            if hasattr(ob, "bids") and ob.bids:
                for bid in ob.bids:
                    price = float(bid.price) if hasattr(bid, "price") else float(bid.get("price", 0))
                    size = int(float(bid.size)) if hasattr(bid, "size") else int(float(bid.get("size", 0)))
                    bids.append((price, size))

            if hasattr(ob, "asks") and ob.asks:
                for ask in ob.asks:
                    price = float(ask.price) if hasattr(ask, "price") else float(ask.get("price", 0))
                    size = int(float(ask.size)) if hasattr(ask, "size") else int(float(ask.get("size", 0)))
                    asks.append((price, size))

            # Sort bids descending, asks ascending
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])

            return OrderBook(
                ticker=ticker,
                bids=bids,
                asks=asks,
                timestamp=utc_now(),
            )
        except Exception as e:
            logger.error(f"Failed to get orderbook for {ticker}: {e}")
            return OrderBook(ticker=ticker, bids=[], asks=[], timestamp=utc_now())

    def _get_position(self, ticker: str) -> Optional[Position]:
        """Get position for a specific token.

        Args:
            ticker: Token ID

        Returns:
            Position if exists, None otherwise
        """
        if not self._initialized:
            return None

        try:
            balance = self._client.get_token_balance(ticker)
            if balance > 0:
                # Get current price for the position
                try:
                    current_price = self._client.get_orderbook_price(ticker)
                except Exception:
                    current_price = 0.0

                position = Position(
                    ticker=ticker,
                    size=int(balance),
                    entry_price=0.0,  # Not tracked by Polymarket API
                    current_price=current_price * 100,  # Convert to cents
                )
                self._positions[ticker] = position
                return position
        except Exception as e:
            logger.warning(f"Failed to get position for {ticker}: {e}")

        return self._positions.get(ticker)

    def _get_orders(self, ticker: str, status: Optional[str] = None) -> List[Order]:
        """Get orders for a specific token.

        Args:
            ticker: Token ID
            status: Filter by status

        Returns:
            List of Order instances
        """
        orders = [o for o in self._orders.values() if o.ticker == ticker]
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def _get_fills(self, ticker: str, limit: int = 100) -> List[Fill]:
        """Get fills for a specific token.

        Args:
            ticker: Token ID
            limit: Maximum number of fills to return

        Returns:
            List of Fill instances
        """
        # Polymarket doesn't provide a fills API directly
        # Fills would need to be tracked through event logs or order updates
        logger.warning("Fills API not available for Polymarket")
        return []

    def _get_market_data(self, ticker: str) -> Market:
        """Get fresh market data.

        Args:
            ticker: Token ID

        Returns:
            Market instance with updated data
        """
        if not self._initialized:
            raise RuntimeError("Cannot get market data - client not initialized")

        market_data = self._client.get_market(ticker)
        return self._convert_to_market(market_data, ticker)

    def _convert_to_market(self, market_data, token_id: str = "") -> Market:
        """Convert Polymarket market data to our Market model.

        Args:
            market_data: Raw market data from Polymarket API
            token_id: Token ID to use as ticker

        Returns:
            Market instance
        """
        if isinstance(market_data, dict):
            return Market(
                ticker=token_id or str(market_data.get("id", "")),
                title=market_data.get("question", ""),
                category=self._detect_category(market_data.get("question", "")),
                close_time=market_data.get("end"),
                status="open" if market_data.get("active") else "closed",
                volume_24h=None,  # Not directly available
                open_interest=None,
            )
        else:
            # SimpleMarket or similar object
            ticker = token_id
            if not ticker and hasattr(market_data, "clob_token_ids") and market_data.clob_token_ids:
                try:
                    token_ids = ast.literal_eval(market_data.clob_token_ids)
                    if token_ids:
                        ticker = token_ids[0]
                except Exception:
                    pass

            return Market(
                ticker=ticker or str(getattr(market_data, "id", "")),
                title=getattr(market_data, "question", ""),
                category=self._detect_category(getattr(market_data, "question", "")),
                close_time=getattr(market_data, "end", None),
                status="open" if getattr(market_data, "active", False) else "closed",
                volume_24h=None,
                open_interest=None,
            )

    def _detect_category(self, question: str) -> str:
        """Detect market category from question text.

        Args:
            question: Market question text

        Returns:
            Category string
        """
        if not question:
            return "other"

        question_lower = question.lower()

        # Category keywords
        politics_keywords = [
            "election", "president", "vote", "congress", "senate",
            "minister", "government", "fed", "rate", "chancellor", "prime minister"
        ]
        sports_keywords = [
            "nba", "nfl", "mlb", "soccer", "football", "basketball",
            "baseball", "league", "cup", "championship", "win", "relegated"
        ]
        crypto_keywords = [
            "bitcoin", "eth", "crypto", "token", "blockchain", "opensea", "nft"
        ]

        if any(keyword in question_lower for keyword in politics_keywords):
            return "politics"
        elif any(keyword in question_lower for keyword in sports_keywords):
            return "sports"
        elif any(keyword in question_lower for keyword in crypto_keywords):
            return "crypto"

        return "other"
