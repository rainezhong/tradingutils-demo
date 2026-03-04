#!/usr/bin/env python3
"""CLI entry point for the latency probe framework.

Usage:
    # Crypto (BTC)
    python3 scripts/latency_probe/run.py crypto --duration 3600 --db data/probe_btc.db
    python3 scripts/latency_probe/run.py crypto --series KXETH15M --duration 3600

    # NBA game winner
    python3 scripts/latency_probe/run.py nba --duration 7200 --db data/probe_nba.db

    # NBA total points (over/under)
    python3 scripts/latency_probe/run.py nba-points --series KXNBAOU --db data/probe_nba_points.db

    # NCAAB game winner
    python3 scripts/latency_probe/run.py ncaab --duration 7200 --db data/probe_ncaab.db

    # NCAAB total points
    python3 scripts/latency_probe/run.py ncaab-points --series KXNCAABOU --db data/probe_ncaab_points.db

    # Analyze
    python3 scripts/latency_probe/run.py analyze --db data/probe_btc.db
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.latency_probe import ProbeRecorder, ProbeAnalyzer, LatencyProbe, ProbeConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def cmd_crypto(args: argparse.Namespace) -> None:
    """Run the crypto (BTC) latency probe."""
    from scripts.latency_probe.crypto import CryptoTruthSource

    db_path = Path(args.db)
    recorder = ProbeRecorder(db_path)

    truth = CryptoTruthSource(recorder=recorder, volatility=args.vol)

    config = ProbeConfig(
        series_ticker=args.series,
        poll_interval_sec=args.poll_interval,
    )

    probe = LatencyProbe(
        truth_source=truth,
        recorder=recorder,
        config=config,
    )

    try:
        asyncio.run(probe.run(duration_sec=args.duration))
    finally:
        recorder.close()


def cmd_basketball(args: argparse.Namespace, league: str) -> None:
    """Run the basketball (NBA/NCAAB) latency probe."""
    from scripts.latency_probe.basketball import BasketballTruthSource

    db_path = Path(args.db)
    recorder = ProbeRecorder(db_path)

    market_type = getattr(args, "market_type", "game_winner")

    truth = BasketballTruthSource(
        league=league,
        recorder=recorder,
        poll_interval=args.espn_poll_interval,
        market_type=market_type,
    )

    series_ticker = args.series or ("KXNBAGAME" if league == "nba" else "KXNCAAMBGAME")

    config = ProbeConfig(
        series_ticker=series_ticker,
        poll_interval_sec=args.poll_interval,
        multi_market=True,  # Track all live games
    )

    probe = LatencyProbe(
        truth_source=truth,
        recorder=recorder,
        config=config,
    )

    try:
        asyncio.run(probe.run(duration_sec=args.duration))
    finally:
        recorder.close()


def cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze an existing probe database."""
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    analyzer = ProbeAnalyzer(db_path)
    try:
        analyzer.summary()
    finally:
        analyzer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Latency Probe Framework")
    sub = parser.add_subparsers(dest="command", required=True)

    # crypto subcommand
    p_crypto = sub.add_parser("crypto", help="Run crypto (BTC) latency probe")
    p_crypto.add_argument("--duration", type=int, default=3600,
                          help="Duration in seconds (default: 3600)")
    p_crypto.add_argument("--db", default="data/probe_btc.db",
                          help="SQLite database path")
    p_crypto.add_argument("--series", default="KXBTC15M",
                          help="Kalshi series ticker (default: KXBTC15M)")
    p_crypto.add_argument("--poll-interval", type=float, default=0.5,
                          help="Kalshi poll interval in seconds (default: 0.5)")
    p_crypto.add_argument("--vol", type=float, default=0.65,
                          help="Annualized volatility for Black-Scholes (default: 0.65)")

    # nba subcommand
    p_nba = sub.add_parser("nba", help="Run NBA latency probe")
    p_nba.add_argument("--duration", type=int, default=7200,
                       help="Duration in seconds (default: 7200 = 2 hours)")
    p_nba.add_argument("--db", default="data/probe_nba.db",
                       help="SQLite database path")
    p_nba.add_argument("--series", default=None,
                       help="Kalshi series ticker (default: KXNBAGAME)")
    p_nba.add_argument("--poll-interval", type=float, default=0.5,
                       help="Kalshi poll interval in seconds (default: 0.5)")
    p_nba.add_argument("--espn-poll-interval", type=float, default=5.0,
                       help="ESPN API poll interval in seconds (default: 5.0)")

    # ncaab subcommand
    p_ncaab = sub.add_parser("ncaab", help="Run NCAAB latency probe")
    p_ncaab.add_argument("--duration", type=int, default=7200,
                         help="Duration in seconds (default: 7200 = 2 hours)")
    p_ncaab.add_argument("--db", default="data/probe_ncaab.db",
                         help="SQLite database path")
    p_ncaab.add_argument("--series", default=None,
                         help="Kalshi series ticker (default: KXNCAAMBGAME)")
    p_ncaab.add_argument("--poll-interval", type=float, default=0.5,
                         help="Kalshi poll interval in seconds (default: 0.5)")
    p_ncaab.add_argument("--espn-poll-interval", type=float, default=5.0,
                         help="ESPN API poll interval in seconds (default: 5.0)")

    # nba-points subcommand (total points over/under)
    p_nba_pts = sub.add_parser("nba-points",
                               help="Run NBA total points (over/under) latency probe")
    p_nba_pts.add_argument("--duration", type=int, default=7200,
                           help="Duration in seconds (default: 7200)")
    p_nba_pts.add_argument("--db", default="data/probe_nba_points.db",
                           help="SQLite database path")
    p_nba_pts.add_argument("--series", default=None,
                           help="Kalshi series ticker for NBA total points")
    p_nba_pts.add_argument("--poll-interval", type=float, default=0.5,
                           help="Kalshi poll interval in seconds (default: 0.5)")
    p_nba_pts.add_argument("--espn-poll-interval", type=float, default=5.0,
                           help="ESPN API poll interval in seconds (default: 5.0)")

    # ncaab-points subcommand (total points over/under)
    p_ncaab_pts = sub.add_parser("ncaab-points",
                                 help="Run NCAAB total points (over/under) latency probe")
    p_ncaab_pts.add_argument("--duration", type=int, default=7200,
                             help="Duration in seconds (default: 7200)")
    p_ncaab_pts.add_argument("--db", default="data/probe_ncaab_points.db",
                             help="SQLite database path")
    p_ncaab_pts.add_argument("--series", default=None,
                             help="Kalshi series ticker for NCAAB total points")
    p_ncaab_pts.add_argument("--poll-interval", type=float, default=0.5,
                             help="Kalshi poll interval in seconds (default: 0.5)")
    p_ncaab_pts.add_argument("--espn-poll-interval", type=float, default=5.0,
                             help="ESPN API poll interval in seconds (default: 5.0)")

    # analyze subcommand
    p_analyze = sub.add_parser("analyze", help="Analyze probe database")
    p_analyze.add_argument("--db", default="data/probe_btc.db",
                           help="SQLite database path to analyze")

    args = parser.parse_args()

    if args.command == "crypto":
        cmd_crypto(args)
    elif args.command == "nba":
        cmd_basketball(args, league="nba")
    elif args.command == "ncaab":
        cmd_basketball(args, league="ncaab")
    elif args.command == "nba-points":
        args.market_type = "total_points"
        if not args.series:
            print("ERROR: --series is required for nba-points (e.g. --series KXNBAOU)")
            print("Check Kalshi for the correct NBA total points series ticker.")
            sys.exit(1)
        cmd_basketball(args, league="nba")
    elif args.command == "ncaab-points":
        args.market_type = "total_points"
        if not args.series:
            print("ERROR: --series is required for ncaab-points")
            print("Check Kalshi for the correct NCAAB total points series ticker.")
            sys.exit(1)
        cmd_basketball(args, league="ncaab")
    elif args.command == "analyze":
        cmd_analyze(args)


if __name__ == "__main__":
    main()
