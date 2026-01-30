"""
Comprehensive Testing Framework for Arbitrage Trading.

This module provides detailed visibility into trade execution, identifies
where money is being lost, and outputs reports analyzable by both humans
and Claude.

Example usage:
    from src.testing import ArbitrageTestHarness

    harness = ArbitrageTestHarness(
        config=ArbitrageConfig(paper_mode=True),
        initial_capital=10000.0,
        output_dir=Path("test_results/run_001"),
    )

    harness.setup(market_data_client=kalshi_client)
    analysis = harness.run_scenario(num_trades=50)
    paths = harness.generate_reports(analysis)
    harness.teardown()
"""

from src.testing.models import (
    # Enums
    TradeJournalStatus,
    LossCategory,
    ExecutionEventType,
    WarningLevel,
    # Data classes
    InputSnapshot,
    QuoteSnapshot,
    DecisionRecord,
    ExecutionEvent,
    PnLBreakdown,
    WhatIfAnalysis,
    TradeJournalEntry,
    SessionAnalysis,
    LossBreakdown,
    Warning,
)

from src.testing.trade_journal import TradeJournal
from src.testing.session_analyzer import SessionAnalyzer
from src.testing.report_generator import ReportGenerator
from src.testing.test_harness import ArbitrageTestHarness
from src.testing.live_display import LiveMetricsDisplay

__all__ = [
    # Enums
    "TradeJournalStatus",
    "LossCategory",
    "ExecutionEventType",
    "WarningLevel",
    # Data classes
    "InputSnapshot",
    "QuoteSnapshot",
    "DecisionRecord",
    "ExecutionEvent",
    "PnLBreakdown",
    "WhatIfAnalysis",
    "TradeJournalEntry",
    "SessionAnalysis",
    "LossBreakdown",
    "Warning",
    # Core classes
    "TradeJournal",
    "SessionAnalyzer",
    "ReportGenerator",
    "ArbitrageTestHarness",
    "LiveMetricsDisplay",
]
