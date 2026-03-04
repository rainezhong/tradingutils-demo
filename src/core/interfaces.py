"""Stub interfaces for legacy imports.

The original implementations were consolidated into the top-level core/ package.
This file provides the minimal ABCs that strategies/base.py imports.
"""

from abc import ABC


class APIClient(ABC):
    """Abstract interface for trading API clients."""

    pass


class DataProvider(ABC):
    """Abstract interface for data providers."""

    pass


class OrderManager(ABC):
    """Abstract interface for order management."""

    pass


class AbstractBot(ABC):
    """Abstract interface for trading bots."""

    pass


class SpreadQuote:
    """Placeholder for spread quote type."""

    pass
