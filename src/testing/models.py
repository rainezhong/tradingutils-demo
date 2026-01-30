"""
Data schemas for trade journal entries and session analysis.

This module defines all dataclasses and enums used throughout the testing framework.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import json


class TradeJournalStatus(Enum):
    """Status of a trade journal entry."""
    SUCCESS = "success"           # Both legs filled successfully
    PARTIAL = "partial"           # Partial fill on one or both legs
    ROLLED_BACK = "rolled_back"   # Leg 1 filled, leg 2 failed, unwound
    FAILED = "failed"             # Complete failure


class LossCategory(Enum):
    """Categories for tracking where money was lost."""
    NONE = "none"                           # No loss (profitable trade)
    SLIPPAGE_LEG1 = "slippage_leg1"         # Paid more than expected on buy
    SLIPPAGE_LEG2 = "slippage_leg2"         # Received less than expected on sell
    FEES_EXCEEDED = "fees_exceeded"          # Actual fees > calculated
    PARTIAL_FILL = "partial_fill"            # Lost profit from unfilled portion
    FAILED_LEG2 = "failed_leg2"              # Leg 2 failed after leg 1 filled
    ROLLBACK_COST = "rollback_cost"          # Loss from unwinding leg 1
    TIMING_STALE_QUOTE = "timing_stale_quote"  # Quote was stale at execution
    OPPORTUNITY_CLOSED = "opportunity_closed"   # Market moved before execution
    MULTIPLE = "multiple"                     # Multiple loss categories


class ExecutionEventType(Enum):
    """Types of execution events tracked."""
    DETECTION = "detection"           # Opportunity detected
    DECISION = "decision"             # Decision to execute made
    LEG1_SUBMITTED = "leg1_submitted"
    LEG1_PARTIAL = "leg1_partial"
    LEG1_FILLED = "leg1_filled"
    LEG1_TIMEOUT = "leg1_timeout"
    LEG1_FAILED = "leg1_failed"
    LEG2_SUBMITTED = "leg2_submitted"
    LEG2_PARTIAL = "leg2_partial"
    LEG2_FILLED = "leg2_filled"
    LEG2_TIMEOUT = "leg2_timeout"
    LEG2_FAILED = "leg2_failed"
    ROLLBACK_STARTED = "rollback_started"
    ROLLBACK_COMPLETED = "rollback_completed"
    ROLLBACK_FAILED = "rollback_failed"
    COMPLETED = "completed"
    ERROR = "error"


class WarningLevel(Enum):
    """Warning severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class QuoteSnapshot:
    """Snapshot of a single market quote."""
    exchange: str
    ticker: str
    bid: float
    ask: float
    mid: float
    spread: float
    bid_size: int
    ask_size: int
    timestamp: datetime
    age_ms: float  # Age of quote at snapshot time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange,
            "ticker": self.ticker,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread": self.spread,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "timestamp": self.timestamp.isoformat(),
            "age_ms": self.age_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuoteSnapshot":
        return cls(
            exchange=data["exchange"],
            ticker=data["ticker"],
            bid=data["bid"],
            ask=data["ask"],
            mid=data["mid"],
            spread=data["spread"],
            bid_size=data["bid_size"],
            ask_size=data["ask_size"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            age_ms=data["age_ms"],
        )


@dataclass
class InputSnapshot:
    """Complete snapshot of system state at detection time."""
    leg1_quote: QuoteSnapshot
    leg2_quote: QuoteSnapshot
    expected_gross_spread: float
    expected_net_spread: float
    expected_fees: float
    capital_available: float
    active_positions: int
    active_spreads: int
    system_load: Optional[float] = None  # CPU/memory if available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg1_quote": self.leg1_quote.to_dict(),
            "leg2_quote": self.leg2_quote.to_dict(),
            "expected_gross_spread": self.expected_gross_spread,
            "expected_net_spread": self.expected_net_spread,
            "expected_fees": self.expected_fees,
            "capital_available": self.capital_available,
            "active_positions": self.active_positions,
            "active_spreads": self.active_spreads,
            "system_load": self.system_load,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InputSnapshot":
        return cls(
            leg1_quote=QuoteSnapshot.from_dict(data["leg1_quote"]),
            leg2_quote=QuoteSnapshot.from_dict(data["leg2_quote"]),
            expected_gross_spread=data["expected_gross_spread"],
            expected_net_spread=data["expected_net_spread"],
            expected_fees=data["expected_fees"],
            capital_available=data["capital_available"],
            active_positions=data["active_positions"],
            active_spreads=data["active_spreads"],
            system_load=data.get("system_load"),
        )


@dataclass
class DecisionRecord:
    """Record of why this opportunity was selected."""
    opportunity_rank: int           # Rank among detected opportunities
    total_opportunities: int        # Total opportunities at decision time
    edge_cents: float               # Expected edge in cents
    roi_pct: float                  # Expected ROI percentage
    liquidity_score: float          # Liquidity rating (0-1)
    filters_passed: List[str]       # List of filter names passed
    filters_failed: List[str]       # List of filter names failed (should be empty)
    decision_reason: str            # Human-readable reason
    alternative_opportunities: List[Dict[str, Any]]  # Other options not taken

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opportunity_rank": self.opportunity_rank,
            "total_opportunities": self.total_opportunities,
            "edge_cents": self.edge_cents,
            "roi_pct": self.roi_pct,
            "liquidity_score": self.liquidity_score,
            "filters_passed": self.filters_passed,
            "filters_failed": self.filters_failed,
            "decision_reason": self.decision_reason,
            "alternative_opportunities": self.alternative_opportunities,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionRecord":
        return cls(
            opportunity_rank=data["opportunity_rank"],
            total_opportunities=data["total_opportunities"],
            edge_cents=data["edge_cents"],
            roi_pct=data["roi_pct"],
            liquidity_score=data["liquidity_score"],
            filters_passed=data["filters_passed"],
            filters_failed=data["filters_failed"],
            decision_reason=data["decision_reason"],
            alternative_opportunities=data["alternative_opportunities"],
        )


@dataclass
class ExecutionEvent:
    """Single event in the execution timeline."""
    event_type: ExecutionEventType
    timestamp: datetime
    elapsed_ms: float  # Time since execution started
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "elapsed_ms": self.elapsed_ms,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionEvent":
        return cls(
            event_type=ExecutionEventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            elapsed_ms=data["elapsed_ms"],
            details=data.get("details", {}),
        )


@dataclass
class PnLBreakdown:
    """Detailed P&L accounting for a trade."""
    # Expected values
    expected_gross_profit: float
    expected_net_profit: float
    expected_total_fees: float

    # Leg 1 (typically buy side)
    leg1_expected_price: float
    leg1_actual_price: Optional[float]
    leg1_expected_size: int
    leg1_actual_size: int
    leg1_slippage_cost: float  # Positive = loss (paid more than expected)

    # Leg 2 (typically sell side)
    leg2_expected_price: float
    leg2_actual_price: Optional[float]
    leg2_expected_size: int
    leg2_actual_size: int
    leg2_slippage_cost: float  # Positive = loss (received less than expected)

    # Fees
    expected_leg1_fee: float
    actual_leg1_fee: float
    expected_leg2_fee: float
    actual_leg2_fee: float
    fee_variance: float  # actual - expected (positive = loss)

    # Other losses
    partial_fill_loss: float      # Lost profit from unfilled portion
    rollback_loss: float          # Loss from unwinding leg 1

    # Actual results
    actual_gross_profit: Optional[float]
    actual_net_profit: Optional[float]

    # Classification
    primary_loss_category: LossCategory
    loss_categories: List[LossCategory]  # All applicable categories
    total_loss_amount: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_gross_profit": self.expected_gross_profit,
            "expected_net_profit": self.expected_net_profit,
            "expected_total_fees": self.expected_total_fees,
            "leg1_expected_price": self.leg1_expected_price,
            "leg1_actual_price": self.leg1_actual_price,
            "leg1_expected_size": self.leg1_expected_size,
            "leg1_actual_size": self.leg1_actual_size,
            "leg1_slippage_cost": self.leg1_slippage_cost,
            "leg2_expected_price": self.leg2_expected_price,
            "leg2_actual_price": self.leg2_actual_price,
            "leg2_expected_size": self.leg2_expected_size,
            "leg2_actual_size": self.leg2_actual_size,
            "leg2_slippage_cost": self.leg2_slippage_cost,
            "expected_leg1_fee": self.expected_leg1_fee,
            "actual_leg1_fee": self.actual_leg1_fee,
            "expected_leg2_fee": self.expected_leg2_fee,
            "actual_leg2_fee": self.actual_leg2_fee,
            "fee_variance": self.fee_variance,
            "partial_fill_loss": self.partial_fill_loss,
            "rollback_loss": self.rollback_loss,
            "actual_gross_profit": self.actual_gross_profit,
            "actual_net_profit": self.actual_net_profit,
            "primary_loss_category": self.primary_loss_category.value,
            "loss_categories": [c.value for c in self.loss_categories],
            "total_loss_amount": self.total_loss_amount,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PnLBreakdown":
        return cls(
            expected_gross_profit=data["expected_gross_profit"],
            expected_net_profit=data["expected_net_profit"],
            expected_total_fees=data["expected_total_fees"],
            leg1_expected_price=data["leg1_expected_price"],
            leg1_actual_price=data.get("leg1_actual_price"),
            leg1_expected_size=data["leg1_expected_size"],
            leg1_actual_size=data["leg1_actual_size"],
            leg1_slippage_cost=data["leg1_slippage_cost"],
            leg2_expected_price=data["leg2_expected_price"],
            leg2_actual_price=data.get("leg2_actual_price"),
            leg2_expected_size=data["leg2_expected_size"],
            leg2_actual_size=data["leg2_actual_size"],
            leg2_slippage_cost=data["leg2_slippage_cost"],
            expected_leg1_fee=data["expected_leg1_fee"],
            actual_leg1_fee=data["actual_leg1_fee"],
            expected_leg2_fee=data["expected_leg2_fee"],
            actual_leg2_fee=data["actual_leg2_fee"],
            fee_variance=data["fee_variance"],
            partial_fill_loss=data["partial_fill_loss"],
            rollback_loss=data["rollback_loss"],
            actual_gross_profit=data.get("actual_gross_profit"),
            actual_net_profit=data.get("actual_net_profit"),
            primary_loss_category=LossCategory(data["primary_loss_category"]),
            loss_categories=[LossCategory(c) for c in data["loss_categories"]],
            total_loss_amount=data["total_loss_amount"],
        )


@dataclass
class WhatIfAnalysis:
    """Comparison to optimal execution."""
    optimal_profit: float           # Best possible outcome
    optimal_leg1_price: float       # Best available price for leg 1
    optimal_leg2_price: float       # Best available price for leg 2
    maker_fee_savings: float        # Savings if all maker fees
    timing_loss: float              # Loss due to quote staleness
    size_optimization_potential: float  # Potential gain from optimal sizing

    # Execution path analysis
    would_profit_at_detection_prices: bool
    profit_at_detection_prices: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "optimal_profit": self.optimal_profit,
            "optimal_leg1_price": self.optimal_leg1_price,
            "optimal_leg2_price": self.optimal_leg2_price,
            "maker_fee_savings": self.maker_fee_savings,
            "timing_loss": self.timing_loss,
            "size_optimization_potential": self.size_optimization_potential,
            "would_profit_at_detection_prices": self.would_profit_at_detection_prices,
            "profit_at_detection_prices": self.profit_at_detection_prices,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WhatIfAnalysis":
        return cls(
            optimal_profit=data["optimal_profit"],
            optimal_leg1_price=data["optimal_leg1_price"],
            optimal_leg2_price=data["optimal_leg2_price"],
            maker_fee_savings=data["maker_fee_savings"],
            timing_loss=data["timing_loss"],
            size_optimization_potential=data["size_optimization_potential"],
            would_profit_at_detection_prices=data["would_profit_at_detection_prices"],
            profit_at_detection_prices=data["profit_at_detection_prices"],
        )


@dataclass
class TradeJournalEntry:
    """Complete record of a single trade execution."""
    # Identifiers
    journal_id: str
    spread_id: str
    session_id: str

    # Timing
    detected_at: datetime
    execution_started_at: datetime
    execution_completed_at: Optional[datetime]
    total_duration_ms: Optional[int]

    # Structured data
    input_snapshot: InputSnapshot
    decision_record: DecisionRecord
    execution_events: List[ExecutionEvent]
    pnl_breakdown: PnLBreakdown
    what_if_analysis: WhatIfAnalysis

    # Result
    status: TradeJournalStatus
    error_message: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "journal_id": self.journal_id,
            "spread_id": self.spread_id,
            "session_id": self.session_id,
            "detected_at": self.detected_at.isoformat(),
            "execution_started_at": self.execution_started_at.isoformat(),
            "execution_completed_at": self.execution_completed_at.isoformat() if self.execution_completed_at else None,
            "total_duration_ms": self.total_duration_ms,
            "input_snapshot": self.input_snapshot.to_dict(),
            "decision_record": self.decision_record.to_dict(),
            "execution_events": [e.to_dict() for e in self.execution_events],
            "pnl_breakdown": self.pnl_breakdown.to_dict(),
            "what_if_analysis": self.what_if_analysis.to_dict(),
            "status": self.status.value,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeJournalEntry":
        return cls(
            journal_id=data["journal_id"],
            spread_id=data["spread_id"],
            session_id=data["session_id"],
            detected_at=datetime.fromisoformat(data["detected_at"]),
            execution_started_at=datetime.fromisoformat(data["execution_started_at"]),
            execution_completed_at=datetime.fromisoformat(data["execution_completed_at"]) if data.get("execution_completed_at") else None,
            total_duration_ms=data.get("total_duration_ms"),
            input_snapshot=InputSnapshot.from_dict(data["input_snapshot"]),
            decision_record=DecisionRecord.from_dict(data["decision_record"]),
            execution_events=[ExecutionEvent.from_dict(e) for e in data["execution_events"]],
            pnl_breakdown=PnLBreakdown.from_dict(data["pnl_breakdown"]),
            what_if_analysis=WhatIfAnalysis.from_dict(data["what_if_analysis"]),
            status=TradeJournalStatus(data["status"]),
            error_message=data.get("error_message"),
            metadata=data.get("metadata", {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "TradeJournalEntry":
        return cls.from_dict(json.loads(json_str))


@dataclass
class LossBreakdown:
    """Aggregated loss breakdown by category."""
    slippage_leg1_usd: float = 0.0
    slippage_leg2_usd: float = 0.0
    fees_exceeded_usd: float = 0.0
    partial_fill_usd: float = 0.0
    failed_leg2_usd: float = 0.0
    rollback_cost_usd: float = 0.0
    timing_stale_quote_usd: float = 0.0
    opportunity_closed_usd: float = 0.0

    @property
    def total_slippage_usd(self) -> float:
        return self.slippage_leg1_usd + self.slippage_leg2_usd

    @property
    def total_loss_usd(self) -> float:
        return (
            self.slippage_leg1_usd +
            self.slippage_leg2_usd +
            self.fees_exceeded_usd +
            self.partial_fill_usd +
            self.failed_leg2_usd +
            self.rollback_cost_usd +
            self.timing_stale_quote_usd +
            self.opportunity_closed_usd
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slippage_leg1_usd": self.slippage_leg1_usd,
            "slippage_leg2_usd": self.slippage_leg2_usd,
            "fees_exceeded_usd": self.fees_exceeded_usd,
            "partial_fill_usd": self.partial_fill_usd,
            "failed_leg2_usd": self.failed_leg2_usd,
            "rollback_cost_usd": self.rollback_cost_usd,
            "timing_stale_quote_usd": self.timing_stale_quote_usd,
            "opportunity_closed_usd": self.opportunity_closed_usd,
            "total_slippage_usd": self.total_slippage_usd,
            "total_loss_usd": self.total_loss_usd,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LossBreakdown":
        return cls(
            slippage_leg1_usd=data.get("slippage_leg1_usd", 0.0),
            slippage_leg2_usd=data.get("slippage_leg2_usd", 0.0),
            fees_exceeded_usd=data.get("fees_exceeded_usd", 0.0),
            partial_fill_usd=data.get("partial_fill_usd", 0.0),
            failed_leg2_usd=data.get("failed_leg2_usd", 0.0),
            rollback_cost_usd=data.get("rollback_cost_usd", 0.0),
            timing_stale_quote_usd=data.get("timing_stale_quote_usd", 0.0),
            opportunity_closed_usd=data.get("opportunity_closed_usd", 0.0),
        )


@dataclass
class Warning:
    """Warning generated during analysis."""
    level: WarningLevel
    category: str
    message: str
    affected_trade_ids: List[str] = field(default_factory=list)
    metric_value: Optional[float] = None
    threshold: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "category": self.category,
            "message": self.message,
            "affected_trade_ids": self.affected_trade_ids,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Warning":
        return cls(
            level=WarningLevel(data["level"]),
            category=data["category"],
            message=data["message"],
            affected_trade_ids=data.get("affected_trade_ids", []),
            metric_value=data.get("metric_value"),
            threshold=data.get("threshold"),
        )


@dataclass
class SessionAnalysis:
    """Complete analysis of a trading session."""
    session_id: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float

    # Trade counts
    total_trades: int
    successful_trades: int
    partial_trades: int
    rolled_back_trades: int
    failed_trades: int

    # P&L metrics
    total_pnl_usd: float
    gross_profit_usd: float
    gross_loss_usd: float
    total_fees_usd: float

    # Performance metrics
    win_rate: float
    profit_factor: float
    sharpe_ratio: Optional[float]
    max_drawdown_usd: float
    max_drawdown_pct: float
    average_profit_per_trade: float
    average_loss_per_trade: float

    # Loss breakdown
    loss_breakdown: LossBreakdown

    # Timing metrics
    avg_execution_time_ms: float
    max_execution_time_ms: float
    min_execution_time_ms: float

    # Quote staleness
    avg_quote_age_ms: float
    max_quote_age_ms: float
    stale_quote_count: int  # Quotes older than threshold

    # Warnings
    warnings: List[Warning]

    # Best/worst trades
    best_trade_id: Optional[str] = None
    best_trade_pnl: Optional[float] = None
    worst_trade_id: Optional[str] = None
    worst_trade_pnl: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "total_trades": self.total_trades,
            "successful_trades": self.successful_trades,
            "partial_trades": self.partial_trades,
            "rolled_back_trades": self.rolled_back_trades,
            "failed_trades": self.failed_trades,
            "total_pnl_usd": self.total_pnl_usd,
            "gross_profit_usd": self.gross_profit_usd,
            "gross_loss_usd": self.gross_loss_usd,
            "total_fees_usd": self.total_fees_usd,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_usd": self.max_drawdown_usd,
            "max_drawdown_pct": self.max_drawdown_pct,
            "average_profit_per_trade": self.average_profit_per_trade,
            "average_loss_per_trade": self.average_loss_per_trade,
            "loss_breakdown": self.loss_breakdown.to_dict(),
            "avg_execution_time_ms": self.avg_execution_time_ms,
            "max_execution_time_ms": self.max_execution_time_ms,
            "min_execution_time_ms": self.min_execution_time_ms,
            "avg_quote_age_ms": self.avg_quote_age_ms,
            "max_quote_age_ms": self.max_quote_age_ms,
            "stale_quote_count": self.stale_quote_count,
            "warnings": [w.to_dict() for w in self.warnings],
            "best_trade_id": self.best_trade_id,
            "best_trade_pnl": self.best_trade_pnl,
            "worst_trade_id": self.worst_trade_id,
            "worst_trade_pnl": self.worst_trade_pnl,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionAnalysis":
        return cls(
            session_id=data["session_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=datetime.fromisoformat(data["ended_at"]),
            duration_seconds=data["duration_seconds"],
            total_trades=data["total_trades"],
            successful_trades=data["successful_trades"],
            partial_trades=data["partial_trades"],
            rolled_back_trades=data["rolled_back_trades"],
            failed_trades=data["failed_trades"],
            total_pnl_usd=data["total_pnl_usd"],
            gross_profit_usd=data["gross_profit_usd"],
            gross_loss_usd=data["gross_loss_usd"],
            total_fees_usd=data["total_fees_usd"],
            win_rate=data["win_rate"],
            profit_factor=data["profit_factor"],
            sharpe_ratio=data.get("sharpe_ratio"),
            max_drawdown_usd=data["max_drawdown_usd"],
            max_drawdown_pct=data["max_drawdown_pct"],
            average_profit_per_trade=data["average_profit_per_trade"],
            average_loss_per_trade=data["average_loss_per_trade"],
            loss_breakdown=LossBreakdown.from_dict(data["loss_breakdown"]),
            avg_execution_time_ms=data["avg_execution_time_ms"],
            max_execution_time_ms=data["max_execution_time_ms"],
            min_execution_time_ms=data["min_execution_time_ms"],
            avg_quote_age_ms=data["avg_quote_age_ms"],
            max_quote_age_ms=data["max_quote_age_ms"],
            stale_quote_count=data["stale_quote_count"],
            warnings=[Warning.from_dict(w) for w in data["warnings"]],
            best_trade_id=data.get("best_trade_id"),
            best_trade_pnl=data.get("best_trade_pnl"),
            worst_trade_id=data.get("worst_trade_id"),
            worst_trade_pnl=data.get("worst_trade_pnl"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "SessionAnalysis":
        return cls.from_dict(json.loads(json_str))
