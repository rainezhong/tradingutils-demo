#!/usr/bin/env python3
"""Check NBA markets with CORRECTED game start time parsing."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import re

sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


def parse_game_start_from_ticker(ticker: str):
    """Parse game start time from ticker (with +1 day offset for evening games)."""
    match = re.search(r'-(\d{2})([A-Z]{3})(\d{2})', ticker)
    if not match:
        return None

    year_suffix = match.group(1)
    month_str = match.group(2)
    day = match.group(3)

    months = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
              'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
    month = months.get(month_str)
    if not month:
        return None

    try:
        year = 2000 + int(year_suffix)
        day_int = int(day)

        # Ticker date is local US, games are evening (7-10:30 PM EST)
        # Add +1 day to get midnight UTC (when most games start)
        game_date_local = datetime(year, month, day_int, 0, 0, 0, tzinfo=timezone.utc)
        game_start = game_date_local + timedelta(days=1)
        return game_start
    except (ValueError, OverflowError):
        return None


async def main():
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        exchange = KalshiExchangeClient.from_env(demo=True)

    await exchange.connect()

    try:
        markets = await exchange.get_markets(series_ticker="KXNBAGAME", status="open")
        now = datetime.now(timezone.utc)
        now_pst = now - timedelta(hours=8)

        print(f"\n{'='*120}")
        print(f"NBA MARKET OPPORTUNITIES (CORRECTED TIMING)")
        print(f"Current time: {now_pst.strftime('%I:%M %p PST')} ({now.strftime('%I:%M %p UTC')})")
        print(f"{'='*120}\n")
        print(f"{'Ticker':<45} {'Price':<8} {'Side':<8} {'Game Start':<16} {'Hrs Until':<10} {'Status':<20}")
        print(f"{'-'*120}")

        optimal_markets = []
        close_markets = []
        far_markets = []

        for market in markets:
            ticker = market.ticker
            yes_ask = market.yes_ask or 100
            no_ask = market.no_ask or 100

            # Determine underdog
            if yes_ask < no_ask:
                underdog_side = "YES"
                underdog_price = yes_ask
            else:
                underdog_side = "NO"
                underdog_price = no_ask

            # Parse game start time
            game_start = parse_game_start_from_ticker(ticker)
            if not game_start:
                continue

            hours_until = (game_start - now).total_seconds() / 3600

            # Check if in optimal range
            in_price_range = 5 <= underdog_price <= 15
            in_time_range = 2 <= hours_until <= 5
            is_optimal = in_price_range and in_time_range

            if is_optimal:
                optimal_markets.append((ticker, underdog_side, underdog_price, hours_until))
                status = "✓ OPTIMAL (2-5h, 5-15¢)"
            elif in_price_range and hours_until > 5:
                far_markets.append((ticker, underdog_side, underdog_price, hours_until))
                status = "⏰ Too early (good price)"
            elif in_price_range and hours_until < 2:
                close_markets.append((ticker, underdog_side, underdog_price, hours_until))
                status = "⏰ Too late (good price)"
            elif in_time_range and underdog_price < 5:
                status = "💰 Too cheap (<5¢)"
            elif in_time_range and underdog_price > 15:
                status = "💰 Too expensive (>15¢)"
            elif hours_until < 0:
                status = "🏁 Game started/finished"
            else:
                status = "⊘ Outside range"

            print(
                f"{ticker:<45} {underdog_price:>4}¢    {underdog_side:<8} "
                f"{game_start.strftime('%m/%d %H:%M'):<16} {hours_until:>6.1f}h     {status:<20}"
            )

        print(f"{'-'*120}")
        print(f"\n{'='*120}")
        print("SUMMARY")
        print(f"{'='*120}")
        print(f"\n✓ OPTIMAL MARKETS (5-15¢, 2-5h window): {len(optimal_markets)}")

        if optimal_markets:
            print("\n  Ready to bet NOW:")
            for ticker, side, price, hours in optimal_markets:
                print(f"    • {ticker} {side} @ {price}¢ ({hours:.1f}h until game)")
        else:
            print("    None right now - waiting for games to enter window")

        if close_markets:
            print(f"\n⏰ GOOD PRICE, TOO CLOSE (<2h): {len(close_markets)}")
            for ticker, side, price, hours in close_markets[:3]:
                print(f"    • {ticker} {side} @ {price}¢ ({hours:.1f}h until game)")

        if far_markets:
            print(f"\n⏰ GOOD PRICE, TOO EARLY (>5h): {len(far_markets)}")
            for ticker, side, price, hours in far_markets[:3]:
                game_start = parse_game_start_from_ticker(ticker)
                optimal_time = game_start - timedelta(hours=3.5)  # Middle of 2-5h window
                optimal_pst = optimal_time - timedelta(hours=8)
                print(f"    • {ticker} {side} @ {price}¢ - enter around {optimal_pst.strftime('%I:%M %p PST')}")

        print(f"\n{'='*120}\n")

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
