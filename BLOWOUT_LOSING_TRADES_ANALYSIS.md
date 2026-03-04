# Late Game Blowout Strategy - Losing Trades Analysis

## Executive Summary

The blowout strategy had **4 losing trades** out of 21 total trades:
- **2 small losses**: -$0.30 each (buying favorites at slight premium)
- **2 catastrophic losses**: -$4.63 and -$4.72 (heavy favorites who collapsed)

The two big losses account for **$9.35 of the $9.41 in total losses**, making them responsible for the strategy's negative expectancy.

---

## Loss Pattern #1: Premium Overpayment (2 trades, -$0.60 total)

### CLE vs LAC (Feb 4, 2026)
- **Entry**: Bought CLE at $1.03
- **Settlement**: CLE won → paid $1.00
- **Loss**: -$0.30 (overpaid by 3 cents)

### IND vs TOR (Feb 8, 2026)
- **Entry**: Bought TOR at $1.03
- **Settlement**: TOR won → paid $1.00
- **Loss**: -$0.30 (overpaid by 3 cents)

**Root Cause**: Strategy bought heavy favorites (>97% win probability) at a premium above fair value. Even though the bets won, we overpaid for certainty.

**Impact**: Minimal (-0.3% of bankroll each)

---

## Loss Pattern #2: Late Game Collapse (2 trades, -$9.35 total)

### LOSS #1: Memphis Grizzlies @ Golden State Warriors (-$4.63)

**Entry Context** (Frame 9125, Late Q3):
- **Score at entry**: MEM 96 - GSW 80 (MEM +16)
- **Time remaining**: 0:38 left in Q3
- **Entry price**: $0.89 (89% win probability)
- **Position**: Bought 5 contracts at $0.92 (with slippage)
- **Expected value**: +$4.45 profit

**The Collapse**:
| Time | Score | MEM Lead | Win Prob |
|------|-------|----------|----------|
| Q3 0:38 (Entry) | MEM 96-80 GSW | +16 | 89% |
| End Q3 | MEM 98-85 GSW | +13 | 82% |
| Q4 10:52 | MEM 102-87 GSW | +15 | 90% |
| Q4 7:02 | MEM 108-97 GSW | +11 | 91% |
| Q4 4:40 | MEM 111-103 GSW | +8 | 89% |
| Q4 2:46 | MEM 113-110 GSW | +3 | 69% |
| Q4 1:54 | MEM 113-112 GSW | +1 | 58% |
| **FINAL** | **MEM 113-114 GSW** | **-1** | **0%** |

**Swing**: 17-point swing against us (from +16 to -1)

**Final Result**:
- MEM lost 113-114
- Settlement: $0.00
- **Loss: -$4.63** (including $0.03 fees)

---

### LOSS #2: Houston Rockets @ New York Knicks (-$4.72)

**Entry Context** (Frame 10673, Mid Q3):
- **Score at entry**: HOU 82 - NYK 67 (HOU +15)
- **Time remaining**: 2:42 left in Q3
- **Entry price**: $0.89 (89% win probability)
- **Position**: Bought 5 contracts at $0.94 (with slippage)
- **Expected value**: +$4.45 profit

**The Collapse**:
| Time | Score | HOU Lead | Win Prob |
|------|-------|----------|----------|
| Q3 2:42 (Entry) | HOU 82-67 NYK | +15 | 89% |
| End Q3 | HOU 91-75 NYK | +16 | 94% |
| Q4 10:53 | HOU 93-75 NYK | +18 | 97% |
| Q4 7:05 | HOU 97-91 NYK | +6 | 80% |
| Q4 3:47 | HOU 99-95 NYK | +4 | 70% |
| Q4 0:47 | HOU 103-103 NYK | 0 | 45% |
| Q4 0:06 | HOU 103-105 NYK | -2 | 8% |
| **FINAL** | **HOU 106-108 NYK** | **-2** | **0%** |

**Swing**: 17-point swing against us (from +15 to -2)

**Final Result**:
- HOU lost 106-108
- Settlement: $0.00
- **Loss: -$4.72** (including $0.02 fees)

---

## Key Insights

### 1. **Identical Entry Pattern**
Both catastrophic losses entered at:
- **Late Q3** (0:38 and 2:42 remaining in Q3)
- **+15-16 point lead** for our team
- **89% implied win probability**
- **Expected profit of ~$4.45 per trade**

### 2. **The Classic "Bad Beat"**
These are textbook examples of low-probability, high-impact events:
- Buying at 89-94% win probability
- Actually experiencing the 6-11% loss scenario
- Result: 100% loss of capital ($4.60-$4.70 per trade)

### 3. **Late Game Volatility**
Both games showed the team INCREASING their lead after our entry (MEM went to +15, HOU went to +18), giving false confidence before the collapse.

### 4. **Runaway Collapses**
Once the lead started shrinking significantly (below +5), the collapse accelerated:
- **MEM**: Lead went from +11 → +3 in under 2 minutes
- **HOU**: Lead went from +6 → 0 in under 4 minutes

### 5. **High Conviction, Wrong Direction**
The strategy entered with high conviction (89% win probability), but:
- These were 11% tail events that materialized
- No exit mechanism when the thesis was invalidating
- Position held until worthless (0.00 settlement)

---

## Statistical Reality Check

With 21 trades and 89% entry win probability:
- **Expected losses at this confidence**: 21 × 0.11 = 2.31 losses
- **Actual losses**: 4 (above expected)
- **Expected cost of 2.31 losses**: 2.31 × $4.70 = $10.86
- **Actual cost**: $9.95 total losses ($9.35 from big losses)

**Conclusion**: The results are statistically consistent with the entry probabilities. The strategy is experiencing the downside of its own risk model.

---

## Recommendations

### 1. **Add Stop-Loss Protection**
- Exit when lead drops below a threshold (e.g., +5 points in Q4)
- Exit when win probability drops below 70%
- Prevents riding positions to zero

### 2. **Stricter Entry Criteria**
- Require larger leads (e.g., +20 points instead of +15)
- Enter later in Q4 when less time for comebacks
- Avoid entering during opponent's momentum runs

### 3. **Position Sizing**
- Reduce size on lower-conviction entries (85-90% vs 95%+)
- Kelly criterion suggests much smaller sizes for 89% edges

### 4. **Momentum Filter**
- Track recent scoring runs (last 2-3 minutes)
- Avoid entry if opponent is on a run (e.g., 8-0 run)
- Both losses may have had warning signs in recent momentum

### 5. **Expected Value Threshold**
- Current: Entering at any positive EV with 10%+ edge
- Proposed: Require higher edge or better win probability to offset tail risk

---

## Conclusion

The blowout strategy's negative performance is driven by **two catastrophic losses** from entering heavy favorites (89% win probability) who then collapsed in the final minutes. While the 81% overall win rate looks good, the 4:1 loss-to-win ratio ($4.70 losses vs $0.47 wins) makes the strategy unprofitable.

**The core issue**: The strategy correctly identifies late-game blowouts but lacks risk management for the 10-15% of cases where heavy favorites collapse. Adding stop-loss protection and stricter entry criteria could transform this from a -2.5% strategy into a profitable one.
