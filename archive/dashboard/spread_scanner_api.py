"""Spread Scanner API endpoints for the dashboard.

Provides endpoints for:
- Fast refresh: Update quotes for current top 10 spreads
- Slow discovery: Full scan for new opportunities
- Mock trading: Log trade details without execution
"""

import time
from datetime import datetime
from typing import Dict, List, Tuple, Any
from pydantic import BaseModel


class SpreadScanConfig(BaseModel):
    """Configuration for spread scanning."""

    fast_refresh_interval: int = 5  # seconds
    slow_scan_interval: int = 60  # seconds
    min_profit: float = 0.05  # dollars
    min_volume: int = 0  # minimum 24h volume
    contract_size: int = 100  # for fee calculations
    discover_mode: bool = True  # auto-discover pairs vs use known pairs


class SpreadOpportunity(BaseModel):
    """A spread trading opportunity."""

    rank: int
    spread_id: str
    event_title: str
    ticker_a: str
    ticker_b: str
    profit: float
    profit_change: float = 0.0
    ask_a: float
    ask_b: float
    combined_cost: float
    volume: int  # min(vol_a, vol_b)
    volume_change_pct: float = 0.0
    last_updated: float


class TradeRequest(BaseModel):
    """Request to execute a mock trade."""

    spread_id: str
    amount: int  # number of contracts


class TradeLog(BaseModel):
    """Log entry for a mock trade."""

    timestamp: str
    spread_id: str
    event_title: str
    action: str  # "BUY"
    amount: int
    leg_a: Dict[str, Any]
    leg_b: Dict[str, Any]
    total_cost: float
    expected_profit: float
    status: str  # "MOCK_TRADE"


class SpreadScannerState:
    """Manages spread scanner state with configurable refresh rates."""

    def __init__(self):
        self.config = SpreadScanConfig()
        self.current_display: List[SpreadOpportunity] = []
        self.background_cache: List[SpreadOpportunity] = []
        self.trade_logs: List[TradeLog] = []
        self.last_display_refresh: float = 0
        self.last_background_scan: float = 0

        # Smart scanning: cache discovered pairs and profitable spreads
        self.discovered_pairs: List[Tuple[str, str]] = []  # Cached ticker pairs
        self.last_discovery_time: float = 0  # When we last discovered pairs
        self.profitable_spreads: Dict[
            str, Tuple[str, str]
        ] = {}  # spread_id -> ticker_pair
        self.discovery_interval: float = 900  # Re-discover every 15 minutes

        # Concurrency control
        self.scan_in_progress: bool = False  # Prevent overlapping scans
        self.scan_start_time: float = 0  # When current scan started

    def update_config(self, config: SpreadScanConfig):
        """Update scanner configuration."""
        self.config = config

    def has_new_opportunities(self) -> bool:
        """Check if background cache differs from current display."""
        if len(self.background_cache) == 0:
            return False

        # Compare top 10 spread IDs
        bg_ids = sorted([o.spread_id for o in self.background_cache[:10]])
        cur_ids = sorted([o.spread_id for o in self.current_display[:10]])

        return bg_ids != cur_ids

    def sync_background_to_display(self):
        """Copy background cache to display."""
        self.current_display = [
            SpreadOpportunity(**opp.dict()) for opp in self.background_cache
        ]
        self.last_display_refresh = time.time()

    def add_trade_log(self, log: TradeLog):
        """Add a trade log entry."""
        self.trade_logs.append(log)

        # Keep last 100 logs
        if len(self.trade_logs) > 100:
            self.trade_logs = self.trade_logs[-100:]

    def clear_trade_logs(self):
        """Clear all trade logs."""
        self.trade_logs = []


# Global scanner state
scanner_state = SpreadScannerState()


def get_scanner_state() -> SpreadScannerState:
    """Get the global scanner state."""
    return scanner_state


async def refresh_current_spreads() -> List[SpreadOpportunity]:
    """Fast refresh: Re-fetch quotes for current display spreads.

    This only refreshes the spreads currently displayed, making it fast.
    """
    from src.core.api_client import KalshiClient
    from src.core.config import get_config
    from arb.kalshi_scanner import KalshiSpreadScanner

    if not scanner_state.current_display:
        return []

    # Extract ticker pairs from current display
    ticker_pairs = [
        (opp.ticker_a, opp.ticker_b) for opp in scanner_state.current_display
    ]

    print(
        f"[REFRESH] Fast refresh for {len(ticker_pairs)} spreads (top {len(scanner_state.current_display)})"
    )
    print(
        f"[REFRESH]   - Estimated API calls: ~{len(ticker_pairs)} (1 per spread pair)"
    )
    print("[REFRESH]   - Rate limit: 0.05s delay (~20 calls/sec, safe for small batch)")

    config = get_config()
    client = KalshiClient(config)
    scanner = KalshiSpreadScanner(client, min_volume=scanner_state.config.min_volume)

    # Fetch quotes for these specific pairs
    refresh_start = time.time()
    pairs = scanner.scan_known_pairs(ticker_pairs, delay_seconds=0.05)
    refresh_elapsed = time.time() - refresh_start
    print(f"[REFRESH]   - Quote fetching took {refresh_elapsed:.1f}s")
    print(
        f"[REFRESH]   - Average rate: {len(ticker_pairs) / refresh_elapsed:.1f} calls/sec"
    )

    # Filter valid pairs
    valid_pairs = [
        p for p in pairs if p.combined_yes_ask is not None and p.combined_yes_ask < 1.5
    ]

    # Build volume lookup
    volume_lookup = {}
    for p in valid_pairs:
        spread_id = f"{p.market_a.ticker}|{p.market_b.ticker}"
        volume_a = p.market_a.volume_24h or 0
        volume_b = p.market_b.volume_24h or 0
        volume_lookup[spread_id] = min(volume_a, volume_b)

    # Scan for opportunities
    opportunities = scanner.scan_opportunities(
        pairs=valid_pairs,
        min_edge_cents=0.0,
        contract_size=scanner_state.config.contract_size,
    )

    # Build updated opportunities
    updated_opps = []
    for rank, opp in enumerate(opportunities[:10], 1):
        pair = opp["pair"]
        spread_id = f"{pair['market_a']['ticker']}|{pair['market_b']['ticker']}"
        profit = opp["dutch_profit_per_contract"]
        volume = volume_lookup.get(spread_id, 0)

        # Find old opportunity to calculate changes
        old_opp = next(
            (o for o in scanner_state.current_display if o.spread_id == spread_id), None
        )
        profit_change = profit - old_opp.profit if old_opp else 0.0
        volume_change_pct = (
            ((volume - old_opp.volume) / old_opp.volume * 100)
            if (old_opp and old_opp.volume > 0)
            else 0.0
        )

        updated_opps.append(
            SpreadOpportunity(
                rank=rank,
                spread_id=spread_id,
                event_title=pair["event_title"],
                ticker_a=pair["market_a"]["ticker"],
                ticker_b=pair["market_b"]["ticker"],
                profit=profit,
                profit_change=profit_change,
                ask_a=pair["market_a"]["yes_ask"],
                ask_b=pair["market_b"]["yes_ask"],
                combined_cost=opp["combined_cost"],
                volume=volume,
                volume_change_pct=volume_change_pct,
                last_updated=time.time(),
            )
        )

    scanner_state.current_display = updated_opps
    scanner_state.last_display_refresh = time.time()

    return updated_opps


async def discover_new_opportunities() -> List[SpreadOpportunity]:
    """Smart scan: Discovers pairs once, then only monitors profitable spreads.

    Strategy:
    1. Discover pairs every 15 minutes (not every scan)
    2. Do full scan to find all opportunities
    3. Cache profitable spread IDs for efficient re-scanning
    4. Only re-scan the profitable spreads on subsequent calls
    """
    from src.core.api_client import KalshiClient
    from src.core.config import get_config
    from arb.kalshi_scanner import (
        KalshiSpreadScanner,
        discover_complementary_pairs,
        get_all_known_pairs,
    )

    print(f"\n{'=' * 70}")
    print(
        f"[DISCOVER] API call received at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
    )
    print(f"{'=' * 70}")

    # CRITICAL: Prevent overlapping scans
    if scanner_state.scan_in_progress:
        elapsed = time.time() - scanner_state.scan_start_time
        print("[DISCOVER] ⚠️  SCAN ALREADY IN PROGRESS!")
        print(f"[DISCOVER]   - Started {elapsed:.1f}s ago")
        print("[DISCOVER]   - Skipping this scan to prevent overlap")
        print(f"{'=' * 70}\n")
        return scanner_state.background_cache

    # Lock the scanner
    scanner_state.scan_in_progress = True
    scanner_state.scan_start_time = time.time()
    print("[DISCOVER] 🔒 Scan lock acquired")

    try:
        current_time = time.time()
        config = get_config()
        client = KalshiClient(config)
        scanner = KalshiSpreadScanner(
            client, min_volume=scanner_state.config.min_volume
        )

        print("[DISCOVER] Step 1: Checking discovery cache...")

        # Step 1: Discover pairs (only if needed)
        needs_discovery = (
            len(scanner_state.discovered_pairs) == 0
            or current_time - scanner_state.last_discovery_time
            > scanner_state.discovery_interval
        )

        print(
            f"[DISCOVER]   - Discovered pairs in cache: {len(scanner_state.discovered_pairs)}"
        )
        print(
            f"[DISCOVER]   - Time since last discovery: {current_time - scanner_state.last_discovery_time:.1f}s"
        )
        print(f"[DISCOVER]   - Needs discovery: {needs_discovery}")

        if needs_discovery:
            print("[DISCOVER] Step 2: Running pair discovery...")
            print(
                f"[DISCOVER]   - Discovery mode: {scanner_state.config.discover_mode}"
            )

            discovery_start = time.time()
            if scanner_state.config.discover_mode:
                print(
                    "[DISCOVER]   - Calling discover_complementary_pairs() with max_pages=5..."
                )
                ticker_pairs = discover_complementary_pairs(
                    client, max_pages=5, delay=1.0
                )
            else:
                print("[DISCOVER]   - Using get_all_known_pairs()...")
                ticker_pairs = get_all_known_pairs()

            discovery_elapsed = time.time() - discovery_start
            print(f"[DISCOVER]   - Discovery took {discovery_elapsed:.1f}s")
            print(
                f"[DISCOVER]   - Discovered {len(ticker_pairs)} pairs (cached for 15 min)"
            )

            scanner_state.discovered_pairs = ticker_pairs
            scanner_state.last_discovery_time = current_time
        else:
            ticker_pairs = scanner_state.discovered_pairs
            print(f"[DISCOVER] Step 2: Using cached pairs ({len(ticker_pairs)} pairs)")

        if not ticker_pairs:
            print("[DISCOVER] ERROR: No pairs to scan!")
            return []

        # Step 2: Decide which pairs to scan
        # If we have profitable spreads cached, only scan those (efficient)
        # Otherwise, do full scan to find profitable spreads (discovery phase)
        print("[DISCOVER] Step 3: Deciding scan strategy...")
        print(
            f"[DISCOVER]   - Profitable spreads in cache: {len(scanner_state.profitable_spreads)}"
        )

        if scanner_state.profitable_spreads:
            # Efficient mode: only scan profitable spreads
            pairs_to_scan = list(scanner_state.profitable_spreads.values())
            print("[DISCOVER]   - Strategy: SMART SCAN (only profitable spreads)")
            print(f"[DISCOVER]   - Pairs to scan: {len(pairs_to_scan)}")
            print(f"[DISCOVER]   - Estimated API calls: ~{len(pairs_to_scan) * 2}")
        else:
            # Discovery mode: scan all pairs to find profitable ones
            pairs_to_scan = ticker_pairs
            print("[DISCOVER]   - Strategy: FULL SCAN (find profitable spreads)")
            print(f"[DISCOVER]   - Pairs to scan: {len(pairs_to_scan)}")
            print(f"[DISCOVER]   - Estimated API calls: ~{len(pairs_to_scan) * 2}")

        # Step 3: Fetch quotes and scan with rate limiting
        # Use 0.12s delay to stay under 10 req/sec (1 call per pair = 8.3 calls/sec)
        print(f"[DISCOVER] Step 4: Fetching quotes for {len(pairs_to_scan)} pairs...")
        print("[DISCOVER]   - Rate limit: 0.12s delay between calls (~8 calls/sec)")
        scan_start = time.time()
        pairs = scanner.scan_known_pairs(pairs_to_scan, delay_seconds=0.12)
        scan_elapsed = time.time() - scan_start

        print(f"[DISCOVER]   - Quote fetching took {scan_elapsed:.1f}s")
        print(f"[DISCOVER]   - Received {len(pairs)} pairs")
        print(
            f"[DISCOVER]   - Average rate: {len(pairs_to_scan) / scan_elapsed:.1f} calls/sec"
        )

        valid_pairs = [
            p
            for p in pairs
            if p.combined_yes_ask is not None and p.combined_yes_ask < 1.5
        ]
        print(f"[DISCOVER]   - Valid pairs (combined_ask < 1.5): {len(valid_pairs)}")

        # Build volume lookup
        print("[DISCOVER] Step 5: Building volume lookup...")
        volume_lookup = {}
        for p in valid_pairs:
            spread_id = f"{p.market_a.ticker}|{p.market_b.ticker}"
            volume_a = p.market_a.volume_24h or 0
            volume_b = p.market_b.volume_24h or 0
            volume_lookup[spread_id] = min(volume_a, volume_b)

        # Scan for opportunities
        print("[DISCOVER] Step 6: Scanning for arbitrage opportunities...")
        opp_start = time.time()
        opportunities = scanner.scan_opportunities(
            pairs=valid_pairs,
            min_edge_cents=0.0,  # Get all opportunities, filter below
            contract_size=scanner_state.config.contract_size,
        )
        opp_elapsed = time.time() - opp_start
        print(f"[DISCOVER]   - Opportunity scan took {opp_elapsed:.1f}s")
        print(f"[DISCOVER]   - Found {len(opportunities)} raw opportunities")

        # Step 4: Update profitable spreads cache and build opportunity list
        print("[DISCOVER] Step 7: Processing opportunities...")
        print(
            f"[DISCOVER]   - Min profit threshold: ${scanner_state.config.min_profit:.4f}"
        )
        print(
            f"[DISCOVER]   - Buffer threshold (50%): ${scanner_state.config.min_profit * 0.5:.4f}"
        )

        new_profitable_spreads = {}
        new_opps = []

        for rank, opp in enumerate(opportunities[:10], 1):
            pair = opp["pair"]
            ticker_a = pair["market_a"]["ticker"]
            ticker_b = pair["market_b"]["ticker"]
            spread_id = f"{ticker_a}|{ticker_b}"
            profit = opp["dutch_profit_per_contract"]

            # Cache if profitable (with 50% buffer for volatility)
            threshold_with_buffer = scanner_state.config.min_profit * 0.5
            if profit >= threshold_with_buffer:
                new_profitable_spreads[spread_id] = (ticker_a, ticker_b)

            # Only add to display if above actual threshold
            if profit >= scanner_state.config.min_profit:
                new_opps.append(
                    SpreadOpportunity(
                        rank=rank,
                        spread_id=spread_id,
                        event_title=pair["event_title"],
                        ticker_a=ticker_a,
                        ticker_b=ticker_b,
                        profit=profit,
                        profit_change=0.0,
                        ask_a=pair["market_a"]["yes_ask"],
                        ask_b=pair["market_b"]["yes_ask"],
                        combined_cost=opp["combined_cost"],
                        volume=volume_lookup.get(spread_id, 0),
                        volume_change_pct=0.0,
                        last_updated=time.time(),
                    )
                )

        # Update state
        scanner_state.profitable_spreads = new_profitable_spreads
        scanner_state.background_cache = new_opps
        scanner_state.last_background_scan = time.time()

        total_elapsed = time.time() - current_time
        print("[DISCOVER] Step 8: Results summary")
        print(f"[DISCOVER]   - Opportunities above threshold: {len(new_opps)}")
        print(
            f"[DISCOVER]   - Spreads to track (with buffer): {len(new_profitable_spreads)}"
        )
        print(f"[DISCOVER]   - Total scan time: {total_elapsed:.1f}s")
        print(
            f"[DISCOVER]   - Next scan will use: {'SMART MODE' if new_profitable_spreads else 'FULL SCAN'}"
        )
        print("[DISCOVER] 🔓 Scan lock released")
        print(f"{'=' * 70}\n")

        return new_opps

    finally:
        # Always release the lock, even if there's an error
        scanner_state.scan_in_progress = False


def create_trade_log(opportunity: SpreadOpportunity, amount: int) -> TradeLog:
    """Create a trade log entry from an opportunity."""
    return TradeLog(
        timestamp=datetime.now().isoformat(),
        spread_id=opportunity.spread_id,
        event_title=opportunity.event_title,
        action="BUY",
        amount=amount,
        leg_a={
            "ticker": opportunity.ticker_a,
            "side": "YES",
            "action": "BUY",
            "price": opportunity.ask_a,
            "amount": amount,
            "cost": opportunity.ask_a * amount,
        },
        leg_b={
            "ticker": opportunity.ticker_b,
            "side": "YES",
            "action": "BUY",
            "price": opportunity.ask_b,
            "amount": amount,
            "cost": opportunity.ask_b * amount,
        },
        total_cost=opportunity.combined_cost * amount,
        expected_profit=opportunity.profit * amount,
        status="MOCK_TRADE",
    )
