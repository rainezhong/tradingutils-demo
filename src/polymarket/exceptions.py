"""Custom exceptions for Polymarket API client."""


class PolymarketError(Exception):
    """Base exception for Polymarket errors."""

    pass


class PolymarketAPIError(PolymarketError):
    """Raised when API request fails."""

    def __init__(self, message: str, status_code: int = None, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class PolymarketAuthError(PolymarketError):
    """Raised when authentication fails."""

    pass


class PolymarketRateLimitError(PolymarketAPIError):
    """Raised when rate limit is exceeded."""

    def __init__(self, retry_after: float = None):
        super().__init__("Rate limit exceeded", status_code=429)
        self.retry_after = retry_after


class PolymarketOrderError(PolymarketError):
    """Raised when order placement or cancellation fails."""

    pass


class PolymarketWebSocketError(PolymarketError):
    """Raised when WebSocket connection fails."""

    pass


class PolymarketBlockchainError(PolymarketError):
    """Raised when blockchain interaction fails."""

    pass


class PolymarketInsufficientFundsError(PolymarketError):
    """Raised when account has insufficient funds."""

    def __init__(self, required: float, available: float):
        super().__init__(f"Insufficient funds: need {required}, have {available}")
        self.required = required
        self.available = available
