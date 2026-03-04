"""NBA strategy adapters for the unified backtest framework.

Provides:
- NBADataFeed: reads NBAGameRecorder JSON recordings into BacktestFrames.
- NBAMispricingAdapter: wraps ScoreAnalyzer for early-game mispricing.
- BlowoutAdapter: wraps LateGameBlowoutStrategy._check_entry.
- TotalPointsAdapter: wraps TotalPointsStrategy.check_entry.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from src.core.models import MarketState
from strategies.base import Signal

from ..data_feed import BacktestFrame, DataFeed
from ..engine import BacktestAdapter


# ---------------------------------------------------------------------------
# NBADataFeed
# ---------------------------------------------------------------------------


class NBADataFeed(DataFeed):
    """Converts an NBA game recording JSON into BacktestFrames.

    Expects the format produced by ``src.simulation.nba_recorder.NBAGameRecorder``.
    """

    def __init__(self, recording_path: str):
        self._path = recording_path
        self._data = self._load(recording_path)
        self._metadata_dict = self._data.get("metadata", {})
        self._frames_raw = self._data.get("frames", [])

    @staticmethod
    def _load(path: str) -> dict:
        with open(path, "r") as f:
            return json.load(f)

    # --- DataFeed interface ---

    def __iter__(self) -> Iterator[BacktestFrame]:
        home_ticker = self._metadata_dict.get("home_ticker", "HOME")
        away_ticker = self._metadata_dict.get("away_ticker", "AWAY")

        for idx, raw in enumerate(self._frames_raw):
            ts = datetime.fromtimestamp(raw["timestamp"], tz=timezone.utc)

            markets: Dict[str, MarketState] = {}

            # Home win market
            home_bid = raw.get("home_bid", 0.0)
            home_ask = raw.get("home_ask", 0.0)
            if home_ask >= home_bid and home_ask > 0:
                markets[home_ticker] = MarketState(
                    ticker=home_ticker,
                    timestamp=ts,
                    bid=home_bid,
                    ask=home_ask,
                    volume=raw.get("volume", 0),
                )

            # Away win market
            away_bid = raw.get("away_bid", 0.0)
            away_ask = raw.get("away_ask", 0.0)
            if away_ask >= away_bid and away_ask > 0:
                markets[away_ticker] = MarketState(
                    ticker=away_ticker,
                    timestamp=ts,
                    bid=away_bid,
                    ask=away_ask,
                    volume=raw.get("volume", 0),
                )

            context = {
                "home_score": raw.get("home_score", 0),
                "away_score": raw.get("away_score", 0),
                "period": raw.get("period", 1),
                "time_remaining": raw.get("time_remaining", "12:00"),
                "game_status": raw.get("game_status", ""),
                "home_ticker": home_ticker,
                "away_ticker": away_ticker,
                "game_id": self._metadata_dict.get("game_id", ""),
                "total_orderbooks": raw.get("total_orderbooks"),
            }

            yield BacktestFrame(
                timestamp=ts,
                frame_idx=idx,
                markets=markets,
                context=context,
            )

    def get_settlement(self) -> Dict[str, Optional[float]]:
        home_ticker = self._metadata_dict.get("home_ticker", "HOME")
        away_ticker = self._metadata_dict.get("away_ticker", "AWAY")

        final_home = self._metadata_dict.get("final_home_score")
        final_away = self._metadata_dict.get("final_away_score")

        # Fall back to last frame
        if final_home is None and self._frames_raw:
            final_home = self._frames_raw[-1].get("home_score", 0)
        if final_away is None and self._frames_raw:
            final_away = self._frames_raw[-1].get("away_score", 0)

        if final_home is not None and final_away is not None:
            if final_home > final_away:
                return {home_ticker: 1.0, away_ticker: 0.0}
            elif final_away > final_home:
                return {home_ticker: 0.0, away_ticker: 1.0}

        return {home_ticker: None, away_ticker: None}

    @property
    def tickers(self) -> List[str]:
        return [
            self._metadata_dict.get("home_ticker", "HOME"),
            self._metadata_dict.get("away_ticker", "AWAY"),
        ]

    @property
    def metadata(self) -> Dict[str, Any]:
        m = self._metadata_dict
        return {
            "game_id": m.get("game_id", ""),
            "home_team": m.get("home_team", ""),
            "away_team": m.get("away_team", ""),
            "date": m.get("date", ""),
            "total_frames": len(self._frames_raw),
        }


# ---------------------------------------------------------------------------
# NBAMispricingAdapter
# ---------------------------------------------------------------------------


class NBAMispricingAdapter(BacktestAdapter):
    """Wraps ScoreAnalyzer to detect early-game mispricing.

    Replicates the signal generation logic of NBAStrategyBacktester but
    returns standard Signal objects for the unified engine.
    """

    def __init__(
        self,
        min_edge_cents: float = 3.0,
        max_period: int = 2,
        position_size: int = 10,
    ):
        from signal_extraction.data_feeds.score_feed import ScoreAnalyzer

        self._analyzer = ScoreAnalyzer()
        self._min_edge = min_edge_cents
        self._max_period = min(max_period, 2)  # model unreliable after Q2
        self._position_size = position_size

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        ctx = frame.context
        if ctx.get("game_status") != "live":
            return []
        if ctx.get("period", 99) > self._max_period:
            return []

        home_score = ctx["home_score"]
        away_score = ctx["away_score"]
        score_diff = home_score - away_score
        time_remaining_seconds = self._analyzer.parse_time_remaining(
            ctx["time_remaining"]
        )

        home_win_prob = self._analyzer.calculate_win_probability(
            score_diff,
            ctx["period"],
            time_remaining_seconds,
        )

        home_ticker = ctx.get("home_ticker", "HOME")
        away_ticker = ctx.get("away_ticker", "AWAY")

        home_market = frame.markets.get(home_ticker)
        if home_market is None:
            return []

        market_mid = home_market.mid
        edge_cents = abs(home_win_prob - market_mid) * 100

        if edge_cents < self._min_edge:
            return []

        if home_win_prob > market_mid:
            ticker = home_ticker
            side = "BID"
            confidence = min(1.0, edge_cents / 20.0)
        else:
            ticker = away_ticker
            side = "BID"
            confidence = min(1.0, edge_cents / 20.0)

        return [
            Signal(
                ticker=ticker,
                side=side,
                price=frame.markets[ticker].ask if ticker in frame.markets else 0.5,
                size=self._position_size,
                confidence=confidence,
                reason=f"edge={edge_cents:.1f}c hwp={home_win_prob:.3f} mid={market_mid:.3f}",
                timestamp=frame.timestamp,
                metadata={
                    "edge_cents": edge_cents,
                    "home_win_prob": home_win_prob,
                    "market_mid": market_mid,
                    "period": ctx["period"],
                },
            )
        ]

    @property
    def name(self) -> str:
        return "nba-mispricing"


# ---------------------------------------------------------------------------
# BlowoutAdapter
# ---------------------------------------------------------------------------


class BlowoutAdapter(BacktestAdapter):
    """Wraps LateGameBlowoutStrategy._check_entry for late-game blowout signals.

    By default, only one trade per game (one_trade_per_game=True).
    """

    def __init__(
        self,
        min_point_differential: int = 10,
        max_time_remaining_seconds: int = 600,
        base_position_size: float = 5.0,
        one_trade_per_game: bool = True,
    ):
        from strategies.late_game_blowout_strategy import (
            LateGameBlowoutStrategy,
            BlowoutStrategyConfig,
            BlowoutSide,
        )

        config = BlowoutStrategyConfig(
            min_point_differential=min_point_differential,
            max_time_remaining_seconds=max_time_remaining_seconds,
            base_position_size=base_position_size,
        )
        self._strategy = LateGameBlowoutStrategy(config)
        self._BlowoutSide = BlowoutSide
        self._one_trade = one_trade_per_game
        self._traded = False

    def on_start(self) -> None:
        self._traded = False

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        if self._one_trade and self._traded:
            return []

        ctx = frame.context
        if ctx.get("game_status") != "live":
            return []

        home_ticker = ctx.get("home_ticker", "HOME")
        away_ticker = ctx.get("away_ticker", "AWAY")
        home_market = frame.markets.get(home_ticker)
        away_market = frame.markets.get(away_ticker)

        home_price = home_market.mid if home_market else None
        away_price = away_market.mid if away_market else None

        blowout_signal = self._strategy._check_entry(
            home_score=ctx["home_score"],
            away_score=ctx["away_score"],
            period=ctx["period"],
            time_remaining=ctx["time_remaining"],
            timestamp=frame.timestamp.timestamp(),
            game_id=ctx.get("game_id", ""),
            home_price=home_price,
            away_price=away_price,
        )

        if blowout_signal is None:
            return []

        if blowout_signal.leading_team == self._BlowoutSide.HOME:
            ticker = home_ticker
        else:
            ticker = away_ticker

        position_size = self._strategy._get_position_size(blowout_signal.confidence)

        self._traded = True
        return [
            Signal(
                ticker=ticker,
                side="BID",
                price=frame.markets[ticker].ask if ticker in frame.markets else 0.5,
                size=max(1, int(position_size)),
                confidence=blowout_signal.win_probability,
                reason=f"blowout lead={blowout_signal.score_differential} conf={blowout_signal.confidence}",
                timestamp=frame.timestamp,
                metadata={
                    "score_differential": blowout_signal.score_differential,
                    "win_probability": blowout_signal.win_probability,
                    "confidence_level": blowout_signal.confidence,
                },
            )
        ]

    @property
    def name(self) -> str:
        return "blowout"


# ---------------------------------------------------------------------------
# TotalPointsAdapter
# ---------------------------------------------------------------------------


class TotalPointsAdapter(BacktestAdapter):
    """Wraps TotalPointsStrategy.check_entry for over/under signals.

    Since NBA recordings may not include total-points market data, this
    adapter can simulate market prices from the strategy's own projection
    (with configurable noise).
    """

    def __init__(
        self,
        test_line: Optional[float] = None,
        min_edge_cents: float = 3.0,
        max_period: int = 3,
        position_size: int = 10,
        market_noise: float = 0.03,
    ):
        from strategies.total_points_strategy import (
            TotalPointsConfig,
            TotalPointsStrategy,
        )

        config = TotalPointsConfig(
            min_edge_cents=min_edge_cents,
            max_period=max_period,
            position_size=position_size,
        )
        self._strategy = TotalPointsStrategy(config)
        self._test_line = test_line  # None = derive from settlement
        self._market_noise = market_noise
        self._position_size = position_size
        self._line_resolved: Optional[float] = None

    def on_start(self) -> None:
        self._strategy.reset()
        self._line_resolved = self._test_line

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        import numpy as np

        ctx = frame.context
        if ctx.get("game_status") != "live":
            return []

        # Resolve line lazily if not provided
        if self._line_resolved is None:
            self._line_resolved = 220.0  # sensible NBA default

        home_score = ctx["home_score"]
        away_score = ctx["away_score"]
        period = ctx["period"]
        time_remaining = ctx["time_remaining"]

        # Parse time and calculate model probability
        time_remaining_seconds = self._strategy._parse_time_remaining(time_remaining)
        self._strategy.calculate_time_remaining_fraction(
            period,
            time_remaining_seconds,
        )

        # Halftime detection
        if period == 2 and time_remaining_seconds < 30:
            self._strategy.set_halftime_total(home_score + away_score)

        over_prob, _, _, _ = self._strategy.calculate_over_probability(
            home_score + away_score,
            self._line_resolved,
            period,
            time_remaining_seconds,
            self._strategy._halftime_total,
        )

        # Simulate market price
        noise = np.random.normal(0, self._market_noise)
        market_over = max(0.01, min(0.99, over_prob + noise))

        # Use a synthetic ticker for the total points market
        ticker = f"TOTAL-{ctx.get('game_id', 'UNK')}-{int(self._line_resolved)}"

        signal = self._strategy.check_entry(
            home_score=home_score,
            away_score=away_score,
            period=period,
            time_remaining=time_remaining,
            timestamp=frame.timestamp.timestamp(),
            game_id=ctx.get("game_id", ""),
            line=self._line_resolved,
            market_over_bid=market_over - 0.01,
            market_over_ask=market_over + 0.01,
            ticker=ticker,
        )

        if signal is None:
            return []

        # Build a MarketState on-the-fly for the synthetic ticker
        # so the engine can simulate fill
        if ticker not in frame.markets:
            if signal.direction == "BUY_OVER":
                bid = market_over - 0.01
                ask = market_over + 0.01
            else:
                bid = (1.0 - market_over) - 0.01
                ask = (1.0 - market_over) + 0.01
            bid = max(0.01, bid)
            ask = max(bid + 0.001, ask)
            frame.markets[ticker] = MarketState(
                ticker=ticker,
                timestamp=frame.timestamp,
                bid=bid,
                ask=ask,
            )

        side = "BID"  # always buying
        price = frame.markets[ticker].ask

        return [
            Signal(
                ticker=ticker,
                side=side,
                price=price,
                size=self._position_size,
                confidence=min(1.0, signal.edge_cents / 15.0),
                reason=f"{signal.direction} edge={signal.edge_cents:.1f}c proj={signal.projected_total:.0f}",
                timestamp=frame.timestamp,
                metadata={
                    "direction": signal.direction,
                    "edge_cents": signal.edge_cents,
                    "projected_total": signal.projected_total,
                    "line": signal.line,
                },
            )
        ]

    @property
    def name(self) -> str:
        return "total-points"
