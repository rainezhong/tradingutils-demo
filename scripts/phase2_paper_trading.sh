#!/bin/bash
# Phase 2: Paper Trading
# Run strategies in dry-run mode with all features enabled

set -e

echo "=== Phase 2: Paper Trading ==="
echo "This will run prediction MM in DRY RUN mode with all features enabled."
echo "Duration: 48-72 hours recommended"
echo ""

cd "$(dirname "$0")/.."

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Create paper trading configs
mkdir -p config/paper_trading

echo -e "${YELLOW}Creating paper trading config...${NC}"

# Generate paper trading config with all features enabled
python3 -c "
import yaml
from pathlib import Path

# Load base config
base_config_path = Path('strategies/configs/prediction_mm_strategy.yaml')
if base_config_path.exists():
    with open(base_config_path, 'r') as f:
        config = yaml.safe_load(f)
else:
    config = {}

# Force dry run
config['dry_run'] = True

# Enable all features
config['use_reservation_price'] = True
config['risk_aversion'] = 0.05

if 'vpin_kill_switch' not in config:
    config['vpin_kill_switch'] = {}
config['vpin_kill_switch']['enabled'] = True
config['vpin_kill_switch']['toxic_threshold'] = 0.70
config['vpin_kill_switch']['warning_threshold'] = 0.50
config['vpin_kill_switch']['check_interval_sec'] = 5
config['vpin_kill_switch']['toxic_cooldown_sec'] = 60

# Save paper trading config
with open('config/paper_trading/prediction_mm_paper.yaml', 'w') as f:
    yaml.dump(config, f)

# Portfolio config
portfolio_config = {
    'use_empirical_kelly': True,
    'empirical_kelly_simulations': 1000,
    'kelly_fraction': 0.5,
    'max_allocation_per_strategy': 0.25,
    'max_total_allocation': 0.80,
}

with open('config/paper_trading/portfolio_paper.yaml', 'w') as f:
    yaml.dump(portfolio_config, f)

print('Paper trading configs created:')
print('  - config/paper_trading/prediction_mm_paper.yaml')
print('  - config/paper_trading/portfolio_paper.yaml')
"

echo -e "${GREEN}Configs created!${NC}"
echo ""

# Create monitoring script
cat > scripts/paper_trading_monitor.py << 'EOF'
#!/usr/bin/env python3
"""Monitor paper trading session and log feature activations."""

import time
import sys
from datetime import datetime
from pathlib import Path

class PaperTradingMonitor:
    def __init__(self, log_dir='logs/paper_trading'):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.start_time = time.time()

        self.metrics = {
            'vpin_triggers': [],
            'sequence_gaps': [],
            'kelly_adjustments': [],
            'reservation_price_calcs': 0,
        }

    def tail_logs(self):
        """Tail relevant log files and extract metrics."""
        log_patterns = {
            'VPIN KILL SWITCH': 'vpin',
            'sequence gap detected': 'gap',
            'empirical Kelly': 'kelly',
            'reservation price': 'reservation',
        }

        # Try to tail the main log file
        log_file = Path('logs/prediction_mm.log')
        if not log_file.exists():
            print(f"Log file not found: {log_file}")
            return

        with open(log_file, 'r') as f:
            # Seek to end if file is large
            f.seek(0, 2)
            size = f.tell()
            if size > 10000:
                f.seek(size - 10000)

            for line in f:
                for pattern, metric_type in log_patterns.items():
                    if pattern in line:
                        self._process_log_line(line, metric_type)

    def _process_log_line(self, line, metric_type):
        """Process a log line and update metrics."""
        timestamp = datetime.now()

        if metric_type == 'vpin':
            # Extract VPIN value and state
            self.metrics['vpin_triggers'].append({
                'time': timestamp,
                'line': line.strip()
            })
            print(f"[{timestamp}] VPIN: {line.strip()}")

        elif metric_type == 'gap':
            self.metrics['sequence_gaps'].append({
                'time': timestamp,
                'line': line.strip()
            })
            print(f"[{timestamp}] GAP: {line.strip()}")

        elif metric_type == 'kelly':
            self.metrics['kelly_adjustments'].append({
                'time': timestamp,
                'line': line.strip()
            })
            print(f"[{timestamp}] KELLY: {line.strip()}")

        elif metric_type == 'reservation':
            self.metrics['reservation_price_calcs'] += 1

    def print_summary(self):
        """Print summary statistics."""
        runtime = time.time() - self.start_time
        hours = runtime / 3600

        print("\n" + "="*60)
        print(f"Paper Trading Summary ({hours:.1f} hours)")
        print("="*60)
        print(f"VPIN triggers:          {len(self.metrics['vpin_triggers'])}")
        print(f"Sequence gaps:          {len(self.metrics['sequence_gaps'])}")
        print(f"Kelly adjustments:      {len(self.metrics['kelly_adjustments'])}")
        print(f"Reservation price calcs: {self.metrics['reservation_price_calcs']}")
        print("="*60)

        # Save metrics
        import json
        output_file = self.log_dir / f'metrics_{int(time.time())}.json'
        with open(output_file, 'w') as f:
            json.dump({
                'runtime_hours': hours,
                'metrics': {
                    'vpin_trigger_count': len(self.metrics['vpin_triggers']),
                    'sequence_gap_count': len(self.metrics['sequence_gaps']),
                    'kelly_adjustment_count': len(self.metrics['kelly_adjustments']),
                    'reservation_calc_count': self.metrics['reservation_price_calcs'],
                }
            }, f, indent=2)
        print(f"\nMetrics saved to: {output_file}")

if __name__ == '__main__':
    monitor = PaperTradingMonitor()

    try:
        print("Starting paper trading monitor...")
        print("Press Ctrl+C to view summary and exit\n")

        while True:
            monitor.tail_logs()
            time.sleep(10)  # Check every 10 seconds

    except KeyboardInterrupt:
        print("\n\nStopping monitor...")
        monitor.print_summary()
        sys.exit(0)
EOF

chmod +x scripts/paper_trading_monitor.py

echo -e "${YELLOW}Starting paper trading session...${NC}"
echo ""
echo "This will run in DRY RUN mode (no real orders)."
echo "Recommended: Run for 48-72 hours."
echo ""
echo "In one terminal, run:"
echo -e "  ${GREEN}python3 main.py run prediction-mm --config config/paper_trading/prediction_mm_paper.yaml${NC}"
echo ""
echo "In another terminal, run the monitor:"
echo -e "  ${GREEN}python3 scripts/paper_trading_monitor.py${NC}"
echo ""
echo "After 48-72 hours, review:"
echo "  - VPIN trigger frequency (expect: low)"
echo "  - Sequence gaps (expect: 0-2 per day)"
echo "  - Kelly adjustments (expect: reasonable CV values)"
echo "  - Overall system stability"
echo ""
echo "If successful, proceed to Phase 3 (live with minimal capital)"
echo -e "  Run: ${GREEN}bash scripts/phase3_live_single_feature.sh${NC}"
