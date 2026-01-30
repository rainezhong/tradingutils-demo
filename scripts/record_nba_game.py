#!/usr/bin/env python3
"""
Record live NBA game data + Kalshi market prices for later replay.

Usage:
    # List currently live/upcoming games
    python scripts/record_nba_game.py --list-games

    # Record a specific game (auto-detects Kalshi tickers)
    python scripts/record_nba_game.py --game-id 0022400123

    # Record with custom output directory
    python scripts/record_nba_game.py --game-id 0022400123 --output data/recordings/

    # Record with custom tickers (if auto-detection fails)
    python scripts/record_nba_game.py --game-id 0022400123 \
        --home-ticker NBALALBOS-LALWIN \
        --away-ticker NBALALBOS-BOSWIN

    # Record for a limited duration (seconds)
    python scripts/record_nba_game.py --game-id 0022400123 --max-duration 3600

The recording will continue until:
1. The game ends (status = "final")
2. --max-duration is reached
3. You press Ctrl+C
"""

import argparse
import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.simulation.nba_recorder import NBAGameRecorder, list_live_games
from src.kalshi.client import KalshiClient


def find_kalshi_tickers_for_game(home_team: str, away_team: str) -> tuple:
    """Try to find Kalshi tickers for a game.

    This attempts to match the game to Kalshi NBA game markets.
    The ticker format is: KXNBAGAME-{YY}{MON}{DD}{AWAY}{HOME}-{TEAM}

    Example: KXNBAGAME-26JAN28CHIIND-IND for Indiana win market

    Args:
        home_team: Home team tricode (e.g., "LAL")
        away_team: Away team tricode (e.g., "BOS")

    Returns:
        Tuple of (home_ticker, away_ticker) or (None, None) if not found
    """
    from datetime import datetime

    # Get today's date in Kalshi format: YYMONDD (e.g., 26JAN28)
    now = datetime.now()
    month_abbrev = now.strftime("%b").upper()  # JAN, FEB, etc.
    date_str = f"{now.year % 100}{month_abbrev}{now.day:02d}"

    # Kalshi format: KXNBAGAME-{date}{away}{home}-{team}
    matchup = f"{away_team}{home_team}"
    prefix = f"KXNBAGAME-{date_str}{matchup}"

    home_ticker = f"{prefix}-{home_team}"
    away_ticker = f"{prefix}-{away_team}"

    return home_ticker, away_ticker


async def verify_tickers(client: KalshiClient, home_ticker: str, away_ticker: str) -> bool:
    """Verify that tickers exist on Kalshi.

    Args:
        client: KalshiClient instance
        home_ticker: Home team win ticker
        away_ticker: Away team win ticker

    Returns:
        True if both tickers exist
    """
    try:
        await client.get_market_data_async(home_ticker)
        await client.get_market_data_async(away_ticker)
        return True
    except Exception as e:
        print(f"Ticker verification failed: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="Record NBA game data + Kalshi market prices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--list-games",
        action="store_true",
        help="List currently live/upcoming NBA games",
    )

    parser.add_argument(
        "--game-id",
        type=str,
        help="NBA game ID to record (e.g., 0022400123)",
    )

    parser.add_argument(
        "--home-ticker",
        type=str,
        help="Kalshi ticker for home team win (auto-detected if not provided)",
    )

    parser.add_argument(
        "--away-ticker",
        type=str,
        help="Kalshi ticker for away team win (auto-detected if not provided)",
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        default="data/recordings",
        help="Output directory for recordings (default: data/recordings)",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=2000,
        help="Poll interval in milliseconds (default: 2000)",
    )

    parser.add_argument(
        "--max-duration",
        type=int,
        help="Maximum recording duration in seconds",
    )

    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Skip ticker verification prompt (continue even if verification fails)",
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use Kalshi demo API instead of production",
    )

    args = parser.parse_args()

    # List games mode
    if args.list_games:
        print("\nFetching NBA games...\n")
        games = list_live_games()

        if not games:
            print("No games found.")
            return

        print(f"{'Game ID':<15} {'Matchup':<20} {'Score':<15} {'Status':<10} {'Clock'}")
        print("-" * 75)

        for game in games:
            score = f"{game['away_score']} - {game['home_score']}"
            print(f"{game['game_id']:<15} {game['matchup']:<20} {score:<15} {game['status']:<10} {game['clock']}")

        print("\nTo record a game, use: python scripts/record_nba_game.py --game-id <GAME_ID>")
        return

    # Recording mode
    if not args.game_id:
        parser.error("--game-id is required for recording (or use --list-games)")

    # Find the game info
    games = list_live_games()
    game_info = None
    for game in games:
        if game["game_id"] == args.game_id:
            game_info = game
            break

    if not game_info:
        # Try to parse game ID for team info
        print(f"Warning: Game {args.game_id} not found in live games")
        print("This could mean the game hasn't started yet or has already ended.")

        if not (args.home_ticker and args.away_ticker):
            print("\nPlease provide --home-ticker and --away-ticker manually.")
            return

        # Use placeholder team names
        home_team = args.home_ticker.split("-")[-1][:3] if args.home_ticker else "HOM"
        away_team = args.away_ticker.split("-")[-1][:3] if args.away_ticker else "AWY"
        game_info = {
            "game_id": args.game_id,
            "home_team": home_team,
            "away_team": away_team,
        }

    home_team = game_info["home_team"]
    away_team = game_info["away_team"]

    # Determine tickers
    if args.home_ticker and args.away_ticker:
        home_ticker = args.home_ticker
        away_ticker = args.away_ticker
    else:
        home_ticker, away_ticker = find_kalshi_tickers_for_game(home_team, away_team)
        if not home_ticker:
            print(f"\nCould not auto-detect Kalshi tickers for {away_team} @ {home_team}")
            print("Please provide --home-ticker and --away-ticker manually.")
            print("\nTry searching Kalshi for tickers like:")
            print(f"  KXNBA{away_team}{home_team}-{home_team}")
            print(f"  KXNBA{away_team}{home_team}-{away_team}")
            return

    print(f"\n{'='*60}")
    print(f"NBA Game Recorder")
    print(f"{'='*60}")
    print(f"Game ID:      {args.game_id}")
    print(f"Matchup:      {away_team} @ {home_team}")
    print(f"Home Ticker:  {home_ticker}")
    print(f"Away Ticker:  {away_ticker}")
    print(f"Poll Interval: {args.poll_interval}ms")
    print(f"Output Dir:   {args.output}")
    if args.max_duration:
        print(f"Max Duration: {args.max_duration}s")
    print(f"{'='*60}\n")

    # Create recorder
    recorder = NBAGameRecorder(
        game_id=args.game_id,
        home_team=home_team,
        away_team=away_team,
        home_ticker=home_ticker,
        away_ticker=away_ticker,
    )

    # Handle Ctrl+C gracefully
    stop_requested = False

    def signal_handler(sig, frame):
        nonlocal stop_requested
        if stop_requested:
            print("\nForce quitting...")
            sys.exit(1)
        print("\nStopping recording (press Ctrl+C again to force quit)...")
        stop_requested = True
        recorder.stop()

    signal.signal(signal.SIGINT, signal_handler)

    # Connect to Kalshi
    print("Connecting to Kalshi API...")
    client = KalshiClient.from_env(demo=args.demo)

    async with client:
        # Verify tickers exist
        print("Verifying tickers...")
        if not await verify_tickers(client, home_ticker, away_ticker):
            print("\nWarning: Could not verify tickers on Kalshi.")
            print("The tickers may be incorrect or the market may be closed.")
            if args.force:
                print("Continuing anyway (--force flag set)...")
            else:
                response = input("Continue anyway? [y/N] ")
                if response.lower() != "y":
                    return

        print("Starting recording...")
        print("Press Ctrl+C to stop recording.\n")

        try:
            await recorder.start_async(
                kalshi_client=client,
                poll_interval_ms=args.poll_interval,
                max_duration_seconds=args.max_duration,
            )
        except Exception as e:
            print(f"\nRecording error: {e}")

    # Save recording
    if recorder.frames:
        # Generate filename
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{away_team}_vs_{home_team}_{date_str}.json"
        filepath = Path(args.output) / filename

        recorder.save(str(filepath))
        print(f"\nRecording saved to: {filepath}")
        print(f"Total frames: {len(recorder.frames)}")

        if recorder.metadata.final_status:
            print(f"Final score: {away_team} {recorder.metadata.final_away_score} - "
                  f"{recorder.metadata.final_home_score} {home_team}")
    else:
        print("\nNo frames captured - recording not saved.")


if __name__ == "__main__":
    asyncio.run(main())
