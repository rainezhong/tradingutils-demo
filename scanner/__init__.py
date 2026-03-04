"""Scanner module for discovering and filtering markets.

Provides exchange-agnostic I_Scanner interface and implementations.

Usage:
    from scanner import I_Scanner, KalshiScanner, ScanFilter, ScanResult

    # Use interface type in strategy
    class MyStrategy:
        def __init__(self, scanner: I_Scanner):
            self._scanner = scanner

    # Instantiate with concrete implementation
    strategy = MyStrategy(scanner=KalshiScanner())
"""

from .scanner_types import ScanFilter, ScanResult, MarketFilterFn
from .i_scanner import I_Scanner
from .kalshi_scanner import KalshiScanner

__all__ = [
    # Interface
    "I_Scanner",
    # Types
    "ScanFilter",
    "ScanResult",
    "MarketFilterFn",
    # Implementations
    "KalshiScanner",
]
