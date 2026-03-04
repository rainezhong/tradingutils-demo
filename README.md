# TradingUtils - Trading Framework Demo

**⚠️ DEMO REPOSITORY**: This is a sanitized demo version showcasing the framework architecture. Proprietary trading strategies and credentials have been removed.

## What's Included

This repository demonstrates a production-grade trading framework with:

### ✅ Core Framework
- **Exchange Integrations**: Kalshi, Polymarket exchange clients
- **Order Management**: Sophisticated order manager with fill tracking, position management
- **Market Abstractions**: Clean interfaces for working with prediction markets
- **Risk Management**: Kelly criterion sizing, position limits, drawdown tracking, correlation limits
- **Portfolio Optimization**: Multi-variate Kelly with copula-based tail dependence modeling
- **Automation**: Scheduling, state management, lifecycle hooks

### ✅ Infrastructure
- **Backtesting Engine**: Event-driven backtesting with realistic fill models
- **Market Scanning**: Flexible scanner interfaces for finding opportunities
- **Indicators**: VPIN, orderflow, BRTI, regime detection
- **Latency Measurement**: Framework for measuring exchange latency

### ✅ Example Strategies
Simple educational examples showing how to implement the `I_Strategy` interface:
- Basic scalping
- NBA game mispricing
- Total points
- Market making
- Blowout detection

### ❌ What's Excluded
- Proprietary trading strategies with proven profitability
- API keys and credentials (you'll need your own)
- Historical trade data and results
- Optimized parameter configurations

## Quick Start

### Prerequisites
- Python 3.9+
- Kalshi API access (get keys from [kalshi.com](https://kalshi.com))

### Setup
```bash
# Clone the repository
git clone https://github.com/yourusername/tradingutils-demo.git
cd tradingutils-demo

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env and add your Kalshi API key

# Run an example strategy
python main.py run scalp --tickers KXBTC-24DEC31-B95000 --dry-run
```

## Architecture

This project follows **interface-first design** principles:

- **I_Strategy** - All strategies implement this interface
- **I_ExchangeClient** - Exchange-agnostic client interface
- **I_OrderManager** - Order execution and fill tracking
- **I_Scanner** - Market opportunity scanning

See [ARCHITECTURE.md](./ARCHITECTURE.md) for complete details.

## Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) - Complete architecture guide
- [CLAUDE.md](./CLAUDE.md) - Development guide for LLM-assisted coding
- [core/risk/README.md](./core/risk/README.md) - Risk management system
- [docs/](./docs/) - Additional documentation

## Building Your Own Strategies

1. **Read the docs**: Start with `ARCHITECTURE.md`
2. **Study examples**: Check `strategies/scalp_strategy.py` for a simple example
3. **Implement I_Strategy**: Create your own strategy class
4. **Add configuration**: Create a YAML config file
5. **Register**: Add your strategy to `main.py`
6. **Backtest**: Use the backtesting framework to validate
7. **Deploy**: Run live with `--dry-run` first!

## Testing

```bash
# Run all tests
pytest

# Run specific test suite
pytest tests/strategies/
pytest tests/backtest/
```

## Contributing

This is a demo repository. If you build something cool, consider:
- Sharing your experience in Discussions
- Contributing framework improvements (not strategies!)
- Reporting bugs or documentation issues

## License

MIT License - See [LICENSE](./LICENSE)

## Disclaimer

This software is for educational purposes. Trading involves risk. No warranty is provided.
The excluded proprietary strategies are not available in this repository.

## Support

For questions about the framework:
- Open an issue
- Check the documentation
- Review example strategies

For questions about building profitable strategies:
- Do your own research
- This is your competitive advantage!

---

Built with Claude Code. See `CLAUDE.md` for LLM-assisted development practices.
