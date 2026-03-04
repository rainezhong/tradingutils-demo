"""Metrics and result container for the unified backtest framework.

BacktestMetrics holds aggregate numbers; BacktestResult bundles everything
produced by a single engine.run() call.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.models import Fill
from strategies.base import Signal


@dataclass
class BacktestMetadata:
    """Data quality and completeness metrics for a backtest run."""

    # Data confidence level based on completeness
    data_confidence: str  # "LOW", "MEDIUM", or "HIGH"

    # Percentage of signals that had complete market data
    signals_with_full_data_pct: float

    # Percentage of signals that had orderbook depth data
    signals_with_depth_data_pct: float

    # Percentage of signals that had spread data
    signals_with_spread_data_pct: float

    # Count of signals using estimated depth (missing real data)
    signals_with_estimated_depth: int = 0

    # Count of signals using default spread (missing real data)
    signals_with_default_spread: int = 0

    # Total signals evaluated (for context)
    total_signals: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "data_confidence": self.data_confidence,
            "signals_with_full_data_pct": self.signals_with_full_data_pct,
            "signals_with_depth_data_pct": self.signals_with_depth_data_pct,
            "signals_with_spread_data_pct": self.signals_with_spread_data_pct,
            "signals_with_estimated_depth": self.signals_with_estimated_depth,
            "signals_with_default_spread": self.signals_with_default_spread,
            "total_signals": self.total_signals,
        }


@dataclass
class BacktestMetrics:
    """Aggregate performance metrics from a backtest run."""

    # Counts
    total_frames: int = 0
    total_signals: int = 0
    total_fills: int = 0

    # P&L
    initial_bankroll: float = 0.0
    final_bankroll: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    total_fees: float = 0.0

    # Risk
    max_drawdown_pct: float = 0.0
    peak_bankroll: float = 0.0

    # Accuracy (based on settlement)
    winning_fills: int = 0
    losing_fills: int = 0
    win_rate_pct: float = 0.0

    # Raw portfolio metrics dict (from PositionTracker)
    portfolio: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Complete result produced by BacktestEngine.run()."""

    adapter_name: str
    metrics: BacktestMetrics
    signals: List[Signal]
    fills: List[Fill]
    feed_metadata: Dict[str, Any] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    bankroll_curve: List = field(default_factory=list)
    settlements: Dict[str, Optional[float]] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    data_metadata: Optional[BacktestMetadata] = None

    def summary(self) -> str:
        """One-line summary suitable for CLI output."""
        m = self.metrics
        return (
            f"{self.adapter_name}: "
            f"{m.total_fills} fills, "
            f"PnL ${m.net_pnl:+,.2f} ({m.return_pct:+.1f}%), "
            f"MaxDD {m.max_drawdown_pct:.1f}%, "
            f"WR {m.win_rate_pct:.0f}%"
        )

    def report(self) -> str:
        """Multi-line human-readable report."""
        m = self.metrics
        lines = [
            "=" * 60,
            f"  BACKTEST REPORT: {self.adapter_name}",
            "=" * 60,
        ]

        # Metadata
        meta = self.feed_metadata
        if meta:
            for k, v in meta.items():
                lines.append(f"  {k}: {v}")
            lines.append("")

        # Config
        if self.config:
            lines.append("--- Config ---")
            for k, v in self.config.items():
                lines.append(f"  {k}: {v}")
            lines.append("")

        # Counts
        lines.append("--- Activity ---")
        lines.append(f"  Frames processed:  {m.total_frames}")
        lines.append(f"  Signals generated: {m.total_signals}")
        lines.append(f"  Fills executed:    {m.total_fills}")
        lines.append("")

        # P&L
        lines.append("--- P&L ---")
        lines.append(f"  Initial bankroll:  ${m.initial_bankroll:,.2f}")
        lines.append(f"  Final bankroll:    ${m.final_bankroll:,.2f}")
        lines.append(f"  Net P&L:           ${m.net_pnl:+,.2f}")
        lines.append(f"  Return:            {m.return_pct:+.1f}%")
        lines.append(f"  Total fees:        ${m.total_fees:,.2f}")
        lines.append("")

        # Risk
        lines.append("--- Risk ---")
        lines.append(f"  Max drawdown:      {m.max_drawdown_pct:.1f}%")
        lines.append(f"  Peak bankroll:     ${m.peak_bankroll:,.2f}")
        lines.append("")

        # Accuracy
        if m.total_fills > 0:
            lines.append("--- Accuracy ---")
            lines.append(f"  Winners:           {m.winning_fills}")
            lines.append(f"  Losers:            {m.losing_fills}")
            lines.append(f"  Win rate:          {m.win_rate_pct:.0f}%")
            lines.append("")

        # Data Quality
        if self.data_metadata:
            dm = self.data_metadata
            lines.append("")
            lines.append("--- Data Quality ---")
            lines.append(f"  Confidence:        {dm.data_confidence}")
            lines.append(f"  Full data:         {dm.signals_with_full_data_pct:.1f}%")
            lines.append(f"  With depth:        {dm.signals_with_depth_data_pct:.1f}%")
            lines.append(f"  With spread:       {dm.signals_with_spread_data_pct:.1f}%")
            if dm.signals_with_estimated_depth > 0:
                lines.append(f"  Estimated depth:   {dm.signals_with_estimated_depth}")
            if dm.signals_with_default_spread > 0:
                lines.append(f"  Default spread:    {dm.signals_with_default_spread}")

        # Timing
        if self.started_at and self.completed_at:
            dur = (self.completed_at - self.started_at).total_seconds()
            lines.append("")
            lines.append(f"  Duration:          {dur:.1f}s")

        lines.append("=" * 60)
        return "\n".join(lines)
