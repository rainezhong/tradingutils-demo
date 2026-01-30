"""
Test Harness - Integrated test runner for arbitrage trading.

Combines paper trading simulation, trade journaling, session analysis,
and report generation into a unified testing framework.
"""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.arbitrage.config import ArbitrageConfig
from src.arbitrage.fee_calculator import FeeCalculator
from src.core.interfaces import APIClient
from src.oms.models import SpreadExecutionResult, SpreadExecutionStatus
from src.oms.order_manager import OrderManagementSystem
from src.oms.spread_executor import SpreadExecutor, SpreadExecutorConfig
from src.simulation.paper_trading import PaperTradingClient
from src.testing.live_display import LiveMetricsDisplay
from src.testing.models import (
    DecisionRecord,
    ExecutionEventType,
    InputSnapshot,
    QuoteSnapshot,
    SessionAnalysis,
    TradeJournalStatus,
)
from src.testing.report_generator import ReportGenerator
from src.testing.session_analyzer import SessionAnalyzer
from src.testing.trade_journal import TradeJournal


logger = logging.getLogger(__name__)


class ArbitrageTestHarness:
    """
    Integrated test runner for arbitrage trading.

    Provides a complete testing environment that:
    - Simulates trades using PaperTradingClient
    - Records detailed execution data via TradeJournal
    - Analyzes session performance with SessionAnalyzer
    - Generates comprehensive reports
    - Optionally displays live metrics during test runs
    """

    def __init__(
        self,
        config: Optional[ArbitrageConfig] = None,
        initial_capital: float = 10000.0,
        output_dir: Optional[Path] = None,
        session_id: Optional[str] = None,
        enable_live_display: bool = True,
    ):
        """
        Initialize the test harness.

        Args:
            config: ArbitrageConfig instance (paper_mode will be forced True)
            initial_capital: Starting capital for paper trading
            output_dir: Directory for output files (created if needed)
            session_id: Optional session identifier (generated if not provided)
            enable_live_display: Whether to show live metrics during testing
        """
        self.config = config or ArbitrageConfig()
        self.config.paper_mode = True  # Force paper mode for testing

        self.initial_capital = initial_capital
        self.output_dir = output_dir or Path("test_results")
        self.session_id = session_id or f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.enable_live_display = enable_live_display

        # Components (initialized in setup)
        self._paper_client: Optional[PaperTradingClient] = None
        self._oms: Optional[OrderManagementSystem] = None
        self._executor: Optional[SpreadExecutor] = None
        self._journal: Optional[TradeJournal] = None
        self._fee_calculator: Optional[FeeCalculator] = None
        self._live_display: Optional[LiveMetricsDisplay] = None

        # State
        self._is_setup = False
        self._trade_count = 0
        self._start_time: Optional[datetime] = None

        # Callbacks for external hooks
        self._on_trade_complete: List[Callable[[TradeJournalStatus, float], None]] = []

    def setup(
        self,
        market_data_client: APIClient,
        executor_config: Optional[SpreadExecutorConfig] = None,
    ) -> None:
        """
        Set up the test harness components.

        Args:
            market_data_client: Client for fetching live market data
            executor_config: Optional custom executor configuration
        """
        if self._is_setup:
            logger.warning("Test harness already set up")
            return

        logger.info(f"Setting up test harness for session {self.session_id}")

        # Create output directory
        session_dir = self.output_dir / self.session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Initialize paper trading client
        self._paper_client = PaperTradingClient(
            market_data_client=market_data_client,
            initial_balance=self.initial_capital,
            persist_path=session_dir / "paper_state.json",
        )
        self._paper_client.start()

        # Initialize fee calculator
        self._fee_calculator = FeeCalculator(config=self.config)

        # Initialize OMS with paper client
        self._oms = OrderManagementSystem()
        # Set name attribute for OMS registration
        self._paper_client.name = "paper"
        self._oms.register_exchange(self._paper_client)

        # Initialize spread executor
        exec_config = executor_config or SpreadExecutorConfig(
            leg1_timeout_seconds=10.0,
            leg2_timeout_seconds=10.0,
            rollback_timeout_seconds=30.0,
        )
        self._executor = SpreadExecutor(
            oms=self._oms,
            config=exec_config,
        )

        # Initialize trade journal
        self._journal = TradeJournal(
            session_id=self.session_id,
            output_dir=session_dir / "journal",
            auto_save=True,
            fee_calculator=self._fee_calculator,
        )

        # Set up executor callbacks
        callbacks = self._journal.create_executor_callbacks()
        self._executor.set_on_leg_fill(callbacks["on_leg_fill"])
        self._executor.set_on_rollback(callbacks["on_rollback"])

        # Initialize live display if enabled
        if self.enable_live_display:
            self._live_display = LiveMetricsDisplay(
                session_id=self.session_id,
                initial_capital=self.initial_capital,
            )
            self._journal.register_callback("on_trade_completed", self._on_journal_trade_complete)

        self._is_setup = True
        logger.info("Test harness setup complete")

    def teardown(self) -> None:
        """Clean up test harness resources."""
        if not self._is_setup:
            return

        logger.info("Tearing down test harness")

        if self._paper_client:
            self._paper_client.stop()
            self._paper_client.save_state()

        if self._journal:
            self._journal.save_all()

        if self._live_display:
            self._live_display.stop()

        self._is_setup = False
        logger.info("Test harness teardown complete")

    def execute_trade(
        self,
        leg1_exchange: str,
        leg1_ticker: str,
        leg1_side: str,
        leg1_price: float,
        leg1_size: int,
        leg2_exchange: str,
        leg2_ticker: str,
        leg2_side: str,
        leg2_price: float,
        leg2_size: int,
        expected_profit: float = 0.0,
        opportunity_rank: int = 1,
        total_opportunities: int = 1,
        decision_reason: str = "Test execution",
    ) -> SpreadExecutionResult:
        """
        Execute a single spread trade with full journaling.

        Args:
            leg1_exchange: Exchange for leg 1
            leg1_ticker: Ticker for leg 1
            leg1_side: 'buy' or 'sell' for leg 1
            leg1_price: Target price for leg 1
            leg1_size: Size for leg 1
            leg2_exchange: Exchange for leg 2
            leg2_ticker: Ticker for leg 2
            leg2_side: 'buy' or 'sell' for leg 2
            leg2_price: Target price for leg 2
            leg2_size: Size for leg 2
            expected_profit: Expected net profit
            opportunity_rank: Rank of this opportunity
            total_opportunities: Total opportunities available
            decision_reason: Why this trade was selected

        Returns:
            SpreadExecutionResult from executor
        """
        if not self._is_setup:
            raise RuntimeError("Test harness not set up. Call setup() first.")

        self._trade_count += 1
        spread_id = f"SPREAD-{uuid.uuid4().hex[:8].upper()}"

        # Get current market data for snapshot
        leg1_market = self._paper_client.get_market_data(leg1_ticker)
        leg2_market = self._paper_client.get_market_data(leg2_ticker)

        now = datetime.now()

        # Create input snapshot
        input_snapshot = InputSnapshot(
            leg1_quote=QuoteSnapshot(
                exchange=leg1_exchange,
                ticker=leg1_ticker,
                bid=leg1_market.bid,
                ask=leg1_market.ask,
                mid=leg1_market.mid,
                spread=leg1_market.spread,
                bid_size=100,  # Estimated
                ask_size=100,
                timestamp=leg1_market.timestamp,
                age_ms=(now - leg1_market.timestamp).total_seconds() * 1000,
            ),
            leg2_quote=QuoteSnapshot(
                exchange=leg2_exchange,
                ticker=leg2_ticker,
                bid=leg2_market.bid,
                ask=leg2_market.ask,
                mid=leg2_market.mid,
                spread=leg2_market.spread,
                bid_size=100,
                ask_size=100,
                timestamp=leg2_market.timestamp,
                age_ms=(now - leg2_market.timestamp).total_seconds() * 1000,
            ),
            expected_gross_spread=(leg2_price - leg1_price) * min(leg1_size, leg2_size),
            expected_net_spread=expected_profit,
            expected_fees=self._estimate_fees(leg1_price, leg2_price, min(leg1_size, leg2_size)),
            capital_available=self._paper_client.get_balance(),
            active_positions=len(self._paper_client.get_positions()),
            active_spreads=0,
        )

        # Create decision record
        decision_record = DecisionRecord(
            opportunity_rank=opportunity_rank,
            total_opportunities=total_opportunities,
            edge_cents=(leg2_price - leg1_price) * 100,  # Convert to cents
            roi_pct=expected_profit / (leg1_price * leg1_size) if leg1_price * leg1_size > 0 else 0,
            liquidity_score=0.8,  # Estimated
            filters_passed=["min_edge", "min_roi", "min_liquidity"],
            filters_failed=[],
            decision_reason=decision_reason,
            alternative_opportunities=[],
        )

        # Start journal entry
        journal_id = self._journal.start_trade(
            spread_id=spread_id,
            input_snapshot=input_snapshot,
            decision_record=decision_record,
        )

        # Record decision event
        self._journal.record_event(
            spread_id,
            ExecutionEventType.DECISION,
            {"reason": decision_reason},
        )

        # Execute the spread
        self._journal.record_event(spread_id, ExecutionEventType.LEG1_SUBMITTED, {})

        result = self._executor.execute_spread(
            opportunity_id=spread_id,
            leg1_exchange=leg1_exchange,
            leg1_ticker=leg1_ticker,
            leg1_side=leg1_side,
            leg1_price=leg1_price,
            leg1_size=leg1_size,
            leg2_exchange=leg2_exchange,
            leg2_ticker=leg2_ticker,
            leg2_side=leg2_side,
            leg2_price=leg2_price,
            leg2_size=leg2_size,
            expected_profit=expected_profit,
        )

        # Determine journal status
        journal_status = self._map_execution_status(result.status)

        # Calculate actual fees
        actual_leg1_fee = 0.0
        actual_leg2_fee = 0.0
        if result.leg1.actual_fill_price is not None:
            actual_leg1_fee = result.leg1.actual_fill_price * result.leg1.actual_fill_size * 0.07
        if result.leg2.actual_fill_price is not None:
            actual_leg2_fee = result.leg2.actual_fill_price * result.leg2.actual_fill_size * 0.07

        # Calculate rollback loss
        rollback_loss = 0.0
        if result.rollback_order and result.rollback_order.avg_fill_price:
            # Loss = (original buy price - rollback sell price) * size
            rollback_loss = (
                result.leg1.actual_fill_price - result.rollback_order.avg_fill_price
            ) * result.leg1.actual_fill_size

        # Complete journal entry
        self._journal.complete_trade(
            spread_id=spread_id,
            status=journal_status,
            leg1_actual_price=result.leg1.actual_fill_price,
            leg1_actual_size=result.leg1.actual_fill_size,
            leg2_actual_price=result.leg2.actual_fill_price,
            leg2_actual_size=result.leg2.actual_fill_size,
            actual_leg1_fee=actual_leg1_fee,
            actual_leg2_fee=actual_leg2_fee,
            rollback_loss=rollback_loss,
            error_message=result.error,
            metadata={"execution_result": result.status.value},
        )

        return result

    def run_scenario(
        self,
        opportunities: List[Dict[str, Any]],
        delay_between_trades_ms: int = 0,
    ) -> SessionAnalysis:
        """
        Run a complete test scenario with multiple opportunities.

        Args:
            opportunities: List of opportunity dicts with trade parameters
            delay_between_trades_ms: Optional delay between trades

        Returns:
            SessionAnalysis of the completed session
        """
        if not self._is_setup:
            raise RuntimeError("Test harness not set up. Call setup() first.")

        self._start_time = datetime.now()

        if self._live_display:
            self._live_display.start(total_trades=len(opportunities))

        logger.info(f"Starting scenario with {len(opportunities)} opportunities")

        for i, opp in enumerate(opportunities):
            try:
                self.execute_trade(
                    leg1_exchange=opp.get("leg1_exchange", "paper"),
                    leg1_ticker=opp["leg1_ticker"],
                    leg1_side=opp.get("leg1_side", "buy"),
                    leg1_price=opp["leg1_price"],
                    leg1_size=opp.get("leg1_size", 10),
                    leg2_exchange=opp.get("leg2_exchange", "paper"),
                    leg2_ticker=opp["leg2_ticker"],
                    leg2_side=opp.get("leg2_side", "sell"),
                    leg2_price=opp["leg2_price"],
                    leg2_size=opp.get("leg2_size", 10),
                    expected_profit=opp.get("expected_profit", 0.0),
                    opportunity_rank=i + 1,
                    total_opportunities=len(opportunities),
                    decision_reason=opp.get("reason", "Scenario execution"),
                )
            except Exception as e:
                logger.error(f"Trade {i + 1} failed: {e}")

            if delay_between_trades_ms > 0:
                import time
                time.sleep(delay_between_trades_ms / 1000.0)

        # Generate analysis
        analyzer = SessionAnalyzer(self._journal)
        analysis = analyzer.analyze()

        if self._live_display:
            self._live_display.show_final_summary(analysis)

        return analysis

    def generate_reports(
        self,
        analysis: Optional[SessionAnalysis] = None,
    ) -> Dict[str, Path]:
        """
        Generate all reports for the session.

        Args:
            analysis: Optional pre-computed analysis (generated if not provided)

        Returns:
            Dictionary mapping report type to file path
        """
        if not self._journal:
            raise RuntimeError("No journal data available")

        if analysis is None:
            analyzer = SessionAnalyzer(self._journal)
            analysis = analyzer.analyze()

        reporter = ReportGenerator(self._journal)
        session_dir = self.output_dir / self.session_id

        return reporter.generate_all_reports(analysis, session_dir / "reports")

    def get_journal(self) -> TradeJournal:
        """Get the trade journal for this session."""
        if not self._journal:
            raise RuntimeError("Test harness not set up")
        return self._journal

    def get_paper_client(self) -> PaperTradingClient:
        """Get the paper trading client."""
        if not self._paper_client:
            raise RuntimeError("Test harness not set up")
        return self._paper_client

    def register_on_trade_complete(
        self,
        callback: Callable[[TradeJournalStatus, float], None],
    ) -> None:
        """
        Register a callback for trade completion.

        Args:
            callback: Function(status, pnl) called after each trade
        """
        self._on_trade_complete.append(callback)

    def _map_execution_status(
        self,
        status: SpreadExecutionStatus,
    ) -> TradeJournalStatus:
        """Map SpreadExecutionStatus to TradeJournalStatus."""
        mapping = {
            SpreadExecutionStatus.COMPLETED: TradeJournalStatus.SUCCESS,
            SpreadExecutionStatus.PARTIAL: TradeJournalStatus.PARTIAL,
            SpreadExecutionStatus.ROLLED_BACK: TradeJournalStatus.ROLLED_BACK,
            SpreadExecutionStatus.FAILED: TradeJournalStatus.FAILED,
        }
        return mapping.get(status, TradeJournalStatus.FAILED)

    def _estimate_fees(
        self,
        leg1_price: float,
        leg2_price: float,
        size: int,
    ) -> float:
        """Estimate total fees for a trade."""
        # Use taker fee rate for estimation
        fee_rate = self.config.kalshi_fee_rate
        leg1_fee = leg1_price * size * fee_rate
        leg2_fee = leg2_price * size * fee_rate
        return leg1_fee + leg2_fee

    def _on_journal_trade_complete(self, entry) -> None:
        """Handle trade completion from journal."""
        if self._live_display:
            pnl = entry.pnl_breakdown.actual_net_profit or 0.0
            self._live_display.update_trade_complete(entry.status, pnl, entry)

        # Fire external callbacks
        for callback in self._on_trade_complete:
            try:
                pnl = entry.pnl_breakdown.actual_net_profit or 0.0
                callback(entry.status, pnl)
            except Exception as e:
                logger.error(f"Trade complete callback error: {e}")

    @classmethod
    def from_journal(
        cls,
        journal_path: Path,
        output_dir: Optional[Path] = None,
    ) -> "ArbitrageTestHarness":
        """
        Create a harness from an existing journal file for analysis.

        Args:
            journal_path: Path to journal JSON file
            output_dir: Optional output directory

        Returns:
            ArbitrageTestHarness with loaded journal
        """
        journal = TradeJournal.load_from_json(journal_path)

        harness = cls(
            session_id=journal.session_id,
            output_dir=output_dir or journal_path.parent.parent,
            enable_live_display=False,
        )
        harness._journal = journal
        harness._is_setup = False  # Only for analysis, not trading

        return harness
