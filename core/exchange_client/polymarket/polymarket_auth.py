"""Authentication helpers for Polymarket CLOB API.

Thin wrapper around src.polymarket.wallet, exposing auth in core/ style.
"""

import os
from typing import Any, Dict

from src.polymarket.wallet import PolymarketWallet

# Auto-load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    from pathlib import Path

    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


class PolymarketAuth:
    """Authentication handler for Polymarket CLOB API.

    Wraps PolymarketWallet from src.polymarket.wallet to provide
    a consistent auth interface matching KalshiAuth.

    Example:
        >>> auth = PolymarketAuth.from_env()
        >>> headers = auth.sign_request("GET", "/orders")
        >>> signature = auth.sign_order(order_data)
    """

    def __init__(self, private_key: str):
        """Initialize with private key.

        Args:
            private_key: Hex-encoded private key (with or without 0x prefix)

        Raises:
            ValueError: If private key is invalid
        """
        self._wallet = PolymarketWallet(private_key=private_key)

    @classmethod
    def from_env(cls) -> "PolymarketAuth":
        """Create auth handler from POLYMARKET_PRIVATE_KEY env var.

        Returns:
            PolymarketAuth instance

        Raises:
            ValueError: If POLYMARKET_PRIVATE_KEY not set
        """
        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not private_key:
            raise ValueError("POLYMARKET_PRIVATE_KEY environment variable not set")
        return cls(private_key)

    @property
    def address(self) -> str:
        """Get wallet address (checksummed)."""
        return self._wallet.address

    @property
    def wallet(self) -> PolymarketWallet:
        """Get underlying wallet instance."""
        return self._wallet

    def sign_request(
        self,
        method: str,
        path: str,
        body: str = "",
    ) -> Dict[str, str]:
        """Generate signed L2 headers for a CLOB API request.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path: Request path (e.g., "/orders")
            body: Request body as string

        Returns:
            Dictionary of authentication headers
        """
        return self._wallet.create_l2_headers(
            method=method,
            path=path,
            body=body if body else None,
        )

    def sign_order(self, order_data: Dict[str, Any]) -> str:
        """Sign an order using EIP-712 typed data signing.

        Args:
            order_data: Order data dict matching CLOB order schema

        Returns:
            Hex-encoded signature string
        """
        return self._wallet.sign_order(order_data)
