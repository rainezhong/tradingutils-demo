"""
Live Metrics Display - Real-time console output during test runs.

Displays running P&L, win rate, trade status, loss category accumulation,
and warnings as they occur during test execution.
"""

import logging
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.testing.models import (
    LossBreakdown,
    SessionAnalysis,
    TradeJournalEntry,
    TradeJournalStatus,
    WarningLevel,
)


logger = logging.getLogger(__name__)


class LiveMetricsDisplay:
    """
    Real-time console display for test session metrics.

    Shows a continuously updating display of:
    - Trade progress and P&L
    - Win rate and status breakdown
    - Cumulative loss breakdown by category
    - Recent warnings
    """

    # Box drawing characters
    BOX_TL = "+"  # Top-left
    BOX_TR = "+"  # Top-right
    BOX_BL = "+"  # Bottom-left
    BOX_BR = "+"  # Bottom-right
    BOX_H = "-"   # Horizontal
    BOX_V = "|"   # Vertical
    BOX_LT = "+"  # Left T
    BOX_RT = "+"  # Right T

    def __init__(
        self,
        session_id: str,
        initial_capital: float = 10000.0,
        width: int = 64,
    ):
        """
        Initialize the live display.

        Args:
            session_id: Session identifier to display
            initial_capital: Starting capital for percentage calculations
            width: Display width in characters
        """
        self.session_id = session_id
        self.initial_capital = initial_capital
        self.width = width

        # State tracking
        self._total_trades = 0
        self._completed_trades = 0
        self._successful_trades = 0
        self._partial_trades = 0
        self._rolled_back_trades = 0
        self._failed_trades = 0

        self._total_pnl = 0.0
        self._gross_profit = 0.0
        self._gross_loss = 0.0

        self._current_trade_status = ""
        self._warnings: List[str] = []

        # Loss breakdown
        self._loss_breakdown = LossBreakdown()

        # Threading
        self._lock = threading.Lock()
        self._running = False
        self._refresh_thread: Optional[threading.Thread] = None

    def start(self, total_trades: int = 0) -> None:
        """
        Start the live display.

        Args:
            total_trades: Expected total number of trades (0 if unknown)
        """
        self._total_trades = total_trades
        self._running = True
        self._display_initial()

    def stop(self) -> None:
        """Stop the live display."""
        self._running = False

    def update_trade_complete(
        self,
        status: TradeJournalStatus,
        pnl: float,
        entry: TradeJournalEntry,
    ) -> None:
        """
        Update display after a trade completes.

        Args:
            status: Final status of the trade
            pnl: P&L of the trade
            entry: Full journal entry for the trade
        """
        with self._lock:
            self._completed_trades += 1

            # Update status counts
            if status == TradeJournalStatus.SUCCESS:
                self._successful_trades += 1
            elif status == TradeJournalStatus.PARTIAL:
                self._partial_trades += 1
            elif status == TradeJournalStatus.ROLLED_BACK:
                self._rolled_back_trades += 1
            else:
                self._failed_trades += 1

            # Update P&L
            self._total_pnl += pnl
            if pnl > 0:
                self._gross_profit += pnl
            else:
                self._gross_loss += abs(pnl)

            # Update loss breakdown
            breakdown = entry.pnl_breakdown
            self._loss_breakdown.slippage_leg1_usd += max(0, breakdown.leg1_slippage_cost)
            self._loss_breakdown.slippage_leg2_usd += max(0, breakdown.leg2_slippage_cost)
            self._loss_breakdown.fees_exceeded_usd += max(0, breakdown.fee_variance)
            self._loss_breakdown.partial_fill_usd += breakdown.partial_fill_loss
            self._loss_breakdown.rollback_cost_usd += breakdown.rollback_loss

            # Check for warnings
            if breakdown.leg1_slippage_cost > entry.input_snapshot.expected_net_spread * 0.05:
                self._add_warning(f"High slippage on {entry.spread_id}")

            self._current_trade_status = ""

        self._refresh_display()

    def update_trade_started(
        self,
        spread_id: str,
        description: str,
    ) -> None:
        """
        Update display when a trade starts.

        Args:
            spread_id: Identifier for the trade
            description: Short description of the trade
        """
        with self._lock:
            self._current_trade_status = f"Executing {description}"
        self._refresh_display()

    def update_leg_status(
        self,
        spread_id: str,
        leg: int,
        status: str,
    ) -> None:
        """
        Update display with leg execution status.

        Args:
            spread_id: Identifier for the trade
            leg: Leg number (1 or 2)
            status: Status message
        """
        with self._lock:
            self._current_trade_status = f"Leg {leg}: {status}"
        self._refresh_display()

    def _add_warning(self, message: str) -> None:
        """Add a warning message (internal, call with lock held)."""
        self._warnings.append(f"[!] {message}")
        # Keep only last 5 warnings
        if len(self._warnings) > 5:
            self._warnings = self._warnings[-5:]

    def _display_initial(self) -> None:
        """Display initial empty state."""
        self._refresh_display()

    def _refresh_display(self) -> None:
        """Refresh the console display."""
        if not self._running:
            return

        with self._lock:
            lines = self._build_display()

        # Clear previous display (move cursor up and overwrite)
        # For simplicity, just print new display
        # In production, could use curses or ANSI escape codes
        output = "\n".join(lines)
        print("\033[H\033[J" + output, file=sys.stderr, flush=True)

    def _build_display(self) -> List[str]:
        """Build the display lines."""
        lines = []
        w = self.width
        inner = w - 2  # Width inside box borders

        # Top border
        lines.append(self.BOX_TL + self.BOX_H * inner + self.BOX_TR)

        # Header
        header = f"LIVE TRADING SESSION: {self.session_id[:30]}"
        lines.append(self._box_line(header.center(inner)))

        # Divider
        lines.append(self.BOX_LT + self.BOX_H * inner + self.BOX_RT)

        # Progress line
        progress = self._completed_trades
        total = self._total_trades if self._total_trades > 0 else "?"
        win_rate = self._calculate_win_rate()

        progress_str = f"Trades: {progress}/{total}    P&L: ${self._total_pnl:+.2f}    Win Rate: {win_rate:.1%}"
        lines.append(self._box_line(progress_str.ljust(inner)))

        # Current trade status
        if self._current_trade_status:
            status_str = f"Current: {self._current_trade_status[:inner-10]}"
            lines.append(self._box_line(status_str.ljust(inner)))

        # Divider
        lines.append(self.BOX_LT + self.BOX_H * inner + self.BOX_RT)

        # Loss breakdown
        lines.append(self._box_line("Loss Breakdown (cumulative):".ljust(inner)))

        total_loss = self._loss_breakdown.total_loss_usd
        if total_loss > 0:
            slippage = self._loss_breakdown.total_slippage_usd
            partial = self._loss_breakdown.partial_fill_usd
            fees = self._loss_breakdown.fees_exceeded_usd
            rollback = self._loss_breakdown.rollback_cost_usd

            slip_pct = (slippage / total_loss * 100) if total_loss > 0 else 0
            part_pct = (partial / total_loss * 100) if total_loss > 0 else 0
            fee_pct = (fees / total_loss * 100) if total_loss > 0 else 0
            roll_pct = (rollback / total_loss * 100) if total_loss > 0 else 0

            loss_line1 = f"  Slippage: ${slippage:.2f} ({slip_pct:.0f}%)  Partial: ${partial:.2f} ({part_pct:.0f}%)"
            loss_line2 = f"  Fees: ${fees:.2f} ({fee_pct:.0f}%)  Rollback: ${rollback:.2f} ({roll_pct:.0f}%)"

            lines.append(self._box_line(loss_line1.ljust(inner)))
            lines.append(self._box_line(loss_line2.ljust(inner)))
        else:
            lines.append(self._box_line("  No losses recorded".ljust(inner)))

        # Warnings section
        if self._warnings:
            lines.append(self.BOX_LT + self.BOX_H * inner + self.BOX_RT)
            for warning in self._warnings[-3:]:  # Show last 3
                lines.append(self._box_line(warning[:inner].ljust(inner)))

        # Bottom border
        lines.append(self.BOX_BL + self.BOX_H * inner + self.BOX_BR)

        return lines

    def _box_line(self, content: str) -> str:
        """Wrap content in box borders."""
        return f"{self.BOX_V}{content}{self.BOX_V}"

    def _calculate_win_rate(self) -> float:
        """Calculate current win rate."""
        if self._completed_trades == 0:
            return 0.0
        winning = self._successful_trades + self._partial_trades
        return winning / self._completed_trades

    def show_final_summary(self, analysis: SessionAnalysis) -> None:
        """
        Display final summary after session completes.

        Args:
            analysis: Complete session analysis
        """
        lines = []
        w = self.width
        inner = w - 2

        lines.append("")
        lines.append(self.BOX_TL + self.BOX_H * inner + self.BOX_TR)
        lines.append(self._box_line("SESSION COMPLETE".center(inner)))
        lines.append(self.BOX_LT + self.BOX_H * inner + self.BOX_RT)

        # Final metrics
        lines.append(self._box_line(f"Total Trades: {analysis.total_trades}".ljust(inner)))
        lines.append(self._box_line(f"Win Rate: {analysis.win_rate:.1%}".ljust(inner)))
        lines.append(self._box_line(f"Total P&L: ${analysis.total_pnl_usd:+.2f}".ljust(inner)))
        lines.append(self._box_line(f"Profit Factor: {analysis.profit_factor:.2f}".ljust(inner)))
        lines.append(self._box_line(f"Max Drawdown: ${analysis.max_drawdown_usd:.2f} ({analysis.max_drawdown_pct:.1%})".ljust(inner)))

        # Warnings summary
        if analysis.warnings:
            lines.append(self.BOX_LT + self.BOX_H * inner + self.BOX_RT)
            lines.append(self._box_line(f"Warnings: {len(analysis.warnings)}".ljust(inner)))
            for warning in analysis.warnings[:5]:
                level = warning.level.value.upper()
                msg = f"  [{level}] {warning.message[:inner-12]}"
                lines.append(self._box_line(msg.ljust(inner)))

        lines.append(self.BOX_BL + self.BOX_H * inner + self.BOX_BR)
        lines.append("")

        output = "\n".join(lines)
        print(output, file=sys.stderr, flush=True)

    def get_current_metrics(self) -> Dict[str, Any]:
        """
        Get current metrics as a dictionary.

        Returns:
            Dictionary with current metric values
        """
        with self._lock:
            return {
                "completed_trades": self._completed_trades,
                "total_trades": self._total_trades,
                "successful": self._successful_trades,
                "partial": self._partial_trades,
                "rolled_back": self._rolled_back_trades,
                "failed": self._failed_trades,
                "total_pnl": self._total_pnl,
                "gross_profit": self._gross_profit,
                "gross_loss": self._gross_loss,
                "win_rate": self._calculate_win_rate(),
                "loss_breakdown": self._loss_breakdown.to_dict(),
                "warnings": self._warnings.copy(),
            }


class QuietDisplay:
    """
    Minimal display that only logs summary at end.

    Use this for automated testing where real-time display is not needed.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._trade_count = 0
        self._total_pnl = 0.0

    def start(self, total_trades: int = 0) -> None:
        logger.info(f"Starting session {self.session_id} with {total_trades} expected trades")

    def stop(self) -> None:
        pass

    def update_trade_complete(
        self,
        status: TradeJournalStatus,
        pnl: float,
        entry: TradeJournalEntry,
    ) -> None:
        self._trade_count += 1
        self._total_pnl += pnl
        if self._trade_count % 10 == 0:
            logger.info(f"Progress: {self._trade_count} trades, P&L: ${self._total_pnl:+.2f}")

    def update_trade_started(self, spread_id: str, description: str) -> None:
        pass

    def update_leg_status(self, spread_id: str, leg: int, status: str) -> None:
        pass

    def show_final_summary(self, analysis: SessionAnalysis) -> None:
        logger.info(
            f"Session {self.session_id} complete: "
            f"{analysis.total_trades} trades, "
            f"P&L: ${analysis.total_pnl_usd:+.2f}, "
            f"Win rate: {analysis.win_rate:.1%}"
        )
