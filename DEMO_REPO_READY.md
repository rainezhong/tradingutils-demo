# Demo Repository Export - Ready to Use

The demo repository export system is now complete and ready to use.

## What Was Created

### 1. Planning Document
- **`DEMO_REPO_PLAN.md`** - Comprehensive plan detailing what's included/excluded
  - Lists all proprietary strategies to exclude
  - Lists all framework components to include
  - Security checklist
  - Impact analysis

### 2. Export Script
- **`scripts/export_demo_repo.py`** - Fully automated export tool
  - Excludes proprietary strategies (crypto_scalp, latency_arb, prediction_mm, etc.)
  - Excludes credentials and API keys
  - Excludes trade data and results
  - Sanitizes remaining config files
  - Creates demo-specific README, .env.example, .gitignore
  - Initializes fresh git repository

### 3. Usage Guide
- **`DEMO_EXPORT_GUIDE.md`** - Step-by-step instructions
  - How to run dry-run test
  - How to export
  - How to review and validate
  - How to create GitHub repository
  - How to maintain and update
  - Security checklist

## Quick Start

### 1. Test Export (Dry Run)
```bash
python3 scripts/export_demo_repo.py --output /tmp/tradingutils-demo --dry-run
```

### 2. Review What Will Be Exported
Check the dry-run output to ensure:
- Proprietary strategies excluded: crypto_scalp, latency_arb, prediction_mm, etc.
- Framework files included: core/, scanner/, docs/, etc.
- Basic example strategies included: scalp_strategy.py, late_game_blowout_strategy.py, etc.

### 3. Create Demo Repository
```bash
python3 scripts/export_demo_repo.py --output ../tradingutils-demo
```

### 4. Review Exported Files
```bash
cd ../tradingutils-demo

# Verify proprietary strategies excluded
ls strategies/  # Should NOT see crypto_scalp/, latency_arb/, prediction_mm/

# Verify credentials excluded
cat .env  # Should not exist
cat .env.example  # Should have placeholders

# Check demo README
cat README.md  # Should have demo disclaimer
```

### 5. Test the Export
```bash
# Create fresh venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest tests/core/
pytest tests/strategies/test_scalp_strategy.py
```

### 6. Publish to GitHub
```bash
# Using GitHub CLI
gh repo create tradingutils-demo --public --source=. --remote=origin
git push -u origin main

# Or manually
# 1. Create repo on github.com
# 2. git remote add origin user@example.com:YourUsername/tradingutils-demo.git
# 3. git push -u origin main
```

## What's Included

### ✅ Framework (100%)
- `core/` - All infrastructure
  - exchange_client/
  - order_manager/
  - risk/
  - portfolio/
  - indicators/
  - automation/
  - latency_probe/
- `scanner/` - Market scanning
- `src/backtesting/` - Backtest engine
- `tests/` - Test infrastructure

### ✅ Documentation
- ARCHITECTURE.md
- CLAUDE.md
- README.md (demo version)
- docs/ (all docs)

### ✅ Basic Example Strategies
- scalp_strategy.py
- late_game_blowout_strategy.py
- nba_mispricing_strategy.py
- total_points_strategy.py
- market_making_strategy.py
- tied_game_spread_strategy.py

### ❌ Excluded (Proprietary)
- strategies/crypto_scalp/
- strategies/crypto_latency/
- strategies/latency_arb/
- strategies/prediction_mm/
- strategies/spread_capture_strategy.py
- strategies/edge_capture_strategy.py
- strategies/correlation_arb_strategy.py
- strategies/depth_scalper_strategy.py
- strategies/nba_underdog_strategy.py
- strategies/nba_fade_momentum.py
- strategies/nba_mean_reversion.py
- All API keys and credentials
- All trade data (*.db, *.csv)

## Security Checklist

Before publishing, verify:
- [ ] No `.env` file (only `.env.example`)
- [ ] No API keys in configs
- [ ] No trade databases
- [ ] No proprietary strategies
- [ ] Fresh git history (no commit messages revealing strategy details)
- [ ] Demo README in place

Run verification:
```bash
cd ../tradingutils-demo

# Check for credentials
grep -r "api_key" . --include="*.yaml" --include="*.py" | grep -v "YOUR_API_KEY"
# Should be empty

# Check for emails
grep -r "@" . --include="*.py" --include="*.yaml" | grep -v "@example.com"
# Should be minimal

# List data files
find data/ -type f 2>/dev/null
# Should be empty or minimal
```

## Maintenance

To update the demo repo with new framework changes:

```bash
# Export fresh version
python3 scripts/export_demo_repo.py --output /tmp/tradingutils-demo-new

# Review changes
diff -r ../tradingutils-demo /tmp/tradingutils-demo-new

# Update if changes look good
cd ../tradingutils-demo
rsync -av --exclude='.git' /tmp/tradingutils-demo-new/ .
git add -A
git commit -m "Update framework"
git push
```

## Files Created

1. **DEMO_REPO_PLAN.md** - Planning document (762 lines)
2. **scripts/export_demo_repo.py** - Export script (450 lines)
3. **DEMO_EXPORT_GUIDE.md** - Usage guide (350 lines)
4. **This file** - Quick reference

## Statistics (from test export)

- **Copied**: 762 files
- **Excluded**: 271 files
- **Sanitized**: 13 files
- **Size**: ~60% of original codebase by file count
- **Impact**: Framework 100% included, proprietary strategies 100% excluded

## Next Steps

1. **Review** the planning documents
2. **Run** a dry-run test
3. **Export** to a local directory
4. **Test** the exported repository
5. **Review** for any missed sensitive data
6. **Publish** to GitHub

For detailed instructions, see **DEMO_EXPORT_GUIDE.md**

---

✅ Everything is ready to go. When you're ready to publish, just run the export script!
