"""Custom exceptions for Kalshi API operations."""

from typing import Optional


class KalshiAPIError(Exception):
    """Base exception for Kalshi API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[dict] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.status_code}] {self.message}"
        return self.message


class AuthenticationError(KalshiAPIError):
    """Authentication failed (invalid API key/secret or signature)."""

    def __init__(
        self,
        message: str = "Authentication failed",
        response_body: Optional[dict] = None,
    ):
        super().__init__(message, status_code=401, response_body=response_body)


class RateLimitError(KalshiAPIError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[int] = None,
        response_body: Optional[dict] = None,
    ):
        super().__init__(message, status_code=429, response_body=response_body)
        self.retry_after = retry_after

    def __str__(self) -> str:
        base = super().__str__()
        if self.retry_after:
            return f"{base} (retry after {self.retry_after}s)"
        return base


class OrderError(KalshiAPIError):
    """Order-related error (rejected, invalid parameters, etc.)."""

    def __init__(
        self,
        message: str,
        order_id: Optional[str] = None,
        reason: Optional[str] = None,
        status_code: Optional[int] = 400,
        response_body: Optional[dict] = None,
    ):
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.order_id = order_id
        self.reason = reason


class MarketNotFoundError(KalshiAPIError):
    """Market does not exist."""

    def __init__(self, ticker: str, response_body: Optional[dict] = None):
        super().__init__(
            f"Market not found: {ticker}",
            status_code=404,
            response_body=response_body,
        )
        self.ticker = ticker


class InsufficientFundsError(KalshiAPIError):
    """Insufficient balance for order."""

    def __init__(
        self,
        message: str = "Insufficient funds",
        required: Optional[float] = None,
        available: Optional[float] = None,
        response_body: Optional[dict] = None,
    ):
        super().__init__(message, status_code=400, response_body=response_body)
        self.required = required
        self.available = available


class WebSocketError(KalshiAPIError):
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


class OrderBookError(KalshiAPIError):
    """Order book state error (sequence gap, invalid state)."""

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
