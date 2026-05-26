---
feature_ids: []
topics: ["strategy", "research", "backtest", "momentum", "rotation", "etf", "premium"]
doc_kind: "strategy-research"
created: "2026-05-26"
strategy_id: "n100_guard_z"
strategy_name: "N100 Guard-Z (纳指100ETF防守轮动)"
---

# N100 Guard-Z 研究文档

## 1. 研究目标

- 将 DualMom-B FastRe 的 US 择时能力与 A 股纳指100 ETF 溢价轮动结合
- 二元口径（NDX/CASH）简化信号层，执行层通过 Z-Score 选择最优 ETF 标的
- 实现 T+1 跨市场执行：T日US收盘信号 → T+1日A股开盘执行

## 2. 策略定义

### 信号层：DualMom-B FastRe（二元口径）
- 出场信号（月频）：月末 QQQ ≤ SMA225 → 转入现金
- 回场信号（周频）：TRUE_CASH_STRETCH 中每周五 QQQ > SMA225 且 SPY 12m > 0 且 QQQ 12m > SPY 12m
- 二元映射：SPY_INVESTED → CASH（不触发 re-entry）

### 执行层：Hybrid Z-Score 溢价轮动
- 仅 risk_on 期间生效，从 11 只纳指100 ETF 中选最优标的
- 切换条件（同时满足）：z-score 差 > 1.0、绝对溢价差 > 16bps、持有 ≥ 10 天
- 溢价计算：tradable_premium = close / estimated_nav - 1

## 3. 数据与假设

- US 数据：QQQ/SPY 日线（yfinance, auto_adjust=True）
- A 股数据：11 只纳指100 ETF 日线 + NAV（AKShare）
- 交易成本：0.1%/side，轮动 2×fee，进出场 1×fee
- 空仓期收益：货币基金年化 2%
- adj_close = close × terminal_split_factor（常数乘数）

## 4. 回测设置

- 执行语义：T+1（T日US收盘信号，T+1日A股执行）
- 回测起点：受限于 A 股 ETF 上市日期（最早 2013-05）
- IPO warmup：60 个交易日（新 ETF 不参与轮动）
- Z-Score lookback：60 天滚动窗口

## 5. 历史表现（参考，非未来保证）

| 口径 | CAGR | MaxDD | Sharpe | 年交易 |
|------|------|-------|--------|--------|
| N100 Guard-Z 10.6年（万1） | 15.2% | -29.7% | 0.905 | 3.8 |
| N100 Guard-Z 2023-04+（万1） | 33.2% | -17.7% | 1.770 | 6.1 |

## 6. 源项目

- 参考实现：https://github.com/xu75/ndx100etf-a-strategy-clowder
- 信号层：src/strategies.py → _fastre_signal() + fastre_rotation_zscore()
- 执行层：src/strategies.py → buyhold_rotation_zscore()
