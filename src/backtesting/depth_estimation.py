"""Depth estimation and data completeness helpers for backtest framework.

Provides utilities for handling partial/missing orderbook data:
- Depth estimation from spread when real depth is unavailable
- Conservative defaults for missing data
- Fallback logic with estimation tracking
"""

import logging
from typing import Tuple, Optional

from src.core.models import MarketState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conservative defaults for missing orderbook data
# ---------------------------------------------------------------------------

DEFAULT_LOW_DEPTH = 5  # Conservative minimum depth when data is missing
DEFAULT_WIDE_SPREAD_CENTS = 10  # Conservative wide spread when data is missing
DEFAULT_BASE_DEPTH = 50  # Base depth for estimation formula


def estimate_depth_from_spread(
    spread_cents: Optional[int],
    base_depth: int = DEFAULT_BASE_DEPTH,
) -> int:
    """Estimate orderbook depth from spread when depth data is missing.

    Heuristic: Tight spread suggests better liquidity (more depth).
    Formula: estimated_depth = base_depth * (100 / max(spread_cents, 1))

    This is a conservative estimate - real depth may be higher or lower.
    Used only when actual depth data is unavailable.

    Args:
        spread_cents: Bid-ask spread in cents, or None if also missing
        base_depth: Base depth for 100-cent (100%) spread (default: 50)

    Returns:
        Estimated depth in contracts (minimum DEFAULT_LOW_DEPTH)

    Examples:
        >>> estimate_depth_from_spread(1)  # 1-cent spread
        5000  # Very tight spread → high estimated depth

        >>> estimate_depth_from_spread(10)  # 10-cent spread
        500  # Moderate spread → moderate estimated depth

        >>> estimate_depth_from_spread(None)  # Missing spread
        5  # Conservative fallback
    """
    if spread_cents is None or spread_cents <= 0:
        logger.warning(
            "Missing or invalid spread data, using conservative depth fallback: %d",
            DEFAULT_LOW_DEPTH,
        )
        return DEFAULT_LOW_DEPTH

    # Inverse relationship: tighter spread → better depth
    # Normalize by 100-cent (100%) spread as baseline
    estimated = base_depth * (100 / max(spread_cents, 1))

    # Apply minimum threshold
    return max(int(estimated), DEFAULT_LOW_DEPTH)


def get_orderbook_depth_with_fallback(
    market: MarketState,
    side: str,
) -> Tuple[int, bool]:
    """Get orderbook depth with fallback estimation if missing.

    Args:
        market: Market state (may have None depth values)
        side: "BID" (need ask depth) or "ASK" (need bid depth)

    Returns:
        Tuple of (depth, is_estimated)
        - depth: Actual depth or estimated fallback
        - is_estimated: True if depth was estimated, False if real data

    Examples:
        >>> market = MarketState(ticker="TEST", timestamp=..., bid=0.45, ask=0.46,
        ...                      bid_depth=100, ask_depth=50)
        >>> get_orderbook_depth_with_fallback(market, "BID")  # Need ask depth
        (50, False)  # Real data available

        >>> market = MarketState(ticker="TEST", timestamp=..., bid=0.45, ask=0.46,
        ...                      bid_depth=None, ask_depth=None)
        >>> get_orderbook_depth_with_fallback(market, "BID")  # Need ask depth
        (5000, True)  # Estimated from spread (1 cent)
    """
    # Determine which side we need
    if side == "BID":
        # Buying: need ask side depth
        actual_depth = market.ask_depth
    else:
        # Selling: need bid side depth
        actual_depth = market.bid_depth

    # Return real data if available
    if actual_depth is not None and actual_depth > 0:
        return actual_depth, False

    # Estimate from spread if available
    spread_cents = int(market.spread * 100) if market.spread is not None else None
    estimated_depth = estimate_depth_from_spread(spread_cents)

    logger.debug(
        "Estimating depth for %s %s: spread=%s cents, estimated_depth=%d",
        market.ticker,
        side,
        spread_cents,
        estimated_depth,
    )

    return estimated_depth, True
