"""
Session Analyzer - Aggregate analysis and loss breakdown.

Analyzes trade journal data to identify patterns, calculate metrics,
and generate warnings about potential issues.
"""

import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.testing.models import (
    LossBreakdown,
    LossCategory,
    SessionAnalysis,
    TradeJournalEntry,
    TradeJournalStatus,
    Warning,
    WarningLevel,
)
from src.testing.trade_journal import TradeJournal


logger = logging.getLogger(__name__)


class SessionAnalyzer:
    """
    Analyzes trade journal data to identify patterns and losses.

    Calculates performance metrics, categorizes losses, and generates
    warnings about potential issues in the trading system.
    """

    # Thresholds for warnings
    SLIPPAGE_WARNING_PCT = 0.30      # Warn if slippage > 30% of losses
    STALE_QUOTE_THRESHOLD_MS = 2000  # Quotes older than this are stale
    HIGH_LOSS_TRADE_PCT = 0.05       # Flag trades losing > 5% of expected
    MIN_WIN_RATE = 0.60              # Warn if win rate below this
    MAX_DRAWDOWN_PCT = 0.10          # Warn if drawdown > 10%

    def __init__(self, journal: TradeJournal):
        """
        Initialize the analyzer.

        Args:
            journal: TradeJournal instance with recorded trades
        """
        self.journal = journal
        self._entries = journal.entries

    def analyze(self) -> SessionAnalysis:
        """
        Perform comprehensive analysis of the trading session.

        Returns:
            SessionAnalysis with all metrics and warnings
        """
        if not self._entries:
            return self._empty_analysis()

        # Basic counts
        total = len(self._entries)
        successful = len([e for e in self._entries if e.status == TradeJournalStatus.SUCCESS])
        partial = len([e for e in self._entries if e.status == TradeJournalStatus.PARTIAL])
        rolled_back = len([e for e in self._entries if e.status == TradeJournalStatus.ROLLED_BACK])
        failed = len([e for e in self._entries if e.status == TradeJournalStatus.FAILED])

        # P&L calculations
        pnl_data = self._calculate_pnl_metrics()

        # Timing metrics
        timing = self._calculate_timing_metrics()

        # Quote staleness
        staleness = self._calculate_staleness_metrics()

        # Loss breakdown
        loss_breakdown = self._calculate_loss_breakdown()

        # Performance metrics
        win_rate = self._calculate_win_rate()
        profit_factor = self._calculate_profit_factor()
        sharpe = self._calculate_sharpe_ratio()
        drawdown_usd, drawdown_pct = self._calculate_max_drawdown()

        # Best/worst trades
        best_id, best_pnl = self._find_best_trade()
        worst_id, worst_pnl = self._find_worst_trade()

        # Average profit/loss
        avg_profit = self._calculate_avg_profit()
        avg_loss = self._calculate_avg_loss()

        # Generate warnings
        warnings = self._generate_warnings(
            win_rate=win_rate,
            loss_breakdown=loss_breakdown,
            drawdown_pct=drawdown_pct,
            staleness=staleness,
        )

        # Session timing
        started_at = min(e.detected_at for e in self._entries)
        ended_at = max(e.execution_completed_at or e.detected_at for e in self._entries)
        duration = (ended_at - started_at).total_seconds()

        return SessionAnalysis(
            session_id=self.journal.session_id,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
            total_trades=total,
            successful_trades=successful,
            partial_trades=partial,
            rolled_back_trades=rolled_back,
            failed_trades=failed,
            total_pnl_usd=pnl_data["total_pnl"],
            gross_profit_usd=pnl_data["gross_profit"],
            gross_loss_usd=pnl_data["gross_loss"],
            total_fees_usd=pnl_data["total_fees"],
            win_rate=win_rate,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            max_drawdown_usd=drawdown_usd,
            max_drawdown_pct=drawdown_pct,
            average_profit_per_trade=avg_profit,
            average_loss_per_trade=avg_loss,
            loss_breakdown=loss_breakdown,
            avg_execution_time_ms=timing["avg"],
            max_execution_time_ms=timing["max"],
            min_execution_time_ms=timing["min"],
            avg_quote_age_ms=staleness["avg_age"],
            max_quote_age_ms=staleness["max_age"],
            stale_quote_count=staleness["stale_count"],
            warnings=warnings,
            best_trade_id=best_id,
            best_trade_pnl=best_pnl,
            worst_trade_id=worst_id,
            worst_trade_pnl=worst_pnl,
        )

    def _empty_analysis(self) -> SessionAnalysis:
        """Return an empty analysis for sessions with no trades."""
        now = datetime.now()
        return SessionAnalysis(
            session_id=self.journal.session_id,
            started_at=now,
            ended_at=now,
            duration_seconds=0,
            total_trades=0,
            successful_trades=0,
            partial_trades=0,
            rolled_back_trades=0,
            failed_trades=0,
            total_pnl_usd=0,
            gross_profit_usd=0,
            gross_loss_usd=0,
            total_fees_usd=0,
            win_rate=0,
            profit_factor=0,
            sharpe_ratio=None,
            max_drawdown_usd=0,
            max_drawdown_pct=0,
            average_profit_per_trade=0,
            average_loss_per_trade=0,
            loss_breakdown=LossBreakdown(),
            avg_execution_time_ms=0,
            max_execution_time_ms=0,
            min_execution_time_ms=0,
            avg_quote_age_ms=0,
            max_quote_age_ms=0,
            stale_quote_count=0,
            warnings=[],
        )

    def _calculate_pnl_metrics(self) -> Dict[str, float]:
        """Calculate aggregate P&L metrics."""
        total_pnl = 0.0
        gross_profit = 0.0
        gross_loss = 0.0
        total_fees = 0.0

        for entry in self._entries:
            pnl = entry.pnl_breakdown
            actual_net = pnl.actual_net_profit or 0.0

            total_pnl += actual_net

            if actual_net > 0:
                gross_profit += actual_net
            else:
                gross_loss += abs(actual_net)

            total_fees += pnl.actual_leg1_fee + pnl.actual_leg2_fee

        return {
            "total_pnl": total_pnl,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "total_fees": total_fees,
        }

    def _calculate_timing_metrics(self) -> Dict[str, float]:
        """Calculate execution timing metrics."""
        durations = [
            e.total_duration_ms for e in self._entries
            if e.total_duration_ms is not None
        ]

        if not durations:
            return {"avg": 0, "max": 0, "min": 0}

        return {
            "avg": sum(durations) / len(durations),
            "max": max(durations),
            "min": min(durations),
        }

    def _calculate_staleness_metrics(self) -> Dict[str, float]:
        """Calculate quote staleness metrics."""
        ages = []
        stale_count = 0

        for entry in self._entries:
            snapshot = entry.input_snapshot
            leg1_age = snapshot.leg1_quote.age_ms
            leg2_age = snapshot.leg2_quote.age_ms

            ages.append(leg1_age)
            ages.append(leg2_age)

            if leg1_age > self.STALE_QUOTE_THRESHOLD_MS:
                stale_count += 1
            if leg2_age > self.STALE_QUOTE_THRESHOLD_MS:
                stale_count += 1

        if not ages:
            return {"avg_age": 0, "max_age": 0, "stale_count": 0}

        return {
            "avg_age": sum(ages) / len(ages),
            "max_age": max(ages),
            "stale_count": stale_count,
        }

    def _calculate_loss_breakdown(self) -> LossBreakdown:
        """Calculate loss breakdown by category."""
        breakdown = LossBreakdown()

        for entry in self._entries:
            pnl = entry.pnl_breakdown

            breakdown.slippage_leg1_usd += max(0, pnl.leg1_slippage_cost)
            breakdown.slippage_leg2_usd += max(0, pnl.leg2_slippage_cost)
            breakdown.fees_exceeded_usd += max(0, pnl.fee_variance)
            breakdown.partial_fill_usd += pnl.partial_fill_loss
            breakdown.rollback_cost_usd += pnl.rollback_loss

            if entry.status == TradeJournalStatus.ROLLED_BACK:
                # Count the full loss from failed leg 2
                if pnl.actual_net_profit is not None:
                    breakdown.failed_leg2_usd += abs(min(0, pnl.actual_net_profit))

            # Check for stale quote losses
            if LossCategory.TIMING_STALE_QUOTE in pnl.loss_categories:
                # Estimate timing loss based on what-if
                breakdown.timing_stale_quote_usd += entry.what_if_analysis.timing_loss

        return breakdown

    def _calculate_win_rate(self) -> float:
        """Calculate win rate as percentage of profitable trades."""
        profitable = sum(
            1 for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit > 0
        )

        total_with_pnl = sum(
            1 for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
        )

        if total_with_pnl == 0:
            return 0.0

        return profitable / total_with_pnl

    def _calculate_profit_factor(self) -> float:
        """Calculate profit factor (gross profit / gross loss)."""
        gross_profit = sum(
            e.pnl_breakdown.actual_net_profit
            for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit > 0
        )

        gross_loss = abs(sum(
            e.pnl_breakdown.actual_net_profit
            for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit < 0
        ))

        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def _calculate_sharpe_ratio(self) -> Optional[float]:
        """
        Calculate Sharpe ratio of returns.

        Assumes risk-free rate of 0 for simplicity.
        """
        returns = [
            e.pnl_breakdown.actual_net_profit
            for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
        ]

        if len(returns) < 2:
            return None

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return None

        return mean_return / std_dev

    def _calculate_max_drawdown(self) -> Tuple[float, float]:
        """Calculate maximum drawdown in USD and percentage."""
        if not self._entries:
            return 0.0, 0.0

        # Sort by execution time
        sorted_entries = sorted(
            self._entries,
            key=lambda e: e.execution_started_at
        )

        cumulative_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0

        for entry in sorted_entries:
            pnl = entry.pnl_breakdown.actual_net_profit or 0.0
            cumulative_pnl += pnl

            if cumulative_pnl > peak:
                peak = cumulative_pnl

            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Calculate percentage (relative to peak)
        drawdown_pct = max_drawdown / peak if peak > 0 else 0.0

        return max_drawdown, drawdown_pct

    def _find_best_trade(self) -> Tuple[Optional[str], Optional[float]]:
        """Find the most profitable trade."""
        best_entry = None
        best_pnl = float('-inf')

        for entry in self._entries:
            pnl = entry.pnl_breakdown.actual_net_profit
            if pnl is not None and pnl > best_pnl:
                best_pnl = pnl
                best_entry = entry

        if best_entry:
            return best_entry.journal_id, best_pnl
        return None, None

    def _find_worst_trade(self) -> Tuple[Optional[str], Optional[float]]:
        """Find the worst performing trade."""
        worst_entry = None
        worst_pnl = float('inf')

        for entry in self._entries:
            pnl = entry.pnl_breakdown.actual_net_profit
            if pnl is not None and pnl < worst_pnl:
                worst_pnl = pnl
                worst_entry = entry

        if worst_entry:
            return worst_entry.journal_id, worst_pnl
        return None, None

    def _calculate_avg_profit(self) -> float:
        """Calculate average profit for winning trades."""
        profits = [
            e.pnl_breakdown.actual_net_profit
            for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit > 0
        ]

        if not profits:
            return 0.0

        return sum(profits) / len(profits)

    def _calculate_avg_loss(self) -> float:
        """Calculate average loss for losing trades."""
        losses = [
            abs(e.pnl_breakdown.actual_net_profit)
            for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit < 0
        ]

        if not losses:
            return 0.0

        return sum(losses) / len(losses)

    def _generate_warnings(
        self,
        win_rate: float,
        loss_breakdown: LossBreakdown,
        drawdown_pct: float,
        staleness: Dict[str, float],
    ) -> List[Warning]:
        """Generate warnings based on analysis results."""
        warnings = []

        # Low win rate warning
        if win_rate < self.MIN_WIN_RATE and len(self._entries) >= 10:
            warnings.append(Warning(
                level=WarningLevel.HIGH,
                category="win_rate",
                message=f"Win rate ({win_rate:.1%}) is below threshold ({self.MIN_WIN_RATE:.1%})",
                metric_value=win_rate,
                threshold=self.MIN_WIN_RATE,
            ))

        # High slippage warning
        total_loss = loss_breakdown.total_loss_usd
        if total_loss > 0:
            slippage_pct = loss_breakdown.total_slippage_usd / total_loss
            if slippage_pct > self.SLIPPAGE_WARNING_PCT:
                warnings.append(Warning(
                    level=WarningLevel.HIGH,
                    category="slippage",
                    message=f"Slippage accounts for {slippage_pct:.1%} of total losses",
                    metric_value=slippage_pct,
                    threshold=self.SLIPPAGE_WARNING_PCT,
                ))

        # High drawdown warning
        if drawdown_pct > self.MAX_DRAWDOWN_PCT:
            warnings.append(Warning(
                level=WarningLevel.HIGH,
                category="drawdown",
                message=f"Max drawdown ({drawdown_pct:.1%}) exceeds threshold ({self.MAX_DRAWDOWN_PCT:.1%})",
                metric_value=drawdown_pct,
                threshold=self.MAX_DRAWDOWN_PCT,
            ))

        # Stale quote warning
        stale_count = staleness["stale_count"]
        if stale_count > 0:
            level = WarningLevel.MEDIUM if stale_count < 5 else WarningLevel.HIGH
            warnings.append(Warning(
                level=level,
                category="stale_quotes",
                message=f"{stale_count} trades had stale quotes at execution",
                affected_trade_ids=self._get_stale_quote_trades(),
                metric_value=float(stale_count),
            ))

        # High rollback rate warning
        rolled_back = sum(
            1 for e in self._entries
            if e.status == TradeJournalStatus.ROLLED_BACK
        )
        if rolled_back > 0 and len(self._entries) >= 5:
            rollback_rate = rolled_back / len(self._entries)
            if rollback_rate > 0.10:  # More than 10% rollbacks
                warnings.append(Warning(
                    level=WarningLevel.HIGH,
                    category="rollbacks",
                    message=f"High rollback rate: {rollback_rate:.1%} of trades rolled back",
                    metric_value=rollback_rate,
                    threshold=0.10,
                ))

        # Partial fill warning
        partial_pct = loss_breakdown.partial_fill_usd / total_loss if total_loss > 0 else 0
        if partial_pct > 0.20:  # More than 20% from partial fills
            warnings.append(Warning(
                level=WarningLevel.MEDIUM,
                category="partial_fills",
                message=f"Partial fills account for {partial_pct:.1%} of losses",
                metric_value=partial_pct,
                threshold=0.20,
            ))

        # Fee variance warning
        fee_pct = loss_breakdown.fees_exceeded_usd / total_loss if total_loss > 0 else 0
        if fee_pct > 0.15:  # More than 15% from fee variance
            warnings.append(Warning(
                level=WarningLevel.MEDIUM,
                category="fee_variance",
                message=f"Fee variance accounts for {fee_pct:.1%} of losses",
                metric_value=fee_pct,
                threshold=0.15,
            ))

        # Sort warnings by severity
        severity_order = {
            WarningLevel.CRITICAL: 0,
            WarningLevel.HIGH: 1,
            WarningLevel.MEDIUM: 2,
            WarningLevel.LOW: 3,
        }
        warnings.sort(key=lambda w: severity_order[w.level])

        return warnings

    def _get_stale_quote_trades(self) -> List[str]:
        """Get journal IDs of trades with stale quotes."""
        stale_ids = []
        for entry in self._entries:
            snapshot = entry.input_snapshot
            if (snapshot.leg1_quote.age_ms > self.STALE_QUOTE_THRESHOLD_MS or
                snapshot.leg2_quote.age_ms > self.STALE_QUOTE_THRESHOLD_MS):
                stale_ids.append(entry.journal_id)
        return stale_ids

    def get_loss_breakdown_by_trade(self) -> List[Dict]:
        """Get per-trade loss breakdown for detailed analysis."""
        results = []

        for entry in self._entries:
            pnl = entry.pnl_breakdown
            results.append({
                "journal_id": entry.journal_id,
                "status": entry.status.value,
                "expected_pnl": pnl.expected_net_profit,
                "actual_pnl": pnl.actual_net_profit,
                "slippage_leg1": pnl.leg1_slippage_cost,
                "slippage_leg2": pnl.leg2_slippage_cost,
                "fee_variance": pnl.fee_variance,
                "partial_fill_loss": pnl.partial_fill_loss,
                "rollback_loss": pnl.rollback_loss,
                "primary_loss_category": pnl.primary_loss_category.value,
            })

        return results

    def get_trades_by_loss_category(
        self,
        category: LossCategory,
    ) -> List[TradeJournalEntry]:
        """Get all trades affected by a specific loss category."""
        return [
            e for e in self._entries
            if category in e.pnl_breakdown.loss_categories
        ]

    def calculate_category_impact(self) -> Dict[str, Dict]:
        """
        Calculate the impact of each loss category.

        Returns dict mapping category to stats.
        """
        categories = {}

        for entry in self._entries:
            for category in entry.pnl_breakdown.loss_categories:
                if category == LossCategory.NONE:
                    continue

                cat_name = category.value
                if cat_name not in categories:
                    categories[cat_name] = {
                        "count": 0,
                        "total_impact": 0.0,
                        "trade_ids": [],
                    }

                categories[cat_name]["count"] += 1
                categories[cat_name]["trade_ids"].append(entry.journal_id)

                # Add the specific loss amount for this category
                pnl = entry.pnl_breakdown
                if category == LossCategory.SLIPPAGE_LEG1:
                    categories[cat_name]["total_impact"] += pnl.leg1_slippage_cost
                elif category == LossCategory.SLIPPAGE_LEG2:
                    categories[cat_name]["total_impact"] += pnl.leg2_slippage_cost
                elif category == LossCategory.FEES_EXCEEDED:
                    categories[cat_name]["total_impact"] += pnl.fee_variance
                elif category == LossCategory.PARTIAL_FILL:
                    categories[cat_name]["total_impact"] += pnl.partial_fill_loss
                elif category == LossCategory.ROLLBACK_COST:
                    categories[cat_name]["total_impact"] += pnl.rollback_loss

        return categories
