# Proposal: multi-user-content-isolation

## Why

平台已有账号体系（users 表 + 登录门槛 + session），但所有业务数据共用一个池：任何账号（如新注册的 layne 与原有 demo）登录后都能看到全部 RSS 源、文章、帖子、素材、提示词、briefs 和定时帖子。需要按用户隔离内容数据，让每个用户只看到自己的内容；同时把存量无主数据划给第一个账号，避免升级后数据"消失"。

## What Changes

- **BREAKING**：内容域数据表（feeds、episodes、articles、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、recent prompts、content_briefs、content_brief_runs、scheduled_posts）增加 `user_id` 归属，所有读写按当前登录用户过滤；跨用户 ID 直访返回 404。
- 数据层 `database.py` 的 list/get/create/update/delete 函数增加用户归属参数与过滤。
- Web 层 `insights_web.py` 全部内容路由注入 `current_user`，按用户过滤列表、校验资源归属。
- 后台 worker（episode 处理、content_agent 跑批、brief 定时跑、YouTube 频道回填）在入队/生成时记录归属用户。
- 一次性数据迁移：现有全部无主内容数据划给第一个注册账号（admin）。
- 用量/活动页的「全部」视角仅对 admin 开放，普通用户只能看自己的。
- 平台账号（social_accounts）、浏览器扩展配对、调度时段/每日限额、`app_settings` 本次**不**隔离（后续 change 处理）。

## Capabilities

### New Capabilities

- `user-content-scoping`: 内容数据按用户隔离的行为契约——归属、可见性、跨用户访问拒绝、后台任务归属、存量迁移。

### Modified Capabilities

<!-- 项目尚无任何 spec，全部为新增能力。 -->

## Impact

- `database.py`（4365 行）：~12 张表 schema + 全部 list/get/create/update/delete 函数签名与 SQL。
- `insights_web.py`（7064 行，~100 路由）：内容相关路由的查询过滤与归属校验。
- `content_agent.py` / `insights.py`：后台 worker 的归属传递。
- SQLite 迁移：`ALTER TABLE ADD COLUMN user_id` + 回填脚本（应用启动时自动执行，沿用现有 in-place 升级模式）。
- 不影响：MultiPost 扩展协议、agent 配对、发布通道（仍共享）、调度时段配置。
