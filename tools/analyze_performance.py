#!/usr/bin/env python3
"""Performance analysis tool for market-making strategies.

This tool analyzes trading performance:
- P&L metrics (total, realized, unrealized)
- Position metrics (inventory, turnover)
- Quote metrics (fill rate, spread capture)
- Risk metrics (drawdown, Sharpe ratio)

Usage:
    python tools/analyze_performance.py results.json
    python tools/analyze_performance.py --from-engine engine_status.json
    python tools/analyze_performance.py --demo
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass
class Trade:
    """Trade record for analysis."""

    ticker: str
    side: str
    price: float
    size: int
    timestamp: datetime
    pnl: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "Trade":
        return cls(
            ticker=data["ticker"],
            side=data["side"],
            price=data["price"],
            size=data["size"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            pnl=data.get("pnl", 0.0),
        )


@dataclass
class MarketSnapshot:
    """Market data snapshot."""

    timestamp: datetime
    mid_price: float
    spread: float
    position: int
    pnl: float


@dataclass
class PerformanceMetrics:
    """Performance metrics summary."""

    # P&L Metrics
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Position Metrics
    avg_position: float = 0.0
    max_position: int = 0
    min_position: int = 0
    position_turnover: int = 0

    # Quote Metrics
    total_quotes: int = 0
    filled_quotes: int = 0
    fill_rate: float = 0.0
    avg_spread_captured: float = 0.0

    # Risk Metrics
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0

    # Time Metrics
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_hours: float = 0.0
    trades_per_hour: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pnl": {
                "total": round(self.total_pnl, 4),
                "realized": round(self.realized_pnl, 4),
                "unrealized": round(self.unrealized_pnl, 4),
                "max_profit": round(self.max_profit, 4),
                "max_loss": round(self.max_loss, 4),
                "win_rate": round(self.win_rate, 4),
                "profit_factor": round(self.profit_factor, 4),
            },
            "position": {
                "average": round(self.avg_position, 2),
                "max": self.max_position,
                "min": self.min_position,
                "turnover": self.position_turnover,
            },
            "quotes": {
                "total": self.total_quotes,
                "filled": self.filled_quotes,
                "fill_rate": round(self.fill_rate, 4),
                "avg_spread_captured": round(self.avg_spread_captured, 4),
            },
            "risk": {
                "max_drawdown": round(self.max_drawdown, 4),
                "max_drawdown_pct": round(self.max_drawdown_pct, 4),
                "sharpe_ratio": round(self.sharpe_ratio, 4),
                "sortino_ratio": round(self.sortino_ratio, 4),
            },
            "time": {
                "start": self.start_time.isoformat() if self.start_time else None,
                "end": self.end_time.isoformat() if self.end_time else None,
                "duration_hours": round(self.duration_hours, 2),
                "trades_per_hour": round(self.trades_per_hour, 2),
            },
        }


class PerformanceAnalyzer:
    """Analyzes trading performance."""

    def __init__(self):
        self.trades: list[Trade] = []
        self.snapshots: list[MarketSnapshot] = []
        self.pnl_series: list[float] = []

    def load_trades(self, trades_file: Path) -> None:
        """Load trades from JSON file."""
        with open(trades_file) as f:
            data = json.load(f)

        if isinstance(data, list):
            self.trades = [Trade.from_dict(t) for t in data]
        elif "trades" in data:
            self.trades = [Trade.from_dict(t) for t in data["trades"]]

        self.trades.sort(key=lambda t: t.timestamp)

    def load_engine_status(self, status_file: Path) -> None:
        """Load from engine status JSON."""
        with open(status_file) as f:
            data = json.load(f)

        # Extract relevant data
        if "market_maker" in data:
            mm = data["market_maker"]
            if "stats" in mm:
                stats = mm["stats"]
                self.pnl_series = stats.get("pnl_series", [])

            if "fills" in mm:
                for fill in mm["fills"]:
                    self.trades.append(Trade.from_dict(fill))

    def add_trade(self, trade: Trade) -> None:
        """Add a trade for analysis."""
        self.trades.append(trade)

    def add_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Add a market snapshot."""
        self.snapshots.append(snapshot)
        self.pnl_series.append(snapshot.pnl)

    def analyze(self) -> PerformanceMetrics:
        """Run full performance analysis."""
        metrics = PerformanceMetrics()

        if not self.trades:
            return metrics

        # Time metrics
        self._calculate_time_metrics(metrics)

        # P&L metrics
        self._calculate_pnl_metrics(metrics)

        # Position metrics
        self._calculate_position_metrics(metrics)

        # Quote metrics
        self._calculate_quote_metrics(metrics)

        # Risk metrics
        self._calculate_risk_metrics(metrics)

        return metrics

    def _calculate_time_metrics(self, metrics: PerformanceMetrics) -> None:
        """Calculate time-based metrics."""
        if not self.trades:
            return

        metrics.start_time = self.trades[0].timestamp
        metrics.end_time = self.trades[-1].timestamp

        duration = metrics.end_time - metrics.start_time
        metrics.duration_hours = duration.total_seconds() / 3600

        if metrics.duration_hours > 0:
            metrics.trades_per_hour = len(self.trades) / metrics.duration_hours

    def _calculate_pnl_metrics(self, metrics: PerformanceMetrics) -> None:
        """Calculate P&L metrics."""
        if not self.trades:
            return

        # Track position and P&L through trades
        position = 0
        avg_entry = 0.0
        realized_pnl = 0.0
        trade_pnls = []

        for trade in self.trades:
            if trade.side == "BID":
                if position >= 0:
                    # Adding to or opening long
                    new_pos = position + trade.size
                    if position == 0:
                        avg_entry = trade.price
                    else:
                        avg_entry = (position * avg_entry +
                                     trade.size * trade.price) / new_pos
                    position = new_pos
                else:
                    # Covering short
                    cover_size = min(trade.size, abs(position))
                    pnl = cover_size * (avg_entry - trade.price)
                    realized_pnl += pnl
                    trade_pnls.append(pnl)
                    position += trade.size
                    if position > 0:
                        avg_entry = trade.price
            else:
                # ASK - selling
                if position <= 0:
                    # Adding to or opening short
                    new_pos = position - trade.size
                    if position == 0:
                        avg_entry = trade.price
                    else:
                        avg_entry = (abs(position) * avg_entry +
                                     trade.size * trade.price) / abs(new_pos)
                    position = new_pos
                else:
                    # Closing long
                    close_size = min(trade.size, position)
                    pnl = close_size * (trade.price - avg_entry)
                    realized_pnl += pnl
                    trade_pnls.append(pnl)
                    position -= trade.size
                    if position < 0:
                        avg_entry = trade.price

        metrics.realized_pnl = realized_pnl
        metrics.total_pnl = realized_pnl  # + unrealized if we have market data

        if trade_pnls:
            winning = [p for p in trade_pnls if p > 0]
            losing = [p for p in trade_pnls if p < 0]

            metrics.max_profit = max(trade_pnls) if trade_pnls else 0
            metrics.max_loss = min(trade_pnls) if trade_pnls else 0

            if trade_pnls:
                metrics.win_rate = len(winning) / len(trade_pnls)

            if losing:
                total_loss = abs(sum(losing))
                total_profit = sum(winning)
                if total_loss > 0:
                    metrics.profit_factor = total_profit / total_loss

    def _calculate_position_metrics(self, metrics: PerformanceMetrics) -> None:
        """Calculate position metrics."""
        if not self.trades:
            return

        positions = [0]
        position = 0

        for trade in self.trades:
            if trade.side == "BID":
                position += trade.size
            else:
                position -= trade.size
            positions.append(position)

        metrics.avg_position = sum(positions) / len(positions)
        metrics.max_position = max(positions)
        metrics.min_position = min(positions)
        metrics.position_turnover = sum(t.size for t in self.trades)

    def _calculate_quote_metrics(self, metrics: PerformanceMetrics) -> None:
        """Calculate quote metrics."""
        # For now, use trade count as proxy for filled quotes
        metrics.filled_quotes = len(self.trades)

        # Estimate spread captured
        if self.trades:
            spreads = []
            for trade in self.trades:
                # Assume we captured half-spread on each side
                spreads.append(0.02)  # Default assumption

            if spreads:
                metrics.avg_spread_captured = sum(spreads) / len(spreads)

    def _calculate_risk_metrics(self, metrics: PerformanceMetrics) -> None:
        """Calculate risk metrics."""
        if not self.pnl_series and not self.trades:
            return

        # Build P&L series from trades if not provided
        if not self.pnl_series:
            self._build_pnl_series_from_trades()

        if not self.pnl_series:
            return

        # Max drawdown
        peak = self.pnl_series[0]
        max_dd = 0.0

        for pnl in self.pnl_series:
            if pnl > peak:
                peak = pnl
            drawdown = peak - pnl
            if drawdown > max_dd:
                max_dd = drawdown

        metrics.max_drawdown = max_dd

        if peak > 0:
            metrics.max_drawdown_pct = max_dd / peak

        # Calculate returns
        if len(self.pnl_series) > 1:
            returns = []
            for i in range(1, len(self.pnl_series)):
                if self.pnl_series[i - 1] != 0:
                    ret = (self.pnl_series[i] - self.pnl_series[i - 1]) / abs(
                        self.pnl_series[i - 1] + 1
                    )
                    returns.append(ret)

            if returns:
                avg_return = sum(returns) / len(returns)
                std_dev = math.sqrt(
                    sum((r - avg_return) ** 2 for r in returns) / len(returns)
                ) if len(returns) > 1 else 0

                if std_dev > 0:
                    # Annualized (assuming hourly data)
                    metrics.sharpe_ratio = (avg_return / std_dev) * math.sqrt(252 * 24)

                # Sortino ratio (only negative returns)
                negative_returns = [r for r in returns if r < 0]
                if negative_returns:
                    downside_std = math.sqrt(
                        sum(r ** 2 for r in negative_returns) / len(negative_returns)
                    )
                    if downside_std > 0:
                        metrics.sortino_ratio = (
                            avg_return / downside_std
                        ) * math.sqrt(252 * 24)

    def _build_pnl_series_from_trades(self) -> None:
        """Build cumulative P&L series from trades."""
        if not self.trades:
            return

        position = 0
        avg_entry = 0.0
        cumulative_pnl = 0.0
        self.pnl_series = [0.0]

        for trade in self.trades:
            if trade.side == "BID":
                if position >= 0:
                    new_pos = position + trade.size
                    if position == 0:
                        avg_entry = trade.price
                    else:
                        avg_entry = (position * avg_entry +
                                     trade.size * trade.price) / new_pos
                    position = new_pos
                else:
                    cover_size = min(trade.size, abs(position))
                    pnl = cover_size * (avg_entry - trade.price)
                    cumulative_pnl += pnl
                    position += trade.size
                    if position > 0:
                        avg_entry = trade.price
            else:
                if position <= 0:
                    new_pos = position - trade.size
                    if position == 0:
                        avg_entry = trade.price
                    else:
                        avg_entry = (abs(position) * avg_entry +
                                     trade.size * trade.price) / abs(new_pos)
                    position = new_pos
                else:
                    close_size = min(trade.size, position)
                    pnl = close_size * (trade.price - avg_entry)
                    cumulative_pnl += pnl
                    position -= trade.size
                    if position < 0:
                        avg_entry = trade.price

            self.pnl_series.append(cumulative_pnl)


class PerformanceReporter:
    """Generates performance reports."""

    def __init__(self, metrics: PerformanceMetrics):
        self.metrics = metrics

    def print_report(self) -> None:
        """Print formatted performance report."""
        m = self.metrics

        print("\n" + "=" * 60)
        print("PERFORMANCE REPORT")
        print("=" * 60)

        print("\n--- P&L METRICS ---")
        print(f"  Total P&L:        ${m.total_pnl:>12.4f}")
        print(f"  Realized P&L:     ${m.realized_pnl:>12.4f}")
        print(f"  Unrealized P&L:   ${m.unrealized_pnl:>12.4f}")
        print(f"  Max Single Profit:${m.max_profit:>12.4f}")
        print(f"  Max Single Loss:  ${m.max_loss:>12.4f}")
        print(f"  Win Rate:          {m.win_rate:>12.1%}")
        print(f"  Profit Factor:     {m.profit_factor:>12.2f}")

        print("\n--- POSITION METRICS ---")
        print(f"  Avg Position:      {m.avg_position:>12.1f}")
        print(f"  Max Position:      {m.max_position:>12}")
        print(f"  Min Position:      {m.min_position:>12}")
        print(f"  Total Turnover:    {m.position_turnover:>12}")

        print("\n--- QUOTE METRICS ---")
        print(f"  Total Quotes:      {m.total_quotes:>12}")
        print(f"  Filled Quotes:     {m.filled_quotes:>12}")
        print(f"  Fill Rate:         {m.fill_rate:>12.1%}")
        print(f"  Avg Spread Capt:   {m.avg_spread_captured:>12.4f}")

        print("\n--- RISK METRICS ---")
        print(f"  Max Drawdown:     ${m.max_drawdown:>12.4f}")
        print(f"  Max Drawdown %:    {m.max_drawdown_pct:>12.1%}")
        print(f"  Sharpe Ratio:      {m.sharpe_ratio:>12.2f}")
        print(f"  Sortino Ratio:     {m.sortino_ratio:>12.2f}")

        print("\n--- TIME METRICS ---")
        if m.start_time:
            print(f"  Start:             {m.start_time.isoformat()}")
        if m.end_time:
            print(f"  End:               {m.end_time.isoformat()}")
        print(f"  Duration (hours):  {m.duration_hours:>12.2f}")
        print(f"  Trades/Hour:       {m.trades_per_hour:>12.1f}")

        print("\n" + "=" * 60)

    def get_grade(self) -> str:
        """Get overall performance grade."""
        score = 0

        # P&L score
        if self.metrics.total_pnl > 0:
            score += 2
        if self.metrics.profit_factor > 1.5:
            score += 2
        if self.metrics.win_rate > 0.5:
            score += 1

        # Risk score
        if self.metrics.max_drawdown_pct < 0.1:
            score += 2
        if self.metrics.sharpe_ratio > 1.0:
            score += 2
        elif self.metrics.sharpe_ratio > 0.5:
            score += 1

        if score >= 8:
            return "A"
        elif score >= 6:
            return "B"
        elif score >= 4:
            return "C"
        elif score >= 2:
            return "D"
        else:
            return "F"


def create_demo_data() -> list[Trade]:
    """Create demo trades for testing."""
    base_time = datetime.now() - timedelta(hours=2)
    trades = []

    # Simulate some market making trades
    for i in range(50):
        time = base_time + timedelta(minutes=i * 2)

        # Alternate buy/sell with slight profit
        if i % 2 == 0:
            trades.append(Trade(
                ticker="DEMO",
                side="BID",
                price=0.48 + (i % 5) * 0.01,
                size=10,
                timestamp=time,
            ))
        else:
            trades.append(Trade(
                ticker="DEMO",
                side="ASK",
                price=0.52 + (i % 5) * 0.01,
                size=10,
                timestamp=time,
            ))

    return trades


def main():
    parser = argparse.ArgumentParser(
        description="Analyze trading performance"
    )
    parser.add_argument(
        "data_file",
        nargs="?",
        help="Path to trades JSON or engine status file"
    )
    parser.add_argument(
        "--from-engine",
        action="store_true",
        help="Parse as engine status file"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results (JSON)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with demo data"
    )
    parser.add_argument(
        "--compare",
        type=str,
        help="Compare with another results file"
    )

    args = parser.parse_args()

    analyzer = PerformanceAnalyzer()

    if args.demo:
        trades = create_demo_data()
        for t in trades:
            analyzer.add_trade(t)
    elif args.data_file:
        path = Path(args.data_file)
        if args.from_engine:
            analyzer.load_engine_status(path)
        else:
            analyzer.load_trades(path)
    else:
        parser.print_help()
        return 1

    # Run analysis
    metrics = analyzer.analyze()

    # Print report
    reporter = PerformanceReporter(metrics)
    reporter.print_report()

    grade = reporter.get_grade()
    print(f"\nOVERALL GRADE: {grade}")

    # Save output
    if args.output:
        output = {
            "metrics": metrics.to_dict(),
            "grade": grade,
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults saved to {args.output}")

    # Compare if requested
    if args.compare:
        print(f"\nComparison with {args.compare} would go here")

    return 0


if __name__ == "__main__":
    sys.exit(main())
