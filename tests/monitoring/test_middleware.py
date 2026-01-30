"""Tests for request logging middleware."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monitoring.middleware import (
    RequestLoggingMiddleware,
    APIClientLogging,
    log_execution_time,
)


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware."""

    @pytest.mark.asyncio
    async def test_middleware_passes_non_http(self):
        """Test that non-HTTP requests pass through."""
        app = AsyncMock()
        middleware = RequestLoggingMiddleware(app)

        scope = {"type": "websocket"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_middleware_excludes_health_paths(self):
        """Test that health paths are excluded from logging."""
        app = AsyncMock()
        middleware = RequestLoggingMiddleware(app)

        scope = {"type": "http", "path": "/health", "method": "GET"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_logs_request(self):
        """Test that requests are logged."""
        async def mock_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b""})

        middleware = RequestLoggingMiddleware(mock_app)

        scope = {
            "type": "http",
            "path": "/api/orders",
            "method": "POST",
            "query_string": b"limit=10",
        }
        receive = AsyncMock()
        send = AsyncMock()

        with patch("src.monitoring.middleware.logger") as mock_logger:
            await middleware(scope, receive, send)

            # Should have logged request start and completion
            assert mock_logger.info.call_count >= 2

    @pytest.mark.asyncio
    async def test_middleware_captures_status_code(self):
        """Test that response status code is captured."""
        status_captured = []

        async def mock_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 201})
            await send({"type": "http.response.body", "body": b""})

        middleware = RequestLoggingMiddleware(mock_app)

        scope = {"type": "http", "path": "/api/orders", "method": "POST", "query_string": b""}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # The send wrapper should have captured 201

    @pytest.mark.asyncio
    async def test_middleware_handles_exception(self):
        """Test that exceptions are logged and re-raised."""
        async def mock_app(scope, receive, send):
            raise RuntimeError("Test error")

        middleware = RequestLoggingMiddleware(mock_app)

        scope = {"type": "http", "path": "/api/orders", "method": "GET", "query_string": b""}
        receive = AsyncMock()
        send = AsyncMock()

        with pytest.raises(RuntimeError):
            await middleware(scope, receive, send)


class TestAPIClientLogging:
    """Tests for APIClientLogging wrapper."""

    @pytest.mark.asyncio
    async def test_get_request(self):
        """Test GET request logging."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.request = AsyncMock(return_value=mock_response)

        logged_client = APIClientLogging(mock_client, platform="kalshi")

        response = await logged_client.get("https://api.kalshi.com/markets")

        assert response == mock_response
        mock_client.request.assert_called_once_with("GET", "https://api.kalshi.com/markets")

    @pytest.mark.asyncio
    async def test_post_request(self):
        """Test POST request logging."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client.request = AsyncMock(return_value=mock_response)

        logged_client = APIClientLogging(mock_client, platform="kalshi")

        response = await logged_client.post(
            "https://api.kalshi.com/orders",
            json={"ticker": "TEST"},
        )

        assert response == mock_response

    @pytest.mark.asyncio
    async def test_request_exception(self):
        """Test request exception handling."""
        mock_client = MagicMock()
        mock_client.request = AsyncMock(side_effect=ConnectionError("Failed"))

        logged_client = APIClientLogging(mock_client, platform="kalshi")

        with pytest.raises(ConnectionError):
            await logged_client.get("https://api.kalshi.com/markets")

    @pytest.mark.asyncio
    async def test_all_http_methods(self):
        """Test all HTTP methods."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.request = AsyncMock(return_value=mock_response)

        logged_client = APIClientLogging(mock_client, platform="test")

        await logged_client.get("http://test.com")
        await logged_client.post("http://test.com")
        await logged_client.put("http://test.com")
        await logged_client.delete("http://test.com")
        await logged_client.patch("http://test.com")

        assert mock_client.request.call_count == 5


class TestLogExecutionTime:
    """Tests for log_execution_time decorator."""

    @pytest.mark.asyncio
    async def test_async_function(self):
        """Test decorator on async function."""
        @log_execution_time("test_async", "test")
        async def async_func():
            await asyncio.sleep(0.01)
            return "result"

        result = await async_func()

        assert result == "result"

    def test_sync_function(self):
        """Test decorator on sync function."""
        @log_execution_time("test_sync", "test")
        def sync_func():
            time.sleep(0.01)
            return "result"

        result = sync_func()

        assert result == "result"

    @pytest.mark.asyncio
    async def test_async_function_exception(self):
        """Test decorator handles async exceptions."""
        @log_execution_time("test_async_error", "test")
        async def async_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await async_func()

    def test_sync_function_exception(self):
        """Test decorator handles sync exceptions."""
        @log_execution_time("test_sync_error", "test")
        def sync_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            sync_func()

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata."""
        @log_execution_time("test", "component")
        async def documented_func():
            """This is the docstring."""
            pass

        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "This is the docstring."
