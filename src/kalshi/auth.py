"""Authentication helpers for Kalshi API using RSA-SHA256."""

import base64
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# Auto-load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    # Look for .env in project root
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv not installed, rely on environment variables


def get_credentials_from_env() -> Tuple[str, str]:
    """Get API credentials from environment variables.

    Returns:
        Tuple of (api_key, private_key_pem)

    Raises:
        ValueError: If credentials are not set

    Notes:
        KALSHI_API_SECRET can be either:
        - The PEM key content directly
        - A path to a .pem file (if it starts with / or ~)
    """
    api_key = os.environ.get("KALSHI_API_KEY", "")
    private_key = os.environ.get("KALSHI_API_SECRET", "")

    if not api_key:
        raise ValueError("KALSHI_API_KEY environment variable not set")
    if not private_key:
        raise ValueError("KALSHI_API_SECRET environment variable not set")

    # If private_key looks like a file path, read from file
    if private_key.startswith("/") or private_key.startswith("~"):
        key_path = os.path.expanduser(private_key)
        if os.path.isfile(key_path):
            with open(key_path, "r") as f:
                private_key = f.read()

    return api_key, private_key


def generate_signature(
    private_key_pem: str,
    timestamp_ms: int,
    method: str,
    path: str,
    body: str = "",
) -> str:
    """Generate RSA-PSS signature for Kalshi API request.

    Args:
        private_key_pem: RSA private key in PEM format
        timestamp_ms: Request timestamp in milliseconds
        method: HTTP method (GET, POST, DELETE, etc.)
        path: Request path (e.g., /trade-api/v2/markets)
        body: Request body as string (empty for GET requests)

    Returns:
        Base64-encoded signature string
    """
    # Create message to sign: timestamp + method + path (without query params)
    path_without_query = path.split('?')[0]
    message = f"{timestamp_ms}{method}{path_without_query}"

    # Load the private key
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
        backend=default_backend(),
    )

    # Sign with RSA-PSS (Kalshi's required format)
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    # Return base64-encoded signature
    return base64.b64encode(signature).decode("utf-8")


def generate_auth_headers(
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    body: str = "",
    timestamp_ms: Optional[int] = None,
) -> Dict[str, str]:
    """Generate authentication headers for Kalshi API request.

    Args:
        api_key: API key
        api_secret: API secret
        method: HTTP method
        path: Request path (full path including /trade-api/v2)
        body: Request body as string
        timestamp_ms: Optional timestamp (uses current time if not provided)

    Returns:
        Dictionary of headers to include in request

    Example:
        >>> headers = generate_auth_headers(
        ...     "my-key",
        ...     "my-secret",
        ...     "POST",
        ...     "/trade-api/v2/portfolio/orders",
        ...     '{"ticker": "ABC", "side": "yes"}',
        ... )
        >>> # Returns: {"KALSHI-ACCESS-KEY": "...", "KALSHI-ACCESS-SIGNATURE": "...", ...}
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    signature = generate_signature(
        private_key_pem=api_secret,
        timestamp_ms=timestamp_ms,
        method=method,
        path=path,
        body=body,
    )

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
    }


class KalshiAuth:
    """Authentication handler for Kalshi API.

    Manages API credentials and generates signed headers for requests.

    Example:
        >>> auth = KalshiAuth.from_env()
        >>> headers = auth.sign_request("GET", "/trade-api/v2/markets")
    """

    def __init__(self, api_key: str, api_secret: str):
        """Initialize with API credentials.

        Args:
            api_key: Kalshi API key
            api_secret: Kalshi API secret
        """
        self._api_key = api_key
        self._api_secret = api_secret

    @classmethod
    def from_env(cls) -> "KalshiAuth":
        """Create auth handler from environment variables.

        Returns:
            KalshiAuth instance

        Raises:
            ValueError: If credentials are not set
        """
        api_key, api_secret = get_credentials_from_env()
        return cls(api_key, api_secret)

    @property
    def api_key(self) -> str:
        """Get the API key."""
        return self._api_key

    def sign_request(
        self,
        method: str,
        path: str,
        body: str = "",
        timestamp_ms: Optional[int] = None,
    ) -> Dict[str, str]:
        """Generate signed headers for a request.

        Args:
            method: HTTP method
            path: Full request path
            body: Request body as string
            timestamp_ms: Optional timestamp

        Returns:
            Dictionary of authentication headers
        """
        return generate_auth_headers(
            api_key=self._api_key,
            api_secret=self._api_secret,
            method=method,
            path=path,
            body=body,
            timestamp_ms=timestamp_ms,
        )
