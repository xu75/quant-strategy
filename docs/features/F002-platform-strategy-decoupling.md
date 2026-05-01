---
feature_ids: [F002]
related_features: [F001]
topics: [architecture, platform, strategy-plugin, decoupling, extensibility]
doc_kind: spec
created: 2026-05-01
status: spec
---

# F002 - 平台-策略解耦架构

> Status: spec | Owner: 宪宪/Opus-46

## Why

当前项目（F001）已完成 MVP：一个 BTC MA240 策略的完整管道。但代码中存在多处硬耦合：

1. `run_strategy.py` 硬编码了 BTC MA240 的配置、输出路径、launch date
2. 前端页面 `btc-ma240-4d.astro` 是手写的，每加一个策略需要手写一套页面
3. GitHub Actions workflow 只跑一个策略
4. 数据目录 `data/btc_ma240_4d/` 硬编码在多处

这意味着每新增一个策略（如 MSTR），都需要修改平台代码。随着策略数量增长，维护成本线性上升，且容易引入回归 bug。

**目标**：新增策略 = 新建目录 + 写 manifest + 实现信号接口，**零平台代码修改**。

## What

将项目重构为 **平台 + 策略插件** 架构：

- **平台层**：通用的数据管道、回测引擎、报告生成、前端渲染、CI/CD 编排
- **策略层**：每个策略是一个自包含的插件，通过标准接口与平台交互

### 目标目录结构

```
quant-strategy/
├── core/                        # 平台核心（新增，避免遮蔽 stdlib platform）
│   ├── __init__.py
│   ├── registry.py              # 策略注册表：扫描 + 验证
│   └── runner.py                # 通用编排：遍历策略 → fetch → backtest → report
├── pipeline/                    # 通用引擎（已有，微调接口）
│   ├── data_fetcher.py
│   ├── backtest.py              # 策略函数通过参数注入，禁止 import strategies.*
│   └── report.py                # 同上
├── strategies/                  # 策略插件目录
│   └── btc_ma_trend/            # 现有策略，迁移到新接口
│       ├── manifest.yaml        # 策略元数据（新增）
│       ├── signal.py            # 信号计算（已有，适配接口）
│       └── README.md
├── data/
│   └── btc_ma_trend/            # 按 strategy_id 组织（重命名）
│       ├── latest.json
│       ├── backtest.json
│       └── charts/
├── site/
│   ├── public/
│   │   └── charts/
│   │       └── btc_ma_trend/    # 按 strategy_id 命名空间隔离
│   │           ├── equity.png
│   │           └── price_ma.png
│   └── src/pages/
│       ├── index.astro          # 首页：自动列出所有策略
│       └── strategy/
│           └── [slug].astro     # 动态路由（替代硬编码页面）
└── run_strategy.py              # 瘦入口，委托给 core/runner.py
```

## 策略插件规范

### manifest.yaml

每个策略必须在其目录下提供 `manifest.yaml`：

```yaml
# strategies/btc_ma_trend/manifest.yaml
id: btc_ma_trend                    # 唯一标识，与目录名一致
name: "TrendLock 40"                # 显示名称
version: "1.0.0"
description: "BTC 4H MA240 趋势跟踪，最小持仓 4 天"

config:
  ma_window: 240
  min_hold_bars: 24
  timeframe: "4H"
  symbol: "BTC-USDT"
  data_source: "okx"                # 数据源标识

display:
  slug: "btc-ma240-4d"             # URL slug
  category: "trend-following"       # 策略分类
  short_desc: "BTC 均线趋势跟踪"    # 一句话描述

launch_date: "2026-04-30"          # 策略上线日期（用于 Since 计算）
enabled: true                       # false = 跳过运行和展示
```

### signal.py 标准接口

每个策略的 `signal.py` 必须实现以下接口：

```python
from dataclasses import dataclass
from typing import List
import pandas as pd

@dataclass
class StrategyConfig:
    """策略配置，字段由 manifest.yaml 的 config 节驱动"""
    ...

@dataclass
class Signal:
    timestamp: pd.Timestamp
    action: str          # "buy" | "sell"
    price: float
    reason: str

def compute_signals(df: pd.DataFrame, config: StrategyConfig) -> List[Signal]:
    """核心信号计算。输入标准 OHLCV DataFrame，输出信号序列。"""
    ...
```

**约束**：
- `compute_signals` 是纯函数：相同输入 → 相同输出，无副作用
- DataFrame 列名标准：`timestamp`, `open`, `high`, `low`, `close`, `volume`
- 返回的 Signal 列表按时间升序排列

### 数据输出约定

每个策略输出到 `data/{strategy_id}/`，标准文件：

| 文件 | 用途 | 生成者 |
|------|------|--------|
| `latest.json` | 当前信号状态 | `pipeline/report.py` |
| `backtest.json` | 回测结果 + 分期指标 | `pipeline/report.py` |
| `charts/equity.png` | 权益曲线图 | `pipeline/report.py` |
| `charts/price_ma.png` | 价格+指标+信号图 | `pipeline/report.py` |

**前端资源路径**：图表同步到 `site/public/charts/{strategy_id}/`，前端引用路径为 `/charts/{strategy_id}/equity.png` 等，避免多策略互相覆盖。

## 架构约束

### 依赖方向规则

```
core/ ──→ pipeline/ ──→ (无外部依赖)
  │                        ↑
  └──→ strategies/         │ 参数注入，非 import
       (signal.py) ────────┘
```

**硬约束**：`pipeline/` 禁止 import `strategies.*`。当前 `pipeline/backtest.py` 和 `pipeline/report.py` 直接 import BTC 策略的 `compute_signals` / `StrategyConfig` / `get_current_signal`，这些耦合必须在重构中消除。策略函数通过 `core/runner.py` 以参数注入方式传递给 pipeline。

CI 校验：增加测试用例，扫描 `pipeline/` 目录下所有 `.py` 文件，断言不存在 `from strategies` 或 `import strategies` 语句。

## 平台核心

### core/registry.py

```python
def discover_strategies(strategies_dir: Path) -> List[StrategyManifest]:
    """扫描 strategies/*/manifest.yaml，返回所有 enabled=true 的策略清单"""

def load_strategy_module(manifest: StrategyManifest) -> StrategyModule:
    """动态加载策略的 signal.py，验证接口合规"""

def validate_manifest(manifest_path: Path) -> StrategyManifest:
    """校验 manifest.yaml 必填字段和格式"""
```

### core/runner.py

```python
def run_single_strategy(manifest, module, pipeline_fns):
    """运行单个策略：fetch → compute_signals → backtest → report"""
    df = pipeline_fns.fetch_data(manifest)
    signals = module.compute_signals(df, module.config)
    # 策略函数作为参数注入 pipeline，而非 pipeline 自行 import
    result = pipeline_fns.run_backtest(df, module.config, signals_fn=module.compute_signals)
    pipeline_fns.generate_reports(result, signals, manifest)

def run_all_strategies():
    """主编排：发现策略 → 逐个运行 → 输出报告"""
    strategies = discover_strategies(STRATEGIES_DIR)
    for manifest in strategies:
        module = load_strategy_module(manifest)
        run_single_strategy(manifest, module, pipeline_fns)
```

`run_strategy.py` 简化为：

```python
from core.runner import run_all_strategies
if __name__ == "__main__":
    run_all_strategies()
```

## 前端变更

### 动态路由

用 Astro 的 `getStaticPaths` 实现动态路由，替代硬编码页面：

```typescript
// site/src/pages/strategy/[slug].astro
export async function getStaticPaths() {
  // 读取所有策略的 manifest + data，生成路由
  const strategies = await loadAllStrategies();
  return strategies.map(s => ({
    params: { slug: s.display.slug },
    props: { strategy: s }
  }));
}
```

### 首页策略列表

首页自动从策略数据生成策略卡片列表，不再硬编码。

### 回测页面

同样使用动态路由 `site/src/pages/backtest/[slug].astro`。

## CI/CD 变更

GitHub Actions workflow 改为：

```yaml
- name: Run all strategies
  run: python run_strategy.py
  # runner.py 内部遍历所有 enabled 策略
```

无需为每个策略单独配置 workflow step。

## 未来扩展点（不在本期范围）

以下能力在架构上预留接口，但不实现：

1. **个性化与通知**：用户注册后可获得信号推送、自定义 watchlist 等增值体验（不涉及内容可见性分级，所有策略数据保持公开，与 F001 愿景一致）
2. **策略独立调度**：manifest 可增加 `schedule` 字段，CI 按策略各自的 cron 运行
3. **多数据源**：`data_source` 字段已预留，可扩展 Binance、Yahoo Finance 等
4. **策略版本管理**：manifest 已有 `version` 字段，未来可支持多版本并行

## 迁移计划

从当前状态到目标架构的步骤：

| 步骤 | 内容 | 影响范围 |
|------|------|---------|
| M1 | 创建 `core/` 目录，实现 registry + runner | 新增文件 |
| M2 | 为 btc_ma_trend 编写 `manifest.yaml` | 新增文件 |
| M3 | 适配 `signal.py` 接口（如需） | 策略代码 |
| M4 | 消除 `pipeline/` 对 `strategies/` 的直接 import，改为参数注入 | pipeline 代码 |
| M5 | 重构 `run_strategy.py` 委托给 runner | 修改入口 |
| M6 | 数据目录重命名 `btc_ma240_4d` → `btc_ma_trend`，public charts 路径改为 `charts/{strategy_id}/` | 数据层 + 前端 |
| M7 | 前端改为动态路由 | site/ |
| M8 | 更新 GitHub Actions | CI/CD |
| M9 | 验证：端到端运行 + 网站正常 | 全链路 |

**原则**：每步可独立验证，不破坏现有功能。M1-M4 可以先做，M5-M7 可以后续跟进。

## Acceptance Criteria

- [ ] AC-1: `core/registry.py` 能自动发现 `strategies/` 下所有 enabled 策略
- [ ] AC-2: `core/runner.py` 能遍历运行所有策略，输出到各自的 `data/{id}/`
- [ ] AC-3: 新增策略只需创建目录 + manifest + signal.py，不修改平台代码
- [ ] AC-4: `pipeline/` 目录下所有 `.py` 文件不存在 `from strategies` 或 `import strategies` 语句（CI 自动校验）
- [ ] AC-5: 前端使用动态路由，自动为所有策略生成页面
- [ ] AC-6: 前端图表路径为 `/charts/{strategy_id}/equity.png` 等，多策略不冲突
- [ ] AC-7: GitHub Actions 无需为新策略修改 workflow
- [ ] AC-8: 现有 BTC MA240 策略迁移后功能不变，网站正常展示
- [ ] AC-9: `pytest` 全部通过

## Risk

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| 动态加载策略模块的安全性 | 低 | 策略代码在同一 repo，受 code review 保护 |
| Astro 动态路由 build 时间随策略数增长 | 低 | 短期策略数 <10，不构成问题 |
| 数据目录重命名导致 Vercel 部署中断 | 中 | M5 步骤需要同步更新前端引用，一次性完成 |
| manifest.yaml 格式变更的向后兼容 | 低 | 加 schema version 字段，做向后兼容校验 |

## Open Questions

1. ~~注册机制~~ → 铲屎官决定暂缓，架构预留扩展点即可
2. ~~数据目录是否从 `btc_ma240_4d` 重命名为 `btc_ma_trend`？~~ → 确认重命名，与 strategy_id 一致，同步处理 public chart 路径为 `charts/{strategy_id}/`
3. ~~策略的图表生成是否也应该由策略自定义？~~ → 确认由平台统一生成，策略只负责信号计算，不参与图表生成逻辑
