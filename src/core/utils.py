"""Shared utilities for the trading system."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from .config import Config, get_config


def setup_logger(
    name: str,
    level: Optional[str] = None,
    format_str: Optional[str] = None,
) -> logging.Logger:
    """
    Set up and return a configured logger.

    Args:
        name: Logger name (typically __name__)
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_str: Log message format string

    Returns:
        Configured logger instance
    """
    config = get_config()
    level = level or config.log_level
    format_str = format_str or config.log_format

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(getattr(logging, level.upper()))
        formatter = logging.Formatter(format_str)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC datetime as ISO8601 string."""
    return utc_now().isoformat()


def parse_iso_timestamp(timestamp: str) -> datetime:
    """
    Parse an ISO8601 timestamp string to datetime.

    Args:
        timestamp: ISO8601 formatted string

    Returns:
        datetime object (timezone-aware if timezone info present)
    """
    # Handle 'Z' suffix for UTC
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"
    return datetime.fromisoformat(timestamp)


def ensure_directory(path: str) -> Path:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        path: Path to directory

    Returns:
        Path object for the directory
    """
    dir_path = Path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


@contextmanager
def get_db_connection(
    db_path: Optional[str] = None,
) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager for database connections.

    Args:
        db_path: Path to SQLite database file

    Yields:
        SQLite connection with Row factory
    """
    config = get_config()
    path = db_path or config.db_path

    # Ensure parent directory exists
    ensure_directory(str(Path(path).parent))

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        yield conn
    finally:
        conn.close()


def calculate_spread(yes_bid: int, yes_ask: int) -> tuple[int, float, float]:
    """
    Calculate spread metrics from bid/ask prices.

    Args:
        yes_bid: Best bid price in cents (0-100)
        yes_ask: Best ask price in cents (0-100)

    Returns:
        Tuple of (spread_cents, spread_pct, mid_price)
    """
    spread_cents = yes_ask - yes_bid
    mid_price = (yes_bid + yes_ask) / 2
    spread_pct = (spread_cents / mid_price) * 100 if mid_price > 0 else 0.0
    return spread_cents, spread_pct, mid_price
