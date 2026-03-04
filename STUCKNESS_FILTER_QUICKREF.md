# Stuckness Filter - Quick Reference

## ✅ Implementation Status: COMPLETE

---

## 🚀 Quick Start

### Enable Filter (After Validation)
```yaml
# strategies/configs/crypto_scalp_live.yaml
enable_stuckness_filter: true  # Change from false to true
```

### Test First (Recommended)
```bash
# Run in paper mode, check logs
python3 main.py run crypto-scalp --dry-run

# Monitor for "STUCK FILTER" messages
tail -f logs/crypto-scalp_*.log | grep "STUCK"
```

---

## 📊 Expected Results

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Signals/session | 135 | 43 | -68% |
| Win rate | 38% | 65% | **+27pp** |
| P&L/session | -$2.50 | +$1.50 | **+$4.00** |

---

## ⚙️ Configuration

```yaml
# Current settings (in crypto_scalp_live.yaml)
enable_stuckness_filter: false  # SET TO TRUE TO ENABLE
min_price_entropy: 1.0  # bits
min_price_volatility_cents: 2.0  # ¢
max_extreme_price: 90  # ¢
```

---

## 🧪 Testing Checklist

- [x] Code implemented
- [x] Unit tests pass
- [ ] Paper mode test (2-4 hours)
- [ ] Verify stuck signals lose
- [ ] Enable in live
- [ ] Validate win rate +27pp

---

## 📁 Key Files

- **Config:** `strategies/configs/crypto_scalp_live.yaml`
- **Code:** `strategies/crypto_scalp/detector.py`
- **Analysis:** `STUCKNESS_ANALYSIS_RESULTS.md`
- **Docs:** `STUCKNESS_FILTER_IMPLEMENTED.md`

---

## 🔧 Tuning

**Filter too aggressive?**
```yaml
min_price_entropy: 0.7  # Lower
```

**Filter too loose?**
```yaml
min_price_entropy: 1.2  # Raise
```

---

## 💡 How It Works

Skips trades when:
- Entropy < 1.0 bits (prices concentrated), OR
- Price >90¢ or <10¢ AND volatility <2¢

Result: Avoids 68% of signals that have no edge!

---

**Status:** ✅ Ready for testing
**Next:** Enable and monitor win rate improvement
