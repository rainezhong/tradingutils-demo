#!/usr/bin/env python3
"""
Export a sanitized demo version of the tradingutils repository.

This script creates a clean copy of the repository with:
- Proprietary strategies removed
- API keys and credentials sanitized
- Trade data excluded
- Fresh git history

Usage:
    python3 scripts/export_demo_repo.py --output ../tradingutils-demo
"""

import argparse
import os
import shutil
import subprocess
import re
from pathlib import Path
from typing import List, Set
import yaml


# Directories to completely exclude
EXCLUDE_DIRS = {
    # Proprietary strategies
    "strategies/crypto_scalp",
    "strategies/crypto_scalp_chop",
    "strategies/crypto_latency",
    "strategies/latency_arb",
    "strategies/prediction_mm",

    # Data directories
    "data/recordings",
    "data/edge_capture",
    "data/depth_snapshots",
    "data/spread_capture",
    "data/dashboard_state",
    "data/scanner",
    "data/mm_trades",
    "data/liquidity_trades",
    "data/live_trades",
    "data/scalp_trades",
    "data/sweep",
    "data/oms_recordings",
    "data/nba_cache",
    "data/backtest_results",
    "data/dry_run_*",

    # Credentials
    "apis/keys",

    # Build artifacts
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "htmlcov",
    "test_results",
    "reports",
    "logs",
    "plots",

    # Virtual environments
    ".venv",
    "venv",
    "env",

    # Models
    "models",

    # Claude/IDE
    ".claude/projects",
    ".vscode",
    ".idea",

    # Git
    ".git",
}

# Individual files to exclude
EXCLUDE_FILES = {
    # Proprietary strategies
    "strategies/spread_capture_strategy.py",
    "strategies/edge_capture_strategy.py",
    "strategies/edge_capture.py",
    "strategies/correlation_arb_strategy.py",
    "strategies/depth_scalper_strategy.py",
    "strategies/depth_strategy_base.py",
    "strategies/nba_underdog_strategy.py",
    "strategies/nba_fade_momentum.py",
    "strategies/nba_mean_reversion.py",
    "strategies/nba_points_arb_strategy.py",
    "strategies/liquidity_provider_strategy.py",

    # Proprietary configs
    "strategies/configs/crypto_scalp_live.yaml",
    "strategies/configs/crypto_scalp_paper.yaml",
    "strategies/configs/crypto_scalp_chop.yaml",
    "strategies/configs/prediction_mm_strategy.yaml",
    "strategies/configs/spread_capture_strategy.yaml",
    "strategies/configs/edge_capture_strategy.yaml",
    "strategies/configs/correlation_arb_strategy.yaml",

    # Backtest adapters with proprietary logic
    "src/backtesting/adapters/crypto_adapter.py",
    "src/backtesting/adapters/scalp_adapter.py",
    "src/backtesting/adapters/mm_adapter.py",
    "src/backtesting/adapters/arb_adapter.py",

    # Credentials
    ".env",
    ".kalshi_key.txt",
    ".kalshi_key.json",

    # Data files
    "data/*.db",
    "data/*.db-shm",
    "data/*.db-wal",
    "data/*.csv",
    "data/*.json",
    "data/*.pkl",
    "*.key",
    "*.pem",

    # Temporary/generated files
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".coverage",
    "*.log",
    "*.err",

    # Private investigation scripts
    "scripts/investigate_march1_session.py",
    "scripts/analyze_march2_fills.py",
    "analyze_march2_fills.py",
    "recent_fills.json",

    # Build files
    "*.plist",
    "*.ipynb",

    # Markdown docs with proprietary info
    "*DAMAGE_REPORT*.md",
    "*INVESTIGATION*.md",
    "*SESSION_FINAL*.md",
    "*BUG_FIXES*.md",
    "*FILL_ANALYSIS*.md",
    "*TRADE_VERIFICATION*.md",
    "*VALIDATION_REPORT*.md",
    "*SIGNAL_ANALYSIS*.md",
    "*THRESHOLD_*_TEST*.md",
    "*WEBSOCKET_*.md",
    "*P0_FIXES*.md",
    "*P1_FIXES*.md",
    "*IMPLEMENTATION_COMPLETE*.md",
    "*EVIDENCE*.md",
    "*CORRELATION_ANALYSIS*.md",
    "*UNDERDOG_*.md",
    "*ARCHITECTURE_ISSUES*.md",
    "*BACKTEST_*_RESULTS*.md",
    "*CRASH_PROTECTION*.md",
    "*REALISM_IMPLEMENTATION*.md",
    "*MARKET_IMPACT*.md",
    "*NETWORK_LATENCY*.md",
    "*STRESS_TEST*.md",
    "*INTEGRATION_TEST*.md",
    "*QUICK_START*.md",  # These are internal quick starts, not public docs
    "DEMO_REPO_PLAN.md",  # Internal planning doc
    "DEMO_EXPORT_GUIDE.md",  # Internal guide
}

# File patterns to exclude
EXCLUDE_PATTERNS = [
    r".*\.pyc$",
    r".*\.pyo$",
    r".*\.pyd$",
    r".*\.db$",
    r".*\.db-shm$",
    r".*\.db-wal$",
    r".*\.csv$",
    r".*\.pkl$",
    r".*\.key$",
    r".*\.pem$",
    r".*\.log$",
    r".*\.err$",
    r".*\.ipynb$",
    r".*_results\.csv$",
    r"data/.*",  # Exclude all data directory contents
]

# Strings to sanitize in config files
SENSITIVE_STRINGS = {
    # API keys (placeholder patterns)
    r"kalshi_api_key:\s*['\"]?[a-zA-Z0-9_-]+['\"]?": "kalshi_api_key: YOUR_API_KEY_HERE,
    r"api_key:\s*['\"]?[a-zA-Z0-9_-]+['\"]?": "api_key: YOUR_API_KEY_HERE,
    r"secret_key:\s*['\"]?[a-zA-Z0-9_-]+['\"]?": "secret_key: YOUR_SECRET_KEY_HERE,

    # Email addresses
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}": "user@example.com",
}


def should_exclude_path(path: Path, base_path: Path) -> bool:
    """Check if a path should be excluded."""
    rel_path = path.relative_to(base_path)
    rel_str = str(rel_path)

    # Check directory exclusions
    for exclude_dir in EXCLUDE_DIRS:
        if rel_str.startswith(exclude_dir) or ("/" + exclude_dir + "/") in ("/" + rel_str + "/"):
            return True

    # Check file exclusions
    if rel_str in EXCLUDE_FILES:
        return True

    # Check wildcards in exclude files
    for exclude_file in EXCLUDE_FILES:
        if "*" in exclude_file:
            # Simple glob matching
            pattern = exclude_file.replace("*", ".*")
            if re.match(pattern, rel_str):
                return True

    # Check patterns
    for pattern in EXCLUDE_PATTERNS:
        if re.match(pattern, rel_str):
            return True

    return False


def sanitize_file(file_path: Path) -> str:
    """Sanitize sensitive information from a file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        # Binary file, return as-is
        return None

    # Apply sanitization patterns
    for pattern, replacement in SENSITIVE_STRINGS.items():
        content = re.sub(pattern, replacement, content)

    return content


def copy_and_sanitize(src_dir: Path, dst_dir: Path, dry_run: bool = False):
    """Copy repository with exclusions and sanitization."""
    src_dir = src_dir.resolve()
    dst_dir = dst_dir.resolve()

    print(f"Source: {src_dir}")
    print(f"Destination: {dst_dir}")
    print(f"Dry run: {dry_run}\n")

    copied_files = 0
    excluded_files = 0
    sanitized_files = 0

    # Walk the source directory
    for root, dirs, files in os.walk(src_dir):
        root_path = Path(root)

        # Filter out excluded directories
        dirs[:] = [d for d in dirs if not should_exclude_path(root_path / d, src_dir)]

        # Process files
        for file in files:
            src_file = root_path / file

            # Check if file should be excluded
            if should_exclude_path(src_file, src_dir):
                excluded_files += 1
                continue

            # Calculate destination path
            rel_path = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel_path

            # Create destination directory
            if not dry_run:
                dst_file.parent.mkdir(parents=True, exist_ok=True)

            # Check if file needs sanitization
            if src_file.suffix in {'.yaml', '.yml', '.py', '.md', '.txt', '.sh', '.json'}:
                sanitized_content = sanitize_file(src_file)
                if sanitized_content is not None and sanitized_content != open(src_file, 'r').read():
                    sanitized_files += 1
                    if not dry_run:
                        with open(dst_file, 'w', encoding='utf-8') as f:
                            f.write(sanitized_content)
                    print(f"Sanitized: {rel_path}")
                elif not dry_run:
                    shutil.copy2(src_file, dst_file)
                copied_files += 1
            else:
                # Binary file, copy as-is
                if not dry_run:
                    shutil.copy2(src_file, dst_file)
                copied_files += 1

    print(f"\nSummary:")
    print(f"  Copied: {copied_files} files")
    print(f"  Excluded: {excluded_files} files")
    print(f"  Sanitized: {sanitized_files} files")

    return copied_files > 0


def create_demo_readme(dst_dir: Path):
    """Create a demo-specific README."""
    readme_content = """# TradingUtils - Trading Framework Demo

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
"""

    with open(dst_dir / "README.md", 'w') as f:
        f.write(readme_content)

    print("Created demo README.md")


def create_env_example(dst_dir: Path):
    """Create .env.example file."""
    env_example = """# Kalshi API Configuration
KALSHI_API_KEY=your_api_key_here
KALSHI_API_SECRET=your_api_secret_here
KALSHI_BASE_URL=https://api.elections.kalshi.com/trade-api/v2

# Environment
TRADING_ENV=demo  # demo, paper, or live

# Risk Management
MAX_POSITION_SIZE=100
MAX_DAILY_LOSS=200.0
MAX_ROLLING_DRAWDOWN_PCT=0.15

# Portfolio Optimization (optional)
ENABLE_PORTFOLIO_OPT=false

# Logging
LOG_LEVEL=INFO
"""

    with open(dst_dir / ".env.example", 'w') as f:
        f.write(env_example)

    print("Created .env.example")


def create_gitignore(dst_dir: Path):
    """Create comprehensive .gitignore for demo repo."""
    gitignore_content = """# Credentials
.env
*.key
*.pem
.kalshi_key.*
apis/keys/

# Python
__pycache__/
*.py[cod]
*.so
*.egg
*.egg-info/
dist/
build/

# Testing
.pytest_cache/
.coverage
htmlcov/
test_results/

# Data
data/*.db
data/*.db-shm
data/*.db-wal
data/*.csv
data/*.json
data/*.pkl
data/recordings/
data/backtest_results/

# Logs
*.log
*.err
logs/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Jupyter
*.ipynb
.ipynb_checkpoints/

# Models
models/
"""

    with open(dst_dir / ".gitignore", 'w') as f:
        f.write(gitignore_content)

    print("Created .gitignore")


def initialize_git_repo(dst_dir: Path, dry_run: bool = False):
    """Initialize a fresh git repository."""
    if dry_run:
        print("\n[DRY RUN] Would initialize git repository")
        return

    print("\nInitializing git repository...")
    subprocess.run(["git", "init"], cwd=dst_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=dst_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit: Trading framework demo"],
        cwd=dst_dir,
        check=True
    )
    print("Git repository initialized with initial commit")


def main():
    parser = argparse.ArgumentParser(
        description="Export sanitized demo version of tradingutils repository"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output directory for demo repository"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually doing it"
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Skip git initialization"
    )

    args = parser.parse_args()

    # Get source directory (repository root)
    script_dir = Path(__file__).parent
    src_dir = script_dir.parent
    dst_dir = Path(args.output).resolve()

    print("=" * 60)
    print("TradingUtils Demo Repository Export")
    print("=" * 60)

    # Check if destination exists
    if dst_dir.exists() and not args.dry_run:
        response = input(f"\n{dst_dir} already exists. Overwrite? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted.")
            return
        shutil.rmtree(dst_dir)

    # Create destination directory
    if not args.dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)

    # Copy and sanitize
    print("\nCopying and sanitizing files...")
    success = copy_and_sanitize(src_dir, dst_dir, args.dry_run)

    if not success:
        print("\nNo files copied!")
        return

    # Create demo-specific files
    if not args.dry_run:
        print("\nCreating demo-specific files...")
        create_demo_readme(dst_dir)
        create_env_example(dst_dir)
        create_gitignore(dst_dir)

    # Initialize git
    if not args.no_git and not args.dry_run:
        initialize_git_repo(dst_dir, args.dry_run)

    print("\n" + "=" * 60)
    print("Export complete!")
    print("=" * 60)
    print(f"\nDemo repository created at: {dst_dir}")
    print("\nNext steps:")
    print("  1. Review the exported files")
    print("  2. Test that the framework works")
    print("  3. Create a GitHub repository")
    print("  4. Push the demo repo:")
    print(f"     cd {dst_dir}")
    print("     git remote add origin user@example.com:yourusername/tradingutils-demo.git")
    print("     git push -u origin main")


if __name__ == "__main__":
    main()
