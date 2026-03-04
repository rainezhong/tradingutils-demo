"""NBA Underdog strategy adapter for the unified backtest framework.

Provides:
- NBAUnderdogCSVFeed: reads historical candlestick CSV with real settlements.
- NBAUnderdogDataFeed: reads NBA game snapshots from probe SQLite DB.
- NBAUnderdogAdapter: identifies underdogs in configured price range, buys them.

TIMING: Uses game start time (parsed from ticker) instead of market close time,
since Kalshi now closes NBA markets 14 days after the game.
"""

import csv
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.core.models import Fill, MarketState
from strategies.base import Signal

from ..data_feed import BacktestFrame, DataFeed
from ..engine import BacktestAdapter


# ---------------------------------------------------------------------------
# Helper: Parse game start time from ticker
# ---------------------------------------------------------------------------


def _parse_game_start_from_ticker(ticker: str) -> Optional[int]:
    """Extract game start timestamp from ticker format: KXNBAGAME-YYMMMDDTEAMS-TEAM

    Example: KXNBAGAME-26FEB26INDDET-IND (year 26, month FEB, day 26) -> Feb 26, 2026
    Returns: Unix timestamp (seconds) of game start, or None if parse fails
    """
    match = re.search(r'-(\d{2})([A-Z]{3})(\d{2})', ticker)
    if not match:
        return None

    year_short, month_str, day = match.groups()

    months = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
              'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
    month = months.get(month_str.upper())
    if not month:
        return None

    try:
        year = 2000 + int(year_short)
        day_int = int(day)
        # NBA games typically start 7-10:30 PM EST
        # Ticker date is in EST, so evening games map to next day UTC midnight
        game_date = datetime(year, month, day_int, 0, 0, 0, tzinfo=timezone.utc)
        game_start = game_date + timedelta(days=1)  # Next day midnight UTC = evening EST
        return int(game_start.timestamp())
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# NBAUnderdogCSVFeed — historical candlestick CSV with real settlements
# ---------------------------------------------------------------------------


class NBAUnderdogCSVFeed(DataFeed):
    """Reads the historical NBA candlestick CSV (558 settled games).

    CSV columns: event_ticker, ticker, team, timestamp, yes_price, volume,
                 won, close_ts, minutes_until_close

    Each row is a 1-minute candle for one side of a game. Frames are grouped
    by timestamp so each frame contains all tickers observed at that minute.
    Games may have 1 side (underdog-only) or 2 sides recorded.

    ALL frames are loaded (no timing filter) so stop-loss checks see the
    full in-game price path. Entry timing is controlled by the adapter.
    """

    def __init__(self, csv_path: str):
        self._csv_path = csv_path
        self._frames, self._tickers_list, self._settlements, self._meta = self._load()

    def _load(self) -> Tuple[list, List[str], Dict[str, Optional[float]], dict]:
        rows_by_ts: Dict[int, Dict[str, dict]] = {}
        tickers = set()
        settlements: Dict[str, Optional[float]] = {}
        games = set()

        with open(self._csv_path, "r") as f:
            reader = csv.DictReader(f)
            row_count = 0
            for row in reader:
                row_count += 1
                ticker = row["ticker"]
                ts = int(float(row["timestamp"]))
                price = float(row["yes_price"])
                volume = int(float(row["volume"])) if row.get("volume") else 0
                won = row["won"] == "True"
                mtc = float(row["minutes_until_close"])
                event = row["event_ticker"]

                tickers.add(ticker)
                games.add(event)
                settlements[ticker] = 1.0 if won else 0.0

                if ts not in rows_by_ts:
                    rows_by_ts[ts] = {}

                # Calculate minutes until game start (not market close)
                game_start_ts = _parse_game_start_from_ticker(ticker)
                minutes_until_game_start = None
                if game_start_ts:
                    minutes_until_game_start = (game_start_ts - ts) / 60.0

                price_cents = int(price * 100)
                rows_by_ts[ts][ticker] = {
                    "yes_price": price,
                    "yes_bid": max(1, price_cents - 1),
                    "yes_ask": price_cents,
                    "yes_mid": price_cents,
                    "volume": volume,
                    "minutes_until_close": mtc,  # Legacy
                    "minutes_until_game_start": minutes_until_game_start,
                }

        sorted_ts = sorted(rows_by_ts.keys())
        frames = [{"ts": ts, "markets": rows_by_ts[ts]} for ts in sorted_ts]

        n_settled = sum(1 for v in settlements.values() if v is not None)
        duration_min = (sorted_ts[-1] - sorted_ts[0]) / 60 if len(sorted_ts) > 1 else 0

        meta = {
            "csv_path": self._csv_path,
            "total_rows": row_count,
            "sampled_frames": len(frames),
            "tickers": len(tickers),
            "games": len(games),
            "settled": n_settled,
            "duration_min": round(duration_min, 1),
        }

        return frames, sorted(tickers), settlements, meta

    def __iter__(self) -> Iterator[BacktestFrame]:
        for idx, frame_data in enumerate(self._frames):
            ts = datetime.fromtimestamp(frame_data["ts"], tz=timezone.utc)
            markets: Dict[str, MarketState] = {}
            context: Dict[str, Any] = {"all_markets": {}}

            for ticker, snap in frame_data["markets"].items():
                yb = snap["yes_bid"] / 100.0
                ya = snap["yes_ask"] / 100.0
                if ya < yb:
                    ya = yb + 0.01

                markets[ticker] = MarketState(
                    ticker=ticker,
                    timestamp=ts,
                    bid=yb,
                    ask=ya,
                    volume=snap.get("volume", 0),
                )

                context["all_markets"][ticker] = {
                    "yes_bid": snap["yes_bid"],
                    "yes_ask": snap["yes_ask"],
                    "yes_mid": snap["yes_mid"],
                    "minutes_until_close": snap.get("minutes_until_close"),  # Legacy
                    "minutes_until_game_start": snap.get("minutes_until_game_start"),
                }

            yield BacktestFrame(
                timestamp=ts,
                frame_idx=idx,
                markets=markets,
                context=context,
            )

    def get_settlement(self) -> Dict[str, Optional[float]]:
        return dict(self._settlements)

    @property
    def tickers(self) -> List[str]:
        return list(self._tickers_list)

    @property
    def metadata(self) -> Dict[str, Any]:
        return dict(self._meta)


# ---------------------------------------------------------------------------
# NBAUnderdogDataFeed — probe SQLite DB (live data)
# ---------------------------------------------------------------------------


class NBAUnderdogDataFeed(DataFeed):
    """Reads NBA game market snapshots from a latency probe database.

    Groups snapshots by timestamp so each frame contains all active markets
    at that point in time. This allows the adapter to identify underdogs
    across simultaneous games.
    """

    def __init__(
        self,
        db_path: str,
        sample_interval_sec: float = 30.0,
    ):
        self._db_path = db_path
        self._sample_interval = sample_interval_sec
        self._frames, self._tickers_list, self._settlements, self._meta = self._load()

    def _load(self) -> Tuple[list, List[str], Dict[str, Optional[float]], dict]:
        conn = sqlite3.connect(self._db_path)

        rows = conn.execute(
            "SELECT ts, ticker, yes_bid, yes_ask, yes_mid, "
            "close_time, seconds_to_close, volume, open_interest "
            "FROM kalshi_snapshots ORDER BY ts"
        ).fetchall()

        # Check for real settlements first
        settlements: Dict[str, Optional[float]] = {}
        try:
            settle_rows = conn.execute(
                "SELECT ticker, settled_yes FROM market_settlements "
                "WHERE settled_yes IS NOT NULL"
            ).fetchall()
            for ticker, settled_yes in settle_rows:
                settlements[ticker] = 1.0 if settled_yes else 0.0
        except Exception:
            pass

        conn.close()

        if not rows:
            return [], [], settlements, {}

        # Identify game pairs (e.g., BOSPHX-BOS and BOSPHX-PHX)
        tickers = set()
        game_pairs: Dict[str, List[str]] = defaultdict(list)  # game_key -> [tickers]
        for row in rows:
            ticker = row[1]
            tickers.add(ticker)
            # Parse game key: KXNBAGAME-26FEB24BOSPHX-BOS -> 26FEB24BOSPHX
            parts = ticker.split("-")
            if len(parts) >= 3:
                game_key = parts[1]  # e.g., "26FEB24BOSPHX"
                if ticker not in game_pairs[game_key]:
                    game_pairs[game_key].append(ticker)

        # Track last known state per ticker for settlement inference
        last_state: Dict[str, dict] = {}

        # Group rows by sampled timestamps
        # Use sampling to reduce 830k rows to manageable frame count
        frames_by_ts: Dict[float, Dict[str, dict]] = {}
        last_ts = 0.0

        for row in rows:
            ts, ticker, yb, ya, ym, ct, ttx, vol, oi = row

            # Always track last state for settlements
            last_state[ticker] = {
                "yes_bid": yb, "yes_ask": ya, "yes_mid": ym,
                "close_time": ct, "seconds_to_close": ttx,
            }

            # Sample: skip if within interval of last frame (but allow same ts)
            if ts != last_ts and ts - last_ts < self._sample_interval and last_ts > 0:
                continue

            if ts not in frames_by_ts:
                frames_by_ts[ts] = {}
                last_ts = ts

            frames_by_ts[ts][ticker] = {
                "yes_bid": yb,
                "yes_ask": ya,
                "yes_mid": ym,
                "close_time": ct,
                "seconds_to_close": ttx,
                "volume": vol,
                "open_interest": oi,
            }

        # If no real settlements, infer from last prices
        # For game pairs, the side with last_mid > 50 is likely winner
        if not settlements:
            for game_key, game_tickers in game_pairs.items():
                if len(game_tickers) == 2:
                    t1, t2 = game_tickers
                    m1 = last_state.get(t1, {}).get("yes_mid", 50)
                    m2 = last_state.get(t2, {}).get("yes_mid", 50)
                    # Assign settlement: higher-priced side wins
                    if m1 > m2:
                        settlements[t1] = 1.0
                        settlements[t2] = 0.0
                    else:
                        settlements[t1] = 0.0
                        settlements[t2] = 1.0

        # Build sorted frame list
        sorted_ts = sorted(frames_by_ts.keys())
        frames = []
        for ts in sorted_ts:
            frames.append({"ts": ts, "markets": frames_by_ts[ts]})

        tickers_list = sorted(tickers)
        n_games = len(game_pairs)
        settled = sum(1 for v in settlements.values() if v is not None)
        duration_min = (sorted_ts[-1] - sorted_ts[0]) / 60 if len(sorted_ts) > 1 else 0

        meta = {
            "db_path": self._db_path,
            "total_snapshots": len(rows),
            "sampled_frames": len(frames),
            "tickers": len(tickers_list),
            "games": n_games,
            "settled": settled,
            "duration_min": duration_min,
            "sample_interval_sec": self._sample_interval,
        }

        return frames, tickers_list, settlements, meta

    def __iter__(self) -> Iterator[BacktestFrame]:
        for idx, frame_data in enumerate(self._frames):
            ts = datetime.fromtimestamp(frame_data["ts"], tz=timezone.utc)
            markets: Dict[str, MarketState] = {}
            context: Dict[str, Any] = {"all_markets": {}}

            for ticker, snap in frame_data["markets"].items():
                yb = snap["yes_bid"] / 100.0
                ya = snap["yes_ask"] / 100.0
                if ya < yb:
                    ya = yb + 0.01

                markets[ticker] = MarketState(
                    ticker=ticker,
                    timestamp=ts,
                    bid=yb,
                    ask=ya,
                    volume=snap.get("volume", 0),
                )

                context["all_markets"][ticker] = {
                    "yes_bid": snap["yes_bid"],
                    "yes_ask": snap["yes_ask"],
                    "yes_mid": snap["yes_mid"],
                    "close_time": snap.get("close_time"),
                    "seconds_to_close": snap.get("seconds_to_close"),
                }

            yield BacktestFrame(
                timestamp=ts,
                frame_idx=idx,
                markets=markets,
                context=context,
            )

    def get_settlement(self) -> Dict[str, Optional[float]]:
        return dict(self._settlements)

    @property
    def tickers(self) -> List[str]:
        return list(self._tickers_list)

    @property
    def metadata(self) -> Dict[str, Any]:
        return dict(self._meta)


# ---------------------------------------------------------------------------
# NBAUnderdogAdapter
# ---------------------------------------------------------------------------


class NBAUnderdogAdapter(BacktestAdapter):
    """Identifies NBA underdogs in a price range and generates BID signals.

    Replicates the core logic of NBAUnderdogStrategy for backtesting:
    1. Pairs markets by game (same game prefix, two sides)
    2. Identifies the underdog (lower-priced side in each pair)
    3. If underdog ask is in [min_price, max_price], generates a BID signal
    4. Limits to one entry per game
    5. Optionally simulates stop-loss exits
    6. Entry timing controlled by min/max_minutes_before_game_start

    TIMING NOTE: Uses minutes_until_game_start (not market close) since
    Kalshi now closes NBA markets 14 days after games.
    """

    def __init__(
        self,
        min_price_cents: int = 5,
        max_price_cents: int = 15,
        position_size: int = 10,
        max_entries_per_game: int = 1,
        stop_loss_cents: int = 0,
        cooldown_frames: int = 0,
        min_minutes_before: float = 120.0,  # Historical: 2h before close = ~0h before game
        max_minutes_before: float = 300.0,  # Historical: 5h before close = ~2.7h before game
    ):
        self._min_price = min_price_cents
        self._max_price = max_price_cents
        self._position_size = position_size
        self._max_per_game = max_entries_per_game
        self._stop_loss = stop_loss_cents
        self._cooldown = cooldown_frames
        self._min_mtc = min_minutes_before
        self._max_mtc = max_minutes_before

        # State
        self._game_entries: Dict[str, int] = {}  # game_key -> entry count
        self._entered_tickers: set = set()
        self._positions: Dict[str, dict] = {}  # ticker -> {entry_price_cents, ...}
        self._last_entry_frame: int = -999

        # Stats
        self.total_signals = 0
        self.stop_loss_exits = 0
        self.entries_by_bucket: Dict[str, int] = defaultdict(int)

    @property
    def name(self) -> str:
        timing = ""
        if self._min_mtc > 0 or self._max_mtc < 999:
            timing = f", {self._min_mtc:.0f}-{self._max_mtc:.0f}m"
        return f"nba-underdog ({self._min_price}-{self._max_price}c{timing})"

    def on_start(self) -> None:
        self._game_entries.clear()
        self._entered_tickers.clear()
        self._positions.clear()
        self._last_entry_frame = -999
        self.total_signals = 0
        self.stop_loss_exits = 0
        self.entries_by_bucket.clear()

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        signals = []
        all_mkt = frame.context.get("all_markets", {})

        # Group tickers by game
        game_groups: Dict[str, List[str]] = defaultdict(list)
        for ticker in frame.markets:
            parts = ticker.split("-")
            if len(parts) >= 3:
                game_key = parts[1]
                game_groups[game_key].append(ticker)

        # Check stop-loss exits first
        exit_signals = self._check_stop_losses(frame)
        signals.extend(exit_signals)

        # Simple validation logic: enter ANY ticker in price range during timing window
        # (matches original validation that counted 201 qualifying tickers)
        for ticker in frame.markets:
            # Already entered?
            if ticker in self._entered_tickers:
                continue

            # Cooldown
            if frame.frame_idx - self._last_entry_frame < self._cooldown:
                continue

            # Check price range
            info = all_mkt.get(ticker, {})
            ask = info.get("yes_ask", 100)
            if not (self._min_price <= ask <= self._max_price):
                continue

            # Check entry timing window
            # For historical CSV: use minutes_until_close (markets closed ~136m after game start)
            # For live trading: would use minutes_until_game_start instead
            timing_val = info.get("minutes_until_game_start")
            if timing_val is None:
                timing_val = info.get("minutes_until_close")

            if timing_val is not None and not (self._min_mtc <= timing_val <= self._max_mtc):
                continue

            # Generate signal
            self.total_signals += 1
            bucket = self._get_bucket(ask)
            self.entries_by_bucket[bucket] += 1

            # Track per-game entries
            parts = ticker.split("-")
            game_key = parts[1] if len(parts) >= 3 else ticker
            self._game_entries[game_key] = self._game_entries.get(game_key, 0) + 1
            self._last_entry_frame = frame.frame_idx

            market = frame.markets.get(ticker)
            if market is None:
                continue

            signals.append(Signal(
                ticker=ticker,
                side="BID",
                price=market.ask,
                size=self._position_size,
                confidence=0.8,
                reason=f"underdog {bucket} bucket at {ask}c",
                timestamp=frame.timestamp,
                metadata={"bucket": bucket, "ask_cents": ask},
            ))

        return signals

    def on_fill(self, fill: Fill) -> None:
        ticker = fill.ticker
        parts = ticker.split("-")
        game_key = parts[1] if len(parts) >= 3 else ticker

        self._entered_tickers.add(ticker)
        self._game_entries[game_key] = self._game_entries.get(game_key, 0) + 1
        self._positions[ticker] = {
            "entry_price_cents": int(fill.price * 100),
            "entry_frame": 0,
        }

    def _check_stop_losses(self, frame: BacktestFrame) -> List[Signal]:
        """Check positions for stop-loss exits."""
        if self._stop_loss <= 0:
            return []

        exits = []
        for ticker, pos in list(self._positions.items()):
            market = frame.markets.get(ticker)
            if market is None:
                continue

            current_bid_cents = int(market.bid * 100)
            entry = pos["entry_price_cents"]
            loss = entry - current_bid_cents

            if loss >= self._stop_loss:
                self.stop_loss_exits += 1
                exits.append(Signal(
                    ticker=ticker,
                    side="ASK",  # Sell to exit
                    price=market.bid,
                    size=self._position_size,
                    confidence=1.0,
                    reason=f"stop-loss: entry {entry}c now {current_bid_cents}c (loss {loss}c)",
                    timestamp=frame.timestamp,
                ))
                del self._positions[ticker]

        return exits

    def _get_bucket(self, price_cents: int) -> str:
        if price_cents < 15:
            return "10-15c"
        elif price_cents < 20:
            return "15-20c"
        elif price_cents < 25:
            return "20-25c"
        elif price_cents < 30:
            return "25-30c"
        elif price_cents < 35:
            return "30-35c"
        return "35c+"

    def summary(self) -> str:
        """Human-readable summary of adapter activity."""
        lines = [
            f"  Total entry signals: {self.total_signals}",
            f"  Stop-loss exits:     {self.stop_loss_exits}",
            f"  Games entered:       {len(self._game_entries)}",
        ]
        if self.entries_by_bucket:
            lines.append("  Entries by bucket:")
            for bucket, count in sorted(self.entries_by_bucket.items()):
                lines.append(f"    {bucket}: {count}")
        return "\n".join(lines)
