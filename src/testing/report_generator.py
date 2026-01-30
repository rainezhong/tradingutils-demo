"""
Report Generator - Human and Claude-friendly report generation.

Generates both Markdown reports for human readability and JSON reports
for machine parsing and analysis.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.testing.models import (
    LossBreakdown,
    SessionAnalysis,
    TradeJournalEntry,
    WarningLevel,
)
from src.testing.session_analyzer import SessionAnalyzer
from src.testing.trade_journal import TradeJournal


logger = logging.getLogger(__name__)


class ReportGenerator:
    """
    Generates analysis reports in multiple formats.

    Produces both human-readable Markdown reports and machine-parseable
    JSON reports from trade journal and analysis data.
    """

    def __init__(
        self,
        journal: TradeJournal,
        analyzer: Optional[SessionAnalyzer] = None,
    ):
        """
        Initialize the report generator.

        Args:
            journal: TradeJournal with recorded trades
            analyzer: Optional SessionAnalyzer (created if not provided)
        """
        self.journal = journal
        self.analyzer = analyzer or SessionAnalyzer(journal)

    def generate_markdown_report(
        self,
        analysis: SessionAnalysis,
        output_path: Optional[Path] = None,
    ) -> str:
        """
        Generate a human-readable Markdown report.

        Args:
            analysis: SessionAnalysis from analyzer
            output_path: Optional path to save report

        Returns:
            Markdown formatted report string
        """
        lines = []

        # Header
        lines.append(f"# Arbitrage Trading Session Report")
        lines.append(f"")
        lines.append(f"**Session ID:** {analysis.session_id}")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Duration:** {self._format_duration(analysis.duration_seconds)}")
        lines.append(f"")

        # Executive Summary
        lines.append(f"## Executive Summary")
        lines.append(f"")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Trades | {analysis.total_trades} |")
        lines.append(f"| Win Rate | {analysis.win_rate:.1%} |")
        lines.append(f"| Total P&L | ${analysis.total_pnl_usd:+.2f} |")
        lines.append(f"| Profit Factor | {analysis.profit_factor:.2f} |")
        if analysis.sharpe_ratio is not None:
            lines.append(f"| Sharpe Ratio | {analysis.sharpe_ratio:.2f} |")
        lines.append(f"| Max Drawdown | ${analysis.max_drawdown_usd:.2f} ({analysis.max_drawdown_pct:.1%}) |")
        lines.append(f"")

        # Trade Breakdown
        lines.append(f"## Trade Breakdown")
        lines.append(f"")
        lines.append(f"| Status | Count | Percentage |")
        lines.append(f"|--------|-------|------------|")
        total = analysis.total_trades
        if total > 0:
            lines.append(f"| Successful | {analysis.successful_trades} | {analysis.successful_trades/total:.1%} |")
            lines.append(f"| Partial | {analysis.partial_trades} | {analysis.partial_trades/total:.1%} |")
            lines.append(f"| Rolled Back | {analysis.rolled_back_trades} | {analysis.rolled_back_trades/total:.1%} |")
            lines.append(f"| Failed | {analysis.failed_trades} | {analysis.failed_trades/total:.1%} |")
        lines.append(f"")

        # P&L Details
        lines.append(f"## P&L Details")
        lines.append(f"")
        lines.append(f"| Metric | Amount |")
        lines.append(f"|--------|--------|")
        lines.append(f"| Gross Profit | ${analysis.gross_profit_usd:+.2f} |")
        lines.append(f"| Gross Loss | ${-analysis.gross_loss_usd:.2f} |")
        lines.append(f"| Total Fees | ${analysis.total_fees_usd:.2f} |")
        lines.append(f"| **Net P&L** | **${analysis.total_pnl_usd:+.2f}** |")
        lines.append(f"")
        lines.append(f"- Average profit per winning trade: ${analysis.average_profit_per_trade:.2f}")
        lines.append(f"- Average loss per losing trade: ${analysis.average_loss_per_trade:.2f}")
        lines.append(f"")

        # Loss Breakdown
        lines.append(f"## Where Money Was Lost")
        lines.append(f"")
        breakdown = analysis.loss_breakdown
        total_loss = breakdown.total_loss_usd

        if total_loss > 0:
            lines.append(f"| Category | Amount | % of Total |")
            lines.append(f"|----------|--------|------------|")

            loss_items = [
                ("Slippage (Leg 1)", breakdown.slippage_leg1_usd),
                ("Slippage (Leg 2)", breakdown.slippage_leg2_usd),
                ("Fee Variance", breakdown.fees_exceeded_usd),
                ("Partial Fills", breakdown.partial_fill_usd),
                ("Failed Leg 2", breakdown.failed_leg2_usd),
                ("Rollback Costs", breakdown.rollback_cost_usd),
                ("Stale Quotes", breakdown.timing_stale_quote_usd),
            ]

            for name, amount in loss_items:
                if amount > 0:
                    pct = amount / total_loss
                    lines.append(f"| {name} | ${amount:.2f} | {pct:.1%} |")

            lines.append(f"| **Total** | **${total_loss:.2f}** | **100%** |")
        else:
            lines.append(f"*No losses recorded*")
        lines.append(f"")

        # Warnings
        lines.append(f"## Warnings")
        lines.append(f"")

        if analysis.warnings:
            for warning in analysis.warnings:
                level_icon = self._get_warning_icon(warning.level)
                lines.append(f"- {level_icon} **{warning.level.value.upper()}:** {warning.message}")
        else:
            lines.append(f"*No warnings generated*")
        lines.append(f"")

        # Best/Worst Trades
        lines.append(f"## Notable Trades")
        lines.append(f"")

        if analysis.best_trade_id:
            lines.append(f"### Best Trade")
            lines.append(f"- **ID:** {analysis.best_trade_id}")
            lines.append(f"- **P&L:** ${analysis.best_trade_pnl:+.2f}")
            best_entry = self.journal.get_entry(analysis.best_trade_id)
            if best_entry:
                lines.append(f"- **Edge:** {best_entry.decision_record.edge_cents:.2f} cents")
                lines.append(f"- **Duration:** {best_entry.total_duration_ms}ms")
            lines.append(f"")

        if analysis.worst_trade_id:
            lines.append(f"### Worst Trade")
            lines.append(f"- **ID:** {analysis.worst_trade_id}")
            lines.append(f"- **P&L:** ${analysis.worst_trade_pnl:+.2f}")
            worst_entry = self.journal.get_entry(analysis.worst_trade_id)
            if worst_entry:
                lines.append(f"- **Primary Loss:** {worst_entry.pnl_breakdown.primary_loss_category.value}")
                lines.append(f"- **Duration:** {worst_entry.total_duration_ms}ms")
            lines.append(f"")

        # Execution Timing
        lines.append(f"## Execution Timing")
        lines.append(f"")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Average | {analysis.avg_execution_time_ms:.0f}ms |")
        lines.append(f"| Maximum | {analysis.max_execution_time_ms:.0f}ms |")
        lines.append(f"| Minimum | {analysis.min_execution_time_ms:.0f}ms |")
        lines.append(f"")

        # Quote Staleness
        lines.append(f"## Quote Staleness")
        lines.append(f"")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Average Age | {analysis.avg_quote_age_ms:.0f}ms |")
        lines.append(f"| Maximum Age | {analysis.max_quote_age_ms:.0f}ms |")
        lines.append(f"| Stale Quote Count | {analysis.stale_quote_count} |")
        lines.append(f"")

        # Footer
        lines.append(f"---")
        lines.append(f"*Report generated by Arbitrage Testing Framework*")

        report = "\n".join(lines)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(report)
            logger.info(f"Saved Markdown report to {output_path}")

        return report

    def generate_json_report(
        self,
        analysis: SessionAnalysis,
        output_path: Optional[Path] = None,
        include_all_trades: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate a machine-parseable JSON report.

        Args:
            analysis: SessionAnalysis from analyzer
            output_path: Optional path to save report
            include_all_trades: Whether to include all trade details

        Returns:
            Dictionary containing the full report
        """
        report = {
            "meta": {
                "session_id": analysis.session_id,
                "generated_at": datetime.now().isoformat(),
                "generator": "ArbitrageTestingFramework",
                "version": "1.0.0",
            },
            "summary": {
                "total_trades": analysis.total_trades,
                "successful_trades": analysis.successful_trades,
                "partial_trades": analysis.partial_trades,
                "rolled_back_trades": analysis.rolled_back_trades,
                "failed_trades": analysis.failed_trades,
                "win_rate": analysis.win_rate,
                "total_pnl_usd": analysis.total_pnl_usd,
                "profit_factor": analysis.profit_factor,
                "sharpe_ratio": analysis.sharpe_ratio,
            },
            "pnl": {
                "gross_profit_usd": analysis.gross_profit_usd,
                "gross_loss_usd": analysis.gross_loss_usd,
                "total_fees_usd": analysis.total_fees_usd,
                "net_pnl_usd": analysis.total_pnl_usd,
                "average_profit_per_trade": analysis.average_profit_per_trade,
                "average_loss_per_trade": analysis.average_loss_per_trade,
            },
            "risk": {
                "max_drawdown_usd": analysis.max_drawdown_usd,
                "max_drawdown_pct": analysis.max_drawdown_pct,
            },
            "loss_breakdown": {
                "slippage": {
                    "leg1_usd": analysis.loss_breakdown.slippage_leg1_usd,
                    "leg2_usd": analysis.loss_breakdown.slippage_leg2_usd,
                    "total_usd": analysis.loss_breakdown.total_slippage_usd,
                },
                "fees_exceeded_usd": analysis.loss_breakdown.fees_exceeded_usd,
                "partial_fills_usd": analysis.loss_breakdown.partial_fill_usd,
                "failed_leg2_usd": analysis.loss_breakdown.failed_leg2_usd,
                "rollback_costs_usd": analysis.loss_breakdown.rollback_cost_usd,
                "stale_quotes_usd": analysis.loss_breakdown.timing_stale_quote_usd,
                "total_usd": analysis.loss_breakdown.total_loss_usd,
            },
            "timing": {
                "session_duration_seconds": analysis.duration_seconds,
                "execution_time_ms": {
                    "average": analysis.avg_execution_time_ms,
                    "maximum": analysis.max_execution_time_ms,
                    "minimum": analysis.min_execution_time_ms,
                },
                "quote_age_ms": {
                    "average": analysis.avg_quote_age_ms,
                    "maximum": analysis.max_quote_age_ms,
                    "stale_count": analysis.stale_quote_count,
                },
            },
            "warnings": [w.to_dict() for w in analysis.warnings],
            "notable_trades": {
                "best": {
                    "trade_id": analysis.best_trade_id,
                    "pnl_usd": analysis.best_trade_pnl,
                },
                "worst": {
                    "trade_id": analysis.worst_trade_id,
                    "pnl_usd": analysis.worst_trade_pnl,
                },
            },
        }

        if include_all_trades:
            report["all_trades"] = [
                self._entry_to_json_summary(entry)
                for entry in self.journal.entries
            ]

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"Saved JSON report to {output_path}")

        return report

    def _entry_to_json_summary(self, entry: TradeJournalEntry) -> Dict[str, Any]:
        """Convert a journal entry to a JSON-friendly summary."""
        return {
            "journal_id": entry.journal_id,
            "spread_id": entry.spread_id,
            "status": entry.status.value,
            "detected_at": entry.detected_at.isoformat(),
            "duration_ms": entry.total_duration_ms,
            "input": {
                "leg1": {
                    "exchange": entry.input_snapshot.leg1_quote.exchange,
                    "ticker": entry.input_snapshot.leg1_quote.ticker,
                    "bid": entry.input_snapshot.leg1_quote.bid,
                    "ask": entry.input_snapshot.leg1_quote.ask,
                    "age_ms": entry.input_snapshot.leg1_quote.age_ms,
                },
                "leg2": {
                    "exchange": entry.input_snapshot.leg2_quote.exchange,
                    "ticker": entry.input_snapshot.leg2_quote.ticker,
                    "bid": entry.input_snapshot.leg2_quote.bid,
                    "ask": entry.input_snapshot.leg2_quote.ask,
                    "age_ms": entry.input_snapshot.leg2_quote.age_ms,
                },
                "expected_profit": entry.input_snapshot.expected_net_spread,
            },
            "decision": {
                "rank": entry.decision_record.opportunity_rank,
                "total_opportunities": entry.decision_record.total_opportunities,
                "edge_cents": entry.decision_record.edge_cents,
                "roi_pct": entry.decision_record.roi_pct,
            },
            "execution": {
                "leg1_filled": entry.pnl_breakdown.leg1_actual_price is not None,
                "leg1_price": entry.pnl_breakdown.leg1_actual_price,
                "leg1_size": entry.pnl_breakdown.leg1_actual_size,
                "leg2_filled": entry.pnl_breakdown.leg2_actual_price is not None,
                "leg2_price": entry.pnl_breakdown.leg2_actual_price,
                "leg2_size": entry.pnl_breakdown.leg2_actual_size,
            },
            "pnl": {
                "expected_gross": entry.pnl_breakdown.expected_gross_profit,
                "expected_net": entry.pnl_breakdown.expected_net_profit,
                "actual_gross": entry.pnl_breakdown.actual_gross_profit,
                "actual_net": entry.pnl_breakdown.actual_net_profit,
                "slippage_leg1": entry.pnl_breakdown.leg1_slippage_cost,
                "slippage_leg2": entry.pnl_breakdown.leg2_slippage_cost,
                "fee_variance": entry.pnl_breakdown.fee_variance,
                "partial_fill_loss": entry.pnl_breakdown.partial_fill_loss,
                "rollback_loss": entry.pnl_breakdown.rollback_loss,
                "primary_loss_category": entry.pnl_breakdown.primary_loss_category.value,
            },
            "what_if": {
                "optimal_profit": entry.what_if_analysis.optimal_profit,
                "maker_fee_savings": entry.what_if_analysis.maker_fee_savings,
                "timing_loss": entry.what_if_analysis.timing_loss,
            },
            "event_count": len(entry.execution_events),
            "error": entry.error_message,
        }

    def generate_summary_table(
        self,
        analysis: SessionAnalysis,
    ) -> str:
        """
        Generate a compact summary table for quick reference.

        Args:
            analysis: SessionAnalysis from analyzer

        Returns:
            ASCII table summary
        """
        lines = []
        lines.append("+" + "-" * 58 + "+")
        lines.append(f"| {'TRADING SESSION SUMMARY':^56} |")
        lines.append("+" + "-" * 58 + "+")
        lines.append(f"| Session: {analysis.session_id:<46} |")
        lines.append(f"| Duration: {self._format_duration(analysis.duration_seconds):<45} |")
        lines.append("+" + "-" * 58 + "+")
        lines.append(f"| Trades: {analysis.total_trades:<4}  Win Rate: {analysis.win_rate:>5.1%}  P&L: ${analysis.total_pnl_usd:>+8.2f} |")
        lines.append(f"| Success: {analysis.successful_trades:<3}  Partial: {analysis.partial_trades:<3}  Rolled: {analysis.rolled_back_trades:<3}  Failed: {analysis.failed_trades:<3} |")
        lines.append("+" + "-" * 58 + "+")
        lines.append(f"| Profit Factor: {analysis.profit_factor:>5.2f}  Max Drawdown: ${analysis.max_drawdown_usd:>8.2f} |")
        lines.append("+" + "-" * 58 + "+")

        if analysis.warnings:
            lines.append(f"| WARNINGS: {len(analysis.warnings):<46} |")
            for warning in analysis.warnings[:3]:  # Show top 3 warnings
                msg = warning.message[:50]
                lines.append(f"|   [{warning.level.value.upper():^6}] {msg:<43} |")
            if len(analysis.warnings) > 3:
                lines.append(f"|   ... and {len(analysis.warnings) - 3} more warnings{' ' * 31} |")
            lines.append("+" + "-" * 58 + "+")

        return "\n".join(lines)

    def generate_loss_table(
        self,
        breakdown: LossBreakdown,
    ) -> str:
        """
        Generate a formatted loss breakdown table.

        Args:
            breakdown: LossBreakdown data

        Returns:
            ASCII table of losses
        """
        total = breakdown.total_loss_usd
        if total == 0:
            return "No losses recorded."

        lines = []
        lines.append("+" + "-" * 48 + "+")
        lines.append(f"| {'LOSS BREAKDOWN':^46} |")
        lines.append("+" + "-" * 48 + "+")
        lines.append(f"| {'Category':<24} | {'Amount':>10} | {'%':>6} |")
        lines.append("+" + "-" * 48 + "+")

        items = [
            ("Slippage (Leg 1)", breakdown.slippage_leg1_usd),
            ("Slippage (Leg 2)", breakdown.slippage_leg2_usd),
            ("Fee Variance", breakdown.fees_exceeded_usd),
            ("Partial Fills", breakdown.partial_fill_usd),
            ("Failed Leg 2", breakdown.failed_leg2_usd),
            ("Rollback Costs", breakdown.rollback_cost_usd),
            ("Stale Quotes", breakdown.timing_stale_quote_usd),
        ]

        for name, amount in items:
            if amount > 0:
                pct = (amount / total) * 100
                lines.append(f"| {name:<24} | ${amount:>8.2f} | {pct:>5.1f}% |")

        lines.append("+" + "-" * 48 + "+")
        lines.append(f"| {'TOTAL':<24} | ${total:>8.2f} | {'100%':>6} |")
        lines.append("+" + "-" * 48 + "+")

        return "\n".join(lines)

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def _get_warning_icon(self, level: WarningLevel) -> str:
        """Get icon for warning level."""
        icons = {
            WarningLevel.LOW: "[!]",
            WarningLevel.MEDIUM: "[!!]",
            WarningLevel.HIGH: "[!!!]",
            WarningLevel.CRITICAL: "[!!!!]",
        }
        return icons.get(level, "[?]")

    def generate_all_reports(
        self,
        analysis: SessionAnalysis,
        output_dir: Path,
    ) -> Dict[str, Path]:
        """
        Generate all report formats and save to directory.

        Args:
            analysis: SessionAnalysis from analyzer
            output_dir: Directory to save reports

        Returns:
            Dictionary mapping report type to file path
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        paths = {}

        # Markdown report
        md_path = output_dir / f"report_{analysis.session_id}.md"
        self.generate_markdown_report(analysis, md_path)
        paths["markdown"] = md_path

        # JSON report
        json_path = output_dir / f"report_{analysis.session_id}.json"
        self.generate_json_report(analysis, json_path)
        paths["json"] = json_path

        # Summary text
        summary_path = output_dir / f"summary_{analysis.session_id}.txt"
        summary = self.generate_summary_table(analysis)
        summary += "\n\n"
        summary += self.generate_loss_table(analysis.loss_breakdown)
        with open(summary_path, "w") as f:
            f.write(summary)
        paths["summary"] = summary_path

        logger.info(f"Generated all reports in {output_dir}")

        return paths
