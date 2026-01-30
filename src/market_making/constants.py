"""Constants for market-making operations.

All prices in this module use the 0-1 probability range (not cents).
"""

# Price boundaries (probability range)
MIN_PRICE: float = 0.01  # 1 cent
MAX_PRICE: float = 0.99  # 99 cents

# Quote size limits
MIN_QUOTE_SIZE: int = 5
MAX_QUOTE_SIZE: int = 100
DEFAULT_QUOTE_SIZE: int = 20

# Position limits
DEFAULT_MAX_POSITION: int = 50
DEFAULT_MAX_TOTAL_POSITION: int = 100

# Spread thresholds
MIN_VIABLE_SPREAD: float = 0.02  # 2% minimum to consider quoting
DEFAULT_TARGET_SPREAD: float = 0.04  # 4% target spread

# Risk thresholds
DEFAULT_MAX_LOSS_PER_POSITION: float = 20.0  # dollars
DEFAULT_MAX_DAILY_LOSS: float = 50.0  # dollars

# Order sides
SIDE_BID: str = "BID"
SIDE_ASK: str = "ASK"
VALID_SIDES: frozenset[str] = frozenset({SIDE_BID, SIDE_ASK})

# Conversion factor from cents to probability
CENTS_TO_PROB: float = 0.01  # 1 cent = 0.01 probability
