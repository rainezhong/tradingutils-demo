# Trading Utils - Demo Version

A demonstration version of a prediction market trading system. This demo version showcases the system architecture without containing real trading logic or credentials.

## Demo Notice

**This is a demonstration version:**
- All API clients are mocked - no real connections to exchanges
- Strategy implementations have been removed
- No real trading can occur
- For educational and demonstration purposes only

## Features Demonstrated

- **Market Scanning**: Discover and filter markets (mock data)
- **Snapshot Logging**: Capture orderbook data with bid/ask spreads
- **Market Analysis**: Score and rank markets based on trading criteria
- **Automated Scheduling**: Run data collection on configurable schedules
- **System Monitoring**: Track system status

## Project Structure

```
tradingutils-demo/
├── src/
│   ├── core/           # Foundation: database, models, config
│   ├── collectors/     # Data collection: scanner, logger
│   ├── analysis/       # Market analysis: metrics, scorer, ranker
│   ├── automation/     # Scheduling: scheduler, monitor
│   ├── kalshi/         # Kalshi API client (mocked)
│   ├── polymarket/     # Polymarket client (mocked)
│   └── strategies/     # Strategy shells (logic removed)
├── signal_extraction/
│   └── strategies/     # Signal strategies (logic removed)
├── main.py             # CLI entry point
├── run_as_bot.py       # Bot runner (demo mode only)
├── data/               # Data directory (empty in demo)
└── logs/               # Log directory (empty in demo)
```

## Quick Start

### Installation

```bash
# Clone the repository
cd tradingutils-demo

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
# Edit .env with your configuration (demo keys work fine)
```

### Basic Usage

```bash
# Scan for markets (mock data)
python main.py scan

# Analyze markets
python main.py analyze --days 3

# Run simulation
python main.py run-simulation --ticker DEMO-001 --steps 50

# List available commands
python main.py --help
```

## Available Commands

| Command | Description |
|---------|-------------|
| `scan` | Discover markets (mock data) |
| `log` | Capture market snapshots |
| `analyze` | Score and rank markets |
| `run-simulation` | Run market-making simulation |
| `monitor` | Display system status |
| `healthcheck` | Run health verification |

### Example Commands

```bash
# Run market analysis
python main.py analyze --days 7 --min-score 10 --top 20

# Run a market-making simulation
python main.py run-simulation --ticker DEMO-MARKET --steps 100 --verbose

# Check system health
python main.py healthcheck
```

## What's Different in Demo Version

### Removed/Modified Components

1. **API Credentials**: Real API keys removed, replaced with placeholders
2. **Strategy Logic**: All proprietary trading algorithms removed, shells remain
3. **API Clients**: Real clients replaced with mock implementations
4. **Data Files**: Market databases and logs cleared

### Mock Clients

The demo uses mock clients that return simulated data:
- `MockKalshiClient` - Simulates Kalshi exchange
- `MockPolymarketClient` - Simulates Polymarket

### Strategy Shells

Strategy files show the class interface but return no-op implementations:
- `nba_mispricing.py` - NBA mispricing strategy shell
- `late_game_blowout.py` - Late game strategy shell
- `momentum_strategy.py` - Momentum strategy shell
- `mean_reversion_strategy.py` - Mean reversion strategy shell

## Architecture Overview

The system follows a modular architecture:

```
┌─────────────────┐     ┌─────────────────┐
│   Data Layer    │     │  Strategy Layer │
│   (Collectors)  │────▶│   (Analysis)    │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│   Exchange API  │     │    Execution    │
│    (Mocked)     │◀────│    (Mocked)     │
└─────────────────┘     └─────────────────┘
```

## Development

### Running Tests

```bash
# Run tests (if available)
pytest

# Run with verbose output
pytest -v
```

### Code Structure

- `src/core/` - Base configuration, models, and utilities
- `src/collectors/` - Market data collection
- `src/analysis/` - Market scoring and ranking
- `src/kalshi/` - Kalshi exchange integration (mocked)
- `src/polymarket/` - Polymarket integration (mocked)

## Disclaimer

This is a demonstration version for educational purposes. It does not contain real trading functionality and cannot execute real trades. The strategy logic has been intentionally removed.

**Do not use this for real trading.** For production systems, proper risk management, testing, and regulatory compliance are essential.

## License

MIT License - see LICENSE file for details.
