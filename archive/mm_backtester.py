"""Market Making Backtester - Replays NBA recordings to test market making strategy.

This module provides a backtester specifically designed for market-making strategies:
1. Replays NBA game recordings frame-by-frame
2. Uses MarketMaker class to generate quotes each frame
3. Simulates fills when market price crosses our quotes
4. Tracks P&L with Kalshi 7% fee on profits
5. Supports parameter sweeps for optimization
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.market_maker.market_maker import MarketMaker
from src.market_making.config import MarketMakerConfig
from src.market_making.models import Fill, MarketState, Quote
from src.market_making.constants import SIDE_BID

from .nba_recorder import NBAGameRecorder


# =============================================================================
# CONFIGURATION
# =============================================================================


@dataclass
class MMBacktestConfig:
    """Configuration for market-making backtester.

    Attributes:
        min_spread_cents: Minimum bid-ask spread (in cents) to participate.
        max_inventory: Maximum position size (contracts).
        quote_edge_cents: Additional edge to add to each side (in cents).
        quote_size: Number of contracts per quote.
        inventory_skew_factor: How much to adjust quotes based on inventory.
        kalshi_fee_rate: Fee rate on profits (default 7%).
        pnl_stop_usd: Stop loss per market in USD.
        flatten_before_end_minutes: Minutes before game end to stop quoting.
    """

    min_spread_cents: float = 5.0
    max_inventory: int = 50
    quote_edge_cents: float = 1.0
    quote_size: int = 10
    inventory_skew_factor: float = 0.01
    kalshi_fee_rate: float = 0.07
    pnl_stop_usd: float = 20.0
    flatten_before_end_minutes: float = 5.0

    def to_market_maker_config(self) -> MarketMakerConfig:
        """Convert to MarketMakerConfig for the MarketMaker class."""
        # Convert cents to probability (0.01 = 1 cent)
        return MarketMakerConfig(
            target_spread=self.min_spread_cents / 100.0,
            edge_per_side=self.quote_edge_cents / 100.0,
            quote_size=self.quote_size,
            max_position=self.max_inventory,
            inventory_skew_factor=self.inventory_skew_factor,
            min_spread_to_quote=self.min_spread_cents / 100.0,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "min_spread_cents": self.min_spread_cents,
            "max_inventory": self.max_inventory,
            "quote_edge_cents": self.quote_edge_cents,
            "quote_size": self.quote_size,
            "inventory_skew_factor": self.inventory_skew_factor,
            "kalshi_fee_rate": self.kalshi_fee_rate,
            "pnl_stop_usd": self.pnl_stop_usd,
            "flatten_before_end_minutes": self.flatten_before_end_minutes,
        }


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class MMQuoteRecord:
    """Record of a quote generated during backtesting."""

    frame_idx: int
    timestamp: float
    ticker: str
    side: str  # "BID" or "ASK"
    price: float
    size: int
    filled: bool = False
    fill_price: Optional[float] = None
    fill_size: int = 0


@dataclass
class MMFillRecord:
    """Record of a fill during backtesting."""

    frame_idx: int
    timestamp: float
    ticker: str
    side: str
    price: float
    size: int
    realized_pnl: float = 0.0
    position_after: int = 0


@dataclass
class MMFrameSnapshot:
    """Snapshot of market maker state at a single frame."""

    frame_idx: int
    timestamp: float

    # Market state
    ticker: str
    bid: float
    ask: float
    spread_cents: float

    # Position
    position: int
    avg_entry_price: float
    unrealized_pnl: float
    realized_pnl: float

    # Quotes (if any)
    bid_quote_price: Optional[float] = None
    bid_quote_size: Optional[int] = None
    ask_quote_price: Optional[float] = None
    ask_quote_size: Optional[int] = None

    # Fills this frame
    fills_this_frame: int = 0


@dataclass
class MMBacktestMetrics:
    """Aggregate metrics from a market-making backtest."""

    # Basic counts
    total_frames: int = 0
    frames_quoted: int = 0
    total_quotes_generated: int = 0
    total_fills: int = 0
    total_volume: int = 0

    # P&L
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0

    # Position stats
    max_position: int = 0
    avg_position: float = 0.0
    final_position: int = 0

    # Fill rate
    bid_fills: int = 0
    ask_fills: int = 0
    fill_imbalance: float = 0.0  # (bid_fills - ask_fills) / total_fills

    # Market stats
    avg_spread_cents: float = 0.0
    pct_time_quoting: float = 0.0

    # Risk
    max_drawdown: float = 0.0
    sharpe_approx: float = 0.0  # Rough approximation

    # One-sided detection
    one_sided_runs: int = 0  # Consecutive fills on same side
    max_one_sided_run: int = 0


@dataclass
class MMBacktestResult:
    """Complete result of a market-making backtest."""

    recording_path: str
    game_id: str
    home_team: str
    away_team: str
    ticker: str

    # Config used
    config: MMBacktestConfig

    # Results
    metrics: MMBacktestMetrics

    # Detailed records
    fills: List[MMFillRecord] = field(default_factory=list)
    frame_snapshots: List[MMFrameSnapshot] = field(default_factory=list)

    # Timing
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "recording_path": self.recording_path,
            "game_id": self.game_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "ticker": self.ticker,
            "config": self.config.to_dict(),
            "metrics": {
                "total_frames": self.metrics.total_frames,
                "frames_quoted": self.metrics.frames_quoted,
                "total_fills": self.metrics.total_fills,
                "total_volume": self.metrics.total_volume,
                "gross_pnl": self.metrics.gross_pnl,
                "fees": self.metrics.fees,
                "net_pnl": self.metrics.net_pnl,
                "max_position": self.metrics.max_position,
                "final_position": self.metrics.final_position,
                "avg_spread_cents": self.metrics.avg_spread_cents,
                "pct_time_quoting": self.metrics.pct_time_quoting,
                "max_drawdown": self.metrics.max_drawdown,
                "fill_imbalance": self.metrics.fill_imbalance,
            },
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


# =============================================================================
# FILL SIMULATION
# =============================================================================


class FillSimulator:
    """Simulates order fills based on market price crossing quote prices.

    Fill logic:
    - Bid fills when market ask <= our bid price
    - Ask fills when market bid >= our ask price
    """

    def __init__(self, fill_probability: float = 0.8):
        """Initialize fill simulator.

        Args:
            fill_probability: Probability of filling when price crosses (0-1).
        """
        self.fill_probability = fill_probability
        import random

        self._random = random.Random()

    def check_fill(
        self,
        quote: Quote,
        market_bid: float,
        market_ask: float,
    ) -> Tuple[bool, float]:
        """Check if a quote would fill.

        Args:
            quote: The quote to check.
            market_bid: Current market best bid.
            market_ask: Current market best ask.

        Returns:
            Tuple of (should_fill, fill_price).
        """
        should_fill = False
        fill_price = quote.price

        if quote.side == SIDE_BID:
            # Our bid fills if market ask drops to or below our bid
            if market_ask <= quote.price:
                should_fill = True
                fill_price = quote.price  # We get filled at our limit
        else:  # SIDE_ASK
            # Our ask fills if market bid rises to or above our ask
            if market_bid >= quote.price:
                should_fill = True
                fill_price = quote.price  # We get filled at our limit

        # Apply fill probability
        if should_fill and self._random.random() > self.fill_probability:
            should_fill = False

        return should_fill, fill_price


# =============================================================================
# BACKTESTER
# =============================================================================


class MMBacktester:
    """Market-making backtester for NBA game recordings.

    Replays game recordings and simulates market-making:
    1. Each frame, generate quotes via MarketMaker class
    2. Check if previous quotes would have filled
    3. Update inventory and P&L on fills
    4. Track metrics

    Example:
        recording = NBAGameRecorder.load("data/recordings/MIL_vs_BOS.json")
        config = MMBacktestConfig(min_spread_cents=5.0, max_inventory=50)
        backtester = MMBacktester(recording, config, use_home_market=True)
        result = backtester.run()
        print(f"Net P&L: ${result.metrics.net_pnl:.2f}")
    """

    def __init__(
        self,
        recording: NBAGameRecorder,
        config: MMBacktestConfig,
        use_home_market: bool = True,
        fill_probability: float = 0.8,
    ):
        """Initialize the backtester.

        Args:
            recording: Loaded game recording.
            config: Backtester configuration.
            use_home_market: If True, market-make on home team ticker.
            fill_probability: Probability of fill when price crosses.
        """
        self.recording = recording
        self.config = config
        self.use_home_market = use_home_market

        # Determine ticker
        if use_home_market:
            self.ticker = recording.home_ticker
        else:
            self.ticker = recording.away_ticker

        # Create market maker
        mm_config = config.to_market_maker_config()
        self.market_maker = MarketMaker(self.ticker, mm_config)

        # Fill simulator
        self.fill_simulator = FillSimulator(fill_probability)

        # Active quotes (to check for fills next frame)
        self.active_bid_quote: Optional[Quote] = None
        self.active_ask_quote: Optional[Quote] = None

        # Tracking
        self.fills: List[MMFillRecord] = []
        self.frame_snapshots: List[MMFrameSnapshot] = []

        # Running stats
        self._peak_pnl = 0.0
        self._current_drawdown = 0.0
        self._max_drawdown = 0.0
        self._position_sum = 0
        self._spread_sum = 0.0
        self._spread_count = 0

        # One-sided tracking
        self._last_fill_side: Optional[str] = None
        self._consecutive_same_side = 0
        self._one_sided_runs = 0
        self._max_one_sided_run = 0

        # P&L stop
        self._stopped = False

    def run(self, verbose: bool = False) -> MMBacktestResult:
        """Run the backtest.

        Args:
            verbose: Print progress during backtest.

        Returns:
            MMBacktestResult with all metrics and data.
        """
        started_at = datetime.now()

        # Reset state
        self.market_maker.reset()
        self.active_bid_quote = None
        self.active_ask_quote = None
        self.fills = []
        self.frame_snapshots = []
        self._peak_pnl = 0.0
        self._max_drawdown = 0.0
        self._position_sum = 0
        self._spread_sum = 0.0
        self._spread_count = 0
        self._last_fill_side = None
        self._consecutive_same_side = 0
        self._one_sided_runs = 0
        self._max_one_sided_run = 0
        self._stopped = False

        frames_quoted = 0
        total_quotes = 0
        bid_fills = 0
        ask_fills = 0
        max_position = 0

        for frame_idx, frame in enumerate(self.recording.frames):
            # Skip non-live frames
            if frame.game_status != "live":
                continue

            # Check P&L stop
            total_pnl = (
                self.market_maker.position.realized_pnl
                + self.market_maker.position.unrealized_pnl
            )
            if total_pnl < -self.config.pnl_stop_usd:
                if not self._stopped:
                    self._stopped = True
                    if verbose:
                        print(
                            f"[Frame {frame_idx}] P&L stop triggered: ${total_pnl:.2f}"
                        )
                continue

            # Get market data for our ticker
            if self.use_home_market:
                bid = frame.home_bid
                ask = frame.home_ask
            else:
                bid = frame.away_bid
                ask = frame.away_ask

            spread_cents = (ask - bid) * 100

            # Track spread stats
            self._spread_sum += spread_cents
            self._spread_count += 1

            # Create market state
            try:
                market_state = MarketState(
                    ticker=self.ticker,
                    timestamp=datetime.fromtimestamp(frame.timestamp),
                    best_bid=bid,
                    best_ask=ask,
                    mid_price=(bid + ask) / 2,
                    bid_size=100,  # Assumed
                    ask_size=100,
                )
            except Exception:
                # Invalid market state (bid >= ask or other issues)
                continue

            # 1. Check if previous quotes filled
            fills_this_frame = 0

            if self.active_bid_quote:
                should_fill, fill_price = self.fill_simulator.check_fill(
                    self.active_bid_quote, bid, ask
                )
                if should_fill:
                    self._process_fill(
                        frame_idx, frame.timestamp, self.active_bid_quote, fill_price
                    )
                    bid_fills += 1
                    fills_this_frame += 1
                self.active_bid_quote = None

            if self.active_ask_quote:
                should_fill, fill_price = self.fill_simulator.check_fill(
                    self.active_ask_quote, bid, ask
                )
                if should_fill:
                    self._process_fill(
                        frame_idx, frame.timestamp, self.active_ask_quote, fill_price
                    )
                    ask_fills += 1
                    fills_this_frame += 1
                self.active_ask_quote = None

            # 2. Update unrealized P&L
            self.market_maker.calculate_unrealized_pnl(market_state.mid_price)

            # Track drawdown
            total_pnl = (
                self.market_maker.position.realized_pnl
                + self.market_maker.position.unrealized_pnl
            )
            if total_pnl > self._peak_pnl:
                self._peak_pnl = total_pnl
            drawdown = self._peak_pnl - total_pnl
            if drawdown > self._max_drawdown:
                self._max_drawdown = drawdown

            # Track position
            pos = abs(self.market_maker.position.contracts)
            self._position_sum += pos
            if pos > max_position:
                max_position = pos

            # 3. Generate new quotes (if spread is wide enough)
            bid_quote_price = None
            bid_quote_size = None
            ask_quote_price = None
            ask_quote_size = None

            if spread_cents >= self.config.min_spread_cents and not self._stopped:
                quotes = self.market_maker.generate_quotes(market_state)

                if quotes:
                    frames_quoted += 1
                    total_quotes += len(quotes)

                    for quote in quotes:
                        if quote.side == SIDE_BID:
                            self.active_bid_quote = quote
                            bid_quote_price = quote.price
                            bid_quote_size = quote.size
                        else:
                            self.active_ask_quote = quote
                            ask_quote_price = quote.price
                            ask_quote_size = quote.size

            # 4. Capture frame snapshot
            snapshot = MMFrameSnapshot(
                frame_idx=frame_idx,
                timestamp=frame.timestamp,
                ticker=self.ticker,
                bid=bid,
                ask=ask,
                spread_cents=spread_cents,
                position=self.market_maker.position.contracts,
                avg_entry_price=self.market_maker.position.avg_entry_price,
                unrealized_pnl=self.market_maker.position.unrealized_pnl,
                realized_pnl=self.market_maker.position.realized_pnl,
                bid_quote_price=bid_quote_price,
                bid_quote_size=bid_quote_size,
                ask_quote_price=ask_quote_price,
                ask_quote_size=ask_quote_size,
                fills_this_frame=fills_this_frame,
            )
            self.frame_snapshots.append(snapshot)

            if verbose and frame_idx % 500 == 0:
                print(
                    f"[Frame {frame_idx}] Spread: {spread_cents:.1f}c | "
                    f"Pos: {self.market_maker.position.contracts} | "
                    f"P&L: ${total_pnl:.2f}"
                )

        completed_at = datetime.now()

        # Calculate final metrics
        total_frames = len(
            [f for f in self.recording.frames if f.game_status == "live"]
        )
        total_fills = bid_fills + ask_fills
        total_volume = sum(f.size for f in self.fills)

        gross_pnl = self.market_maker.position.realized_pnl
        fees = gross_pnl * self.config.kalshi_fee_rate if gross_pnl > 0 else 0.0
        net_pnl = gross_pnl - fees

        avg_position = self._position_sum / total_frames if total_frames > 0 else 0.0
        avg_spread = (
            self._spread_sum / self._spread_count if self._spread_count > 0 else 0.0
        )
        pct_time_quoting = frames_quoted / total_frames if total_frames > 0 else 0.0

        fill_imbalance = 0.0
        if total_fills > 0:
            fill_imbalance = (bid_fills - ask_fills) / total_fills

        metrics = MMBacktestMetrics(
            total_frames=total_frames,
            frames_quoted=frames_quoted,
            total_quotes_generated=total_quotes,
            total_fills=total_fills,
            total_volume=total_volume,
            gross_pnl=gross_pnl,
            fees=fees,
            net_pnl=net_pnl,
            max_position=max_position,
            avg_position=avg_position,
            final_position=self.market_maker.position.contracts,
            bid_fills=bid_fills,
            ask_fills=ask_fills,
            fill_imbalance=fill_imbalance,
            avg_spread_cents=avg_spread,
            pct_time_quoting=pct_time_quoting,
            max_drawdown=self._max_drawdown,
            one_sided_runs=self._one_sided_runs,
            max_one_sided_run=self._max_one_sided_run,
        )

        return MMBacktestResult(
            recording_path="",
            game_id=self.recording.game_id,
            home_team=self.recording.home_team,
            away_team=self.recording.away_team,
            ticker=self.ticker,
            config=self.config,
            metrics=metrics,
            fills=self.fills,
            frame_snapshots=self.frame_snapshots,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _process_fill(
        self,
        frame_idx: int,
        timestamp: float,
        quote: Quote,
        fill_price: float,
    ) -> None:
        """Process a fill and update position."""
        # Create fill object for market maker
        fill = Fill(
            order_id=str(uuid.uuid4()),
            ticker=quote.ticker,
            side=quote.side,
            price=fill_price,
            size=quote.size,
            timestamp=quote.timestamp,
        )

        # Get P&L before update
        pnl_before = self.market_maker.position.realized_pnl

        # Update market maker position
        self.market_maker.update_position(fill)

        # Calculate realized P&L from this fill
        realized_pnl = self.market_maker.position.realized_pnl - pnl_before

        # Record fill
        self.fills.append(
            MMFillRecord(
                frame_idx=frame_idx,
                timestamp=timestamp,
                ticker=quote.ticker,
                side=quote.side,
                price=fill_price,
                size=quote.size,
                realized_pnl=realized_pnl,
                position_after=self.market_maker.position.contracts,
            )
        )

        # Track one-sided fills
        if self._last_fill_side == quote.side:
            self._consecutive_same_side += 1
            if self._consecutive_same_side > self._max_one_sided_run:
                self._max_one_sided_run = self._consecutive_same_side
        else:
            if self._consecutive_same_side >= 3:
                self._one_sided_runs += 1
            self._last_fill_side = quote.side
            self._consecutive_same_side = 1


# =============================================================================
# PARAMETER SWEEP
# =============================================================================


@dataclass
class SweepResult:
    """Result of a parameter sweep."""

    params: Dict[str, Any]
    net_pnl: float
    total_fills: int
    max_drawdown: float
    fill_imbalance: float
    pct_time_quoting: float


def run_parameter_sweep(
    recording: NBAGameRecorder,
    min_spread_values: List[float] = [5, 8, 10, 15, 20],
    max_inventory_values: List[int] = [10, 25, 50, 100],
    quote_edge_values: List[float] = [0.5, 1.0, 2.0],
    verbose: bool = False,
) -> List[SweepResult]:
    """Run a parameter sweep over configurations.

    Args:
        recording: Game recording to test.
        min_spread_values: Spread thresholds to test (cents).
        max_inventory_values: Max inventory values to test.
        quote_edge_values: Quote edge values to test (cents).
        verbose: Print progress.

    Returns:
        List of SweepResult sorted by net P&L descending.
    """
    results = []

    total_combos = (
        len(min_spread_values) * len(max_inventory_values) * len(quote_edge_values)
    )
    combo_idx = 0

    for min_spread in min_spread_values:
        for max_inv in max_inventory_values:
            for edge in quote_edge_values:
                combo_idx += 1

                config = MMBacktestConfig(
                    min_spread_cents=min_spread,
                    max_inventory=max_inv,
                    quote_edge_cents=edge,
                )

                # Run for home market
                backtester = MMBacktester(recording, config, use_home_market=True)
                result = backtester.run(verbose=False)

                sweep_result = SweepResult(
                    params={
                        "min_spread_cents": min_spread,
                        "max_inventory": max_inv,
                        "quote_edge_cents": edge,
                    },
                    net_pnl=result.metrics.net_pnl,
                    total_fills=result.metrics.total_fills,
                    max_drawdown=result.metrics.max_drawdown,
                    fill_imbalance=result.metrics.fill_imbalance,
                    pct_time_quoting=result.metrics.pct_time_quoting,
                )
                results.append(sweep_result)

                if verbose:
                    print(
                        f"[{combo_idx}/{total_combos}] "
                        f"spread={min_spread}c inv={max_inv} edge={edge}c | "
                        f"P&L: ${result.metrics.net_pnl:.2f} | "
                        f"Fills: {result.metrics.total_fills}"
                    )

    # Sort by net P&L descending
    results.sort(key=lambda x: x.net_pnl, reverse=True)

    return results


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


async def run_backtest(
    recording_path: str,
    config: Optional[MMBacktestConfig] = None,
    use_home_market: bool = True,
    verbose: bool = False,
) -> MMBacktestResult:
    """Convenience function to run a market-making backtest.

    Args:
        recording_path: Path to game recording JSON.
        config: Backtester config (uses defaults if None).
        use_home_market: Market-make on home team ticker.
        verbose: Print progress.

    Returns:
        MMBacktestResult.
    """
    recording = NBAGameRecorder.load(recording_path)

    if config is None:
        config = MMBacktestConfig()

    backtester = MMBacktester(
        recording=recording,
        config=config,
        use_home_market=use_home_market,
    )

    result = backtester.run(verbose=verbose)
    result.recording_path = recording_path

    return result


def format_backtest_report(result: MMBacktestResult) -> str:
    """Format a backtest result as a readable report."""
    m = result.metrics

    lines = [
        "=" * 60,
        "MARKET MAKING BACKTEST REPORT",
        "=" * 60,
        "",
        f"Game: {result.away_team} @ {result.home_team}",
        f"Market: {result.ticker}",
        "",
        "--- Configuration ---",
        f"Min Spread: {result.config.min_spread_cents:.1f} cents",
        f"Max Inventory: {result.config.max_inventory}",
        f"Quote Edge: {result.config.quote_edge_cents:.1f} cents",
        f"Quote Size: {result.config.quote_size}",
        "",
        "--- Activity ---",
        f"Total Frames: {m.total_frames}",
        f"Frames Quoted: {m.frames_quoted} ({m.pct_time_quoting * 100:.1f}%)",
        f"Quotes Generated: {m.total_quotes_generated}",
        f"Total Fills: {m.total_fills}",
        f"  Bid Fills: {m.bid_fills}",
        f"  Ask Fills: {m.ask_fills}",
        f"  Imbalance: {m.fill_imbalance:+.2f}",
        f"Total Volume: {m.total_volume} contracts",
        "",
        "--- Market Stats ---",
        f"Avg Spread: {m.avg_spread_cents:.1f} cents",
        "",
        "--- P&L ---",
        f"Gross P&L: ${m.gross_pnl:.2f}",
        f"Fees (7%): ${m.fees:.2f}",
        f"Net P&L: ${m.net_pnl:.2f}",
        "",
        "--- Position ---",
        f"Max Position: {m.max_position}",
        f"Avg Position: {m.avg_position:.1f}",
        f"Final Position: {m.final_position}",
        "",
        "--- Risk ---",
        f"Max Drawdown: ${m.max_drawdown:.2f}",
        f"One-Sided Runs: {m.one_sided_runs}",
        f"Max One-Sided Run: {m.max_one_sided_run}",
        "",
        "=" * 60,
    ]

    return "\n".join(lines)


def format_sweep_results(results: List[SweepResult], top_n: int = 10) -> str:
    """Format parameter sweep results as a table."""
    lines = [
        "=" * 80,
        "PARAMETER SWEEP RESULTS (Top {})".format(top_n),
        "=" * 80,
        "",
        f"{'Spread':<8} {'MaxInv':<8} {'Edge':<8} {'Net P&L':<12} {'Fills':<8} {'MaxDD':<10}",
        "-" * 80,
    ]

    for r in results[:top_n]:
        lines.append(
            f"{r.params['min_spread_cents']:<8.1f} "
            f"{r.params['max_inventory']:<8} "
            f"{r.params['quote_edge_cents']:<8.1f} "
            f"${r.net_pnl:<11.2f} "
            f"{r.total_fills:<8} "
            f"${r.max_drawdown:<9.2f}"
        )

    lines.extend(["", "=" * 80])

    # Best params
    if results:
        best = results[0]
        lines.extend(
            [
                "",
                "OPTIMAL PARAMETERS:",
                f"  min_spread_cents: {best.params['min_spread_cents']}",
                f"  max_inventory: {best.params['max_inventory']}",
                f"  quote_edge_cents: {best.params['quote_edge_cents']}",
            ]
        )

    return "\n".join(lines)
