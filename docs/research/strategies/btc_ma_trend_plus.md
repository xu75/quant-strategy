---
feature_ids: []
topics: ["btc", "trend-following", "ma240", "trendlock40", "exit-confirm"]
doc_kind: "strategy-research"
created: "2026-05-04"
strategy_id: "btc_ma_trend_plus"
strategy_name: "TrendLock 40 Plus"
---

# TrendLock 40 Plus 出场确认优化研究

## 1. 研究目标

- 在 TrendLock 40（BTC 4H MA240 + min_hold=24）基础上，通过出场确认机制减少 MA 附近假跌破导致的错误退出
- 不改变入场逻辑（crossover 确认），仅优化出场条件
- 边界：不涉及双均线、止盈止损、ADX 过滤等复杂化改造

## 2. 策略定义

### 信号定义

- 入场（Buy）：与 TrendLock 40 完全一致。prev_close <= prev_MA240 AND close > MA240（crossover 确认）
- 入场过滤（斜率门，2026-07 生效）：金叉当根计算 slope = MA240(当前) / MA240(30 根 4H 之前) − 1；若 slope < −2% 则本次金叉不进场，等待下一次金叉。只在金叉当根判定一次，被挡后不做延迟补进。MA240 历史不足 30 根时正常放行。只影响开多入场，出场与持仓完全不受影响。详见第 5.1 节。
- 出场（Sell）：持仓满 min_hold_bars 后，要求连续 exit_confirm_bars 根 K 线收盘价 < MA240 才卖出

### 执行语义

- 信号在 bar N 收盘时产生，在 bar N+1 开盘执行（next-bar execution）
- 生产代码执行价格模型略有不同，详见第 7 节"生产语义对齐"

### 风险控制

- 最小持仓：12 根 4H K 线（2 天），较 TrendLock 40 的 24 根（4 天）缩短
- 出场确认：连续 2 根 K 线收盘 < MA 才触发卖出
- 无止损、无止盈、无加仓

### 关键设计决策

exit_confirm_bars 与 min_hold_bars 解决不同问题：

- min_hold_bars 是一次性门控，仅保护入场后初始阶段
- exit_confirm_bars 提供持续保护，在整个持仓期间过滤假跌破
- 当 exit_confirm 已提供防抖保护时，min_hold 可以缩短而不增加风险

## 3. 数据与假设

- 数据源：OKX BTC-USDT 4H K 线，通过 `sweep_btc_ma.load_btc_interval()` 加载
- 覆盖时间：2020-01-01 至 2026-04-30（6 年）
- 交易成本：双边 0.1%（fee_rate=0.001 per side）
- 无滑点模型（压力测试中单独评估）
- 无资金容量约束

## 4. 回测设置

### 参数空间

四组实验，共 42 个配置 × 3 个时间窗口 = 126 次回测：

1. Exit Confirm（最高优先级）：ecb ∈ {1, 2, 3, 5} × mh ∈ {12, 18, 24}
2. Exit Buffer：buffer ∈ {0.5%, 1.0%, 1.5%, 2.0%} × mh ∈ {12, 18, 24}
3. Entry Confirm：ecb ∈ {1, 2, 3} × mh ∈ {18, 24}
4. Symmetric Confirm：cb ∈ {1, 2, 3} × mh ∈ {12, 18, 24}

### 对照组

TrendLock 40 生产配置：ma_window=240, min_hold_bars=24, 无 exit_confirm

### 时间窗口

| 窗口 | 起止 | 用途 |
|------|------|------|
| 2y | 2024-01-01 ~ 2026-04-30 | 近期表现 |
| 3y | 2023-01-01 ~ 2026-04-30 | 中期稳健性 |
| 6y | 2020-01-01 ~ 2026-04-30 | 长期鲁棒性（含完整牛熊周期） |

### 评价指标

- 总收益率、最大回撤、交易次数
- 三窗口均值收益（Mean Return）作为主排序指标
- 跨窗口一致性作为反过拟合信号

## 5. 核心结果

### 主结果表

| 排名 | 配置 | 2y | 3y | 6y | 6y 回撤 | 6y 交易 | 均值 |
|------|------|-----|------|------|---------|---------|------|
| 1 | ecb=2, mh=12 | 73.94% | 298.94% | 2200.24% | -39.42% | 153 | 857.71% |
| 2 | ecb=2, mh=18 | 74.07% | 293.79% | 2141.88% | -40.59% | 147 | 836.58% |
| 3 | buffer=0.5%, mh=24 | 62.02% | 275.86% | 2114.90% | -37.52% | 143 | 817.59% |
| 4 | ecb=3, mh=12 | 73.13% | 294.16% | 2038.69% | -40.53% | 143 | 801.99% |
| 5 | sym=2, mh=12 | 87.51% | 311.96% | 1955.36% | -42.97% | 143 | 784.94% |
| — | **基线 mh=24** | **69.74%** | **279.34%** | **1852.15%** | **-43.10%** | **163** | **733.74%** |
| — | entry=3, mh=24 | 68.13% | 276.23% | 1727.68% | -48.23% | — | 690.68% |

### 推荐配置 vs 基线

| 指标 | 基线 (mh=24) | Plus (ecb=2, mh=12) | 变化 |
|------|-------------|---------------------|------|
| 2y 收益 | 69.74% | 73.94% | +4.2pp |
| 3y 收益 | 279.34% | 298.94% | +19.6pp |
| 6y 收益 | 1852.15% | 2200.24% | +348pp |
| 6y 回撤 | -43.10% | -39.42% | 改善 3.7pp |
| 6y 交易 | 163 | 153 | 减少 10 笔 |
| 均值收益 | 733.74% | 857.71% | +16.9% |

### 关键发现

1. **Exit Confirm (ecb=2) 是明确赢家**：三窗口全面优于基线，回撤改善，交易更少
2. **Exit Buffer (0.5%) 是次优选择**：回撤控制最好（-37.52%），但 2y 表现较弱
3. **Entry Confirm 损害表现**：确认研究论文发现——"过强的进场过滤会错过BTC主升浪"
4. **Symmetric Confirm 不如纯 Exit Confirm**：入场过滤抵消了出场改善的收益
5. **min_hold 在有 exit_confirm 时可以缩短**：ecb=2 已提供防抖保护，mh=12 优于 mh=24

### 跨窗口一致性（反过拟合）

ecb=2 mh=12 在所有三个窗口都优于基线，不存在"某个窗口好、其他窗口差"的过拟合特征。三窗口改善方向一致：收益提升 + 回撤降低 + 交易减少。

## 5.1 MA240 开多斜率门（2026-07 新增）

### 规则

金叉当根计算 `slope = MA240(当前) / MA240(30 根 4H 之前) − 1`；若 `slope < −2%` 本次金叉不进场，等待下一次金叉。只在金叉当根判定一次，被挡后不做延迟补进；MA240 历史不足 30 根时正常放行；只影响开多入场，出场与持仓不受影响。

阈值特意设为 −2%（而非 0）：轻微下行中的金叉仍放行，以保住"均线还在降、价格已反转"的 V 型周期底部行情（如 2019、2023 年初）。研究表明"斜率为负就禁止开多"的严格版会显著损害收益。

### 设计依据（trade-level 验证）

按金叉当根 MA240 斜率对基线（gate off）全部 80 笔交易分桶，验证脚本 `scripts/verify_slope_gate.py`：

| 入场斜率桶 | 交易数 | 胜率 | 净 PnL 合计 |
|-----------|--------|------|------------|
| slope < −2%（将被拦截） | 10 | 10.0% | −6.4% |
| slope ≥ −2%（放行） | 70 | 28.6% | +465.6% |

结论方向与设计假设一致：陡峭下行中的金叉是熊市反弹尾部，胜率极低、合计亏损；利润全部来自其余入场。

### Provenance 与口径差异（诚实标注）

设计文档援引的口径为"30 笔 slope < −2% 金叉、胜率 13%"。本仓库 canonical 数据（`data/market/BTC-USD_1h.csv`，2020-01-01 起）复算为 **10 笔成交入场、胜率 10.0%**（另有 18 根满足 slope < −2% 的金叉，但其中 8 根发生在持仓中、不产生新入场）。差异的最可能来源：

- **数据窗口不同**：本仓库数据从 2020 起，缺 2017–2019 熊市，而斜率门最易触发的正是深熊反弹尾部——原研究若含更早历史，steep-slope 金叉自然更多。
- **数据源不同**：manifest 声明 BTC-USDT/OKX，仓库 canonical 文件为 BTC-USD，早期价格与均线轨迹存在差异。

**方向性结论稳健**（steep-slope 金叉近乎无价值），但绝对笔数依赖数据窗口/源，跨源对比需对齐窗口后再引用（遵循 CLAUDE.md「Cross-strategy comparisons must use matching windows」）。

### 回测影响（同数据 / 同费率 0.1% 每边，数据窗口至 2026-07-15）

| 配置 | 交易 | 总收益 | 最大回撤 | 胜率 | Sharpe |
|------|------|--------|---------|------|--------|
| 基线（gate off） | 80 | 2071.15% | 41.88% | 26.25% | 1.344 |
| **forward-only（2026-07-01 生效，生产默认）** | 79 | 2133.61% | 41.88% | 26.58% | 1.354 |
| counterfactual（全历史应用） | 72 | 2350.38% | 36.33% | 29.17% | 1.444 |

- **生产采用 forward-only**：规则自 2026-07-01 起对新金叉生效，历史回测不被改写（无 lookahead）。截至 2026-07-15，2026-07-01 后共 2 根金叉、拦截 1 根（发生在 MA240 下行期），收益与胜率小幅改善。
- **counterfactual 仅供参考**：全历史应用是 in-sample、含过拟合成分，不作为生产宣称。它印证了方向（回撤 41.88%→36.33%、胜率 26.25%→29.17%），但收益提升幅度不应被当作前瞻性预期。

## 6. 风险与限制

### 失效场景

- BTC 市场结构发生根本变化（如波动率持续压缩至传统资产水平）
- 极端快速崩盘场景下，exit_confirm 的 2 根 K 线延迟（8 小时）可能增加损失
- 如果未来假跌破频率大幅降低，exit_confirm 的价值会减弱

### 统计偏差

- 参数通过历史回测选择，存在样本内优化风险
- 缓解措施：跨窗口一致性验证、分段表现验证、滑点压力测试
- 6 年样本包含完整牛熊周期，但仅覆盖 BTC 历史的一部分

### 备选配置

- **震荡行情版 (sym=2, mh=12)**：2y/3y 表现最好，适合"未来 BTC 更温和"的假设
- **研究上限版 (ecb=2, mh=0)**：理论最优但交易多（197），不建议直接上线

### 决策记录

- 两猫（opus + gpt52）独立评审后达成共识：推荐 ecb=2 mh=12
- 铲屎官确认（2026-05-04）

## 7. 生产语义对齐

### 研究模型 vs 生产代码

| 项目 | 研究（sweep_exit_optimization.py） | 生产（signal.py + pipeline/backtest.py） |
|------|-----------------------------------|----------------------------------------|
| 入场 | crossover: prev_close <= prev_ma AND close > ma | 相同 |
| 出场 | min_hold 后连续 ecb 根 close < ma | 相同 |
| 执行 | next-bar execution（信号在 bar N 收盘产生，bar N+1 开盘执行） | close execution（信号在 bar N 收盘产生，以 bar N 收盘价执行） |
| 费率 | 0.001 per side | 0.001 per side |
| MA 计算 | rolling(240).mean() | rolling(240).mean() |

### 执行语义差异

研究 sweep 和生产 pipeline 的执行价格模型不同：

- **研究（sweep）**：`pending_buy = True` → 下一根 K 线以 `opens[i]` 执行（next-bar open execution）
- **生产（pipeline）**：信号价格 = `close`，pipeline 直接以 `sig.price` 执行（same-bar close execution）

这导致绝对收益数字存在差异（6y sweep: 2200.24% vs pipeline: ~2119%），因为 open 和 close 价格不同。

### 为什么相对比较仍然有效

- 在 sweep 中，baseline 和 Plus 使用相同的 next-bar open 执行模型
- 在 pipeline 中，baseline 和 Plus 使用相同的 close 执行模型
- 因此 Plus vs baseline 的**相对优势**在两种模型下方向一致
- 分段验证和滑点压力测试均在同一执行模型内进行，结论不受影响

### 实现差异

- 研究脚本使用 numpy 数组遍历，生产代码使用 pandas DataFrame 遍历
- 入场/出场信号逻辑语义一致，仅执行价格取值点不同

## 8. 复现路径

### 脚本

- Sweep 脚本：`sweep_exit_optimization.py`
- 生产信号：`strategies/btc_ma_trend_plus/signal.py`
- 验证脚本：`scripts/validate_trendlock40_plus.py`
- 斜率门验证脚本：`scripts/verify_slope_gate.py`（三配置对比 + trade-level 分桶）

### 命令

```bash
# 运行四组 sweep
python3 sweep_exit_optimization.py

# 运行验证
python3 scripts/validate_trendlock40_plus.py

# 运行斜率门验证（baseline / forward-only / counterfactual）
python3 scripts/verify_slope_gate.py
```

### 输出文件

- `outputs/sweep_exit_optimization.csv`：完整 sweep 结果（126 行）
- `outputs/sweep_exit_optimization_report.md`：分析报告
- `outputs/validate_trendlock40_plus.txt`：验证结果

## 9. 变更记录

- 2026-05-04: 初始版本。基于四组 sweep 实验，选定 ecb=2 mh=12 作为 TrendLock 40 Plus 默认配置
- 2026-07-15: 新增 MA240 开多斜率门（第 5.1 节），2026-07-01 forward-only 生效。金叉当根 slope < −2% 拦截开多；只影响入场。trade-level 验证 steep-slope 桶 10 笔/胜率 10.0%/净 −6.4%。Provenance 口径差异见 5.1。验证脚本 `scripts/verify_slope_gate.py`
