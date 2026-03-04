"""Custom exceptions for Kalshi API client.

Provides structured error handling with error codes and context.
"""

from typing import Optional


class KalshiError(Exception):
    """Base exception for all Kalshi API errors."""

    def __init__(self, message: str, code: Optional[str] = None):
        self.message = message
        self.code = code
        super().__init__(message)


class KalshiAuthError(KalshiError):
    """Authentication failed (401 Unauthorized).

    Common causes:
    - Invalid API key
    - Invalid/expired signature
    - System clock out of sync
    """

    pass


class KalshiNotFoundError(KalshiError):
    """Resource not found (404 Not Found).

    Common causes:
    - Invalid market ticker
    - Order already filled/canceled
    - Endpoint doesn't exist
    """

    def __init__(self, resource: str, message: Optional[str] = None):
        self.resource = resource
        msg = message or f"Resource not found: {resource}"
        super().__init__(msg, code="not_found")


class KalshiRateLimitError(KalshiError):
    """Rate limit exceeded (429 Too Many Requests).

    Attributes:
        retry_after: Seconds to wait before retrying
    """

    def __init__(self, retry_after: int = 1):
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded. Retry after {retry_after}s",
            code="rate_limit",
        )


class KalshiBadRequestError(KalshiError):
    """Invalid request parameters (400 Bad Request).

    Common causes:
    - Invalid order parameters
    - Insufficient balance
    - Market closed
    """

    pass


class KalshiConnectionError(KalshiError):
    """Network or connection error.

    Common causes:
    - Network timeout
    - DNS resolution failed
    - Connection refused
    """

    pass


class KalshiTimeoutError(KalshiConnectionError):
    """Request timed out."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"Request timed out after {timeout}s", code="timeout")


class KalshiMaxRetriesError(KalshiError):
    """Maximum retry attempts exceeded."""

    def __init__(self, method: str, endpoint: str, attempts: int):
        self.method = method
        self.endpoint = endpoint
        self.attempts = attempts
        super().__init__(
            f"Max retries ({attempts}) exceeded for {method} {endpoint}",
            code="max_retries",
        )


class WebSocketError(KalshiError):
    """WebSocket connection or communication error.

    Common causes:
    - Connection failed
    - Connection dropped
    - Authentication rejected
    - Subscription failed
    """

    pass
