"""Stub OMS models for legacy imports.

Provides the Action and Outcome enums that strategies/base.py imports.
"""

from enum import Enum


class Outcome(Enum):
    """Contract outcome in prediction markets."""

    YES = "yes"
    NO = "no"


class Action(Enum):
    """Trading action."""

    BUY = "buy"
    SELL = "sell"
