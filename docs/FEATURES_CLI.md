# Features CLI Reference

All 4 institutional quant features are now integrated into the `main.py` CLI as a unified "models" interface.

## Quick Start

```bash
# See all features and their status
python main.py features status

# Interactive wizard (recommended for first time)
python main.py features quickstart

# Enable a specific feature
python main.py features enable empirical-kelly

# Validate configuration
python main.py features validate

# Disable a feature
python main.py features disable vpin-kill-switch
```

---

## Commands

### `features status`

Shows status of all 4 features with descriptions and applicability.

```bash
python main.py features status
```

**Output:**
- ✓ ENABLED / ✗ DISABLED status
- Scope (portfolio, infrastructure, market-making)
- Description
- Which strategies it applies to
- Config file location

---

### `features enable <feature>`

Enable a feature with automatic configuration.

```bash
python main.py features enable empirical-kelly
python main.py features enable vpin-kill-switch
python main.py features enable sequence-gap-detection
python main.py features enable as-reservation-price
```

**What it does:**
1. Creates/updates config file
2. Sets feature to enabled
3. Adds default values for related parameters
4. Validates configuration
5. Shows next steps

**Options:**
- `--dry-run` - Show what would be done without modifying files

**Example:**
```bash
$ python main.py features enable empirical-kelly

Enabling: empirical-kelly
  Scope: portfolio
  Applies to: ALL
  Config file: config/portfolio_config.yaml

✓ empirical-kelly enabled successfully
✓ Configuration valid
  simulations: 500
  kelly_fraction: 0.5

Next steps:
  1. Run portfolio rebalance:
     python main.py portfolio rebalance

  2. Check the logs for CV adjustments:
     grep 'empirical Kelly' logs/portfolio.log

  3. View current allocations:
     python main.py portfolio status
```

---

### `features disable <feature>`

Disable a feature.

```bash
python main.py features disable vpin-kill-switch
```

**Options:**
- `-y, --yes` - Skip confirmation prompt
- `--dry-run` - Show what would be done without modifying files

---

### `features validate`

Validate configuration of all enabled features.

```bash
python main.py features validate
```

**Checks:**
- **Empirical Kelly**: simulation count >= 100, kelly_fraction in (0, 1]
- **VPIN Kill Switch**: toxic_threshold > warning_threshold, both in valid range
- **Sequence Gap Detection**: gap_tolerance reasonable
- **A-S Reservation Price**: risk_aversion > 0, reasonable range

**Example:**
```bash
$ python main.py features validate

================================================================================
FEATURE VALIDATION
================================================================================

empirical-kelly:
  ✓ Valid
    simulations: 500
    kelly_fraction: 0.5

vpin-kill-switch:
  ✗ Invalid
    - toxic_threshold (0.60) must be > warning_threshold (0.65)

================================================================================

✗ Some features have configuration issues
```

---

### `features quickstart`

Interactive wizard for feature setup.

```bash
python main.py features quickstart
```

**What it does:**
1. Shows current status
2. Recommends starting point (empirical Kelly)
3. Provides menu to enable/disable features
4. Shows next steps after each action

**Recommended for:**
- First-time setup
- Users unfamiliar with feature names
- Quick enable/disable without remembering syntax

---

## Feature Names

| Feature Name | Description | Scope |
|--------------|-------------|-------|
| `empirical-kelly` | Monte Carlo uncertainty-adjusted position sizing | Portfolio-wide |
| `sequence-gap-detection` | WebSocket message validation | Infrastructure |
| `vpin-kill-switch` | Toxic flow detection and quote cancellation | Market making |
| `as-reservation-price` | Avellaneda-Stoikov inventory unwinding | Market making |

---

## Integration with Existing Commands

Features work seamlessly with existing CLI:

```bash
# Enable empirical Kelly
python main.py features enable empirical-kelly

# Run portfolio rebalance (uses empirical Kelly if enabled)
python main.py portfolio rebalance

# Check allocations
python main.py portfolio status

# Run a strategy (features automatically applied)
python main.py run prediction-mm
```

---

## Configuration Files

Features automatically manage these configs:

| Feature | Config File | Key |
|---------|-------------|-----|
| Empirical Kelly | `config/portfolio_config.yaml` | `use_empirical_kelly` |
| VPIN Kill Switch | `strategies/configs/prediction_mm_strategy.yaml` | `vpin_kill_switch.enabled` |
| Sequence Gap Detection | `config/websocket_config.yaml` | `enable_sequence_validation` |
| A-S Reservation Price | `strategies/configs/prediction_mm_strategy.yaml` | `use_reservation_price` |

**You can also edit these files directly**, but using the CLI is recommended for:
- Automatic validation
- Default parameter setup
- Consistency across features

---

## Recommended Workflow

### First Time (Week 1)

```bash
# Check current status
python main.py features status

# Interactive setup
python main.py features quickstart
# → Select 1 (empirical-kelly)

# Verify it worked
python main.py features validate

# Run rebalance to see it in action
python main.py portfolio rebalance
```

### Add More Features (Week 2+)

```bash
# Enable next feature
python main.py features enable sequence-gap-detection

# Validate
python main.py features validate

# Run strategy that uses WebSocket
python main.py run crypto-scalp
```

---

## Troubleshooting

### Feature shows as disabled but config file says enabled

```bash
# Check config directly
cat config/portfolio_config.yaml | grep empirical_kelly

# Re-enable to fix
python main.py features enable empirical-kelly
```

### Validation fails

```bash
# See detailed errors
python main.py features validate

# Fix issues manually or re-enable
python main.py features disable <feature>
python main.py features enable <feature>
```

### Want to see what would change before enabling

```bash
# Dry run mode
python main.py features enable empirical-kelly --dry-run
```

---

## Examples

### Enable Empirical Kelly for Portfolio Optimization

```bash
# Enable
python main.py features enable empirical-kelly

# Run rebalance
python main.py portfolio rebalance

# Monitor logs
grep 'empirical Kelly' logs/portfolio.log

# Check allocations
python main.py portfolio status
```

### Enable VPIN Kill Switch for Prediction MM

```bash
# Enable
python main.py features enable vpin-kill-switch

# Run strategy
python main.py run prediction-mm

# Monitor activations
grep 'VPIN KILL SWITCH' logs/prediction_mm.log
```

### Enable All Features

```bash
# Enable all 4
python main.py features enable empirical-kelly
python main.py features enable sequence-gap-detection
python main.py features enable vpin-kill-switch
python main.py features enable as-reservation-price

# Validate all
python main.py features validate

# Check status
python main.py features status
```

### Disable a Feature

```bash
# With confirmation
python main.py features disable vpin-kill-switch

# Skip confirmation
python main.py features disable vpin-kill-switch -y
```

---

## vs. Bash Scripts (Old Way)

### Old Way (Bash Scripts)
```bash
# Manual, error-prone
bash scripts/quickstart_empirical_kelly.sh
bash scripts/phase1_backtest_validation.sh
```

**Problems:**
- Separate scripts for each feature
- No validation
- No status checking
- Manual config editing

### New Way (CLI Interface)
```bash
# Unified, validated
python main.py features enable empirical-kelly
python main.py features status
python main.py features validate
```

**Benefits:**
- ✅ Integrated into main CLI
- ✅ Automatic validation
- ✅ Consistent interface
- ✅ Status checking
- ✅ Dry-run mode
- ✅ Interactive wizard

---

## Advanced Usage

### Programmatic Access

```python
from core.feature_manager import Feature, FeatureManager

fm = FeatureManager()

# Get status
status = fm.get_status(Feature.EMPIRICAL_KELLY)
print(f"Enabled: {status.enabled}")

# Enable programmatically
fm.enable(Feature.EMPIRICAL_KELLY)

# Validate
result = fm.validate(Feature.EMPIRICAL_KELLY)
if result.get("valid"):
    print("Configuration valid")
```

### Custom Config Paths

```python
from pathlib import Path
from core.feature_manager import FeatureManager

# Use custom project root
fm = FeatureManager(project_root=Path("/path/to/project"))
fm.enable(Feature.EMPIRICAL_KELLY)
```

---

## See Also

- `docs/QUICK_START_NEW_FEATURES.md` - Comprehensive feature guide
- `docs/FEATURE_APPLICABILITY.md` - Which features apply to which strategies
- `docs/FEATURE_ROLLOUT_PLAN.md` - Phased deployment strategy
- `docs/EMPIRICAL_KELLY.md` - Empirical Kelly deep dive
- `docs/VPIN_KILL_SWITCH.md` - VPIN kill switch guide
- `docs/SEQUENCE_GAP_DETECTION.md` - Sequence gap detection guide
- `docs/AVELLANEDA_STOIKOV_RESERVATION_PRICE.md` - A-S reservation price guide
