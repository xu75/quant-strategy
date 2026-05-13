---
feature_ids: []
topics: ["strategy", "research", "backtest", "momentum", "rotation"]
doc_kind: "strategy-research"
created: "2026-05-13"
strategy_id: "dualmom_b_fastre"
strategy_name: "DualMom-B FastRe (QQQ/SPY/CASH)"
---

# DualMom-B FastRe 研究文档

## 1. 研究目标

- 在 DualMom-B 月频轮动基础上，解决空仓期回场滞后问题（月频最多等 1 个月）
- 不改变出场逻辑（保留月频出场的 whipsaw 保护能力）

## 2. 策略定义

- 出场信号（月频）：月末 QQQ 收盘 ≤ SMA225 → 次月初转 CASH
- 回场信号（周频）：空仓期每周末检查 QQQ > SMA225 且 SPY 12m > 0 → 次交易日回场
- 资产选择：QQQ 12m > SPY 12m → QQQ，否则 → SPY
- 执行语义：Next Open（信号日收盘生成，次日开盘执行）

## 3. 数据与假设

- 数据源：yfinance（QQQ、SPY 日线，auto_adjust=True），1999-03-10 至 2026-05-11
- 交易成本：0.1%/side（保守假设，含佣金及滑点），路径级扣费
- 无资金容量约束（ETF 流动性充足）

## 4. 回测设置

- 参数：SMA_WINDOW=225, MOMENTUM_LOOKBACK=12m, reentry_freq="W", require_abs_momentum=True
- 对照组：Buy & Hold QQQ, DualMom-B（月频）
- 评价指标：CAGR, MaxDD, Sharpe, Calmar, 交易次数, 持仓占比

## 5. 核心结果

含 0.1%/side 路径级扣费：

| 窗口 | CAGR | MaxDD | Sharpe | Calmar |
|------|------|-------|--------|--------|
| 全样本 1999–2026 | 10.22% | -32.28% | 0.65 | 0.32 |
| 2014+ | 14.07% | -28.56% | 0.83 | 0.49 |
| 2020+ | 16.94% | -13.56% | 1.13 | 1.25 |
| 2022+ | 22.50% | -13.56% | 1.38 | 1.66 |

对照（含费）：DualMom-B CAGR 8.69%, MaxDD -32.28%; B&H QQQ CAGR 10.90%, MaxDD -82.96%

## 6. 风险与限制

- 改善集中在"短暂回调后快速反弹"场景；缓慢阴跌型熊市无额外收益
- SPY 12m > 0 确认过滤了 2001/2002/2008/2022 熊市假突破
- A 股 QDII ETF 年度跟踪误差 ±5–9%（汇率 + 溢价）
- 策略选择偏差：从多个候选中选出最优方案

## 7. 生产语义对齐

- 已接入 GitHub Actions pipeline，每 4 小时自动生成信号与回测数据
- A 股执行映射：513100（QQQ）/ 513500（SPY）/ 511880（CASH）
- 执行频率：月末 1 次 + 空仓期每周 1 次

## 8. 复现路径

- 源项目：`../backtest/ndx100etf-a-strategy-clowder/`
- 信号函数：`src/backtest_qqq_relstrength.py::dual_momentum_riskon_b_fast_reentry_signal()`
- 回测函数：`src/backtest_qqq_relstrength.py::backtest_dual_mom_b_fast_reentry()`
- 数据：`../history_data/normalized/QQQ_1d.csv`, `SPY_1d.csv`
- 完整研究报告：`docs/research_report_v1.1_20260513.md`

## 9. 变更记录

- 2026-05-13: 初始版本，基于 v1.1 研究报告创建
