# Tasks: multi-user-publishing-channels

## 1. token 与配对

- [x] 1.1 `agent_tokens` 表（哈希落库、user_id、last_seen_at）+ 启动迁移：全局 AGENT_TOKEN 绑定第一个账号（已配对扩展不断线）
- [x] 1.2 配对码绑定铸造者：已登录会话铸造、兑换签发该用户 token；TTL/尝试上限语义不变
- [x] 1.3 公开端点（heartbeat/claim/job events/pair）按 token 解析用户，解析失败 401

## 2. 登录态与账号页

- [x] 2.1 heartbeat 内存结构按用户维度存储；`platform_login_states`/`login_states_view` 按 user_id 查询
- [x] 2.2 `/accounts` 账号页只显示当前用户的平台连接与登录态；"每平台一号"逻辑按用户成立
- [x] 2.3 social_accounts 加 `user_id` + 一次性回填（复用 ① 迁移惯例）；list/create/delete 按用户过滤

## 3. 发布与调度

- [x] 3.1 compose 平台/账号选择器按当前用户过滤（平台可用性 = 自己的登录态 + 自己的账号记录）
- [x] 3.2 发布入队与即时发布按帖子归属解析本人通道；他人通道不可见不可用
- [x] 3.3 全局调度器按帖子归属分组选用通道；通道不可用时保持排队并标注原因（"平台未连接/登录态异常"），不得借用他人通道

## 4. 验证

- [x] 4.1 MultiPost 扩展零改动验证：读 `D:\git3\MultiPost-Extension` 配对/心跳源码，确认 token 语义兼容；发现必须改动时最小化并单独说明
- [x] 4.2 端到端验收：双用户各自配对后账号页互不可见；A 帖子只用 A 通道发出；B 通道不可用时 A 帖子排队且标注原因，绝不误发到 B 的平台账号
