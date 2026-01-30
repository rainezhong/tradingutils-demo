"""
Position and Risk Management.
Tracks positions and enforces risk limits.
"""

from dataclasses import dataclass
from typing import Dict, Optional, List
from enum import Enum
import numpy as np


@dataclass
class Position:
    """Represents a position in a market."""
    ticker: str
    quantity: int  # Positive = long, negative = short
    average_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    
    @property
    def market_value(self) -> float:
        """Current market value of position."""
        return self.quantity * self.average_price
    
    @property
    def is_long(self) -> bool:
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        return self.quantity < 0
    
    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class PositionManager:
    """
    Tracks and manages trading positions.
    """
    
    def __init__(self, initial_capital: float = 10000.0):
        """
        Initialize position manager.
        
        Args:
            initial_capital: Starting account balance
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        
        # P&L tracking
        self.realized_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        
        print(f"[PositionManager] Initialized with ${initial_capital:,.2f}")
    
    def open_position(
        self,
        ticker: str,
        quantity: int,
        price: float
    ):
        """
        Open or add to a position.
        
        Args:
            ticker: Market ticker
            quantity: Number of contracts (positive or negative)
            price: Execution price
        """
        cost = abs(quantity) * price
        
        # Check if we have enough cash
        if cost > self.cash:
            print(f"[PositionManager] Insufficient cash: need ${cost:.2f}, have ${self.cash:.2f}")
            return
        
        if ticker not in self.positions:
            # New position
            self.positions[ticker] = Position(
                ticker=ticker,
                quantity=quantity,
                average_price=price
            )
            print(f"[PositionManager] Opened position: {quantity} {ticker} @ ${price:.4f}")
        else:
            # Add to existing position
            pos = self.positions[ticker]
            
            # Calculate new average price
            old_value = pos.quantity * pos.average_price
            new_value = quantity * price
            total_quantity = pos.quantity + quantity
            
            if total_quantity != 0:
                pos.average_price = (old_value + new_value) / total_quantity
                pos.quantity = total_quantity
                print(f"[PositionManager] Added to position: {ticker} now {pos.quantity} @ ${pos.average_price:.4f}")
            else:
                # Position closed - calculate P&L correctly
                if pos.quantity > 0:  # Was long: profit = (exit - entry) * qty
                    realized = (price - pos.average_price) * abs(pos.quantity)
                else:  # Was short: profit = (entry - exit) * qty
                    realized = (pos.average_price - price) * abs(pos.quantity)
                
                pos.realized_pnl += realized
                self.realized_pnl += realized
                self.cash += abs(pos.quantity) * pos.average_price + realized  # Return original cost + P&L
                
                self.total_trades += 1
                if realized > 0:
                    self.winning_trades += 1
                else:
                    self.losing_trades += 1
                
                del self.positions[ticker]
                print(f"[PositionManager] Closed position: {ticker} P&L: ${realized:.2f}")
                return
        
        # Update cash
        self.cash -= cost
    
    def close_position(self, ticker: str, price: float) -> float:
        """
        Close entire position.
        
        Args:
            ticker: Market ticker
            price: Exit price
            
        Returns:
            Realized P&L
        """
        if ticker not in self.positions:
            print(f"[PositionManager] No position in {ticker}")
            return 0.0
        
        pos = self.positions[ticker]
        quantity = pos.quantity
        
        # Close by taking opposite position
        self.open_position(ticker, -quantity, price)
        
        return pos.realized_pnl
    
    def update_market_prices(self, prices: Dict[str, float]):
        """
        Update unrealized P&L based on current market prices.
        
        Args:
            prices: Dict of {ticker: current_price}
        """
        for ticker, pos in self.positions.items():
            if ticker in prices:
                current_price = prices[ticker]
                pos.unrealized_pnl = pos.quantity * (current_price - pos.average_price)
    
    def get_position(self, ticker: str) -> Optional[Position]:
        """Get position for ticker."""
        return self.positions.get(ticker)
    
    def get_total_exposure(self) -> float:
        """Get total market value of all positions."""
        return sum(abs(pos.market_value) for pos in self.positions.values())
    
    def get_total_pnl(self) -> float:
        """Get total P&L (realized + unrealized)."""
        unrealized = sum(pos.unrealized_pnl for pos in self.positions.values())
        return self.realized_pnl + unrealized
    
    def get_equity(self) -> float:
        """Get total account equity."""
        return self.cash + sum(pos.market_value for pos in self.positions.values())
    
    def print_positions(self):
        """Print all current positions."""
        print("\n" + "="*70)
        print("CURRENT POSITIONS")
        print("="*70)
        
        if not self.positions:
            print("No open positions")
        else:
            for ticker, pos in self.positions.items():
                pnl_str = f"+${pos.unrealized_pnl:.2f}" if pos.unrealized_pnl >= 0 else f"-${abs(pos.unrealized_pnl):.2f}"
                print(f"{ticker:15} | Qty: {pos.quantity:4} | Avg: ${pos.average_price:.4f} | P&L: {pnl_str}")
        
        print("-"*70)
        print(f"Cash:            ${self.cash:,.2f}")
        print(f"Equity:          ${self.get_equity():,.2f}")
        print(f"Total P&L:       ${self.get_total_pnl():,.2f}")
        print(f"Win Rate:        {self.winning_trades}/{self.total_trades} = " +
              f"{self.winning_trades/self.total_trades*100:.1f}%" if self.total_trades > 0 else "N/A")
        print("="*70 + "\n")


class RiskManager:
    """
    Enforces risk limits and position sizing rules.
    """
    
    def __init__(
        self,
        position_manager: PositionManager,
        max_position_size: float = 0.02,  
        max_total_exposure: float = 0.20, 
        max_loss_per_trade: float = 0.02, 
        max_daily_loss: float = 0.5
    ):
        """
        Initialize risk manager.
        
        Args:
            position_manager: PositionManager instance
            max_position_size: Max % of capital per position
            max_total_exposure: Max % of capital across all positions
            max_loss_per_trade: Stop loss %
            max_daily_loss: Daily drawdown limit %
        """
        self.pm = position_manager
        self.max_position_size = max_position_size
        self.max_total_exposure = max_total_exposure
        self.max_loss_per_trade = max_loss_per_trade
        self.max_daily_loss = max_daily_loss
        
        # Daily tracking
        self.daily_start_equity = position_manager.get_equity()
        self.daily_pnl = 0.0
        
        print(f"[RiskManager] Initialized with limits:")
        print(f"  Max position size: {max_position_size*100:.1f}%")
        print(f"  Max total exposure: {max_total_exposure*100:.1f}%")
        print(f"  Stop loss: {max_loss_per_trade*100:.1f}%")
        print(f"  Daily loss limit: {max_daily_loss*100:.1f}%")
    
    def calculate_position_size(
        self,
        ticker: str,
        signal_strength: float,
        price: float,
        confidence: float = 1.0
    ) -> int:
        """
        Calculate appropriate position size based on signal and risk limits.
        
        Args:
            ticker: Market ticker
            signal_strength: Signal strength [-1, 1]
            price: Entry price
            confidence: Confidence level [0, 1]
            
        Returns:
            Number of contracts to trade
        """
        if abs(signal_strength) < 0.1:
            return 0  # Signal too weak
        
        # Base size from max position size limit
        equity = self.pm.get_equity()
        max_dollars = equity * self.max_position_size
        
        # Adjust for signal strength (using simple linear multiplier)
        size_multiplier = abs(signal_strength)
        target_dollars = max_dollars * size_multiplier
        
        # Convert to contracts (use max(1, ...) for small accounts if signal is valid)
        contracts = round(target_dollars / price)
        
        # If we have a signal but the account is too small for 1 contract via %, 
        # force 1 contract if we have enough cash.
        if contracts == 0 and target_dollars > 0:
            contracts = 1
            
        return max(0, contracts)
    
    def can_open_position(
        self,
        ticker: str,
        quantity: int,
        price: float
    ) -> tuple[bool, str]:
        """
        Check if opening position would violate risk limits.
        
        Args:
            ticker: Market ticker
            quantity: Number of contracts
            price: Entry price
            
        Returns:
            (allowed, reason_if_not)
        """
        cost = abs(quantity) * price
        
        # Check cash
        if cost > self.pm.cash:
            return False, f"Insufficient cash: need ${cost:.2f}, have ${self.pm.cash:.2f}"
        
        # Check position size limit
        equity = self.pm.get_equity()
        if cost > equity * self.max_position_size:
            return False, f"Exceeds max position size (${equity * self.max_position_size:.2f})"
        
        # Check total exposure limit
        current_exposure = self.pm.get_total_exposure()
        new_exposure = current_exposure + cost
        if new_exposure > equity * self.max_total_exposure:
            return False, f"Exceeds total exposure limit (${equity * self.max_total_exposure:.2f})"
        
        # Check daily loss limit
        current_pnl = self.pm.get_total_pnl()
        drawdown = (self.daily_start_equity - (self.daily_start_equity + current_pnl)) / self.daily_start_equity
        if drawdown > self.max_daily_loss:
            return False, f"Daily loss limit reached ({drawdown*100:.1f}%)"
        
        return True, "OK"
    
    def should_stop_out(self, ticker: str, current_price: float) -> bool:
        """
        Check if position should be stopped out.
        
        Args:
            ticker: Market ticker
            current_price: Current market price
            
        Returns:
            True if stop loss triggered
        """
        pos = self.pm.get_position(ticker)
        if not pos:
            return False
        
        # Calculate loss %
        if pos.is_long:
            loss_pct = (pos.average_price - current_price) / pos.average_price
        else:
            loss_pct = (current_price - pos.average_price) / pos.average_price
        
        if loss_pct > self.max_loss_per_trade:
            print(f"[RiskManager] Stop loss triggered for {ticker}: {loss_pct*100:.1f}%")
            return True
        
        return False
    
    def reset_daily_limits(self):
        """Reset daily tracking (call at start of each day)."""
        self.daily_start_equity = self.pm.get_equity()
        self.daily_pnl = 0.0
        print(f"[RiskManager] Reset daily limits. Starting equity: ${self.daily_start_equity:,.2f}")