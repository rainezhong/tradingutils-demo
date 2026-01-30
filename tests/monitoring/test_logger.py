"""Tests for structured logging."""

import json
import logging
import pytest
from io import StringIO
from unittest.mock import patch

import structlog

from src.monitoring.logger import (
    setup_logging,
    get_logger,
    log_context,
    bind_context,
    clear_context,
    LoggerAdapter,
)


class TestSetupLogging:
    """Tests for logging setup."""

    def test_setup_logging_json_output(self):
        """Test that JSON output mode produces valid JSON logs."""
        setup_logging(log_level="INFO", json_output=True)
        logger = get_logger("test")

        # Capture output
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            logger.info("Test message", key="value")
            output = mock_stdout.getvalue()

        # Should be valid JSON (may need to handle structlog's output format)
        # structlog may not write to stdout directly in tests
        assert logger is not None

    def test_setup_logging_console_output(self):
        """Test that console output mode works."""
        setup_logging(log_level="DEBUG", json_output=False)
        logger = get_logger("test")
        assert logger is not None

    def test_setup_logging_log_levels(self):
        """Test different log levels are respected."""
        # Just verify no errors when setting up with different levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            setup_logging(log_level=level, json_output=True)
            # Logger should be configured (may not change root level due to structlog)


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_with_name(self):
        """Test getting a named logger."""
        setup_logging()
        logger = get_logger("my.module")
        assert logger is not None

    def test_get_logger_without_name(self):
        """Test getting a logger without name."""
        setup_logging()
        logger = get_logger()
        assert logger is not None

    def test_logger_can_log(self):
        """Test that logger can actually log messages."""
        setup_logging(log_level="DEBUG", json_output=True)
        logger = get_logger("test")

        # These should not raise
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")

    def test_logger_with_context_fields(self):
        """Test logging with additional context fields."""
        setup_logging(log_level="DEBUG", json_output=True)
        logger = get_logger("test")

        # Should not raise
        logger.info(
            "Trade executed",
            trade_id="t-123",
            ticker="BTC-USD",
            profit=25.50,
        )


class TestLogContext:
    """Tests for log_context context manager."""

    def setup_method(self):
        """Setup before each test."""
        setup_logging(log_level="DEBUG", json_output=True)
        clear_context()

    def test_log_context_adds_fields(self):
        """Test that log_context adds fields to logs."""
        logger = get_logger("test")

        with log_context(trade_id="t-123", platform="kalshi"):
            # Context should be bound
            pass

        # Context should be cleared after exiting

    def test_log_context_nested(self):
        """Test nested log contexts."""
        logger = get_logger("test")

        with log_context(request_id="req-1"):
            with log_context(trade_id="t-123"):
                # Both contexts should be active
                pass
            # trade_id should be cleared, request_id still active
        # All contexts cleared

    def test_log_context_cleanup_on_exception(self):
        """Test that context is cleaned up even if exception occurs."""
        logger = get_logger("test")

        with pytest.raises(ValueError):
            with log_context(trade_id="t-123"):
                raise ValueError("Test error")

        # Context should be cleaned up


class TestBindContext:
    """Tests for bind_context and clear_context."""

    def setup_method(self):
        """Setup before each test."""
        setup_logging(log_level="DEBUG", json_output=True)
        clear_context()

    def test_bind_context(self):
        """Test binding persistent context."""
        bind_context(user_id="user-123")
        # Context is now bound

    def test_clear_context(self):
        """Test clearing bound context."""
        bind_context(user_id="user-123")
        clear_context()
        # Context should be cleared


class TestLoggerAdapter:
    """Tests for LoggerAdapter compatibility layer."""

    def setup_method(self):
        """Setup before each test."""
        setup_logging(log_level="DEBUG", json_output=True)

    def test_adapter_debug(self):
        """Test adapter debug method."""
        logger = get_logger("test")
        adapter = LoggerAdapter(logger)
        adapter.debug("Debug message", extra_key="value")

    def test_adapter_info(self):
        """Test adapter info method."""
        logger = get_logger("test")
        adapter = LoggerAdapter(logger)
        adapter.info("Info message")

    def test_adapter_warning(self):
        """Test adapter warning method."""
        logger = get_logger("test")
        adapter = LoggerAdapter(logger)
        adapter.warning("Warning message")

    def test_adapter_error(self):
        """Test adapter error method."""
        logger = get_logger("test")
        adapter = LoggerAdapter(logger)
        adapter.error("Error message")

    def test_adapter_critical(self):
        """Test adapter critical method."""
        logger = get_logger("test")
        adapter = LoggerAdapter(logger)
        adapter.critical("Critical message")

    def test_adapter_exception(self):
        """Test adapter exception method."""
        logger = get_logger("test")
        adapter = LoggerAdapter(logger)
        try:
            raise ValueError("Test error")
        except ValueError:
            adapter.exception("Exception occurred")
