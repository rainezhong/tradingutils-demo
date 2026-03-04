#!/usr/bin/env python3
"""Run crypto scalp strategy (paper mode) with volume filters.

Intended to run alongside btc_latency_probe.py for data collection.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.crypto_scalp.config import CryptoScalpConfig
from strategies.crypto_scalp.orchestrator import CryptoScalpStrategy

try:
    from core.exchange_client.kalshi import KalshiExchangeClient
except ImportError:
    KalshiExchangeClient = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

config = CryptoScalpConfig(
    signal_feed="binance",
    spot_lookback_sec=5.0,
    min_spot_move_usd=10.0,
    exit_delay_sec=20.0,
    max_hold_sec=35.0,
    contracts_per_trade=25,
    max_open_positions=1,
    max_total_exposure_usd=50.0,
    slippage_buffer_cents=1,
    exit_slippage_cents=0,
    min_entry_price_cents=25,
    max_entry_price_cents=75,
    cooldown_sec=15.0,
    max_daily_loss_usd=100.0,
    paper_mode=True,
    # 🔥 CRITICAL: Regime filter (backtest validated: 54% WR with osc < 3.0)
    regime_window_sec=60.0,
    regime_osc_threshold=3.0,  # Filter choppy markets (validated in backtest)
    # Volume filters (per-feed calibrated from probe data)
    min_window_volume={"binance": 0.5, "coinbase": 0.3, "kraken": 0.1},
    min_volume_concentration=0.0,
    require_multi_exchange_confirm=True,
    scan_interval_sec=30.0,
)


async def main():
    """Run crypto scalp strategy."""
    # Create exchange client
    if KalshiExchangeClient is None:
        print("ERROR: KalshiExchangeClient not available")
        return

    client = KalshiExchangeClient.from_env()

    # Create and run strategy
    strategy = CryptoScalpStrategy(
        exchange_client=client,
        config=config,
        dry_run=config.paper_mode,
    )

    await strategy.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested...")
