# 🚨 安全清理待办 (URGENT)

## 问题
在将仓库设为 public 之前，`config/subscribers.json` 已被提交到 Git 历史中，包含敏感的 webhook URL。

## 已泄露的信息
- **文件**: `config/subscribers.json`
- **内容**: Webhook URL `https://api.chuckfang.com/73422055/`
- **首次提交**: commit `78be093721f8c7d5e58dc0849eb6a0dc5bdf73fb` (2026-05-11)
- **可见范围**: 整个公开仓库的历史记录

## 已完成的临时措施
✅ 将 `config/subscribers.json` 添加到 `.gitignore`  
✅ 从 Git 追踪中移除该文件（`git rm --cached`）  
⚠️ **但历史记录中仍然存在**

## 必须立即执行的操作

### 1. 吊销泄露的 Webhook（最高优先级）
```
旧 URL: https://api.chuckfang.com/73422055/
行动: 在 chuckfang.com 管理后台重新生成新的 webhook URL
```

### 2. 从 Git 历史中彻底删除敏感文件

#### 方案 A: 使用 BFG Repo-Cleaner（推荐）
```bash
# 1. 安装 BFG
brew install bfg  # macOS
# 或从 https://rtyley.github.io/bfg-repo-cleaner/ 下载

# 2. 备份仓库
cd /path/to/quant-strategy
git clone --mirror . ../quant-strategy-backup.git

# 3. 删除敏感文件
bfg --delete-files subscribers.json .

# 4. 清理引用和垃圾回收
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 5. 强制推送（会重写历史）
git push origin --force --all
git push origin --force --tags
```

#### 方案 B: 使用 git-filter-repo（更彻底）
```bash
# 1. 安装
pip install git-filter-repo

# 2. 备份
cp -r . ../quant-strategy-backup

# 3. 删除文件及其所有历史
git filter-repo --path config/subscribers.json --invert-paths

# 4. 强制推送
git push origin --force --all
git push origin --force --tags
```

### 3. 验证清理结果
```bash
# 检查文件是否还在历史中
git log --all --full-history -- config/subscribers.json

# 应该返回空，说明清理成功
```

### 4. 通知协作者
如果有其他人 clone 了这个仓库，他们需要：
```bash
# 删除本地仓库
rm -rf quant-strategy

# 重新 clone
git clone https://github.com/xu75/quant-strategy.git
```

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
