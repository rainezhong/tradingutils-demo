"""
Mean reversion trading strategy.

DEMO VERSION - Strategy logic removed.
This file shows the class structure but contains no proprietary trading logic.
"""

from typing import Dict, Optional
from enum import Enum


class SignalType(Enum):
    """Trading signal types."""
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class MeanReversionStrategy:
    """
    Mean reversion strategy.

    DEMO VERSION - All trading logic has been removed.
    This class demonstrates the interface but does not contain real strategy logic.
    """

    def __init__(
        self,
        kalman_filter=None,
        order_manager=None,
        position_manager=None,
        risk_manager=None,
        entry_threshold: float = 0.01,
        exit_threshold: float = 0.002,
        min_signal_strength: float = 0.3,
        min_imbalance: float = 0.2
    ):
        """Initialize strategy (DEMO)."""
        self.ticker = None
        self.last_signal: Optional[SignalType] = None
        self.entry_price: Optional[float] = None
        print("[MeanReversionStrategy] Initialized (DEMO MODE)")

    def update(
        self,
        ticker: str,
        current_price: float,
        orderbook_features: Dict[str, float],
        score_features: Dict[str, float]
    ) -> Optional[SignalType]:
        """
        Update strategy with new market data.

        DEMO: Always returns HOLD.
        """
        self.ticker = ticker
        return SignalType.HOLD

    def execute_signal(
        self,
        signal: SignalType,
        current_price: float
    ) -> bool:
        """
        Execute trading signal.

        DEMO: Always returns False.
        """
        return False

    def get_status(self) -> Dict:
        """Get current strategy status."""
        return {
            'ticker': self.ticker,
            'last_signal': self.last_signal.value if self.last_signal else None,
            'demo_mode': True,
        }
