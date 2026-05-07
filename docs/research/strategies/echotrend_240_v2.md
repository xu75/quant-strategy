---
feature_ids: [F001]
topics: ["strategy", "research", "backtest", "MSTR", "BTC", "trend-following", "regime-gate", "CAP1.0"]
doc_kind: "strategy-research"
created: "2026-05-07"
updated: "2026-05-07"
strategy_id: "echotrend_240_v2"
strategy_name: "EchoTrend 240 V2"
---

# EchoTrend 240 V2: BTC 4H SMA240 Regime Gate + V6 Execution Package

## 1. 研究目标

- 利用 BTC 4H SMA240 趋势信号作为 regime gate（核心 alpha 来源）
- CAP1.0 二元持仓：bull regime = 100% MSTR，bear regime = 0%
- V6 执行包（cooldown/EMA/reentry/step）优化交易节奏，不改变仓位大小
- 修正 V1 归因错误：V6 多因子评分不构成独立 alpha

## 2. 策略架构

**双层架构（v9 trend_regime variant）：**

- Layer T: BTC 4H regime gate（核心 alpha）— bear=0%, bull=100%
- Layer V6: Execution package（边际增强）— cooldown/EMA/reentry 优化节奏
- Final: regime flip → 立即全量执行（step=1.0），非 flip → V6 节奏控制

**CAP1.0 约束：**

| 参数 | 值 | 说明 |
|------|------|------|
| min_exposure | 1.00 | bull 时最低仓位 |
| max_exposure | 1.00 | 无杠杆 |
| mode_floors | all 1.00 | 禁止 V6 减仓 |
| mode_ceilings | all 1.00 | 禁止 V6 加仓 |
| bull_ceiling | 1.00 | regime gate bull 上限 |
| bear_ceiling | 0.00 | regime gate bear 上限 |

## 3. Alpha 归因

三组对比实验：

| 组别 | 描述 | Avg Excess | DD Improvement |
|------|------|-----------|----------------|
| A | 纯 BTC SMA240 趋势 | +98.5% | baseline |
| B (CAP1.0) | Regime gate + V6 无减仓 | +168.5% | +24.6pp |
| C | V6 含减仓 | < B | 无正贡献 |

结论：regime gate 是唯一核心 alpha，V6 execution 是边际增强，V6 bull reduction 无效。选择 B 组。

## 4. 验证结果

**4 窗口回测（双标准：excess ≥ 0 AND DD improvement > 0）：**

| 窗口 | Excess Return | DD Improvement | 结果 |
|------|--------------|----------------|------|
| 1Y | PASS | PASS | PASS |
| 2Y | PASS | PASS | PASS |
| 3Y | PASS | PASS | PASS |
| 5Y | PASS | PASS | PASS |

**稳健性：**
- RD-8 参数敏感性：25/26 pass
- Walk-forward Sharpe stability: 0.87
- 成本敏感性：1x/2x/5x 全部 pass

## 5. 执行语义

- 数据源：BTC 1H（OKX）重采样 4H 计算 SMA240，MSTR 1H 执行
- 时段：美股常规交易时段（US/Eastern 09:30-16:00）
- 信号延迟：shift(1) 避免前视偏差
- 执行：regime flip → next regular-hours bar open，全量执行
- 成本：commission 0.0005 + slippage 0.0005 = 0.1%/side（项目标准）

## 6. 与 V1 的区别

| 维度 | V1 | V2 |
|------|------|------|
| Alpha 归因 | V6 多因子 + regime gate | Regime gate only |
| 仓位管理 | 连续 70%–100% | 二元 100%/0% |
| V6 角色 | 核心评分 | 执行节奏优化 |
| 杠杆 | 可能 1.10x | CAP1.0 无杠杆 |
| 状态 | Retracted | Active |

## 7. 局限性

- MSTR-BTC 结构性风险：MicroStrategy 改变 BTC 持仓策略时相关性可能断裂
- 参数在 2020-2026 数据上优化，未来市场结构可能变化
- 回测使用 next-bar open 执行，实际交易可能有额外延迟或滑点
- V1 教训：多因子评分不构成独立 alpha，仅为执行优化

## 8. 源项目

- 源仓库：mstr-strategy-clowder
- 交接文档：quant-strategy-listing-v2.md
- 详细报告：mstr-strategy-research-report-v2.md
