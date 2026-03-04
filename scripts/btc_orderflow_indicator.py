#!/usr/bin/env python3
"""BTC Orderflow Indicator — live dashboard showing predicted BTC direction.

Reads Binance + Coinbase L2 depth and trade flow, outputs a continuously
updating display with direction, confidence, and regime.

Usage:
    python3 scripts/btc_orderflow_indicator.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

from core.indicators.orderflow import OrderflowIndicator, OrderflowReading


def print_dashboard(r: OrderflowReading) -> None:
    os.system("clear")
    arrow = {"UP": "\u25b2", "DOWN": "\u25bc", "NEUTRAL": "\u2500"}[r.direction]
    vol_label = "HIGH" if r.regime == "ACTIVE" else "LOW"
    imb_label = "bids heavier" if r.book_imbalance > 0 else "asks heavier"
    trade_label = "aggressive buying" if r.trade_imbalance > 0 else "aggressive selling"

    bn_mid = f"${r.binance.mid_price:,.0f}" if r.binance else "---"
    bn_spread = f"{r.binance.spread_bps:.2f}%" if r.binance else "---"
    bn_depth = (
        f"{r.binance.bid_depth_btc + r.binance.ask_depth_btc:.1f} BTC"
        if r.binance
        else "---"
    )
    cb_mid = f"${r.coinbase.mid_price:,.0f}" if r.coinbase else "---"
    cb_spread = f"{r.coinbase.spread_bps:.2f}%" if r.coinbase else "---"
    cb_depth = (
        f"{r.coinbase.bid_depth_btc + r.coinbase.ask_depth_btc:.1f} BTC"
        if r.coinbase
        else "---"
    )

    bid_d = r.binance.bid_depth_btc if r.binance else 0
    ask_d = r.binance.ask_depth_btc if r.binance else 0
    if r.coinbase:
        bid_d += r.coinbase.bid_depth_btc
        ask_d += r.coinbase.ask_depth_btc

    lines = [
        "BTC ORDERFLOW INDICATOR",
        "\u2501" * 50,
        f"Direction:   {r.direction} {arrow}         Confidence: {r.confidence * 100:.0f}%",
        f"Regime:      {r.regime:12s} Volume: {r.volume_rate_btc_sec:.1f} BTC/sec",
        "",
        f"Book Imbalance:    {r.book_imbalance:+.2f}  ({imb_label})",
        f"Trade Imbalance:   {r.trade_imbalance:+.2f}  ({trade_label})",
        f"Volume Rate:       {r.volume_rate_btc_sec:.1f} BTC/sec  ({vol_label})",
        f"Large Trades:      {r.large_trade_count} in last 30s",
        f"Depth (0.1%):      {bid_d:.1f} BTC bid / {ask_d:.1f} BTC ask",
        "",
        f"Binance:  mid={bn_mid}  spread={bn_spread}  depth={bn_depth}",
        f"Coinbase: mid={cb_mid}  spread={cb_spread}  depth={cb_depth}",
        "\u2501" * 50,
    ]
    print("\n".join(lines))


def main() -> None:
    indicator = OrderflowIndicator()
    indicator.start()
    print("Starting BTC Orderflow Indicator... waiting for data")
    try:
        while True:
            reading = indicator.get_reading()
            if reading:
                print_dashboard(reading)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nShutting down...")
        indicator.stop()


if __name__ == "__main__":
    main()
