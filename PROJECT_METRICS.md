# TradingUtils Project Metrics

*Last updated: 2026-03-03*

## 📊 Code Statistics

### Repository
- **Total Python Files**: 1,937
- **Lines of Code**: 673,669
- **Main CLI**: 2,358 lines (44 functions)
- **Git Commits**: 239 total (all in 2026)
- **Contributors**: 6

### Strategies
- **Total Strategy Files**: 56 Python files
- **Top-Level Strategies**: 21 implementations
- **Strategy Subdirectories**: 6 (crypto_scalp, crypto_latency, latency_arb, prediction_mm, etc.)
- **Strategy Config Files**: 10 YAML files
- **Files with Strategy Classes**: 43

#### Strategy Breakdown
**Proprietary (Production):**
- Crypto scalping & latency arbitrage (3 implementations)
- Prediction market making (9 modules)
- Spread & edge capture (3 implementations)
- Depth-based strategies (2 implementations)
- Advanced NBA strategies (4 implementations)
- Correlation arbitrage

**Educational (Framework Examples):**
- Basic scalp strategy
- Late game blowout
- NBA mispricing
- Total points
- Market making
- Tied game spread

### Core Framework
- **Core Modules**: 62 Python files
- **Risk Management**: 5 modules (Kelly, position sizing, drawdown, correlation limits)
- **Portfolio Optimization**: 6 modules (tracker, estimator, optimizer, copulas)
- **Indicators**: 4 implementations (VPIN, orderflow, BRTI, regime detection)
- **Exchange Clients**: 2 (Kalshi, Polymarket)
- **Interfaces**: 5 (I_Strategy, I_ExchangeClient, I_OrderManager, I_Scanner, I_Market)
- **Type Definition Files**: 16 (*_types.py files)
- **Dataclass Files**: 245 (extensive type safety)

### Testing
- **Test Files**: 52
- **Test Functions**: 835
- **Test Coverage**: Core framework, strategies, backtesting, portfolio

### Scripts & Utilities
- **Script Files**: 97 Python scripts
- **Executable Tools**: Main CLI + 101 scripts
- **Backtest Adapters**: 9 (nba, crypto, scalp, arb, mm, generic, etc.)

### Documentation
- **Markdown Files**: 202 total
- **README Files**: 10
- **Documentation Folder**: 54 .md files
- **Core Docs**: ARCHITECTURE.md (7,995 lines), CLAUDE.md (6,494 lines)

### Dependencies
- **Requirements**: 9 core dependencies
- **Config Files**: 20 YAML/JSON files

## 📈 Data & Infrastructure

### Databases
- **Total Database Files**: 27
- **Total Data Size**: 12 GB
- **Largest DB**: btc_ob_48h.db (2.1 GB - 1.09M orderbook snapshots)

#### Database Breakdown
**Market Data:**
- markets.db (2.2 GB) - Market metadata
- btc_historical_6months.db (152 MB) - Historical crypto data
- btc_merged_feb.db (1.9 GB) - Merged orderbook data
- btc_ob_48h.db (2.1 GB) - 48-hour orderbook snapshots
- btc_probe_20260227.db (1.4 GB) - Latency probe data

**Sports Markets:**
- probe_nba.db (474 MB) - NBA latency probe
- probe_ncaab.db (499 MB) - NCAA basketball probe

**Strategy Data:**
- portfolio_trades.db (28 KB) - 6 portfolio trades
- research.db (52 KB) - Research orchestrator

**Other:**
- smart_spreads.db (20 KB)
- spreads.db (40 KB)
- weather_markets.db (44 KB)

### Data Tables
**BTC Latency Probe DB:**
- kraken_trades
- kraken_snapshots
- kalshi_snapshots
- market_settlements
- binance_trades
- coinbase_trades
- kalshi_orderbook (1.09M records)
- binance_l2
- coinbase_l2

## 🏗️ Architecture

### Design Patterns
- **Interface-First**: All components implement I_* interfaces
- **Type-Safe**: Extensive use of dataclasses (245 files)
- **Modular**: Clear separation (core/, strategies/, scanner/, etc.)
- **Testable**: 835 test functions covering critical paths

### Exchange Integrations
1. **Kalshi** (Full support)
   - REST API client
   - WebSocket (sync & async)
   - Authentication
   - Order management
   - Market scanning

2. **Polymarket** (Partial support)
   - REST API client
   - Authentication
   - Basic order types

### Market Types Supported
- Cryptocurrency (BTC, ETH)
- NBA & NCAA Basketball
- Elections
- Weather
- Economic Indicators (CPI)
- Generic prediction markets

### Strategy Categories
- Latency arbitrage (2-3 implementations)
- Market making (2 implementations)
- Scalping (2 implementations)
- Spread/edge capture (3 implementations)
- Statistical arbitrage (correlation, depth-based)
- NBA-specific (4 strategies: underdog, blowout, mispricing, points)

## 📚 Key Components

### Risk Management System
- Kelly criterion position sizing
- Portfolio-level drawdown tracking
- Per-strategy position limits
- Correlation-based exposure limits
- Stop-loss protection

### Portfolio Optimization
- Multi-variate Kelly criterion
- Copula-based tail dependence modeling (Gaussian & Student-t)
- Shrinkage correlation estimator
- Performance tracking (SQLite)
- Daily rebalancing

### Backtesting Framework
- Event-driven architecture
- Realistic fill models (partial fills, slippage, market impact)
- Multiple data sources (orderbook, trades, L2 feeds)
- Per-strategy adapters
- Comprehensive metrics

### Indicators & Analysis
- **VPIN**: Volume-synchronized probability of informed trading
- **Orderflow**: Binance/Coinbase aggregated orderflow indicator
- **BRTI**: Multi-exchange Bitcoin Reference Rate Indicator (5 exchanges)
- **Regime Detection**: Oscillation-based market state

### Latency Measurement
- Truth source framework (generic)
- Crypto truth source (Kraken + Black-Scholes)
- NBA/NCAAB truth source (ESPN API + normal dist model)
- Settlement accuracy tracking

## 🚀 Production Features

### Automation
- Scheduler (cron-like syntax)
- Lifecycle hooks (startup, shutdown, hourly, daily)
- State persistence
- Error recovery

### Order Management
- Fill tracking
- Position reconciliation
- Duplicate prevention
- Opposite-side protection
- Fee calculation

### Live Trading Safeguards
- Dry-run mode
- Paper trading mode
- Position limits
- Daily loss limits
- Circuit breakers
- Balance tracking

## 📊 Development Velocity

### Recent Activity (2026)
- **Total commits**: 239
- **Active development**: Daily commits
- **Latest commit**: "Add comprehensive backtest realism models for accurate performance estimation"

### Major Features Added (Feb-Mar 2026)
1. Portfolio optimization with copulas
2. Comprehensive backtest realism models
3. Crypto scalp crash protection
4. Opposite-side position protection
5. NBA underdog duplicate prevention
6. NBA timing fix (game start vs market close)
7. Unified backtest framework
8. Regime detection
9. BRTI tracker
10. Latency probe framework

## 🎯 Quality Metrics

### Type Safety
- 245 files with dataclasses
- 16 dedicated *_types.py files
- 5 core interfaces (I_Strategy, I_ExchangeClient, etc.)
- Comprehensive type hints throughout

### Testing
- 835 test functions
- 52 test files
- Unit tests for all critical components
- Integration tests for strategies
- Backtest validation tests

### Documentation
- 202 markdown files
- Architecture guide (7,995 lines)
- Development guide (6,494 lines)
- Per-component READMEs (10 files)
- Inline documentation
- Example code

## 📦 Demo Repository Export

### Export Metrics
- **Files to Export**: ~762 files (~60% of codebase)
- **Files to Exclude**: ~271 files (~40% of codebase)
- **Files to Sanitize**: 13 config files

### What's Included in Demo
- ✅ 100% of framework (core/, scanner/, backtest)
- ✅ 6 example strategies
- ✅ All documentation
- ✅ All tests for included components

### What's Excluded from Demo
- ❌ 15 proprietary strategies
- ❌ All API keys and credentials
- ❌ 12 GB of trade data
- ❌ Optimized parameter configurations

## 🏆 Highlights

### Scale
- **Nearly 675K lines** of production Python code
- **12 GB** of market data collected
- **1.09 million** orderbook snapshots recorded
- **27 databases** for different market types
- **2 exchanges** integrated

### Sophistication
- **Multi-variate Kelly** with tail dependence modeling
- **Copula-based** correlation estimation
- **Event-driven** backtesting with realistic fills
- **Interface-first** architecture for extensibility
- **Type-safe** throughout (245 dataclass files)

### Production-Ready
- **835 tests** covering critical paths
- **Risk management** at multiple levels
- **Position reconciliation** and balance tracking
- **Crash protection** based on backtest findings
- **Duplicate prevention** across multiple dimensions

### Well-Documented
- **202 markdown files**
- **~15K lines** of architectural documentation
- **Per-component** READMEs
- **LLM-optimized** development guides

---

*This project represents a production-grade quantitative trading system built with interface-first design, comprehensive testing, and extensive documentation.*
