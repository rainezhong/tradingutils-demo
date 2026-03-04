#!/bin/bash
# Phase 1: Backtest Validation
# Validates all 4 features in historical data before risking capital

set -e

echo "=== Phase 1: Backtest Validation ==="
echo "This will run 8 backtests to validate new features."
echo ""

# Create results directory
mkdir -p results/phase1_validation
cd "$(dirname "$0")/.."

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}1/8: Baseline - Standard Kelly${NC}"
python3 main.py backtest prediction-mm \
    --db data/btc_latency_probe.db \
    --config config/portfolio_config.yaml \
    --output results/phase1_validation/baseline_kelly.json

echo -e "${YELLOW}2/8: Feature Test - Empirical Kelly${NC}"
# Temporarily enable empirical Kelly in config
python3 -c "
import yaml
with open('config/portfolio_config.yaml', 'r') as f:
    config = yaml.safe_load(f)
config['use_empirical_kelly'] = True
config['empirical_kelly_simulations'] = 1000
with open('config/portfolio_config_empirical.yaml', 'w') as f:
    yaml.dump(config, f)
"

python3 main.py backtest prediction-mm \
    --db data/btc_latency_probe.db \
    --config config/portfolio_config_empirical.yaml \
    --output results/phase1_validation/empirical_kelly.json

echo -e "${YELLOW}3/8: Baseline - No VPIN Kill Switch${NC}"
python3 main.py backtest prediction-mm \
    --db data/btc_latency_probe.db \
    --config strategies/configs/prediction_mm_strategy.yaml \
    --output results/phase1_validation/baseline_no_vpin.json

echo -e "${YELLOW}4/8: Feature Test - VPIN Kill Switch${NC}"
python3 -c "
import yaml
with open('strategies/configs/prediction_mm_strategy.yaml', 'r') as f:
    config = yaml.safe_load(f)
if 'vpin_kill_switch' not in config:
    config['vpin_kill_switch'] = {}
config['vpin_kill_switch']['enabled'] = True
config['vpin_kill_switch']['toxic_threshold'] = 0.70
with open('strategies/configs/prediction_mm_vpin_test.yaml', 'w') as f:
    yaml.dump(config, f)
"

python3 main.py backtest prediction-mm \
    --db data/btc_latency_probe.db \
    --config strategies/configs/prediction_mm_vpin_test.yaml \
    --output results/phase1_validation/vpin_enabled.json

echo -e "${YELLOW}5/8: Baseline - Simple Inventory Skew${NC}"
python3 main.py backtest prediction-mm \
    --db data/btc_latency_probe.db \
    --config strategies/configs/prediction_mm_strategy.yaml \
    --output results/phase1_validation/baseline_simple_skew.json

echo -e "${YELLOW}6/8: Feature Test - A-S Reservation Price${NC}"
python3 -c "
import yaml
with open('strategies/configs/prediction_mm_strategy.yaml', 'r') as f:
    config = yaml.safe_load(f)
config['use_reservation_price'] = True
config['risk_aversion'] = 0.05
with open('strategies/configs/prediction_mm_as_test.yaml', 'w') as f:
    yaml.dump(config, f)
"

python3 main.py backtest prediction-mm \
    --db data/btc_latency_probe.db \
    --config strategies/configs/prediction_mm_as_test.yaml \
    --output results/phase1_validation/avellaneda_stoikov.json

echo -e "${YELLOW}7/8: Analyzing sequence gaps in historical data${NC}"
if [ -f "scripts/analyze_sequence_gaps.py" ]; then
    python3 scripts/analyze_sequence_gaps.py \
        --db data/btc_latency_probe.db \
        --output results/phase1_validation/sequence_gaps.json
else
    echo "Sequence gap analysis script not found, skipping..."
fi

echo -e "${YELLOW}8/8: Generating comparison report${NC}"
python3 -c "
import json
import pandas as pd
from pathlib import Path

results_dir = Path('results/phase1_validation')

# Load all results
results = {}
for f in results_dir.glob('*.json'):
    with open(f) as fp:
        results[f.stem] = json.load(fp)

# Create comparison table
comparison = []
for name, data in results.items():
    if 'total_pnl' in data:
        comparison.append({
            'Test': name,
            'Total PnL': data.get('total_pnl', 0),
            'Max DD': data.get('max_drawdown_pct', 0),
            'Sharpe': data.get('sharpe_ratio', 0),
            'Win Rate': data.get('win_rate', 0),
        })

if comparison:
    df = pd.DataFrame(comparison)
    print('\\n=== BACKTEST COMPARISON ===')
    print(df.to_string(index=False))

    # Calculate improvements
    print('\\n=== FEATURE IMPROVEMENTS ===')

    # Empirical Kelly vs Standard Kelly
    if 'baseline_kelly' in results and 'empirical_kelly' in results:
        base_dd = results['baseline_kelly'].get('max_drawdown_pct', 0)
        emp_dd = results['empirical_kelly'].get('max_drawdown_pct', 0)
        improvement = ((base_dd - emp_dd) / base_dd * 100) if base_dd else 0
        print(f'Empirical Kelly DD reduction: {improvement:.1f}%')

    # VPIN Kill Switch
    if 'baseline_no_vpin' in results and 'vpin_enabled' in results:
        base_pnl = results['baseline_no_vpin'].get('total_pnl', 0)
        vpin_pnl = results['vpin_enabled'].get('total_pnl', 0)
        improvement = ((vpin_pnl - base_pnl) / abs(base_pnl) * 100) if base_pnl else 0
        print(f'VPIN Kill Switch PnL improvement: {improvement:+.1f}%')

    # A-S Reservation Price
    if 'baseline_simple_skew' in results and 'avellaneda_stoikov' in results:
        base_sr = results['baseline_simple_skew'].get('sharpe_ratio', 0)
        as_sr = results['avellaneda_stoikov'].get('sharpe_ratio', 0)
        improvement = ((as_sr - base_sr) / base_sr * 100) if base_sr else 0
        print(f'A-S Reservation Sharpe improvement: {improvement:+.1f}%')

    # Save report
    with open(results_dir / 'comparison_report.txt', 'w') as f:
        f.write('BACKTEST COMPARISON\\n')
        f.write(df.to_string(index=False))

    print('\\nReport saved to results/phase1_validation/comparison_report.txt')
else:
    print('No backtest results found.')
"

echo ""
echo -e "${GREEN}Phase 1 Validation Complete!${NC}"
echo ""
echo "Review results in: results/phase1_validation/"
echo "Next step: If improvements look good, proceed to Phase 2 (paper trading)"
echo "  Run: bash scripts/phase2_paper_trading.sh"
