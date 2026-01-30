"""Request Logging Middleware.

Provides middleware for logging HTTP requests with:
- Request/response logging
- Timing metrics
- Request ID tracking
- Error capture

Example:
    from fastapi import FastAPI
    from src.monitoring.middleware import RequestLoggingMiddleware, add_request_logging

    app = FastAPI()
    add_request_logging(app)

    # Or manually
    app.add_middleware(RequestLoggingMiddleware)
"""

import time
import uuid
from typing import Any, Callable, List, Optional

from .logger import bind_context, clear_context, get_logger
from .metrics import API_LATENCY, API_REQUESTS, ERROR_COUNT

logger = get_logger(__name__)


class RequestLoggingMiddleware:
    """ASGI middleware for request logging and metrics.

    Logs all incoming requests with:
    - Request method, path, and query params
    - Response status code
    - Request duration
    - Request ID for correlation

    Also records metrics for latency and request counts.
    """

    def __init__(
        self,
        app: Any,
        excluded_paths: Optional[List[str]] = None,
        log_request_body: bool = False,
        log_response_body: bool = False,
    ):
        """Initialize middleware.

        Args:
            app: ASGI application
            excluded_paths: Paths to exclude from logging (e.g., /health)
            log_request_body: Whether to log request body (careful with sensitive data)
            log_response_body: Whether to log response body
        """
        self.app = app
        self.excluded_paths = excluded_paths or ["/health", "/health/live", "/health/ready", "/metrics"]
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Process request through middleware."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip excluded paths
        if any(path.startswith(excluded) for excluded in self.excluded_paths):
            await self.app(scope, receive, send)
            return

        # Generate request ID
        request_id = str(uuid.uuid4())[:8]

        # Bind request context
        bind_context(
            request_id=request_id,
            method=scope.get("method", ""),
            path=path,
        )

        start_time = time.time()
        status_code = 500  # Default to error in case we don't get a response

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            # Log request
            logger.info(
                "Request started",
                query_string=scope.get("query_string", b"").decode(),
            )

            await self.app(scope, receive, send_wrapper)

        except Exception as e:
            status_code = 500
            ERROR_COUNT.labels(component="http", error_type=type(e).__name__).inc()
            logger.error(
                "Request failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

        finally:
            duration = time.time() - start_time

            # Log response
            logger.info(
                "Request completed",
                status_code=status_code,
                duration_ms=round(duration * 1000, 2),
            )

            # Record metrics
            API_LATENCY.labels(
                platform="self",
                endpoint=path,
                method=scope.get("method", "GET"),
            ).observe(duration)

            API_REQUESTS.labels(
                platform="self",
                endpoint=path,
                method=scope.get("method", "GET"),
                status_code=str(status_code),
            ).inc()

            # Clear request context
            clear_context()


def add_request_logging(
    app: Any,
    excluded_paths: Optional[List[str]] = None,
) -> None:
    """Add request logging middleware to a FastAPI/Starlette app.

    Args:
        app: FastAPI or Starlette application
        excluded_paths: Paths to exclude from logging
    """
    app.add_middleware(
        RequestLoggingMiddleware,
        excluded_paths=excluded_paths,
    )


class APIClientLogging:
    """Wrapper to add logging to API client requests.

    Wraps an HTTP client to automatically log requests and record metrics.

    Example:
        import httpx

        client = httpx.AsyncClient()
        logged_client = APIClientLogging(client, platform="kalshi")

        response = await logged_client.get("/markets")
    """

    def __init__(
        self,
        client: Any,
        platform: str,
        log_bodies: bool = False,
    ):
        """Initialize logging wrapper.

        Args:
            client: HTTP client (httpx, aiohttp session, etc.)
            platform: Platform identifier for metrics
            log_bodies: Whether to log request/response bodies
        """
        self._client = client
        self._platform = platform
        self._log_bodies = log_bodies

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        """Make a logged HTTP request.

        Args:
            method: HTTP method
            url: Request URL
            **kwargs: Additional arguments passed to client

        Returns:
            Response from the underlying client
        """
        # Extract endpoint from URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        endpoint = parsed.path

        start_time = time.time()

        logger.debug(
            "API request started",
            platform=self._platform,
            method=method,
            endpoint=endpoint,
        )

        try:
            response = await self._client.request(method, url, **kwargs)
            duration = time.time() - start_time

            # Get status code (works with httpx and aiohttp)
            status_code = getattr(response, "status_code", None) or getattr(response, "status", 0)

            logger.debug(
                "API request completed",
                platform=self._platform,
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                duration_ms=round(duration * 1000, 2),
            )

            # Record metrics
            API_LATENCY.labels(
                platform=self._platform,
                endpoint=endpoint,
                method=method,
            ).observe(duration)

            API_REQUESTS.labels(
                platform=self._platform,
                endpoint=endpoint,
                method=method,
                status_code=str(status_code),
            ).inc()

            return response

        except Exception as e:
            duration = time.time() - start_time

            logger.error(
                "API request failed",
                platform=self._platform,
                method=method,
                endpoint=endpoint,
                error=str(e),
                duration_ms=round(duration * 1000, 2),
            )

            ERROR_COUNT.labels(
                component=f"api_{self._platform}",
                error_type=type(e).__name__,
            ).inc()

            raise

    async def get(self, url: str, **kwargs: Any) -> Any:
        """Make a GET request."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        """Make a POST request."""
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> Any:
        """Make a PUT request."""
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Any:
        """Make a DELETE request."""
        return await self.request("DELETE", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> Any:
        """Make a PATCH request."""
        return await self.request("PATCH", url, **kwargs)


def log_execution_time(
    name: str,
    component: str = "function",
) -> Callable:
    """Decorator to log function execution time.

    Args:
        name: Name to use in logs
        component: Component name for grouping

    Example:
        @log_execution_time("process_order", "execution")
        async def process_order(order):
            # ... processing ...
            pass
    """
    def decorator(func: Callable) -> Callable:
        import functools
        import asyncio

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                logger.debug(
                    "Function completed",
                    function=name,
                    component=component,
                    duration_ms=round(duration * 1000, 2),
                )
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    "Function failed",
                    function=name,
                    component=component,
                    duration_ms=round(duration * 1000, 2),
                    error=str(e),
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                logger.debug(
                    "Function completed",
                    function=name,
                    component=component,
                    duration_ms=round(duration * 1000, 2),
                )
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    "Function failed",
                    function=name,
                    component=component,
                    duration_ms=round(duration * 1000, 2),
                    error=str(e),
                )
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
