# Webhook 安全迁移方案

## 问题诊断

### 当前风险
1. ⚠️ `config/subscribers.json` 包含敏感 webhook URL
2. ⚠️ 已泄露到 Git 公开历史（commit `78be093`）
3. ⚠️ GitHub Actions 需要读取该文件才能发送通知

### 为什么这是问题？
- Webhook URL 相当于 API token（任何知道 URL 的人都能伪造通知）
- 公开仓库 = 全世界可见
- Git 历史清理需要 force push（影响所有协作者）

---

## 🎯 推荐方案：GitHub Encrypted Secrets

### 架构设计

```
GitHub Actions (运行时)
  ↓ 读取加密的环境变量
GitHub Secrets (仅 repo owner 可见)
  ↓ 存储
WEBHOOK_SUBSCRIBERS_JSON (JSON 字符串)
```

### 优势
✅ **零泄露风险**：Secrets 在 Actions 日志中自动打码  
✅ **无需 private repo**：一个 public repo 即可  
✅ **易于更新**：在 GitHub UI 修改，无需 commit  
✅ **Actions 原生支持**：无需额外认证  
✅ **审计友好**：Secret 更新有日志  

---

## 🚀 实施步骤

### 第 1 步：备份当前 webhook 配置

```bash
# 1. 复制当前配置（用于后续设置 Secret）
cat config/subscribers.json
```

**重要**：复制输出内容，稍后粘贴到 GitHub Secrets。

---

### 第 2 步：在 GitHub 添加 Secret

1. 访问：https://github.com/xu75/quant-strategy/settings/secrets/actions
2. 点击 **New repository secret**
3. 设置：
   - **Name**: `WEBHOOK_SUBSCRIBERS_JSON`
   - **Value**: 粘贴完整的 JSON（可以压缩成一行）
     ```json
     [{"id":"chuckfang","url":"https://api.chuckfang.com/73422055/","format":"json"}]
     ```
4. 点击 **Add secret**

---

### 第 3 步：修改 GitHub Actions 读取 Secret

修改 `.github/workflows/run_strategy.yml`：

```yaml
- name: Notify on signal changes
  env:
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
    WEBHOOK_SUBSCRIBERS_JSON: ${{ secrets.WEBHOOK_SUBSCRIBERS_JSON }}  # 新增
  run: python scripts/telegram_notify.py
  continue-on-error: true
```

---

### 第 4 步：修改 Python 脚本支持环境变量

修改 `scripts/telegram_notify.py`：

```python
# 在文件开头添加（约第 35 行附近）
SUBSCRIBERS_FILE = Path("config/subscribers.json")
SUBSCRIBERS_JSON_ENV = os.environ.get("WEBHOOK_SUBSCRIBERS_JSON", "")

# 修改 load_subscribers() 函数
def load_subscribers() -> list[dict]:
    """Load webhook subscribers from env var or config file.
    
    Priority: WEBHOOK_SUBSCRIBERS_JSON env var > config/subscribers.json
    Env var mode allows GitHub Actions to use encrypted secrets without
    committing sensitive URLs to the repository.
    """
    # Priority 1: Environment variable (GitHub Actions secrets)
    if SUBSCRIBERS_JSON_ENV:
        try:
            return json.loads(SUBSCRIBERS_JSON_ENV)
        except json.JSONDecodeError as e:
            print(f"[error] Invalid JSON in WEBHOOK_SUBSCRIBERS_JSON: {e}")
            return []
    
    # Priority 2: Local config file (dev/manual runs)
    if not SUBSCRIBERS_FILE.exists():
        return []
    try:
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
```

---

### 第 5 步：从 Git 彻底删除敏感文件

⚠️ **这会重写 Git 历史，需要 force push**

#### 方案 A：使用 git-filter-repo（推荐）

```bash
# 1. 安装工具
pip install git-filter-repo

# 2. 备份仓库（重要！）
cd /Users/xujinsong/VSCode/SynologyDrive
cp -r quant-strategy quant-strategy-backup-$(date +%Y%m%d)

# 3. 进入项目
cd quant-strategy

# 4. 删除文件的所有历史记录
git filter-repo --path config/subscribers.json --invert-paths

# 5. 强制推送（会重写所有 commit SHA）
git remote add origin https://github.com/xu75/quant-strategy.git
git push origin --force --all
git push origin --force --tags
```

#### 方案 B：使用 BFG Repo-Cleaner（更快）

```bash
# 1. 安装
brew install bfg

# 2. 备份
cd /Users/xujinsong/VSCode/SynologyDrive
git clone --mirror quant-strategy quant-strategy-backup.git

# 3. 删除敏感文件
cd quant-strategy
bfg --delete-files subscribers.json

# 4. 清理引用
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 5. 强制推送
git push origin --force --all
git push origin --force --tags
```

---

### 第 6 步：验证配置

#### 6.1 检查 Git 历史是否干净

```bash
# 应该返回空（说明文件已从历史中删除）
git log --all --full-history -- config/subscribers.json
```

#### 6.2 手动触发 GitHub Actions 测试

1. 访问：https://github.com/xu75/quant-strategy/actions/workflows/run_strategy.yml
2. 点击 **Run workflow** → 选择 `daily-signal` 模式
3. 查看日志，确认：
   - ✅ "Active channels: Webhooks(1)" 出现（说明 Secret 被正确读取）
   - ✅ 无 "No channels configured" 错误

#### 6.3 本地测试（可选）

```bash
# 设置临时环境变量
export WEBHOOK_SUBSCRIBERS_JSON='[{"id":"test","url":"https://webhook.site/your-unique-url","format":"json"}]'

# 运行脚本（不会修改 Git）
python scripts/telegram_notify.py

# 检查 webhook.site 是否收到测试请求
```

---

### 第 7 步：吊销泄露的 Webhook（如果可能）

如果 `api.chuckfang.com` 支持重新生成 webhook URL：

1. 登录 chuckfang.com 管理后台
2. 重新生成新的 webhook URL（例如 `/98765432/`）
3. 更新 GitHub Secret `WEBHOOK_SUBSCRIBERS_JSON`：
   ```json
   [{"id":"chuckfang","url":"https://api.chuckfang.com/98765432/","format":"json"}]
   ```
4. 删除旧 webhook 或设置白名单（仅接受来自 GitHub Actions IP）

---

## 📋 迁移检查清单

- [ ] **第 1 步**：备份当前 `config/subscribers.json` 内容
- [ ] **第 2 步**：在 GitHub 添加 Secret `WEBHOOK_SUBSCRIBERS_JSON`
- [ ] **第 3 步**：修改 `.github/workflows/run_strategy.yml` 传递 Secret
- [ ] **第 4 步**：修改 `scripts/telegram_notify.py` 读取环境变量
- [ ] **第 5 步**：使用 `git-filter-repo` 或 BFG 清理历史
- [ ] **第 6 步**：验证 Actions 能正确读取 Secret
- [ ] **第 7 步**：（可选）吊销泄露的 webhook URL
- [ ] **第 8 步**：删除本地 `config/subscribers.json`（保留在 `.gitignore`）

---

## 🔄 日常维护

### 添加新的 webhook 订阅者

1. 访问：https://github.com/xu75/quant-strategy/settings/secrets/actions
2. 点击 `WEBHOOK_SUBSCRIBERS_JSON` → **Update**
3. 编辑 JSON：
   ```json
   [
     {"id":"chuckfang","url":"https://api.chuckfang.com/98765432/","format":"json"},
     {"id":"new-subscriber","url":"https://example.com/webhook","format":"discord"}
   ]
   ```
4. 点击 **Update secret**
5. 无需 commit、无需重新部署（下次 Actions 运行时自动生效）

### 本地开发测试

开发时可以保留 `config/subscribers.json`（已在 `.gitignore`），脚本会自动回退到读取本地文件。

---

## 🆚 方案对比

| 方案 | 安全性 | 复杂度 | 可见性 | 审计 |
|------|--------|--------|--------|------|
| **GitHub Secrets（推荐）** | ✅ 最高 | ✅ 低 | ✅ Owner 可见 | ✅ 有日志 |
| Private Submodule | ⚠️ 中（依赖 repo 权限） | ⚠️ 中（多仓库管理） | ⚠️ 协作者可见 | ❌ 无 |
| 环境变量文件 (.env) | ❌ 低（易误提交） | ✅ 低 | ❌ 本地可见 | ❌ 无 |
| 加密文件 + GPG | ⚠️ 中 | ❌ 高 | ⚠️ 需要密钥 | ❌ 无 |

---

## ❓ FAQ

### Q1: 为什么不用 private submodule？

**可以用**，但 GitHub Secrets 更简单：
- Private submodule 需要管理两个仓库 + 配置 SSH keys
- Secrets 原生支持，无需额外权限配置
- Secrets 在 Actions 日志中自动打码

如果你有其他敏感配置文件（不只是 JSON），submodule 更合适。

### Q2: GitHub Secrets 是否足够安全？

✅ **是的**，GitHub Secrets 使用：
- AES-256 加密存储
- TLS 传输
- 仅在运行时解密到内存
- 日志自动打码（显示为 `***`）
- 仅 repo admin 可见

参考：https://docs.github.com/en/actions/security-guides/encrypted-secrets

### Q3: 如果 Actions 日志泄露了怎么办？

**不会泄露**。GitHub 自动检测并打码：
```
[notify] Active channels: Webhooks(1)
[webhook] Sent successfully to ***
```

即使脚本打印完整 URL，也会被替换为 `***`。

### Q4: 本地开发如何测试 webhook？

三种方式：
1. **保留本地 `config/subscribers.json`**（已在 `.gitignore`）
2. **设置环境变量**：`export WEBHOOK_SUBSCRIBERS_JSON='[...]'`
3. **使用 webhook.site**：生成临时测试 URL

### Q5: 需要通知其他协作者吗？

⚠️ **需要**。如果有其他人 clone 了仓库：

清理历史后，他们需要：
```bash
# 删除旧仓库
rm -rf quant-strategy

# 重新 clone
git clone https://github.com/xu75/quant-strategy.git
```

或者：
```bash
cd quant-strategy
git fetch origin
git reset --hard origin/main
```

---

## 📚 相关文档

- [GitHub Encrypted Secrets 官方文档](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [git-filter-repo 用户手册](https://github.com/newren/git-filter-repo)
- [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)

---

**创建于**: 2026-09-18  
**状态**: ✅ Ready to Implement  
**预计时间**: 30 分钟
