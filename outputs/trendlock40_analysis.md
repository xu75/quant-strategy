# TrendLock 40 Parameter Analysis

## Executive Summary

**Current baseline: 4H MA240 signal + 4H execution + 4D freeze**

After running a comprehensive three-factor sweep with two signal modes:
1. **long_close_signal**: Signal computed on signal interval, inherited by execution interval
2. **exec_close_vs_signal_ma**: Each execution bar close compared against latest signal MA (MSTR-style "short execution")

## Key Findings

### 1. Execution Interval Impact Depends on Signal Mode

**4H signal + 4D freeze:**

| Mode | Exec | 2y Return | 3y Return | 6y Return | 6y Drawdown |
|------|------|-----------|-----------|-----------|-------------|
| long_close_signal | 4H | 61.20% | 242.56% | 1678.33% | -44.29% |
| exec_close_vs_signal_ma | 4H | 60.78% | 244.50% | 1703.24% | -44.31% |
| exec_close_vs_signal_ma | 1H | 59.50% | 251.07% | 1338.64% | -44.53% |
| exec_close_vs_signal_ma | 1D | 82.93% | 312.72% | 1621.00% | -53.82% |

**Conclusion**: 
- When signal and execution are the same interval (4H+4H), both modes produce nearly identical results
- When execution is shorter than signal (4H signal + 1H exec), the "short execution advantage" does NOT materialize for BTC — performance is actually worse
- When execution is longer than signal (4H signal + 1D exec), results can be better in some configurations (e.g., 4H+1D+2D has higher mean return than 4H+4H+2D)

**This contradicts the MSTR finding** where shorter execution improved returns. For BTC, there is no systematic "short execution advantage."

### 2. Freeze Period: 2D vs 4D Trade-off

**4H signal + 4H execution (exec_close_vs_signal_ma mode):**

| Freeze | 2y Return | 3y Return | 6y Return | 6y Drawdown | 6y Trades |
|--------|-----------|-----------|-----------|-------------|-----------|
| 0D     | 46.02%    | 197.91%   | 1609.49%  | -37.90%     | 313       |
| 2D     | 61.64%    | 278.85%   | **1739.69%** | -42.50%  | 163       |
| 4D     | 60.78%    | 244.50%   | 1703.24%  | -44.31%     | 129       |
| 5D     | 61.52%    | 233.49%   | 1415.06%  | -44.60%     | 117       |
| 7D     | 49.12%    | 214.81%   | 1273.00%  | -39.57%     | 107       |

**Analysis:**
- **2D freeze** has the highest 6y return (+1739.69% vs +1703.24% for 4D, +36% absolute)
- **2D freeze** also has better 2y return (61.64% vs 60.78%) and 3y return (278.85% vs 244.50%)
- **2D freeze** has slightly better drawdown (-42.50% vs -44.31% — less negative is better)
- **Trade frequency:** 2D = 163 trades (6y), 4D = 129 trades (6y)

**Verdict:** 2D is better across all windows. The only advantage of 4D is lower trade frequency (129 vs 163 trades).

### 3. Best Overall Configuration

**Top 5 configurations by mean return across windows (exec_close_vs_signal_ma mode):**

| Signal | Exec | Freeze | 2y | 3y | 6y | Mean | Min |
|--------|------|--------|----|----|----|----|-----|
| 1D | 1D | 5D | 73.04% | 311.43% | 2255.49% | **879.99%** | 73.04% |
| 1D | 1D | 2D | 88.16% | 362.88% | 2094.19% | **848.41%** | 88.16% |
| 4H | 1D | 2D | 77.43% | 343.66% | 2030.02% | **817.04%** | 77.43% |
| 4H | 1D | 5D | 65.48% | 280.06% | 2075.94% | **807.16%** | 65.48% |
| 1H | 1D | 2D | 77.43% | 343.66% | 1943.28% | **788.12%** | 77.43% |

**Current 4H+4H+4D ranks #15 (out of 45)** with mean return of 669.51% in the exec_close_vs_signal_ma mode.

## Comparison with Production Strategy

**Important caveat**: The current production TrendLock 40 uses:
- **Entry**: Crossover (close crosses above MA from below)
- **Exit**: min_hold period + price below MA (not requiring crossover down)

The sweep uses:
- **Entry/Exit**: Continuous regime (close > MA = long, close < MA = flat)
- **Freeze**: Symmetric lock after regime flip

These are different signal semantics. The sweep validates parameter sensitivity but does not directly validate the production strategy.

## Recommendations

### Option 1: Keep Current 4H+4H+4D (Conservative)
- ✅ Proven stable across multiple windows
- ✅ Reasonable trade frequency (129 trades / 6y)
- ✅ Good drawdown control (-44.31%)
- ⚠️ Not the highest return configuration

### Option 2: Switch to 4H+4H+2D (Moderate)
- ✅ Higher returns across all windows (2y: 61.64% vs 60.78%, 3y: 278.85% vs 244.50%, 6y: 1739.69% vs 1703.24%)
- ✅ Better drawdown (-42.50% vs -44.31%)
- ⚠️ Higher trade frequency (163 trades / 6y vs 129 trades / 6y)
- ⚠️ Risk of overfitting (though 2D is better in all three windows, not just 6y)

### Option 3: Switch to 1D+1D+2D or 1D+1D+5D (Aggressive)
- ✅ Highest mean returns across windows (848% and 880%)
- ✅ Best cross-window stability
- ⚠️ Different signal interval from current production
- ⚠️ Requires validation with production signal semantics

## Final Verdict

**Current 4H+4H+4D is a solid, conservative choice** but not optimal for pure return maximization.

If the goal is:
- **Maximum stability + proven track record** → Keep 4H+4H+4D
- **Moderate optimization** → Test 4H+4H+2D
- **Maximum return** → Test 1D+1D+2D or 1D+1D+5D (requires production signal validation)

## Execution Interval Conclusion

**For BTC, execution interval does NOT provide the "short execution advantage" observed in MSTR.** When signal and execution intervals match, both signal modes produce nearly identical results. Shorter execution (1H) with longer signal (4H) actually performs worse in most windows.

