#!/usr/bin/env python3
"""Analyze Kalshi orderbook repricing speed after Kraken moves.

Investigates why NO side reprices faster than YES side.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Tuple
import statistics


@dataclass
class PriceMove:
    """A Kraken price move and subsequent Kalshi repricing."""
    kraken_ts: float
    kraken_delta: float  # USD price change
    direction: str  # "UP" or "DOWN"
    kalshi_ticker: str

    # Kalshi orderbook before move
    before_yes_ask: int
    before_no_ask: int

    # Kalshi orderbook after move (first snapshot after Kraken move)
    after_yes_ask: int
    after_no_ask: int
    latency_ms: float  # time from Kraken move to Kalshi repricing

    # Price changes
    yes_ask_delta: int  # cents change in YES ask
    no_ask_delta: int  # cents change in NO ask

    @property
    def expected_side(self) -> str:
        """Which side should reprice (move UP) based on Kraken direction."""
        return "NO" if self.direction == "DOWN" else "YES"

    @property
    def expected_delta(self) -> int:
        """Expected price change on the relevant side (should be positive)."""
        return self.no_ask_delta if self.direction == "DOWN" else self.yes_ask_delta

    @property
    def repriced_significantly(self) -> bool:
        """Did the expected side reprice by 5+ cents?"""
        return self.expected_delta >= 5


def find_repricing_events(db_path: str, min_move: float = 10.0) -> List[PriceMove]:
    """Find Kraken price moves and measure Kalshi repricing speed.

    Args:
        db_path: Path to btc_latency_probe.db
        min_move: Minimum USD price move to analyze (default $10)

    Returns:
        List of PriceMove objects
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all Kraken price snapshots
    kraken_prices = conn.execute("""
        SELECT ts, spot_price
        FROM kraken_snapshots
        ORDER BY ts
    """).fetchall()

    # Find significant price moves
    moves = []
    lookback_window = 5.0  # 5 second window (same as strategy)

    for i in range(len(kraken_prices)):
        current = kraken_prices[i]

        # Look back 5 seconds
        lookback_idx = i - 1
        while lookback_idx >= 0:
            if current['ts'] - kraken_prices[lookback_idx]['ts'] > lookback_window:
                break
            lookback_idx -= 1

        if lookback_idx < 0:
            continue

        # Calculate price change
        delta = current['spot_price'] - kraken_prices[lookback_idx]['spot_price']

        if abs(delta) >= min_move:
            direction = "UP" if delta > 0 else "DOWN"

            # Find Kalshi orderbook snapshots around this time
            # Get snapshot just before Kraken move
            before_snap = conn.execute("""
                SELECT ticker, yes_ask, floor_strike, ts
                FROM kalshi_snapshots
                WHERE ts <= ?
                ORDER BY ts DESC
                LIMIT 1
            """, (kraken_prices[lookback_idx]['ts'],)).fetchone()

            if not before_snap:
                continue

            ticker = before_snap['ticker']

            # Get snapshot just after Kraken move
            after_snap = conn.execute("""
                SELECT yes_ask, ts
                FROM kalshi_snapshots
                WHERE ticker = ? AND ts >= ?
                ORDER BY ts ASC
                LIMIT 1
            """, (ticker, current['ts'])).fetchone()

            if not after_snap:
                continue

            # Calculate NO ask prices (100 - YES bid)
            # For simplicity, approximate as 100 - YES ask (conservative)
            before_no_ask = 100 - (before_snap['yes_ask'] - 1)  # rough approximation
            after_no_ask = 100 - (after_snap['yes_ask'] - 1)

            yes_ask_delta = after_snap['yes_ask'] - before_snap['yes_ask']
            no_ask_delta = after_no_ask - before_no_ask

            latency_ms = (after_snap['ts'] - current['ts']) * 1000

            move = PriceMove(
                kraken_ts=current['ts'],
                kraken_delta=delta,
                direction=direction,
                kalshi_ticker=ticker,
                before_yes_ask=before_snap['yes_ask'],
                before_no_ask=before_no_ask,
                after_yes_ask=after_snap['yes_ask'],
                after_no_ask=after_no_ask,
                latency_ms=latency_ms,
                yes_ask_delta=yes_ask_delta,
                no_ask_delta=no_ask_delta,
            )

            moves.append(move)

    conn.close()
    return moves


def analyze_repricing_asymmetry(moves: List[PriceMove]) -> None:
    """Analyze if NO reprices faster than YES."""

    up_moves = [m for m in moves if m.direction == "UP"]
    down_moves = [m for m in moves if m.direction == "DOWN"]

    print("\n" + "="*80)
    print("REPRICING SPEED ANALYSIS")
    print("="*80)

    print(f"\nTotal moves analyzed: {len(moves)}")
    print(f"  UP moves (buy YES): {len(up_moves)}")
    print(f"  DOWN moves (buy NO): {len(down_moves)}")

    # Analyze UP moves (should reprice YES ask)
    if up_moves:
        yes_latencies = [m.latency_ms for m in up_moves]
        yes_deltas = [m.yes_ask_delta for m in up_moves]
        yes_repriced = [m for m in up_moves if m.repriced_significantly]

        print(f"\n--- UP MOVES (BTC rises → buy YES) ---")
        print(f"Average repricing latency: {statistics.mean(yes_latencies):.1f}ms")
        print(f"Median repricing latency: {statistics.median(yes_latencies):.1f}ms")
        print(f"Average YES ask delta: {statistics.mean(yes_deltas):.1f}¢")
        print(f"Median YES ask delta: {statistics.median(yes_deltas):.1f}¢")
        print(f"Repriced 5+¢: {len(yes_repriced)}/{len(up_moves)} ({len(yes_repriced)/len(up_moves)*100:.1f}%)")

        # Find fast repricers (< 67ms, faster than our order placement)
        fast_yes = [m for m in up_moves if m.latency_ms < 67]
        print(f"Repriced in <67ms: {len(fast_yes)}/{len(up_moves)} ({len(fast_yes)/len(up_moves)*100:.1f}%)")

    # Analyze DOWN moves (should reprice NO ask)
    if down_moves:
        no_latencies = [m.latency_ms for m in down_moves]
        no_deltas = [m.no_ask_delta for m in down_moves]
        no_repriced = [m for m in down_moves if m.repriced_significantly]

        print(f"\n--- DOWN MOVES (BTC drops → buy NO) ---")
        print(f"Average repricing latency: {statistics.mean(no_latencies):.1f}ms")
        print(f"Median repricing latency: {statistics.median(no_latencies):.1f}ms")
        print(f"Average NO ask delta: {statistics.mean(no_deltas):.1f}¢")
        print(f"Median NO ask delta: {statistics.median(no_deltas):.1f}¢")
        print(f"Repriced 5+¢: {len(no_repriced)}/{len(down_moves)} ({len(no_repriced)/len(down_moves)*100:.1f}%)")

        fast_no = [m for m in down_moves if m.latency_ms < 67]
        print(f"Repriced in <67ms: {len(fast_no)}/{len(down_moves)} ({len(fast_no)/len(down_moves)*100:.1f}%)")

    # Statistical comparison
    if up_moves and down_moves:
        print("\n" + "="*80)
        print("ASYMMETRY ANALYSIS")
        print("="*80)

        yes_median_latency = statistics.median([m.latency_ms for m in up_moves])
        no_median_latency = statistics.median([m.latency_ms for m in down_moves])

        yes_fast_pct = len([m for m in up_moves if m.latency_ms < 67]) / len(up_moves) * 100
        no_fast_pct = len([m for m in down_moves if m.latency_ms < 67]) / len(down_moves) * 100

        print(f"\nMedian repricing latency:")
        print(f"  YES side (BTC up): {yes_median_latency:.1f}ms")
        print(f"  NO side (BTC down): {no_median_latency:.1f}ms")
        print(f"  Difference: {abs(yes_median_latency - no_median_latency):.1f}ms")

        print(f"\nRepricing speed (<67ms to beat our orders):")
        print(f"  YES side: {yes_fast_pct:.1f}% reprice in <67ms")
        print(f"  NO side: {no_fast_pct:.1f}% reprice in <67ms")

        if no_fast_pct > yes_fast_pct:
            print(f"\n⚠️  NO SIDE REPRICES FASTER: {no_fast_pct - yes_fast_pct:.1f}pp more likely to beat our orders")
        elif yes_fast_pct > no_fast_pct:
            print(f"\n✅ YES SIDE REPRICES FASTER: {yes_fast_pct - no_fast_pct:.1f}pp more likely to beat our orders")
        else:
            print("\n➖ NO ASYMMETRY DETECTED")

    # Show examples
    print("\n" + "="*80)
    print("EXAMPLE REPRICING EVENTS")
    print("="*80)

    if down_moves:
        print("\nFastest NO repricing (BTC drops):")
        fastest_no = sorted(down_moves, key=lambda m: m.latency_ms)[:3]
        for m in fastest_no:
            print(f"  ${m.kraken_delta:+.1f} → NO ask {m.before_no_ask}¢→{m.after_no_ask}¢ in {m.latency_ms:.0f}ms")

    if up_moves:
        print("\nFastest YES repricing (BTC rises):")
        fastest_yes = sorted(up_moves, key=lambda m: m.latency_ms)[:3]
        for m in fastest_yes:
            print(f"  ${m.kraken_delta:+.1f} → YES ask {m.before_yes_ask}¢→{m.after_yes_ask}¢ in {m.latency_ms:.0f}ms")


if __name__ == "__main__":
    import sys

    db_path = "data/btc_latency_probe.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    print(f"Analyzing repricing speed from {db_path}...")
    print("Looking for $10+ Kraken moves and measuring Kalshi repricing...")

    moves = find_repricing_events(db_path, min_move=10.0)

    if not moves:
        print("\n❌ No significant price moves found in database")
        sys.exit(1)

    analyze_repricing_asymmetry(moves)
