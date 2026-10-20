# Brainstorm Summary

- Change: admin-user-management
- Date: 2026-10-20（方案主体 parked 前确认，恢复后收口级联表清单；用户已确认定稿）

## 确认的技术方案

1. **访问控制**：用户管理单独 admin 门控，对外 404（不动 `require_admin` 全局 403 语义，`/settings` 行为不变）。非 admin 访问页面与接口均 404，且与不存在资源逐字节一致（沿用 ① 的惯例）。
2. **删除用户 = 单事务显式逐表清理**（SQLite FK 从未启用，外键子句不可依赖）：
   - DELETE 15 表（WHERE user_id = ?）：`_CONTENT_TABLES` 11 表（feeds, episodes, articles, social_posts, standalone_posts, url_sources, uploaded_images, generated_thumbnails, prompt_library, content_brief_runs, scheduled_posts）+ content_briefs + ② 通道 3 表（social_accounts, agent_jobs, agent_tokens）
   - content_briefs **一并删除**（用户裁决 2026-10-20：是用户内容，proposal 写明不留匿名数据；schema 的 SET NULL 标注不采信，FK 未启用本就无约束力）
   - agent_tokens 删除后该用户扩展配对立即失效
   - activity_log / usage_events：user_id 与 username 均置 NULL 保留（审计/用量去身份化；builder check 追问后用户裁决 2026-10-20：username 一并置 NULL）
   - 本地文件一并删（builder check 追问后用户裁决 2026-10-20）：事务提交后清理 uploaded_images(storage='local') 与 generated_thumbnails 的本地文件；cloud/external 行跳过；文件清理失败仅记日志
   - schedule_settings 等实例全局配置无 user_id，不在范围
3. **自我保护**：禁删当前登录 admin 自己；禁删最后一个 is_admin 账号（先转让再删）。
4. **重置密码**：表单输入即改（werkzeug 哈希），不校验旧密码、无邮件；被重置者现有 session 保持有效至自然过期（Flask session 无服务端态，接受并文档注明，强制下线另起 change）。
5. **UI**：`.pd` 组件库 + base.html 侧边栏 admin 区块（仅 admin 渲染）；删除二次确认 = 输入用户名 + 删除前展示将删数据量级（逐表计数）。
6. 数据层新增 `delete_user`（级联）与 `update_user_password`；列表复用已有 `list_users`（database.py:3762）。

## 关键取舍与风险

- 误删不可恢复 → 输入用户名二次确认 + 量级预览 + 部署前备份惯例沿用。
- 重置密码后旧会话有效 → 接受（session 无服务端态）。
- 删除范围与 ①/② 数据清单漂移 → 删除函数集中一处、按表清单实现；本 summary 已按 ① `_CONTENT_TABLES` + ② 三表逐一核对（2026-10-20 代码事实）。
- 无主行（user_id NULL）对所有用户不可见（严格 `user_id = ?` 查询模式）——不产生删除后的泄露面。

## 测试策略

沿用项目 gate 惯例：新增 `scripts/check_admin_user_management.py`（venv python 运行，分节 + OK token，临时库/纯内存，不联网、不触碰真实 insights.db）：
- `data` 节：级联删除后 15 表该用户行清零、activity_log/usage_events 保留且 user_id/username 均 NULL、agent_tokens 清零、local 文件被删；重置密码后新哈希可验证旧哈希失效；禁删自己/最后一个 admin 被拒。
- `e2e` 节：真路由——admin 列表/重置/删除全流程；非 admin 访问管理页与各接口 404 且与不存在资源逐字节一致；删除后该用户数据各视角不可见。

## Spec Patch

新建 delta spec `openspec/changes/admin-user-management/specs/admin-user-management/spec.md`（change 目录尚无 specs/，open 阶段未产出）：
- Requirement: 用户列表仅 admin 可见可访问（页面+接口 404，与不存在逐字节一致）
- Requirement: admin 可重置任意用户密码（Scenario: 重置生效旧密码失效；Session 保持有效至过期）
- Requirement: admin 可删除用户及全部数据（Scenario: 15 表级联清零+审计去身份化+token 失效；Scenario: 禁删自己；Scenario: 禁删最后一个 admin；Scenario: 非 admin 404）
