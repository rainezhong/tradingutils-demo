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
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
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

    if not api_key: YOUR_API_KEY_HERE ValueError("KALSHI_API_KEY environment variable not set")
    if not private_key:
        raise ValueError("KALSHI_API_SECRET environment variable not set")

    # If private_key looks like a file path, read from file
    if private_key.startswith("/") or private_key.startswith("~"):
        key_path = os.path.expanduser(private_key)
        if os.path.isfile(key_path):
            with open(key_path, "r") as f:
                private_key = f.read()

    return api_key, private_key


def load_private_key(private_key_pem: str):
    """Load and validate RSA private key from PEM string.

    Args:
        private_key_pem: RSA private key in PEM format

    Returns:
        Loaded private key object

    Raises:
        ValueError: If key is invalid or not RSA
    """
    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=None,
            backend=default_backend(),
        )
        if not hasattr(key, "sign"):
            raise ValueError("Key does not support signing")
        return key
    except Exception as e:
        raise ValueError(f"Invalid private key: {e}") from e


def sign_with_key(
    private_key,
    timestamp_ms: int,
    method: str,
    path: str,
) -> str:
    """Generate signature using pre-loaded private key (faster).

    Args:
        private_key: Pre-loaded RSA private key object
        timestamp_ms: Request timestamp in milliseconds
        method: HTTP method
        path: Request path

    Returns:
        Base64-encoded signature string
    """
    path_without_query = path.split("?")[0]
    message = f"{timestamp_ms}{method}{path_without_query}"

    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )

    return base64.b64encode(signature).decode("utf-8")


def generate_signature(
    private_key_pem: str,
    timestamp_ms: int,
    method: str,
    path: str,
) -> str:
    """Generate RSA-PSS signature for Kalshi API request.

    Args:
        private_key_pem: RSA private key in PEM format
        timestamp_ms: Request timestamp in milliseconds
        method: HTTP method (GET, POST, DELETE, etc.)
        path: Request path (e.g., /trade-api/v2/markets)

    Returns:
        Base64-encoded signature string

    Note:
        For high-frequency use, prefer KalshiAuth.sign_request() which
        caches the parsed key for better performance.
    """
    private_key = load_private_key(private_key_pem)
    return sign_with_key(private_key, timestamp_ms, method, path)


def generate_auth_headers(
    api_key: YOUR_API_KEY_HERE,
    api_secret: str,
    method: str,
    path: str,
    body: str = "",
    timestamp_ms: Optional[int] = None,
) -> Dict[str, str]:
    """Generate authentication headers for Kalshi API request.

    Args:
        api_key: YOUR_API_KEY_HERE key
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

    def __init__(self, api_key: YOUR_API_KEY_HERE, api_secret: str):
        """Initialize with API credentials.

        Args:
            api_key: YOUR_API_KEY_HERE API key
            api_secret: Kalshi API secret (PEM format)

        Raises:
            ValueError: If api_secret is not a valid RSA private key
        """
        self._api_key = api_key
        self._api_secret = api_secret
        # Parse and cache the private key for performance (~1ms savings per request)
        self._private_key = load_private_key(api_secret)

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

    @classmethod
    def from_user(cls, username: str, keys_dir: Optional[str] = None) -> "KalshiAuth":
        """Create auth handler from a user's key folder.

        Looks for keys/{username}/{username}_id.txt and {username}_key.pem.

        Args:
            username: User profile name (e.g., "liam")
            keys_dir: Override path to the keys directory.
                      Defaults to {project_root}/keys/

        Returns:
            KalshiAuth instance

        Raises:
            FileNotFoundError: If user folder or key files don't exist
            ValueError: If key files are empty or invalid
        """
        if keys_dir is None:
            # Default: {project_root}/keys/
            keys_dir = str(Path(__file__).parent.parent.parent.parent / "keys")

        user_dir = Path(keys_dir) / username

        if not user_dir.is_dir():
            available = cls.list_users(keys_dir)
            raise FileNotFoundError(
                f"User '{username}' not found in {keys_dir}. "
                f"Available users: {available}"
            )

        id_file = user_dir / f"{username}_id.txt"
        key_file = user_dir / f"{username}_key.pem"

        if not id_file.exists():
            raise FileNotFoundError(f"API key file not found: {id_file}")
        if not key_file.exists():
            raise FileNotFoundError(f"Private key file not found: {key_file}")

        api_key = id_file.read_text().strip()
        api_secret = key_file.read_text().strip()

        if not api_key: YOUR_API_KEY_HERE ValueError(f"API key file is empty: {id_file}")
        if not api_secret:
            raise ValueError(f"Private key file is empty: {key_file}")

        return cls(api_key, api_secret)

    @staticmethod
    def list_users(keys_dir: Optional[str] = None) -> list:
        """List available user profiles in the keys directory.

        Args:
            keys_dir: Override keys directory path.
                      Defaults to {project_root}/keys/

        Returns:
            List of username strings
        """
        if keys_dir is None:
            keys_dir = str(Path(__file__).parent.parent.parent.parent / "keys")

        keys_path = Path(keys_dir)
        if not keys_path.is_dir():
            return []

        return sorted(
            [
                d.name
                for d in keys_path.iterdir()
                if d.is_dir() and (d / f"{d.name}_key.pem").exists()
            ]
        )

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
            body: Request body as string (unused, kept for API compatibility)
            timestamp_ms: Optional timestamp

        Returns:
            Dictionary of authentication headers
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)

        # Use cached private key for better performance
        signature = sign_with_key(
            private_key=self._private_key,
            timestamp_ms=timestamp_ms,
            method=method,
            path=path,
        )

        return {
            "KALSHI-ACCESS-KEY": self._api_key,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        }
