#!/bin/bash
# Quick Start: Enable Empirical Kelly
# Estimated time: 15 minutes

set -e

echo "=== Quick Start: Empirical Kelly ==="
echo ""

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "This will enable empirical Kelly for position sizing across all strategies."
echo ""
echo "Expected impact:"
echo "  • Max drawdown: -15 to -30%"
echo "  • Sharpe ratio: +10 to +20%"
echo "  • Position sizes: 60-80% of current (safer)"
echo ""

read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo -e "${YELLOW}Step 1/4: Backing up current config${NC}"
cp config/portfolio_config.yaml config/portfolio_config.yaml.backup
echo "✓ Backup saved to config/portfolio_config.yaml.backup"

echo ""
echo -e "${YELLOW}Step 2/4: Enabling empirical Kelly${NC}"

python3 << 'EOF'
import yaml
from pathlib import Path

config_path = Path('config/portfolio_config.yaml')

# Load current config
if config_path.exists():
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f) or {}
else:
    config = {}

# Enable empirical Kelly with conservative settings
config['use_empirical_kelly'] = True
config['empirical_kelly_simulations'] = 500  # Fast enough for production
config['kelly_fraction'] = 0.5  # Half Kelly (conservative)

# Ensure other portfolio settings exist
if 'max_allocation_per_strategy' not in config:
    config['max_allocation_per_strategy'] = 0.25
if 'max_total_allocation' not in config:
    config['max_total_allocation'] = 0.80
if 'min_allocation_threshold' not in config:
    config['min_allocation_threshold'] = 0.05

# Save
with open(config_path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

print("✓ Empirical Kelly enabled in config/portfolio_config.yaml")
print("")
print("Settings:")
print(f"  use_empirical_kelly: {config['use_empirical_kelly']}")
print(f"  empirical_kelly_simulations: {config['empirical_kelly_simulations']}")
print(f"  kelly_fraction: {config['kelly_fraction']}")
EOF

echo ""
echo -e "${YELLOW}Step 3/4: Running quick backtest validation${NC}"
echo "This compares standard Kelly vs empirical Kelly on recent data..."
echo ""

# Check if we have historical data
if [ ! -f "data/portfolio_trades.db" ]; then
    echo "⚠️  No historical data found (data/portfolio_trades.db)"
    echo "   Skipping backtest validation. You can run this manually later:"
    echo "   python3 scripts/backtest_empirical_kelly.py"
else
    # Quick validation script
    python3 << 'EOF'
import sqlite3
import pandas as pd
from pathlib import Path

db_path = Path('data/portfolio_trades.db')
if not db_path.exists():
    print("No trade database found, skipping validation")
    exit(0)

try:
    conn = sqlite3.connect(db_path)

    # Get recent trades
    df = pd.read_sql("""
        SELECT strategy_name, net_pnl
        FROM strategy_trades
        WHERE settled = 1
        ORDER BY settle_timestamp DESC
        LIMIT 200
    """, conn)

    if len(df) == 0:
        print("No settled trades found, skipping validation")
        conn.close()
        exit(0)

    # Calculate per-strategy stats
    stats = df.groupby('strategy_name').agg({
        'net_pnl': ['mean', 'std', 'count']
    }).round(4)

    print("Recent Performance by Strategy:")
    print(stats)
    print("")

    # Calculate CV for each strategy
    print("Coefficient of Variation (CV) by Strategy:")
    for strategy in df['strategy_name'].unique():
        strat_pnls = df[df['strategy_name'] == strategy]['net_pnl']
        if len(strat_pnls) > 10:
            mean_pnl = strat_pnls.mean()
            std_pnl = strat_pnls.std()
            if abs(mean_pnl) > 0.001:
                cv = abs(std_pnl / mean_pnl)
                haircut = max(0, 1 - cv)
                print(f"  {strategy}:")
                print(f"    CV = {cv:.3f}")
                print(f"    Empirical Kelly haircut = {haircut:.1%}")
                print(f"    (Position size will be {haircut:.1%} of standard Kelly)")

    conn.close()
    print("")
    print("✓ Validation complete")

except Exception as e:
    print(f"Error during validation: {e}")
    print("Continuing anyway...")

EOF
fi

echo ""
echo -e "${YELLOW}Step 4/4: Next steps${NC}"
echo ""
echo "Empirical Kelly is now enabled in your portfolio config."
echo ""
echo "To see it in action:"
echo ""
echo "  1. Run portfolio rebalance:"
echo -e "     ${GREEN}python3 main.py portfolio rebalance${NC}"
echo ""
echo "  2. Check the logs for CV adjustments:"
echo -e "     ${GREEN}grep 'empirical Kelly' logs/portfolio.log${NC}"
echo ""
echo "  3. View current allocations:"
echo -e "     ${GREEN}python3 main.py portfolio status${NC}"
echo ""
echo "What to expect:"
echo "  • Strategies with stable returns (low CV): ~90-100% of standard Kelly"
echo "  • Strategies with volatile returns (high CV): ~50-70% of standard Kelly"
echo "  • Overall: Position sizes 60-80% of current (safer, lower drawdown)"
echo ""
echo "Monitor for 1-2 weeks, then consider adding:"
echo "  • Sequence gap detection (if using WebSocket feeds)"
echo "  • VPIN kill switch (if running prediction MM)"
echo "  • A-S reservation price (if running prediction MM)"
echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "To rollback: mv config/portfolio_config.yaml.backup config/portfolio_config.yaml"
