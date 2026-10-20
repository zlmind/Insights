# Comet Design Handoff

- Change: admin-user-management
- Phase: design
- Mode: compact
- Context hash: a31c7bd70255b4f9a6011cea1e3f96fc0ebad2918af32941196762246f8f4670

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/admin-user-management/proposal.md

- Source: openspec/changes/admin-user-management/proposal.md
- Lines: 1-30
- SHA256: 85beed8aadc48914d71808de5e939f8dd4fc48627d25f7b2909d6888d271acf7

```md
# Proposal: admin-user-management

## Why

多用户隔离落地后（multi-user-content-isolation / multi-user-publishing-channels），账号的生命周期管理只能靠直接操作数据库：没有用户列表、无法重置密码、无法删除账号。需要给 admin 一个用户管理页面，覆盖查看、重置密码、删除用户（含其全部数据）的日常操作；注册开关沿用现有 ALLOW_SIGNUP 设置。

## What Changes

- 新增 admin 专属用户管理页面：用户列表（用户名、是否 admin、创建时间、最近登录）。
- 重置密码：admin 为任意用户设置新密码（输入即改，不做邮件流程）。
- 删除用户：删除该用户及其全部内容数据（连带删除，不留匿名数据）；**禁止删除 admin 自己和最后一个 admin 账号**。
- 用户管理入口仅 admin 可见可访问，非 admin 访问返回 404。
- 注册开关沿用 `/settings` 中 ALLOW_SIGNUP（本次不改其行为）。

## Capabilities

### New Capabilities

- `admin-user-management`: admin 管理用户生命周期（查看/重置密码/删除）的行为契约与访问控制。

### Modified Capabilities

<!-- 无：登录/注册流程本身不变，内容隔离与通道隔离由其他 change 承载。 -->

## Impact

- `database.py`：用户列表已有 `list_users`；新增删除用户及其数据的级联清理、密码更新函数。
- `insights_web.py`：新增用户管理路由（admin 门控，沿用 `require_admin` 但对外 404）。
- 模板：新增用户管理页（沿用 `.pd` 组件库与侧边栏）；base.html 侧边栏 admin 条目。
- 删除用户需清理的数据面随 multi-user-content-isolation 的数据清单同步扩展（两 change 若同仓先后落地，删除范围以已隔离的表为准）。
```

## openspec/changes/admin-user-management/design.md

- Source: openspec/changes/admin-user-management/design.md
- Lines: 1-40
- SHA256: 6b7f93e4bf1fd2f23a9a8f8d9625623c9184e30902404015b76162779c7850a7

```md
# Design: admin-user-management

## Context

`users` 表：id/username/password_hash/is_admin/created_at/last_login_at。首个注册用户自动成为 admin（`create_user` 内置）；`require_admin` 装饰器已存在（当前 403）；`/settings`（admin）管理 ALLOW_SIGNUP。密码用 werkzeug 哈希。项目尚无任何用户管理界面。

## Goals / Non-Goals

**Goals:**

- admin 一个页面完成：看用户列表、重置密码、删除用户（连数据）。
- 非 admin 完全不可见该功能（页面与接口均 404，不泄露存在性）。

**Non-Goals:**

- 不做角色/权限体系细化（仅 is_admin 布尔）。
- 不做邮箱、昵称、头像等新字段；不改登录/注册/会话机制。
- 不做"匿名保留"删除选项（用户已确认连带删除）。

## Decisions

1. **访问控制 = 复用 `require_admin`，但把其 403 改为 404**？不动全局装饰器语义——为用户管理单独做 admin 门控（沿用装饰器逻辑、响应 404），避免影响现有 `/settings` 行为。**备选**：全局把 admin 页面统一改 404——影响面超出本 change，不做。
2. **删除用户 = 单事务级联清理**：按内容域数据清单逐表 `DELETE WHERE user_id = ?`，再做 `DELETE FROM users`；`activity_log`/`usage_events` 外键为 `SET NULL`，审计与用量记录保留但去身份化（符合"内容连数据一起删、审计留痕"的惯例）。`agent_tokens` 若存在则一并删除（该用户的扩展立即失效）。**备选**：数据库外键 ON DELETE CASCADE——现有表未启用外键约束（SQLite 默认关闭），依赖它不可靠，显式逐表删除。
3. **自我保护约束**：不允许删除当前登录 admin 自己；不允许删除最后一个 is_admin 账号（先转让再删）。防锁死在系统之外。
4. **重置密码 = 表单输入新密码即改**（werkzeug 哈希），不校验旧密码、无邮件；被重置用户的现有 session 保持有效至自然过期（Flask session 无服务端存储，如需强制下线需改会话机制——超出本期，接受）。
5. **UI 沿用 `.pd` 组件库与侧边栏**：新增侧边栏 admin 区块入口（仅 admin 可见），页面风格与 settings 页一致；删除操作二次确认（输入用户名确认）。

## Risks / Trade-offs

- [删除用户误操作不可恢复] → 二次确认输入用户名 + 删除前列出将删除的数据量级；部署前备份惯例沿用。
- [重置密码后旧会话仍有效] → 接受（session 无服务端态）；文档注明，如需强制下线另起 change。
- [删除范围与 ① 的数据清单漂移] → 删除函数集中在一处、按表清单实现；① 落地顺序在前时以 ① 的表清单为准，评审时交叉核对。

## Migration Plan

纯新增功能，无 schema 迁移（`agent_tokens` 表由 multi-user-publishing-channels 建）。回滚 = 还原代码。

## Open Questions

- 无。
```

## openspec/changes/admin-user-management/tasks.md

- Source: openspec/changes/admin-user-management/tasks.md
- Lines: 1-19
- SHA256: 820eef14d83838e56346ccfc98b022daea9feafff0fd8e27d01b5bda4b13daac

```md
# Tasks: admin-user-management

## 1. 数据层

- [ ] 1.1 用户删除级联函数：单事务逐表清理该用户全部内容数据（表清单与 multi-user-content-isolation 对齐）+ agent_tokens + users 行；activity_log/usage_events 保留但去身份化
- [ ] 1.2 密码更新函数（werkzeug 哈希）；最后一个 admin 保护与"不可删除自己"的判定函数

## 2. Web 层

- [ ] 2.1 用户管理页路由（列表/重置密码/删除用户），admin 门控对外 404（不动 /settings 的 403 行为）
- [ ] 2.2 删除前置校验：禁止删除自己、禁止删除最后一个 admin；删除二次确认（输入用户名）+ 将删除的数据量级提示

## 3. UI

- [ ] 3.1 用户管理模板（沿用 .pd 组件库）+ base.html 侧边栏 admin 区块入口（仅 admin 渲染）+ zh.json 词条

## 4. 验证

- [ ] 4.1 端到端验收：admin 可列表/重置/删除；非 admin 访问管理页与接口均 404；删除后该用户数据在库中不可见、其扩展 token 失效（若 ② 已落地）；删除自己/最后一个 admin 被拒绝
```

## openspec/changes/admin-user-management/specs/admin-user-management/spec.md

- Source: openspec/changes/admin-user-management/specs/admin-user-management/spec.md
- Lines: 1-55
- SHA256: 93037d64f8671022066f0340a39fa8ac35345ffd0375c522ce7b0a9898aca7c5

```md
# admin-user-management Specification

## ADDED Requirements

### Requirement: 用户列表仅 admin 可见可访问

admin 专属用户管理页面展示用户列表（用户名、是否 admin、创建时间、最近登录）。非 admin 用户访问该页面或其任何接口时，服务端返回 404，且响应与访问不存在资源时逐字节一致，不泄露功能存在性。

#### Scenario: 非 admin 访问返回 404

- **WHEN** 非 admin（含未登录）用户请求用户管理页面或其接口
- **THEN** 返回 404，响应体与请求不存在路径的响应逐字节一致

#### Scenario: admin 可查看列表

- **WHEN** admin 用户打开用户管理页面
- **THEN** 展示全部用户及其用户名、是否 admin、创建时间、最近登录

### Requirement: admin 可重置任意用户密码

admin 在用户管理页为任意用户输入新密码即完成重置（werkzeug 哈希存储），不校验旧密码、无邮件流程。被重置用户的现有 session 保持有效至自然过期。

#### Scenario: 重置生效旧密码失效

- **WHEN** admin 为用户 U 设置新密码
- **THEN** U 用新密码可登录，旧密码登录被拒

#### Scenario: 现有 session 保持有效

- **WHEN** U 已登录且密码被 admin 重置
- **THEN** U 的现有 session 在自然过期前继续有效（无服务端会话存储，强制下线不在本能力范围）

### Requirement: admin 可删除用户及其全部数据

删除用户 = 删除该用户全部内容数据（不留匿名数据）+ users 行，单事务完成：内容域 11 表（feeds、episodes、articles、social_posts、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、content_brief_runs、scheduled_posts）、content_briefs、通道 3 表（social_accounts、agent_jobs、agent_tokens）逐表按 user_id 清理；agent_tokens 删除后该用户扩展配对立即失效；activity_log 与 usage_events 保留但 user_id 与 username 均置 NULL（去身份化审计）。本地存储的文件（uploaded_images storage='local' 对应 static/uploads/ 下文件、generated_thumbnails 对应 static/uploads/youtube_thumbnails/ 下文件）在事务提交后一并删除；云端/外链存储行无本地文件。删除前二次确认需输入目标用户名，并展示将删除的数据量级。禁止删除当前登录 admin 自己；禁止删除最后一个 admin 账号（转让 admin 为手工 SQL 操作，不在本能力范围）。

#### Scenario: 级联删除生效

- **WHEN** admin 确认删除用户 U（输入用户名匹配）
- **THEN** U 在上述 15 张表中的行全部清除，users 行删除；activity_log/usage_events 中原属 U 的记录保留且 user_id、username 均为 NULL；U 的本地文件被删除；U 的扩展 token 失效

#### Scenario: 禁止删除自己

- **WHEN** admin 尝试删除自己的账号
- **THEN** 操作被拒绝，账号与数据不变

#### Scenario: 禁止删除最后一个 admin

- **WHEN** 系统仅剩一个 admin 账号且 admin 尝试删除它
- **THEN** 操作被拒绝（须先转让 admin 再删）

#### Scenario: 确认输入不匹配则不删除

- **WHEN** 删除确认弹层中输入的用户名与目标用户名不一致
- **THEN** 不执行任何删除
```

