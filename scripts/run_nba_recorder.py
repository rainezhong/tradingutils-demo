"""Record all Kalshi markets for live NBA games.

Usage:
    python Scripts/run_nba_recorder.py
    python Scripts/run_nba_recorder.py --teams LAL BOS --date 26FEB10
    python Scripts/run_nba_recorder.py --all-live
"""

import asyncio
import argparse
import logging
import sys
import os
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.exchange_client import KalshiExchangeClient
from core.nba_utils import get_todays_games, get_live_games
from core.recorder import NBAGameRecorder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_nba_recorder")


def today_kalshi_date() -> str:
    """Get today's date in Kalshi format: YYMMMDD (e.g., '26FEB10')."""
    now = datetime.now()
    return now.strftime("%y%b%d").upper()


async def record_game(
    client: KalshiExchangeClient,
    home_team: str,
    away_team: str,
    date: str,
    poll_interval_ms: int,
    orderbook_depth: int,
    output_dir: str,
    max_duration: Optional[int],
) -> None:
    """Record a single NBA game."""
    recorder = NBAGameRecorder(
        home_team=home_team,
        away_team=away_team,
        date=date,
        poll_interval_ms=poll_interval_ms,
        orderbook_depth=orderbook_depth,
    )

    logger.info(f"Recording: {away_team} @ {home_team}")
    recording = await recorder.start_async(client, max_duration_seconds=max_duration)

    game_date = datetime.now().strftime("%Y%m%d")
    filename = f"{away_team}_at_{home_team}_{game_date}.json"
    filepath = os.path.join(output_dir, filename)

    recording.save(filepath)
    logger.info(f"Saved: {filepath} ({len(recording)} snapshots)")


async def record_all_live(
    client: KalshiExchangeClient,
    date: str,
    poll_interval_ms: int,
    orderbook_depth: int,
    output_dir: str,
    max_duration: Optional[int],
) -> None:
    """Discover and record all currently live NBA games in parallel."""
    live = get_live_games()

    if not live:
        logger.info("No live NBA games right now. Checking today's schedule...")
        today = get_todays_games()
        if today:
            logger.info(f"Today's games ({len(today)}):")
            for g in today:
                logger.info(
                    f"  {g['away_team']} @ {g['home_team']} — {g['status']} "
                    f"({g['away_score']}-{g['home_score']})"
                )
        else:
            logger.info("No games scheduled today.")
        return

    logger.info(f"Found {len(live)} live game(s):")
    for g in live:
        logger.info(f"  {g['away_team']} @ {g['home_team']} ({g['time_remaining']})")

    tasks = []
    for game in live:
        tasks.append(
            record_game(
                client=client,
                home_team=game["home_team"],
                away_team=game["away_team"],
                date=date,
                poll_interval_ms=poll_interval_ms,
                orderbook_depth=orderbook_depth,
                output_dir=output_dir,
                max_duration=max_duration,
            )
        )

    await asyncio.gather(*tasks)


async def main():
    parser = argparse.ArgumentParser(description="Record NBA game markets from Kalshi")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--teams",
        nargs=2,
        metavar=("HOME", "AWAY"),
        help="Record a specific game (e.g., --teams LAL BOS)",
    )
    group.add_argument(
        "--all-live",
        action="store_true",
        help="Auto-detect and record all currently live games",
    )

    parser.add_argument(
        "--date", default=None, help="Kalshi date (e.g., 26FEB10). Default: today"
    )
    parser.add_argument(
        "--poll", type=int, default=500, help="Poll interval in ms (default: 500)"
    )
    parser.add_argument(
        "--depth", type=int, default=10, help="Orderbook depth (default: 10)"
    )
    parser.add_argument("--output", default="data/recordings", help="Output directory")
    parser.add_argument(
        "--max-duration",
        type=int,
        default=None,
        help="Max recording duration in seconds",
    )

    args = parser.parse_args()
    date = args.date or today_kalshi_date()

    logger.info(f"Kalshi date: {date}")

    # Connect to Kalshi
    client = KalshiExchangeClient.from_env()
    await client.connect()
    logger.info("Connected to Kalshi")

    try:
        if args.teams:
            home, away = args.teams
            await record_game(
                client=client,
                home_team=home,
                away_team=away,
                date=date,
                poll_interval_ms=args.poll,
                orderbook_depth=args.depth,
                output_dir=args.output,
                max_duration=args.max_duration,
            )
        else:
            await record_all_live(
                client=client,
                date=date,
                poll_interval_ms=args.poll,
                orderbook_depth=args.depth,
                output_dir=args.output,
                max_duration=args.max_duration,
            )
    finally:
        await client.exit()
        logger.info("Disconnected from Kalshi")


if __name__ == "__main__":
    asyncio.run(main())
