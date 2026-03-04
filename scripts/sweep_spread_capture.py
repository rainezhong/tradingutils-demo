#!/usr/bin/env python3
"""
Sweep Spread Capture Parameters — run multiple configs against the same
live orderbook feed and compare results side-by-side.

One shared REST poller fetches orderbooks; snapshots are copied to N
independent strategy instances each cycle.  All strategies are dry-run
with identical passive_fill_rate so fill timing is comparable.

Usage:
    python scripts/sweep_spread_capture.py                       # default 5-min run
    python scripts/sweep_spread_capture.py --duration 600        # 10-min run
    python scripts/sweep_spread_capture.py --sport nba           # NBA markets
    python scripts/sweep_spread_capture.py --passive-fill-rate 0.05  # faster fills
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.orderbook_manager import OrderBookManager
from src.kalshi.auth import KalshiAuth
from strategies.spread_capture_strategy import (
    SpreadCaptureConfig,
    SpreadCaptureState,
    SpreadCaptureStrategy,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =========================================================================
# Config Definitions
# =========================================================================

CONFIGS: Dict[str, dict] = {
    "baseline": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "tight_spreads": dict(
        min_spread_cents=3,
        max_spread_cents=10,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "wide_spreads": dict(
        min_spread_cents=10,
        max_spread_cents=30,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "bid_improve_1": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=1,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "bid_improve_2": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=2,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "patient": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=120,
        exit_timeout_seconds=240,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "small_size": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=10,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=10,
    ),
    "no_cooldown": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=3,
        cooldown_between_trades_seconds=0,
    ),
    "low_depth_ok": dict(
        min_spread_cents=5,
        max_spread_cents=30,
        bid_improvement_cents=0,
        ask_discount_cents=0,
        entry_timeout_seconds=60,
        exit_timeout_seconds=120,
        max_entry_size=25,
        min_depth_at_best=1,
        cooldown_between_trades_seconds=10,
    ),
    "aggressive": dict(
        min_spread_cents=3,
        max_spread_cents=30,
        bid_improvement_cents=2,
        ask_discount_cents=1,
        entry_timeout_seconds=90,
        exit_timeout_seconds=180,
        max_entry_size=25,
        min_depth_at_best=1,
        cooldown_between_trades_seconds=5,
    ),
}


# =========================================================================
# Market Discovery (same as live_spread_capture.py)
# =========================================================================

SPORT_PREFIXES = {
    "nba": "KXNBAGAME",
    "ncaab": "KXNCAAMBGAME",
    "nhl": "KXNHLGAME",
}


def discover_markets(sport: str) -> List[str]:
    """Discover all open markets for a sport (wide filter — each config
    applies its own spread filter via analyze_opportunity)."""
    import requests

    auth = KalshiAuth.from_env()
    host = "https://api.elections.kalshi.com"
    prefix = SPORT_PREFIXES.get(sport, "")
    if not prefix:
        logger.error(
            f"Unknown sport: {sport}. Available: {list(SPORT_PREFIXES.keys())}"
        )
        return []

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
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return []

    tickers = []
    for event in events:
        for market in event.get("markets", []):
            ticker = market.get("ticker", "")
            status = market.get("status", "")
            if status not in ("open", "active"):
                continue
            yes_bid = market.get("yes_bid", 0)
            yes_ask = market.get("yes_ask", 0)
            if yes_bid and yes_ask and (yes_ask - yes_bid) >= 3:
                tickers.append(ticker)

    logger.info(f"Found {len(tickers)} markets with spread >= 3c")
    return tickers


# =========================================================================
# Shared Orderbook Fetcher
# =========================================================================


class SharedFetcher:
    """Fetches orderbooks via REST and distributes to strategy instances."""

    def __init__(self, tickers: List[str]):
        self.tickers = tickers
        self._auth = KalshiAuth.from_env()
        # Master orderbook manager — strategies copy from here
        self._master_mgr = OrderBookManager()

    async def poll_once(self) -> int:
        """Fetch all orderbooks. Returns number successfully fetched."""
        import requests
        from src.core.orderbook_manager import OrderBookLevel, OrderBookState
        from src.core.utils import utc_now

        host = "https://api.elections.kalshi.com"
        fetched = 0

        for ticker in self.tickers:
            try:
                # Fetch /markets/{ticker}
                market_path = f"/trade-api/v2/markets/{ticker}"
                headers = self._auth.sign_request("GET", market_path, "")
                headers["Content-Type"] = "application/json"
                market_resp = requests.get(
                    f"{host}{market_path}", headers=headers, timeout=10
                )
                if market_resp.status_code != 200:
                    continue

                md = market_resp.json().get("market", {})
                yes_bid = md.get("yes_bid")
                yes_ask = md.get("yes_ask")
                if not yes_bid or not yes_ask:
                    continue

                # Fetch /markets/{ticker}/orderbook
                ob_path = f"/trade-api/v2/markets/{ticker}/orderbook"
                headers = self._auth.sign_request("GET", ob_path, "")
                headers["Content-Type"] = "application/json"
                ob_resp = requests.get(f"{host}{ob_path}", headers=headers, timeout=10)
                ob_data = {}
                if ob_resp.status_code == 200:
                    ob_data = ob_resp.json().get("orderbook", {})

                # Parse bids
                raw_bids = []
                for level in ob_data.get("yes", []):
                    if len(level) >= 2:
                        price, size = level[0], level[1]
                        if 1 <= price <= 99 and size > 0:
                            raw_bids.append((price, size))

                raw_asks = []
                for level in ob_data.get("no", []):
                    if len(level) >= 2:
                        no_price, size = level[0], level[1]
                        ask_price = 100 - no_price
                        if 1 <= ask_price <= 99 and size > 0:
                            raw_asks.append((ask_price, size))

                # Build best bid/ask with depth
                best_bid_depth = None
                for price, size in raw_bids:
                    if price >= yes_bid - 2 and price <= yes_bid:
                        if best_bid_depth is None or price > best_bid_depth[0]:
                            best_bid_depth = (price, size)

                best_ask_depth = None
                for price, size in raw_asks:
                    if price <= yes_ask + 2 and price >= yes_ask:
                        if best_ask_depth is None or price < best_ask_depth[0]:
                            best_ask_depth = (price, size)

                bids = []
                if best_bid_depth:
                    bids.append(
                        OrderBookLevel(price=best_bid_depth[0], size=best_bid_depth[1])
                    )
                else:
                    bids.append(OrderBookLevel(price=yes_bid, size=1))

                for price, size in sorted(raw_bids, key=lambda x: -x[0]):
                    if price < yes_bid - 2:
                        bids.append(OrderBookLevel(price=price, size=size))

                asks = []
                if best_ask_depth:
                    asks.append(
                        OrderBookLevel(price=best_ask_depth[0], size=best_ask_depth[1])
                    )
                else:
                    asks.append(OrderBookLevel(price=yes_ask, size=1))

                for price, size in sorted(raw_asks, key=lambda x: x[0]):
                    if price > yes_ask + 2:
                        asks.append(OrderBookLevel(price=price, size=size))

                bids.sort(key=lambda x: x.price, reverse=True)
                asks.sort(key=lambda x: x.price)

                state = OrderBookState(
                    ticker=ticker,
                    bids=bids,
                    asks=asks,
                    sequence=0,
                    timestamp=utc_now(),
                )
                self._master_mgr._books[ticker] = state
                fetched += 1

            except Exception:
                continue

        return fetched

    def copy_books_to(self, strategy: SpreadCaptureStrategy) -> None:
        """Copy current orderbook snapshots into a strategy's manager."""
        for ticker, book in self._master_mgr._books.items():
            strategy._orderbook_mgr._books[ticker] = book


# =========================================================================
# Results Extraction
# =========================================================================


@dataclass
class RunResult:
    name: str
    trades_completed: int = 0
    trades_cancelled: int = 0
    entry_timeouts: int = 0
    wins: int = 0
    losses: int = 0
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    avg_spread_captured: float = 0.0
    avg_hold_time: float = 0.0
    avg_trade_size: float = 0.0
    orders_placed: int = 0
    fill_rate: float = 0.0
    pnl_per_trade: float = 0.0
    win_rate: float = 0.0


def extract_results(name: str, strategy: SpreadCaptureStrategy) -> RunResult:
    """Extract results from a strategy instance."""
    r = RunResult(name=name)

    completed = []
    for trade in strategy._trades.values():
        if trade.state == SpreadCaptureState.CLOSED:
            r.trades_completed += 1
            completed.append(trade)
        elif trade.state == SpreadCaptureState.CANCELLED:
            r.trades_cancelled += 1

    r.wins = strategy._session_wins
    r.losses = strategy._session_losses
    r.orders_placed = strategy._stats["orders_placed"]

    if completed:
        r.gross_pnl = sum(t.gross_pnl for t in completed)
        r.total_fees = sum(t.entry_fee + t.exit_fee for t in completed)
        r.net_pnl = sum(t.net_pnl for t in completed)
        spreads = [
            (t.exit_fill_price - t.entry_fill_price)
            for t in completed
            if t.exit_fill_price and t.entry_fill_price
        ]
        r.avg_spread_captured = sum(spreads) / len(spreads) if spreads else 0
        hold_times = [t.hold_time() for t in completed if t.entry_fill_time]
        r.avg_hold_time = sum(hold_times) / len(hold_times) if hold_times else 0
        sizes = [t.entry_fill_size for t in completed]
        r.avg_trade_size = sum(sizes) / len(sizes) if sizes else 0

    r.entry_timeouts = r.trades_cancelled
    r.fill_rate = (
        r.trades_completed / (r.trades_completed + r.trades_cancelled)
        if (r.trades_completed + r.trades_cancelled) > 0
        else 0
    )
    r.pnl_per_trade = r.net_pnl / r.trades_completed if r.trades_completed else 0
    r.win_rate = r.wins / (r.wins + r.losses) if (r.wins + r.losses) > 0 else 0

    return r


# =========================================================================
# Display
# =========================================================================


def print_results(results: List[RunResult], duration_s: float) -> None:
    """Print comparison table."""

    # Sort by net P&L descending
    results.sort(key=lambda r: r.net_pnl, reverse=True)

    hdr = (
        f"{'Config':<16} {'Trades':>6} {'Timeout':>7} {'Fill%':>6} "
        f"{'W/L':>7} {'Win%':>5} {'Gross$':>8} {'Fees$':>7} {'Net$':>8} "
        f"{'$/Trade':>8} {'AvgSprd':>7} {'AvgSz':>5} {'AvgHold':>7}"
    )
    print()
    print("=" * len(hdr))
    print(f"  PARAMETER SWEEP RESULTS  ({duration_s:.0f}s run)")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))

    for r in results:
        wl = f"{r.wins}/{r.losses}"
        print(
            f"{r.name:<16} {r.trades_completed:>6} {r.entry_timeouts:>7} "
            f"{r.fill_rate:>5.0%}  {wl:>7} {r.win_rate:>4.0%}  "
            f"{r.gross_pnl:>7.2f} {r.total_fees:>6.2f} {r.net_pnl:>7.2f}  "
            f"{r.pnl_per_trade:>7.4f} {r.avg_spread_captured:>6.1f}c "
            f"{r.avg_trade_size:>5.1f} {r.avg_hold_time:>6.1f}s"
        )

    print("-" * len(hdr))

    if results:
        best = results[0]
        print(f"\n  Best config: {best.name}  (net ${best.net_pnl:.2f})")

    print()


# =========================================================================
# Main Loop
# =========================================================================


async def run_sweep(
    tickers: List[str],
    configs: Dict[str, dict],
    duration_seconds: float,
    passive_fill_rate: float,
    poll_interval: float,
) -> List[RunResult]:
    """Run all configs concurrently against shared orderbook feed."""

    fetcher = SharedFetcher(tickers)

    # Create strategy instances
    strategies: Dict[str, SpreadCaptureStrategy] = {}
    for name, cfg_overrides in configs.items():
        cfg = SpreadCaptureConfig(**cfg_overrides)
        cfg.validate()
        strat = SpreadCaptureStrategy(
            config=cfg,
            dry_run=True,
            log_dir=f"data/sweep/{name}",
            use_polling=True,
            poll_interval=poll_interval,
            passive_fill_rate=passive_fill_rate,
        )
        strat._running = True
        strat._subscribed_tickers = set(tickers)
        strategies[name] = strat

    logger.info(f"Running {len(strategies)} configs for {duration_seconds:.0f}s")
    logger.info(f"Passive fill rate: {passive_fill_rate}/s")
    logger.info(f"Poll interval: {poll_interval}s")

    start = time.time()
    cycle = 0

    while time.time() - start < duration_seconds:
        cycle += 1
        elapsed = time.time() - start
        remaining = duration_seconds - elapsed

        # Fetch orderbooks (shared)
        n_fetched = await fetcher.poll_once()

        # Copy books to all strategies and run their analysis + fills
        for name, strat in strategies.items():
            fetcher.copy_books_to(strat)
            strat._check_dry_run_fills()
            # Trigger opportunity analysis
            for ticker in tickers:
                book = strat._orderbook_mgr.get_orderbook(ticker)
                if book:
                    strat._check_opportunity(ticker, book)

        # Status update
        {
            name: sum(1 for t in s._trades.values() if t.is_active())
            for name, s in strategies.items()
        }
        completed_counts = {name: s._session_trades for name, s in strategies.items()}
        logger.info(
            f"[Cycle {cycle}] {n_fetched}/{len(tickers)} books | "
            f"{remaining:.0f}s left | "
            f"completed: {completed_counts}"
        )

        # Wait before next cycle (but don't overshoot)
        wait = min(poll_interval, max(0, remaining - 0.1))
        if wait > 0:
            await asyncio.sleep(wait)

    # Stop all strategies (force-exit active trades)
    for strat in strategies.values():
        strat._running = False

    # Give pending fills a final chance
    for strat in strategies.values():
        strat._check_dry_run_fills()

    # Small delay to let any in-flight execute_opportunity tasks settle
    await asyncio.sleep(0.5)

    # Extract results
    results = []
    for name, strat in strategies.items():
        r = extract_results(name, strat)
        results.append(r)

    return results


# =========================================================================
# CLI
# =========================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Sweep spread capture parameters against live orderbook data",
    )
    parser.add_argument(
        "--sport",
        type=str,
        default="ncaab",
        choices=list(SPORT_PREFIXES.keys()),
        help="Sport to trade (default: ncaab)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=300,
        help="Run duration in seconds (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--passive-fill-rate",
        type=float,
        default=0.025,
        help="Passive fill hazard rate per second (default: 0.025)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between orderbook polls (default: 5.0)",
    )
    parser.add_argument(
        "--configs",
        type=str,
        default=None,
        help="Comma-separated config names to run (default: all)",
    )

    args = parser.parse_args()

    # Select configs
    if args.configs:
        selected = {k: v for k, v in CONFIGS.items() if k in args.configs.split(",")}
        if not selected:
            logger.error(f"No valid configs. Available: {list(CONFIGS.keys())}")
            return 1
    else:
        selected = CONFIGS

    # Discover markets
    tickers = discover_markets(args.sport)
    if not tickers:
        logger.error("No markets found")
        return 1

    # Limit tickers to keep polling fast enough
    if len(tickers) > 50:
        logger.info(f"Limiting to 50 tickers (from {len(tickers)}) for faster polling")
        tickers = tickers[:50]

    logger.info(f"Configs: {list(selected.keys())}")
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Duration: {args.duration:.0f}s")

    # Handle Ctrl+C
    loop = asyncio.new_event_loop()

    results = loop.run_until_complete(
        run_sweep(
            tickers=tickers,
            configs=selected,
            duration_seconds=args.duration,
            passive_fill_rate=args.passive_fill_rate,
            poll_interval=args.poll_interval,
        )
    )

    print_results(results, args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
