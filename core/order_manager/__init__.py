"""Order manager module.

Provides abstract interface and concrete implementations for order management.
"""

from .i_order_manager import I_OrderManager
from .order_manager_types import (
    Side,
    Action,
    OrderStatus,
    OrderType,
    OrderRequest,
    Fill,
    TrackedOrder,
    OrderResult,
)
from .kalshi_order_manager import KalshiOrderManager
from .polymarket_order_manager import PolymarketOrderManager

__all__ = [
    # Interface
    "I_OrderManager",
    # Types
    "Side",
    "Action",
    "OrderStatus",
    "OrderType",
    "OrderRequest",
    "Fill",
    "TrackedOrder",
    "OrderResult",
    # Kalshi Implementation
    "KalshiOrderManager",
    # Polymarket Implementation
    "PolymarketOrderManager",
]
