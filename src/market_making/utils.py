"""Utility functions for market-making operations.

All price functions work with the 0-1 probability range.
"""

from .constants import (
    CENTS_TO_PROB,
    MAX_PRICE,
    MAX_QUOTE_SIZE,
    MIN_PRICE,
    MIN_QUOTE_SIZE,
    VALID_SIDES,
)


def validate_price(price: float) -> bool:
    """Check if a price is within valid range.

    Args:
        price: Price to validate (0-1 range).

    Returns:
        True if price is valid.

    Example:
        >>> validate_price(0.45)
        True
        >>> validate_price(1.5)
        False
        >>> validate_price(-0.1)
        False
    """
    return MIN_PRICE <= price <= MAX_PRICE


def validate_spread(bid: float, ask: float) -> bool:
    """Check if bid/ask spread is valid.

    Validates that:
    - Both prices are in valid range
    - Bid is less than ask

    Args:
        bid: Bid price (0-1 range).
        ask: Ask price (0-1 range).

    Returns:
        True if spread is valid.

    Example:
        >>> validate_spread(0.45, 0.48)
        True
        >>> validate_spread(0.48, 0.45)
        False
        >>> validate_spread(0.45, 0.45)
        False
    """
    return validate_price(bid) and validate_price(ask) and bid < ask


def validate_size(size: int) -> bool:
    """Check if quote size is within valid range.

    Args:
        size: Number of contracts.

    Returns:
        True if size is valid.

    Example:
        >>> validate_size(20)
        True
        >>> validate_size(0)
        False
        >>> validate_size(200)
        False
    """
    return MIN_QUOTE_SIZE <= size <= MAX_QUOTE_SIZE


def validate_side(side: str) -> bool:
    """Check if side is valid.

    Args:
        side: Order side ('BID' or 'ASK').

    Returns:
        True if side is valid.

    Example:
        >>> validate_side("BID")
        True
        >>> validate_side("SELL")
        False
    """
    return side in VALID_SIDES


def calculate_mid(bid: float, ask: float) -> float:
    """Calculate mid price from bid and ask.

    Args:
        bid: Bid price (0-1 range).
        ask: Ask price (0-1 range).

    Returns:
        Mid price.

    Example:
        >>> calculate_mid(0.45, 0.50)
        0.475
    """
    return (bid + ask) / 2


def calculate_spread_pct(bid: float, ask: float) -> float:
    """Calculate spread as percentage of mid price.

    Args:
        bid: Bid price (0-1 range).
        ask: Ask price (0-1 range).

    Returns:
        Spread percentage (e.g., 0.05 for 5% spread).

    Example:
        >>> round(calculate_spread_pct(0.45, 0.50), 4)
        0.1053
        >>> calculate_spread_pct(0.50, 0.50)
        0.0
    """
    mid = calculate_mid(bid, ask)
    if mid == 0:
        return 0.0
    return (ask - bid) / mid


def calculate_spread_absolute(bid: float, ask: float) -> float:
    """Calculate absolute spread.

    Args:
        bid: Bid price (0-1 range).
        ask: Ask price (0-1 range).

    Returns:
        Absolute spread.

    Example:
        >>> calculate_spread_absolute(0.45, 0.50)
        0.05
    """
    return ask - bid


def cents_to_probability(cents: int) -> float:
    """Convert price from cents (0-100) to probability (0-1).

    Args:
        cents: Price in cents.

    Returns:
        Price in probability range.

    Example:
        >>> cents_to_probability(45)
        0.45
        >>> cents_to_probability(100)
        1.0
    """
    return cents * CENTS_TO_PROB


def probability_to_cents(probability: float) -> int:
    """Convert price from probability (0-1) to cents (0-100).

    Args:
        probability: Price in 0-1 range.

    Returns:
        Price in cents (rounded to nearest cent).

    Example:
        >>> probability_to_cents(0.45)
        45
        >>> probability_to_cents(0.456)
        46
    """
    return round(probability / CENTS_TO_PROB)


def calculate_quote_prices(
    mid_price: float,
    target_spread: float,
    inventory_skew: float = 0.0,
) -> tuple[float, float]:
    """Calculate bid and ask prices for quoting.

    Args:
        mid_price: Current mid price (0-1 range).
        target_spread: Desired total spread (e.g., 0.04 for 4%).
        inventory_skew: Adjustment for inventory (-1 to 1, negative skews down).

    Returns:
        Tuple of (bid_price, ask_price).

    Example:
        >>> bid, ask = calculate_quote_prices(0.50, 0.04)
        >>> round(bid, 2), round(ask, 2)
        (0.48, 0.52)

        >>> # With inventory skew (long position, skew prices down)
        >>> bid, ask = calculate_quote_prices(0.50, 0.04, inventory_skew=-0.01)
        >>> bid < 0.48 and ask < 0.52
        True
    """
    half_spread = target_spread / 2
    bid = mid_price - half_spread + inventory_skew
    ask = mid_price + half_spread + inventory_skew

    # Clamp to valid range
    bid = max(MIN_PRICE, min(MAX_PRICE, bid))
    ask = max(MIN_PRICE, min(MAX_PRICE, ask))

    # Ensure bid < ask
    if bid >= ask:
        # If clamping caused overlap, adjust
        mid = (bid + ask) / 2
        bid = mid - 0.01
        ask = mid + 0.01

    return bid, ask


def calculate_inventory_skew(
    position: int,
    max_position: int,
    skew_factor: float,
) -> float:
    """Calculate price skew based on inventory.

    When long, skew prices down to encourage selling.
    When short, skew prices up to encourage buying.

    Args:
        position: Current position (positive=long, negative=short).
        max_position: Maximum allowed position.
        skew_factor: Multiplier for skew effect.

    Returns:
        Price adjustment to apply to both bid and ask.

    Example:
        >>> # Long 25 contracts out of max 50, skew factor 0.01
        >>> calculate_inventory_skew(25, 50, 0.01)
        -0.005

        >>> # Short 25 contracts
        >>> calculate_inventory_skew(-25, 50, 0.01)
        0.005
    """
    if max_position == 0:
        return 0.0

    # Normalize position to -1 to 1 range
    normalized = position / max_position

    # Return negative skew when long (pushes prices down)
    # Return positive skew when short (pushes prices up)
    return -normalized * skew_factor


def should_quote(
    spread_pct: float,
    min_spread: float,
    position: int,
    max_position: int,
) -> tuple[bool, bool]:
    """Determine whether to quote bid and/or ask.

    Args:
        spread_pct: Current market spread percentage.
        min_spread: Minimum spread to participate.
        position: Current position.
        max_position: Maximum position.

    Returns:
        Tuple of (should_quote_bid, should_quote_ask).

    Example:
        >>> # Wide spread, no position - quote both sides
        >>> should_quote(0.05, 0.03, 0, 50)
        (True, True)

        >>> # At max long - only quote ask
        >>> should_quote(0.05, 0.03, 50, 50)
        (False, True)

        >>> # Spread too tight - don't quote
        >>> should_quote(0.02, 0.03, 0, 50)
        (False, False)
    """
    # Don't quote if spread is too tight
    if spread_pct < min_spread:
        return False, False

    # Quote bid if not at max long
    quote_bid = position < max_position

    # Quote ask if not at max short
    quote_ask = position > -max_position

    return quote_bid, quote_ask
