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
