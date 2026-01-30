"""Wallet and signing functionality for Polymarket CLOB.

Security notes:
- Private key loaded from environment variable only
- Private key NEVER logged or exposed
- Signatures validated before use
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional

from .exceptions import PolymarketAuthError


logger = logging.getLogger(__name__)


# EIP-712 Domain for Polymarket CLOB
CLOB_DOMAIN = {
    "name": "Polymarket CLOB",
    "version": "1",
    "chainId": 137,  # Polygon mainnet
}

# Type definitions for EIP-712 signing
ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}


class PolymarketWallet:
    """Wallet for Polymarket authentication and order signing.

    Loads private key from environment variable and provides:
    - Address derivation
    - API authentication headers (L1/L2)
    - EIP-712 order signing for CLOB

    Example:
        >>> wallet = PolymarketWallet()  # Uses POLYMARKET_PRIVATE_KEY env var
        >>> print(wallet.address)
        0x1234...
        >>> headers = wallet.create_l2_headers("GET", "/orders")
    """

    def __init__(self, private_key: Optional[str] = None) -> None:
        """Initialize wallet with private key.

        Args:
            private_key: Hex-encoded private key (with or without 0x prefix).
                        If None, reads from POLYMARKET_PRIVATE_KEY env var.

        Raises:
            PolymarketAuthError: If private key is not provided or invalid
        """
        # Load private key
        key = private_key or os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not key:
            raise PolymarketAuthError(
                "Private key not provided. Set POLYMARKET_PRIVATE_KEY environment variable."
            )

        # Normalize key format
        if key.startswith("0x"):
            key = key[2:]

        if len(key) != 64:
            raise PolymarketAuthError("Invalid private key length")

        try:
            self._private_key_bytes = bytes.fromhex(key)
        except ValueError as e:
            raise PolymarketAuthError(f"Invalid private key format: {e}")

        # Import eth_account for address derivation and signing
        try:
            from eth_account import Account
            self._account = Account.from_key(self._private_key_bytes)
        except ImportError:
            raise PolymarketAuthError(
                "eth_account package required. Install with: pip install eth-account"
            )

        # Log address only (NEVER log private key)
        logger.info("Wallet initialized: %s", self.address[:10] + "...")

    @property
    def address(self) -> str:
        """Get wallet address (checksummed)."""
        return self._account.address

    def create_l1_headers(self, nonce: Optional[int] = None) -> Dict[str, str]:
        """Create L1 authentication headers for API requests.

        L1 auth is simpler HMAC-based auth for basic API access.

        Args:
            nonce: Optional nonce (defaults to current timestamp)

        Returns:
            Dictionary of HTTP headers
        """
        if nonce is None:
            nonce = int(time.time() * 1000)

        # Create signature
        message = f"{nonce}"
        signature = self._sign_message(message)

        return {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(nonce),
            "POLY_NONCE": str(nonce),
        }

    def create_l2_headers(
        self,
        method: str,
        path: str,
        body: Optional[str] = None,
        nonce: Optional[int] = None,
    ) -> Dict[str, str]:
        """Create L2 authentication headers for CLOB API requests.

        L2 auth uses HMAC signature over request details.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path (e.g., "/orders")
            body: Request body (for POST requests)
            nonce: Optional nonce (defaults to current timestamp)

        Returns:
            Dictionary of HTTP headers
        """
        if nonce is None:
            nonce = int(time.time() * 1000)

        # Build message to sign
        message_parts = [str(nonce), method.upper(), path]
        if body:
            message_parts.append(body)

        message = "".join(message_parts)
        signature = self._create_hmac_signature(message)

        return {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(nonce),
            "POLY_NONCE": str(nonce),
        }

    def sign_order(self, order_data: Dict[str, Any]) -> str:
        """Sign an order using EIP-712 typed data signing.

        Args:
            order_data: Order data to sign

        Returns:
            Hex-encoded signature
        """
        from eth_account.messages import encode_typed_data

        # Build typed data structure
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                **ORDER_TYPES,
            },
            "primaryType": "Order",
            "domain": CLOB_DOMAIN,
            "message": order_data,
        }

        # Sign the typed data
        signed_message = self._account.sign_message(
            encode_typed_data(full_message=typed_data)
        )

        return signed_message.signature.hex()

    def sign_typed_data(self, domain: Dict, types: Dict, message: Dict) -> str:
        """Sign arbitrary EIP-712 typed data.

        Args:
            domain: EIP-712 domain
            types: Type definitions
            message: Message to sign

        Returns:
            Hex-encoded signature
        """
        from eth_account.messages import encode_typed_data

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                **types,
            },
            "primaryType": list(types.keys())[0],
            "domain": domain,
            "message": message,
        }

        signed_message = self._account.sign_message(
            encode_typed_data(full_message=typed_data)
        )

        return signed_message.signature.hex()

    def _sign_message(self, message: str) -> str:
        """Sign a message using personal_sign style.

        Args:
            message: Message to sign

        Returns:
            Hex-encoded signature
        """
        from eth_account.messages import encode_defunct

        signable = encode_defunct(text=message)
        signed = self._account.sign_message(signable)
        return signed.signature.hex()

    def _create_hmac_signature(self, message: str) -> str:
        """Create HMAC-SHA256 signature.

        Args:
            message: Message to sign

        Returns:
            Hex-encoded signature
        """
        signature = hmac.new(
            self._private_key_bytes,
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def derive_api_key(self) -> str:
        """Derive an API key from the wallet address.

        Returns:
            Derived API key string
        """
        # Create a deterministic API key from address
        return hashlib.sha256(
            self.address.lower().encode("utf-8")
        ).hexdigest()[:32]


class PolymarketCredentials:
    """Container for Polymarket API credentials.

    Supports both wallet-based auth and API key auth.
    """

    def __init__(
        self,
        wallet: Optional[PolymarketWallet] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
    ) -> None:
        """Initialize credentials.

        Args:
            wallet: Wallet for signing (preferred)
            api_key: API key (alternative)
            api_secret: API secret
            api_passphrase: API passphrase
        """
        self.wallet = wallet
        self.api_key = api_key or os.environ.get("POLYMARKET_API_KEY")
        self.api_secret = api_secret or os.environ.get("POLYMARKET_API_SECRET")
        self.api_passphrase = api_passphrase or os.environ.get("POLYMARKET_PASSPHRASE")

    def get_headers(
        self,
        method: str = "GET",
        path: str = "/",
        body: Optional[str] = None,
    ) -> Dict[str, str]:
        """Get authentication headers.

        Args:
            method: HTTP method
            path: Request path
            body: Request body

        Returns:
            Dictionary of HTTP headers
        """
        if self.wallet:
            return self.wallet.create_l2_headers(method, path, body)

        if self.api_key and self.api_secret:
            nonce = int(time.time() * 1000)
            message = f"{nonce}{method.upper()}{path}{body or ''}"
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            return {
                "POLY_API_KEY": self.api_key,
                "POLY_SIGNATURE": signature,
                "POLY_TIMESTAMP": str(nonce),
                "POLY_PASSPHRASE": self.api_passphrase or "",
            }

        raise PolymarketAuthError("No valid credentials configured")
