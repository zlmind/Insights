---
comet_change: admin-user-management
role: technical-design
canonical_spec: openspec
---

# Design Doc: admin-user-management

## Intent

多用户隔离落地后（① multi-user-content-isolation、② multi-user-publishing-channels），账号生命周期管理只能直接操作数据库。本设计给 admin 一个用户管理页面：查看用户列表、重置密码、删除用户（连数据一起删，不留匿名数据）。

OpenSpec delta spec（`openspec/changes/admin-user-management/specs/admin-user-management/spec.md`）是需求事实源；本文只写实现方案。

## Goals / Non-Goals

**Goals:**

- admin 一个页面完成：列表、重置密码、删除用户（含全部数据）。
- 非 admin 完全不可见：页面与接口均 404，且与不存在资源逐字节一致。

**Non-Goals:**

- 不做角色体系细化（仅 `is_admin` 布尔）；不加邮箱/昵称/头像字段；不改登录/注册/会话机制。
- 不做"匿名保留"删除选项（用户已裁决：连带删除）。
- 不改 `/settings` 的 `require_admin` 403 行为；不做强制下线。

## Constraints

- SQLite `PRAGMA foreign_keys` 从未启用——所有外键子句（含 `ON DELETE SET NULL`/`CASCADE`）**不可依赖**，级联必须显式逐表 `DELETE WHERE user_id = ?`。
- 项目查询模式为严格 `user_id = ?`：无主行（NULL）对所有用户不可见，删除后不产生泄露面。
- 密码用 werkzeug 哈希（`generate_password_hash`）。
- UI 沿用 `.pd` 组件库 + `base.html` 侧边栏；i18n 走 zh.json 词条。

## Confirmed Decisions（用户已确认 2026-10-20）

1. **访问控制**：用户管理单独 admin 门控（沿用 `require_admin` 逻辑但响应 404），不动全局 403 语义。非 admin 访问页面与接口均 404，与不存在资源逐字节一致（沿用 ① 的惯例）。
2. **删除用户 = 单事务显式逐表清理**，DELETE 15 表（`WHERE user_id = ?`）+ users 行：
   - `_CONTENT_TABLES` 11 表：feeds, episodes, articles, social_posts, standalone_posts, url_sources, uploaded_images, generated_thumbnails, prompt_library, content_brief_runs, scheduled_posts
   - content_briefs（用户裁决：一并删除；schema 的 SET NULL 标注不采信，FK 未启用无约束力）
   - ② 通道 3 表：social_accounts, agent_jobs, agent_tokens
   - agent_tokens 删除后该用户扩展配对立即失效
   - activity_log / usage_events：`user_id` 与 `username` 均置 NULL **保留**（审计/用量去身份化；用户裁决 2026-10-20：username 一并置 NULL，否则去身份化名不副实）
   - **本地文件一并删**（用户裁决 2026-10-20）：事务提交后追加文件系统清理——`uploaded_images` 中 storage='local' 行对应 `static/uploads/` 下文件、`generated_thumbnails` 对应 `static/uploads/youtube_thumbnails/` 下文件（路径取自行记录，收集于事务内、提交后 unlink）；cloudinary/unsplash/pexels/external 行无本地文件，跳过。文件清理失败仅记日志，不回滚（DB 已提交，残留文件可手工清）
   - 不在范围（无 user_id）：schedule_settings、schedule_time_slots、time_slot_platforms、platform_daily_limits、app_settings
   - 不在范围（无 user_id）：schedule_settings、schedule_time_slots、time_slot_platforms、platform_daily_limits、app_settings
3. **自我保护**：禁删当前登录 admin 自己；禁删最后一个 `is_admin` 账号（先转让再删）。
4. **重置密码**：表单输入即改（werkzeug 哈希），不校验旧密码、无邮件；被重置者现有 session 保持有效至自然过期（Flask session 无服务端态，接受并注明，强制下线另起 change）。
5. **UI**：`.pd` 组件库 + base.html 侧边栏 admin 区块（仅 admin 渲染）；删除二次确认 = 输入用户名 + 删除前展示将删数据量级（逐表计数）。
6. **数据层**：新增 `delete_user`（级联，单事务 + 提交后文件清理）与 `update_user_password`；列表复用已有 `list_users`（database.py:3762，需补 SELECT is_admin 列）。

7. **promote/demote admin 不在本期**（builder check 定夺，设计者裁决）：Non-Goals 已排除角色管理，列表页无 admin 开关；「先转让再删」= 手工 SQL 逃生门（`UPDATE users SET is_admin=1 WHERE username=?`），写进文档即可。

## Implementation Sketch

（留给 builder 的落点，表清单/函数名以本节为准）

- `database.py`：
  - `delete_user(conn_or_none, user_id)` — 单事务：事务内先收集待删文件路径（uploaded_images storage='local'、generated_thumbnails）→ 15 表逐表 DELETE → `UPDATE activity_log/usage_events SET user_id=NULL, username=NULL` → `DELETE FROM users` → 提交 → 事务外 unlink 文件（失败仅记日志）。表清单用元组常量集中定义，注释对齐 `_CONTENT_TABLES`（database.py:722）提醒漂移同步。
  - `update_user_password(user_id, password_hash)`。
  - 判定辅助：是否最后一个 admin（`SELECT COUNT(*) FROM users WHERE is_admin=1`）、目标是否自己（web 层比对 session user id）。
- `insights_web.py`：
  - 路由 `/admin/users`（列表 + POST 重置/删除），独立 404 门控装饰器（复用 `require_admin` 判定、`abort(404)`）。
  - 删除前置校验：自己 → 拒；最后一个 admin → 拒；确认输入用户名不匹配 → 拒。
  - 删除前量级接口/内联数据：逐表 `SELECT COUNT(*)` 供确认弹层展示。
- 模板 + `base.html` 侧边栏 admin 区块（`session.is_admin` 才渲染）+ zh.json 词条。
- `scripts/check_admin_user_management.py`（项目 gate 惯例，venv python 运行，分节 + OK token，临时库，不联网不触真实 insights.db）：
  - `data` 节：级联后 15 表清零、审计两表保留且 user_id/username 均 NULL、agent_tokens 清零、local 文件被 unlink；重置后新哈希可验证/旧哈希失效；禁删自己/最后一个 admin 被拒。→ `ADMIN_USER_MANAGEMENT_DATA_OK`
  - `e2e` 节：真路由——admin 列表/重置/删除全流程；非 admin 页面+接口 404 且与不存在资源逐字节一致；删除后各视角数据不可见。→ `ADMIN_USER_MANAGEMENT_E2E_OK`

## Risks / Trade-offs

- 误删不可恢复 → 用户名二次确认 + 量级预览 + 部署前备份惯例。
- 重置密码后旧 session 有效 → 接受（无服务端态），文档注明。
- 表清单与 ①/② 数据面漂移 → 删除函数集中一处、按清单实现；本清单已按 ① `_CONTENT_TABLES` + ② 三表逐一核对（2026-10-20 代码事实）。

## Migration Plan

纯新增，无 schema 迁移。回滚 = 还原代码。
