"""Crypto (BTC) truth source for the latency probe framework.

Uses Kraken BTC/USD 60-second rolling average + Black-Scholes N(d2)
to compute P(spot > strike at expiry).
"""

import logging
import math
import threading
import time
from typing import Optional, Tuple

from core.latency_probe.truth_source import TruthReading, TruthSource
from core.latency_probe.recorder import ProbeRecorder
from src.feeds.kraken_feed import KrakenPriceFeed, KrakenPriceUpdate

logger = logging.getLogger(__name__)

DEFAULT_ANNUALIZED_VOL = 0.65


# ------------------------------------------------------------------
# Black-Scholes helpers (standalone, from strategies/latency_arb/crypto.py)
# ------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation of N(x)."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sgn = 1 if x >= 0 else -1
    x = abs(x)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(
        -x * x / 2
    )
    return 0.5 * (1.0 + sgn * y)


def black_scholes_prob(
    spot: float,
    strike: float,
    seconds_to_expiry: float,
    volatility: float = DEFAULT_ANNUALIZED_VOL,
) -> float:
    """P(S_T > K) under martingale assumption = N(d2)."""
    if spot <= 0 or strike <= 0:
        return 0.5

    time_years = seconds_to_expiry / (365.25 * 24 * 3600)
    if time_years <= 0:
        return 1.0 if spot > strike else 0.0

    try:
        vol_sqrt_t = volatility * math.sqrt(time_years)
        if vol_sqrt_t <= 0:
            return 1.0 if spot > strike else 0.0

        d2 = (
            math.log(spot / strike) - 0.5 * volatility ** 2 * time_years
        ) / vol_sqrt_t

        prob = _normal_cdf(d2)
        return max(0.001, min(0.999, prob))
    except (ValueError, ZeroDivisionError):
        return 0.5


# ------------------------------------------------------------------
# CryptoTruthSource
# ------------------------------------------------------------------

KRAKEN_SNAPSHOTS_SQL = """
    CREATE TABLE IF NOT EXISTS kraken_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        spot_price REAL NOT NULL,
        avg_60s REAL NOT NULL,
        trade_count_60s INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_kraken_snap_ts ON kraken_snapshots(ts);
"""


class CryptoTruthSource(TruthSource):
    """Kraken BTC/USD feed → Black-Scholes probability.

    Wraps KrakenPriceFeed and converts its 60-second rolling average
    into a probability that BTC settles above a given strike.
    """

    def __init__(
        self,
        recorder: Optional[ProbeRecorder] = None,
        volatility: float = DEFAULT_ANNUALIZED_VOL,
    ) -> None:
        self._feed = KrakenPriceFeed(symbols=["BTCUSDT"])
        self._recorder = recorder
        self._vol = volatility
        self._latest_avg60: Optional[float] = None
        self._latest_spot: Optional[float] = None
        self._latest_count: int = 0
        # Pending Kraken snapshot buffered from WS thread, flushed on main thread
        self._pending_snap: Optional[Tuple[float, float, float, int]] = None
        self._lock = threading.Lock()

        # Register extension table for raw Kraken snapshots
        if self._recorder:
            self._recorder.register_tables(KRAKEN_SNAPSHOTS_SQL)

    def start(self) -> None:
        self._feed.on_price_update(self._on_update)
        self._feed.start()

    def stop(self) -> None:
        self._feed.stop()

    @property
    def is_connected(self) -> bool:
        return self._feed.is_connected

    def get_reading(
        self,
        ticker: str,
        strike: Optional[float],
        seconds_to_close: float,
    ) -> Optional[TruthReading]:
        if self._latest_avg60 is None or strike is None:
            return None

        # Flush any pending Kraken snapshot (safe: called from main thread)
        self._flush_pending_snap()

        prob = black_scholes_prob(
            self._latest_avg60, strike, seconds_to_close, self._vol
        )
        return TruthReading(
            timestamp=time.time(),
            probability=prob,
            raw_value=self._latest_avg60,
            metadata={"spot": self._latest_spot, "trade_count_60s": self._latest_count},
        )

    def _flush_pending_snap(self) -> None:
        """Write buffered Kraken snapshot to DB from main thread."""
        if not self._recorder:
            return
        with self._lock:
            snap = self._pending_snap
            self._pending_snap = None
        if snap:
            ts, price, avg60, count = snap
            self._recorder.execute(
                "INSERT INTO kraken_snapshots "
                "(ts, spot_price, avg_60s, trade_count_60s) VALUES (?,?,?,?)",
                (ts, price, avg60, count),
            )

    def _on_update(self, update: KrakenPriceUpdate) -> None:
        self._latest_avg60 = update.avg_60s
        self._latest_spot = update.price
        self._latest_count = update.trade_count_60s

        # Buffer snapshot for main-thread flush (avoids SQLite threading error)
        if self._recorder and update.avg_60s > 0:
            with self._lock:
                self._pending_snap = (
                    time.time(), update.price, update.avg_60s, update.trade_count_60s
                )
