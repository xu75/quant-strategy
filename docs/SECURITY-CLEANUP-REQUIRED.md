# 🚨 安全清理记录

## 问题
在将仓库设为 public 之前，`config/subscribers.json` 已被提交到 Git 历史中，包含敏感的 webhook URL。

## 已泄露的信息
- **文件**: `config/subscribers.json`
- **内容**: Webhook URL（已从当前文档和 HEAD 中移除）
- **首次提交**: commit `78be093721f8c7d5e58dc0849eb6a0dc5bdf73fb` (2026-05-11)
- **可见范围**: 整个公开仓库的历史记录中仍可访问

## 已完成的措施（2026-09-17）
✅ 将 `config/subscribers.json` 添加到 `.gitignore`
✅ 从 Git 追踪中移除该文件（`git rm --cached`）
✅ 从当前文档中移除明文 webhook URL
✅ 迁移到 GitHub Secrets（`WEBHOOK_SUBSCRIBERS_JSON`）
✅ 添加配置校验（URL/format schema）

## Owner 决策（2026-09-18）

**不执行以下操作**（已确认接受风险）：
- ❌ 不轮换泄露的 webhook URL
- ❌ 不重写 Git 历史（不使用 BFG/git-filter-repo）

**理由**：
- Webhook endpoint 为内部测试用途，无生产数据
- 重写历史会影响协作者和已 fork 的仓库
- 当前 HEAD 已清理，新配置使用 GitHub Secrets

**接受的风险**：
- ⚠️ 历史 commit 中的 webhook URL 仍然公开可访问
- ⚠️ 任何人都可以通过 `git log --all --full-history -- config/subscribers.json` 查看
- ⚠️ 如果这些 webhook 未来被用于生产环境，必须先轮换

## 未来防护措施

### 已实施
- ✅ `.gitignore` 现在包含 `config/subscribers.json`

### 建议额外措施
1. **使用 git-secrets 或 pre-commit hooks**
   ```bash
   pip install pre-commit
   # 添加 pre-commit hook 扫描敏感信息
   ```

2. **敏感配置文件命名规范**
   - 所有包含 URL/token/key 的文件使用 `*.secret.*` 或 `*.private.*` 后缀
   - 在 `.gitignore` 中添加通配符规则

3. **定期审计**
   ```bash
   # 检查是否有新的敏感文件被追踪
   git ls-files | grep -iE "(secret|token|webhook|password|private|credential|key)"
   ```

## 时间线
- 2026-05-11: 文件首次提交
- 2026-09-17: 仓库变为 public，问题被发现
- 2026-09-17: 临时措施完成（文件停止追踪）
- **待执行**: 彻底清理历史 + 吊销泄露的 webhook

---
**创建于**: 2026-09-17  
**状态**: ⚠️ URGENT - 等待执行  
**负责人**: @xu75
