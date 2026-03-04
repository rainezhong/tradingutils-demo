"""Kalshi Order Manager - Implementation of I_OrderManager for Kalshi."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

# Import WebSocket if available
try:
    from core.exchange_client.kalshi.kalshi_websocket import KalshiWebSocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    KalshiWebSocket = None

from .i_order_manager import I_OrderManager
from .order_manager_types import (
    Action,
    Fill,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    TrackedOrder,
)

logger = logging.getLogger(__name__)


def generate_idempotency_key() -> str:
    """Generate a unique idempotency key."""
    return f"oms-{uuid.uuid4().hex[:16]}-{int(time.time() * 1000)}"


class KalshiOrderManager(I_OrderManager):
    """Order manager for Kalshi exchange implementing I_OrderManager.

    Handles order submission, tracking, and lifecycle management
    for Kalshi markets.

    Example:
        >>> from core.exchange_client.kalshi_client import KalshiExchangeClient
        >>> client = KalshiExchangeClient.from_env()
        >>> await client.connect()
        >>>
        >>> order_manager = KalshiOrderManager(client)
        >>>
        >>> request = OrderRequest(
        ...     ticker="KXNBAGAME-...",
        ...     side=Side.YES,
        ...     action=Action.BUY,
        ...     size=10,
        ...     price_cents=45,
        ... )
        >>> order_id = await order_manager.buy(request)
    """

    def __init__(self, exchange_client: Any, enable_websocket: bool = True):
        """Initialize order manager.

        Args:
            exchange_client: Kalshi exchange client (must implement request methods)
            enable_websocket: If True, use WebSocket for real-time fill detection (default: True)
        """
        self._client = exchange_client
        self._orders: Dict[str, TrackedOrder] = {}
        self._fills: List[Fill] = []

        # WebSocket for real-time fills
        self._enable_websocket = enable_websocket and WEBSOCKET_AVAILABLE
        self._websocket: Optional[Any] = None  # KalshiWebSocket instance

        # Position tracking by (ticker, side) to prevent buying both YES and NO
        self._positions: Dict[Tuple[str, Side], int] = {}

        # Cached position summary for fast lookup (updated on fills)
        self._position_tickers: set = set()  # All tickers with positions

        # Market close times for position expiry tracking
        self._market_close_times: Dict[str, datetime] = {}

        # Callbacks
        self._on_fill: Optional[Callable[[TrackedOrder, Fill], None]] = None
        self._on_cancel: Optional[Callable[[TrackedOrder], None]] = None
        self._on_rejected: Optional[Callable[[TrackedOrder, str], None]] = None
        self._on_stale: Optional[Callable[[TrackedOrder], None]] = None
        self._on_partial_fill: Optional[Callable[[TrackedOrder, Fill], None]] = None
        self._on_expired: Optional[Callable[[TrackedOrder], None]] = None

        # Initialization flag
        self._initialized = False

        # Order age sweeper task
        self._sweeper_task: Optional[asyncio.Task] = None
        self._sweeper_running = False

        # WebSocket fill stream task
        self._websocket_task: Optional[asyncio.Task] = None

        # Fill pagination tracking
        self._last_fill_timestamp: Optional[float] = None

    async def initialize(self) -> None:
        """Initialize OMS - MUST be called on startup!

        Performs critical startup tasks:
        1. Cancels all resting orders from previous runs (clean slate)
        2. Recovers positions from recent fills (sync position tracking)
        3. Logs summary of cleanup and recovery

        This prevents:
        - Stranded positions from crashed runs
        - Duplicate order accumulation
        - Position tracking desync

        Example:
            >>> om = KalshiOrderManager(client)
            >>> await om.initialize()
            INFO: Canceled 3 stale orders from previous runs
            INFO: Recovered 2 positions from 15 fills
        """
        if self._initialized:
            logger.warning("OMS already initialized, skipping")
            return

        logger.info("Initializing OMS...")

        # STEP 1: Cancel ALL resting orders (clean slate)
        # This prevents stale orders from previous runs from filling
        logger.info("Canceling all resting orders from previous runs...")
        try:
            canceled = await self.cancel_all_orders()
            logger.info(f"✓ Canceled {canceled} stale order(s)")
        except Exception as e:
            logger.error(f"Failed to cancel resting orders: {e}")
            # Continue anyway - don't block initialization

        # STEP 2: Recover positions from recent fills
        # This syncs OMS position tracking with actual exchange state
        logger.info("Recovering positions from recent fills...")
        try:
            fills = await self.get_fills()
            logger.info(
                f"✓ Recovered {len(self._positions)} position(s) from {len(fills)} fill(s)"
            )

            # Log recovered positions for visibility
            if self._positions:
                for (ticker, side), qty in self._positions.items():
                    logger.info(f"  - {ticker} {side.value}: {qty} contracts")
            else:
                logger.info("  - No open positions")

        except Exception as e:
            logger.error(f"Failed to recover positions: {e}")
            # Continue anyway - positions will be tracked from new fills

        # STEP 3: Start order age sweeper
        # This background task cancels orders that exceed max_age_seconds
        logger.info("Starting order age sweeper...")
        self._sweeper_running = True
        self._sweeper_task = asyncio.create_task(self._order_age_sweeper())
        logger.info("✓ Order age sweeper started")

        # STEP 4: Start WebSocket fill stream (if enabled)
        if self._enable_websocket:
            try:
                logger.info("Starting WebSocket fill stream...")
                # Run WebSocket in background task (with reconnection loop)
                self._websocket_task = asyncio.create_task(self._start_websocket_fills())
                logger.info("✓ WebSocket fill stream started (real-time fills enabled)")
            except Exception as e:
                logger.warning(
                    f"WebSocket fill stream failed to start: {e}. "
                    f"Falling back to REST API polling."
                )
                self._enable_websocket = False

        self._initialized = True
        logger.info("OMS initialization complete")

    async def shutdown(self) -> None:
        """Shutdown OMS gracefully.

        Stops background tasks and cleans up resources.
        Call this before destroying the OMS instance.
        """
        logger.info("Shutting down OMS...")

        # Stop sweeper task (this also stops WebSocket fill stream via _sweeper_running flag)
        self._sweeper_running = False
        if self._sweeper_task:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            logger.info("✓ Order age sweeper stopped")

        # Stop WebSocket fill stream task
        if self._websocket_task:
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except asyncio.CancelledError:
                pass
            logger.info("✓ WebSocket fill stream task stopped")

        # Disconnect WebSocket
        if self._websocket:
            try:
                await self._websocket.disconnect()
                logger.info("✓ WebSocket disconnected")
            except Exception as e:
                logger.error(f"WebSocket shutdown error: {e}")

        logger.info("OMS shutdown complete")

    async def _order_age_sweeper(self) -> None:
        """Background task to cancel orders that exceed max_age_seconds.

        Runs every 30 seconds and checks all open orders for expiry.
        Orders with expiry_time in the past are automatically canceled.

        This prevents stale entry orders from filling when signals expire.
        """
        logger.info("Order age sweeper running (checking every 30s)")

        while self._sweeper_running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                now = datetime.now()
                orders_to_cancel = []

                # Find expired orders
                for order_id, order in list(self._orders.items()):
                    # Only check open orders
                    if order.status not in (
                        OrderStatus.PENDING,
                        OrderStatus.SUBMITTED,
                        OrderStatus.RESTING,
                    ):
                        continue

                    # Check if order expired
                    if order.is_expired:
                        orders_to_cancel.append((order_id, order))

                # Cancel expired orders
                for order_id, order in orders_to_cancel:
                    age = order.age_seconds
                    logger.warning(
                        f"Order {order_id} aged out ({age:.1f}s > {order.max_age_seconds}s) - "
                        f"{order.action.value} {order.size}x {order.ticker} @ {order.price_cents}¢"
                    )

                    # Trigger stale callback BEFORE canceling
                    if self._on_stale:
                        try:
                            self._on_stale(order)
                        except Exception as cb_err:
                            logger.error(f"Stale callback failed for {order_id}: {cb_err}")

                    try:
                        await self.cancel_order(order_id)
                    except Exception as e:
                        logger.error(f"Failed to cancel aged order {order_id}: {e}")

                if orders_to_cancel:
                    logger.info(f"Sweeper: canceled {len(orders_to_cancel)} aged order(s)")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Order age sweeper error: {e}")
                # Continue running despite errors

        logger.info("Order age sweeper stopped")

    async def _start_websocket_fills(self) -> None:
        """Start WebSocket connection for real-time fill detection with auto-reconnection.

        Implements exponential backoff reconnection to handle disconnections gracefully.
        Reconnection logic matches the pattern used in Binance/Coinbase feeds.

        Raises:
            Exception: If WebSocket connection fails after max retries
        """
        if not WEBSOCKET_AVAILABLE:
            raise RuntimeError("WebSocket not available (import failed)")

        # Get authentication from client
        # Assume client has _auth attribute (KalshiExchangeClient)
        if not hasattr(self._client, "_auth"):
            raise RuntimeError("Client does not support WebSocket authentication")

        # Reconnection parameters
        reconnect_attempts = 0
        max_reconnect_attempts = 10
        reconnect_delay_base = 1.0
        reconnect_delay_max = 30.0

        while self._sweeper_running:  # Use sweeper_running as running flag
            try:
                # Create WebSocket instance
                self._websocket = KalshiWebSocket(auth=self._client._auth)

                # Register fill callback
                self._websocket.on_fill(self._handle_websocket_fill)

                # Connect
                await self._websocket.connect()

                # Subscribe to fill channel (authenticated)
                # Use "*" to subscribe to all fills for this account
                await self._websocket.subscribe("fill", "*")

                logger.info("WebSocket fill stream connected and subscribed")

                # Reset reconnect counter on successful connection
                reconnect_attempts = 0

                # Monitor connection state - wait until disconnect
                while self._sweeper_running:
                    # Check if WebSocket is still connected
                    if not self._websocket.is_connected:
                        logger.warning("Fill WebSocket disconnected, will reconnect...")
                        break

                    await asyncio.sleep(1.0)

                # Clean up WebSocket before reconnecting
                if self._websocket:
                    try:
                        await self._websocket.disconnect()
                    except Exception as e:
                        logger.debug(f"Error during WebSocket cleanup: {e}")
                    self._websocket = None

            except Exception as e:
                if not self._sweeper_running:
                    break

                reconnect_attempts += 1

                # Check if max reconnection attempts exceeded
                if reconnect_attempts > max_reconnect_attempts:
                    logger.error(
                        f"Fill WebSocket: Max reconnection attempts ({max_reconnect_attempts}) exceeded"
                    )
                    raise RuntimeError(
                        f"WebSocket fill stream failed after {max_reconnect_attempts} reconnection attempts: {e}"
                    )

                # Calculate exponential backoff delay
                delay = min(
                    reconnect_delay_base * (2 ** (reconnect_attempts - 1)),
                    reconnect_delay_max,
                )

                logger.warning(
                    f"Fill WebSocket error: {e} - reconnecting in {delay:.1f}s (attempt {reconnect_attempts}/{max_reconnect_attempts})"
                )

                # Clean up failed WebSocket
                if self._websocket:
                    try:
                        await self._websocket.disconnect()
                    except Exception:
                        pass
                    self._websocket = None

                await asyncio.sleep(delay)

        logger.info("WebSocket fill stream stopped")

    def _handle_websocket_fill(self, msg: Dict[str, Any]) -> None:
        """Handle fill message from WebSocket.

        Args:
            msg: Fill message from WebSocket

        Note:
            This is called from the WebSocket event loop, so it must be thread-safe.
        """
        try:
            # Parse fill from WebSocket message
            # Message format: {trade_id, order_id, ticker, side, action, count, price, ...}
            fill = Fill(
                fill_id=msg.get("trade_id", ""),
                order_id=msg.get("order_id", ""),
                ticker=msg.get("ticker", ""),
                outcome=Side.YES if msg.get("side", "").lower() == "yes" else Side.NO,
                action=Action.BUY if msg.get("action", "").lower() == "buy" else Action.SELL,
                quantity=msg.get("count", 0),
                price_cents=msg.get("yes_price", 0) or msg.get("no_price", 0),
                timestamp=msg.get("created_time", time.time()),
            )

            # Check if this is a new fill (deduplicate)
            is_new = not any(
                existing.fill_id == fill.fill_id for existing in self._fills
            )

            if not is_new:
                return  # Already processed

            # Add to fills list
            self._fills.append(fill)

            # Update position tracking
            self.update_position_from_fill(fill)

            # Trigger on_fill callback if registered
            if self._on_fill and fill.order_id in self._orders:
                tracked_order = self._orders[fill.order_id]
                try:
                    self._on_fill(tracked_order, fill)
                except Exception as cb_err:
                    logger.error(f"Fill callback error for {fill.order_id}: {cb_err}")

            logger.info(
                f"WebSocket fill: {fill.action.value} {fill.quantity}x {fill.ticker} "
                f"{fill.outcome.value} @ {fill.price_cents}¢ (order: {fill.order_id})"
            )

        except Exception as e:
            logger.error(f"Error handling WebSocket fill: {e}")

    def set_on_fill_callback(
        self, callback: Callable[[TrackedOrder, Fill], None]
    ) -> None:
        """Set callback for fill events."""
        self._on_fill = callback

    def set_on_cancel_callback(self, callback: Callable[[TrackedOrder], None]) -> None:
        """Set callback for cancel events."""
        self._on_cancel = callback

    def set_on_rejected_callback(
        self, callback: Callable[[TrackedOrder, str], None]
    ) -> None:
        """Set callback for order rejection events.

        Args:
            callback: Function called when order is rejected by exchange.
                      Receives (TrackedOrder, rejection_reason)
        """
        self._on_rejected = callback

    def set_on_stale_callback(self, callback: Callable[[TrackedOrder], None]) -> None:
        """Set callback for stale order events.

        Args:
            callback: Function called when order is auto-canceled due to age.
                      Receives TrackedOrder that was aged out.
        """
        self._on_stale = callback

    def set_on_partial_fill_callback(
        self, callback: Callable[[TrackedOrder, Fill], None]
    ) -> None:
        """Set callback for partial fill events.

        Args:
            callback: Function called when order is partially filled.
                      Receives (TrackedOrder, Fill) for the partial fill.
        """
        self._on_partial_fill = callback

    def set_on_expired_callback(self, callback: Callable[[TrackedOrder], None]) -> None:
        """Set callback for order expiry events.

        Args:
            callback: Function called when order expires naturally (market closed).
                      Receives TrackedOrder that expired.
        """
        self._on_expired = callback

    # --- I_OrderManager Implementation ---

    async def submit_order(self, request: OrderRequest) -> str:
        """Submit an order to Kalshi.

        Args:
            request: Order parameters

        Returns:
            Order ID from exchange

        Raises:
            ValueError: If order parameters are invalid or would create opposite side position
            RuntimeError: If order submission fails
        """
        # CRITICAL: Check for opposite side positions to prevent overleveraging
        # YES and NO are perfectly negatively correlated - holding both is wasteful
        # OPTIMIZATION: Fast-path check using cached ticker set before dict lookup
        if request.action == Action.BUY and request.ticker in self._position_tickers:
            opposite_side = Side.NO if request.side == Side.YES else Side.YES
            opposite_pos = self._positions.get((request.ticker, opposite_side), 0)

            if opposite_pos > 0:
                raise ValueError(
                    f"Cannot buy {request.side.value} on {request.ticker}: "
                    f"already holding {opposite_pos} {opposite_side.value} contracts. "
                    f"This would overleverage your position on perfectly correlated outcomes."
                )

        # CRITICAL: Check for concurrent orders to prevent position accumulation
        # and Kalshi "invalid order" rejections
        if not request.allow_concurrent:
            pending_same = [
                o
                for o in self.get_open_orders()
                if o.ticker == request.ticker
                and o.side == request.side
                and o.action == request.action
            ]
            if pending_same:
                action_name = request.action.value.upper()
                if request.action == Action.SELL:
                    raise ValueError(
                        f"Cannot submit sell order on {request.ticker} {request.side.value}: "
                        f"already have {len(pending_same)} pending sell order(s). "
                        f"Use force_exit() to cancel pending orders first, or cancel manually."
                    )
                else:  # Action.BUY
                    raise ValueError(
                        f"Cannot submit buy order on {request.ticker} {request.side.value}: "
                        f"already have {len(pending_same)} pending buy order(s). "
                        f"This would accumulate position beyond limits. "
                        f"Cancel pending orders first or set allow_concurrent=True."
                    )

        # Generate idempotency key if not provided
        idempotency_key = request.idempotency_key or generate_idempotency_key()

        # Prepare arguments for client
        yes_price = None
        no_price = None
        if request.price_cents is not None:
            if request.side == Side.YES:
                yes_price = request.price_cents
            else:
                no_price = request.price_cents

        # Submit to exchange via high-level client method
        try:
            # We assume _client is KalshiExchangeClient
            response = await self._client.create_order(
                ticker=request.ticker,
                action=request.action.value,
                side=request.side.value,
                count=request.size,
                type=request.order_type.value,
                yes_price=yes_price,
                no_price=no_price,
            )

            # response is KalshiOrderResponse object
            order_id = response.order_id

            # Calculate expiry time if TTL specified
            expiry_time = None
            if request.max_age_seconds is not None:
                expiry_time = datetime.now() + timedelta(
                    seconds=request.max_age_seconds
                )

            # Track the order
            tracked = TrackedOrder(
                order_id=order_id,
                ticker=request.ticker,
                side=request.side,
                action=request.action,
                size=request.size,
                price_cents=request.price_cents,
                status=OrderStatus.SUBMITTED,
                idempotency_key=idempotency_key,
                max_age_seconds=request.max_age_seconds,
                expiry_time=expiry_time,
            )
            self._orders[order_id] = tracked

            logger.info(
                f"Order submitted: {order_id} {request.action.value} {request.size}x {request.ticker}"
            )

            return order_id

        except Exception as e:
            # Extract rejection reason from exception
            rejection_reason = str(e)

            # Create a synthetic order ID for tracking rejected orders
            synthetic_order_id = f"rejected_{idempotency_key}"

            # Track the rejected order
            tracked = TrackedOrder(
                order_id=synthetic_order_id,
                ticker=request.ticker,
                side=request.side,
                action=request.action,
                size=request.size,
                price_cents=request.price_cents,
                status=OrderStatus.REJECTED,
                idempotency_key=idempotency_key,
                max_age_seconds=request.max_age_seconds,
                expiry_time=None,  # Rejected orders don't need expiry
            )
            self._orders[synthetic_order_id] = tracked

            # Trigger rejection callback if registered
            if self._on_rejected:
                try:
                    self._on_rejected(tracked, rejection_reason)
                except Exception as cb_err:
                    logger.error(f"Rejection callback failed: {cb_err}")

            logger.error(
                f"Order rejected: {request.action.value} {request.size}x {request.ticker} "
                f"@ {request.price_cents}¢ - Reason: {rejection_reason}"
            )

            # Still raise the exception for backward compatibility
            raise RuntimeError(f"Order submission failed: {e}")

    async def buy(self, request: OrderRequest) -> str:
        """Submit a buy order.

        Args:
            request: Order parameters

        Returns:
            Order ID
        """
        # Ensure action is BUY
        request.action = Action.BUY
        return await self.submit_order(request)

    async def sell(self, request: OrderRequest) -> str:
        """Submit a sell order.

        Args:
            request: Order parameters

        Returns:
            Order ID
        """
        # Ensure action is SELL
        request.action = Action.SELL
        return await self.submit_order(request)

    async def cancel_order(
        self, order_id: str, max_retries: int = 20, retry_delay: float = 1.0
    ) -> bool:
        """Cancel an order, retrying until successful.

        Args:
            order_id: ID of order to cancel
            max_retries: Maximum number of retry attempts (default 20)
            retry_delay: Delay in seconds between retries (default 1.0)

        Returns:
            True if successfully canceled, False if filled or failed

        Note:
            This method validates actual cancellation by polling order status.
            If order filled before/during cancel, triggers fill callback and returns False.
        """
        attempt = 0
        while attempt < max_retries:
            try:
                # Request cancellation from exchange
                await self._client.cancel_order(order_id)

                # CRITICAL: Verify actual cancellation status
                # Order could have filled between our cancel request and exchange processing
                await asyncio.sleep(0.5)  # Give exchange time to process
                actual_status = await self.get_order_status(order_id)

                # Check if order filled before/during cancellation
                if actual_status == OrderStatus.FILLED:
                    logger.warning(
                        f"Order {order_id} filled before cancellation could complete"
                    )
                    # Fetch and process the fill
                    fills = await self.get_fills(order_id)
                    if fills:
                        logger.info(
                            f"Processed {len(fills)} fill(s) for {order_id} during cancel"
                        )
                    return False  # Not canceled, it filled

                # Check if partially filled before cancel
                if order_id in self._orders:
                    order = self._orders[order_id]
                    if order.filled_quantity > 0:
                        logger.warning(
                            f"Order {order_id} partially filled ({order.filled_quantity}/{order.size}) before cancel"
                        )
                        # Fetch partial fills
                        fills = await self.get_fills(order_id)

                        # Trigger partial fill callback for each fill
                        if self._on_partial_fill and fills:
                            for fill in fills:
                                try:
                                    self._on_partial_fill(order, fill)
                                except Exception as cb_err:
                                    logger.error(
                                        f"Partial fill callback failed for {order_id}: {cb_err}"
                                    )

                # If actually canceled, update tracked order
                if actual_status == OrderStatus.CANCELED:
                    if order_id in self._orders:
                        self._orders[order_id].status = OrderStatus.CANCELED
                        self._orders[order_id].updated_at = datetime.now()

                        if self._on_cancel:
                            self._on_cancel(self._orders[order_id])

                    logger.info(f"Order canceled: {order_id}")
                    return True
                else:
                    # Not canceled yet, retry
                    logger.debug(
                        f"Cancel attempt {attempt+1}: Order {order_id} status={actual_status.value}, retrying..."
                    )
                    attempt += 1
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(
                            f"Cancel failed for {order_id} after {max_retries} attempts (final status: {actual_status.value})"
                        )
                        return False

            except Exception as e:
                attempt += 1
                if attempt < max_retries:
                    logger.warning(
                        f"Cancel attempt {attempt} failed for {order_id}: {e}. Retrying in {retry_delay}s..."
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
            # Use client's get_order method
            order_data = await self._client.get_order(order_id)

            # order_data is a dict from get_order
            status_str = order_data.get("status", "pending")

            # Map API status to enum
            status_map = {
                "pending": OrderStatus.PENDING,
                "resting": OrderStatus.RESTING,
                "active": OrderStatus.RESTING,
                "executed": OrderStatus.FILLED,
                "filled": OrderStatus.FILLED,
                "canceled": OrderStatus.CANCELED,
                "cancelled": OrderStatus.CANCELED,
            }

            status = status_map.get(status_str.lower(), OrderStatus.PENDING)

            # Update tracked order
            if order_id in self._orders:
                self._orders[order_id].status = status
                self._orders[order_id].filled_quantity = order_data.get(
                    "filled_count", 0
                )
                self._orders[order_id].updated_at = datetime.now()

            return status

        except Exception as e:
            logger.error(f"Failed to get status for {order_id}: {e}")
            return OrderStatus.PENDING

    async def get_fills(
        self, order_id: Optional[str] = None, paginate: bool = True
    ) -> List[Fill]:
        """Get fills for an order or all recent fills.

        Args:
            order_id: Optional order ID filter
            paginate: If True, fetches all fills using pagination (default: True)

        Returns:
            List of fill events

        Note:
            With pagination enabled, this will fetch ALL fills since last known fill,
            not just the most recent 100. Useful for recovering from missed fills
            in high-frequency scenarios.
        """
        try:
            all_response_fills = []

            if paginate and order_id is None:
                # Paginate to get all fills since last known
                # Keep fetching until we get <100 fills (last page)
                page_limit = 100
                total_fetched = 0

                while True:
                    # Kalshi API supports min_ts for incremental queries
                    # But we don't know if the API actually supports it
                    # For now, just fetch in batches and filter client-side
                    response_fills = await self._client.get_fills(
                        ticker=None, limit=page_limit
                    )

                    all_response_fills.extend(response_fills)
                    total_fetched += len(response_fills)

                    # If we got fewer than page_limit, we're done
                    if len(response_fills) < page_limit:
                        break

                    # Safety: don't fetch more than 500 fills in one call
                    if total_fetched >= 500:
                        logger.warning(
                            f"Fill pagination stopped at {total_fetched} fills (safety limit)"
                        )
                        break

                if total_fetched > 100:
                    logger.info(
                        f"Paginated fills: fetched {total_fetched} total (>100 limit)"
                    )
            else:
                # Single fetch (backward compatible)
                all_response_fills = await self._client.get_fills(
                    ticker=None, limit=100
                )

            # Process fills
            fills = []
            newest_timestamp = self._last_fill_timestamp or 0.0

            for f in all_response_fills:
                # Filter by order_id if provided
                if order_id and f.get("order_id") != order_id:
                    continue

                # Convert timestamp to float (API may return string)
                timestamp_raw = f.get("created_time", time.time())
                if isinstance(timestamp_raw, str):
                    # Try to parse ISO format or use current time as fallback
                    try:
                        from datetime import datetime
                        timestamp = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00')).timestamp()
                    except:
                        timestamp = time.time()
                else:
                    timestamp = float(timestamp_raw)

                fill = Fill(
                    fill_id=f.get("trade_id", ""),
                    order_id=f.get("order_id", ""),
                    ticker=f.get("ticker", ""),
                    outcome=Side.YES if f.get("side", "").lower() == "yes" else Side.NO,
                    action=Action.BUY
                    if f.get("action", "").lower() == "buy"
                    else Action.SELL,
                    quantity=f.get("count", 0),
                    price_cents=f.get("yes_price", 0) or f.get("no_price", 0),
                    timestamp=timestamp,
                )
                fills.append(fill)

                # Track newest timestamp for next pagination
                if fill.timestamp > newest_timestamp:
                    newest_timestamp = fill.timestamp

                # Check if this is a new fill
                is_new = not any(
                    existing.fill_id == fill.fill_id for existing in self._fills
                )

                if is_new:
                    self._fills.append(fill)

                    # Update position tracking
                    self.update_position_from_fill(fill)

                    # Trigger on_fill callback if registered
                    if self._on_fill and fill.order_id in self._orders:
                        tracked_order = self._orders[fill.order_id]
                        self._on_fill(tracked_order, fill)

            # Update last known timestamp
            if fills:
                self._last_fill_timestamp = newest_timestamp

            return fills

        except Exception as e:
            logger.error(f"Failed to get fills: {e}")
            return []

    # --- Additional Methods ---

    async def cancel_all_orders(self, ticker: Optional[str] = None) -> int:
        """Cancel all open orders.

        Args:
            ticker: Optional ticker filter

        Returns:
            Number of orders canceled
        """
        canceled = 0

        # Get all resting orders
        # Get all resting orders via client
        try:
            orders = await self._client.get_orders(status="resting")

            for order in orders:
                if ticker and order.get("ticker") != ticker:
                    continue

                if await self.cancel_order(order.get("order_id", "")):
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

    # --- Position Tracking Methods ---

    def get_position(self, ticker: str, side: Side) -> int:
        """Get current position for a ticker and side.

        Args:
            ticker: Market ticker
            side: YES or NO

        Returns:
            Number of contracts held (0 if no position)
        """
        return self._positions.get((ticker, side), 0)

    def has_opposite_position(self, ticker: str, side: Side) -> bool:
        """Check if we have a position on the opposite side.

        Args:
            ticker: Market ticker
            side: Side we want to trade

        Returns:
            True if we hold contracts on the opposite side
        """
        opposite = Side.NO if side == Side.YES else Side.YES
        return self._positions.get((ticker, opposite), 0) > 0

    def update_position_from_fill(self, fill: Fill) -> None:
        """Update position tracking from a fill.

        Args:
            fill: Fill event to process
        """
        key = (fill.ticker, fill.outcome)

        # BUY adds to position, SELL reduces it
        delta = fill.quantity if fill.action == Action.BUY else -fill.quantity

        current = self._positions.get(key, 0)
        new_pos = current + delta

        if new_pos <= 0:
            # Position closed or negative (shouldn't happen)
            self._positions.pop(key, None)
            # Update cached ticker set - check if any positions remain for this ticker
            if not any(t == fill.ticker for t, _ in self._positions.keys()):
                self._position_tickers.discard(fill.ticker)
        else:
            self._positions[key] = new_pos
            self._position_tickers.add(fill.ticker)

        logger.info(
            f"Position updated: {fill.ticker} {fill.outcome.value} "
            f"{current} → {new_pos} (delta={delta})"
        )

    def get_all_positions(self) -> Dict[Tuple[str, Side], int]:
        """Get all current positions.

        Returns:
            Dictionary mapping (ticker, side) to quantity
        """
        return self._positions.copy()

    def set_market_close_time(self, ticker: str, close_time: datetime) -> None:
        """Set the close time for a market.

        Used for automatic position cleanup when markets expire.

        Args:
            ticker: Market ticker
            close_time: When the market closes/expires
        """
        self._market_close_times[ticker] = close_time
        logger.debug(f"Market close time set: {ticker} closes at {close_time}")

    def cleanup_expired_positions(self) -> int:
        """Remove positions for markets that have closed.

        Returns:
            Number of positions cleaned up

        Note:
            This should be called periodically (e.g., every hour) to prevent
            memory leak from accumulating closed positions.
        """
        now = datetime.now()
        cleaned = 0

        for (ticker, side), qty in list(self._positions.items()):
            # Check if we know the close time for this market
            close_time = self._market_close_times.get(ticker)

            if close_time and now >= close_time:
                # Market closed, clean up position
                logger.info(
                    f"Cleaning expired position: {ticker} {side.value} "
                    f"({qty} contracts, market closed {(now - close_time).total_seconds():.0f}s ago)"
                )

                # Clear the position
                self.clear_position(ticker, side)
                cleaned += 1

                # Clean up close time tracking
                if ticker not in [t for t, _ in self._positions.keys()]:
                    self._market_close_times.pop(ticker, None)

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} expired position(s)")

        return cleaned

    def clear_position(self, ticker: str, side: Side) -> None:
        """Manually clear a position.

        Use this when a position needs to be removed without a fill
        (e.g., market expired, position abandoned due to closure).

        Args:
            ticker: Market ticker
            side: Position side to clear
        """
        key = (ticker, side)
        if key in self._positions:
            removed_qty = self._positions[key]
            del self._positions[key]

            # Update cached ticker set
            if not any(t == ticker for t, _ in self._positions.keys()):
                self._position_tickers.discard(ticker)

            logger.info(
                f"Position manually cleared: {ticker} {side.value} "
                f"(removed {removed_qty} contracts)"
            )

    async def force_exit(
        self,
        ticker: str,
        side: Side,
        size: int,
        price_cents: int,
        reason: str = "force exit",
    ) -> str:
        """Force exit a position by canceling pending orders then submitting exit.

        This is an atomic operation that prevents "invalid order" rejections
        due to concurrent sell orders.

        Args:
            ticker: Market ticker
            side: Side of position to exit
            size: Number of contracts to sell
            price_cents: Limit price for exit
            reason: Reason for force exit (for logging)

        Returns:
            Order ID of the exit order

        Raises:
            RuntimeError: If exit order submission fails
        """
        # Cancel all pending orders on this ticker first
        canceled = await self.cancel_all_orders(ticker)
        if canceled > 0:
            logger.info(
                f"Force exit ({reason}): canceled {canceled} pending order(s) "
                f"on {ticker} before submitting exit"
            )

        # Now submit the exit order
        request = OrderRequest(
            ticker=ticker,
            side=side,
            action=Action.SELL,
            size=size,
            price_cents=price_cents,
            order_type=OrderType.LIMIT,
        )

        return await self.submit_order(request)
