"""Polymarket Order Manager - Implementation of I_OrderManager for Polymarket."""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .i_order_manager import I_OrderManager
from .order_manager_types import (
    Action,
    Fill,
    OrderRequest,
    OrderStatus,
    Side,
    TrackedOrder,
)

logger = logging.getLogger(__name__)


def generate_idempotency_key() -> str:
    """Generate a unique idempotency key."""
    return f"oms-poly-{uuid.uuid4().hex[:16]}-{int(time.time() * 1000)}"


# Map OMS Side to Polymarket token selection
# YES -> use yes_token_id, NO -> use no_token_id
# But in Polymarket, ticker in OrderRequest is already the token_id

# Map Polymarket order status to OMS OrderStatus
POLYMARKET_STATUS_MAP = {
    "LIVE": OrderStatus.RESTING,
    "MATCHED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELED,
    "CANCELED": OrderStatus.CANCELED,
    "DELAYED": OrderStatus.PENDING,
}


class PolymarketOrderManager(I_OrderManager):
    """Order manager for Polymarket exchange implementing I_OrderManager.

    Handles order submission, tracking, and lifecycle management
    for Polymarket markets.

    Key differences from Kalshi:
    - request.ticker = Polymarket token_id
    - Prices are converted: price_cents (0-99) <-> probability (0.0-1.0)
    - Side (YES/NO) maps to BUY/SELL on the correct token

    Example:
        >>> from core.exchange_client.polymarket import PolymarketExchangeClient
        >>> client = PolymarketExchangeClient.from_env()
        >>> await client.connect()
        >>>
        >>> order_manager = PolymarketOrderManager(client)
        >>>
        >>> request = OrderRequest(
        ...     ticker="token_id_here",
        ...     side=Side.YES,
        ...     action=Action.BUY,
        ...     size=10,
        ...     price_cents=45,  # 0.45 probability
        ... )
        >>> order_id = await order_manager.buy(request)
    """

    def __init__(self, exchange_client: Any):
        """Initialize order manager.

        Args:
            exchange_client: Polymarket exchange client (PolymarketExchangeClient)
        """
        self._client = exchange_client
        self._orders: Dict[str, TrackedOrder] = {}
        self._fills: List[Fill] = []

        # Callbacks
        self._on_fill: Optional[Callable[[TrackedOrder, Fill], None]] = None
        self._on_cancel: Optional[Callable[[TrackedOrder], None]] = None

    def set_on_fill_callback(
        self, callback: Callable[[TrackedOrder, Fill], None]
    ) -> None:
        """Set callback for fill events."""
        self._on_fill = callback

    def set_on_cancel_callback(self, callback: Callable[[TrackedOrder], None]) -> None:
        """Set callback for cancel events."""
        self._on_cancel = callback

    # --- I_OrderManager Implementation ---

    async def submit_order(self, request: OrderRequest) -> str:
        """Submit an order to Polymarket.

        Args:
            request: Order parameters
                - ticker: token_id for the outcome
                - price_cents: Price in cents (0-99), converted to 0.0-1.0
                - action: BUY or SELL
                - size: Number of shares

        Returns:
            Order ID from exchange

        Raises:
            ValueError: If order parameters are invalid
            RuntimeError: If order submission fails
        """
        idempotency_key = request.idempotency_key or generate_idempotency_key()

        # Convert price_cents to probability (0-1)
        price = request.price_cents / 100.0 if request.price_cents is not None else 0.50

        # Map action to Polymarket side
        side = request.action.value.upper()  # "BUY" or "SELL"

        try:
            response = await self._client.create_order(
                token_id=request.ticker,
                side=side,
                price=price,
                size=float(request.size),
            )

            order_id = response.order_id

            # Track the order
            tracked = TrackedOrder(
                order_id=order_id,
                ticker=request.ticker,
                side=request.side,
                action=request.action,
                size=request.size,
                price_cents=request.price_cents,
                status=OrderStatus.SUBMITTED,
                exchange="polymarket",
                idempotency_key=idempotency_key,
            )
            self._orders[order_id] = tracked

            logger.info(
                f"Order submitted: {order_id} {request.action.value} "
                f"{request.size}x @ {price:.2f} ({request.ticker[:16]}...)"
            )

            return order_id

        except Exception as e:
            logger.error(f"Order submission failed: {e}")
            raise RuntimeError(f"Order submission failed: {e}")

    async def buy(self, request: OrderRequest) -> str:
        """Submit a buy order."""
        request.action = Action.BUY
        return await self.submit_order(request)

    async def sell(self, request: OrderRequest) -> str:
        """Submit a sell order."""
        request.action = Action.SELL
        return await self.submit_order(request)

    async def cancel_order(
        self, order_id: str, max_retries: int = 20, retry_delay: float = 1.0
    ) -> bool:
        """Cancel an order, retrying until successful.

        Args:
            order_id: ID of order to cancel
            max_retries: Maximum number of retry attempts
            retry_delay: Delay in seconds between retries

        Returns:
            True if successfully canceled
        """
        attempt = 0
        while attempt < max_retries:
            try:
                await self._client.cancel_order(order_id)

                if order_id in self._orders:
                    self._orders[order_id].status = OrderStatus.CANCELED
                    self._orders[order_id].updated_at = datetime.now()

                    if self._on_cancel:
                        self._on_cancel(self._orders[order_id])

                logger.info(f"Order canceled: {order_id}")
                return True

            except Exception as e:
                attempt += 1
                if attempt < max_retries:
                    logger.warning(
                        f"Cancel attempt {attempt} failed for {order_id}: {e}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        f"Cancel failed for {order_id} after {max_retries} attempts: {e}"
                    )
                    return False

        return False

    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Get current status of an order.

        Args:
            order_id: Order ID to check

        Returns:
            Current order status
        """
        try:
            order_data = await self._client.get_order(order_id)

            status_str = order_data.get("status", "LIVE")
            status = POLYMARKET_STATUS_MAP.get(status_str.upper(), OrderStatus.PENDING)

            # Update tracked order
            if order_id in self._orders:
                self._orders[order_id].status = status
                self._orders[order_id].filled_quantity = int(
                    float(order_data.get("size_matched", 0))
                )
                self._orders[order_id].updated_at = datetime.now()

            return status

        except Exception as e:
            logger.error(f"Failed to get status for {order_id}: {e}")
            return OrderStatus.PENDING

    async def get_fills(self, order_id: Optional[str] = None) -> List[Fill]:
        """Get fills for an order or all recent fills.

        Args:
            order_id: Optional order ID filter

        Returns:
            List of fill events
        """
        try:
            response_fills = await self._client.get_fills()

            fills = []
            for f in response_fills:
                if order_id and f.get("order_id") != order_id:
                    continue

                # Convert Polymarket price (0-1) to cents
                trade_price = float(f.get("price", 0))
                price_cents = int(trade_price * 100)

                fill = Fill(
                    fill_id=f.get("id", f.get("trade_id", "")),
                    order_id=f.get("order_id", ""),
                    ticker=f.get("asset_id", f.get("token_id", "")),
                    outcome=Side.YES if f.get("side", "").upper() == "BUY" else Side.NO,
                    action=Action.BUY
                    if f.get("side", "").upper() == "BUY"
                    else Action.SELL,
                    quantity=int(float(f.get("size", 0))),
                    price_cents=price_cents,
                    timestamp=f.get("created_at", f.get("timestamp", time.time())),
                )
                fills.append(fill)
                if not any(
                    existing.fill_id == fill.fill_id for existing in self._fills
                ):
                    self._fills.append(fill)

            return fills

        except Exception as e:
            logger.error(f"Failed to get fills: {e}")
            return []

    # --- Additional Methods ---

    async def cancel_all_orders(self, ticker: Optional[str] = None) -> int:
        """Cancel all open orders.

        Args:
            ticker: Optional token_id filter

        Returns:
            Number of orders canceled
        """
        canceled = 0

        try:
            orders = await self._client.get_orders()

            for order in orders:
                if order.get("status", "").upper() != "LIVE":
                    continue
                if ticker and order.get("asset_id") != ticker:
                    continue

                if await self.cancel_order(order.get("id", order.get("order_id", ""))):
                    canceled += 1

        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")

        return canceled

    def get_tracked_orders(self) -> Dict[str, TrackedOrder]:
        """Get all tracked orders."""
        return self._orders.copy()

    def get_open_orders(self) -> List[TrackedOrder]:
        """Get orders that are still open."""
        return [
            o
            for o in self._orders.values()
            if o.status
            in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.RESTING)
        ]

    async def sync_orders(self) -> None:
        """Sync tracked orders with exchange state."""
        for order_id in list(self._orders.keys()):
            await self.get_order_status(order_id)
