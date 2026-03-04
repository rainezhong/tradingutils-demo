#!/usr/bin/env python3
"""Investigate Kalshi NBA market structure."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone
import json

sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


async def main():
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        exchange = KalshiExchangeClient.from_env(demo=True)

    await exchange.connect()

    try:
        print(f"\n{'='*120}")
        print(f"KALSHI NBA MARKET INVESTIGATION")
        print(f"{'='*120}\n")

        # Try both series
        for series in ["KXNBAGAME", "KXNBA"]:
            print(f"\n{'='*120}")
            print(f"SERIES: {series}")
            print(f"{'='*120}\n")

            markets = await exchange.get_markets(series_ticker=series, status="open", limit=10)

            print(f"Found {len(markets)} open markets\n")

            for i, market in enumerate(markets[:3]):
                # Try to get full details via API
                try:
                    response = await exchange._client.get(
                        f"/trade-api/v2/markets",
                        params={"ticker": market.ticker, "limit": 1}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        full_market = data.get('markets', [{}])[0] if 'markets' in data else {}
                    else:
                        full_market = {}
                except Exception as e:
                    print(f"API error: {e}")
                    full_market = {}

                print(f"{'─'*120}")
                print(f"Market {i+1}: {market.ticker}")
                print(f"{'─'*120}")
                print(f"Title: {full_market.get('title', 'N/A')}")
                print(f"Subtitle: {full_market.get('subtitle', 'N/A')}")
                print(f"Series: {full_market.get('series_ticker', series)}")
                print(f"Event: {full_market.get('event_ticker', 'N/A')}")

                print(f"\nTIMING:")
                print(f"  Open:       {full_market.get('open_time', 'N/A')}")
                print(f"  Close:      {full_market.get('close_time', market.close_time)}")
                print(f"  Expiration: {full_market.get('expiration_time', 'N/A')}")

                print(f"\nQUESTION/RULES:")
                rules = full_market.get('rules', 'N/A')
                if rules != 'N/A' and len(rules) > 300:
                    rules = rules[:300] + "..."
                print(f"  {rules}")

                print(f"\nPRICING:")
                print(f"  Yes: {market.yes_bid or 0}¢ / {market.yes_ask or 0}¢")
                print(f"  No:  {market.no_bid or 0}¢ / {market.no_ask or 0}¢")
                print(f"  Volume: {full_market.get('volume', market.volume or 0)}")
                print(f"  Open Interest: {full_market.get('open_interest', market.open_interest or 0)}")

                print(f"\nSETTLEMENT:")
                print(f"  Can close early: {full_market.get('can_close_early', 'N/A')}")
                print(f"  Result source: {full_market.get('result_source', 'N/A')}")
                print(f"  Settled: {full_market.get('result', 'N/A')}")

                print()

        # Compare game times
        print(f"\n{'='*120}")
        print("GAME TIME vs MARKET CLOSE TIME ANALYSIS")
        print(f"{'='*120}\n")

        # Get ESPN schedule
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
            espn_data = resp.json()

        games = espn_data.get('events', [])[:5]

        for game in games:
            game_name = game.get('name', '')
            game_time = game.get('date', '')
            game_datetime = datetime.fromisoformat(game_time.replace('Z', '+00:00'))

            # Try to find matching Kalshi market
            away_team = game.get('competitions', [{}])[0].get('competitors', [{}])[0].get('team', {}).get('abbreviation', '')
            home_team = game.get('competitions', [{}])[0].get('competitors', [{}])[1].get('team', {}).get('abbreviation', '')

            print(f"\nGame: {game_name}")
            print(f"  Game starts: {game_time}")

            # Search for matching market
            all_markets = await exchange.get_markets(series_ticker="KXNBAGAME", status="open", limit=100)

            matching = [m for m in all_markets if away_team in m.ticker.upper() and home_team in m.ticker.upper()]

            if matching:
                m = matching[0]
                print(f"  Kalshi market: {m.ticker}")
                print(f"  Market closes: {m.close_time}")
                print(f"  Gap: {(m.close_time - game_datetime).total_seconds() / 86400:.1f} days")
            else:
                print(f"  No matching Kalshi market found")

    finally:
        await exchange.exit()


if __name__ == "__main__":
    asyncio.run(main())
