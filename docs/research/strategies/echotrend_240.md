---
feature_ids: [F001]
topics: ["strategy", "research", "backtest", "MSTR", "BTC", "trend-following"]
doc_kind: "strategy-research"
created: "2026-05-04"
strategy_id: "echotrend_240"
strategy_name: "EchoTrend 240"
---

# EchoTrend 240: BTC 4H SMA240 Regime Gate 策略研究

## 1. 研究目标

- 利用 BTC 4H SMA240 趋势信号作为 regime gate，在牛市持有 MSTR、熊市清仓
- 验证跨资产趋势信号（BTC → MSTR）的有效性
- 不解决：日内择时、个股基本面分析、MSTR-BTC 相关性断裂场景

## 2. 策略定义

**信号定义：**
- BTC 1H 数据重采样为 4H → 计算 SMA240（240 根 4H bar = 40 天）
- Regime gate: close_4h > SMA240 → bull，close_4h < SMA240 → bear
- 确认机制: bull_confirm_bars=3, bear_confirm_bars=3（连续 3 根 4H bar 确认方向才切换）
- Shift(1): 信号在 4H bar 收盘后才可用，避免前视偏差

**执行语义：**
- MSTR 数据过滤至美股常规交易时段（US/Eastern 09:30-16:00），排除盘前盘后
- Regime 翻转后在下一根常规时段 bar 的 open 价执行（next-bar open）
- Bull regime → 买入 MSTR（全仓）
- Bear regime → 卖出 MSTR（清仓，bear_ceiling=0.0）
- 二元持仓（全仓/空仓），无仓位管理

## 3. 数据与假设

| 项目 | 值 |
|------|-----|
| BTC 数据源 | 本地 BTC-USD_1h.csv（OKX 归一化） |
| MSTR 数据源 | 本地 MSTR_1h.csv（过滤至常规交易时段） |
| 覆盖时间 | 2020-01-02 ~ 2026-05-01 |
| 交易成本 | 双边 0.2%（fee_rate=0.001/side） |
| 滑点 | 未单独建模（含在 fee_rate 中） |
| 资金容量 | 未约束（MSTR 日均成交量足够） |

## 4. 回测设置

**参数（已冻结）：**
- ma_window: 240（4H bars）
- bear_confirm_bars: 3
- bull_confirm_bars: 3
- bear_ceiling: 0.0
- bull_ceiling: 1.10
- hysteresis_pct: 0.0
- freeze_bars: 0

**执行约束：**
- MSTR 仅使用美股常规交易时段数据（US/Eastern 09:30-16:00）
- 信号执行使用 next-bar open 价格

**对照组：** Buy & Hold MSTR

**评价指标：** 总收益、最大回撤、Sharpe ratio、胜率、交易次数

## 5. 核心结果

### 5.1 全周期表现（2020-01 ~ 2026-05）

| 指标 | EchoTrend 240 | Buy & Hold |
|------|---------------|------------|
| 总收益 | +1575.3% | +1284.4% |
| 超额收益 | +290.8% | — |
| 最大回撤 | 80.0% | 89.6% |
| DD 改善 | +9.6pp | — |
| Sharpe | 1.025 | — |
| 交易次数 | 58 | — |
| 胜率 | 36.2% | — |
| 当前持仓 | 是（bull regime） | — |

### 5.2 Alpha 来源

核心 alpha 来自 BTC 4H SMA240 regime gate：
- 熊市期间完全退出 MSTR，避免深度回撤（策略 DD 80.0% vs B&H 89.6%）
- 牛市期间全仓持有，捕获 MSTR 对 BTC 的杠杆效应
- confirm_bars=3 过滤假突破，减少无效交易（58 trades over 5Y）
- 超额收益 +290.8%（+1575.3% vs B&H +1284.4%）

### 5.3 源项目鲁棒性验证（来自 mstr-strategy-clowder）

| 验证项 | 结果 |
|--------|------|
| 事件时间无前视偏差 | ✅ P0 修复 + 回归测试锁定 |
| 多窗口验证（1Y/2Y/3Y/5Y） | ✅ 四窗口全 PASS |
| 参数敏感性（RD-8） | ✅ 25/26 pass |
| Walk-forward Sharpe stability | ✅ 0.87 |
| 成本敏感性（1x/2x/5x） | ✅ 12/12 pass |

## 6. 风险与限制

- **MSTR-BTC 结构性风险**: 策略假设 MSTR 与 BTC 高度相关。若 MicroStrategy 改变 BTC 持仓策略或面临公司特有风险，相关性可能断裂
- **过拟合风险**: 参数在 2020-2026 数据上优化，未来市场结构可能变化
- **执行假设**: 回测使用 next-bar open 执行，实际交易可能有额外延迟
- **流动性**: MSTR 在非美股交易时段流动性较低，策略已限制为常规时段执行以缓解此风险

## 7. 生产语义对齐

| 项目 | 研究模型 | 生产模型 | 一致性 |
|------|----------|----------|--------|
| Regime gate | BTC 4H SMA240 + confirm_bars=3 | 同左 | ✅ |
| 信号延迟 | shift(1) | shift(1) | ✅ |
| 执行时机 | regime 翻转后下一根常规时段 bar open | 同左 | ✅ |
| 仓位管理 | 二元（全仓/空仓） | 同左 | ✅ |
| 交易成本 | fee_rate=0.001/side | commission=0.02% + slippage=0.03% | ≈ 一致 |
| 数据过滤 | 美股常规时段（US/Eastern 09:30-16:00） | 同左 | ✅ |

## 8. 复现路径

**源项目**: https://github.com/xu75/mstr-strategy-clowder

| 脚本 | 用途 |
|------|------|
| `sweep_ma_minihold.py` | RD-7: BTC 4H MA + miniHold |
| `sweep_rd8_robustness.py` | RD-8: 参数敏感性 + walk-forward |
| `sweep_window_compare.py` | 跨窗口对比（1Y/2Y/3Y/5Y） |

**本项目回测**:
```bash
python3 -c "
from pipeline.data_fetcher import load_local_history_by_name
from pipeline.backtest import run_backtest
from strategies.echotrend_240.signal import StrategyConfig, compute_signals, get_filtered_df
config = StrategyConfig()
mstr_df = load_local_history_by_name('MSTR_1h.csv', target_bar='1H')
btc_df = load_local_history_by_name('BTC-USD_1h.csv', target_bar='1H')
signals = compute_signals(mstr_df, config, btc_df=btc_df)
mstr_exec = get_filtered_df(mstr_df, config)
result = run_backtest(mstr_exec, config, signals=signals, fee_rate=0.001)
"
```

## 9. 变更记录

- 2026-05-04: 初始版本，从 mstr-strategy-clowder 交接上线 [宪宪/Opus-46🐾]
- 2026-05-04: GPT52 review 修正 — 更正回测数据（常规时段+next-open），移除 v6 仓位管理声明 [宪宪/Opus-46🐾]
