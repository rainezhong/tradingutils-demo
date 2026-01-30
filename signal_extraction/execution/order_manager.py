"""
Order management and execution system.
Handles order placement, cancellation, and tracking.
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime
import uuid


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Represents a trading order."""
    order_id: str
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[float] = None  # None for market orders
    
    # Status tracking
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    
    # Timestamps
    created_at: float = field(default_factory=time.time)
    submitted_at: Optional[float] = None
    filled_at: Optional[float] = None
    
    # Kalshi API response
    exchange_order_id: Optional[str] = None
    
    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity
    
    @property
    def is_active(self) -> bool:
        return self.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED]
    
    @property
    def is_complete(self) -> bool:
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED]
    
    def __str__(self) -> str:
        return (f"Order({self.order_id[:8]}... {self.side.value} {self.quantity} "
                f"{self.ticker} @ {self.price} - {self.status.value})")


class OrderManager:
    """
    Manages order lifecycle: creation, submission, tracking, cancellation.
    """
    
    def __init__(self, client, dry_run: bool = False):
        """
        Initialize order manager.
        
        Args:
            client: Kalshi API client
            dry_run: If True, simulate orders without actually placing them
        """
        self.client = client
        self.dry_run = dry_run
        
        # Order tracking
        self.orders: Dict[str, Order] = {}
        self.active_orders: Dict[str, Order] = {}
        
        # Performance tracking
        self.total_orders = 0
        self.filled_orders = 0
        self.canceled_orders = 0
        self.rejected_orders = 0
        
        print(f"[OrderManager] Initialized (dry_run={dry_run})")
    
    def create_order(
        self,
        ticker: str,
        side: OrderSide,
        quantity: int,
        price: Optional[float] = None,
        order_type: OrderType = OrderType.LIMIT
    ) -> Order:
        """
        Create a new order.
        
        Args:
            ticker: Market ticker
            side: BUY or SELL
            quantity: Number of contracts
            price: Limit price (required for LIMIT orders)
            order_type: MARKET or LIMIT
            
        Returns:
            Created Order object
        """
        if order_type == OrderType.LIMIT and price is None:
            raise ValueError("Limit orders require a price")
        
        order_id = str(uuid.uuid4())
        
        order = Order(
            order_id=order_id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price
        )
        
        self.orders[order_id] = order
        self.total_orders += 1
        
        print(f"[OrderManager] Created: {order}")
        return order
    
    def submit_order(self, order: Order) -> bool:
        """
        Submit order to exchange.
        
        Args:
            order: Order to submit
            
        Returns:
            True if successfully submitted
        """
        if order.status != OrderStatus.PENDING:
            print(f"[OrderManager] Cannot submit order in status {order.status}")
            return False
        
        if self.dry_run:
            # Simulate submission
            order.status = OrderStatus.SUBMITTED
            order.submitted_at = time.time()
            order.exchange_order_id = f"DRY_{order.order_id[:8]}"
            self.active_orders[order.order_id] = order
            print(f"[OrderManager] DRY RUN: Simulated submission of {order}")
            return True
        
        try:
            # Submit to Kalshi
            # Convert price to cents (Kalshi uses cents)
            price_cents = int(order.price * 100) if order.price else None
            
            # Determine if YES or NO side
            # For simplicity, assume BUY = YES, SELL = NO
            # In reality, you need to map this to your specific market
            action = "buy" if order.side == OrderSide.BUY else "sell"
            
            # Place order via API
            response = self.client.create_order(
                ticker=order.ticker,
                action=action,
                side="yes",  # or "no" depending on your logic
                count=order.quantity,
                type="limit" if order.order_type == OrderType.LIMIT else "market",
                yes_price=price_cents if action == "buy" else None,
                no_price=price_cents if action == "sell" else None
            )
            
            # Update order with exchange info
            if hasattr(response, 'order') and hasattr(response.order, 'order_id'):
                order.exchange_order_id = response.order.order_id
                order.status = OrderStatus.SUBMITTED
                order.submitted_at = time.time()
                self.active_orders[order.order_id] = order
                
                print(f"[OrderManager] Submitted: {order}")
                return True
            else:
                order.status = OrderStatus.REJECTED
                self.rejected_orders += 1
                print(f"[OrderManager] Rejected: {order}")
                return False
                
        except Exception as e:
            print(f"[OrderManager] Error submitting order: {e}")
            order.status = OrderStatus.REJECTED
            self.rejected_orders += 1
            return False
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an active order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if successfully canceled
        """
        if order_id not in self.orders:
            print(f"[OrderManager] Order {order_id} not found")
            return False
        
        order = self.orders[order_id]
        
        if not order.is_active:
            print(f"[OrderManager] Order {order_id} is not active (status: {order.status})")
            return False
        
        if self.dry_run:
            # Simulate cancellation
            order.status = OrderStatus.CANCELED
            if order_id in self.active_orders:
                del self.active_orders[order_id]
            self.canceled_orders += 1
            print(f"[OrderManager] DRY RUN: Canceled {order}")
            return True
        
        try:
            # Cancel via API
            if order.exchange_order_id:
                self.client.cancel_order(order.exchange_order_id)
            
            order.status = OrderStatus.CANCELED
            if order_id in self.active_orders:
                del self.active_orders[order_id]
            self.canceled_orders += 1
            
            print(f"[OrderManager] Canceled: {order}")
            return True
            
        except Exception as e:
            print(f"[OrderManager] Error canceling order: {e}")
            return False
    
    def update_order_status(self, order_id: str) -> Optional[Order]:
        """
        Query exchange for order status update.
        
        Args:
            order_id: Order ID to check
            
        Returns:
            Updated Order object or None if not found
        """
        if order_id not in self.orders:
            return None
        
        order = self.orders[order_id]
        
        if self.dry_run:
            # Simulate fill after 5 seconds
            if order.status == OrderStatus.SUBMITTED:
                if time.time() - order.submitted_at > 5:
                    order.status = OrderStatus.FILLED
                    order.filled_quantity = order.quantity
                    order.average_fill_price = order.price
                    order.filled_at = time.time()
                    if order_id in self.active_orders:
                        del self.active_orders[order_id]
                    self.filled_orders += 1
                    print(f"[OrderManager] DRY RUN: Filled {order}")
            return order
        
        try:
            # Query API for order status
            if not order.exchange_order_id:
                return order
            
            response = self.client.get_order(order.exchange_order_id)
            
            if hasattr(response, 'order'):
                api_order = response.order
                
                # Update status
                if api_order.status == "filled":
                    order.status = OrderStatus.FILLED
                    order.filled_quantity = order.quantity
                    order.filled_at = time.time()
                    if order_id in self.active_orders:
                        del self.active_orders[order_id]
                    self.filled_orders += 1
                    
                elif api_order.status == "canceled":
                    order.status = OrderStatus.CANCELED
                    if order_id in self.active_orders:
                        del self.active_orders[order_id]
                    self.canceled_orders += 1
                
                # Update fill info if available
                if hasattr(api_order, 'filled_count'):
                    order.filled_quantity = api_order.filled_count
                
                if hasattr(api_order, 'average_price'):
                    order.average_fill_price = api_order.average_price / 100.0
            
            return order
            
        except Exception as e:
            print(f"[OrderManager] Error updating order status: {e}")
            return order
    
    def cancel_all_orders(self, ticker: Optional[str] = None) -> int:
        """
        Cancel all active orders, optionally filtered by ticker.
        
        Args:
            ticker: If provided, only cancel orders for this ticker
            
        Returns:
            Number of orders canceled
        """
        orders_to_cancel = [
            order for order in self.active_orders.values()
            if ticker is None or order.ticker == ticker
        ]
        
        canceled_count = 0
        for order in orders_to_cancel:
            if self.cancel_order(order.order_id):
                canceled_count += 1
        
        return canceled_count
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)
    
    def get_active_orders(self, ticker: Optional[str] = None) -> List[Order]:
        """Get all active orders, optionally filtered by ticker."""
        orders = list(self.active_orders.values())
        if ticker:
            orders = [o for o in orders if o.ticker == ticker]
        return orders
    
    def get_statistics(self) -> Dict:
        """Get order execution statistics."""
        return {
            'total_orders': self.total_orders,
            'filled_orders': self.filled_orders,
            'canceled_orders': self.canceled_orders,
            'rejected_orders': self.rejected_orders,
            'active_orders': len(self.active_orders),
            'fill_rate': self.filled_orders / self.total_orders if self.total_orders > 0 else 0.0
        }
    
    def print_statistics(self):
        """Print order execution statistics."""
        stats = self.get_statistics()
        print("\n" + "="*50)
        print("ORDER EXECUTION STATISTICS")
        print("="*50)
        print(f"Total Orders:    {stats['total_orders']}")
        print(f"Filled:          {stats['filled_orders']}")
        print(f"Canceled:        {stats['canceled_orders']}")
        print(f"Rejected:        {stats['rejected_orders']}")
        print(f"Active:          {stats['active_orders']}")
        print(f"Fill Rate:       {stats['fill_rate']:.1%}")
        print("="*50 + "\n")