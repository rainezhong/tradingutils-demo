"""Structured Logging Configuration.

Provides JSON-formatted structured logging using structlog with:
- Contextual fields (trade_id, opportunity_id, ticker, platform)
- Request ID tracking via context variables
- Automatic exception formatting
- Multiple output formats (JSON for production, console for development)

Example:
    from src.monitoring.logger import setup_logging, get_logger, log_context

    # Initialize once at startup
    setup_logging(log_level="INFO", json_output=True)

    # Get logger instance
    logger = get_logger(__name__)

    # Basic logging
    logger.info("Trade executed", ticker="BTC-USD", profit=25.50)

    # With context block
    with log_context(trade_id="t-123", platform="kalshi"):
        logger.info("Processing trade")  # Automatically includes trade_id and platform
        logger.debug("Order details", size=100, price=0.65)
"""

import logging
import sys
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, unbind_contextvars


def setup_logging(
    log_level: str = "INFO",
    json_output: bool = True,
    log_file: Optional[str] = None,
) -> None:
    """Configure structured logging for the application.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: If True, output JSON formatted logs; if False, use console renderer
        log_file: Optional file path to write logs to (in addition to stdout)

    Example:
        # Production setup
        setup_logging(log_level="INFO", json_output=True)

        # Development setup
        setup_logging(log_level="DEBUG", json_output=False)
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
    )

    # Add file handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(numeric_level)
        logging.getLogger().addHandler(file_handler)

    # Define structlog processors
    shared_processors: List[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _add_caller_info,
    ]

    if json_output:
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _add_caller_info(
    logger: logging.Logger,
    method_name: str,
    event_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Add caller file and line number to log events."""
    # Skip adding caller info if already present
    if "caller" not in event_dict:
        import inspect

        # Walk up the stack to find the actual caller
        frame = inspect.currentframe()
        if frame is not None:
            # Skip structlog internal frames
            for _ in range(10):
                frame = frame.f_back
                if frame is None:
                    break
                filename = frame.f_code.co_filename
                if "structlog" not in filename and "logging" not in filename:
                    event_dict["caller"] = f"{frame.f_code.co_filename}:{frame.f_lineno}"
                    break

    return event_dict


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Get a configured logger instance.

    Args:
        name: Logger name, typically __name__ of the calling module

    Returns:
        Configured structlog BoundLogger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Application started", version="1.0.0")
    """
    return structlog.get_logger(name)


@contextmanager
def log_context(**kwargs: Any) -> Generator[None, None, None]:
    """Context manager for adding context to all logs within the block.

    All logs emitted within this context will automatically include
    the specified key-value pairs.

    Args:
        **kwargs: Key-value pairs to add to log context

    Example:
        with log_context(trade_id="t-123", platform="kalshi"):
            logger.info("Processing trade")  # Includes trade_id and platform
            process_order()  # Any logs in here also include context
            logger.info("Trade completed")
    """
    bind_contextvars(**kwargs)
    try:
        yield
    finally:
        unbind_contextvars(*kwargs.keys())


def bind_context(**kwargs: Any) -> None:
    """Bind context variables that persist until explicitly cleared.

    Unlike log_context(), these bindings persist across function calls
    until clear_context() or unbind is called.

    Args:
        **kwargs: Key-value pairs to bind to log context

    Example:
        bind_context(request_id="req-456", user_id="user-789")
        logger.info("Request started")
        # ... later ...
        clear_context()
    """
    bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all bound context variables.

    Example:
        bind_context(request_id="req-456")
        # ... do work ...
        clear_context()  # Remove all context
    """
    clear_contextvars()


class LoggerAdapter:
    """Adapter to make structlog work with code expecting standard logging.

    This allows gradual migration from standard logging to structlog.

    Example:
        # Existing code expects logging.Logger
        def process(logger: logging.Logger):
            logger.info("Processing")

        # Can now pass structlog logger wrapped in adapter
        adapter = LoggerAdapter(get_logger(__name__))
        process(adapter)
    """

    def __init__(self, structlog_logger: structlog.stdlib.BoundLogger):
        self._logger = structlog_logger

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.exception(msg, *args, **kwargs)
