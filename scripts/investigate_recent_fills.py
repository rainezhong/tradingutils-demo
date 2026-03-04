#!/usr/bin/env python3
"""Quick script to check all recent fills to understand the time window issue."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import re

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.exchange_client.kalshi import KalshiExchangeClient


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO8601 timestamp, handling microseconds with any number of digits."""
    # Extract and normalize microseconds to exactly 6 digits
    match = re.match(r'(.+\.)(\d+)(Z|[+-]\d{2}:\d{2})$', ts_str)
    if match:
        base, micros, tz = match.groups()
        # Pad or truncate to 6 digits
        micros = micros.ljust(6, '0')[:6]
        ts_str = f"{base}{micros}{tz}"
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


async def main():
    print("Querying recent fills from Kalshi...")

    client = KalshiExchangeClient.from_env()
    await client.connect()

    try:
        # Get recent fills
        fills = await client.get_fills(limit=100)

        print(f"\n✓ Retrieved {len(fills)} recent fill(s)\n")

        if not fills:
            print("No fills found in account")
            return

        # Show first 20 fills
        print("Most Recent Fills:")
        print("-" * 120)
        print(f"{'#':<4} {'Timestamp':<25} {'Ticker':<35} {'Action':<6} {'Side':<4} {'Qty':<4} {'Price':<6}")
        print("-" * 120)

        for i, fill in enumerate(fills[:20], 1):
            created_time = fill.get("created_time", "")
            if isinstance(created_time, str):
                # ISO8601 timestamp
                timestamp = parse_timestamp(created_time).strftime("%Y-%m-%d %H:%M:%S UTC")
            else:
                # Unix timestamp
                timestamp = datetime.fromtimestamp(created_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            ticker = fill.get("ticker", "unknown")[:34]
            action = fill.get("action", "?").upper()
            side = fill.get("side", "?").upper()
            count = fill.get("count", 0)
            yes_price = fill.get("yes_price", 0)
            no_price = fill.get("no_price", 0)
            price = yes_price if side.lower() == "yes" else no_price

            print(f"{i:<4} {timestamp:<25} {ticker:<35} {action:<6} {side:<4} {count:<4} {price}¢")

        # Save all fills to file
        fills_file = project_root / "recent_fills.json"
        with open(fills_file, 'w') as f:
            json.dump(fills, f, indent=2)

        print(f"\n✓ All fills saved to: {fills_file}")

        # Check for March 1 fills
        print("\nChecking for March 1, 2026 fills...")
        march1_fills = []
        target_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for fill in fills:
            created_time = fill.get("created_time", "")
            if isinstance(created_time, str):
                # ISO8601 timestamp
                fill_date = parse_timestamp(created_time)
            else:
                # Unix timestamp
                fill_date = datetime.fromtimestamp(created_time, tz=timezone.utc)
            if fill_date.date() == target_date.date():
                march1_fills.append(fill)

        if march1_fills:
            print(f"✓ Found {len(march1_fills)} fill(s) from March 1, 2026")
            for fill in march1_fills:
                created_time = fill.get("created_time", "")
                if isinstance(created_time, str):
                    timestamp = parse_timestamp(created_time).strftime("%Y-%m-%d %H:%M:%S UTC")
                else:
                    timestamp = datetime.fromtimestamp(created_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                ticker = fill.get("ticker", "unknown")
                action = fill.get("action", "?").upper()
                print(f"  - {timestamp} | {action} {ticker}")
        else:
            print("❌ No fills found from March 1, 2026")
            print("   Either:")
            print("   1. Fills are older than 100 most recent")
            print("   2. Session date is wrong")
            print("   3. No trades actually filled on March 1")

    finally:
        await client.exit()


if __name__ == "__main__":
    asyncio.run(main())
