"""
Trade Journal - Per-trade instrumentation and recording.

Records every trade with granular detail including input snapshots,
decision records, execution events, P&L breakdowns, and what-if analysis.

Integrates with SpreadExecutor via callbacks.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.testing.models import (
    DecisionRecord,
    ExecutionEvent,
    ExecutionEventType,
    InputSnapshot,
    LossCategory,
    PnLBreakdown,
    QuoteSnapshot,
    TradeJournalEntry,
    TradeJournalStatus,
    WhatIfAnalysis,
)


logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Records detailed trade execution data for analysis.

    Integrates with SpreadExecutor callbacks to capture execution events
    in real-time and provides comprehensive trade journaling.
    """

    def __init__(
        self,
        session_id: str,
        output_dir: Optional[Path] = None,
        auto_save: bool = True,
        fee_calculator: Optional[Any] = None,
    ):
        """
        Initialize the trade journal.

        Args:
            session_id: Unique identifier for this trading session
            output_dir: Directory to save journal files (optional)
            auto_save: Whether to auto-save after each trade completes
            fee_calculator: FeeCalculator instance for computing fees
        """
        self.session_id = session_id
        self.output_dir = output_dir
        self.auto_save = auto_save
        self.fee_calculator = fee_calculator

        self._entries: List[TradeJournalEntry] = []
        self._active_entries: Dict[str, _ActiveEntry] = {}
        self._callbacks: Dict[str, List[Callable]] = {
            "on_trade_started": [],
            "on_trade_completed": [],
            "on_event": [],
        }

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def entries(self) -> List[TradeJournalEntry]:
        """Get all completed journal entries."""
        return self._entries.copy()

    def register_callback(
        self,
        event_type: str,
        callback: Callable,
    ) -> None:
        """
        Register a callback for journal events.

        Args:
            event_type: One of "on_trade_started", "on_trade_completed", "on_event"
            callback: Function to call when event occurs
        """
        if event_type not in self._callbacks:
            raise ValueError(f"Unknown event type: {event_type}")
        self._callbacks[event_type].append(callback)

    def _fire_callbacks(self, event_type: str, *args, **kwargs) -> None:
        """Fire all registered callbacks for an event type."""
        for callback in self._callbacks.get(event_type, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                logger.error(f"Callback error for {event_type}: {e}")

    def start_trade(
        self,
        spread_id: str,
        input_snapshot: InputSnapshot,
        decision_record: DecisionRecord,
    ) -> str:
        """
        Start recording a new trade.

        Args:
            spread_id: Identifier for the spread being executed
            input_snapshot: System state at detection time
            decision_record: Why this trade was selected

        Returns:
            journal_id: Unique identifier for this journal entry
        """
        journal_id = f"JOURNAL-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now()

        active = _ActiveEntry(
            journal_id=journal_id,
            spread_id=spread_id,
            session_id=self.session_id,
            detected_at=now,
            execution_started_at=now,
            input_snapshot=input_snapshot,
            decision_record=decision_record,
            events=[],
        )

        self._active_entries[spread_id] = active

        # Record detection event
        self._add_event(
            spread_id,
            ExecutionEventType.DETECTION,
            {"opportunity_rank": decision_record.opportunity_rank},
        )

        self._fire_callbacks("on_trade_started", journal_id, spread_id)

        return journal_id

    def record_event(
        self,
        spread_id: str,
        event_type: ExecutionEventType,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record an execution event for a trade.

        Args:
            spread_id: Identifier of the spread
            event_type: Type of event
            details: Additional event details
        """
        self._add_event(spread_id, event_type, details or {})

    def _add_event(
        self,
        spread_id: str,
        event_type: ExecutionEventType,
        details: Dict[str, Any],
    ) -> None:
        """Add an event to an active entry."""
        active = self._active_entries.get(spread_id)
        if not active:
            logger.warning(f"No active entry for spread {spread_id}")
            return

        now = datetime.now()
        elapsed_ms = (now - active.execution_started_at).total_seconds() * 1000

        event = ExecutionEvent(
            event_type=event_type,
            timestamp=now,
            elapsed_ms=elapsed_ms,
            details=details,
        )
        active.events.append(event)

        self._fire_callbacks("on_event", spread_id, event)

    def complete_trade(
        self,
        spread_id: str,
        status: TradeJournalStatus,
        leg1_actual_price: Optional[float],
        leg1_actual_size: int,
        leg2_actual_price: Optional[float],
        leg2_actual_size: int,
        actual_leg1_fee: float,
        actual_leg2_fee: float,
        rollback_loss: float = 0.0,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TradeJournalEntry:
        """
        Complete a trade and finalize the journal entry.

        Args:
            spread_id: Identifier of the spread
            status: Final status of the trade
            leg1_actual_price: Actual fill price for leg 1 (None if not filled)
            leg1_actual_size: Actual filled size for leg 1
            leg2_actual_price: Actual fill price for leg 2 (None if not filled)
            leg2_actual_size: Actual filled size for leg 2
            actual_leg1_fee: Actual fee paid for leg 1
            actual_leg2_fee: Actual fee paid for leg 2
            rollback_loss: Loss from rollback (if any)
            error_message: Error message if failed
            metadata: Additional metadata

        Returns:
            Completed TradeJournalEntry
        """
        active = self._active_entries.pop(spread_id, None)
        if not active:
            raise ValueError(f"No active entry for spread {spread_id}")

        now = datetime.now()

        # Record completion event
        self._add_event_direct(
            active,
            ExecutionEventType.COMPLETED,
            {"status": status.value},
        )

        # Calculate P&L breakdown
        pnl_breakdown = self._calculate_pnl_breakdown(
            active.input_snapshot,
            leg1_actual_price,
            leg1_actual_size,
            leg2_actual_price,
            leg2_actual_size,
            actual_leg1_fee,
            actual_leg2_fee,
            rollback_loss,
            status,
        )

        # Calculate what-if analysis
        what_if = self._calculate_what_if(
            active.input_snapshot,
            pnl_breakdown,
        )

        # Create final entry
        entry = TradeJournalEntry(
            journal_id=active.journal_id,
            spread_id=spread_id,
            session_id=self.session_id,
            detected_at=active.detected_at,
            execution_started_at=active.execution_started_at,
            execution_completed_at=now,
            total_duration_ms=int((now - active.execution_started_at).total_seconds() * 1000),
            input_snapshot=active.input_snapshot,
            decision_record=active.decision_record,
            execution_events=active.events,
            pnl_breakdown=pnl_breakdown,
            what_if_analysis=what_if,
            status=status,
            error_message=error_message,
            metadata=metadata or {},
        )

        self._entries.append(entry)

        if self.auto_save and self.output_dir:
            self._save_entry(entry)

        self._fire_callbacks("on_trade_completed", entry)

        return entry

    def _add_event_direct(
        self,
        active: "_ActiveEntry",
        event_type: ExecutionEventType,
        details: Dict[str, Any],
    ) -> None:
        """Add an event directly to an active entry object."""
        now = datetime.now()
        elapsed_ms = (now - active.execution_started_at).total_seconds() * 1000

        event = ExecutionEvent(
            event_type=event_type,
            timestamp=now,
            elapsed_ms=elapsed_ms,
            details=details,
        )
        active.events.append(event)

    def _calculate_pnl_breakdown(
        self,
        snapshot: InputSnapshot,
        leg1_actual_price: Optional[float],
        leg1_actual_size: int,
        leg2_actual_price: Optional[float],
        leg2_actual_size: int,
        actual_leg1_fee: float,
        actual_leg2_fee: float,
        rollback_loss: float,
        status: TradeJournalStatus,
    ) -> PnLBreakdown:
        """Calculate detailed P&L breakdown."""
        leg1_expected = snapshot.leg1_quote.ask  # Buy at ask
        leg2_expected = snapshot.leg2_quote.bid  # Sell at bid
        expected_size = snapshot.leg1_quote.ask_size  # Assuming same size

        # Calculate expected fees
        expected_leg1_fee = snapshot.expected_fees / 2  # Approximate split
        expected_leg2_fee = snapshot.expected_fees / 2

        # Slippage calculations (positive = loss)
        leg1_slippage = 0.0
        if leg1_actual_price is not None:
            # For buy side: paying more than expected = loss
            leg1_slippage = (leg1_actual_price - leg1_expected) * leg1_actual_size

        leg2_slippage = 0.0
        if leg2_actual_price is not None:
            # For sell side: receiving less than expected = loss
            leg2_slippage = (leg2_expected - leg2_actual_price) * leg2_actual_size

        # Fee variance (positive = loss)
        fee_variance = (actual_leg1_fee + actual_leg2_fee) - snapshot.expected_fees

        # Partial fill loss
        partial_fill_loss = 0.0
        if leg1_actual_size < expected_size or leg2_actual_size < expected_size:
            filled_ratio = min(leg1_actual_size, leg2_actual_size) / expected_size
            partial_fill_loss = snapshot.expected_net_spread * (1 - filled_ratio)

        # Calculate actual P&L
        actual_gross = None
        actual_net = None

        if leg1_actual_price is not None and leg2_actual_price is not None:
            # Both legs filled (at least partially)
            filled_size = min(leg1_actual_size, leg2_actual_size)
            actual_gross = (leg2_actual_price - leg1_actual_price) * filled_size
            actual_net = actual_gross - actual_leg1_fee - actual_leg2_fee - rollback_loss
        elif leg1_actual_price is not None and leg2_actual_price is None:
            # Only leg 1 filled (rollback scenario)
            actual_gross = 0.0
            actual_net = -actual_leg1_fee - rollback_loss

        # Determine loss categories
        loss_categories = []
        total_loss = 0.0

        if leg1_slippage > 0:
            loss_categories.append(LossCategory.SLIPPAGE_LEG1)
            total_loss += leg1_slippage

        if leg2_slippage > 0:
            loss_categories.append(LossCategory.SLIPPAGE_LEG2)
            total_loss += leg2_slippage

        if fee_variance > 0:
            loss_categories.append(LossCategory.FEES_EXCEEDED)
            total_loss += fee_variance

        if partial_fill_loss > 0:
            loss_categories.append(LossCategory.PARTIAL_FILL)
            total_loss += partial_fill_loss

        if rollback_loss > 0:
            loss_categories.append(LossCategory.ROLLBACK_COST)
            total_loss += rollback_loss

        if status == TradeJournalStatus.ROLLED_BACK:
            loss_categories.append(LossCategory.FAILED_LEG2)

        # Check for stale quotes
        if snapshot.leg1_quote.age_ms > 2000 or snapshot.leg2_quote.age_ms > 2000:
            loss_categories.append(LossCategory.TIMING_STALE_QUOTE)

        # Determine primary loss category (largest loss)
        if not loss_categories:
            primary_category = LossCategory.NONE
        elif len(loss_categories) == 1:
            primary_category = loss_categories[0]
        else:
            primary_category = LossCategory.MULTIPLE
            # Find the largest contributor
            category_amounts = {
                LossCategory.SLIPPAGE_LEG1: leg1_slippage,
                LossCategory.SLIPPAGE_LEG2: leg2_slippage,
                LossCategory.FEES_EXCEEDED: fee_variance,
                LossCategory.PARTIAL_FILL: partial_fill_loss,
                LossCategory.ROLLBACK_COST: rollback_loss,
            }
            max_loss = 0
            for cat in loss_categories:
                if cat in category_amounts and category_amounts[cat] > max_loss:
                    max_loss = category_amounts[cat]
                    primary_category = cat

        return PnLBreakdown(
            expected_gross_profit=snapshot.expected_gross_spread,
            expected_net_profit=snapshot.expected_net_spread,
            expected_total_fees=snapshot.expected_fees,
            leg1_expected_price=leg1_expected,
            leg1_actual_price=leg1_actual_price,
            leg1_expected_size=expected_size,
            leg1_actual_size=leg1_actual_size,
            leg1_slippage_cost=leg1_slippage,
            leg2_expected_price=leg2_expected,
            leg2_actual_price=leg2_actual_price,
            leg2_expected_size=expected_size,
            leg2_actual_size=leg2_actual_size,
            leg2_slippage_cost=leg2_slippage,
            expected_leg1_fee=expected_leg1_fee,
            actual_leg1_fee=actual_leg1_fee,
            expected_leg2_fee=expected_leg2_fee,
            actual_leg2_fee=actual_leg2_fee,
            fee_variance=fee_variance,
            partial_fill_loss=partial_fill_loss,
            rollback_loss=rollback_loss,
            actual_gross_profit=actual_gross,
            actual_net_profit=actual_net,
            primary_loss_category=primary_category,
            loss_categories=loss_categories,
            total_loss_amount=total_loss,
        )

    def _calculate_what_if(
        self,
        snapshot: InputSnapshot,
        pnl: PnLBreakdown,
    ) -> WhatIfAnalysis:
        """Calculate what-if analysis comparing to optimal execution."""
        # Optimal prices are the detection-time prices
        optimal_leg1 = snapshot.leg1_quote.ask
        optimal_leg2 = snapshot.leg2_quote.bid

        # Calculate optimal profit (no slippage, maker fees)
        maker_fee_rate = 0.0175  # Maker fee rate
        taker_fee_rate = 0.07    # Taker fee rate

        size = pnl.leg1_expected_size
        optimal_gross = (optimal_leg2 - optimal_leg1) * size
        optimal_maker_fees = optimal_gross * maker_fee_rate * 2  # Both legs maker
        optimal_profit = optimal_gross - optimal_maker_fees

        # Maker fee savings potential
        actual_fees = pnl.actual_leg1_fee + pnl.actual_leg2_fee
        optimal_fees = optimal_maker_fees
        maker_fee_savings = actual_fees - optimal_fees if actual_fees > optimal_fees else 0

        # Timing loss due to quote staleness
        timing_loss = 0.0
        if snapshot.leg1_quote.age_ms > 1000:
            # Estimate 1% price movement per second of staleness
            timing_loss += (snapshot.leg1_quote.age_ms / 1000) * 0.01 * size
        if snapshot.leg2_quote.age_ms > 1000:
            timing_loss += (snapshot.leg2_quote.age_ms / 1000) * 0.01 * size

        # Size optimization potential
        size_optimization = 0.0
        available_size = min(snapshot.leg1_quote.ask_size, snapshot.leg2_quote.bid_size)
        if pnl.leg1_actual_size < available_size:
            additional_profit = snapshot.expected_net_spread * (
                (available_size - pnl.leg1_actual_size) / available_size
            )
            size_optimization = additional_profit

        # Check if trade would profit at detection prices
        profit_at_detection = snapshot.expected_net_spread

        return WhatIfAnalysis(
            optimal_profit=optimal_profit,
            optimal_leg1_price=optimal_leg1,
            optimal_leg2_price=optimal_leg2,
            maker_fee_savings=maker_fee_savings,
            timing_loss=timing_loss,
            size_optimization_potential=size_optimization,
            would_profit_at_detection_prices=profit_at_detection > 0,
            profit_at_detection_prices=profit_at_detection,
        )

    def _save_entry(self, entry: TradeJournalEntry) -> None:
        """Save a single entry to disk."""
        if not self.output_dir:
            return

        entry_file = self.output_dir / f"{entry.journal_id}.json"
        with open(entry_file, "w") as f:
            f.write(entry.to_json())

        logger.debug(f"Saved journal entry to {entry_file}")

    def save_all(self, filepath: Optional[Path] = None) -> Path:
        """
        Save all entries to a single JSON file.

        Args:
            filepath: Optional path for output file

        Returns:
            Path to saved file
        """
        if filepath is None:
            if self.output_dir is None:
                raise ValueError("No output directory configured")
            filepath = self.output_dir / f"journal_{self.session_id}.json"

        data = {
            "session_id": self.session_id,
            "saved_at": datetime.now().isoformat(),
            "entry_count": len(self._entries),
            "entries": [e.to_dict() for e in self._entries],
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved {len(self._entries)} journal entries to {filepath}")
        return filepath

    @classmethod
    def load_from_json(cls, filepath: Path) -> "TradeJournal":
        """
        Load a journal from a JSON file.

        Args:
            filepath: Path to journal JSON file

        Returns:
            TradeJournal instance with loaded entries
        """
        with open(filepath, "r") as f:
            data = json.load(f)

        journal = cls(
            session_id=data["session_id"],
            output_dir=filepath.parent,
            auto_save=False,
        )

        journal._entries = [
            TradeJournalEntry.from_dict(e) for e in data["entries"]
        ]

        logger.info(f"Loaded {len(journal._entries)} entries from {filepath}")
        return journal

    # SpreadExecutor callback adapters
    def create_executor_callbacks(self) -> Dict[str, Callable]:
        """
        Create callback functions for SpreadExecutor integration.

        Returns:
            Dictionary with on_complete, on_leg_fill, on_rollback callbacks
        """
        def on_leg_fill(result, leg):
            spread_id = result.spread_id
            if leg.leg_id.endswith("_1") or "leg1" in leg.leg_id.lower():
                event_type = ExecutionEventType.LEG1_FILLED
            else:
                event_type = ExecutionEventType.LEG2_FILLED

            self.record_event(
                spread_id,
                event_type,
                {
                    "leg_id": leg.leg_id,
                    "actual_price": leg.actual_fill_price,
                    "actual_size": leg.actual_fill_size,
                    "slippage": leg.slippage,
                },
            )

        def on_rollback(result):
            self.record_event(
                result.spread_id,
                ExecutionEventType.ROLLBACK_STARTED,
                {"leg1_size": result.leg1.actual_fill_size},
            )

        def on_complete(result):
            # Note: complete_trade should be called separately with full details
            pass

        return {
            "on_complete": on_complete,
            "on_leg_fill": on_leg_fill,
            "on_rollback": on_rollback,
        }

    def get_entry(self, journal_id: str) -> Optional[TradeJournalEntry]:
        """Get a specific entry by ID."""
        for entry in self._entries:
            if entry.journal_id == journal_id:
                return entry
        return None

    def get_entries_by_status(
        self,
        status: TradeJournalStatus,
    ) -> List[TradeJournalEntry]:
        """Get all entries with a specific status."""
        return [e for e in self._entries if e.status == status]

    def get_profitable_entries(self) -> List[TradeJournalEntry]:
        """Get all entries with positive P&L."""
        return [
            e for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit > 0
        ]

    def get_losing_entries(self) -> List[TradeJournalEntry]:
        """Get all entries with negative P&L."""
        return [
            e for e in self._entries
            if e.pnl_breakdown.actual_net_profit is not None
            and e.pnl_breakdown.actual_net_profit < 0
        ]


class _ActiveEntry:
    """Internal class for tracking in-progress trades."""

    def __init__(
        self,
        journal_id: str,
        spread_id: str,
        session_id: str,
        detected_at: datetime,
        execution_started_at: datetime,
        input_snapshot: InputSnapshot,
        decision_record: DecisionRecord,
        events: List[ExecutionEvent],
    ):
        self.journal_id = journal_id
        self.spread_id = spread_id
        self.session_id = session_id
        self.detected_at = detected_at
        self.execution_started_at = execution_started_at
        self.input_snapshot = input_snapshot
        self.decision_record = decision_record
        self.events = events
