#!/usr/bin/env python3
"""
Spread Capture Parameter Optimizer

Fetches live orderbooks (or replays saved snapshots) and sweeps parameter
combinations through the spread capture analysis logic to find optimal settings.

Usage:
    # Live scan, full grid
    python scripts/backtest_spread_capture.py

    # Quick sweep on NCAAB markets
    python scripts/backtest_spread_capture.py --quick --sport ncaab

    # Save orderbooks for later replay
    python scripts/backtest_spread_capture.py --save-books data/depth_snapshots/today.jsonl

    # Replay saved snapshots
    python scripts/backtest_spread_capture.py --replay data/depth_snapshots/today.jsonl

    # CSV output
    python scripts/backtest_spread_capture.py --output results.csv -v

    # Custom grid
    python scripts/backtest_spread_capture.py --grid '{"min_spread_cents":[5,8],"max_spread_cents":[20,30]}'
"""

import argparse
import csv
import itertools
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.orderbook_manager import OrderBookLevel, OrderBookState
from market_data.client import KalshiPublicClient

# Fee calculation — imported from the canonical source.
from src.strategies.spread_capture import kalshi_fee


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class OpportunityResult:
    """Result of analyzing a single market for spread capture opportunity."""

    ticker: str
    entry_price: int
    exit_price: int
    spread: int
    mid_price: float
    bid_depth: int
    ask_depth: int
    entry_size: int
    gross_edge_per: float
    net_edge_per: float
    entry_fee_per: float
    exit_fee_per: float
    expected_net_profit: float
    fill_probability: float
    risk_adjusted_profit: float


@dataclass
class SweepResult:
    """Aggregated results for one parameter combination."""

    params: dict
    markets_scanned: int
    opportunities_found: int
    total_expected_profit: float
    risk_adjusted_profit: float
    avg_net_edge_per: float
    avg_spread: float
    avg_entry_size: float
    total_entry_fees: float
    total_exit_fees: float
    qualifying_tickers: List[str] = field(default_factory=list)
    details: List[OpportunityResult] = field(default_factory=list)


# =============================================================================
# Core Analysis Functions
# =============================================================================


DEFAULT_MAKER_RATE = 0.0175
DEFAULT_MIN_MID = 15.0
DEFAULT_MAX_MID = 85.0
DEFAULT_DEPTH_UTIL = 0.3
DEFAULT_MIN_ENTRY_SIZE = 1


def analyze_spread_opportunity(
    ticker: str,
    book: OrderBookState,
    config: dict,
) -> Optional[OpportunityResult]:
    """Pure analysis function replicating SpreadCaptureStrategy.analyze_opportunity.

    Args:
        ticker: Market ticker.
        book: OrderBookState with full depth.
        config: Dict with keys: min_spread_cents, max_spread_cents,
                min_depth_at_best, bid_improvement_cents, ask_discount_cents,
                max_entry_size. Optional: min_mid_price_cents, max_mid_price_cents,
                kalshi_maker_rate, depth_utilization_pct, min_entry_size.

    Returns:
        OpportunityResult or None if market doesn't qualify.
    """
    if not book.best_bid or not book.best_ask:
        return None

    spread = book.spread
    if spread is None:
        return None

    min_spread = config["min_spread_cents"]
    max_spread = config["max_spread_cents"]
    if spread < min_spread or spread > max_spread:
        return None

    mid = book.mid_price
    if mid is None:
        return None

    min_mid = config.get("min_mid_price_cents", DEFAULT_MIN_MID)
    max_mid = config.get("max_mid_price_cents", DEFAULT_MAX_MID)
    if mid < min_mid or mid > max_mid:
        return None

    min_depth = config["min_depth_at_best"]
    if book.best_bid.size < min_depth or book.best_ask.size < min_depth:
        return None

    # Entry/exit prices with improvement/discount
    entry_price = book.best_bid.price + config["bid_improvement_cents"]
    exit_price = book.best_ask.price - config["ask_discount_cents"]

    if entry_price >= exit_price:
        return None

    # Fees
    maker_rate = config.get("kalshi_maker_rate", DEFAULT_MAKER_RATE)
    entry_fee_per = kalshi_fee(maker_rate, 1, entry_price)
    exit_fee_per = kalshi_fee(maker_rate, 1, exit_price)

    gross_edge_per = (exit_price - entry_price) / 100.0
    net_edge_per = gross_edge_per - entry_fee_per - exit_fee_per

    if net_edge_per <= 0:
        return None

    # Position sizing: depth utilization capped at max_entry_size
    depth_util = config.get("depth_utilization_pct", DEFAULT_DEPTH_UTIL)
    max_entry = config["max_entry_size"]
    min_entry = config.get("min_entry_size", DEFAULT_MIN_ENTRY_SIZE)

    size = min(int(book.best_bid.size * depth_util), max_entry)
    if size < min_entry:
        return None

    expected_net_profit = net_edge_per * size

    # Fill probability
    fill_prob = estimate_fill_probability(
        spread, book.best_bid.size, book.best_ask.size
    )
    risk_adjusted = expected_net_profit * fill_prob

    return OpportunityResult(
        ticker=ticker,
        entry_price=entry_price,
        exit_price=exit_price,
        spread=spread,
        mid_price=mid,
        bid_depth=book.best_bid.size,
        ask_depth=book.best_ask.size,
        entry_size=size,
        gross_edge_per=gross_edge_per,
        net_edge_per=net_edge_per,
        entry_fee_per=entry_fee_per,
        exit_fee_per=exit_fee_per,
        expected_net_profit=expected_net_profit,
        fill_probability=fill_prob,
        risk_adjusted_profit=risk_adjusted,
    )


def estimate_fill_probability(spread: int, bid_depth: int, ask_depth: int) -> float:
    """Heuristic fill probability based on spread width and depth.

    Wider spread → higher entry fill prob (less competition).
    Deeper book → higher exit fill prob (more liquidity to sell into).

    Returns entry_prob * exit_prob in [0, 1].
    """
    # Entry: wider spread means our bid is less likely to be competed away
    # At 3c spread, ~0.4 prob; at 15c+, ~0.85
    entry_prob = min(0.85, 0.3 + spread * 0.04)

    # Exit: deeper ask side means more likely to get our sell filled
    # At 1 contract depth, ~0.3; at 10+, ~0.75
    exit_prob = min(0.75, 0.2 + ask_depth * 0.055)

    return entry_prob * exit_prob


# =============================================================================
# Orderbook Fetching & Parsing
# =============================================================================


def parse_kalshi_orderbook(ticker: str, raw: dict) -> OrderBookState:
    """Convert raw API orderbook response to OrderBookState with full depth.

    API returns: {"orderbook": {"yes": [[price,size],...], "no": [[price,size],...]}}
    - yes list → yes bids (sorted descending by price)
    - no list → derive yes asks as 100 - no_price (sorted ascending)
    """
    ob = raw.get("orderbook", raw)
    yes_levels = ob.get("yes", [])
    no_levels = ob.get("no", [])

    bids = []
    for level in yes_levels:
        if len(level) >= 2 and level[1] > 0:
            price = level[0]
            if 0 <= price <= 99:
                bids.append(OrderBookLevel(price=price, size=level[1]))
    bids.sort(key=lambda x: x.price, reverse=True)

    asks = []
    for level in no_levels:
        if len(level) >= 2 and level[1] > 0:
            ask_price = 100 - level[0]
            if 0 <= ask_price <= 99:
                asks.append(OrderBookLevel(price=ask_price, size=level[1]))
    asks.sort(key=lambda x: x.price)

    return OrderBookState(ticker=ticker, bids=bids, asks=asks)


SPORT_SERIES = {
    "nba": "KXNBAGAME",
    "nba_totals": "KXNBATOTAL",
    "ncaab": "KXNCAAMBGAME",
    "nhl": "KXNHLGAME",
    "ucl": "KXUCL",
    "tennis": "KXWTA",
    "soccer": "KXSOCCER",
}


def fetch_live_orderbooks(
    sport: Optional[str] = None,
    min_volume: int = 0,
    verbose: bool = False,
) -> Dict[str, OrderBookState]:
    """Fetch live orderbooks for all open markets via public API.

    Args:
        sport: Filter by sport prefix (e.g. 'ncaab', 'nba').
        min_volume: Minimum 24h volume filter.
        verbose: Print progress.

    Returns:
        Dict of ticker -> OrderBookState.
    """
    client = KalshiPublicClient()

    # Use server-side series_ticker filtering when sport is known
    series_ticker = SPORT_SERIES.get(sport.lower(), None) if sport else None

    if verbose:
        if series_ticker:
            print(f"Fetching {sport} markets (series={series_ticker})...")
        else:
            print("Fetching market list...")

    markets = client.get_all_markets(
        min_volume=min_volume,
        series_ticker=series_ticker,
    )

    # Client-side fallback filter if sport didn't match a known series
    if sport and not series_ticker:
        sport_upper = sport.upper()
        markets = [
            m
            for m in markets
            if m.get("ticker", "").upper().startswith(sport_upper)
            or m.get("event_ticker", "").upper().startswith(sport_upper)
            or sport_upper in m.get("ticker", "").upper()
        ]

    if verbose:
        print(f"Found {len(markets)} markets, fetching orderbooks...")

    books: Dict[str, OrderBookState] = {}
    for i, market in enumerate(markets):
        ticker = market.get("ticker", "")
        if not ticker:
            continue

        try:
            raw = client.get_orderbook(ticker)
            book = parse_kalshi_orderbook(ticker, raw)

            # Only keep books with both sides
            if book.best_bid and book.best_ask:
                books[ticker] = book

            if verbose and (i + 1) % 25 == 0:
                print(
                    f"  Fetched {i + 1}/{len(markets)} orderbooks ({len(books)} with depth)"
                )

        except Exception as e:
            if verbose:
                print(f"  Error fetching {ticker}: {e}")

        # Rate limit: ~7 req/s
        time.sleep(0.15)

    if verbose:
        print(f"Got {len(books)} orderbooks with two-sided depth")

    return books


# =============================================================================
# JSONL Load / Save
# =============================================================================


def load_depth_snapshots(path: str) -> Dict[str, OrderBookState]:
    """Load JSONL replay file into Dict[ticker, OrderBookState].

    Uses the last snapshot for each ticker (most recent state).
    """
    books: Dict[str, OrderBookState] = {}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            ticker = data.get("ticker", "")
            if not ticker:
                continue

            yes_levels = data.get("yes", [])
            no_levels = data.get("no", [])

            bids = []
            for level in yes_levels:
                if len(level) >= 2 and level[1] > 0 and 0 <= level[0] <= 99:
                    bids.append(OrderBookLevel(price=level[0], size=level[1]))
            bids.sort(key=lambda x: x.price, reverse=True)

            asks = []
            for level in no_levels:
                if len(level) >= 2 and level[1] > 0:
                    ask_price = 100 - level[0]
                    if 0 <= ask_price <= 99:
                        asks.append(OrderBookLevel(price=ask_price, size=level[1]))
            asks.sort(key=lambda x: x.price)

            book = OrderBookState(ticker=ticker, bids=bids, asks=asks)
            if book.best_bid and book.best_ask:
                books[ticker] = book

    return books


def save_orderbooks_jsonl(books: Dict[str, OrderBookState], path: str) -> None:
    """Save fetched orderbooks to JSONL for future --replay."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().isoformat() + "Z"
    with open(path, "a") as f:
        for ticker, book in books.items():
            yes_levels = [[l.price, l.size] for l in book.bids]
            # Convert asks back to no prices for storage
            no_levels = [[100 - l.price, l.size] for l in book.asks]
            record = {
                "ticker": ticker,
                "timestamp": ts,
                "yes": yes_levels,
                "no": no_levels,
            }
            f.write(json.dumps(record) + "\n")

    print(f"Saved {len(books)} orderbooks to {path}")


# =============================================================================
# Parameter Grid & Sweep
# =============================================================================


FULL_GRID = {
    "min_spread_cents": [3, 5, 8, 10, 15],
    "max_spread_cents": [15, 20, 30, 50],
    "min_depth_at_best": [1, 3, 5, 10],
    "bid_improvement_cents": [0, 1, 2],
    "ask_discount_cents": [0, 1, 2],
    "max_entry_size": [5, 10, 25],
}

QUICK_GRID = {
    "min_spread_cents": [5, 10],
    "max_spread_cents": [20, 50],
    "min_depth_at_best": [1, 5],
    "bid_improvement_cents": [0, 1],
    "ask_discount_cents": [0, 1],
    "max_entry_size": [10, 25],
}


def build_configs(param_grid: dict) -> List[dict]:
    """Generate all valid parameter combinations from a grid.

    Skips invalid combos where min_spread >= max_spread.
    """
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    configs = []

    for combo in itertools.product(*values):
        config = dict(zip(keys, combo))

        # Skip invalid: min must be < max spread
        if config.get("min_spread_cents", 0) >= config.get("max_spread_cents", 100):
            continue

        configs.append(config)

    return configs


def run_sweep(
    books: Dict[str, OrderBookState],
    configs: List[dict],
    verbose: bool = False,
) -> List[SweepResult]:
    """Evaluate every config against every market. Return sorted results."""
    results = []

    for i, config in enumerate(configs):
        opps = []
        for ticker, book in books.items():
            result = analyze_spread_opportunity(ticker, book, config)
            if result is not None:
                opps.append(result)

        total_expected = sum(o.expected_net_profit for o in opps)
        total_risk_adj = sum(o.risk_adjusted_profit for o in opps)
        total_entry_fees = sum(o.entry_fee_per * o.entry_size for o in opps)
        total_exit_fees = sum(o.exit_fee_per * o.entry_size for o in opps)

        avg_edge = sum(o.net_edge_per for o in opps) / len(opps) if opps else 0
        avg_spread = sum(o.spread for o in opps) / len(opps) if opps else 0
        avg_size = sum(o.entry_size for o in opps) / len(opps) if opps else 0

        sr = SweepResult(
            params=config,
            markets_scanned=len(books),
            opportunities_found=len(opps),
            total_expected_profit=total_expected,
            risk_adjusted_profit=total_risk_adj,
            avg_net_edge_per=avg_edge,
            avg_spread=avg_spread,
            avg_entry_size=avg_size,
            total_entry_fees=total_entry_fees,
            total_exit_fees=total_exit_fees,
            qualifying_tickers=[o.ticker for o in opps],
            details=opps,
        )
        results.append(sr)

        if verbose and (i + 1) % 100 == 0:
            print(f"  Evaluated {i + 1}/{len(configs)} configs...")

    # Sort by risk-adjusted profit descending
    results.sort(key=lambda r: r.risk_adjusted_profit, reverse=True)
    return results


# =============================================================================
# Output
# =============================================================================


def print_sweep_table(results: List[SweepResult], top_n: int = 20):
    """Print formatted console table of top sweep results."""
    print()
    print("=" * 120)
    print("SPREAD CAPTURE PARAMETER SWEEP RESULTS")
    print("=" * 120)
    print()

    header = (
        f"{'MinSpr':<7} {'MaxSpr':<7} {'Depth':<6} {'BidImp':<7} {'AskDsc':<7} "
        f"{'Size':<6} {'Opps':<6} {'ExpProf':<10} {'RiskAdj':<10} "
        f"{'AvgEdge':<9} {'AvgSpr':<7} {'Fees':<9}"
    )
    print(header)
    print("-" * 120)

    for r in results[:top_n]:
        p = r.params
        total_fees = r.total_entry_fees + r.total_exit_fees
        row = (
            f"{p.get('min_spread_cents', ''):<7} "
            f"{p.get('max_spread_cents', ''):<7} "
            f"{p.get('min_depth_at_best', ''):<6} "
            f"{p.get('bid_improvement_cents', ''):<7} "
            f"{p.get('ask_discount_cents', ''):<7} "
            f"{p.get('max_entry_size', ''):<6} "
            f"{r.opportunities_found:<6} "
            f"${r.total_expected_profit:<9.4f} "
            f"${r.risk_adjusted_profit:<9.4f} "
            f"{r.avg_net_edge_per * 100:<8.2f}c "
            f"{r.avg_spread:<6.1f} "
            f"${total_fees:<8.4f}"
        )
        print(row)

    print("=" * 120)

    if results:
        best = results[0]
        print()
        print("OPTIMAL PARAMETERS (by risk-adjusted profit):")
        for k, v in best.params.items():
            print(f"  {k}: {v}")
        print()
        print(
            f"  Opportunities: {best.opportunities_found}/{best.markets_scanned} markets"
        )
        print(f"  Expected profit: ${best.total_expected_profit:.4f}")
        print(f"  Risk-adjusted profit: ${best.risk_adjusted_profit:.4f}")
        print(f"  Avg net edge: {best.avg_net_edge_per * 100:.2f}c/contract")
        if best.qualifying_tickers:
            print(f"  Top tickers: {', '.join(best.qualifying_tickers[:10])}")

    print()


def print_opportunity_details(results: List[SweepResult], top_n: int = 5):
    """Print detailed opportunities from top configs."""
    for i, sr in enumerate(results[:top_n]):
        print(f"\n--- Config #{i + 1} (risk-adj=${sr.risk_adjusted_profit:.4f}) ---")
        for k, v in sr.params.items():
            print(f"  {k}={v}", end="  ")
        print()

        for opp in sorted(
            sr.details, key=lambda o: o.risk_adjusted_profit, reverse=True
        )[:5]:
            print(
                f"  {opp.ticker}: buy@{opp.entry_price}c sell@{opp.exit_price}c "
                f"spread={opp.spread}c mid={opp.mid_price:.1f}c size={opp.entry_size} "
                f"edge={opp.net_edge_per * 100:.2f}c fill={opp.fill_probability:.2f} "
                f"exp=${opp.expected_net_profit:.4f} risk=${opp.risk_adjusted_profit:.4f}"
            )


def save_results_csv(results: List[SweepResult], path: str):
    """Save sweep results to CSV."""
    if not results:
        print("No results to save")
        return

    Path(path).parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for r in results:
        row = dict(r.params)
        row.update(
            {
                "markets_scanned": r.markets_scanned,
                "opportunities_found": r.opportunities_found,
                "total_expected_profit": round(r.total_expected_profit, 6),
                "risk_adjusted_profit": round(r.risk_adjusted_profit, 6),
                "avg_net_edge_per": round(r.avg_net_edge_per, 6),
                "avg_spread": round(r.avg_spread, 2),
                "avg_entry_size": round(r.avg_entry_size, 2),
                "total_entry_fees": round(r.total_entry_fees, 6),
                "total_exit_fees": round(r.total_exit_fees, 6),
                "qualifying_tickers": ";".join(r.qualifying_tickers),
            }
        )
        rows.append(row)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Results saved to {path} ({len(rows)} rows)")


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Spread Capture Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/backtest_spread_capture.py                         # Live scan, full grid
  python scripts/backtest_spread_capture.py --quick --sport ncaab   # Quick sweep, NCAAB only
  python scripts/backtest_spread_capture.py --output results.csv    # Save to CSV
  python scripts/backtest_spread_capture.py --save-books data/depth_snapshots/today.jsonl
  python scripts/backtest_spread_capture.py --replay data/depth_snapshots/today.jsonl
  python scripts/backtest_spread_capture.py --grid '{"min_spread_cents":[5,8]}'
        """,
    )

    # Data source
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--live-scan",
        action="store_true",
        default=True,
        help="Fetch live orderbooks (default)",
    )
    source.add_argument(
        "--replay",
        type=str,
        metavar="FILE",
        help="Replay from saved JSONL file",
    )

    # Market filters
    parser.add_argument(
        "--sport",
        type=str,
        help="Filter markets by sport prefix (e.g. ncaab, nba)",
    )
    parser.add_argument(
        "--min-volume",
        type=int,
        default=0,
        help="Minimum 24h volume (default: 0)",
    )

    # Grid options
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use quick grid (64 combos instead of full)",
    )
    parser.add_argument(
        "--grid",
        type=str,
        metavar="JSON",
        help="Custom parameter grid as JSON string",
    )

    # Output
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Save results to CSV file",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top results to display (default: 20)",
    )
    parser.add_argument(
        "--save-books",
        type=str,
        metavar="FILE",
        help="Save fetched orderbooks to JSONL for future replay",
    )

    # Verbosity
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # --- Load orderbooks ---
    if args.replay:
        print(f"Loading orderbooks from {args.replay}...")
        books = load_depth_snapshots(args.replay)
        if not books:
            print("Error: no valid orderbooks found in replay file")
            return 1
        print(f"Loaded {len(books)} orderbooks from replay")
    else:
        books = fetch_live_orderbooks(
            sport=args.sport,
            min_volume=args.min_volume,
            verbose=args.verbose,
        )
        if not books:
            print("Error: no orderbooks fetched (check filters or network)")
            return 1

    # Save books if requested
    if args.save_books:
        save_orderbooks_jsonl(books, args.save_books)

    # --- Build parameter grid ---
    if args.grid:
        try:
            custom_grid = json.loads(args.grid)
        except json.JSONDecodeError as e:
            print(f"Error parsing --grid JSON: {e}")
            return 1
        # Merge with defaults for any missing keys
        base = QUICK_GRID if args.quick else FULL_GRID
        grid = {**base, **custom_grid}
    elif args.quick:
        grid = QUICK_GRID
    else:
        grid = FULL_GRID

    configs = build_configs(grid)
    print(
        f"\nSweeping {len(configs)} parameter combinations across {len(books)} markets"
    )
    print("=" * 60)

    # --- Run sweep ---
    results = run_sweep(books, configs, verbose=args.verbose)

    # --- Output ---
    print_sweep_table(results, top_n=args.top)

    if args.verbose:
        print_opportunity_details(results, top_n=5)

    if args.output:
        save_results_csv(results, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
