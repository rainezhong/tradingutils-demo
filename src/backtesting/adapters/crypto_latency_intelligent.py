"""Crypto latency adapter with intelligent exits for backtesting.

Extends CryptoLatencyAdapter with IntelligentExitManager for data-driven exits.
"""

import bisect
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.core.models import MarketState, Fill as CoreFill
from strategies.base import Signal
from strategies.latency_arb.intelligent_exits import IntelligentExitManager, ExitSignal

from ..data_feed import BacktestFrame, DataFeed
from ..engine import BacktestAdapter


# ---------------------------------------------------------------------------
# Helpers (Black-Scholes, Kelly sizing)
# ---------------------------------------------------------------------------


def _normal_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = (
        0.254829592,
        -0.284496736,
        1.421413741,
        -1.453152027,
        1.061405429,
    )
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(
        -x * x / 2
    )
    return 0.5 * (1.0 + sign * y)


def _implied_probability(
    spot: float, strike: float, ttx_sec: float, vol: float
) -> float:
    if spot <= 0 or strike <= 0:
        return 0.5
    time_years = ttx_sec / (365.25 * 24 * 3600)
    if time_years <= 0:
        return 1.0 if spot > strike else 0.0
    vol_sqrt_t = vol * math.sqrt(time_years)
    if vol_sqrt_t <= 0:
        return 1.0 if spot > strike else 0.0
    d2 = (math.log(spot / strike) - 0.5 * vol**2 * time_years) / vol_sqrt_t
    return max(0.001, min(0.999, _normal_cdf(d2)))


def _kelly_size(
    win_prob: float,
    entry_price: float,
    kelly_frac: float,
    bankroll: float,
    max_bet: float,
) -> float:
    if entry_price >= 0.99 or entry_price <= 0.01:
        return 0
    f = (win_prob - entry_price) / (1.0 - entry_price)
    f *= kelly_frac
    if f <= 0:
        return 0
    f = min(f, 0.25)
    return min(bankroll * f, max_bet)


# ---------------------------------------------------------------------------
# Position Tracker
# ---------------------------------------------------------------------------


@dataclass
class BacktestPosition:
    """Track an open position for exit evaluation."""

    ticker: str
    entry_time: datetime
    entry_price_cents: int
    entry_fair_value: float
    entry_market_prob: float
    side: str  # "YES" or "NO"
    size: int
    entry_spot: float
    entry_strike: float


# ---------------------------------------------------------------------------
# CryptoLatencyDataFeed
# ---------------------------------------------------------------------------


class CryptoLatencyDataFeed(DataFeed):
    """Reads paired Kalshi+Kraken probe snapshots from a SQLite database."""

    def __init__(self, db_path: str, use_spot_price: bool = True):
        self._db_path = db_path
        self._use_spot = use_spot_price
        self._snapshots, self._settlements = self._load()

    def _load(self) -> Tuple[list, Dict[str, Optional[float]]]:
        conn = sqlite3.connect(self._db_path)

        kraken_rows = conn.execute(
            "SELECT ts, spot_price, avg_60s FROM kraken_snapshots ORDER BY ts"
        ).fetchall()
        kr_ts = [r[0] for r in kraken_rows]
        kr_spot = [r[1] for r in kraken_rows]
        kr_avg = [r[2] for r in kraken_rows]

        kalshi_rows = conn.execute(
            "SELECT ts, ticker, yes_bid, yes_ask, yes_mid, floor_strike, "
            "close_time, seconds_to_close, volume, open_interest "
            "FROM kalshi_snapshots ORDER BY ts"
        ).fetchall()
        conn.close()

        # Build snapshots with nearest Kraken data
        snapshots = []
        terminal: Dict[str, dict] = {}  # Track last snapshot per ticker

        for row in kalshi_rows:
            ts, ticker, yb, ya, ym, strike, ct, ttx, vol, oi = row
            if strike is None or strike <= 0:
                continue

            idx = bisect.bisect_left(kr_ts, ts)
            if idx >= len(kr_ts):
                idx = len(kr_ts) - 1
            elif idx > 0 and abs(kr_ts[idx - 1] - ts) < abs(kr_ts[idx] - ts):
                idx -= 1

            spot = kr_spot[idx]
            avg_60s = kr_avg[idx]

            snapshots.append(
                {
                    "ts": ts,
                    "ticker": ticker,
                    "yes_bid": yb,
                    "yes_ask": ya,
                    "yes_mid": ym,
                    "floor_strike": strike,
                    "close_time": ct,
                    "seconds_to_close": ttx,
                    "volume": vol,
                    "open_interest": oi,
                    "kraken_spot": spot,
                    "kraken_avg_60s": avg_60s,
                }
            )
            terminal[ticker] = {"ttx": ttx, "yes_mid": ym}

        # Determine settlements from terminal prices
        settlements: Dict[str, Optional[float]] = {}
        for ticker, info in terminal.items():
            if info["ttx"] <= 5:  # Near expiry
                if info["yes_mid"] >= 90:
                    settlements[ticker] = 1.0  # Settled YES
                elif info["yes_mid"] <= 10:
                    settlements[ticker] = 0.0  # Settled NO

        # Sort by timestamp
        snapshots.sort(key=lambda s: s["ts"])
        return snapshots, settlements

    def __iter__(self) -> Iterator[BacktestFrame]:
        for idx, snap in enumerate(self._snapshots):
            ticker = snap["ticker"]
            ts = datetime.fromtimestamp(snap["ts"], tz=timezone.utc)
            yb = snap["yes_bid"] / 100.0
            ya = snap["yes_ask"] / 100.0
            if ya < yb:
                ya = yb + 0.01

            market = MarketState(
                ticker=ticker,
                timestamp=ts,
                bid=yb,
                ask=ya,
                last_price=snap.get("yes_mid", yb) / 100.0 if isinstance(snap.get("yes_mid"), (int, float)) else yb,
                volume=snap.get("volume", 0),
            )

            yield BacktestFrame(
                frame_idx=idx,
                timestamp=ts,
                markets={ticker: market},
                context=snap,
            )

    def get_settlement(self) -> Dict[str, float]:
        return self._settlements.copy()

    @property
    def tickers(self) -> List[str]:
        """Return list of unique tickers in the dataset."""
        return list(set(snap["ticker"] for snap in self._snapshots))

    @property
    def metadata(self) -> Dict:
        return {"db_path": self._db_path, "total_snapshots": len(self._snapshots)}


# ---------------------------------------------------------------------------
# CryptoLatencyIntelligentAdapter
# ---------------------------------------------------------------------------


class CryptoLatencyIntelligentAdapter(BacktestAdapter):
    """Crypto latency adapter with intelligent exit strategies.

    Combines Black-Scholes fair value + Kelly sizing (entry)
    with IntelligentExitManager (exit).
    """

    def __init__(
        self,
        # Entry parameters (same as CryptoLatencyAdapter)
        vol: float = 0.30,
        min_edge: float = 0.10,
        slippage_cents: int = 3,
        min_ttx_sec: int = 120,
        max_ttx_sec: int = 900,
        min_spread_cents: int = 3,
        max_gap_pct: float = 0.05,
        kelly_fraction: float = 0.5,
        max_bet_dollars: float = 50.0,
        bankroll: float = 100.0,
        use_spot_price: bool = True,
        one_entry_per_market: bool = False,
        cooldown_sec: float = 60.0,
        # Intelligent exit parameters
        enable_intelligent_exits: bool = True,
        edge_convergence_threshold: float = 0.30,
        trailing_stop_activation: float = 0.05,
        trailing_stop_distance: float = 0.03,
        velocity_threshold: float = 0.01,
        spread_widening_threshold: float = 5,
        profit_target_cents: Optional[int] = None,
        max_hold_time_sec: float = 60.0,
        # Fallback to fixed time exit if intelligent exits disabled
        fixed_exit_delay_sec: float = 15.0,
    ):
        # Entry config
        self._vol = vol
        self._min_edge = min_edge
        self._slippage = slippage_cents / 100.0
        self._min_ttx = min_ttx_sec
        self._max_ttx = max_ttx_sec
        self._min_spread = min_spread_cents
        self._max_gap_pct = max_gap_pct
        self._kelly_frac = kelly_fraction
        self._max_bet = max_bet_dollars
        self._bankroll = bankroll
        self._use_spot = use_spot_price
        self._one_per_market = one_entry_per_market
        self._cooldown_sec = cooldown_sec

        # Exit config
        self._enable_intelligent = enable_intelligent_exits
        self._fixed_exit_delay = fixed_exit_delay_sec

        # Intelligent exit manager
        if enable_intelligent_exits:
            self._exit_manager = IntelligentExitManager(
                edge_convergence_threshold=edge_convergence_threshold,
                trailing_stop_activation=trailing_stop_activation,
                trailing_stop_distance=trailing_stop_distance,
                velocity_threshold=velocity_threshold,
                spread_widening_threshold=spread_widening_threshold,
                profit_target_cents=profit_target_cents,
                max_hold_time_sec=max_hold_time_sec,
            )
        else:
            self._exit_manager = None

        # State tracking
        self._positions: Dict[str, BacktestPosition] = {}
        self._entered_markets: Dict[str, bool] = {}
        self._last_exit_ts: Dict[str, float] = {}
        self._signal_metadata: Dict[str, Dict] = {}  # Track signal metadata by ticker

        # Cache last-known market state for all tickers (for exit checking)
        self._last_market_state: Dict[str, Dict] = {}

        # Statistics
        self.total_entries = 0
        self.total_exits = 0
        self.intelligent_exits = 0
        self.fixed_time_exits = 0
        self.exit_reasons: Dict[str, int] = {}

    def on_start(self) -> None:
        self._positions.clear()
        self._entered_markets.clear()
        self._last_exit_ts.clear()
        self.total_entries = 0
        self.total_exits = 0
        self.intelligent_exits = 0
        self.fixed_time_exits = 0
        self.exit_reasons.clear()

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        """Evaluate frame for both entry and exit signals."""
        signals = []

        # Update last-known market state for this ticker
        ctx = frame.context
        ticker = ctx.get("ticker", "")
        if ticker:
            self._last_market_state[ticker] = ctx.copy()

        # Check exits for all open positions first
        exit_signals = self._check_exits(frame)
        signals.extend(exit_signals)

        # Then check for new entry opportunities
        entry_signals = self._check_entries(frame)
        signals.extend(entry_signals)

        return signals

    def _check_entries(self, frame: BacktestFrame) -> List[Signal]:
        """Check for entry opportunities (same logic as CryptoLatencyAdapter)."""
        ctx = frame.context
        ticker = ctx.get("ticker", "")
        ttx = ctx.get("seconds_to_close", 0)
        spot = ctx["kraken_spot"] if self._use_spot else ctx["kraken_avg_60s"]
        strike = ctx.get("floor_strike", 0)

        if not ticker or strike <= 0:
            return []

        # Skip if already in position
        if ticker in self._positions:
            return []

        # TTX filter
        if ttx < self._min_ttx or ttx > self._max_ttx:
            return []

        # One entry per market
        if self._one_per_market and self._entered_markets.get(ticker):
            return []

        # Cooldown
        last_exit = self._last_exit_ts.get(ticker, 0.0)
        cur_ts = frame.timestamp.timestamp()
        if last_exit > 0 and (cur_ts - last_exit) < self._cooldown_sec:
            return []

        # Spread filter
        kalshi_spread = ctx["yes_ask"] - ctx["yes_bid"]
        if kalshi_spread < self._min_spread:
            return []

        # Gap filter
        if strike > 0:
            gap_pct = abs(spot - strike) / strike
            if gap_pct > self._max_gap_pct:
                return []

        # Model FV
        fv = _implied_probability(spot, strike, ttx, self._vol)

        # Edge calculation
        yes_ask_dec = ctx["yes_ask"] / 100.0
        no_ask_dec = (100 - ctx["yes_bid"]) / 100.0

        yes_entry = yes_ask_dec + self._slippage
        no_entry = no_ask_dec + self._slippage

        from src.backtesting.fill_model import kalshi_taker_fee

        yes_edge = fv - yes_entry - kalshi_taker_fee(yes_entry)
        no_edge = (1 - fv) - no_entry - kalshi_taker_fee(no_entry)

        direction = None
        edge = 0.0
        entry_price = 0.0
        entry_market_prob = 0.0

        if yes_edge >= self._min_edge and yes_edge >= no_edge:
            direction = "YES"
            edge = yes_edge
            entry_price = yes_entry
            entry_market_prob = yes_entry
        elif no_edge >= self._min_edge:
            direction = "NO"
            edge = no_edge
            entry_price = no_entry
            entry_market_prob = no_entry

        if direction is None:
            return []

        entry_price = max(0.01, min(0.99, entry_price))

        # Kelly sizing
        win_prob = fv if direction == "YES" else (1 - fv)
        bet_dollars = _kelly_size(
            win_prob,
            entry_price,
            self._kelly_frac,
            self._bankroll,
            self._max_bet,
        )
        if bet_dollars < 1.0:
            return []

        contracts = max(1, int(bet_dollars / entry_price))

        # Mark as entered
        self._entered_markets[ticker] = True

        # Store signal metadata for later use in on_fill
        self._signal_metadata[ticker] = {
            "direction": direction,
            "edge": edge,
            "fair_value": fv,
            "spot": spot,
            "strike": strike,
            "ttx": ttx,
            "bet_dollars": bet_dollars,
            "entry_market_prob": entry_market_prob,
        }

        return [
            Signal(
                ticker=ticker,
                side="BID",
                price=entry_price,
                size=contracts,
                confidence=min(1.0, edge / 0.20),
                reason=f"{direction} edge={edge:.3f} fv={fv:.3f} spot={spot:.0f} strike={strike:.0f}",
                timestamp=frame.timestamp,
                metadata=self._signal_metadata[ticker],
            )
        ]

    def _check_exits(self, frame: BacktestFrame) -> List[Signal]:
        """Check ALL open positions for exit conditions (not just current ticker).

        Uses last-known market state for tickers not in current frame.
        """
        exit_signals = []
        current_ts = frame.timestamp

        # Check ALL open positions, not just the current ticker
        for ticker in list(self._positions.keys()):
            pos = self._positions[ticker]

            # Get market data (use cached if not in current frame)
            if ticker == frame.context.get("ticker"):
                ctx = frame.context  # Fresh data
            elif ticker in self._last_market_state:
                ctx = self._last_market_state[ticker]  # Cached data
            else:
                # No data available for this ticker, skip
                continue

            spot = ctx["kraken_spot"] if self._use_spot else ctx["kraken_avg_60s"]
            strike = ctx.get("floor_strike", 0)
            ttx = ctx.get("seconds_to_close", 0)

            # Current Kalshi prices
            yes_bid = ctx["yes_bid"]
            yes_ask = ctx["yes_ask"]
            no_bid = 100 - yes_ask
            no_ask = 100 - yes_bid

            # Recalculate fair value
            current_fair_value = _implied_probability(spot, strike, ttx, self._vol)

            # Determine exit price (bid for current position)
            if pos.side == "YES":
                exit_price_cents = yes_bid
            else:
                exit_price_cents = no_bid

            # Check intelligent exits
            exit_signal = None
            reason = ""

            if self._enable_intelligent and self._exit_manager:
                exit_signal = self._exit_manager.check_exit(
                    ticker=ticker,
                    current_yes_price=yes_bid,
                    current_no_price=no_bid,
                    current_fair_value=current_fair_value,
                )

                if exit_signal:
                    reason = exit_signal.reason
                    self.intelligent_exits += 1
                    self.exit_reasons[reason] = self.exit_reasons.get(reason, 0) + 1

            # Fallback to fixed time exit
            if not exit_signal:
                time_held = (current_ts - pos.entry_time).total_seconds()
                if time_held >= self._fixed_exit_delay:
                    reason = "fixed_time"
                    self.fixed_time_exits += 1
                    self.exit_reasons[reason] = self.exit_reasons.get(reason, 0) + 1
                else:
                    continue  # Don't exit this position yet

            # Generate exit signal
            exit_price = exit_price_cents / 100.0

            exit_signals.append(
                Signal(
                    ticker=ticker,
                    side="ASK",  # Sell to close
                    price=exit_price,
                    size=pos.size,
                    confidence=1.0,
                    reason=f"EXIT:{reason} held={(current_ts - pos.entry_time).total_seconds():.0f}s",
                    timestamp=current_ts,
                    metadata={
                        "exit_reason": reason,
                        "time_held_sec": (current_ts - pos.entry_time).total_seconds(),
                        "entry_price": pos.entry_price_cents / 100.0,
                        "exit_price": exit_price,
                        "pnl_cents": (exit_price_cents - pos.entry_price_cents)
                        * pos.size,
                    },
                )
            )

        return exit_signals

    def on_fill(self, fill: CoreFill) -> None:
        """Track position on fill."""
        ticker = fill.ticker

        # Entry fill
        if fill.side == "BID":
            # Get metadata from stored signal
            metadata = self._signal_metadata.get(ticker, {})
            direction = metadata.get("direction", "YES")
            fair_value = metadata.get("fair_value", 0.5)
            entry_market_prob = metadata.get("entry_market_prob", fill.price)

            # Register with intelligent exit manager
            if self._enable_intelligent and self._exit_manager:
                self._exit_manager.register_position(
                    ticker=ticker,
                    entry_time=fill.timestamp if fill.timestamp else datetime.now(),
                    entry_price=int(fill.price * 100),
                    entry_fair_value=fair_value,
                    entry_market_prob=entry_market_prob,
                    side=direction,
                    size=fill.size,
                )

            # Track position
            self._positions[ticker] = BacktestPosition(
                ticker=ticker,
                entry_time=fill.timestamp if fill.timestamp else datetime.now(),
                entry_price_cents=int(fill.price * 100),
                entry_fair_value=fair_value,
                entry_market_prob=entry_market_prob,
                side=direction,
                size=fill.size,
                entry_spot=metadata.get("spot", 0.0),
                entry_strike=metadata.get("strike", 0.0),
            )

            self.total_entries += 1

        # Exit fill
        elif fill.side == "ASK":
            if ticker in self._positions:
                del self._positions[ticker]

                if self._enable_intelligent and self._exit_manager:
                    self._exit_manager.remove_position(ticker)

                self._last_exit_ts[ticker] = (
                    fill.timestamp.timestamp() if fill.timestamp else 0.0
                )
                self.total_exits += 1

                # Clean up metadata
                if ticker in self._signal_metadata:
                    del self._signal_metadata[ticker]

    @property
    def name(self) -> str:
        return "crypto-latency-intelligent"

    def get_stats(self) -> Dict[str, Any]:
        """Return exit statistics."""
        return {
            "total_entries": self.total_entries,
            "total_exits": self.total_exits,
            "intelligent_exits": self.intelligent_exits,
            "fixed_time_exits": self.fixed_time_exits,
            "exit_reasons": self.exit_reasons.copy(),
            "open_positions": len(self._positions),
        }
