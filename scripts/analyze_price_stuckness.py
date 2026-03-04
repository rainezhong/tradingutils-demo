#!/usr/bin/env python3
"""
Analyze when BTC prediction market prices get "stuck" at extreme values.

A price is "stuck" when:
1. It reaches extreme values (>90¢ or <10¢)
2. Volatility drops to near-zero
3. Entropy is low (price distribution is narrow)
4. No meaningful movement despite spot price changes

This helps filter crypto scalp trades where there's no edge to capture.
"""

import sqlite3
import numpy as np
import argparse
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class PriceState:
    """State of price at a given time."""
    timestamp: float
    price_cents: int  # Kalshi price (0-100)
    spot_price: float  # BTC spot price
    strike: float  # Market strike
    time_to_expiry_sec: float


@dataclass
class StucknessMetrics:
    """Metrics for detecting stuck prices."""
    timestamp: float
    price_cents: int

    # Entropy metrics
    price_entropy: float  # Shannon entropy of recent price distribution
    price_range_cents: int  # Max - min price in recent window

    # Movement metrics
    price_volatility: float  # Std dev of price changes
    spot_volatility: float  # Std dev of spot price changes

    # Responsiveness metrics
    spot_price_change: float  # BTC price change in window
    kalshi_price_change: int  # Kalshi price change in window
    responsiveness_ratio: float  # Kalshi change / spot change (should be >0)

    # Extremity metrics
    distance_from_50: int  # abs(price - 50)
    is_extreme: bool  # price > 90 or < 10

    # Stuckness classification
    is_stuck: bool
    stuck_reason: str


def compute_shannon_entropy(prices: List[int], n_bins: int = 10) -> float:
    """Compute Shannon entropy of price distribution.

    High entropy = prices moving around (good for trading)
    Low entropy = prices concentrated (stuck, bad for trading)

    Args:
        prices: List of prices (0-100 cents)
        n_bins: Number of bins for histogram (default 10 = 10¢ buckets)

    Returns:
        Entropy in bits
    """
    if len(prices) < 2:
        return 0.0

    # Create histogram
    hist, _ = np.histogram(prices, bins=n_bins, range=(0, 100))

    # Normalize to probabilities
    hist = hist / hist.sum()

    # Remove zero bins
    hist = hist[hist > 0]

    # Shannon entropy: H = -sum(p * log2(p))
    entropy = -np.sum(hist * np.log2(hist))

    return float(entropy)


def compute_stuckness_metrics(
    window: List[PriceState],
    lookback_sec: float = 300.0,  # 5 minutes
) -> Optional[StucknessMetrics]:
    """Compute stuckness metrics for a price window.

    Args:
        window: List of PriceState objects in reverse chronological order (newest first)
        lookback_sec: How far back to look for metrics

    Returns:
        StucknessMetrics or None if insufficient data
    """
    if len(window) < 5:  # Need at least 5 data points
        return None

    current = window[0]

    # Filter to lookback window
    cutoff_time = current.timestamp - lookback_sec
    recent = [s for s in window if s.timestamp >= cutoff_time]

    if len(recent) < 3:
        return None

    # Extract arrays
    prices = [s.price_cents for s in recent]
    spot_prices = [s.spot_price for s in recent]

    # Entropy
    price_entropy = compute_shannon_entropy(prices)
    price_range = max(prices) - min(prices)

    # Volatility
    price_changes = np.diff(prices)
    spot_changes = np.diff(spot_prices)

    price_volatility = float(np.std(price_changes)) if len(price_changes) > 0 else 0.0
    spot_volatility = float(np.std(spot_changes)) if len(spot_changes) > 0 else 0.0

    # Responsiveness
    spot_price_change = spot_prices[0] - spot_prices[-1]
    kalshi_price_change = prices[0] - prices[-1]

    # Responsiveness ratio: how much did Kalshi move per $1 BTC move?
    # For a 15-min $67k strike binary, we'd expect ~0.5-1¢ per $10 BTC move
    # So ratio should be > 0.05 (0.5¢ / $10)
    if abs(spot_price_change) > 1.0:  # At least $1 move
        responsiveness_ratio = abs(kalshi_price_change) / abs(spot_price_change)
    else:
        responsiveness_ratio = 1.0  # Assume responsive if no spot movement

    # Extremity
    distance_from_50 = abs(current.price_cents - 50)
    is_extreme = current.price_cents > 90 or current.price_cents < 10

    # Stuckness detection
    is_stuck = False
    stuck_reason = ""

    # Rule 1: Extreme price + low volatility
    if is_extreme and price_volatility < 2.0:
        is_stuck = True
        stuck_reason = f"Extreme price ({current.price_cents}¢) + low volatility ({price_volatility:.1f}¢)"

    # Rule 2: Low entropy (concentrated distribution)
    elif price_entropy < 1.0:  # Less than 1 bit = very concentrated
        is_stuck = True
        stuck_reason = f"Low entropy ({price_entropy:.2f} bits) - prices concentrated"

    # Rule 3: Narrow range despite spot movement
    elif price_range < 3 and abs(spot_price_change) > 20:
        is_stuck = True
        stuck_reason = f"Narrow range ({price_range}¢) despite ${spot_price_change:.0f} BTC move"

    # Rule 4: Unresponsive to spot changes
    elif abs(spot_price_change) > 50 and responsiveness_ratio < 0.02:
        is_stuck = True
        stuck_reason = f"Unresponsive to ${spot_price_change:.0f} BTC move (ratio={responsiveness_ratio:.3f})"

    return StucknessMetrics(
        timestamp=current.timestamp,
        price_cents=current.price_cents,
        price_entropy=price_entropy,
        price_range_cents=price_range,
        price_volatility=price_volatility,
        spot_volatility=spot_volatility,
        spot_price_change=spot_price_change,
        kalshi_price_change=kalshi_price_change,
        responsiveness_ratio=responsiveness_ratio,
        distance_from_50=distance_from_50,
        is_extreme=is_extreme,
        is_stuck=is_stuck,
        stuck_reason=stuck_reason,
    )


def load_price_history(
    db_path: str,
    ticker: Optional[str] = None,
    limit: int = 10000,
) -> List[PriceState]:
    """Load price history from latency probe database.

    Args:
        db_path: Path to btc_latency_probe.db
        ticker: Optional specific ticker to filter
        limit: Max number of records to load

    Returns:
        List of PriceState objects in chronological order
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if we have the new schema (kalshi_snapshots) or old schema
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name IN ('kalshi_snapshots', 'latency_snapshots')
    """)
    tables = [row[0] for row in cursor.fetchall()]

    if "kalshi_snapshots" in tables:
        # New schema (actual schema from Feb 2026)
        query = """
            SELECT
                ts,
                yes_bid,
                floor_strike,
                seconds_to_close
            FROM kalshi_snapshots
        """
        if ticker:
            query += " WHERE ticker = ?"
            query += f" ORDER BY ts DESC LIMIT {limit}"
            cursor.execute(query, (ticker,))
        else:
            query += f" ORDER BY ts DESC LIMIT {limit}"
            cursor.execute(query)
    else:
        # Old schema (latency_snapshots)
        query = """
            SELECT
                ts,
                kalshi_yes_bid,
                floor_strike,
                time_to_expiry_sec
            FROM latency_snapshots
        """
        if ticker:
            query += " WHERE kalshi_ticker = ?"
            query += f" ORDER BY ts DESC LIMIT {limit}"
            cursor.execute(query, (ticker,))
        else:
            query += f" ORDER BY ts DESC LIMIT {limit}"
            cursor.execute(query)

    # Load Kraken/spot prices
    cursor.execute("""
        SELECT ts, spot_price
        FROM kraken_snapshots
        ORDER BY ts DESC
        LIMIT ?
    """, (limit,))
    kraken_data = {row[0]: row[1] for row in cursor.fetchall()}

    # Build PriceState objects
    states = []
    for row in cursor.execute(query if not ticker else query, () if not ticker else (ticker,)):
        ts, kalshi_price, strike, ttx = row

        # Find closest Kraken price
        spot_price = kraken_data.get(ts)
        if spot_price is None:
            # Find nearest timestamp
            nearest_ts = min(kraken_data.keys(), key=lambda t: abs(t - ts), default=None)
            if nearest_ts and abs(nearest_ts - ts) < 5.0:  # Within 5 seconds
                spot_price = kraken_data[nearest_ts]
            else:
                continue  # Skip if no spot price available

        states.append(PriceState(
            timestamp=ts,
            price_cents=kalshi_price,
            spot_price=spot_price,
            strike=strike,
            time_to_expiry_sec=ttx if isinstance(ttx, (int, float)) else 600.0,
        ))

    conn.close()

    # Sort chronologically
    states.sort(key=lambda s: s.timestamp)

    return states


def analyze_stuckness(
    db_path: str,
    ticker: Optional[str] = None,
    lookback_sec: float = 300.0,
    output_csv: Optional[str] = None,
) -> List[StucknessMetrics]:
    """Analyze price stuckness over time.

    Args:
        db_path: Path to database
        ticker: Optional ticker filter
        lookback_sec: Window for computing metrics (default 5 min)
        output_csv: Optional path to save CSV results

    Returns:
        List of StucknessMetrics
    """
    print(f"Loading price history from {db_path}...")
    states = load_price_history(db_path, ticker)
    print(f"Loaded {len(states)} price snapshots")

    if len(states) < 10:
        print("ERROR: Insufficient data")
        return []

    print(f"Analyzing stuckness with {lookback_sec}s lookback window...")

    metrics_list = []

    # Rolling window analysis
    for i in range(len(states)):
        # Window is [i-lookback, i] (current = i)
        window_start = max(0, i - 100)  # Up to 100 samples back
        window = states[window_start:i+1]
        window.reverse()  # Reverse to newest-first

        metrics = compute_stuckness_metrics(window, lookback_sec)
        if metrics:
            metrics_list.append(metrics)

    print(f"Computed {len(metrics_list)} metric snapshots")

    # Summary statistics
    stuck_count = sum(1 for m in metrics_list if m.is_stuck)
    stuck_pct = 100.0 * stuck_count / len(metrics_list) if metrics_list else 0.0

    print(f"\n=== Summary ===")
    print(f"Total snapshots: {len(metrics_list)}")
    print(f"Stuck snapshots: {stuck_count} ({stuck_pct:.1f}%)")
    print(f"")

    # Breakdown by stuck reason
    if stuck_count > 0:
        print("Stuck reasons:")
        reasons = {}
        for m in metrics_list:
            if m.is_stuck:
                key = m.stuck_reason.split("(")[0].strip()  # Get reason prefix
                reasons[key] = reasons.get(key, 0) + 1

        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            pct = 100.0 * count / stuck_count
            print(f"  {reason}: {count} ({pct:.1f}%)")

    # Entropy distribution
    entropies = [m.price_entropy for m in metrics_list]
    print(f"\nEntropy distribution:")
    print(f"  Mean: {np.mean(entropies):.2f} bits")
    print(f"  Median: {np.median(entropies):.2f} bits")
    print(f"  P25: {np.percentile(entropies, 25):.2f} bits")
    print(f"  P75: {np.percentile(entropies, 75):.2f} bits")

    # Save to CSV if requested
    if output_csv:
        import csv
        with open(output_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'datetime', 'price_cents', 'price_entropy', 'price_range',
                'price_volatility', 'spot_volatility', 'spot_change', 'kalshi_change',
                'responsiveness_ratio', 'distance_from_50', 'is_extreme', 'is_stuck', 'stuck_reason'
            ])

            for m in metrics_list:
                dt = datetime.fromtimestamp(m.timestamp).strftime('%Y-%m-%d %H:%M:%S')
                writer.writerow([
                    m.timestamp, dt, m.price_cents, m.price_entropy, m.price_range_cents,
                    m.price_volatility, m.spot_volatility, m.spot_price_change,
                    m.kalshi_price_change, m.responsiveness_ratio, m.distance_from_50,
                    m.is_extreme, m.is_stuck, m.stuck_reason
                ])

        print(f"\nSaved results to {output_csv}")

    return metrics_list


def plot_stuckness(metrics: List[StucknessMetrics], output_path: Optional[str] = None):
    """Plot stuckness metrics over time (requires matplotlib)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("ERROR: matplotlib not installed, cannot plot")
        return

    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)

    timestamps = [m.timestamp for m in metrics]
    datetimes = [datetime.fromtimestamp(ts) for ts in timestamps]

    # Plot 1: Price with stuckness overlay
    ax = axes[0]
    prices = [m.price_cents for m in metrics]
    stuck_mask = [m.is_stuck for m in metrics]

    ax.plot(datetimes, prices, label='Kalshi Price', alpha=0.7)
    stuck_times = [datetimes[i] for i, stuck in enumerate(stuck_mask) if stuck]
    stuck_prices = [prices[i] for i, stuck in enumerate(stuck_mask) if stuck]
    ax.scatter(stuck_times, stuck_prices, color='red', s=20, label='Stuck', alpha=0.5, zorder=10)

    ax.axhline(50, color='gray', linestyle='--', alpha=0.3, label='Mid-market')
    ax.axhline(90, color='red', linestyle='--', alpha=0.3)
    ax.axhline(10, color='red', linestyle='--', alpha=0.3)
    ax.set_ylabel('Kalshi Price (¢)')
    ax.set_title('Price with Stuckness Detection')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 2: Entropy
    ax = axes[1]
    entropies = [m.price_entropy for m in metrics]
    ax.plot(datetimes, entropies, label='Price Entropy', color='green')
    ax.axhline(1.0, color='red', linestyle='--', alpha=0.5, label='Stuck Threshold')
    ax.set_ylabel('Entropy (bits)')
    ax.set_title('Price Entropy (low = stuck)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 3: Volatility
    ax = axes[2]
    price_vols = [m.price_volatility for m in metrics]
    spot_vols = [m.spot_volatility for m in metrics]
    ax.plot(datetimes, price_vols, label='Kalshi Volatility', color='blue')
    ax.plot(datetimes, spot_vols, label='Spot Volatility', color='orange', alpha=0.5)
    ax.set_ylabel('Volatility')
    ax.set_title('Price Volatility (low = stuck)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 4: Responsiveness ratio
    ax = axes[3]
    ratios = [m.responsiveness_ratio for m in metrics]
    ax.plot(datetimes, ratios, label='Responsiveness Ratio', color='purple')
    ax.axhline(0.02, color='red', linestyle='--', alpha=0.5, label='Unresponsive Threshold')
    ax.set_ylabel('Ratio (¢ / $)')
    ax.set_xlabel('Time')
    ax.set_title('Kalshi Responsiveness to Spot Changes (low = stuck)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved plot to {output_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(description='Analyze BTC prediction market price stuckness')
    parser.add_argument('--db', default='data/btc_latency_probe.db', help='Path to latency probe database')
    parser.add_argument('--ticker', help='Optional ticker to filter')
    parser.add_argument('--lookback', type=float, default=300.0, help='Lookback window in seconds (default 300 = 5 min)')
    parser.add_argument('--csv', help='Output CSV path')
    parser.add_argument('--plot', help='Output plot path (requires matplotlib)')

    args = parser.parse_args()

    metrics = analyze_stuckness(
        db_path=args.db,
        ticker=args.ticker,
        lookback_sec=args.lookback,
        output_csv=args.csv,
    )

    if args.plot and metrics:
        plot_stuckness(metrics, args.plot)


if __name__ == '__main__':
    main()
