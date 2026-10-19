# Design: multi-user-publishing-channels

## Context

`agent_hub.py`（191 行）现状：全局一个 `AGENT_TOKEN`（app_settings/env）；heartbeat 在内存中按 tool 名记录，平台登录态从 heartbeat 推导；配对码（单 pending、TTL 5 分钟、限 5 次尝试）兑换后返回同一个全局 token。`social_accounts` 表存平台登录记录，"每平台一号"的展示逻辑在 `/accounts` 路由。扩展侧只拿一个不透明 bearer token，不理解用户概念。

## Goals / Non-Goals

**Goals:**

- 每个用户有自己的扩展配对 token 与平台登录态视图；互不可见、互不可用。
- 发布与调度严格按帖子归属选用通道，他人通道不降级可用。
- 扩展侧协议不变（token 仍是单个不透明字符串）。

**Non-Goals:**

- 不隔离调度时段/每日限额（全局资源）。
- 不做多浏览器 profile 的自动化管理（文档说明即可）。
- 不改 MultiPost 扩展代码（除非验证发现必须改，届时最小化并单独说明）。

## Decisions

1. **token 模型 = 按用户签发、落库**：新增 `agent_tokens`（token、user_id、created_at、last_seen_at；token 落库前哈希）。配对码在已登录会话中铸造并绑定 `current_user`，兑换返回该用户的专属 token。现有全局 `AGENT_TOKEN` 迁移时绑定给第一个账号（admin），行为向后兼容。**备选**：维持单 token + 心跳里带用户名——被否，因为 token 即身份，凭配置文件 token 冒充任意用户太容易。
2. **heartbeat 按用户维度**：内存结构从 `tool -> beat` 改为 `user_id -> tool -> beat`；`platform_login_states`/`login_states_view` 接受 user_id，账号页只看自己的快照。公开端点（agent_heartbeat 等）经 token 解析出用户，解析失败照旧 401。
3. **social_accounts 归属复用 ① 的迁移惯例**：加 `user_id` 列 + 标志位守护的一次性回填到第一个账号。
4. **发布不降级**：入队与调度按帖子的 `user_id` 解析该用户的通道；通道不可用时帖子保持排队并标注原因（"平台未连接/登录态异常"），绝不借用他人登录态。**备选**：允许 admin 通道兜底——被否，违反隔离语义且易误发。
5. **compose 账号选择器按用户过滤**：平台可用性 = 该用户 heartbeat 登录态 + 该用户 social_accounts 记录。
6. **配对码防串扰**：码仍单 pending，但绑定铸造者；兑换成功即签发铸造者的 token。A 页面铸造的码被 B 的浏览器兑换，得到的是 A 的 token——与"码即授权转移"语义一致，TTL 5 分钟窗口内风险可接受（沿用现状模型，不新增复杂度）。

## Risks / Trade-offs

- [一个浏览器 profile 同时只能配对一个用户，第二个人要用发布必须另开 profile] → 文档写明操作方式；不在本期做 profile 管理。
- [全局调度器按归属分组的改动横跨发帖链路，回归面大] → 调度器改造限定在"通道解析"一层；发帖失败原因可见（排队状态标注），验收场景覆盖"他人通道不可用时不误发"。
- [内存 heartbeat 重启即失（现状）] → 保持现状，不为此引入持久化。

## Migration Plan

1. 部署前备份 `insights.db`。
2. 启动时：social_accounts 加列回填（同 ① 惯例）；`agent_tokens` 建表；若设置了全局 `AGENT_TOKEN` 且尚无 admin token，则为第一个账号签发等价 token 记录（保证已配对扩展不断线）。
3. 回滚 = 还原备份；扩展侧无需重配。

## Open Questions

- MultiPost 扩展兑换/心跳请求体是否携带可区分字段（design 阶段读 `D:\git3\MultiPost-Extension` 源码确认，目标是零改动）。
- 登录态异常帖子的重试与提示文案细节（build 阶段定）。
