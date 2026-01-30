"""Kalshi API client - DEMO VERSION.

This demo version always returns a MockKalshiClient.
Real API functionality has been removed.
"""

import logging
from typing import Optional

from .mock_client import MockKalshiClient

logger = logging.getLogger(__name__)


class KalshiClient:
    """Kalshi API client wrapper - DEMO VERSION.

    In demo mode, this class always returns MockKalshiClient instances.
    No real API calls are made.

    Example:
        >>> client = KalshiClient.from_env()
        >>> async with client:
        ...     market = await client.get_market_data("TICKER")
    """

    # API base URLs (not used in demo)
    PRODUCTION_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.com/trade-api/v2"

    def __new__(cls, *args, **kwargs):
        """Always return MockKalshiClient in demo mode."""
        logger.info("DEMO MODE: Using MockKalshiClient instead of real API")
        return MockKalshiClient(
            initial_balance=100000,  # $1000 starting balance
            auto_fill=True,
        )

    @classmethod
    def from_env(
        cls,
        base_url: Optional[str] = None,
        demo: bool = False,
    ) -> MockKalshiClient:
        """Create client from environment variables.

        DEMO: Always returns MockKalshiClient.

        Args:
            base_url: Ignored in demo mode
            demo: Ignored in demo mode

        Returns:
            MockKalshiClient instance
        """
        logger.info("DEMO MODE: Returning MockKalshiClient")
        return MockKalshiClient(
            initial_balance=100000,
            auto_fill=True,
        )


# For backwards compatibility, export the mock as the default
__all__ = ["KalshiClient", "MockKalshiClient"]
