# TrendLock 40 Exit Optimization Sweep Results

## Baseline

**Current production: 4H MA240 + crossover entry + min_hold exit + 0.1% fee/side**

| min_hold | 2y Return | 3y Return | 6y Return | 6y Drawdown | 6y Trades | Mean Return |
|----------|-----------|-----------|-----------|-------------|-----------|-------------|
| 12 bars  | 84.82%    | 314.77%   | 1901.57%  | -41.31%     | 189       | **767.05%** |
| 18 bars  | 71.64%    | 279.38%   | 1762.83%  | -39.61%     | 171       | **704.62%** |
| 24 bars  | 69.74%    | 279.34%   | 1852.15%  | -43.10%     | 163       | **733.74%** |

Production config (mh=24) mean return: 733.74%.

## Group 1: Exit Confirm (highest priority)

After min_hold, require N consecutive bars with close < MA before selling.

| exit_confirm | min_hold | 2y | 3y | 6y | 6y DD | 6y Trades | Mean |
|-------------|----------|-----|------|------|-------|-----------|------|
| 2 | 12 | 73.94% | 298.94% | **2200.24%** | -39.42% | 153 | **857.71%** |
| 2 | 18 | 74.07% | 293.79% | **2141.88%** | -40.59% | 147 | **836.58%** |
| 3 | 12 | 73.13% | 294.16% | 2038.69% | -40.53% | 143 | 801.99% |
| 5 | 12 | 78.08% | 306.87% | 1659.34% | -48.79% | 131 | 681.43% |

**Winner: exit_confirm=2, min_hold=12**
- Mean return 857.71% vs baseline 733.74% (+16.9%)
- 6y return +2200% vs +1852% (+348pp absolute)
- 6y drawdown -39.42% vs -43.10% (improved by 3.7pp)
- 6y trades 153 vs 163 (fewer)
- Better in ALL three windows vs baseline mh=24

Note: exit_confirm=1 produces identical results to baseline (1 bar below MA = same as baseline condition), validating implementation correctness. exit_confirm=5 over-delays exits and worsens 6y performance.

## Group 2: Exit Buffer

After min_hold, require close < MA * (1 - buffer%) to sell.

| buffer | min_hold | 2y | 3y | 6y | 6y DD | 6y Trades | Mean |
|--------|----------|-----|------|------|-------|-----------|------|
| 0.5% | 24 | 62.02% | 275.86% | **2114.90%** | **-37.52%** | 143 | **817.59%** |
| 1.0% | 24 | 58.80% | 271.66% | 2017.91% | -41.66% | 123 | 782.79% |
| 0.5% | 12 | 63.75% | 282.32% | 1938.01% | -39.30% | 159 | 761.36% |

**Best: buffer=0.5%, min_hold=24**
- Mean return 817.59% vs baseline 733.74% (+11.4%)
- 6y return +2114% vs +1852% (+263pp)
- Best drawdown of all groups: -37.52%
- But 2y return is worse (62.02% vs 69.74%)

Confirms research paper Table 3 finding: exit buffer improves long-term performance. The 0.5% buffer is more robust than the 1% tested in the paper.

## Group 3: Entry Confirm

Require N consecutive bars above MA to enter (replaces crossover).

| entry_confirm | min_hold | 2y | 3y | 6y | 6y DD | Mean |
|--------------|----------|-----|------|------|-------|------|
| 3 | 24 | 68.13% | 276.23% | 1727.68% | -48.23% | 690.68% |
| 1 | 24 | 71.20% | 279.34% | 1697.47% | -43.10% | 682.67% |
| 3 | 18 | 61.21% | 265.23% | 1685.40% | -48.41% | 670.61% |

**Verdict: Entry confirm underperforms baseline across all configurations.**
- Best mean 690.68% vs baseline 733.74% (-5.9%)
- Worse drawdown in most configs
- Confirms research paper warning: "过强的进场过滤会错过BTC主升浪"

## Group 4: Symmetric Confirm

Both entry and exit require N consecutive bars.

| confirm | min_hold | 2y | 3y | 6y | 6y DD | Mean |
|---------|----------|-----|------|------|-------|------|
| 2 | 12 | 87.51% | 311.96% | 1955.36% | -42.97% | 784.94% |
| 1 | 12 | 86.41% | 314.77% | 1742.97% | -41.31% | 714.72% |
| 2 | 18 | 74.88% | 281.18% | 1727.89% | -45.83% | 694.65% |

**Verdict: Symmetric cb=2 mh=12 beats baseline (784.94% vs 733.74%) but worse than exit_confirm alone (857.71%).**
The entry confirm component drags down the exit confirm benefit, as predicted.

## Summary Ranking

| Rank | Configuration | Mean Return | vs Baseline | 6y DD |
|------|--------------|-------------|-------------|-------|
| 1 | exit_confirm=2, mh=12 | **857.71%** | +16.9% | -39.42% |
| 2 | exit_confirm=2, mh=18 | **836.58%** | +14.0% | -40.59% |
| 3 | exit_buffer=0.5%, mh=24 | **817.59%** | +11.4% | **-37.52%** |
| 4 | exit_confirm=3, mh=12 | 801.99% | +9.3% | -40.53% |
| 5 | symmetric=2, mh=12 | 784.94% | +7.0% | -42.97% |
| — | **baseline mh=24** | **733.74%** | — | -43.10% |
| — | entry_confirm=3, mh=24 | 690.68% | -5.9% | -48.23% |

## Conclusions

1. **Exit Confirm (ecb=2) is the clear winner.** Requiring 2 consecutive bars below MA before selling filters out false breakdowns without over-delaying real exits. Best combined with shorter min_hold (12 bars = 2D).

2. **Exit Buffer (0.5%) is the runner-up**, with the best drawdown control. Good alternative if lower volatility is prioritized over maximum return.

3. **Entry Confirm hurts performance**, confirming the research paper's finding. BTC profits come from catching trend starts quickly — delaying entry loses more than it saves from avoiding whipsaws.

4. **Symmetric Confirm is worse than exit-only confirm.** The entry filtering component cancels out part of the exit improvement.

5. **The optimal min_hold shifts down when exit_confirm is added.** With exit_confirm=2, mh=12 beats mh=24. This makes sense: exit_confirm already provides whipsaw protection at the exit, so a long min_hold becomes redundant and just delays profitable exits.

## Recommended Next Steps

1. Validate exit_confirm=2 + mh=12 against production signal semantics with real trade timestamps
2. Consider combining exit_confirm=2 with exit_buffer=0.5% (hybrid exit)
3. If adopting, update production config: min_hold_bars=12, add exit_confirm_bars=2
