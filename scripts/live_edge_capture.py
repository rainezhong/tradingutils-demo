#!/usr/bin/env python3
"""
Live Edge Capture Strategy - CLI Runner

Probability-edge directional trading on Kalshi prediction markets.
Estimates fair value using a probability model, finds edge vs market price,
and places limit orders to capture that edge.

Usage:
    python scripts/live_edge_capture.py --sport nba --dry-run           # Dry run with Markov model
    python scripts/live_edge_capture.py --sport nba --dry-run -v        # Verbose
    python scripts/live_edge_capture.py --fair-values "TICK:0.65" -v    # Static fair values
    python scripts/live_edge_capture.py --sport nba --live              # Live trading (REAL MONEY)
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from typing import List, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.edge_capture_strategy import (
    EdgeCaptureConfig,
    EdgeCaptureStrategy,
    MarkovProbabilityProvider,
    StaticProbabilityProvider,
)
from signal_extraction.models.orderbook_intelligence import OrderbookIntelligence

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Sport Types
# =============================================================================

SPORT_PREFIXES = {
    "nba": "KXNBAGAME",
    "nba_totals": "KXNBATOTAL",
    "nba_spread": "KXNBASPREAD",
    "ncaab": "KXNCAAMBGAME",
    "ncaab_totals": "KXNCAAMBTOTAL",
    "ncaab_spread": "KXNCAAMBSPREAD",
    "nfl": "KXSB",
    "nhl": "KXNHLGAME",
    "ucl": "KXUCL",
    "tennis": "KXWTA",
    "soccer": "KXSOCCER",
}

# Map sport to MarkovWinModel SportType
SPORT_TYPE_MAP = {
    "nba": "NBA",
    "nba_totals": "NBA",
    "nba_spread": "NBA",
    "ncaab": "COLLEGE_BB",
    "ncaab_totals": "COLLEGE_BB",
    "ncaab_spread": "COLLEGE_BB",
    "nfl": "NFL",
    "nhl": "NHL",
    "soccer": "SOCCER",
}


# =============================================================================
# Market Discovery (reused from live_spread_capture.py)
# =============================================================================


def parse_game_date(ticker: str):
    """Parse game date from event ticker."""
    import re
    from datetime import datetime

    match = re.search(
        r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", ticker
    )
    if match:
        year = 2000 + int(match.group(1))
        month = {
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
        }[match.group(2)]
        day = int(match.group(3))
        return datetime(year, month, day)
    return None


def is_game_live(ticker: str) -> bool:
    """Check if game has started based on ticker date."""
    from datetime import datetime

    game_date = parse_game_date(ticker)
    if game_date is None:
        return True
    today = datetime.utcnow().date()
    return game_date.date() <= today


def discover_markets(
    sport: str,
    min_spread_cents: int = 2,
    max_spread_cents: int = 50,
    ticker_filter: Optional[Set[str]] = None,
    verbose: bool = False,
    live_games_only: bool = True,
    min_recent_volume: int = 50,
) -> List[str]:
    """Discover open markets from Kalshi REST API."""
    import requests
    from src.kalshi.auth import KalshiAuth

    auth = KalshiAuth.from_env()
    host = "https://api.elections.kalshi.com"

    prefix = SPORT_PREFIXES.get(sport, "")
    if not prefix and not ticker_filter:
        logger.error(
            f"Unknown sport: {sport}. Available: {list(SPORT_PREFIXES.keys())}"
        )
        return []

    # If specific tickers provided, validate them
    if ticker_filter:
        valid_tickers = []
        for ticker in ticker_filter:
            path = f"/trade-api/v2/markets/{ticker}"
            headers = auth.sign_request("GET", path, "")
            headers["Content-Type"] = "application/json"
            try:
                resp = requests.get(f"{host}{path}", headers=headers, timeout=10)
                if resp.status_code == 200:
                    market = resp.json().get("market", {})
                    status = market.get("status", "")
                    if status in ("open", "active"):
                        yes_bid = market.get("yes_bid", 0)
                        yes_ask = market.get("yes_ask", 0)
                        spread = yes_ask - yes_bid if yes_bid and yes_ask else 0
                        logger.info(
                            f"  {ticker}: bid={yes_bid}c ask={yes_ask}c spread={spread}c"
                        )
                        valid_tickers.append(ticker)
                    else:
                        logger.warning(f"  {ticker}: status={status} (skipping)")
                else:
                    logger.warning(f"  {ticker}: HTTP {resp.status_code} (not found)")
            except Exception as e:
                logger.warning(f"  {ticker}: Error - {e}")
            time.sleep(0.2)
        return valid_tickers

    # Discover by event prefix
    logger.info(f"Discovering {sport} markets (prefix={prefix})...")

    path = "/trade-api/v2/events"
    headers = auth.sign_request("GET", path, "")
    headers["Content-Type"] = "application/json"

    params = {
        "status": "open",
        "series_ticker": prefix,
        "limit": 200,
        "with_nested_markets": "true",
    }

    try:
        resp = requests.get(f"{host}{path}", headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Failed to fetch events: HTTP {resp.status_code}")
            return []
        events = resp.json().get("events", [])
        logger.info(f"Found {len(events)} events")
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []

    qualifying_tickers = []

    for event in events:
        event_ticker = event.get("event_ticker", "")
        markets = event.get("markets", [])

        for market in markets:
            ticker = market.get("ticker", "")
            status = market.get("status", "")
            if status not in ("open", "active"):
                continue

            yes_bid = market.get("yes_bid", 0)
            yes_ask = market.get("yes_ask", 0)
            if not yes_bid or not yes_ask:
                continue

            spread = yes_ask - yes_bid

            if spread < min_spread_cents or spread > max_spread_cents:
                continue

            if live_games_only and not is_game_live(event_ticker):
                if verbose:
                    logger.debug(f"  {ticker}: SKIPPED (game not started)")
                continue

            volume_24h = market.get("volume_24h", 0) or 0
            if min_recent_volume > 0 and volume_24h < min_recent_volume:
                if verbose:
                    logger.debug(
                        f"  {ticker}: SKIPPED (volume={volume_24h} < {min_recent_volume})"
                    )
                continue

            if verbose:
                mid = (yes_bid + yes_ask) / 2
                logger.info(
                    f"  {ticker}: bid={yes_bid}c ask={yes_ask}c "
                    f"spread={spread}c mid={mid:.1f}c vol={volume_24h}"
                )

            qualifying_tickers.append(ticker)

    logger.info(f"Found {len(qualifying_tickers)} qualifying markets")
    return qualifying_tickers


# =============================================================================
# Score Feed Integration
# =============================================================================


async def run_score_feed(
    provider: MarkovProbabilityProvider,
    tickers: List[str],
    sport: str,
    poll_interval: float = 30.0,
    stop_event: asyncio.Event = None,
) -> None:
    """Background task: poll score feed and update provider with game states."""
    from signal_extraction.models.markov_win_model import GameState

    sport_type_name = SPORT_TYPE_MAP.get(sport)
    if not sport_type_name:
        logger.warning(f"No SportType mapping for {sport}, score feed disabled")
        return

    # Build ticker -> game mapping
    # For NBA, use get_nba_game_info_from_ticker
    if sport_type_name in ("NBA",):
        try:
            from signal_extraction.data_feeds.score_feed import (
                ScoreAnalyzer,
                get_nba_game_info_from_ticker,
                get_nbalive_games,
            )
        except ImportError:
            logger.warning(
                "Score feed imports unavailable, running without live scores"
            )
            return

        logger.info(f"Starting NBA score feed polling (interval={poll_interval}s)")

        while stop_event is None or not stop_event.is_set():
            try:
                live_games = get_nbalive_games()

                for ticker in tickers:
                    game_info = get_nba_game_info_from_ticker(ticker)
                    if not game_info:
                        continue

                    game_id = game_info["game_id"]

                    # Find matching live game score
                    for game in live_games:
                        if game["id"] == game_id:
                            score_parts = game.get("score", "0 - 0").split(" - ")
                            try:
                                away_score = int(score_parts[0].strip())
                                home_score = int(score_parts[1].strip())
                            except (ValueError, IndexError):
                                continue

                            clock_str = game.get("clock", "")
                            time_remaining = (
                                ScoreAnalyzer.parse_time_remaining(clock_str)
                                if clock_str
                                else 0
                            )

                            period = 1
                            clock_lower = clock_str.lower()
                            for q in range(4, 0, -1):
                                if f"q{q}" in clock_lower or f"{q}q" in clock_lower:
                                    period = q
                                    break

                            # Determine if this ticker is home or away team
                            # Ticker ends with team abbrev, e.g. -IND
                            ticker_team = ticker.rsplit("-", 1)[-1].upper()
                            is_home = ticker_team == game_info["home_team"]

                            # score_diff from this ticker's team perspective
                            if is_home:
                                score_diff = home_score - away_score
                            else:
                                score_diff = away_score - home_score

                            game_state = GameState(
                                score_diff=score_diff,
                                time_remaining=float(time_remaining),
                                period=period,
                                home_possession=True,
                                momentum=0.0,
                            )

                            provider.set_game_state(ticker, game_state)

                            logger.debug(
                                f"[SCORE] {ticker}: {away_score}-{home_score} "
                                f"Q{period} {clock_str} "
                                f"({'home' if is_home else 'away'}) "
                                f"diff={score_diff:+d} -> fv update"
                            )
                            break

            except Exception as e:
                logger.warning(f"Score feed error: {e}")

            for _ in range(int(poll_interval)):
                if stop_event and stop_event.is_set():
                    break
                await asyncio.sleep(1.0)

    elif sport_type_name in ("NFL",):
        import json
        import urllib.request

        # NFL team abbreviation -> Kalshi ticker suffix mapping
        # Build from tickers: e.g. KXSB-26-SEA -> SEA, KXSB-26-NE -> NE
        ticker_teams = {}
        for t in tickers:
            suffix = t.split("-")[-1].upper()
            ticker_teams[suffix] = t

        logger.info(f"Starting NFL/ESPN score feed (interval={poll_interval}s)")
        logger.info(f"  Ticker-team map: {ticker_teams}")

        def _fetch_espn_nfl():
            url = (
                "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())

        def _nfl_time_remaining(period: int, clock_str: str) -> float:
            try:
                parts = clock_str.split(":")
                if len(parts) == 2:
                    period_secs = int(parts[0]) * 60 + int(parts[1])
                else:
                    period_secs = 0.0
            except (ValueError, AttributeError):
                period_secs = 0.0
            remaining_periods = max(0, 4 - period)
            return period_secs + remaining_periods * 15 * 60

        while stop_event is None or not stop_event.is_set():
            try:
                data = _fetch_espn_nfl()
                events = data.get("events", [])

                for event in events:
                    competition = event.get("competitions", [{}])[0]
                    competitors = competition.get("competitors", [])

                    home_comp = away_comp = None
                    for c in competitors:
                        if c.get("homeAway") == "home":
                            home_comp = c
                        else:
                            away_comp = c

                    if not home_comp or not away_comp:
                        continue

                    status = competition.get("status", {})
                    state = status.get("type", {}).get("state", "pre")
                    if state != "in":
                        continue

                    period = status.get("period", 1)
                    clock = status.get("displayClock", "15:00")

                    home_abbr = (
                        home_comp.get("team", {}).get("abbreviation", "").upper()
                    )
                    away_abbr = (
                        away_comp.get("team", {}).get("abbreviation", "").upper()
                    )
                    home_score = int(home_comp.get("score", 0))
                    away_score = int(away_comp.get("score", 0))

                    time_rem = _nfl_time_remaining(period, clock)

                    # Update any matching tickers
                    for abbr, ticker in ticker_teams.items():
                        if abbr == home_abbr:
                            # Home team ticker: score_diff = home - away
                            gs = GameState(
                                score_diff=home_score - away_score,
                                time_remaining=time_rem,
                                period=period,
                                home_possession=True,
                                momentum=0.0,
                            )
                            provider.set_game_state(ticker, gs)
                            logger.info(
                                f"[SCORE] {ticker}: {away_abbr} {away_score} - "
                                f"{home_score} {home_abbr} Q{period} {clock} "
                                f"-> fv={provider.estimate(ticker, None) and provider._calibrate(provider._model.get_fair_value(gs)):.3f}"
                            )
                        elif abbr == away_abbr:
                            # Away team ticker: flip perspective
                            gs = GameState(
                                score_diff=away_score - home_score,
                                time_remaining=time_rem,
                                period=period,
                                home_possession=True,
                                momentum=0.0,
                            )
                            provider.set_game_state(ticker, gs)
                            logger.info(
                                f"[SCORE] {ticker}: {away_abbr} {away_score} - "
                                f"{home_score} {home_abbr} Q{period} {clock} "
                                f"-> fv={provider._calibrate(provider._model.get_fair_value(gs)):.3f}"
                            )

            except Exception as e:
                logger.warning(f"NFL score feed error: {e}")

            for _ in range(int(poll_interval)):
                if stop_event and stop_event.is_set():
                    break
                await asyncio.sleep(1.0)

    else:
        logger.info(
            f"Score feed for {sport} not yet implemented, "
            f"provider will use any externally set game states"
        )
        return


# =============================================================================
# Main
# =============================================================================


async def run_strategy(
    tickers: List[str],
    config: EdgeCaptureConfig,
    provider,
    dry_run: bool = True,
    use_websocket: bool = False,
    poll_interval: float = 5.0,
    passive_fill_rate: float = 0.025,
    sport: str = "nba",
    score_feed_interval: float = 30.0,
) -> None:
    """Run the edge capture strategy."""

    orderbook_intel = OrderbookIntelligence()

    strategy = EdgeCaptureStrategy(
        config=config,
        provider=provider,
        dry_run=dry_run,
        log_dir="data/edge_capture",
        use_polling=not use_websocket,
        poll_interval=poll_interval,
        passive_fill_rate=passive_fill_rate,
        orderbook_intel=orderbook_intel,
    )

    # Signal handler
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("\nSignal received, shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    # Print initial status
    mode_str = "DRY RUN" if dry_run else "LIVE"
    logger.info("=" * 60)
    logger.info(f"EDGE CAPTURE STRATEGY ({mode_str})")
    logger.info("=" * 60)
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Provider: {type(provider).__name__}")
    logger.info(f"Min edge: {config.min_edge_cents}c")
    logger.info(f"Entry aggressiveness: {config.entry_aggressiveness}")
    logger.info(f"Exit mode: {config.exit_mode}")
    logger.info(f"Stop loss: {config.stop_loss_cents}c")
    logger.info(f"Max hold time: {config.max_hold_time_seconds:.0f}s")
    if config.take_profit_cents > 0:
        logger.info(f"Take profit: {config.take_profit_cents}c")
    logger.info(f"Max concurrent: {config.max_concurrent_positions}")
    logger.info(f"Max daily loss: ${config.max_daily_loss_dollars:.2f}")
    if config.use_kelly_sizing:
        logger.info(
            f"Kelly: fraction={config.kelly_fraction}, "
            f"max_bankroll_pct={config.kelly_max_bankroll_pct}"
        )
    if config.bankroll_override:
        logger.info(f"Bankroll override: ${config.bankroll_override:.2f}")
    logger.info(
        f"Feed: {'WebSocket' if use_websocket else f'Polling ({poll_interval}s)'}"
    )
    if config.allowed_ticker_prefixes:
        logger.info(f"Ticker filter: {config.allowed_ticker_prefixes}")
    if config.enable_alerts:
        logger.info(f"Alerts: ENABLED (log={config.alerts_log_file})")
    logger.info("=" * 60)

    if len(tickers) <= 20:
        for t in tickers:
            logger.info(f"  -> {t}")

    logger.info("\nStarting... (Ctrl+C to stop)\n")

    # Start score feed if using Markov provider
    score_feed_task = None
    if isinstance(provider, MarkovProbabilityProvider):
        score_feed_task = asyncio.create_task(
            run_score_feed(
                provider=provider,
                tickers=tickers,
                sport=sport,
                poll_interval=score_feed_interval,
                stop_event=stop_event,
            )
        )

    # Start strategy
    strategy_task = asyncio.create_task(strategy.start(tickers))

    # Periodic status
    async def status_printer():
        while not stop_event.is_set():
            await asyncio.sleep(60)
            if not stop_event.is_set():
                strategy.print_status()

    status_task = asyncio.create_task(status_printer())

    # Wait for stop
    await stop_event.wait()

    # Graceful shutdown
    logger.info("Stopping strategy...")
    await strategy.stop()
    strategy_task.cancel()
    status_task.cancel()

    if score_feed_task:
        score_feed_task.cancel()
        try:
            await score_feed_task
        except asyncio.CancelledError:
            pass

    try:
        await strategy_task
    except asyncio.CancelledError:
        pass
    try:
        await status_task
    except asyncio.CancelledError:
        pass

    strategy.print_status()
    logger.info("Shutdown complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Edge Capture Strategy - Probability-edge directional trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/live_edge_capture.py --sport nba --dry-run              # Dry run, Markov model
  python scripts/live_edge_capture.py --sport nba --dry-run -v           # Verbose
  python scripts/live_edge_capture.py --fair-values "TICK:0.65" -v       # Static provider
  python scripts/live_edge_capture.py --sport nba --live                 # Live (REAL MONEY)
  python scripts/live_edge_capture.py --sport ncaab --exit-mode target   # Target-based exits
        """,
    )

    # Mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run", action="store_true", default=True, help="Dry run mode (default)"
    )
    mode_group.add_argument(
        "--live", action="store_true", help="Live trading mode (REAL MONEY)"
    )

    # Required
    parser.add_argument(
        "--sport",
        type=str,
        default="nba",
        help="Sport to trade (default: nba). Options: "
        + ", ".join(SPORT_PREFIXES.keys()),
    )

    # Probability source (mutually exclusive)
    prob_group = parser.add_mutually_exclusive_group()
    prob_group.add_argument(
        "--model",
        type=str,
        default="markov",
        choices=["markov"],
        help="Probability model (default: markov)",
    )
    prob_group.add_argument(
        "--fair-values",
        type=str,
        default=None,
        help='Static fair values, comma-separated "TICKER1:0.65,TICKER2:0.40"',
    )

    # Edge
    parser.add_argument(
        "--min-edge",
        type=int,
        default=5,
        help="Minimum edge in cents after fees (default: 5)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.3,
        help="Minimum model confidence 0-1 (default: 0.3)",
    )

    # Entry
    parser.add_argument(
        "--entry-timeout",
        type=float,
        default=300.0,
        help="Entry timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--aggressiveness",
        type=float,
        default=0.5,
        help="Entry aggressiveness 0-1 (default: 0.5). 0=at bid, 1=at mid.",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=25,
        help="Maximum entry size in contracts (default: 25)",
    )

    # Exit
    parser.add_argument(
        "--exit-mode",
        type=str,
        default="model",
        choices=["model", "target", "resolution"],
        help="Exit mode (default: model). model=exit on edge reversal, target=take-profit/stop-loss, resolution=hold to settlement.",
    )
    parser.add_argument(
        "--stop-loss",
        type=int,
        default=0,
        help="Stop loss in cents (default: 0 = disabled)",
    )
    parser.add_argument(
        "--edge-reversal-threshold",
        type=int,
        default=8,
        help="Edge reversal threshold cents (default: 8)",
    )
    parser.add_argument(
        "--take-profit",
        type=int,
        default=0,
        help="Take profit in cents (default: 0 = exit at fair value)",
    )
    parser.add_argument(
        "--max-hold-time",
        type=float,
        default=3600.0,
        help="Maximum hold time in seconds (default: 3600)",
    )

    # Kelly
    parser.add_argument(
        "--kelly-fraction",
        type=float,
        default=0.25,
        help="Kelly fraction (default: 0.25 = quarter-Kelly)",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Manual bankroll override in dollars (default: fetch from API)",
    )
    parser.add_argument(
        "--no-kelly",
        action="store_true",
        help="Disable Kelly sizing, use fixed max-size",
    )

    # Risk
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=10,
        help="Maximum concurrent positions (default: 10)",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=50.0,
        help="Maximum daily loss in USD (default: 50)",
    )

    # Market selection
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Specific ticker(s), comma-separated",
    )
    parser.add_argument(
        "--min-volume",
        type=int,
        default=50,
        help="Minimum 24h volume (default: 50)",
    )
    parser.add_argument(
        "--min-spread",
        type=int,
        default=3,
        help="Minimum spread in cents (default: 3)",
    )
    parser.add_argument(
        "--min-score-change",
        type=int,
        default=0,
        help="Min score_diff change to trade (0=disabled, default: 0)",
    )
    parser.add_argument(
        "--max-spread",
        type=int,
        default=50,
        help="Maximum spread in cents (default: 50)",
    )
    parser.add_argument(
        "--include-pregame",
        action="store_true",
        help="Include pre-game markets",
    )
    parser.add_argument(
        "--buy-yes-only",
        action="store_true",
        help="Only allow buy_yes trades (back favorites)",
    )
    parser.add_argument(
        "--min-fv",
        type=float,
        default=0.0,
        help="Min fair value for buy_yes entry (e.g. 0.55)",
    )

    # Feed
    parser.add_argument(
        "--use-websocket", action="store_true", help="Use WebSocket instead of polling"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Poll interval in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--passive-fill-rate",
        type=float,
        default=0.025,
        help="Passive fill hazard rate per second in dry run (default: 0.025)",
    )

    # Score feed
    parser.add_argument(
        "--score-feed-interval",
        type=float,
        default=30.0,
        help="Score feed poll interval in seconds (default: 30)",
    )

    # Calibration
    parser.add_argument(
        "--calibration",
        type=str,
        default="shrink",
        choices=["none", "shrink", "platt"],
        help="Model calibration mode (default: shrink)",
    )
    parser.add_argument(
        "--shrink-factor",
        type=float,
        default=0.70,
        help="Shrink calibration factor (default: 0.70)",
    )

    # Misc
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger("src.strategies").setLevel(logging.DEBUG)

    # Confirm live mode
    dry_run = not args.live
    if args.live:
        logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            logger.info("Aborted.")
            return 1

    # Parse ticker filter
    ticker_filter = None
    if args.ticker:
        ticker_filter = set(t.strip() for t in args.ticker.split(","))

    # Create provider
    if args.fair_values:
        # Static provider
        fair_values = {}
        for pair in args.fair_values.split(","):
            pair = pair.strip()
            if ":" in pair:
                t, v = pair.split(":", 1)
                fair_values[t.strip()] = float(v.strip())
        provider = StaticProbabilityProvider(fair_values)
        logger.info(f"Using static provider: {fair_values}")
    else:
        # Markov provider
        from signal_extraction.models.markov_win_model import SportType

        sport_type_name = SPORT_TYPE_MAP.get(args.sport)
        if sport_type_name:
            sport_type = SportType(
                sport_type_name.lower()
                if sport_type_name != "COLLEGE_BB"
                else "college_basketball"
            )
            provider = MarkovProbabilityProvider(
                sport_type,
                calibration=args.calibration,
                shrink_factor=args.shrink_factor,
            )
            logger.info(f"Using Markov provider: {sport_type_name}")
        else:
            logger.error(
                f"No model mapping for sport '{args.sport}'. Use --fair-values instead."
            )
            return 1

    # Discover markets
    live_games_only = not args.include_pregame
    tickers = discover_markets(
        sport=args.sport,
        min_spread_cents=args.min_spread,
        max_spread_cents=args.max_spread,
        ticker_filter=ticker_filter,
        verbose=args.verbose,
        live_games_only=live_games_only,
        min_recent_volume=args.min_volume,
    )

    if not tickers:
        logger.error(
            "No qualifying markets found. Try --min-spread 1, "
            "different --sport, or --ticker"
        )
        return 1

    # Build config
    config = EdgeCaptureConfig(
        min_edge_cents=args.min_edge,
        min_confidence=args.min_confidence,
        entry_timeout_seconds=args.entry_timeout,
        max_entry_size=args.max_size,
        entry_aggressiveness=args.aggressiveness,
        exit_mode=args.exit_mode,
        stop_loss_cents=args.stop_loss,
        edge_reversal_threshold_cents=args.edge_reversal_threshold,
        take_profit_cents=args.take_profit,
        max_hold_time_seconds=args.max_hold_time,
        use_kelly_sizing=not args.no_kelly,
        kelly_fraction=args.kelly_fraction,
        bankroll_override=args.bankroll,
        max_concurrent_positions=args.max_concurrent,
        max_daily_loss_dollars=args.max_daily_loss,
        min_volume_24h=args.min_volume,
        min_spread_cents=args.min_spread,
        max_spread_cents=args.max_spread,
        min_score_change_to_trade=args.min_score_change,
        buy_yes_only=args.buy_yes_only,
        min_fair_value_for_entry=args.min_fv,
        live_games_only=live_games_only,
        allowed_ticker_prefixes=None,  # Allow all discovered tickers
    )
    config.validate()

    # Run
    asyncio.run(
        run_strategy(
            tickers=tickers,
            config=config,
            provider=provider,
            dry_run=dry_run,
            use_websocket=args.use_websocket,
            poll_interval=args.poll_interval,
            passive_fill_rate=args.passive_fill_rate,
            sport=args.sport,
            score_feed_interval=args.score_feed_interval,
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
