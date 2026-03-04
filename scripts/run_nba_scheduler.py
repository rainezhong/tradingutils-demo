#!/usr/bin/env python3
"""
Run NBA game auto-scheduler to detect and record games.

Usage:
    # Run as daemon (polls every 5 minutes)
    python scripts/run_nba_scheduler.py

    # Single poll for testing
    python scripts/run_nba_scheduler.py --once

    # Custom poll interval (seconds)
    python scripts/run_nba_scheduler.py --poll-interval 300

    # Verbose output
    python scripts/run_nba_scheduler.py -v

    # Use Kalshi demo API
    python scripts/run_nba_scheduler.py --demo
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.automation.nba_scheduler import NBAGameScheduler


def main():
    parser = argparse.ArgumentParser(
        description="NBA game auto-scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit (for testing)",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=300,
        help="Poll interval in seconds (default: 300 = 5 minutes)",
    )

    parser.add_argument(
        "--state-file",
        type=str,
        default=None,
        help="Path to state file (default: data/nba_scheduler_state.json)",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for recordings (default: data/recordings)",
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use Kalshi demo API instead of production",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    # Create scheduler
    scheduler = NBAGameScheduler(
        poll_interval=args.poll_interval,
        state_file=args.state_file,
        recordings_dir=args.output_dir,
        demo=args.demo,
        verbose=args.verbose,
    )

    if args.once:
        # Single poll mode
        print("Running single poll...")
        started = scheduler.poll_once()
        print(f"Started {started} new recorder(s)")

        status = scheduler.get_status()
        print(
            f"Active: {status['active_count']}, Completed today: {status['completed_count']}"
        )

        for game in status["active_games"]:
            print(f"  Recording: {game['matchup']} ({game['frames']} frames)")
    else:
        # Daemon mode
        print(f"Starting NBA scheduler (poll interval: {args.poll_interval}s)")
        print("Press Ctrl+C to stop...")
        print()

        try:
            scheduler.run_forever()
        except KeyboardInterrupt:
            print("\nShutdown requested...")
            scheduler.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
