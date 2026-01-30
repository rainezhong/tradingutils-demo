"""Exchange reconciliation for syncing local state with exchange.

Compares local order and position tracking with exchange state
to detect and correct discrepancies.
"""

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

from src.core.exchange import ExchangeClient, Order
from src.core.models import Position

from .models import (
    OrderStatus,
    ReconciliationMismatch,
    ReconciliationReport,
    TrackedOrder,
)


logger = logging.getLogger(__name__)


def _status_to_local(exchange_status: str) -> OrderStatus:
    """Convert exchange order status to local OrderStatus."""
    status_map = {
        "pending": OrderStatus.PENDING,
        "open": OrderStatus.OPEN,
        "partial": OrderStatus.PARTIAL,
        "filled": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "cancelled": OrderStatus.CANCELED,
        "rejected": OrderStatus.REJECTED,
    }
    return status_map.get(exchange_status.lower(), OrderStatus.OPEN)


class OrderReconciler:
    """Reconciles local order state with exchange state.

    Detects mismatches in:
    - Orders missing from local tracking (found on exchange)
    - Orders missing from exchange (local has, exchange doesn't)
    - Status discrepancies
    - Fill size discrepancies
    """

    def __init__(
        self,
        client: ExchangeClient,
        on_mismatch: Optional[Callable[[ReconciliationMismatch], None]] = None,
    ) -> None:
        """Initialize reconciler.

        Args:
            client: Exchange client to query
            on_mismatch: Optional callback for each mismatch found
        """
        self._client = client
        self._on_mismatch = on_mismatch

    def reconcile_orders(
        self,
        local_orders: Dict[str, TrackedOrder],
        auto_correct: bool = False,
    ) -> ReconciliationReport:
        """Reconcile local orders with exchange.

        Args:
            local_orders: Dict of local tracked orders keyed by order_id
            auto_correct: If True, update local orders to match exchange

        Returns:
            ReconciliationReport with results
        """
        report = ReconciliationReport(
            exchange=self._client.name,
            started_at=datetime.now(),
        )

        try:
            # Get all open orders from exchange
            exchange_orders = self._client.get_all_orders(status="open")
            exchange_order_map = {o.order_id: o for o in exchange_orders}

            # Also get recent filled/canceled orders
            all_orders = self._client.get_all_orders()
            for o in all_orders:
                if o.order_id not in exchange_order_map:
                    exchange_order_map[o.order_id] = o

            report.orders_checked = len(local_orders) + len(exchange_order_map)

            # Check for orders on exchange not in local tracking
            local_order_ids = set(local_orders.keys())
            exchange_order_ids = set(exchange_order_map.keys())

            missing_local = exchange_order_ids - local_order_ids
            for order_id in missing_local:
                ex_order = exchange_order_map[order_id]
                # Only flag if it's an active order
                if ex_order.is_active:
                    mismatch = ReconciliationMismatch(
                        mismatch_type="missing_local",
                        order_id=order_id,
                        local_value=None,
                        exchange_value=ex_order.status,
                        description=f"Order {order_id} exists on {self._client.name} but not in local tracking",
                    )
                    report.mismatches.append(mismatch)
                    self._notify_mismatch(mismatch)

            # Check for orders in local not on exchange
            missing_exchange = local_order_ids - exchange_order_ids
            for order_id in missing_exchange:
                local_order = local_orders[order_id]
                # Only flag if local thinks it's active
                if local_order.is_active:
                    mismatch = ReconciliationMismatch(
                        mismatch_type="missing_exchange",
                        order_id=order_id,
                        local_value=local_order.status.value,
                        exchange_value=None,
                        description=f"Order {order_id} in local tracking but not on {self._client.name}",
                    )
                    report.mismatches.append(mismatch)
                    self._notify_mismatch(mismatch)

                    if auto_correct:
                        # Mark as canceled in local
                        local_order.status = OrderStatus.CANCELED
                        local_order.last_update = datetime.now()
                        report.corrections_made.append(
                            f"Marked order {order_id} as CANCELED (not found on exchange)"
                        )

            # Check for status/fill mismatches
            common_orders = local_order_ids & exchange_order_ids
            for order_id in common_orders:
                local_order = local_orders[order_id]
                ex_order = exchange_order_map[order_id]

                # Check status mismatch
                expected_status = _status_to_local(ex_order.status)
                if local_order.status != expected_status:
                    # Don't flag if local is more "advanced" (e.g., we know it's filled)
                    terminal_statuses = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}
                    if local_order.status not in terminal_statuses:
                        mismatch = ReconciliationMismatch(
                            mismatch_type="status",
                            order_id=order_id,
                            local_value=local_order.status.value,
                            exchange_value=ex_order.status,
                            description=f"Order {order_id} status mismatch: local={local_order.status.value}, exchange={ex_order.status}",
                        )
                        report.mismatches.append(mismatch)
                        self._notify_mismatch(mismatch)

                        if auto_correct:
                            local_order.status = expected_status
                            local_order.last_update = datetime.now()
                            report.corrections_made.append(
                                f"Updated order {order_id} status from {local_order.status.value} to {expected_status.value}"
                            )

                # Check fill size mismatch
                if local_order.filled_size != ex_order.filled_size:
                    mismatch = ReconciliationMismatch(
                        mismatch_type="fill_size",
                        order_id=order_id,
                        local_value=str(local_order.filled_size),
                        exchange_value=str(ex_order.filled_size),
                        description=f"Order {order_id} fill mismatch: local={local_order.filled_size}, exchange={ex_order.filled_size}",
                    )
                    report.mismatches.append(mismatch)
                    self._notify_mismatch(mismatch)

                    if auto_correct:
                        old_filled = local_order.filled_size
                        local_order.filled_size = ex_order.filled_size
                        local_order.last_update = datetime.now()
                        if ex_order.filled_size >= local_order.size:
                            local_order.status = OrderStatus.FILLED
                        elif ex_order.filled_size > 0:
                            local_order.status = OrderStatus.PARTIAL
                        report.corrections_made.append(
                            f"Updated order {order_id} filled_size from {old_filled} to {ex_order.filled_size}"
                        )

            report.success = True

        except Exception as e:
            logger.error("Reconciliation failed: %s", e)
            report.success = False
            report.error = str(e)

        report.completed_at = datetime.now()
        return report

    def _notify_mismatch(self, mismatch: ReconciliationMismatch) -> None:
        """Notify callback of mismatch."""
        logger.warning(
            "Reconciliation mismatch: type=%s order=%s local=%s exchange=%s",
            mismatch.mismatch_type,
            mismatch.order_id,
            mismatch.local_value,
            mismatch.exchange_value,
        )
        if self._on_mismatch:
            try:
                self._on_mismatch(mismatch)
            except Exception as e:
                logger.error("Error in mismatch callback: %s", e)


class PositionReconciler:
    """Reconciles local position state with exchange state."""

    def __init__(
        self,
        client: ExchangeClient,
        on_mismatch: Optional[Callable[[str, Position, Optional[Position]], None]] = None,
    ) -> None:
        """Initialize reconciler.

        Args:
            client: Exchange client to query
            on_mismatch: Optional callback (ticker, local_position, exchange_position)
        """
        self._client = client
        self._on_mismatch = on_mismatch

    def reconcile_positions(
        self,
        local_positions: Dict[str, Position],
        auto_correct: bool = False,
    ) -> ReconciliationReport:
        """Reconcile local positions with exchange.

        Args:
            local_positions: Dict of local positions keyed by ticker
            auto_correct: If True, update local positions to match exchange

        Returns:
            ReconciliationReport with results
        """
        report = ReconciliationReport(
            exchange=self._client.name,
            started_at=datetime.now(),
        )

        try:
            # Get all positions from exchange
            exchange_positions = self._client.get_all_positions()
            report.positions_checked = len(local_positions) + len(exchange_positions)

            local_tickers = set(local_positions.keys())
            exchange_tickers = set(exchange_positions.keys())

            # Check for positions on exchange not in local
            for ticker in exchange_tickers - local_tickers:
                ex_pos = exchange_positions[ticker]
                if ex_pos.size != 0:  # Only flag non-zero positions
                    mismatch = ReconciliationMismatch(
                        mismatch_type="missing_local",
                        order_id=ticker,  # Using order_id field for ticker
                        local_value=None,
                        exchange_value=str(ex_pos.size),
                        description=f"Position in {ticker} exists on exchange but not locally",
                    )
                    report.mismatches.append(mismatch)
                    self._notify_mismatch(ticker, None, ex_pos)

                    if auto_correct:
                        local_positions[ticker] = ex_pos
                        report.corrections_made.append(
                            f"Added position for {ticker}: size={ex_pos.size}"
                        )

            # Check for positions in local not on exchange
            for ticker in local_tickers - exchange_tickers:
                local_pos = local_positions[ticker]
                if local_pos.size != 0:  # Only flag non-zero positions
                    mismatch = ReconciliationMismatch(
                        mismatch_type="missing_exchange",
                        order_id=ticker,
                        local_value=str(local_pos.size),
                        exchange_value=None,
                        description=f"Position in {ticker} exists locally but not on exchange",
                    )
                    report.mismatches.append(mismatch)
                    self._notify_mismatch(ticker, local_pos, None)

                    if auto_correct:
                        # Create flat position
                        local_positions[ticker] = Position(
                            ticker=ticker,
                            size=0,
                            entry_price=0.0,
                            current_price=local_pos.current_price,
                        )
                        report.corrections_made.append(
                            f"Zeroed position for {ticker} (not found on exchange)"
                        )

            # Check for size mismatches
            for ticker in local_tickers & exchange_tickers:
                local_pos = local_positions[ticker]
                ex_pos = exchange_positions[ticker]

                if local_pos.size != ex_pos.size:
                    mismatch = ReconciliationMismatch(
                        mismatch_type="size",
                        order_id=ticker,
                        local_value=str(local_pos.size),
                        exchange_value=str(ex_pos.size),
                        description=f"Position in {ticker} size mismatch: local={local_pos.size}, exchange={ex_pos.size}",
                    )
                    report.mismatches.append(mismatch)
                    self._notify_mismatch(ticker, local_pos, ex_pos)

                    if auto_correct:
                        old_size = local_pos.size
                        local_positions[ticker] = ex_pos
                        report.corrections_made.append(
                            f"Updated position for {ticker}: {old_size} -> {ex_pos.size}"
                        )

            report.success = True

        except Exception as e:
            logger.error("Position reconciliation failed: %s", e)
            report.success = False
            report.error = str(e)

        report.completed_at = datetime.now()
        return report

    def _notify_mismatch(
        self,
        ticker: str,
        local_pos: Optional[Position],
        exchange_pos: Optional[Position],
    ) -> None:
        """Notify callback of position mismatch."""
        logger.warning(
            "Position mismatch: ticker=%s local=%s exchange=%s",
            ticker,
            local_pos.size if local_pos else None,
            exchange_pos.size if exchange_pos else None,
        )
        if self._on_mismatch:
            try:
                self._on_mismatch(ticker, local_pos, exchange_pos)
            except Exception as e:
                logger.error("Error in position mismatch callback: %s", e)


class Reconciler:
    """Combined order and position reconciler for an exchange."""

    def __init__(
        self,
        client: ExchangeClient,
        on_order_mismatch: Optional[Callable[[ReconciliationMismatch], None]] = None,
        on_position_mismatch: Optional[Callable[[str, Position, Optional[Position]], None]] = None,
    ) -> None:
        """Initialize combined reconciler.

        Args:
            client: Exchange client to query
            on_order_mismatch: Callback for order mismatches
            on_position_mismatch: Callback for position mismatches
        """
        self._client = client
        self._order_reconciler = OrderReconciler(client, on_order_mismatch)
        self._position_reconciler = PositionReconciler(client, on_position_mismatch)

    def reconcile(
        self,
        local_orders: Dict[str, TrackedOrder],
        local_positions: Dict[str, Position],
        auto_correct: bool = False,
    ) -> ReconciliationReport:
        """Reconcile both orders and positions.

        Args:
            local_orders: Dict of tracked orders
            local_positions: Dict of positions
            auto_correct: If True, update local state to match exchange

        Returns:
            Combined reconciliation report
        """
        report = ReconciliationReport(
            exchange=self._client.name,
            started_at=datetime.now(),
        )

        # Reconcile orders
        order_report = self._order_reconciler.reconcile_orders(local_orders, auto_correct)
        report.orders_checked = order_report.orders_checked
        report.mismatches.extend(order_report.mismatches)
        report.corrections_made.extend(order_report.corrections_made)

        if not order_report.success:
            report.success = False
            report.error = f"Order reconciliation failed: {order_report.error}"
            report.completed_at = datetime.now()
            return report

        # Reconcile positions
        position_report = self._position_reconciler.reconcile_positions(
            local_positions, auto_correct
        )
        report.positions_checked = position_report.positions_checked
        report.mismatches.extend(position_report.mismatches)
        report.corrections_made.extend(position_report.corrections_made)

        if not position_report.success:
            report.success = False
            report.error = f"Position reconciliation failed: {position_report.error}"
        else:
            report.success = True

        report.completed_at = datetime.now()
        return report

    @property
    def exchange(self) -> str:
        """Get exchange name."""
        return self._client.name
