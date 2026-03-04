#!/usr/bin/env python3
"""
Auto-discover and record all live NCAAB games with real Kalshi market data.

Fetches KXNCAAMBGAME events from Kalshi, matches them to ESPN live games,
and records all matched games in parallel using NCAABGameRecorder.

Usage:
    # List today's games and Kalshi matches (dry discovery, no recording)
    python scripts/record_live_ncaab.py --list

    # Record all live games
    python scripts/record_live_ncaab.py -v

    # Record with custom output dir and poll interval
    python scripts/record_live_ncaab.py --output data/recordings --poll-interval 5000

    # Record tomorrow's games
    python scripts/record_live_ncaab.py --date 2026-02-07

    # Limit concurrent recordings
    python scripts/record_live_ncaab.py --max-games 10

Press Ctrl+C to stop gracefully (saves all in-progress recordings).
"""

import argparse
import asyncio
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Unbuffered output
sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.simulation.ncaab_recorder import NCAABGameRecorder, list_live_ncaab_games
from src.kalshi.client import KalshiClient
from src.kalshi.auth import KalshiAuth

logger = logging.getLogger(__name__)

# =============================================================================
# ESPN → Kalshi team abbreviation aliases
# =============================================================================
# ESPN and Kalshi sometimes use different abbreviations for the same team.
# This maps ESPN abbreviation → Kalshi abbreviation for known differences.

ESPN_TO_KALSHI_ALIASES: Dict[str, str] = {
    "TA&M": "TXAM",
    "TAMU": "TXAM",
    "TAM": "TXAM",
    "MASS": "UMASS",
    "UMES": "UMES",
    "LOU": "LOU",
    "CONN": "UCONN",
    "MSST": "MISSST",
    "MISS": "OLEMISS",
    "USM": "SMISS",
    "SMU": "SMU",
    "USC": "USC",
    "UNCW": "UNCW",
    "UNCG": "UNCG",
    "UNCA": "UNCA",
    "SFA": "SFAUS",
    "ETSU": "ETSU",
    "MTSU": "MTSU",
    "UTSA": "UTSA",
    "UTEP": "UTEP",
    "UTA": "UTARL",
    "SHSU": "SAMHOU",
    "FGCU": "FGCU",
    "CSUN": "CSUN",
    "LMU": "LOYMRY",
    "WSU": "WICHST",
}

# Reverse map: Kalshi → ESPN (auto-generated)
KALSHI_TO_ESPN_ALIASES: Dict[str, str] = {
    v: k for k, v in ESPN_TO_KALSHI_ALIASES.items()
}


# =============================================================================
# Kalshi Event Discovery
# =============================================================================

SERIES_TICKER = "KXNCAAMBGAME"
KALSHI_HOST = "https://api.elections.kalshi.com"
MONTH_MAP = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
MONTH_ABBR = {v: k for k, v in MONTH_MAP.items()}


def date_to_kalshi_prefix(d: date) -> str:
    """Convert a date to the Kalshi event ticker date prefix, e.g. '26FEB07'."""
    yy = d.year % 100
    mon = MONTH_ABBR[d.month]
    dd = d.day
    return f"{yy:02d}{mon}{dd:02d}"


def parse_event_ticker(event_ticker: str) -> Optional[Tuple[str, str, str]]:
    """Parse event ticker like KXNCAAMBGAME-26FEB07DUKEUNC into (date_prefix, team1, team2).

    Returns None if parsing fails. team1/team2 are from the ticker matchup portion
    (order depends on how Kalshi arranges them, typically AWYHOME).
    """
    # KXNCAAMBGAME-26FEB07DUKEUNC
    parts = event_ticker.split("-")
    if len(parts) < 2:
        return None

    suffix = parts[1]  # e.g. "26FEB07DUKEUNC"

    # Extract date prefix (6-7 chars: YYMmmDD)
    match = re.match(
        r"(\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2})(.*)", suffix
    )
    if not match:
        return None

    date_prefix = match.group(1)
    teams_part = match.group(2)  # e.g. "DUKEUNC"

    # We can't reliably split the teams portion without market data,
    # so return the raw teams string
    return date_prefix, teams_part, suffix


def extract_team_from_market_ticker(market_ticker: str) -> Optional[str]:
    """Extract the team code from a market ticker suffix.

    E.g. KXNCAAMBGAME-26FEB07DUKEUNC-DUKE -> DUKE
    """
    parts = market_ticker.split("-")
    if len(parts) >= 3:
        return parts[-1]
    return None


def fetch_kalshi_ncaab_events(
    auth: KalshiAuth,
    target_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Fetch KXNCAAMBGAME events from Kalshi REST API.

    Args:
        auth: KalshiAuth instance for signing requests.
        target_date: Filter to events on this date. None = all open events.

    Returns:
        List of event dicts with nested markets.
    """
    import requests

    path = "/trade-api/v2/events"
    params = {
        "status": "open",
        "series_ticker": SERIES_TICKER,
        "limit": 200,
        "with_nested_markets": "true",
    }

    all_events = []
    cursor = None

    for _ in range(10):  # max pages
        if cursor:
            params["cursor"] = cursor

        headers = auth.sign_request("GET", path, "")
        headers["Content-Type"] = "application/json"

        resp = requests.get(
            f"{KALSHI_HOST}{path}", headers=headers, params=params, timeout=15
        )
        if resp.status_code != 200:
            logger.error(f"Failed to fetch events: HTTP {resp.status_code}")
            break

        data = resp.json()
        events = data.get("events", [])
        all_events.extend(events)

        cursor = data.get("cursor")
        if not cursor or not events:
            break

    # Filter by date if specified
    if target_date:
        prefix = date_to_kalshi_prefix(target_date)
        all_events = [e for e in all_events if prefix in e.get("event_ticker", "")]

    logger.info(
        f"Fetched {len(all_events)} NCAAB events from Kalshi"
        + (f" for {target_date}" if target_date else "")
    )
    return all_events


def build_ticker_map(
    events: List[Dict[str, Any]],
) -> Dict[frozenset, Dict[str, str]]:
    """Build a team-pair → ticker mapping from Kalshi events.

    For each event with exactly 2 markets, extracts team codes from market
    ticker suffixes and maps frozenset({team1, team2}) to tickers.

    Returns:
        Dict mapping frozenset({team1, team2}) -> {
            "event_ticker": str,
            "teams": {team_code: market_ticker, ...},
            "matchup_str": str,  # raw matchup portion from event ticker
        }
    """
    ticker_map = {}

    for event in events:
        event_ticker = event.get("event_ticker", "")
        markets = event.get("markets", [])

        # We need exactly 2 markets (one per team win outcome)
        game_markets = [
            m for m in markets if m.get("status") in ("open", "active", "closed")
        ]

        if len(game_markets) < 2:
            logger.debug(f"Skipping {event_ticker}: only {len(game_markets)} markets")
            continue

        # Extract team codes from the first 2 market tickers
        teams = {}
        for m in game_markets[:2]:
            ticker = m.get("ticker", "")
            team = extract_team_from_market_ticker(ticker)
            if team:
                teams[team] = ticker

        if len(teams) < 2:
            logger.debug(f"Skipping {event_ticker}: couldn't extract 2 teams")
            continue

        team_codes = list(teams.keys())
        key = frozenset(team_codes)

        # Parse matchup string from event ticker
        parsed = parse_event_ticker(event_ticker)
        matchup_str = parsed[2] if parsed else ""

        ticker_map[key] = {
            "event_ticker": event_ticker,
            "teams": teams,
            "matchup_str": matchup_str,
        }

        logger.debug(f"Mapped: {team_codes[0]} vs {team_codes[1]} -> {event_ticker}")

    logger.info(f"Built ticker map with {len(ticker_map)} games")
    return ticker_map


# =============================================================================
# ESPN ↔ Kalshi Matching
# =============================================================================


def normalize_abbrev(abbrev: str) -> str:
    """Normalize a team abbreviation for matching.

    Applies ESPN→Kalshi aliases, strips special characters, uppercases.
    """
    abbrev = abbrev.upper().strip()

    # Apply known aliases
    if abbrev in ESPN_TO_KALSHI_ALIASES:
        return ESPN_TO_KALSHI_ALIASES[abbrev]

    # Strip special chars (& . -)
    cleaned = re.sub(r"[&.\-']", "", abbrev)
    return cleaned


def match_espn_to_kalshi(
    espn_game: Dict[str, Any],
    ticker_map: Dict[frozenset, Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """Match an ESPN game to Kalshi tickers.

    Tries exact frozenset match first, then normalized/fuzzy matching.

    Args:
        espn_game: ESPN game dict with home_team, away_team keys.
        ticker_map: Output of build_ticker_map().

    Returns:
        Dict with "home_ticker", "away_ticker", "event_ticker" if matched, else None.
    """
    home_espn = espn_game["home_team"]
    away_espn = espn_game["away_team"]
    home_norm = normalize_abbrev(home_espn)
    away_norm = normalize_abbrev(away_espn)

    # Strategy 1: Exact frozenset match on normalized abbreviations
    key = frozenset([home_norm, away_norm])
    if key in ticker_map:
        return _assign_tickers(ticker_map[key], home_norm, away_norm)

    # Strategy 2: Try raw ESPN abbreviations (some might match Kalshi directly)
    key_raw = frozenset([home_espn.upper(), away_espn.upper()])
    if key_raw in ticker_map:
        return _assign_tickers(
            ticker_map[key_raw], home_espn.upper(), away_espn.upper()
        )

    # Strategy 3: Substring match against event ticker matchup portion
    for fset, data in ticker_map.items():
        matchup = data.get("matchup_str", "").upper()
        if not matchup:
            continue
        # Check if both normalized team abbreviations appear in the matchup string
        if home_norm in matchup and away_norm in matchup:
            return _assign_tickers(data, home_norm, away_norm)

    # Strategy 4: Try matching with Kalshi→ESPN reverse aliases
    for fset, data in ticker_map.items():
        kalshi_teams = list(fset)
        # Normalize both sides and compare
        mapped_teams = set()
        for kt in kalshi_teams:
            mapped_teams.add(kt)
            if kt in KALSHI_TO_ESPN_ALIASES:
                mapped_teams.add(normalize_abbrev(KALSHI_TO_ESPN_ALIASES[kt]))

        if home_norm in mapped_teams and away_norm in mapped_teams:
            return _assign_tickers(data, home_norm, away_norm)

    return None


def _assign_tickers(
    data: Dict[str, Any],
    home_norm: str,
    away_norm: str,
) -> Dict[str, str]:
    """Assign home/away tickers from the ticker map entry.

    Matches normalized team abbreviations to the Kalshi team codes in the
    market tickers, assigning home_ticker and away_ticker correctly.
    """
    teams = data["teams"]  # {KALSHI_CODE: ticker, ...}

    home_ticker = None
    away_ticker = None

    for code, ticker in teams.items():
        code_upper = code.upper()
        if code_upper == home_norm or normalize_abbrev(code_upper) == home_norm:
            home_ticker = ticker
        elif code_upper == away_norm or normalize_abbrev(code_upper) == away_norm:
            away_ticker = ticker

    # Fallback: if exact match failed, just assign in order
    if not home_ticker or not away_ticker:
        tickers = list(teams.values())
        if len(tickers) >= 2:
            # Try to guess by checking which ticker ends with home team code
            for t in tickers:
                suffix = t.split("-")[-1].upper()
                if suffix == home_norm or normalize_abbrev(suffix) == home_norm:
                    home_ticker = t
                elif suffix == away_norm or normalize_abbrev(suffix) == away_norm:
                    away_ticker = t

    # Last resort: assign first two
    if not home_ticker or not away_ticker:
        tickers = list(teams.values())
        home_ticker = home_ticker or tickers[0]
        away_ticker = away_ticker or tickers[1] if len(tickers) > 1 else tickers[0]

    return {
        "home_ticker": home_ticker,
        "away_ticker": away_ticker,
        "event_ticker": data["event_ticker"],
    }


# =============================================================================
# Recording
# =============================================================================


async def record_game(
    game: Dict[str, Any],
    tickers: Dict[str, str],
    client: KalshiClient,
    output_dir: Path,
    poll_interval_ms: int = 3000,
) -> Optional[Path]:
    """Record a single game using NCAABGameRecorder.

    Args:
        game: ESPN game dict.
        tickers: Dict with home_ticker, away_ticker.
        client: Connected KalshiClient.
        output_dir: Where to save recording.
        poll_interval_ms: Poll interval in ms.

    Returns:
        Path to saved recording file, or None on failure.
    """
    home = game["home_team"]
    away = game["away_team"]
    game_id = game["game_id"]
    home_ticker = tickers["home_ticker"]
    away_ticker = tickers["away_ticker"]

    logger.info(f"[{away}@{home}] Starting recording (game_id={game_id})")
    logger.info(f"[{away}@{home}]   Home: {home_ticker}")
    logger.info(f"[{away}@{home}]   Away: {away_ticker}")

    recorder = NCAABGameRecorder(
        game_id=game_id,
        home_team=home,
        away_team=away,
        home_ticker=home_ticker,
        away_ticker=away_ticker,
    )

    try:
        await recorder.start_async(
            kalshi_client=client,
            poll_interval_ms=poll_interval_ms,
        )
    except Exception as e:
        logger.error(f"[{away}@{home}] Recording error: {e}")

    # Save if we got frames
    if recorder.frames:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{away}_vs_{home}_{timestamp}.json"
        filepath = output_dir / filename
        recorder.save(str(filepath))
        logger.info(
            f"[{away}@{home}] Saved {len(recorder.frames)} frames -> {filepath}"
        )
        return filepath
    else:
        logger.warning(f"[{away}@{home}] No frames captured, not saving")
        return None


# =============================================================================
# Main Loop
# =============================================================================


async def main_loop(
    client: KalshiClient,
    auth: KalshiAuth,
    output_dir: Path,
    target_date: Optional[date] = None,
    poll_interval_ms: int = 3000,
    scan_interval: int = 60,
    ticker_refresh_interval: int = 300,
    max_games: Optional[int] = None,
    verbose: bool = False,
):
    """Continuously discover and record live NCAAB games.

    Args:
        client: Connected KalshiClient.
        auth: KalshiAuth for REST discovery.
        output_dir: Directory for saving recordings.
        target_date: Date to filter Kalshi events for.
        poll_interval_ms: Recording poll interval in ms.
        scan_interval: Seconds between ESPN scans for new games.
        ticker_refresh_interval: Seconds between Kalshi ticker map refreshes.
        max_games: Max concurrent recordings (None = unlimited).
        verbose: Verbose logging.
    """
    active_tasks: Dict[str, asyncio.Task] = {}  # game_id -> task
    finished_games: Set[str] = set()  # game_ids we've already recorded
    ticker_map: Dict[frozenset, Dict[str, str]] = {}
    last_ticker_refresh = 0.0

    logger.info("=" * 60)
    logger.info("NCAAB Live Game Recorder")
    logger.info("=" * 60)
    logger.info(f"Output:         {output_dir}")
    logger.info(f"Poll interval:  {poll_interval_ms}ms")
    logger.info(f"Scan interval:  {scan_interval}s")
    logger.info(f"Date filter:    {target_date or 'today'}")
    if max_games:
        logger.info(f"Max concurrent: {max_games}")
    logger.info("=" * 60)

    while True:
        try:
            # Refresh Kalshi ticker map periodically
            now = time.time()
            if now - last_ticker_refresh > ticker_refresh_interval:
                events = fetch_kalshi_ncaab_events(auth, target_date)
                ticker_map = build_ticker_map(events)
                last_ticker_refresh = now
                if verbose:
                    logger.info(
                        f"Ticker map: {len(ticker_map)} games available on Kalshi"
                    )

            # Fetch live ESPN games
            espn_games = list_live_ncaab_games()
            live_games = [g for g in espn_games if g["status"] in ("live", "pregame")]

            # Clean up finished tasks
            done_ids = []
            for game_id, task in active_tasks.items():
                if task.done():
                    done_ids.append(game_id)
                    try:
                        result = task.result()
                        if result:
                            logger.info(f"Recording complete: {result}")
                    except Exception as e:
                        logger.error(f"Recording task failed for {game_id}: {e}")
            for gid in done_ids:
                finished_games.add(gid)
                del active_tasks[gid]

            # Start new recordings
            new_started = 0
            for game in live_games:
                game_id = game["game_id"]

                # Skip if already recording or finished
                if game_id in active_tasks or game_id in finished_games:
                    continue

                # Respect max concurrent limit
                if max_games and len(active_tasks) >= max_games:
                    break

                # Match to Kalshi tickers
                tickers = match_espn_to_kalshi(game, ticker_map)
                if not tickers:
                    if verbose:
                        logger.debug(
                            f"No Kalshi match for {game['away_team']}@{game['home_team']} "
                            f"(espn: {game['away_team']}/{game['home_team']})"
                        )
                    continue

                # Start recording task
                task = asyncio.create_task(
                    record_game(game, tickers, client, output_dir, poll_interval_ms)
                )
                active_tasks[game_id] = task
                new_started += 1

            # Status summary
            total_espn = len(live_games)
            recording = len(active_tasks)
            finished = len(finished_games)

            if new_started > 0 or verbose:
                logger.info(
                    f"[Status] ESPN live/pre: {total_espn} | "
                    f"Recording: {recording} | "
                    f"Finished: {finished} | "
                    f"Kalshi games: {len(ticker_map)}"
                )

            # Check if all tasks done and no more games expected
            if not active_tasks and not live_games and finished_games:
                logger.info("All games finished and no live games remaining. Exiting.")
                break

        except Exception as e:
            logger.error(f"Main loop error: {e}")

        await asyncio.sleep(scan_interval)

    # Wait for any remaining tasks
    if active_tasks:
        logger.info(f"Waiting for {len(active_tasks)} remaining recordings...")
        results = await asyncio.gather(*active_tasks.values(), return_exceptions=True)
        for game_id, result in zip(active_tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Recording {game_id} failed: {result}")
            elif result:
                logger.info(f"Recording saved: {result}")


async def list_mode(
    auth: KalshiAuth, target_date: Optional[date] = None, verbose: bool = False
):
    """List ESPN games and their Kalshi ticker matches (no recording)."""
    # Fetch Kalshi events
    events = fetch_kalshi_ncaab_events(auth, target_date)
    ticker_map = build_ticker_map(events)

    # Fetch ESPN games
    espn_games = list_live_ncaab_games()

    print(f"\n{'=' * 90}")
    print(f"NCAAB Games — {target_date or 'today'}")
    print(f"{'=' * 90}")
    print(f"Kalshi events: {len(ticker_map)} | ESPN games: {len(espn_games)}")
    print()

    # Show Kalshi games available
    if verbose:
        print("Kalshi KXNCAAMBGAME events:")
        for fset, data in sorted(
            ticker_map.items(), key=lambda x: x[1]["event_ticker"]
        ):
            list(fset)
            tickers = list(data["teams"].values())
            print(f"  {data['event_ticker']}")
            for t in tickers:
                print(f"    -> {t}")
        print()

    # Show ESPN games with match status
    print(
        f"{'Status':<10} {'Matchup':<25} {'Score':<12} {'Period':<8} {'Kalshi Match'}"
    )
    print("-" * 90)

    matched = 0
    unmatched = 0

    for game in sorted(espn_games, key=lambda g: g["status"]):
        tickers = match_espn_to_kalshi(game, ticker_map)
        score = f"{game['away_score']}-{game['home_score']}"
        period = f"H{game['period']}" if game["period"] > 0 else "-"

        if tickers:
            match_str = tickers["event_ticker"]
            matched += 1
        else:
            match_str = "NO MATCH"
            unmatched += 1

        print(
            f"{game['status']:<10} {game['matchup']:<25} {score:<12} {period:<8} {match_str}"
        )

        if verbose and tickers:
            print(f"{'':>10} Home: {tickers['home_ticker']}")
            print(f"{'':>10} Away: {tickers['away_ticker']}")

    print()
    print(f"Matched: {matched} | Unmatched: {unmatched}")

    if unmatched > 0:
        print("\nUnmatched games may not have Kalshi markets, or need alias entries.")
    print()


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Auto-discover and record all live NCAAB games with Kalshi market data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/record_live_ncaab.py --list              # Dry discovery
  python scripts/record_live_ncaab.py --list -v            # Verbose with tickers
  python scripts/record_live_ncaab.py -v                   # Record all live games
  python scripts/record_live_ncaab.py --max-games 5        # Limit concurrent
  python scripts/record_live_ncaab.py --date 2026-02-07    # Specific date
        """,
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List games and Kalshi matches (no recording)",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="data/recordings",
        help="Output directory for recordings (default: data/recordings)",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=3000,
        help="Recording poll interval in ms (default: 3000)",
    )

    parser.add_argument(
        "--scan-interval",
        type=int,
        default=60,
        help="Seconds between ESPN scans for new games (default: 60)",
    )

    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date YYYY-MM-DD (default: today)",
    )

    parser.add_argument(
        "--max-games",
        type=int,
        default=None,
        help="Maximum concurrent game recordings",
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use Kalshi demo API",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Logging
    log_level = (
        logging.DEBUG
        if args.debug
        else (logging.INFO if args.verbose else logging.WARNING)
    )
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Always show our logger at INFO+
    logger.setLevel(logging.INFO if not args.debug else logging.DEBUG)

    # Parse date
    target_date = None
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = date.today()

    # Auth
    auth = KalshiAuth.from_env()

    # List mode
    if args.list:
        asyncio.run(list_mode(auth, target_date, args.verbose))
        return 0

    # Recording mode
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    client = KalshiClient.from_env(demo=args.demo)

    # Signal handling
    stop_event = asyncio.Event()

    async def run():
        async with client:
            # Set up signal handlers
            loop = asyncio.get_running_loop()

            def handle_signal():
                logger.info("\nSignal received, stopping gracefully...")
                stop_event.set()

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, handle_signal)

            # Run main loop with cancellation support
            main_task = asyncio.create_task(
                main_loop(
                    client=client,
                    auth=auth,
                    output_dir=output_dir,
                    target_date=target_date,
                    poll_interval_ms=args.poll_interval,
                    scan_interval=args.scan_interval,
                    max_games=args.max_games,
                    verbose=args.verbose or args.debug,
                )
            )

            # Wait for either main loop to finish or stop signal
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                [main_task, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel remaining
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # If stop signal, the main_task was cancelled - that's fine
            if main_task in done:
                try:
                    main_task.result()
                except Exception as e:
                    logger.error(f"Main loop error: {e}")

        logger.info("Shutdown complete.")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
