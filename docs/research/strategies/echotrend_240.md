---
feature_ids: [F001]
topics: ["strategy", "research", "backtest", "MSTR", "BTC", "trend-following", "V6", "continuous-exposure"]
doc_kind: "strategy-research"
created: "2026-05-04"
updated: "2026-05-05"
strategy_id: "echotrend_240"
strategy_name: "EchoTrend 240"
---

# EchoTrend 240: BTC 4H SMA240 Regime Gate + V6 多因子仓位管理

## 1. 研究目标

- 利用 BTC 4H SMA240 趋势信号作为 regime gate，控制 MSTR 仓位上限
- V6 多因子评分系统实现连续仓位管理（70%–100%），替代二元全仓/空仓
- 验证跨资产趋势信号（BTC → MSTR）+ 多维度风控的有效性
- 不解决：日内择时、个股基本面分析、MSTR-BTC 相关性断裂场景

## 2. 策略架构

**双层架构（v9 trend_regime variant）：**

- Layer 0: V6 base target — 三维评分 → 连续仓位目标（0.70–1.00）
- Layer T: BTC 4H regime ceiling — bear=0.0, bull=1.00
- Final: `min(v6_target, regime_ceiling)`

**V6 三维评分系统：**

| 维度 | 因子数 | 范围 | 权重 |
|------|--------|------|------|
| 趋势得分 (trend) | 9 | 0–100 | 0.12 |
| 风险得分 (risk) | 9 | 0–100 | 0.40 |
| 相对强度 (RS) | 6 | 0–100 | 0.07 |

趋势因子: MSTR 1H/4H/5D/20D return, EMA20/50 cross, BTC 20D return, QQQ EMA20, QQQ 1H return
风险因子: MSTR/BTC/QQQ 负向 return, 日内波幅, RS 弱势
RS 因子: MSTR vs BTC 1H/4H spread, 20D ratio momentum

**四模式系统：**

| 模式 | 触发条件 | 仓位下限 | 仓位上限 | 确认 bars |
|------|----------|----------|----------|-----------|
| crash | risk ≥ 70 | 0.70 | 0.76 | 3 |
| risk_off | risk ≥ 45 | 0.72 | 0.88 | 3 |
| neutral | 默认 | 0.90 | 1.00 | 6 |
| bull | exposure ≥ 1.01 | 0.98 | 1.00 | 8 |

注: cap=1.0 下 bull 模式不可达（threshold 1.01 > max 1.00），实际运行在 crash/risk_off/neutral 三模式。

**执行语义：**
- EMA 平滑: alpha=0.30 对原始仓位目标做指数平滑
- 对称步进: 加仓步长 0.55, 减仓步长 0.50, crash 减仓 0.80
- 冷却期: 39 bars（约 6 个交易日）
- 快速重入: 5 条件投票（MSTR/BTC/QQQ 1H return + RS + VWAP），≥3 票通过则跳过冷却，步长 0.90
- 日内微调: dip buy (+0.03), breakout (+0.03), overheat (-0.03)
- Next-bar open 执行: 信号在当前 bar 产生，下一根 bar 的 open 价成交

**Regime Gate：**
- BTC 1H → 4H resample → SMA240（240 根 4H bar ≈ 40 天）
- bull: close_4h > SMA240, bear: close_4h < SMA240
- confirm_bars=3, shift(1) 防前视偏差
- Bear regime → ceiling=0.0 → 清仓
- Bull regime → ceiling=1.00 → V6 自由管理

## 3. 数据与假设

| 项目 | 值 |
|------|-----|
| MSTR 数据源 | 本地 MSTR_1h.csv（过滤至常规交易时段） |
| BTC 数据源 | 本地 BTC-USD_1h.csv（OKX 归一化） |
| QQQ 数据源 | 本地 QQQ_1h.csv + QQQ_5m.csv interval-covering |
| 日线数据 | MSTR_1d.csv, BTC-USD_1d.csv, QQQ_1d.csv |
| 覆盖时间 | 2020-01-02 ~ 2026-05-01 |
| 交易成本 | commission=0.02% + slippage=0.03%（分别建模） |
| 融资 | 不允许（max_exposure=1.00, max_margin_fraction=0.00） |
| 数据过滤 | 美股常规时段 US/Eastern 09:30-16:00 |

## 4. 回测设置

**冻结参数：**

| 参数 | 值 | 说明 |
|------|-----|------|
| ma_window | 240 | 4H bars, ≈40 天 |
| bear_confirm_bars | 3 | 熊市确认 |
| bull_confirm_bars | 3 | 牛市确认 |
| bear_ceiling | 0.0 | 熊市清仓 |
| bull_ceiling | 1.00 | 牛市满仓上限 |
| base_exposure | 1.00 | V6 基准仓位 |
| min_exposure | 0.70 | 最低仓位 |
| max_exposure | 1.00 | 最高仓位（无融资） |
| cooldown_bars | 39 | 冷却期 |
| target_ema_alpha | 0.30 | EMA 平滑系数 |

**Engine-native backtest**: 策略使用自有引擎（engine.py）运行回测，不经过平台二元 buy/sell 引擎。引擎输出 equity curve、exposure log、rebalance events，由 signal.py 转换为平台 BacktestResult。

**对照组：** Buy & Hold MSTR

## 5. 核心结果

### 5.1 全周期表现（2020-01 ~ 2026-05）

| 指标 | EchoTrend 240 | Buy & Hold |
|------|---------------|------------|
| 总收益 | +1520.36% | +1057.68% |
| 超额收益 | +462.68% | — |
| 最大回撤 | 62.47% | 89.56% |
| DD 改善 | +27.09pp | — |
| Sharpe | 1.080 | — |
| 交易次数 | 65 | — |
| 胜率 | 33.3% | — |
| 当前持仓 | 是（exposure 0.97, neutral, bull regime） | — |

### 5.2 分期表现

| 窗口 | 策略收益 | B&H 收益 | 最大回撤 | Sharpe |
|------|----------|----------|----------|--------|
| Since Launch | +10.47% | +15.89% | 9.50% | 4.485 |
| 1Y | -23.08% | -56.04% | 42.79% | -0.591 |
| 2Y | +59.13% | +52.05% | 58.65% | 0.715 |
| 3Y | +345.71% | +408.71% | 58.65% | 1.240 |
| 5Y | +428.26% | +152.01% | 58.65% | 0.884 |

### 5.3 Alpha 来源

1. **Regime gate 避险**: 熊市清仓避免 MSTR 深度回撤（策略 DD 62.5% vs B&H 89.6%）
2. **连续仓位管理**: V6 评分在牛市内动态调整 70%–100%，risk_off 时降仓而非清仓
3. **冷却期 + 重入投票**: 减少噪声交易，强信号时快速重入
4. **多数据源交叉验证**: MSTR + BTC + QQQ 三标的 + 日线趋势特征

### 5.4 源项目鲁棒性验证（来自 mstr-strategy-clowder）

| 验证项 | 结果 |
|--------|------|
| 事件时间无前视偏差 | ✅ shift(1) + 回归测试 |
| 多窗口验证（1Y/2Y/3Y/5Y） | ✅ 四窗口全 PASS |
| 参数敏感性（RD-8） | ✅ 25/26 pass |
| Walk-forward Sharpe stability | ✅ 0.87 |
| 成本敏感性（1x/2x/5x） | ✅ 12/12 pass |

## 6. 风险与限制

- **MSTR-BTC 结构性风险**: 策略假设 MSTR 与 BTC 高度相关。若 MicroStrategy 改变 BTC 持仓策略或面临公司特有风险，相关性可能断裂
- **回撤仍较大**: 62.5% max DD 虽优于 B&H 的 89.6%，但绝对值仍高，源于 MSTR 本身高波动
- **近 1 年负收益**: -23.08%（但优于 B&H 的 -56.04%），市场回调期策略仍承受损失
- **过拟合风险**: V6 参数在 2020-2026 数据上优化，未来市场结构可能变化
- **执行假设**: 回测使用 next-bar open 执行，实际交易可能有额外延迟
- **QQQ 数据覆盖**: QQQ 1H 从 2024-04 开始，更早历史由 5m resample 补足，可能有微小精度差异

## 7. 生产语义对齐

| 项目 | 研究模型 | 生产模型 | 一致性 |
|------|----------|----------|--------|
| Regime gate | BTC 4H SMA240 + confirm_bars=3 | 同左 | ✅ |
| 信号延迟 | shift(1) | shift(1) | ✅ |
| 执行时机 | next-bar open | 同左 | ✅ |
| 仓位管理 | V6 连续 (0.70–1.00) | 同左 | ✅ |
| 融资 | 不允许 (cap=1.0) | 同左 | ✅ |
| 交易成本 | commission=0.02% + slippage=0.03% | 同左 | ✅ |
| 数据过滤 | 美股常规时段 09:30-16:00 | 同左 | ✅ |
| 引擎 | engine-native (engine.py) | 同左 | ✅ |

## 8. 复现路径

**源项目**: https://github.com/xu75/mstr-strategy-clowder

**本项目回测**:
```bash
python3 -c "
from core.runner import run_single_strategy
from core.registry import discover_strategies, load_strategy_module
manifests = discover_strategies()
for m in manifests:
    if m.id == 'echotrend_240':
        adapter = load_strategy_module(m)
        run_single_strategy(adapter)
        break
"
```

**测试**:
```bash
python3 -m pytest tests/test_engine_echotrend.py tests/test_signal_echotrend.py -v
```

## 9. 变更记录

- 2026-05-04: 初始版本，从 mstr-strategy-clowder 交接上线（二元版本）[宪宪/Opus-46]
- 2026-05-04: V6 engine-native 重写 — 连续仓位管理、三维评分、四模式系统 [宪宪/Opus-46]
- 2026-05-04: Cap max_exposure=1.00（不允许融资）[宪宪/Opus-46]
- 2026-05-04: QQQ interval-covering fallback（5m→1h resample）[宪宪/Opus-46]
- 2026-05-05: 研究文档全面刷新至 V6 口径 [宪宪/Opus-46]
