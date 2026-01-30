# Arbitrage System Makefile
# Usage: make <target>

.PHONY: test test-arb test-detect test-execute test-failure test-live test-unit help

# Default target
help:
	@echo "Arbitrage System - Available Commands"
	@echo "======================================"
	@echo ""
	@echo "Testing:"
	@echo "  make test          - Run all unit tests"
	@echo "  make test-arb      - Run all arb algorithm tests"
	@echo "  make test-detect   - Test opportunity detection"
	@echo "  make test-execute  - Test full execution"
	@echo "  make test-failure  - Test failure handling"
	@echo "  make test-live     - Test with live market data"
	@echo "  make test-capital  - Test capital management"
	@echo ""
	@echo "Scanning:"
	@echo "  make scan-kalshi        - Scan Kalshi for spread opportunities"
	@echo "  make scan-kalshi-sports - Scan Kalshi sports markets only"
	@echo "  make scan-kalshi-watch  - Continuous Kalshi scanning"
	@echo ""
	@echo "Quick start:"
	@echo "  make quick         - Run detection + execution tests"
	@echo ""

# Run all unit tests
test:
	python3 -m pytest tests/ -v --tb=short

# Run all arb algorithm tests
test-arb:
	python3 scripts/test_arb.py all

# Individual test commands
test-detect:
	python3 scripts/test_arb.py detect

test-execute:
	python3 scripts/test_arb.py execute

test-failure:
	python3 scripts/test_arb.py failure

test-live:
	python3 scripts/test_arb.py live

test-capital:
	python3 scripts/test_arb.py capital

# Quick test (most common)
quick:
	@echo "Running quick tests..."
	python3 scripts/test_arb.py detect
	python3 scripts/test_arb.py execute

# Run integration tests only
test-integration:
	python3 -m pytest tests/test_arb_integration_e2e.py -v

# Run spread detector tests
test-spread:
	python3 -m pytest tests/test_spread_detector.py -v

# Kalshi single-exchange spread scanning
scan-kalshi:
	python3 scripts/scan_kalshi_spreads.py

scan-kalshi-sports:
	python3 scripts/scan_kalshi_spreads.py --sports

scan-kalshi-discover:
	python3 scripts/scan_kalshi_spreads.py --discover

scan-kalshi-watch:
	python3 scripts/scan_kalshi_spreads.py --watch --interval 60

# Data collection and backtesting
collect-spreads:
	python3 scripts/collect_spreads.py

collect-spreads-once:
	python3 scripts/collect_spreads.py --once

backtest-collected:
	python3 scripts/backtest_collected.py

backtest-collected-list:
	python3 scripts/backtest_collected.py --list

# Autonomous logging (like existing logger but with spreads)
log-all:
	python3 -m src.collectors.logger --bulk --spreads

log-all-loop:
	python3 -m src.collectors.logger --bulk --spreads --loop 60

log-spreads:
	python3 -m src.collectors.logger --spreads-only

log-spreads-loop:
	python3 -m src.collectors.logger --spreads-only --loop 60

log-spreads-discover:
	python3 -m src.collectors.logger --spreads-only --discover
