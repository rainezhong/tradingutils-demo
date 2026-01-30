"""Custom exceptions for Kalshi API operations."""

from typing import Optional


class KalshiError(Exception):
    """Base exception for Kalshi API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.status_code}] {self.message}"
        return self.message


class AuthenticationError(KalshiError):
    """Authentication failed (invalid API key, signature, or expired timestamp)."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401)


class RateLimitError(KalshiError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[int] = None,
    ):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after

    def __str__(self) -> str:
        base = super().__str__()
        if self.retry_after:
            return f"{base} (retry after {self.retry_after}s)"
        return base


class WebSocketError(KalshiError):
    """WebSocket connection or communication error."""

    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        reason: Optional[str] = None,
    ):
        super().__init__(message)
        self.code = code
        self.reason = reason

    def __str__(self) -> str:
        parts = [self.message]
        if self.code:
            parts.append(f"code={self.code}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        return " ".join(parts)


class OrderBookError(KalshiError):
    """Order book state error (e.g., sequence gap, invalid delta)."""

    def __init__(
        self,
        message: str,
        ticker: Optional[str] = None,
        expected_seq: Optional[int] = None,
        received_seq: Optional[int] = None,
    ):
        super().__init__(message)
        self.ticker = ticker
        self.expected_seq = expected_seq
        self.received_seq = received_seq

    def __str__(self) -> str:
        base = self.message
        if self.ticker:
            base = f"[{self.ticker}] {base}"
        if self.expected_seq is not None and self.received_seq is not None:
            base = f"{base} (expected seq={self.expected_seq}, got {self.received_seq})"
        return base


class MarketNotFoundError(KalshiError):
    """Market does not exist."""

    def __init__(self, ticker: str):
        super().__init__(f"Market not found: {ticker}", status_code=404)
        self.ticker = ticker


class InsufficientFundsError(KalshiError):
    """Insufficient balance for order."""

    def __init__(
        self,
        message: str = "Insufficient funds",
        required: Optional[float] = None,
        available: Optional[float] = None,
    ):
        super().__init__(message, status_code=400)
        self.required = required
        self.available = available

    def __str__(self) -> str:
        base = self.message
        if self.required is not None and self.available is not None:
            base = f"{base} (required: ${self.required:.2f}, available: ${self.available:.2f})"
        return base


class OrderError(KalshiError):
    """Order-related error (rejected, invalid parameters, etc.)."""

    def __init__(
        self,
        message: str,
        order_id: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        super().__init__(message, status_code=400)
        self.order_id = order_id
        self.reason = reason

    def __str__(self) -> str:
        parts = [self.message]
        if self.order_id:
            parts.append(f"order_id={self.order_id}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        return " ".join(parts)


class ConnectionError(KalshiError):
    """Network connection error."""

    def __init__(self, message: str = "Connection failed"):
        super().__init__(message)
