"""Kalshi/Polymarket Arbitrage System.

This module provides the orchestration layer that ties together:
- Spread detection from arb/spread_detector.py
- Execution via src/oms/spread_executor.py
- Risk management via src/risk/risk_manager.py
- Capital management via src/oms/capital_manager.py

Components:
- ArbitrageConfig: Configuration for the arbitrage system
- FeeCalculator: Net spread calculation after fees
- OpportunityDetector: Scans for profitable opportunities
- CircuitBreaker: System-level safety controls
- ArbitrageOrchestrator: Main loop connecting detection to execution
- PreflightChecker: Pre-trading system verification
"""

from .config import ArbitrageConfig
from .fee_calculator import FeeCalculator
from .detector import OpportunityDetector
from .circuit_breaker import CircuitBreaker, CircuitBreakerState
from .orchestrator import ArbitrageOrchestrator
from .preflight import PreflightChecker, PreflightResult, CheckResult, CheckStatus, run_preflight

__all__ = [
    "ArbitrageConfig",
    "FeeCalculator",
    "OpportunityDetector",
    "CircuitBreaker",
    "CircuitBreakerState",
    "ArbitrageOrchestrator",
    "PreflightChecker",
    "PreflightResult",
    "CheckResult",
    "CheckStatus",
    "run_preflight",
]
