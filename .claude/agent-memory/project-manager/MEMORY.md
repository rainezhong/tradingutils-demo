# Project Manager Agent Memory

## Backtesting Infrastructure (Surveyed Feb 2026)

### Architecture Layers
- **`src/backtesting/`** — Walk-forward backtesting framework (NBA CLT probability models)
- **`src/simulation/`** — Simulation engine: market simulators, mock APIs, paper trading, NBA replay/backtester
- **`arb/backtest.py`** — Arbitrage/spread backtesting using Kalshi candlestick data
- **`strategies/crypto_latency/backtest.py`** — Validation backtest for crypto latency using real probe data (NEW, untracked)
- **`scripts/backtest_*.py`** — 12+ CLI scripts for various strategies

### Strategy Backtest Coverage
- NBA Mispricing (CLT model): Full framework + walk-forward
- Late Game Blowout: Full backtester in nba_backtester.py
- Tied Game Spread: Full backtester in tied_spread_backtester.py
- Total Points Over/Under: Full backtester + parameter sweep
- Crypto Latency (BTC 15m): Two variants — synthetic (btc_15m) and validation (probe data)
- Arbitrage/Spreads: Kalshi candlestick-based
- Underdog Scalp, Sub-20c Uptick, NBA MM: Script-only backtests

### Data Pipeline
- NBA recordings: ~130 JSON files in `data/recordings/` (Jan 28 - Feb 2026)
- BTC data: 7 SQLite DBs in `data/` (settlement analysis, latency probe, backtest)
- Spread data: `data/spreads.db`, `data/smart_spreads.db`

### Key Facts
- NO dedicated backtest tests exist (tests/test_simulation.py covers simulators only)
- Main CLI integration: `python3 main.py backtest crypto-latency`
- Makefile targets: `backtest-collected`, `backtest-collected-list`
- All framework imports verified working (Python 3.9)
- `strategies/crypto_latency/backtest.py` is untracked (new file)
- `scripts/backtest_btc_15m.py` has significant uncommitted refactoring (Coinbase -> Kraken data source)
