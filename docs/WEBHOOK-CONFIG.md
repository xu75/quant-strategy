---
feature_ids: []
topics: [webhook, configuration, github-secrets, security]
doc_kind: guide
created: 2026-09-17
---

# Webhook 配置指南

## 概述

本项目支持通过环境变量配置 webhook，避免在代码仓库中存储敏感 URL。

## 配置方式

### GitHub Actions（生产环境）

1. **添加 Repository Secret**：
   - 访问：`https://github.com/YOUR_USERNAME/quant-strategy/settings/secrets/actions`
   - 点击 "New repository secret"
   - Name: `WEBHOOK_SUBSCRIBERS_JSON`
   - Value: JSON 数组格式（见下方示例）

2. **JSON 格式**：
```json
[
  {
    "id": "your_webhook_id",
    "url": "https://your-webhook-service.com/path/",
    "format": "json"
  }
]
```

3. **字段说明**：
   - `id`: 订阅者标识符（用于日志）
   - `url`: Webhook 接收端点（必须以 `http://` 或 `https://` 开头）
   - `format`: 消息格式，支持：`json` / `discord` / `text` / `bark`

### 本地开发

创建 `config/subscribers.json`（已在 `.gitignore` 中）：

```json
[
  {
    "id": "local_test",
    "url": "https://webhook.example.com/test/",
    "format": "json"
  }
]
```

⚠️ **注意**：此文件仅用于本地测试，永远不要提交到 Git。

## 优先级

1. **环境变量** `WEBHOOK_SUBSCRIBERS_JSON`（生产优先）
2. **本地文件** `config/subscribers.json`（开发/测试）
3. **无配置**：返回空数组，webhook 功能禁用

## 测试配置

```bash
# 设置测试用环境变量
export WEBHOOK_SUBSCRIBERS_JSON='[{"id":"test","url":"https://example.com/","format":"json"}]'

# 验证配置格式（不发送消息）
python3 -c "
import sys
sys.path.insert(0, '.')
from scripts.telegram_notify import load_subscribers
subs = load_subscribers()
print(f'✓ Configuration valid: {len(subs)} subscriber(s)')
for i, sub in enumerate(subs):
    print(f'  [{i}] {sub[\"id\"]}: {sub[\"url\"]} ({sub[\"format\"]})')
"

# 实际触发通知（会检测信号变化并发送真实消息）
python scripts/telegram_notify.py
```

**注意**：第二个命令会发送真实通知如果检测到信号变化。仅在测试环境或准备接收通知时运行。

## 安全建议

- ✅ 使用 GitHub Secrets 存储生产 webhook URL
- ✅ 定期轮换 webhook URL
- ✅ 监控 webhook 端点的异常请求
- ❌ 永远不要在代码、文档或日志中硬编码 webhook URL
