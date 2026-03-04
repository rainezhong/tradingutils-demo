"""Pre-flight checks for trading system readiness.

Verifies system state before starting trading operations:
- API connectivity for both exchanges
- Account balances meet minimum requirements
- No unexpected stale positions
- Database connectivity
- Recovery of any incomplete spreads
- Rate limit headroom

Usage:
    preflight = PreflightChecker(
        kalshi_client=kalshi,
        polymarket_client=polymarket,
        config=config,
    )

    result = await preflight.run_all_checks()

    if not result.passed:
        for failure in result.failures:
            logger.error(f"Preflight failed: {failure}")
        sys.exit(1)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from .config import ArbitrageConfig


logger = logging.getLogger(__name__)


class CheckStatus(str, Enum):
    """Status of a preflight check."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    """Result of a single preflight check.

    Attributes:
        name: Name of the check
        status: Pass/fail/warning status
        message: Human-readable result message
        details: Additional details (exchange-specific data, etc.)
        duration_ms: How long the check took
    """

    name: str
    status: CheckStatus
    message: str
    details: Optional[Dict[str, Any]] = None
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == CheckStatus.PASSED

    @property
    def failed(self) -> bool:
        return self.status == CheckStatus.FAILED


@dataclass
class PreflightResult:
    """Aggregate result of all preflight checks.

    Attributes:
        checks: List of individual check results
        started_at: When preflight started
        completed_at: When preflight completed
        config: Configuration used for checks
    """

    checks: List[CheckResult] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    config: Optional[ArbitrageConfig] = None

    @property
    def passed(self) -> bool:
        """Whether all checks passed (no failures)."""
        return not any(c.failed for c in self.checks)

    @property
    def failures(self) -> List[CheckResult]:
        """List of failed checks."""
        return [c for c in self.checks if c.failed]

    @property
    def warnings(self) -> List[CheckResult]:
        """List of warning checks."""
        return [c for c in self.checks if c.status == CheckStatus.WARNING]

    @property
    def total_duration_ms(self) -> float:
        """Total duration of all checks."""
        return sum(c.duration_ms for c in self.checks)

    def summary(self) -> str:
        """Generate a summary string."""
        passed = sum(1 for c in self.checks if c.passed)
        failed = len(self.failures)
        warnings = len(self.warnings)
        skipped = sum(1 for c in self.checks if c.status == CheckStatus.SKIPPED)

        status = "PASSED" if self.passed else "FAILED"
        return (
            f"Preflight {status}: {passed} passed, {failed} failed, "
            f"{warnings} warnings, {skipped} skipped "
            f"({self.total_duration_ms:.0f}ms)"
        )


class ExchangeClient(Protocol):
    """Protocol for exchange clients used in preflight checks."""

    async def get_balance(self) -> Any:
        """Get account balance."""
        ...

    async def get_positions(self, **kwargs) -> List[Any]:
        """Get open positions."""
        ...

    async def health_check(self) -> str:
        """Check API health."""
        ...


class PreflightChecker:
    """Runs preflight checks before trading operations.

    Verifies that all systems are ready for trading:
    - Exchange APIs are reachable
    - Account balances are sufficient
    - No unexpected positions exist
    - Database is accessible
    - No incomplete spreads need recovery

    Example:
        checker = PreflightChecker(
            kalshi_client=kalshi,
            polymarket_client=polymarket,
            config=config,
        )

        result = await checker.run_all_checks()

        if result.passed:
            print("All preflight checks passed")
            # Start trading
        else:
            for failure in result.failures:
                print(f"FAILED: {failure.name} - {failure.message}")
            sys.exit(1)
    """

    # Default minimum balance requirements (USD)
    DEFAULT_MIN_BALANCE_KALSHI = 100.0
    DEFAULT_MIN_BALANCE_POLYMARKET = 100.0

    # Timeout for individual checks
    CHECK_TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        kalshi_client: Optional[Any] = None,
        polymarket_client: Optional[Any] = None,
        database_manager: Optional[Any] = None,
        recovery_service: Optional[Any] = None,
        config: Optional[ArbitrageConfig] = None,
        min_balance_kalshi: float = DEFAULT_MIN_BALANCE_KALSHI,
        min_balance_polymarket: float = DEFAULT_MIN_BALANCE_POLYMARKET,
        expected_positions: Optional[Dict[str, List[str]]] = None,
    ):
        """Initialize preflight checker.

        Args:
            kalshi_client: Kalshi exchange client
            polymarket_client: Polymarket exchange client
            database_manager: Database manager for connectivity check
            recovery_service: SpreadRecoveryService for incomplete spread check
            config: Arbitrage configuration
            min_balance_kalshi: Minimum required Kalshi balance (USD)
            min_balance_polymarket: Minimum required Polymarket balance (USD)
            expected_positions: Dict of expected positions by exchange
                               e.g., {"kalshi": ["TICKER-YES"], "polymarket": ["0x123"]}
        """
        self._kalshi = kalshi_client
        self._polymarket = polymarket_client
        self._db = database_manager
        self._recovery = recovery_service
        self._config = config or ArbitrageConfig()

        self._min_balance_kalshi = min_balance_kalshi
        self._min_balance_polymarket = min_balance_polymarket
        self._expected_positions = expected_positions or {}

        # Track balances for reporting
        self._kalshi_balance: Optional[float] = None
        self._polymarket_balance: Optional[float] = None

    async def run_all_checks(self) -> PreflightResult:
        """Run all preflight checks.

        Returns:
            PreflightResult with all check outcomes
        """
        result = PreflightResult(
            started_at=datetime.now(),
            config=self._config,
        )

        logger.info("Starting preflight checks...")

        # Define checks to run
        checks = [
            ("kalshi_api_health", self._check_kalshi_api),
            ("polymarket_api_health", self._check_polymarket_api),
            ("kalshi_balance", self._check_kalshi_balance),
            ("polymarket_balance", self._check_polymarket_balance),
            ("kalshi_positions", self._check_kalshi_positions),
            ("polymarket_positions", self._check_polymarket_positions),
            ("database_connectivity", self._check_database),
            ("incomplete_spreads", self._check_incomplete_spreads),
            ("config_validation", self._check_config),
        ]

        # Run checks sequentially (some depend on earlier results)
        for name, check_fn in checks:
            try:
                check_result = await asyncio.wait_for(
                    check_fn(),
                    timeout=self.CHECK_TIMEOUT_SECONDS,
                )
                result.checks.append(check_result)

                # Log result
                if check_result.failed:
                    logger.error(
                        "Preflight FAILED: %s - %s", name, check_result.message
                    )
                elif check_result.status == CheckStatus.WARNING:
                    logger.warning(
                        "Preflight WARNING: %s - %s", name, check_result.message
                    )
                else:
                    logger.info("Preflight passed: %s", name)

            except asyncio.TimeoutError:
                result.checks.append(
                    CheckResult(
                        name=name,
                        status=CheckStatus.FAILED,
                        message=f"Check timed out after {self.CHECK_TIMEOUT_SECONDS}s",
                    )
                )
                logger.error("Preflight TIMEOUT: %s", name)

            except Exception as e:
                result.checks.append(
                    CheckResult(
                        name=name,
                        status=CheckStatus.FAILED,
                        message=f"Check error: {e}",
                    )
                )
                logger.exception("Preflight ERROR: %s - %s", name, e)

        result.completed_at = datetime.now()

        # Log summary
        logger.info(result.summary())

        return result

    async def _check_kalshi_api(self) -> CheckResult:
        """Check Kalshi API connectivity."""
        start = datetime.now()

        if not self._kalshi:
            return CheckResult(
                name="kalshi_api_health",
                status=CheckStatus.SKIPPED,
                message="Kalshi client not configured",
            )

        try:
            # Try to get exchange status
            if hasattr(self._kalshi, "get_exchange_status"):
                status = await self._kalshi.get_exchange_status()
                duration = (datetime.now() - start).total_seconds() * 1000

                return CheckResult(
                    name="kalshi_api_health",
                    status=CheckStatus.PASSED,
                    message="Kalshi API is reachable",
                    details={"status": str(status), "latency_ms": duration},
                    duration_ms=duration,
                )

            # Fallback: try to get balance as health check
            await self._kalshi.get_balance()
            duration = (datetime.now() - start).total_seconds() * 1000

            return CheckResult(
                name="kalshi_api_health",
                status=CheckStatus.PASSED,
                message="Kalshi API is reachable",
                details={"latency_ms": duration},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="kalshi_api_health",
                status=CheckStatus.FAILED,
                message=f"Kalshi API unreachable: {e}",
                duration_ms=duration,
            )

    async def _check_polymarket_api(self) -> CheckResult:
        """Check Polymarket API connectivity."""
        start = datetime.now()

        if not self._polymarket:
            return CheckResult(
                name="polymarket_api_health",
                status=CheckStatus.SKIPPED,
                message="Polymarket client not configured",
            )

        try:
            # Use health_check if available
            if hasattr(self._polymarket, "health_check"):
                status = await self._polymarket.health_check()
                duration = (datetime.now() - start).total_seconds() * 1000

                if status == "healthy":
                    return CheckResult(
                        name="polymarket_api_health",
                        status=CheckStatus.PASSED,
                        message="Polymarket API is healthy",
                        details={"status": status, "latency_ms": duration},
                        duration_ms=duration,
                    )
                else:
                    return CheckResult(
                        name="polymarket_api_health",
                        status=CheckStatus.FAILED,
                        message=f"Polymarket API unhealthy: {status}",
                        duration_ms=duration,
                    )

            # Fallback: try to get balance
            await self._polymarket.get_balance()
            duration = (datetime.now() - start).total_seconds() * 1000

            return CheckResult(
                name="polymarket_api_health",
                status=CheckStatus.PASSED,
                message="Polymarket API is reachable",
                details={"latency_ms": duration},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="polymarket_api_health",
                status=CheckStatus.FAILED,
                message=f"Polymarket API unreachable: {e}",
                duration_ms=duration,
            )

    async def _check_kalshi_balance(self) -> CheckResult:
        """Check Kalshi account balance."""
        start = datetime.now()

        if not self._kalshi:
            return CheckResult(
                name="kalshi_balance",
                status=CheckStatus.SKIPPED,
                message="Kalshi client not configured",
            )

        try:
            balance = await self._kalshi.get_balance()
            duration = (datetime.now() - start).total_seconds() * 1000

            # Handle different balance response formats
            if hasattr(balance, "balance_dollars"):
                balance_usd = balance.balance_dollars
            elif hasattr(balance, "balance"):
                # Balance in cents
                balance_usd = balance.balance / 100.0
            elif isinstance(balance, (int, float)):
                balance_usd = float(balance)
            else:
                balance_usd = float(balance)

            self._kalshi_balance = balance_usd

            if balance_usd < self._min_balance_kalshi:
                return CheckResult(
                    name="kalshi_balance",
                    status=CheckStatus.FAILED,
                    message=f"Kalshi balance ${balance_usd:.2f} below minimum ${self._min_balance_kalshi:.2f}",
                    details={
                        "balance_usd": balance_usd,
                        "minimum_usd": self._min_balance_kalshi,
                    },
                    duration_ms=duration,
                )

            return CheckResult(
                name="kalshi_balance",
                status=CheckStatus.PASSED,
                message=f"Kalshi balance: ${balance_usd:.2f}",
                details={
                    "balance_usd": balance_usd,
                    "minimum_usd": self._min_balance_kalshi,
                },
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="kalshi_balance",
                status=CheckStatus.FAILED,
                message=f"Failed to get Kalshi balance: {e}",
                duration_ms=duration,
            )

    async def _check_polymarket_balance(self) -> CheckResult:
        """Check Polymarket account balance."""
        start = datetime.now()

        if not self._polymarket:
            return CheckResult(
                name="polymarket_balance",
                status=CheckStatus.SKIPPED,
                message="Polymarket client not configured",
            )

        try:
            balance = await self._polymarket.get_balance()
            duration = (datetime.now() - start).total_seconds() * 1000

            balance_usd = float(balance)
            self._polymarket_balance = balance_usd

            if balance_usd < self._min_balance_polymarket:
                return CheckResult(
                    name="polymarket_balance",
                    status=CheckStatus.FAILED,
                    message=f"Polymarket balance ${balance_usd:.2f} below minimum ${self._min_balance_polymarket:.2f}",
                    details={
                        "balance_usd": balance_usd,
                        "minimum_usd": self._min_balance_polymarket,
                    },
                    duration_ms=duration,
                )

            return CheckResult(
                name="polymarket_balance",
                status=CheckStatus.PASSED,
                message=f"Polymarket balance: ${balance_usd:.2f}",
                details={
                    "balance_usd": balance_usd,
                    "minimum_usd": self._min_balance_polymarket,
                },
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="polymarket_balance",
                status=CheckStatus.FAILED,
                message=f"Failed to get Polymarket balance: {e}",
                duration_ms=duration,
            )

    async def _check_kalshi_positions(self) -> CheckResult:
        """Check for unexpected Kalshi positions."""
        start = datetime.now()

        if not self._kalshi:
            return CheckResult(
                name="kalshi_positions",
                status=CheckStatus.SKIPPED,
                message="Kalshi client not configured",
            )

        try:
            positions = await self._kalshi.get_positions()
            duration = (datetime.now() - start).total_seconds() * 1000

            # Filter for non-zero positions
            open_positions = [
                p
                for p in positions
                if hasattr(p, "position")
                and p.position != 0
                or hasattr(p, "size")
                and p.size != 0
            ]

            expected = self._expected_positions.get("kalshi", [])
            unexpected = []

            for pos in open_positions:
                ticker = getattr(pos, "ticker", None) or getattr(
                    pos, "market_ticker", "unknown"
                )
                if ticker not in expected:
                    size = getattr(pos, "position", None) or getattr(pos, "size", 0)
                    unexpected.append({"ticker": ticker, "size": size})

            if unexpected:
                return CheckResult(
                    name="kalshi_positions",
                    status=CheckStatus.WARNING,
                    message=f"Found {len(unexpected)} unexpected Kalshi positions",
                    details={
                        "unexpected_positions": unexpected,
                        "expected_tickers": expected,
                    },
                    duration_ms=duration,
                )

            return CheckResult(
                name="kalshi_positions",
                status=CheckStatus.PASSED,
                message=f"Kalshi positions OK ({len(open_positions)} open)",
                details={
                    "open_positions": len(open_positions),
                    "expected_tickers": expected,
                },
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="kalshi_positions",
                status=CheckStatus.FAILED,
                message=f"Failed to check Kalshi positions: {e}",
                duration_ms=duration,
            )

    async def _check_polymarket_positions(self) -> CheckResult:
        """Check for unexpected Polymarket positions."""
        start = datetime.now()

        if not self._polymarket:
            return CheckResult(
                name="polymarket_positions",
                status=CheckStatus.SKIPPED,
                message="Polymarket client not configured",
            )

        try:
            # Get open orders/positions
            if hasattr(self._polymarket, "get_open_orders"):
                orders = await self._polymarket.get_open_orders()
            elif hasattr(self._polymarket, "get_all_positions"):
                orders = await self._polymarket.get_all_positions()
            else:
                return CheckResult(
                    name="polymarket_positions",
                    status=CheckStatus.SKIPPED,
                    message="Position check not supported",
                    duration_ms=(datetime.now() - start).total_seconds() * 1000,
                )

            duration = (datetime.now() - start).total_seconds() * 1000

            # Filter for active positions
            open_positions = [
                o
                for o in orders
                if hasattr(o, "is_active")
                and o.is_active
                or hasattr(o, "size_matched")
                and o.size_matched > 0
            ]

            expected = self._expected_positions.get("polymarket", [])
            unexpected = []

            for pos in open_positions:
                market_id = (
                    getattr(pos, "market", None)
                    or getattr(pos, "token_id", None)
                    or getattr(pos, "asset_id", "unknown")
                )
                if market_id not in expected:
                    size = getattr(pos, "size_matched", 0) or getattr(pos, "size", 0)
                    unexpected.append({"market_id": market_id, "size": size})

            if unexpected:
                return CheckResult(
                    name="polymarket_positions",
                    status=CheckStatus.WARNING,
                    message=f"Found {len(unexpected)} unexpected Polymarket positions",
                    details={
                        "unexpected_positions": unexpected,
                        "expected_markets": expected,
                    },
                    duration_ms=duration,
                )

            return CheckResult(
                name="polymarket_positions",
                status=CheckStatus.PASSED,
                message=f"Polymarket positions OK ({len(open_positions)} open)",
                details={
                    "open_positions": len(open_positions),
                    "expected_markets": expected,
                },
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="polymarket_positions",
                status=CheckStatus.FAILED,
                message=f"Failed to check Polymarket positions: {e}",
                duration_ms=duration,
            )

    async def _check_database(self) -> CheckResult:
        """Check database connectivity."""
        start = datetime.now()

        if not self._db:
            return CheckResult(
                name="database_connectivity",
                status=CheckStatus.SKIPPED,
                message="Database manager not configured",
            )

        try:
            # Use health_check if available
            if hasattr(self._db, "health_check"):
                healthy = await self._db.health_check()
                duration = (datetime.now() - start).total_seconds() * 1000

                if healthy:
                    return CheckResult(
                        name="database_connectivity",
                        status=CheckStatus.PASSED,
                        message="Database is reachable",
                        details={"latency_ms": duration},
                        duration_ms=duration,
                    )
                else:
                    return CheckResult(
                        name="database_connectivity",
                        status=CheckStatus.FAILED,
                        message="Database health check failed",
                        duration_ms=duration,
                    )

            # Fallback: try a simple query
            async with self._db.session() as session:
                await session.execute("SELECT 1")

            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="database_connectivity",
                status=CheckStatus.PASSED,
                message="Database is reachable",
                details={"latency_ms": duration},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="database_connectivity",
                status=CheckStatus.FAILED,
                message=f"Database unreachable: {e}",
                duration_ms=duration,
            )

    async def _check_incomplete_spreads(self) -> CheckResult:
        """Check for and optionally recover incomplete spreads."""
        start = datetime.now()

        if not self._recovery:
            return CheckResult(
                name="incomplete_spreads",
                status=CheckStatus.SKIPPED,
                message="Recovery service not configured",
            )

        try:
            # Run recovery
            results = await self._recovery.recover_all()
            duration = (datetime.now() - start).total_seconds() * 1000

            if not results:
                return CheckResult(
                    name="incomplete_spreads",
                    status=CheckStatus.PASSED,
                    message="No incomplete spreads found",
                    duration_ms=duration,
                )

            # Check if all were recovered
            failed_recoveries = [r for r in results if not r.success]

            if failed_recoveries:
                return CheckResult(
                    name="incomplete_spreads",
                    status=CheckStatus.FAILED,
                    message=f"{len(failed_recoveries)} spreads failed recovery",
                    details={
                        "total_incomplete": len(results),
                        "recovered": len(results) - len(failed_recoveries),
                        "failed": len(failed_recoveries),
                        "failed_spreads": [
                            {"spread_id": r.spread_id, "error": r.message}
                            for r in failed_recoveries
                        ],
                    },
                    duration_ms=duration,
                )

            return CheckResult(
                name="incomplete_spreads",
                status=CheckStatus.PASSED,
                message=f"Recovered {len(results)} incomplete spreads",
                details={
                    "recovered": len(results),
                    "spread_ids": [r.spread_id for r in results],
                },
                duration_ms=duration,
            )

        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="incomplete_spreads",
                status=CheckStatus.FAILED,
                message=f"Failed to check incomplete spreads: {e}",
                duration_ms=duration,
            )

    async def _check_config(self) -> CheckResult:
        """Validate configuration."""
        start = datetime.now()

        try:
            # Validate config
            self._config.validate()

            warnings = []

            # Check for risky settings
            if not self._config.paper_mode:
                warnings.append("LIVE TRADING MODE ENABLED")

            if self._config.max_daily_loss > 1000:
                warnings.append(
                    f"High daily loss limit: ${self._config.max_daily_loss}"
                )

            if self._config.max_concurrent_spreads > 5:
                warnings.append(
                    f"High concurrent spreads: {self._config.max_concurrent_spreads}"
                )

            duration = (datetime.now() - start).total_seconds() * 1000

            if warnings:
                return CheckResult(
                    name="config_validation",
                    status=CheckStatus.WARNING,
                    message="; ".join(warnings),
                    details={
                        "paper_mode": self._config.paper_mode,
                        "max_daily_loss": self._config.max_daily_loss,
                        "max_concurrent_spreads": self._config.max_concurrent_spreads,
                    },
                    duration_ms=duration,
                )

            return CheckResult(
                name="config_validation",
                status=CheckStatus.PASSED,
                message="Configuration valid",
                details={
                    "paper_mode": self._config.paper_mode,
                    "scan_interval": self._config.scan_interval_seconds,
                },
                duration_ms=duration,
            )

        except ValueError as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return CheckResult(
                name="config_validation",
                status=CheckStatus.FAILED,
                message=f"Invalid configuration: {e}",
                duration_ms=duration,
            )

    def get_balance_summary(self) -> Dict[str, float]:
        """Get balance summary from last check.

        Returns:
            Dict with balances by exchange
        """
        return {
            "kalshi": self._kalshi_balance or 0.0,
            "polymarket": self._polymarket_balance or 0.0,
            "total": (self._kalshi_balance or 0.0) + (self._polymarket_balance or 0.0),
        }


async def run_preflight(
    kalshi_client: Optional[Any] = None,
    polymarket_client: Optional[Any] = None,
    database_manager: Optional[Any] = None,
    recovery_service: Optional[Any] = None,
    config: Optional[ArbitrageConfig] = None,
    exit_on_failure: bool = True,
) -> PreflightResult:
    """Convenience function to run preflight checks.

    Args:
        kalshi_client: Kalshi exchange client
        polymarket_client: Polymarket exchange client
        database_manager: Database manager
        recovery_service: Recovery service
        config: Configuration
        exit_on_failure: Whether to raise exception on failure

    Returns:
        PreflightResult

    Raises:
        SystemExit: If exit_on_failure is True and checks fail
    """
    checker = PreflightChecker(
        kalshi_client=kalshi_client,
        polymarket_client=polymarket_client,
        database_manager=database_manager,
        recovery_service=recovery_service,
        config=config,
    )

    result = await checker.run_all_checks()

    if not result.passed and exit_on_failure:
        logger.critical("Preflight checks failed!")
        for failure in result.failures:
            logger.critical("  - %s: %s", failure.name, failure.message)
        raise SystemExit(1)

    return result
