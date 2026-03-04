"""Latency probe orchestrator.

Polls Kalshi markets, collects truth source readings, handles market
rotation and settlement recording. Not abstract — takes a TruthSource
as a constructor argument.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .recorder import ProbeRecorder
from .truth_source import TruthSource

logger = logging.getLogger(__name__)


@dataclass
class ProbeConfig:
    """Configuration for a latency probe run."""

    series_ticker: str = "KXBTC15M"
    poll_interval_sec: float = 0.25  # Reduced from 0.5s for faster latency measurement
    market_refresh_polls: int = 120  # Re-discover markets every N polls (doubled from 60 since polls are 2x faster)
    settlement_wait_sec: float = 2.0
    multi_market: bool = False  # Track all open markets concurrently


@dataclass
class _TrackedMarket:
    """Internal state for a market being probed."""

    ticker: str
    strike: Optional[float]
    close_time: Optional[str]
    seconds_to_close: Optional[float] = None


class LatencyProbe:
    """Orchestrator that ties Kalshi polling, truth source, and recording."""

    def __init__(
        self,
        truth_source: TruthSource,
        recorder: ProbeRecorder,
        config: Optional[ProbeConfig] = None,
    ) -> None:
        self._truth = truth_source
        self._recorder = recorder
        self._config = config or ProbeConfig()
        self._client = None  # KalshiExchangeClient, set in run()
        self._markets: List[_TrackedMarket] = []
        self._settled: Set[str] = set()
        self._poll_count = 0
        self._snapshot_count = 0
        self._truth_count = 0

    async def run(self, duration_sec: int = 3600) -> None:
        """Run the probe for the specified duration.

        Connects to Kalshi, starts truth source, polls in a loop.
        """
        # Connect Kalshi client
        try:
            from core.exchange_client.kalshi import KalshiExchangeClient
        except ImportError:
            from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

        self._client = KalshiExchangeClient.from_env()
        await self._client.connect()
        logger.info("Kalshi: connected")

        # Start truth source
        self._truth.start()
        logger.info("Truth source: started (connected=%s)", self._truth.is_connected)

        # Discover initial markets
        await self._discover_markets()

        start_time = time.time()
        try:
            while time.time() - start_time < duration_sec:
                await self._poll_cycle()
                await asyncio.sleep(self._config.poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("Probe interrupted by user")
        finally:
            self._truth.stop()
            self._recorder.flush()
            if self._client:
                await self._client.exit()
            self._print_run_summary(time.time() - start_time)

    async def _poll_cycle(self) -> None:
        """One polling iteration: refresh markets if needed, poll each, record."""
        self._poll_count += 1

        # Refresh market list periodically
        if (self._poll_count % self._config.market_refresh_polls == 0
                or not self._markets):
            await self._discover_markets()

        if not self._markets:
            return

        now = time.time()

        for mkt in list(self._markets):
            try:
                await self._poll_market(mkt, now)
            except Exception as e:
                logger.error("Poll error for %s: %s", mkt.ticker, e)

    async def _poll_market(self, mkt: _TrackedMarket, now: float) -> None:
        """Poll a single market: fetch Kalshi state, record snapshot + truth."""
        mkt_data = await self._client._request(
            "GET", f"/markets/{mkt.ticker}"
        )
        m = mkt_data.get("market", mkt_data)

        yes_bid = m.get("yes_bid") or 0
        yes_ask = m.get("yes_ask") or 100
        yes_mid = (yes_bid + yes_ask) / 2.0

        # Parse close_time → seconds_to_close
        seconds_to_close = None
        close_str = m.get("close_time")
        if close_str:
            try:
                close_dt = datetime.fromisoformat(
                    close_str.replace("Z", "+00:00")
                )
                seconds_to_close = (
                    close_dt - datetime.now(timezone.utc)
                ).total_seconds()
            except (ValueError, AttributeError):
                pass

        raw_strike = m.get("floor_strike", mkt.strike)
        strike = float(raw_strike) if raw_strike is not None else mkt.strike
        mkt.strike = strike
        mkt.close_time = close_str
        mkt.seconds_to_close = seconds_to_close

        # Market expired → handle settlement and remove
        if seconds_to_close is not None and seconds_to_close < 0:
            await self._handle_settlement(mkt)
            self._markets = [x for x in self._markets if x.ticker != mkt.ticker]
            return

        # Record Kalshi snapshot
        self._recorder.record_kalshi_snapshot(
            ts=now,
            ticker=mkt.ticker,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            yes_mid=yes_mid,
            strike=strike,
            close_time=close_str,
            seconds_to_close=seconds_to_close,
            volume=m.get("volume", 0),
            open_interest=m.get("open_interest", 0),
        )
        self._snapshot_count += 1

        # Get truth reading and record
        reading = self._truth.get_reading(
            ticker=mkt.ticker,
            strike=strike,
            seconds_to_close=seconds_to_close or 0,
        )
        if reading:
            self._recorder.record_truth_reading(now, mkt.ticker, reading)
            self._truth_count += 1

        # Log live status
        self._log_status(mkt, yes_mid, yes_bid, yes_ask, seconds_to_close, reading)

    async def _discover_markets(self) -> None:
        """Find open markets in the configured series."""
        try:
            data = await self._client._request(
                "GET",
                "/markets",
                params={
                    "series_ticker": self._config.series_ticker,
                    "status": "open",
                    "limit": 10,
                },
            )
        except Exception as e:
            logger.error("Market discovery failed: %s", e)
            return

        api_markets = data.get("markets", [])
        if not api_markets:
            logger.warning("No open %s markets found", self._config.series_ticker)
            return

        # Sort by close_time (soonest first)
        api_markets.sort(key=lambda m: m.get("close_time", ""))

        existing_tickers = {m.ticker for m in self._markets}

        if self._config.multi_market:
            # Track all open markets
            for m in api_markets:
                ticker = m.get("ticker")
                if ticker and ticker not in existing_tickers and ticker not in self._settled:
                    self._markets.append(_TrackedMarket(
                        ticker=ticker,
                        strike=m.get("floor_strike"),
                        close_time=m.get("close_time"),
                    ))
                    logger.info("Tracking: %s (strike=%s, close=%s)",
                                ticker, m.get("floor_strike"), m.get("close_time"))
        else:
            # Single-market mode: track only the soonest expiring
            if not self._markets:
                m = api_markets[0]
                ticker = m.get("ticker")
                if ticker and ticker not in self._settled:
                    self._markets = [_TrackedMarket(
                        ticker=ticker,
                        strike=m.get("floor_strike"),
                        close_time=m.get("close_time"),
                    )]
                    logger.info("Tracking: %s (strike=%s, close=%s)",
                                ticker, m.get("floor_strike"), m.get("close_time"))

    async def _handle_settlement(self, mkt: _TrackedMarket) -> None:
        """Record settlement outcome after a market expires."""
        ticker = mkt.ticker
        if ticker in self._settled:
            return
        self._settled.add(ticker)

        try:
            await asyncio.sleep(self._config.settlement_wait_sec)

            mkt_data = await self._client._request("GET", f"/markets/{ticker}")
            m = mkt_data.get("market", mkt_data)

            exp_value_raw = m.get("expiration_value")
            exp_value = float(exp_value_raw) if exp_value_raw is not None else None
            result = m.get("result")  # "yes" or "no"
            close_time = m.get("close_time", mkt.close_time or "")

            # Determine settlement
            if result:
                settled_yes = 1 if result.lower() == "yes" else 0
            elif exp_value is not None and mkt.strike is not None:
                settled_yes = 1 if exp_value > mkt.strike else 0
            else:
                settled_yes = None

            # Truth reading near settlement
            reading = self._truth.get_reading(
                ticker=ticker,
                strike=mkt.strike,
                seconds_to_close=0,
            )
            truth_prob = reading.probability if reading else None
            truth_predicted_yes = (
                1 if (truth_prob is not None and truth_prob > 0.5) else 0
            )

            # Last Kalshi snapshot
            kalshi_last_mid = self._recorder.get_last_kalshi_mid(ticker)
            kalshi_predicted_yes = 1 if (kalshi_last_mid and kalshi_last_mid > 50) else 0

            # Correctness
            truth_right = (
                1 if (settled_yes is not None and truth_predicted_yes == settled_yes)
                else 0
            )
            kalshi_right = (
                1 if (settled_yes is not None and kalshi_predicted_yes == settled_yes)
                else 0
            )

            self._recorder.record_settlement(
                ticker=ticker,
                close_time=close_time,
                settled_yes=settled_yes,
                expiration_value=exp_value,
                truth_prob=truth_prob,
                truth_predicted_yes=truth_predicted_yes,
                kalshi_last_mid=kalshi_last_mid,
                kalshi_predicted_yes=kalshi_predicted_yes,
                truth_was_right=truth_right,
                kalshi_was_right=kalshi_right,
            )

            logger.info(
                "SETTLEMENT: %s result=%s expval=%s | "
                "Truth=%s Kalshi=%s",
                ticker,
                "YES" if settled_yes else "NO",
                exp_value,
                "RIGHT" if truth_right else "WRONG",
                "RIGHT" if kalshi_right else "WRONG",
            )

        except Exception as e:
            logger.error("Settlement recording error for %s: %s", ticker, e)

    def _log_status(
        self,
        mkt: _TrackedMarket,
        yes_mid: float,
        yes_bid: int,
        yes_ask: int,
        seconds_to_close: Optional[float],
        reading: Optional[object],
    ) -> None:
        """Log a single-line status update."""
        parts = [f"Kalshi yes_mid={yes_mid:.0f}c bid/ask={yes_bid}/{yes_ask}"]

        if reading and hasattr(reading, "raw_value") and reading.raw_value:
            parts.insert(0, f"Truth={reading.raw_value:,.2f} P={reading.probability:.3f}")

        if mkt.strike:
            parts.append(f"strike={mkt.strike}")

        # Direction comparison
        if reading and reading.probability is not None:
            truth_yes = reading.probability > 0.5
            kalshi_yes = yes_mid > 50
            parts.append("AGREE" if truth_yes == kalshi_yes else "DISAGREE")

        if seconds_to_close is not None:
            parts.append(f"close in {seconds_to_close:.0f}s")

        logger.info("%s | %s", mkt.ticker, " | ".join(parts))

    def _print_run_summary(self, elapsed: float) -> None:
        """Print summary after run completes."""
        print(f"\n=== Probe Run Summary ===")
        print(f"Duration:          {elapsed / 60:.1f} minutes")
        print(f"Polls:             {self._poll_count}")
        print(f"Kalshi snapshots:  {self._snapshot_count}")
        print(f"Truth readings:    {self._truth_count}")
        print(f"Markets settled:   {len(self._settled)}")
