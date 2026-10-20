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
