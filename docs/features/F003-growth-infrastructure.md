---
feature_ids: [F003]
related_features: [F001]
topics: [growth, notification, registration, referral, content-tiering, telegram, vercel]
doc_kind: spec
created: 2026-05-02
status: spec
---

# F003 - 用户增长基础设施：信号通知 + 注册 + 内容分层

> Status: spec | Owner: 宪宪/Opus-4.6
> Evolved from: F001 P2 "订阅通知"

## Why

项目已完成 MVP（F001 P1 ✅），策略每 4 小时自动运行，前端展示信号和回测。但目前：

1. **无法知道有没有人在用**：没有访问统计，没有用户反馈渠道
2. **GitHub 不是有效的用户触达渠道**：中国大陆访问不稳定，非技术用户不看 GitHub
3. **信号价值未被放大**：用户必须主动访问网站才能看到信号变化，没有推送机制
4. **项目推广缺乏增长飞轮**：没有注册、没有社区、没有口碑传播机制

铲屎官明确：变现不是目标，保持免费和开源，但项目推广——让更多人知道和使用——是现阶段目标。

## 核心原则

**策略逻辑永远公开，服务便利层可以分层。**

- 策略规则、参数选择、回测假设、核心研究结论：永远公开，无需注册
- 源码仓库保持 private，按需授予访问权限
- 注册/邀请解锁的是便利服务：通知推送、偏好设置、历史搜索、交互式分析
- 网站提供的是"不用自己跑就能获得整理好的结果"

## 参考

- [Arkvol](https://arkvol.com/)：Freemium 模式，免费层看基础数据，Pro 层通过捐赠解锁。Telegram 社群运营。隐私优先。
- [QuantConnect](https://www.quantconnect.com/)：49 万用户社区，开源引擎 LEAN（19K GitHub stars）引流，公共策略库形成网络效应。
- [OpenBB](https://openbb.co/)：开源数据平台做入口，开发者生态扩展功能，内容营销 + 行业背书增长。

## What

三阶段递进，每阶段独立可交付：

### Phase 1 — 信号通知 + 基础统计（最小可行增长）

不需要用户注册，不需要数据库。

- Telegram 公共信号频道：GitHub Actions 信号变化后 POST Telegram Bot API 推送
- 网站 CTA（Call to Action）：首页和策略页添加"加入 Telegram 频道获取实时信号"入口
- Vercel Analytics：启用免费层访问统计（Hobby 限 50K events/月），了解页面热度和 referrer 来源
- UTM 参数规范：推广链接统一带 UTM 参数；统计侧先用 Vercel referrer/page metrics，UTM 维度分析需 Analytics Plus 或自建轻量 redirect endpoint（后续评估）

### Phase 2 — 用户注册 + 通知偏好

引入轻量用户系统。

- Astro 切 hybrid 模式：公开页继续 SSG，`/api/*` 和 `/account` 走 SSR
- Marketplace Postgres 存储用户数据（Vercel Postgres 已停止新项目接入，通过 Vercel Marketplace 接 Neon / Supabase / Prisma Postgres，具体选型 Phase 2 启动时评估）
- Magic link 登录（邮箱验证码，无密码）。安全契约：一次性 token hash 存储、15 分钟过期、登录尝试限频（5 次/邮箱/小时）、邮件枚举防护（统一返回"已发送"）、回调域名白名单、session cookie HttpOnly + SameSite=Strict
- 通知偏好：选择接收哪些策略的信号、通过哪个渠道（Telegram / email / webhook）
- 个人 dashboard：订阅的策略列表、历史信号归档、搜索

### Phase 3 — Referral + 内容分层

验证需求后再做，不提前建权限系统。

- Referral 机制：邀请一个新注册用户 → 解锁 Tier 2 内容
- 内容分层：
  - Tier 0（无注册）：当前信号、equity curve、基础回测、核心研究结论
  - Tier 1（免费注册）：通知推送、偏好设置、历史归档搜索
  - Tier 2（referral-gated）：交互式参数扫描分析、个性化视图、新策略 early access 提前通知（核心研究结论和 canonical report 仍公开）
- 最小表结构：`profiles`, `referral_events`, `entitlements`, `notification_subscriptions`

## 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| Auth | Magic link (自建) | 最简单，无第三方依赖，Vercel Serverless Function 发邮件 |
| 数据库 | Marketplace Postgres (Neon/Supabase/Prisma TBD) | Vercel Postgres 已停新项目，通过 Marketplace 接外部 Postgres provider |
| 通知 - Telegram | Telegram Bot API | HTTPS 接口，GitHub Actions 直接调用，零成本 |
| 通知 - Email | Resend 免费层 | 100 emails/day，够早期验证 |
| 前端 | Astro hybrid (SSG + SSR) | 公开页静态快，动态路由按需 SSR |
| 部署 | Vercel (现有) | 无额外成本，Serverless Functions 免费层够用 |
| 统计 | Vercel Analytics (Hobby) | 免费 50K events/月，referrer + page 维度；UTM 维度需 Plus 或自建 |

## 推广渠道（Phase 1 同步启动）

| 渠道 | 优先级 | 行动 |
|------|--------|------|
| Telegram 信号频道 | P0 | 创建频道，GitHub Actions 自动推送 |
| GitHub README + Topics | P0 | 已优化（本轮 README review），补 topics 标签 |
| Reddit (r/algotrading, r/cryptocurrency) | P1 | 发帖介绍项目 + 策略逻辑 |
| Twitter/X 量化社区 | P1 | 定期分享信号和研究 |
| 中文量化社区（聚宽、掘金量化） | P1 | 发帖 + 策略分享 |
| 知乎 / Medium 博客 | P2 | 研究备忘录改写为博客文章 |

## Acceptance Criteria

### Phase 1
- [ ] AC-1: Telegram 信号频道创建，策略信号变化时自动推送消息
- [ ] AC-2: 网站首页和策略页有 Telegram 频道 CTA 入口
- [ ] AC-3: Vercel Analytics 启用，能看到基础访问数据
- [ ] AC-4: 推广链接统一 UTM 参数规范；基础流量通过 Vercel referrer/page metrics 可观测

### Phase 2
- [ ] AC-5: 用户可通过 magic link 注册/登录
- [ ] AC-6: 登录用户可设置通知偏好（策略 + 渠道）
- [ ] AC-7: 登录用户可查看历史信号归档

### Phase 3
- [ ] AC-8: Referral 机制：邀请新用户解锁 Tier 2
- [ ] AC-9: 内容分层：Tier 0/1/2 权限正确隔离
- [ ] AC-10: Tier 2 专属交互式分析页面（核心研究结论仍公开）

## Dependencies

- F001 P1 ✅（MVP 已完成）
- Telegram Bot 创建（需铲屎官操作：创建 bot + 频道）
- Marketplace Postgres 启用（需铲屎官在 Vercel Marketplace 选择 provider，Phase 2）

## Risk

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| Telegram 在部分地区受限 | 中 | Phase 2 补 email 通知；长期考虑微信/Discord |
| Vercel 中国大陆访问不稳定 | 中 | 监控访问数据，必要时加 Cloudflare 或国内 CDN |
| Referral 被滥用（批量注册） | 低 | Phase 3 再考虑，magic link 本身有一定防刷能力 |
| 内容分层被误解为"不再公开" | 中 | README + 网站明确说明：策略逻辑与核心研究结论公开，源码 private 按需授权；分层仅限便利服务 |

## Open Questions

1. ~~注册机制是否需要？~~ → 需要，但 Phase 2 再做（铲屎官 2026-05-02 确认）
2. Telegram bot token 和频道由谁创建？→ 需铲屎官操作
3. 中国大陆用户占比多少？是否需要优先解决访问问题？
4. 网站代码许可：F001 写的是 MIT，策略代码 AGPL。F003 新增的 auth/notification 代码跟哪个？

## Key Decisions

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-05-04 | Repo private + 开放策略定位 | 铲屎官决策：repo 保持 private，按需开权限；网站从"开源代码项目"改为"开放策略研究"，去掉"任何人可 clone"承诺 |
| 2026-05-02 | 先通知后注册 | 砚砚 review：先验证需求（Telegram 频道），再建基础设施（注册系统） |
| 2026-05-02 | Vercel 原生技术栈 | 铲屎官反馈：GitHub 不靠谱，前端是用户入口；Vercel 免费层够用 |
| 2026-05-02 | Phase 1 不引入 Auth/DB | Phase 1 不需要数据库；Phase 2 通过 Vercel Marketplace 接 Postgres provider（Neon/Supabase/Prisma TBD） |
| 2026-05-02 | Tier 2 不 gate 研究结论 | 砚砚 review：核心研究结论和 canonical report 永远公开，Tier 2 只 gate 交互式分析和 early access |
