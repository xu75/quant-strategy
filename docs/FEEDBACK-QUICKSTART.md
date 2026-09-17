# GitHub Feedback 功能配置指南（快速版）

## 🚀 立即配置（5 分钟）

### 步骤 1：创建 GitHub Token

1. 访问：https://github.com/settings/tokens/new
2. 填写表单：
   - **Note**: `quant-strategy-feedback`
   - **Expiration**: `No expiration`（或选择一个较长期限）
   - **Scopes**: 勾选 `public_repo`（仅此一项即可）
3. 点击底部 **Generate token**
4. **立即复制** token（形如 `ghp_xxxxxxxxxxxx`）—— 离开页面后无法再看到

### 步骤 2：配置 Vercel 环境变量

1. 访问：https://vercel.com/xu75/quant-strategy/settings/environment-variables
2. 添加两个变量：

**第一个变量**：
```
Name:  GITHUB_TOKEN
Value: ghp_xxxxxxxxxxxx （刚才复制的 token）
Environments: ✓ Production ✓ Preview ✓ Development
```

**第二个变量**：
```
Name:  GITHUB_REPO
Value: xu75/quant-strategy
Environments: ✓ Production ✓ Preview ✓ Development
```

3. 点击 **Save**

### 步骤 3：重新部署

1. 进入 Vercel 项目 **Deployments** 页面
2. 点击最新部署右侧的 **⋯** 菜单
3. 选择 **Redeploy**
4. 确认 **Redeploy**

⏱️ 等待 1-2 分钟部署完成

### 步骤 4：测试反馈功能

1. 访问：https://quant-strategy.mesh-hub.xyz/feedback
2. 填写测试反馈：
   - Name: `Test User`（可选）
   - Email: `test@example.com`（可选）
   - Message: `Testing feedback system`
3. 点击 **Submit Feedback**
4. 成功后会显示 GitHub Issue 链接

### 步骤 5：验证 Issue 已创建

访问：https://github.com/xu75/quant-strategy/issues

应该看到一个新 Issue：
- 标题：`[Website Feedback] Testing feedback system`
- 标签：`feedback`, `from-website`

---

## ⚠️ 故障排查

### 提示 "Feedback service not configured"
- Token 未设置或变量名错误
- 解决：检查 Vercel 环境变量拼写，确保 `GITHUB_TOKEN` 完全正确

### 提示 "Failed to submit feedback"
- Token 权限不足
- 解决：重新生成 token，确保勾选了 `public_repo` scope

### Issue 未创建标签
- 仓库中不存在 `feedback` 或 `from-website` 标签
- 解决：手动创建这两个标签，或删除 `api/feedback.ts` 中的 `labels` 字段

---

## 📖 完整文档

更多细节请参考：`docs/FEEDBACK-SETUP.md`

包含：
- 本地开发配置
- API 接口文档
- 安全注意事项
- 维护指南
