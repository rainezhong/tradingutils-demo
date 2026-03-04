#!/bin/bash
# Phase 3: Live Trading - Single Feature
# Deploy ONE feature at a time with minimal capital

set -e

echo "=== Phase 3: Live Trading - Single Feature Deployment ==="
echo ""

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Create production configs directory
mkdir -p config/production

echo "Which feature do you want to deploy first?"
echo ""
echo "Recommended order:"
echo "  1) Sequence Gap Detection (lowest risk, infrastructure only)"
echo "  2) Empirical Kelly (position sizing)"
echo "  3) A-S Reservation Price (quoting optimization)"
echo "  4) VPIN Kill Switch (most aggressive)"
echo ""
read -p "Enter choice (1-4): " choice

case $choice in
    1)
        FEATURE="Sequence Gap Detection"
        echo -e "${YELLOW}Deploying: ${FEATURE}${NC}"
        echo ""

        # Create config with only sequence gap detection enabled
        python3 -c "
import yaml

# WebSocket config
ws_config = {
    'enable_sequence_validation': True,
    'gap_tolerance': 1,  # Allow small gaps in production
    'reconnect_delay_ms': 1000
}

with open('config/production/kalshi_websocket.yaml', 'w') as f:
    yaml.dump(ws_config, f)

print('Created: config/production/kalshi_websocket.yaml')
print('')
print('Sequence gap detection enabled.')
print('All other features remain at baseline.')
"

        echo ""
        echo -e "${GREEN}Config created!${NC}"
        echo ""
        echo "Run with minimal capital (\$1,000 recommended):"
        echo -e "  ${GREEN}python3 main.py run prediction-mm --config strategies/configs/prediction_mm_strategy.yaml --capital 1000${NC}"
        echo ""
        echo "Monitor for 7 days:"
        echo "  - Gap frequency: grep 'sequence gap' logs/kalshi_ws.log | wc -l"
        echo "  - Reconnect latency: grep 'reconnect' logs/kalshi_ws.log"
        echo ""
        echo "Success criteria: 0 gap-related issues, < 2s reconnect latency"
        ;;

    2)
        FEATURE="Empirical Kelly"
        echo -e "${YELLOW}Deploying: ${FEATURE}${NC}"
        echo ""

        python3 -c "
import yaml

portfolio_config = {
    'kelly_fraction': 0.5,
    'use_empirical_kelly': True,
    'empirical_kelly_simulations': 500,  # Lower for production speed
    'max_allocation_per_strategy': 0.25,
    'max_total_allocation': 0.80,
    'min_allocation_threshold': 0.05,
}

with open('config/production/portfolio_config.yaml', 'w') as f:
    yaml.dump(portfolio_config, f)

print('Created: config/production/portfolio_config.yaml')
print('')
print('Empirical Kelly enabled with conservative settings.')
"

        echo ""
        echo -e "${GREEN}Config created!${NC}"
        echo ""
        echo "Run with \$5,000 capital:"
        echo -e "  ${GREEN}python3 main.py portfolio rebalance --config config/production/portfolio_config.yaml${NC}"
        echo ""
        echo "Monitor for 7 days:"
        echo "  - Position sizes vs. baseline"
        echo "  - CV values: grep 'empirical Kelly' logs/portfolio.log"
        echo "  - Max drawdown vs. historical"
        echo ""
        echo "Success criteria: Allocations within limits, drawdown improved 15%+"
        ;;

    3)
        FEATURE="A-S Reservation Price"
        echo -e "${YELLOW}Deploying: ${FEATURE}${NC}"
        echo ""

        python3 -c "
import yaml

# Load base config
with open('strategies/configs/prediction_mm_strategy.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Enable only A-S reservation price
config['use_reservation_price'] = True
config['risk_aversion'] = 0.03  # Conservative start
config['reservation_use_log_odds'] = False

# Ensure other features are disabled
if 'vpin_kill_switch' in config:
    config['vpin_kill_switch']['enabled'] = False

with open('config/production/prediction_mm_as.yaml', 'w') as f:
    yaml.dump(config, f)

print('Created: config/production/prediction_mm_as.yaml')
print('')
print('A-S Reservation Price enabled with conservative risk aversion.')
"

        echo ""
        echo -e "${GREEN}Config created!${NC}"
        echo ""
        echo "Run with \$10,000 capital:"
        echo -e "  ${GREEN}python3 main.py run prediction-mm --config config/production/prediction_mm_as.yaml --capital 10000${NC}"
        echo ""
        echo "Monitor for 7 days:"
        echo "  - Average inventory (should trend toward 0)"
        echo "  - Extreme position events (should decrease)"
        echo "  - Quote competitiveness (inside spread %)"
        echo ""
        echo "Success criteria: Inventory mean reversion 30%+ faster, PnL maintained"
        echo ""
        echo "Tuning: If inventory still extreme after 3 days, increase risk_aversion to 0.05"
        ;;

    4)
        FEATURE="VPIN Kill Switch"
        echo -e "${RED}WARNING: Deploy this LAST after all other features are stable${NC}"
        echo ""
        echo -e "${YELLOW}Deploying: ${FEATURE}${NC}"
        echo ""

        python3 -c "
import yaml

# Load base config
with open('strategies/configs/prediction_mm_strategy.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Enable VPIN kill switch with conservative thresholds
if 'vpin_kill_switch' not in config:
    config['vpin_kill_switch'] = {}

config['vpin_kill_switch']['enabled'] = True
config['vpin_kill_switch']['toxic_threshold'] = 0.75  # More conservative
config['vpin_kill_switch']['warning_threshold'] = 0.55
config['vpin_kill_switch']['check_interval_sec'] = 5
config['vpin_kill_switch']['toxic_cooldown_sec'] = 120  # Longer cooldown
config['vpin_kill_switch']['warning_spread_multiplier'] = 2.0

with open('config/production/prediction_mm_vpin.yaml', 'w') as f:
    yaml.dump(config, f)

print('Created: config/production/prediction_mm_vpin.yaml')
print('')
print('VPIN Kill Switch enabled with conservative thresholds.')
"

        echo ""
        echo -e "${GREEN}Config created!${NC}"
        echo ""
        echo "Run with full capital allocation:"
        echo -e "  ${GREEN}python3 main.py run prediction-mm --config config/production/prediction_mm_vpin.yaml${NC}"
        echo ""
        echo "Monitor for 7 days:"
        echo "  - Activation frequency: grep 'VPIN KILL SWITCH' logs/prediction_mm.log"
        echo "  - False positive rate (manual review)"
        echo "  - PnL during toxic vs. normal periods"
        echo ""
        echo "Success criteria: < 1 false positive per week, avoided fills have -EV"
        echo ""
        echo "Tuning:"
        echo "  - Too many triggers? Increase toxic_threshold to 0.80"
        echo "  - Missing toxic events? Decrease to 0.70"
        ;;

    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Run the strategy with recommended capital"
echo "2. Monitor for 7 days using the metrics above"
echo "3. If successful, proceed to next feature"
echo "4. After all 4 features are stable, run: bash scripts/phase4_full_production.sh"
