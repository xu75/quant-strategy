---
feature_ids: []
topics: ["strategy", "research", "backtest"]
doc_kind: "strategy-research"
created: "YYYY-MM-DD"
strategy_id: "replace_me"
strategy_name: "Replace Me"
---

# [Strategy Name] 研究文档

## 1. 研究目标

- 这个策略要解决什么问题
- 不解决什么问题（边界）

## 2. 策略定义

- 信号定义
- 执行语义（next-bar / close-to-close / open-to-open）
- 风险控制与持仓约束

## 3. 数据与假设

- 数据源与覆盖时间
- 交易成本假设（默认双边 0.1%，除非明确另写）
- 是否含滑点、资金容量约束

## 4. 回测设置

- 参数空间
- 对照组
- 评价指标（收益、回撤、交易次数、夏普等）

## 5. 核心结果

- 主结果表（包含窗口与参数）
- 稳健性分析（跨周期/跨窗口）
- 与基线比较

## 6. 风险与限制

- 失效场景
- 统计偏差与数据偏差
- 工程风险（执行壳、API、状态一致性）

## 7. 生产语义对齐

- 研究模型与生产模型的差异
- 上线前额外验证项

## 8. 复现路径

- 脚本路径
- 命令
- 输出文件

## 9. 变更记录

- YYYY-MM-DD: 变更摘要
