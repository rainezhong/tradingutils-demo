#!/usr/bin/env python3
"""Test to understand ticker format and extract game start time."""

import re
from datetime import datetime, timezone, timedelta

# Sample tickers from live data
sample_tickers = [
    "KXNBAGAME-26FEB26CHAIND-IND",
    "KXNBAGAME-26FEB26MIAPHI-PHI",
    "KXNBAGAME-26FEB27BKNBOS-BKN",
    "KXNBAGAME-26FEB28NOPUTA-NOP",
]

def parse_game_date_from_ticker(ticker: str) -> datetime:
    """Extract game date from ticker format: KXNBAGAME-26FEB26TEAMS-TEAM.

    Format: KXNBAGAME-YYMMMDDTEAMS-TEAM
    Example: KXNBAGAME-26FEB26CHAIND-IND
             Year 2026, Feb 26
    """
    # Pattern: 26FEB26 = year(26) month(FEB) day(26)
    match = re.search(r'-(\d{2})([A-Z]{3})(\d{2})', ticker)
    if not match:
        raise ValueError(f"Cannot parse date from ticker: {ticker}")

    year_suffix = match.group(1)
    month_str = match.group(2)
    day = match.group(3)

    # Convert month string to number
    months = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
              'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
    month = months.get(month_str)
    if not month:
        raise ValueError(f"Invalid month: {month_str}")

    # Convert year (26 -> 2026)
    year = 2000 + int(year_suffix)

    # Parse day
    day_int = int(day)

    # Create datetime - assume games typically start in evening EST (00:00-03:00 UTC)
    # We'll use midnight UTC as the game date, since exact time isn't in ticker
    game_date = datetime(year, month, day_int, tzinfo=timezone.utc)

    return game_date


print("Testing ticker parsing:\n")
for ticker in sample_tickers:
    try:
        game_date = parse_game_date_from_ticker(ticker)
        now = datetime.now(timezone.utc)
        hours_until = (game_date - now).total_seconds() / 3600
        print(f"{ticker}")
        print(f"  Game date: {game_date}")
        print(f"  Hours until: {hours_until:.1f}h")
        print(f"  In 2-5h window: {2 <= hours_until <= 5}")
        print()
    except Exception as e:
        print(f"{ticker}: ERROR - {e}\n")
