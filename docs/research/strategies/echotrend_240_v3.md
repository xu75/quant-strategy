---
feature_ids: [F001]
topics: ["strategy", "research", "backtest", "MSTR", "BTC", "trend-following", "regime-gate", "CAP1.0", "decision-row", "circuit-breaker"]
doc_kind: "strategy-research"
created: "2026-05-23"
updated: "2026-05-23"
strategy_id: "echotrend_240_v3"
strategy_name: "EchoTrend 240 V3"
---

# EchoTrend 240 V3: Decision-Row Execution + Circuit Breaker + Crash Confirm

## 1. 研究目标

- 在 V2 基础上升级执行模型：从 pending order / next-bar open 改为 decision_row / current-bar open
- 新增 Circuit Breaker（RTH 日内 -10% 熔断）和 Crash Mode 1-bar 确认（step_down=0.80）
- 非对称确认：bear=3 bars, bull=9 bars（MSTR 1H 计数）
- 仓位 sizing 改为 current equity（非 fixed initial_shares）
- 保持 CAP1.0 二元持仓不变

## 2. 策略架构

**执行模型变更（V2→V3）：**

| 维度 | V2 | V3 |
|------|------|------|
| 执行语义 | pending order → next-bar open | decision_row → current-bar open |
| BTC 信号可用性 | shift(1) 后 next bar 使用 | shift(1)+ffill，当前 bar open 已知 |
| Confirm bars | bear=3, bull=3 | bear=3, bull=9 |
| Cooldown | 39 bars | 0（移除） |
| Circuit Breaker | 无 | RTH intraday -10% |
| Crash confirm | 3 bars | 1 bar (回测验证, step_down upgraded) |
| Step down (crash) | 0.50 | 0.80 |
| Strong reentry | 3/5 votes → 0.90 | 3/5 votes → 0.90（阈值更新） |
| Position sizing | fixed initial_shares | current equity |

**Crash Score 阈值表：**

| 条件 | 分数 |
|------|------|
| MSTR 1H return < -3.5% | 15 |
| MSTR 4H return < -6.0% | 15 |
| MSTR 5D return < -10.0% | 15 |
| BTC 1H return < -2.0% | 10 |
| BTC 5D return < -6.0% | 10 |
| QQQ 1H return < -0.8% | 10 |
| QQQ 5D return < -3.0% | 10 |
| MSTR/BTC 4H RS < -4.0% | 10 |
| 当日振幅 > 9% 且 close < VWAP | 5 |

总分 >= 70 即确认（1-bar confirm，回测验证优于 3-bar）→ crash mode（step_down=0.80）。

**Strong Reentry 阈值表：**

| 条件 | 票 |
|------|------|
| MSTR 1H return >= +3.5% | 1 |
| BTC 1H return >= +1.0% | 1 |
| QQQ 1H return >= +0.3% | 1 |
| MSTR/BTC 1H RS >= +1.0% | 1 |
| MSTR close >= VWAP | 1 |

>= 3 票 → step_up = 0.90。

## 3. 归因实验

**E 组（min=1.0, max=1.10）：** avg +398.7%，worst DD -71.9%
**F 组（min=0.70, max=1.0）：** avg +329.7%，worst DD -70.2%

结论：微杠杆是 C 组增量来源（91%），bull 内减仓是负贡献（-17.8pp）。V3 保持 CAP1.0。

## 4. Reference 数据（源项目低成本口径）

以下为源项目 commission 0.02% + slippage 0.03% 口径，非 quant-strategy 上线结果：

| 窗口 | B&H 收益 | 策略收益 | 超额 | DD 改善 | Worst DD |
|------|---------|---------|------|---------|----------|
| 1Y | -54.6% | -3.8% | +50.8pp | +40.1pp | -36.5% |
| 2Y | +35.9% | +131.2% | +95.3pp | +33.7pp | -46.5% |
| 3Y | +1072.9% | +1593.6% | +520.7pp | +23.2pp | -57.0% |
| 5Y | +305.7% | +1028.9% | +723.2pp | +19.1pp | -70.5% |

V3 最终展示数据由 quant-strategy 项目标准交易成本 full-backtest 生成。

## 5. 执行语义

- 数据源：BTC 1H（OKX）重采样 4H 计算 SMA240，MSTR 1H 执行
- 时段：美股常规交易时段（US/Eastern 09:30-16:00）
- 信号延迟：shift(1)+ffill，当前 bar open 时 BTC 4H 信号已知
- 执行：decision_row — 当前 bar open 价立即执行
- 成本：commission 0.05% + slippage 0.05% = 0.1%/side（项目标准）
- CAP1.0：目标仓位二元（bull=100%, bear=0%），无杠杆，执行按 gap*step 分步过渡

## 6. 局限性

- MSTR-BTC 结构性风险：若 MicroStrategy 改变 BTC 持仓策略，相关性可能断裂
- 参数在 2020-2026 数据上优化，未来市场结构可能变化
- Circuit breaker -10% 阈值基于历史波动率，极端行情可能不足
- Crash mode 1-bar confirm（回测验证优于 V2 的 3-bar），step_down 提升至 0.80 加速卖出

## 7. 源项目

- 生产规格：mstr-strategy-clowder/docs/futu-backtest-strategy-spec-v3.md
- 归因数据：mstr-strategy-clowder/outputs/runs/20260522T075328Z_v6_attribution/v6_attribution.csv
- 策略源码：mstr-strategy-clowder/src/strategies.py（VolTargetDdCapV9Strategy, trend_regime variant）
