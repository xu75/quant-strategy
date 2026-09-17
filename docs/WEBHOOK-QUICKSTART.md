# Webhook 安全迁移 - 快速执行指南

## ✅ 代码部分已完成

以下文件已修改：
- `.github/workflows/run_strategy.yml` - 添加 `WEBHOOK_SUBSCRIBERS_JSON` 环境变量
- `scripts/telegram_notify.py` - 支持从环境变量读取配置
- `scripts/test_webhook_config.py` - 新增配置测试工具

---

## 🚀 你需要执行的 3 个步骤（10 分钟）

### 步骤 1：添加 GitHub Secret（5 分钟）

1. **访问**：https://github.com/xu75/quant-strategy/settings/secrets/actions

2. **点击** "New repository secret"

3. **填写**：
   - **Name**: `WEBHOOK_SUBSCRIBERS_JSON`
   - **Value**: 复制下面的 JSON（压缩成一行）

```json
[{"id":"xu_mate80","url":"https://api.chuckfang.com/73422055/","format":"json","created_at":"2026-05-11"}]
```

4. **点击** "Add secret"

---

### 步骤 2：合并并测试（3 分钟）

1. **Review 并合并当前 PR**（包含安全改动）

2. **手动触发 GitHub Actions 测试**：
   - 访问：https://github.com/xu75/quant-strategy/actions/workflows/run_strategy.yml
   - 点击 "Run workflow" → 选择 `daily-signal` 模式
   - 查看日志，确认：
     - ✅ 出现 "Active channels: Webhooks(1)"
     - ✅ 无 "No channels configured" 错误

3. **验证 webhook 收到通知**（如果有信号变化）

---

### 步骤 3：清理 Git 历史（可选，2 分钟）

⚠️ **这会重写 Git 历史，需要 force push**

如果你担心历史中的 webhook URL 已泄露，执行以下命令：

```bash
# 1. 备份仓库（重要！）
cd /Users/xujinsong/VSCode/SynologyDrive
cp -r quant-strategy quant-strategy-backup-$(date +%Y%m%d)

# 2. 安装清理工具（二选一）

## 方案 A: git-filter-repo（推荐）
pip install git-filter-repo

## 方案 B: BFG（更快）
brew install bfg

# 3. 进入项目
cd quant-strategy

# 4. 执行清理

## 使用 git-filter-repo
git filter-repo --path config/subscribers.json --invert-paths
git remote add origin https://github.com/xu75/quant-strategy.git

## 或使用 BFG
bfg --delete-files subscribers.json
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 5. 强制推送（会重写所有 commit SHA）
git push origin --force --all
git push origin --force --tags
```

#### 验证历史清理成功

```bash
# 应该返回空（说明文件已从历史中删除）
git log --all --full-history -- config/subscribers.json
```

---

## 📋 快速检查清单

完成后，确认以下内容：

- [ ] **GitHub Secret 已添加**：`WEBHOOK_SUBSCRIBERS_JSON` 存在
- [ ] **Actions 测试通过**：日志显示 "Active channels: Webhooks(1)"
- [ ] **Webhook 正常工作**：收到测试通知（如有信号变化）
- [ ] **本地 `config/subscribers.json` 保留**：用于本地开发测试
- [ ] **（可选）Git 历史已清理**：`git log` 无 subscribers.json

---

## 🧪 本地测试

### 测试环境变量模式（模拟 GitHub Actions）

```bash
# 设置环境变量
export WEBHOOK_SUBSCRIBERS_JSON='[{"id":"test","url":"https://webhook.site/your-test-url","format":"json"}]'

# 运行配置测试
python3 scripts/test_webhook_config.py

# 运行通知脚本
python3 scripts/telegram_notify.py

# 清除环境变量
unset WEBHOOK_SUBSCRIBERS_JSON
```

### 测试本地文件模式（开发环境）

```bash
# 确保 config/subscribers.json 存在
ls -la config/subscribers.json

# 运行配置测试
python3 scripts/test_webhook_config.py

# 应该显示 "Source: Local file"
```

---

## 🔄 日常维护

### 添加新的 webhook 订阅者

1. 访问：https://github.com/xu75/quant-strategy/settings/secrets/actions
2. 点击 `WEBHOOK_SUBSCRIBERS_JSON` → "Update"
3. 编辑 JSON，添加新订阅者：

```json
[
  {"id":"xu_mate80","url":"https://api.chuckfang.com/73422055/","format":"json"},
  {"id":"new-webhook","url":"https://example.com/webhook","format":"discord"}
]
```

4. 点击 "Update secret"
5. ✅ 无需 commit，下次 Actions 运行时自动生效

### 支持的格式

- `json` - 默认格式，发送 JSON payload
- `discord` - Discord webhook 格式
- `text` - 纯文本格式
- `bark` - Bark iOS 推送格式

---

## ❓ 故障排查

### Actions 日志显示 "No channels configured"

**原因**：Secret 未设置或名称错误

**解决**：
1. 检查 Secret 名称是否为 `WEBHOOK_SUBSCRIBERS_JSON`（区分大小写）
2. 确认 Secret 已添加到正确的仓库

### Webhook 未收到通知

**原因 1**：没有信号变化

**解决**：正常现象。只有当策略信号变化时才会发送通知。

**原因 2**：Webhook URL 错误

**解决**：
1. 使用 webhook.site 生成测试 URL
2. 运行本地测试：`export WEBHOOK_SUBSCRIBERS_JSON='[{"id":"test","url":"https://webhook.site/YOUR-UNIQUE-ID"}]'`
3. 运行 `python3 scripts/telegram_notify.py`

### 本地开发时无法加载配置

**原因**：`config/subscribers.json` 不存在

**解决**：
1. 从 GitHub Secret 复制内容
2. 创建本地文件：`echo '[{"id":"test","url":"..."}]' > config/subscribers.json`
3. ✅ 文件已在 `.gitignore`，不会被提交

---

## 🔐 安全最佳实践

### ✅ 已实现的安全措施

1. **环境变量优先**：GitHub Actions 使用加密的 Secrets
2. **本地文件隔离**：`.gitignore` 阻止 `config/subscribers.json` 被提交
3. **错误处理**：无效 JSON 不会导致脚本崩溃
4. **日志脱敏**：测试工具自动遮蔽长 URL

### ⚠️ 注意事项

1. **不要在日志中打印完整 webhook URL**（GitHub 会自动打码 Secret，但谨慎为好）
2. **不要将 Secret 硬编码到代码**（使用环境变量）
3. **定期轮换 webhook URL**（如果服务支持）
4. **限制 webhook 访问源**（如果服务支持白名单，添加 GitHub Actions IP）

---

## 📚 相关文档

- [完整迁移指南](WEBHOOK-SECURITY-MIGRATION.md) - 详细技术说明
- [GitHub Encrypted Secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [配置测试工具](../scripts/test_webhook_config.py)

---

**创建于**: 2026-09-18  
**状态**: ✅ Ready to Execute  
**预计时间**: 10 分钟（不含历史清理）
