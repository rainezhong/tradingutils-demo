"""NBA Game Recording System — captures all Kalshi markets for an NBA game.

Records game outcome (paired market), point spreads, total points,
and their first-half variants alongside live NBA score data.

Usage:
    >>> from core.exchange_client import KalshiExchangeClient
    >>> from core.recorder.record_kalshiNBA import NBAGameRecorder
    >>>
    >>> client = KalshiExchangeClient.from_env()
    >>> await client.connect()
    >>>
    >>> recorder = NBAGameRecorder(
    ...     home_team="LAL",
    ...     away_team="BOS",
    ...     date="26FEB10",
    ...     poll_interval_ms=500,
    ... )
    >>> recording = await recorder.start_async(client)
    >>> recording.save("data/recordings/LAL_vs_BOS_20260210.json")
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import json
from pathlib import Path

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient
from core.nba_utils import get_game_progress
from .recorder_types import MarketFrame, PairMarketFrame, OrderbookSnapshot

logger = logging.getLogger(__name__)


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class NBAGameSnapshot:
    """Single point-in-time capture of all data for an NBA game.

    Combines live score data with all associated Kalshi markets:
    game outcome, spreads, totals, and their first-half variants.
    """

    timestamp: int  # ms epoch

    # Score context (from nba_utils)
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    period_name: str  # "Q1", "Halftime", "Final", etc.
    game_status: str  # "pregame", "live", "halftime", "final"
    time_remaining: str

    # Game outcome — paired market (home wins / away wins)
    game_market: Optional[PairMarketFrame] = None

    # Spread markets (e.g., LAL -3.5, LAL -4.5, ...)
    spread_markets: List[MarketFrame] = field(default_factory=list)
    spread_1h_markets: List[MarketFrame] = field(default_factory=list)

    # Total points markets (e.g., O215.5, O216.5, ...)
    total_markets: List[MarketFrame] = field(default_factory=list)
    total_1h_markets: List[MarketFrame] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "period": self.period,
            "period_name": self.period_name,
            "game_status": self.game_status,
            "time_remaining": self.time_remaining,
            "game_market": self.game_market.to_dict() if self.game_market else None,
            "spread_markets": [m.to_dict() for m in self.spread_markets],
            "spread_1h_markets": [m.to_dict() for m in self.spread_1h_markets],
            "total_markets": [m.to_dict() for m in self.total_markets],
            "total_1h_markets": [m.to_dict() for m in self.total_1h_markets],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NBAGameSnapshot":
        game_market = None
        if data.get("game_market"):
            game_market = PairMarketFrame.from_dict(data["game_market"])

        return cls(
            timestamp=data["timestamp"],
            home_team=data["home_team"],
            away_team=data["away_team"],
            home_score=data["home_score"],
            away_score=data["away_score"],
            period=data["period"],
            period_name=data["period_name"],
            game_status=data["game_status"],
            time_remaining=data["time_remaining"],
            game_market=game_market,
            spread_markets=[
                MarketFrame.from_dict(m) for m in data.get("spread_markets", [])
            ],
            spread_1h_markets=[
                MarketFrame.from_dict(m) for m in data.get("spread_1h_markets", [])
            ],
            total_markets=[
                MarketFrame.from_dict(m) for m in data.get("total_markets", [])
            ],
            total_1h_markets=[
                MarketFrame.from_dict(m) for m in data.get("total_1h_markets", [])
            ],
        )

    @property
    def score_diff(self) -> int:
        """Home score - away score (positive = home leading)."""
        return self.home_score - self.away_score

    @property
    def total_points(self) -> int:
        return self.home_score + self.away_score

    @property
    def all_market_count(self) -> int:
        """Total number of markets captured in this snapshot."""
        count = 0
        if self.game_market:
            count += 1
        count += len(self.spread_markets)
        count += len(self.spread_1h_markets)
        count += len(self.total_markets)
        count += len(self.total_1h_markets)
        return count


@dataclass
class NBAGameRecordingMetadata:
    """Metadata for an NBA game recording session."""

    home_team: str
    away_team: str
    date: str  # Kalshi date format "26FEB10"
    game_date: str  # YYYY-MM-DD
    recorded_at: str  # ISO timestamp
    poll_interval_ms: int = 500
    orderbook_depth: int = 10
    total_snapshots: int = 0
    final_game_status: Optional[str] = None
    final_home_score: Optional[int] = None
    final_away_score: Optional[int] = None

    # All tickers discovered for this game
    game_tickers: List[str] = field(default_factory=list)
    spread_tickers: List[str] = field(default_factory=list)
    spread_1h_tickers: List[str] = field(default_factory=list)
    total_tickers: List[str] = field(default_factory=list)
    total_1h_tickers: List[str] = field(default_factory=list)

    @property
    def matchup(self) -> str:
        return f"{self.away_team} @ {self.home_team}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NBAGameRecordingMetadata":
        return cls(**data)


@dataclass
class NBAGameRecording:
    """Complete recording of all Kalshi markets for an NBA game.

    Contains metadata and a time-ordered list of NBAGameSnapshots.
    """

    metadata: NBAGameRecordingMetadata
    snapshots: List[NBAGameSnapshot] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "snapshots": [s.to_dict() for s in self.snapshots],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NBAGameRecording":
        metadata = NBAGameRecordingMetadata.from_dict(data["metadata"])
        snapshots = [NBAGameSnapshot.from_dict(s) for s in data.get("snapshots", [])]
        return cls(metadata=metadata, snapshots=snapshots)

    def save(self, filepath: str) -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "NBAGameRecording":
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def add_snapshot(self, snapshot: NBAGameSnapshot) -> None:
        self.snapshots.append(snapshot)
        self.metadata.total_snapshots = len(self.snapshots)

    @property
    def duration_seconds(self) -> float:
        if len(self.snapshots) < 2:
            return 0.0
        return (self.snapshots[-1].timestamp - self.snapshots[0].timestamp) / 1000.0

    def __len__(self) -> int:
        return len(self.snapshots)

    def __iter__(self):
        return iter(self.snapshots)

    def __getitem__(self, idx: int) -> NBAGameSnapshot:
        return self.snapshots[idx]


# =============================================================================
# Recorder
# =============================================================================


class NBAGameRecorder:
    """Records all Kalshi markets for one NBA game alongside live score data.

    On each poll:
    1. Fetches game progress (score, period, status) via nba_utils
    2. Fetches all associated Kalshi markets (game, spread, total, 1H variants)
    3. Optionally fetches orderbook depth for each market
    4. Bundles into a single NBAGameSnapshot

    Stops when the game reaches "final" status.

    Example:
        >>> recorder = NBAGameRecorder("LAL", "BOS", "26FEB10")
        >>> recording = await recorder.start_async(client)
        >>> recording.save("data/recordings/LAL_vs_BOS.json")
    """

    def __init__(
        self,
        home_team: str,
        away_team: str,
        date: str,
        poll_interval_ms: int = 500,
        orderbook_depth: int = 10,
    ):
        """Initialize the NBA game recorder.

        Args:
            home_team: Home team tricode (e.g., "LAL")
            away_team: Away team tricode (e.g., "BOS")
            date: Kalshi date format (e.g., "26FEB10")
            poll_interval_ms: Polling interval in milliseconds (default 500)
            orderbook_depth: Orderbook levels per side (default 10)
        """
        self.home_team = home_team.upper()
        self.away_team = away_team.upper()
        self.date = date
        self.poll_interval_ms = poll_interval_ms
        self.orderbook_depth = orderbook_depth

        self._recording = NBAGameRecording(
            metadata=NBAGameRecordingMetadata(
                home_team=self.home_team,
                away_team=self.away_team,
                date=date,
                game_date=datetime.now().strftime("%Y-%m-%d"),
                recorded_at=datetime.now().isoformat(),
                poll_interval_ms=poll_interval_ms,
                orderbook_depth=orderbook_depth,
            )
        )

        self._stop_event = threading.Event()
        self._recording_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._tickers_discovered = False

    def get_recording(self) -> NBAGameRecording:
        with self._lock:
            return self._recording

    async def start_async(
        self,
        client: KalshiExchangeClient,
        max_duration_seconds: Optional[int] = None,
    ) -> NBAGameRecording:
        """Start recording asynchronously. Runs until game is final or stopped.

        Args:
            client: KalshiExchangeClient instance (needs get_nba_game_markets + get_orderbook)
            max_duration_seconds: Optional max recording duration

        Returns:
            The completed NBAGameRecording
        """
        poll_interval = self.poll_interval_ms / 1000.0
        start_time = time.time()

        logger.info(f"Starting NBA game recording: {self.away_team} @ {self.home_team}")
        logger.info(
            f"Date: {self.date} | Poll: {self.poll_interval_ms}ms | OB depth: {self.orderbook_depth}"
        )

        self._stop_event.clear()

        # Discover tickers on first run
        await self._discover_tickers(client)

        while not self._stop_event.is_set():
            try:
                snapshot = await self._capture_snapshot(client)

                if snapshot:
                    with self._lock:
                        self._recording.add_snapshot(snapshot)

                    # Log progress every 30 snapshots
                    if len(self._recording) % 30 == 0:
                        logger.info(
                            f"[{len(self._recording)} snaps] "
                            f"{snapshot.away_team} {snapshot.away_score} - "
                            f"{snapshot.home_team} {snapshot.home_score} | "
                            f"{snapshot.period_name} {snapshot.time_remaining} | "
                            f"{snapshot.all_market_count} markets"
                        )

                    # Stop on game final
                    if snapshot.game_status == "final":
                        logger.info(
                            f"Game final: {snapshot.away_team} {snapshot.away_score} - "
                            f"{snapshot.home_team} {snapshot.home_score}"
                        )
                        self._recording.metadata.final_game_status = "final"
                        self._recording.metadata.final_home_score = snapshot.home_score
                        self._recording.metadata.final_away_score = snapshot.away_score
                        break

                # Check max duration
                if (
                    max_duration_seconds
                    and (time.time() - start_time) > max_duration_seconds
                ):
                    logger.info(f"Max duration reached ({max_duration_seconds}s)")
                    break

            except Exception as e:
                logger.error(f"Error capturing snapshot: {e}", exc_info=True)

            await asyncio.sleep(poll_interval)

        logger.info(
            f"Recording complete: {len(self._recording)} snapshots, "
            f"{self._recording.duration_seconds:.1f}s duration"
        )
        return self._recording

    def start(self, client: KalshiExchangeClient) -> NBAGameRecording:
        """Start recording synchronously (blocking)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.start_async(client))
        finally:
            loop.close()

    def start_background(self, client: KalshiExchangeClient) -> None:
        """Start recording in a background thread."""
        if self._recording_thread and self._recording_thread.is_alive():
            raise RuntimeError("Recording already in progress")

        self._stop_event.clear()
        self._recording_thread = threading.Thread(
            target=self.start,
            args=(client,),
            daemon=True,
        )
        self._recording_thread.start()

    def stop(self) -> NBAGameRecording:
        """Stop recording and return the result."""
        self._stop_event.set()
        if self._recording_thread:
            self._recording_thread.join(timeout=5.0)
        return self.get_recording()

    def save(self, filepath: str) -> None:
        """Save recording to JSON file."""
        recording = self.get_recording()
        recording.save(filepath)
        logger.info(f"Saved recording to {filepath} ({len(recording)} snapshots)")

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    async def _discover_tickers(self, client: KalshiExchangeClient) -> None:
        """Fetch all markets for this game and categorize tickers."""
        logger.info(
            f"Discovering tickers for {self.away_team} @ {self.home_team} ({self.date})..."
        )

        try:
            all_markets = await client.get_nba_game_markets(
                team1=self.away_team,
                team2=self.home_team,
                date=self.date,
            )

            meta = self._recording.metadata

            for market in all_markets:
                ticker = market.ticker
                event = market.event_ticker

                if "KXNBAGAME" in event:
                    meta.game_tickers.append(ticker)
                elif "KXNBA1HSPREAD" in event:
                    meta.spread_1h_tickers.append(ticker)
                elif "KXNBASPREAD" in event:
                    meta.spread_tickers.append(ticker)
                elif "KXNBA1HTOTAL" in event:
                    meta.total_1h_tickers.append(ticker)
                elif "KXNBATOTAL" in event:
                    meta.total_tickers.append(ticker)
                else:
                    logger.debug(f"Unknown series for ticker {ticker}: {event}")

            # Sort for consistency
            meta.spread_tickers.sort()
            meta.spread_1h_tickers.sort()
            meta.total_tickers.sort()
            meta.total_1h_tickers.sort()

            logger.info(
                f"Discovered tickers: "
                f"{len(meta.game_tickers)} game, "
                f"{len(meta.spread_tickers)} spread, "
                f"{len(meta.spread_1h_tickers)} spread_1h, "
                f"{len(meta.total_tickers)} total, "
                f"{len(meta.total_1h_tickers)} total_1h"
            )

            self._tickers_discovered = True

        except Exception as e:
            logger.error(f"Error discovering tickers: {e}", exc_info=True)

    async def _capture_snapshot(
        self,
        client: KalshiExchangeClient,
    ) -> Optional[NBAGameSnapshot]:
        """Capture a single snapshot of the full game state."""
        now_ms = int(time.time() * 1000)
        meta = self._recording.metadata

        # --- Score data ---
        progress = get_game_progress(self.away_team, self.home_team)

        if progress:
            home_score = progress.home_score
            away_score = progress.away_score
            period = progress.period
            period_name = progress.period_name
            game_status = progress.game_status
            time_remaining = progress.time_remaining
        else:
            # Game not found yet (pregame) — use defaults
            home_score = 0
            away_score = 0
            period = 0
            period_name = "Pregame"
            game_status = "pregame"
            time_remaining = ""

        # --- Market data ---
        game_market = await self._build_game_market(client, now_ms, meta.game_tickers)
        spread_markets = await self._build_market_frames(
            client, now_ms, meta.spread_tickers
        )
        spread_1h_markets = await self._build_market_frames(
            client, now_ms, meta.spread_1h_tickers
        )
        total_markets = await self._build_market_frames(
            client, now_ms, meta.total_tickers
        )
        total_1h_markets = await self._build_market_frames(
            client, now_ms, meta.total_1h_tickers
        )

        return NBAGameSnapshot(
            timestamp=now_ms,
            home_team=self.home_team,
            away_team=self.away_team,
            home_score=home_score,
            away_score=away_score,
            period=period,
            period_name=period_name,
            game_status=game_status,
            time_remaining=time_remaining,
            game_market=game_market,
            spread_markets=spread_markets,
            spread_1h_markets=spread_1h_markets,
            total_markets=total_markets,
            total_1h_markets=total_1h_markets,
        )

    async def _build_game_market(
        self,
        client: KalshiExchangeClient,
        timestamp: int,
        game_tickers: List[str],
    ) -> Optional[PairMarketFrame]:
        """Build a PairMarketFrame from the game outcome tickers.

        Expects exactly 2 game tickers (home win / away win).
        """
        if len(game_tickers) < 2:
            return None

        try:
            # Determine which ticker is home, which is away
            # Kalshi game tickers end with team code: KXNBAGAME-26FEB10-SASLAL-LAL
            home_ticker = None
            away_ticker = None

            for ticker in game_tickers:
                parts = ticker.split("-")
                if len(parts) >= 4:
                    team_code = parts[-1]
                    if team_code == self.home_team:
                        home_ticker = ticker
                    elif team_code == self.away_team:
                        away_ticker = ticker

            if not home_ticker or not away_ticker:
                # Fallback: just use first two
                home_ticker = game_tickers[0]
                away_ticker = game_tickers[1]

            home_market = await client.request_market(home_ticker)
            away_market = await client.request_market(away_ticker)

            # Fetch orderbooks
            home_ob = await self._fetch_orderbook(client, home_ticker)
            away_ob = await self._fetch_orderbook(client, away_ticker)

            return PairMarketFrame(
                timestamp=timestamp,
                yes_ticker=home_ticker,
                no_ticker=away_ticker,
                yes_bid=home_market.yes_bid or 0,
                yes_ask=home_market.yes_ask or 100,
                no_bid=away_market.yes_bid or 0,
                no_ask=away_market.yes_ask or 100,
                volume=(home_market.volume or 0) + (away_market.volume or 0),
                market_status=home_market.status or "open",
                yes_orderbook=home_ob,
                no_orderbook=away_ob,
            )
        except Exception as e:
            logger.warning(f"Error building game market: {e}")
            return None

    async def _build_market_frames(
        self,
        client: KalshiExchangeClient,
        timestamp: int,
        tickers: List[str],
    ) -> List[MarketFrame]:
        """Build MarketFrames for a list of tickers (spread/total lines)."""
        frames = []

        for ticker in tickers:
            try:
                market = await client.request_market(ticker)
                ob = await self._fetch_orderbook(client, ticker)

                frame = MarketFrame(
                    timestamp=timestamp,
                    ticker=ticker,
                    yes_bid=market.yes_bid or 0,
                    yes_ask=market.yes_ask or 100,
                    volume=market.volume or 0,
                    market_status=market.status or "open",
                    orderbook=ob,
                )
                frames.append(frame)
            except Exception as e:
                logger.warning(f"Error fetching market {ticker}: {e}")

        return frames

    async def _fetch_orderbook(
        self,
        client: KalshiExchangeClient,
        ticker: str,
    ) -> Optional[OrderbookSnapshot]:
        """Fetch orderbook depth for a single ticker."""
        if self.orderbook_depth <= 0:
            return None

        try:
            ob_data = await client.get_orderbook(ticker, depth=self.orderbook_depth)
            return OrderbookSnapshot(
                yes=ob_data.get("yes", []),
                no=ob_data.get("no", []),
            )
        except Exception as e:
            logger.warning(f"Failed to fetch orderbook for {ticker}: {e}")
            return None
