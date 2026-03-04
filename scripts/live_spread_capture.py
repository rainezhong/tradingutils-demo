#!/usr/bin/env python3
"""
Live Spread Capture Strategy - CLI Runner

Buy at the bid, sell at the ask on low-volume Kalshi prediction markets
with wide spreads. Sequential round-trip with stuck inventory management.

Usage:
    python scripts/live_spread_capture.py                     # Dry run (default)
    python scripts/live_spread_capture.py --live              # Live trading (REAL MONEY)
    python scripts/live_spread_capture.py --sport ncaab       # NCAA Basketball
    python scripts/live_spread_capture.py --min-spread 8 -v   # Custom spread, verbose
    python scripts/live_spread_capture.py --ticker KXNCAABGAME-26FEB03DUKEUNC-DUKE
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import List, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.spread_capture_strategy import (
    SpreadCaptureConfig,
    SpreadCaptureStrategy,
)

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
    "nhl": "KXNHLGAME",
    "ucl": "KXUCL",
    "tennis": "KXWTA",
    "soccer": "KXSOCCER",
}

# Sports that should always include their totals + spread companions
SPORT_COMPANIONS = {
    "nba": ["nba_totals", "nba_spread"],
    "ncaab": ["ncaab_totals", "ncaab_spread"],
}

# Sport-specific config profiles based on historical analysis
# Override these defaults when trading specific sports
SPORT_CONFIGS = {
    "ncaab": {
        # NCAAB: Best performer. 784 trades, 45% win rate, +$790
        # Sweet spot: 16-24c spreads, 30s-2m hold times
        "min_spread_cents": 15,
        "max_spread_cents": 30,
        "exit_timeout_seconds": 120.0,  # Target 30s-2m exits
        "max_entry_size": 15,
    },
    "ncaab_totals": {
        # Totals markets tend to have wider spreads
        "min_spread_cents": 10,
        "max_spread_cents": 30,
        "exit_timeout_seconds": 120.0,
        "max_entry_size": 15,
    },
    "nba": {
        # NBA: Tighter spreads, more volume, faster action
        # Only 11 trades historically but 72% win rate
        "min_spread_cents": 8,
        "max_spread_cents": 20,
        "exit_timeout_seconds": 90.0,  # Faster markets
        "max_entry_size": 10,
        "stuck_improvement_interval_seconds": 10.0,  # More aggressive
    },
    "nba_totals": {
        "min_spread_cents": 8,
        "max_spread_cents": 25,
        "exit_timeout_seconds": 90.0,
        "max_entry_size": 10,
    },
    "nba_spread": {
        # NBA wins-by-X: many strike levels per game, often wide spreads
        "min_spread_cents": 8,
        "max_spread_cents": 25,
        "exit_timeout_seconds": 90.0,
        "max_entry_size": 10,
    },
    "ncaab_spread": {
        # NCAAB wins-by-X: similar to NCAAB totals
        "min_spread_cents": 10,
        "max_spread_cents": 30,
        "exit_timeout_seconds": 120.0,
        "max_entry_size": 15,
    },
    "nhl": {
        # NHL: Similar to NBA
        "min_spread_cents": 8,
        "max_spread_cents": 25,
        "exit_timeout_seconds": 90.0,
        "max_entry_size": 10,
    },
    "default": {
        # Conservative defaults for unknown sports
        "min_spread_cents": 12,
        "max_spread_cents": 30,
        "exit_timeout_seconds": 120.0,
        "max_entry_size": 10,
    },
}


# =============================================================================
# Market Discovery
# =============================================================================


def parse_game_date(ticker: str) -> Optional[datetime]:
    """Parse game date from event ticker like KXNCAAMBGAME-26FEB05TEAMS.

    Returns datetime if parseable, None otherwise.
    """
    import re

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
    """Check if game has started based on ticker date.

    Returns True if game date is today or earlier (game has started/finished).
    """
    game_date = parse_game_date(ticker)
    if game_date is None:
        return True  # Can't parse, allow it

    today = datetime.utcnow().date()
    return game_date.date() <= today


def discover_markets(
    sport: str,
    min_spread_cents: int = 5,
    max_spread_cents: int = 30,
    ticker_filter: Optional[Set[str]] = None,
    verbose: bool = False,
    live_games_only: bool = True,
    min_recent_volume: int = 50,
    min_time_to_close_minutes: int = 1,
) -> List[str]:
    """Discover open markets from Kalshi REST API.

    Args:
        sport: Sport key (nba, ncaab, etc.)
        min_spread_cents: Minimum spread to consider
        max_spread_cents: Maximum spread to consider
        live_games_only: Only return games that have started (based on ticker date)
        min_recent_volume: Minimum volume_24h required (proxy for recent activity)
        min_time_to_close_minutes: Skip markets closing within this many minutes
        ticker_filter: If set, only return these tickers
        verbose: Print discovery details

    Returns:
        List of ticker strings meeting criteria.
    """
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

    # If specific tickers provided, just validate them
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
            time.sleep(0.2)  # Rate limit
        return valid_tickers

    # Discover markets by event prefix
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

    # Collect all markets from events
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
            mid = (yes_bid + yes_ask) / 2

            if spread < min_spread_cents or spread > max_spread_cents:
                continue

            # Live games only filter - skip future games
            if live_games_only and not is_game_live(event_ticker):
                if verbose:
                    game_date = parse_game_date(event_ticker)
                    logger.debug(
                        f"  {ticker}: SKIPPED (game not started, date={game_date.date() if game_date else 'unknown'})"
                    )
                continue

            # Min recent volume filter
            volume_24h = market.get("volume_24h", 0) or 0
            if min_recent_volume > 0 and volume_24h < min_recent_volume:
                if verbose:
                    logger.debug(
                        f"  {ticker}: SKIPPED (volume={volume_24h} < {min_recent_volume})"
                    )
                continue

            # Min time to close filter - skip markets closing soon
            if min_time_to_close_minutes > 0:
                exp_time_str = market.get("expected_expiration_time") or market.get(
                    "close_time"
                )
                if exp_time_str:
                    from datetime import timezone

                    try:
                        exp_time = datetime.fromisoformat(
                            exp_time_str.replace("Z", "+00:00")
                        )
                        now_utc = datetime.now(timezone.utc)
                        minutes_remaining = (exp_time - now_utc).total_seconds() / 60
                        if minutes_remaining < min_time_to_close_minutes:
                            if verbose:
                                logger.debug(
                                    f"  {ticker}: SKIPPED (only {minutes_remaining:.1f}m remaining)"
                                )
                            continue
                    except (ValueError, TypeError):
                        pass  # Can't parse, allow it

            if verbose:
                logger.info(
                    f"  {ticker}: bid={yes_bid}c ask={yes_ask}c "
                    f"spread={spread}c mid={mid:.1f}c vol={volume_24h}"
                )

            qualifying_tickers.append(ticker)

    logger.info(f"Found {len(qualifying_tickers)} qualifying markets")
    return qualifying_tickers


# =============================================================================
# Main
# =============================================================================


async def run_strategy(
    tickers: List[str],
    config: SpreadCaptureConfig,
    dry_run: bool = True,
    use_websocket: bool = False,
    poll_interval: float = 5.0,
    passive_fill_rate: float = 0.025,
    use_risk_manager: bool = False,
    use_capital_manager: bool = False,
    use_correlation_limits: bool = False,
) -> None:
    """Run the spread capture strategy."""

    # --- Instantiate optional infrastructure modules ---
    risk_manager = None
    capital_manager = None
    correlation_tracker = None

    if use_risk_manager:
        from src.core.config import RiskConfig
        from src.risk.risk_manager import RiskManager

        risk_config = RiskConfig(
            max_position_size=config.max_entry_size,
            max_total_position=config.max_concurrent_positions * config.max_entry_size,
            max_loss_per_position=config.max_loss_per_trade_dollars,
            max_daily_loss=config.max_daily_loss_dollars,
        )
        risk_manager = RiskManager(risk_config)
        logger.info(
            "RiskManager enabled: max_daily_loss=$%.2f, max_position=%d",
            risk_config.max_daily_loss,
            risk_config.max_position_size,
        )

    if use_correlation_limits or (use_risk_manager and use_correlation_limits):
        from src.risk.correlation_limits import (
            CorrelatedExposureTracker,
            CorrelationLimitConfig,
        )

        corr_config = CorrelationLimitConfig(
            correlated_categories=[
                "KXNCAAMBGAME",
                "KXNCAAMBTOTAL",
                "KXNCAAMBSPREAD",
                "KXNBAGAME",
                "KXNBATOTAL",
                "KXNBASPREAD",
                "KXNHLGAME",
            ],
        )
        correlation_tracker = CorrelatedExposureTracker(corr_config)
        logger.info("CorrelatedExposureTracker enabled")

        # Wire into RiskManager if both are active
        if risk_manager:
            risk_manager.set_correlation_tracker(correlation_tracker)

    if use_capital_manager:
        from src.oms.capital_manager import CapitalManager

        capital_manager = CapitalManager()
        # Sync initial balance from Kalshi API in live mode
        if not dry_run:
            try:
                import requests
                from src.kalshi.auth import KalshiAuth

                auth = KalshiAuth.from_env()
                host = "https://api.elections.kalshi.com"
                path = "/trade-api/v2/portfolio/balance"
                headers = auth.sign_request("GET", path, "")
                headers["Content-Type"] = "application/json"
                resp = requests.get(f"{host}{path}", headers=headers, timeout=10)
                if resp.status_code == 200:
                    balance_cents = resp.json().get("balance", 0)
                    balance = balance_cents / 100.0
                    capital_manager.set_exchange_balance("kalshi", balance)
                    logger.info(
                        "CapitalManager enabled: initial balance=$%.2f", balance
                    )
                else:
                    logger.warning(
                        "Failed to fetch balance for CapitalManager: HTTP %d",
                        resp.status_code,
                    )
            except Exception as e:
                logger.warning("Error syncing balance for CapitalManager: %s", e)
        else:
            # In dry run, set a default balance
            capital_manager.set_exchange_balance("kalshi", 10000.0)
            logger.info("CapitalManager enabled (dry-run): balance=$10000.00")

    strategy = SpreadCaptureStrategy(
        config=config,
        dry_run=dry_run,
        log_dir="data/spread_capture",
        use_polling=not use_websocket,
        poll_interval=poll_interval,
        passive_fill_rate=passive_fill_rate,
        risk_manager=risk_manager,
        capital_manager=capital_manager,
        correlation_tracker=correlation_tracker,
    )

    # Signal handler for graceful shutdown
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
    logger.info(f"SPREAD CAPTURE STRATEGY ({mode_str})")
    logger.info("=" * 60)
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Spread range: {config.min_spread_cents}-{config.max_spread_cents}c")
    logger.info(f"Entry size: {config.min_entry_size}-{config.max_entry_size}")
    logger.info(f"Max concurrent: {config.max_concurrent_positions}")
    logger.info(f"Max daily loss: ${config.max_daily_loss_dollars:.2f}")
    logger.info(f"Entry timeout: {config.entry_timeout_seconds:.0f}s")
    if config.use_combined_filter:
        logger.info(
            f"Activity filter: COMBINED (min={config.min_activity_score:.2f}, "
            f"vol_wt={config.volume_weight:.1f}, mov_wt={config.movement_weight:.1f})"
        )
    else:
        vol_filter = f"min={config.min_volume_24h}"
        if config.max_volume_24h is not None:
            vol_filter += f", max={config.max_volume_24h}"
        logger.info(f"Volume filter: {vol_filter}")
        if config.min_movement_score > 0:
            logger.info(f"Movement filter: min_score={config.min_movement_score:.2f}")
    logger.info(
        f"Feed: {'WebSocket' if use_websocket else f'Polling ({poll_interval}s)'}"
    )
    if config.use_dynamic_pricing:
        logger.info(
            f"Dynamic pricing: ENABLED (reprice={config.reprice_interval_seconds}s, "
            f"max_improve={config.max_bid_improvement_cents}c, "
            f"min_edge={config.min_expected_edge_cents}c)"
        )
    if config.prefer_high_prob:
        logger.info(
            f"High-prob filter: ENABLED (mid > {config.high_prob_min_mid_cents:.0f}c)"
        )
    if config.allowed_ticker_prefixes:
        logger.info(f"Ticker filter: {config.allowed_ticker_prefixes}")
    else:
        logger.info("Ticker filter: DISABLED (all markets)")
    if config.live_games_only:
        logger.info("Live games filter: ENABLED (only games that have started)")
    if config.min_time_to_close_minutes > 0:
        logger.info(
            f"Time to close filter: ENABLED (min {config.min_time_to_close_minutes}m before close)"
        )
    if config.require_price_movement:
        logger.info(
            f"Price movement filter: ENABLED (window={config.price_movement_window_seconds}s, "
            f"min_change={config.min_price_change_cents}c)"
        )
    if config.use_taker_exit:
        logger.info(
            f"Taker exit: ENABLED (min_profit={config.taker_exit_min_profit_cents}c)"
        )
    if config.enable_alerts:
        logger.info(f"Alerts: ENABLED (log={config.alerts_log_file})")
    if risk_manager:
        logger.info("Risk manager: ENABLED")
    if capital_manager:
        logger.info("Capital manager: ENABLED")
    if correlation_tracker:
        logger.info("Correlation limits: ENABLED")
    logger.info("=" * 60)

    if len(tickers) <= 10:
        for t in tickers:
            logger.info(f"  → {t}")

    logger.info("\nStarting... (Ctrl+C to stop)\n")

    # Run strategy and wait for stop signal
    strategy_task = asyncio.create_task(strategy.start(tickers))

    # Periodic status printing
    async def status_printer():
        while not stop_event.is_set():
            await asyncio.sleep(60)
            if not stop_event.is_set():
                strategy.print_status()

    status_task = asyncio.create_task(status_printer())

    # Wait for stop signal
    await stop_event.wait()

    # Graceful shutdown
    logger.info("Stopping strategy...")
    await strategy.stop()
    strategy_task.cancel()
    status_task.cancel()

    try:
        await strategy_task
    except asyncio.CancelledError:
        pass
    try:
        await status_task
    except asyncio.CancelledError:
        pass

    # Final status
    strategy.print_status()
    logger.info("Shutdown complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Spread Capture Strategy - Buy at bid, sell at ask on wide-spread markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/live_spread_capture.py                     # Dry run, auto-discover
  python scripts/live_spread_capture.py --sport ncaab -v    # NCAA Basketball, verbose
  python scripts/live_spread_capture.py --min-spread 8      # Wider spread threshold
  python scripts/live_spread_capture.py --ticker TICK1,TICK2 # Specific tickers
  python scripts/live_spread_capture.py --live              # Live trading (REAL MONEY)
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

    # Market selection
    parser.add_argument(
        "--sport",
        type=str,
        default="ncaab",
        help="Sport(s) to trade, comma-separated (default: ncaab). Options: "
        + ", ".join(SPORT_PREFIXES.keys()),
    )
    parser.add_argument(
        "--use-sport-profile",
        action="store_true",
        default=True,
        help="Use sport-specific optimized parameters (default: enabled)",
    )
    parser.add_argument(
        "--no-sport-profile",
        action="store_false",
        dest="use_sport_profile",
        help="Disable sport-specific parameters, use manual settings",
    )
    parser.add_argument(
        "--ticker", type=str, default=None, help="Specific ticker(s), comma-separated"
    )

    # Spread parameters
    parser.add_argument(
        "--min-spread",
        type=int,
        default=10,
        help="Minimum spread in cents (default: 10)",
    )
    parser.add_argument(
        "--max-spread",
        type=int,
        default=30,
        help="Maximum spread in cents (default: 30)",
    )

    # Size parameters
    parser.add_argument(
        "--max-size",
        type=int,
        default=15,
        help="Maximum entry size in contracts (default: 15)",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=1,
        help="Minimum entry size in contracts (default: 1)",
    )

    # Entry parameters
    parser.add_argument(
        "--entry-timeout",
        type=float,
        default=300.0,
        help="Entry timeout in seconds (default: 300). How long to wait for a fill before cancelling.",
    )

    # Risk parameters
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=50.0,
        help="Maximum daily loss in USD (default: 50.0)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=10,
        help="Maximum concurrent positions (default: 10)",
    )

    # Volume filters
    parser.add_argument(
        "--min-volume",
        type=int,
        default=1,
        help="Minimum 24h volume to consider (default: 1, set 0 to disable)",
    )
    parser.add_argument(
        "--max-volume",
        type=int,
        default=None,
        help="Maximum 24h volume to consider (default: no limit). Use to target low-liquidity markets.",
    )
    parser.add_argument(
        "--min-movement",
        type=float,
        default=0.0,
        help="Minimum movement likelihood score 0-1 (default: 0). Higher = only trade active markets.",
    )
    # Combined activity filter
    parser.add_argument(
        "--combined-filter",
        action="store_true",
        help="Use combined volume+movement scoring (replaces separate filters)",
    )
    parser.add_argument(
        "--min-activity",
        type=float,
        default=0.5,
        help="Minimum combined activity score 0-1 (default: 0.5). Only used with --combined-filter.",
    )
    parser.add_argument(
        "--volume-weight",
        type=float,
        default=0.6,
        help="Weight for volume in combined score (default: 0.6). Only used with --combined-filter.",
    )
    parser.add_argument(
        "--movement-weight",
        type=float,
        default=0.4,
        help="Weight for movement in combined score (default: 0.4). Only used with --combined-filter.",
    )
    # High probability filter (safer if stuck at settlement)
    parser.add_argument(
        "--prefer-high-prob",
        action="store_true",
        help="Only trade high-probability markets (mid > 65c). Safer if stuck at settlement.",
    )
    parser.add_argument(
        "--high-prob-min",
        type=float,
        default=65.0,
        help="Min mid price for high-prob filter (default: 65c). Only used with --prefer-high-prob.",
    )
    # Live games only filter (enabled by default)
    parser.add_argument(
        "--live-games-only",
        action="store_true",
        default=True,
        help="Only trade games that have started (default: enabled). Use --include-pregame to disable.",
    )
    parser.add_argument(
        "--include-pregame",
        action="store_true",
        help="Include pre-game markets (disables live-games-only filter)",
    )
    # Min recent volume filter
    parser.add_argument(
        "--min-recent-volume",
        type=int,
        default=50,
        help="Minimum volume in last hour to consider (default: 50). Set 0 to disable. Filters inactive markets.",
    )
    # Min time to close filter
    parser.add_argument(
        "--min-time-to-close",
        type=int,
        default=1,
        help="Minimum minutes before market close to trade (default: 1). Prevents last-minute trades.",
    )

    # Feed parameters
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Poll interval in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--use-websocket", action="store_true", help="Use WebSocket instead of polling"
    )

    # Dry run fill simulation
    parser.add_argument(
        "--passive-fill-rate",
        type=float,
        default=0.025,
        help="Passive fill hazard rate per second in dry run (default: 0.025, ~78%% fill by 60s)",
    )

    # Kelly sizing
    parser.add_argument(
        "--kelly",
        action="store_true",
        help="Use Kelly Criterion for position sizing based on bankroll",
    )
    parser.add_argument(
        "--kelly-fraction",
        type=float,
        default=0.5,
        help="Kelly fraction (0.5 = half-Kelly for safety, default: 0.5)",
    )
    parser.add_argument(
        "--kelly-win-prob",
        type=float,
        default=0.70,
        help="Estimated win probability for Kelly (default: 0.70)",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Manual bankroll override in dollars (default: fetch from API)",
    )

    # Dynamic pricing
    parser.add_argument(
        "--dynamic-pricing",
        action="store_true",
        default=True,
        help="Enable dynamic pricing based on orderbook state (default: enabled)",
    )
    parser.add_argument(
        "--no-dynamic-pricing",
        action="store_false",
        dest="dynamic_pricing",
        help="Disable dynamic pricing",
    )
    parser.add_argument(
        "--reprice-interval",
        type=float,
        default=5.0,
        help="How often to recalculate prices in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--max-improvement",
        type=int,
        default=3,
        help="Maximum bid improvement / ask discount in cents (default: 3)",
    )
    parser.add_argument(
        "--min-edge",
        type=int,
        default=3,
        help="Minimum expected edge in cents (default: 3)",
    )
    parser.add_argument(
        "--imbalance-weight",
        type=float,
        default=0.5,
        help="Weight for imbalance signal (default: 0.5)",
    )
    parser.add_argument(
        "--microprice-weight",
        type=float,
        default=0.3,
        help="Weight for microprice signal (default: 0.3)",
    )

    # Price movement filter
    parser.add_argument(
        "--require-price-movement",
        action="store_true",
        default=True,
        help="Only enter if price moved recently (default: enabled)",
    )
    parser.add_argument(
        "--no-require-price-movement",
        action="store_false",
        dest="require_price_movement",
        help="Disable price movement requirement",
    )
    parser.add_argument(
        "--price-movement-window",
        type=float,
        default=60.0,
        help="Window in seconds to look for price movement (default: 60)",
    )
    parser.add_argument(
        "--min-price-change",
        type=int,
        default=1,
        help="Minimum price change in cents to count as movement (default: 1)",
    )

    # Taker exit
    parser.add_argument(
        "--taker-exit",
        action="store_true",
        default=True,
        help="Enable taker exit when profitable (default: enabled)",
    )
    parser.add_argument(
        "--no-taker-exit",
        action="store_false",
        dest="taker_exit",
        help="Disable taker exit",
    )
    parser.add_argument(
        "--taker-exit-min-profit",
        type=int,
        default=5,
        help="Minimum profit in cents to trigger taker exit (default: 5)",
    )

    # Alerts
    parser.add_argument(
        "--alerts",
        action="store_true",
        default=True,
        help="Enable desktop/log alerts (default: enabled)",
    )
    parser.add_argument(
        "--no-alerts", action="store_false", dest="alerts", help="Disable all alerts"
    )

    # Loss cutting
    parser.add_argument(
        "--max-hold-time",
        type=float,
        default=180.0,
        help="Maximum hold time in seconds before force exit (default: 180)",
    )
    parser.add_argument(
        "--max-loss-cents",
        type=int,
        default=10,
        help="Maximum loss per position in cents before cutting (default: 10)",
    )

    # Ticker prefix restriction
    parser.add_argument(
        "--allowed-prefixes",
        type=str,
        default=None,
        help='Comma-separated ticker prefixes to allow (default: KXNCAAMBGAME). Use "all" to disable filter.',
    )

    # Infrastructure modules
    parser.add_argument(
        "--use-risk-manager",
        action="store_true",
        help="Enable RiskManager integration (drawdown tracking, portfolio limits)",
    )
    parser.add_argument(
        "--use-capital-manager",
        action="store_true",
        help="Enable CapitalManager (capital reservation, prevents double-spending)",
    )
    parser.add_argument(
        "--use-correlation-limits",
        action="store_true",
        help="Enable event/category exposure limits via CorrelatedExposureTracker",
    )

    # Misc
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set logging level
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

    # Discover markets (support comma-separated sports)
    tickers = []
    sports = [s.strip() for s in args.sport.split(",")]

    # Auto-include totals + spread companions (e.g. nba -> nba + nba_totals + nba_spread)
    expanded = []
    for sport in sports:
        expanded.append(sport)
        companions = SPORT_COMPANIONS.get(sport, [])
        for companion in companions:
            if companion not in sports and companion not in expanded:
                expanded.append(companion)
    sports = expanded

    # --include-pregame overrides --live-games-only
    effective_live_games_only = args.live_games_only and not args.include_pregame

    for sport in sports:
        if sport not in SPORT_PREFIXES and not ticker_filter:
            logger.warning(
                f"Unknown sport: {sport}. Available: {list(SPORT_PREFIXES.keys())}"
            )
            continue
        found = discover_markets(
            sport=sport,
            min_spread_cents=args.min_spread,
            max_spread_cents=args.max_spread,
            ticker_filter=ticker_filter,
            verbose=args.verbose,
            live_games_only=effective_live_games_only,
            min_recent_volume=args.min_recent_volume,
            min_time_to_close_minutes=args.min_time_to_close,
        )
        tickers.extend(found)
    tickers = list(set(tickers))  # dedupe

    if not tickers:
        logger.error(
            "No qualifying markets found. Try --min-spread 3 or different --sport"
        )
        return 1

    # Apply sport-specific profile if enabled
    # When multiple sports (e.g. ncaab + ncaab_totals), merge profiles using
    # the widest spread range and largest sizes across all sports
    sport_profile = {}
    if args.use_sport_profile:
        profiles = [SPORT_CONFIGS.get(s, {}) for s in sports]
        profiles = [p for p in profiles if p]
        if profiles:
            sport_profile = dict(profiles[0])
            for p in profiles[1:]:
                for key, val in p.items():
                    if key.startswith("min_") and key in sport_profile:
                        sport_profile[key] = min(sport_profile[key], val)
                    elif key.startswith("max_") and key in sport_profile:
                        sport_profile[key] = max(sport_profile[key], val)
                    elif key not in sport_profile:
                        sport_profile[key] = val
            logger.info(f"Using sport profile for: {', '.join(sports)}")

    # Build config (sport profile provides defaults, CLI args override)
    config = SpreadCaptureConfig(
        min_spread_cents=args.min_spread
        if args.min_spread != 10
        else sport_profile.get("min_spread_cents", args.min_spread),
        max_spread_cents=args.max_spread
        if args.max_spread != 30
        else sport_profile.get("max_spread_cents", args.max_spread),
        min_entry_size=args.min_size,
        max_entry_size=args.max_size
        if args.max_size != 15
        else sport_profile.get("max_entry_size", args.max_size),
        max_daily_loss_dollars=args.max_daily_loss,
        max_concurrent_positions=args.max_concurrent,
        scan_interval_seconds=args.poll_interval,
        min_volume_24h=args.min_volume,
        max_volume_24h=args.max_volume,
        min_movement_score=args.min_movement,
        # Combined activity filter
        use_combined_filter=args.combined_filter,
        min_activity_score=args.min_activity,
        volume_weight=args.volume_weight,
        movement_weight=args.movement_weight,
        # High probability filter (safer if stuck)
        prefer_high_prob=args.prefer_high_prob,
        high_prob_min_mid_cents=args.high_prob_min,
        # Live games only filter
        live_games_only=effective_live_games_only,
        # Entry timeout
        entry_timeout_seconds=args.entry_timeout,
        # Min time to close filter
        min_time_to_close_minutes=args.min_time_to_close,
        # Kelly sizing
        use_kelly_sizing=args.kelly,
        kelly_fraction=args.kelly_fraction,
        kelly_win_prob=args.kelly_win_prob,
        bankroll_override=args.bankroll,
        # Dynamic pricing
        use_dynamic_pricing=args.dynamic_pricing,
        reprice_interval_seconds=args.reprice_interval,
        max_bid_improvement_cents=args.max_improvement,
        max_ask_discount_cents=args.max_improvement,
        min_expected_edge_cents=args.min_edge,
        imbalance_weight=args.imbalance_weight,
        microprice_weight=args.microprice_weight,
        # Price movement filter
        require_price_movement=args.require_price_movement,
        price_movement_window_seconds=args.price_movement_window,
        min_price_change_cents=args.min_price_change,
        # Taker exit
        use_taker_exit=args.taker_exit,
        taker_exit_min_profit_cents=args.taker_exit_min_profit,
        # Alerts
        enable_alerts=args.alerts,
        # Loss cutting
        max_hold_time_seconds=args.max_hold_time,
        max_loss_per_position_cents=args.max_loss_cents,
        # Ticker prefix restriction (backtest-validated markets only)
        allowed_ticker_prefixes=(
            None
            if args.allowed_prefixes == "all"
            else [p.strip() for p in args.allowed_prefixes.split(",")]
            if args.allowed_prefixes
            else [
                "KXNBAGAME",
                "KXNBATOTAL",
                "KXNBASPREAD",
                "KXNCAAMBGAME",
                "KXNCAAMBTOTAL",
                "KXNCAAMBSPREAD",
            ]
        ),
    )
    config.validate()

    # Run
    asyncio.run(
        run_strategy(
            tickers=tickers,
            config=config,
            dry_run=dry_run,
            use_websocket=args.use_websocket,
            poll_interval=args.poll_interval,
            passive_fill_rate=args.passive_fill_rate,
            use_risk_manager=args.use_risk_manager,
            use_capital_manager=args.use_capital_manager,
            use_correlation_limits=args.use_correlation_limits,
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
