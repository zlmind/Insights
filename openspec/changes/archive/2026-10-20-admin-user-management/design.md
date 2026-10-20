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
