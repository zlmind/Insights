# Proposal: multi-user-publishing-channels

## Why

内容数据已按用户隔离（multi-user-content-isolation），但发布通道仍是全局共享的：`social_accounts`（平台登录记录）、浏览器扩展配对 token、agent heartbeat 上报的登录态全部不分用户。用户 A 能在账号页看到并使用用户 B 连接的平台账号发布。需要把发布通道按用户隔离，让每个用户只看到并使用自己的平台连接。

## What Changes

- **BREAKING**：`social_accounts` 增加 `user_id` 归属，平台账号记录按用户隔离；账号页只显示当前用户自己的连接与登录态。
- 浏览器扩展配对（pairing code → bearer token）按用户签发与校验：每个用户的扩展实例持有自己的 token，heartbeat 与登录态上报归属到对应用户。
- agent heartbeat 的平台登录态（ok / logged_out / unknown / offline）按用户维度存储与查询。
- 发布入队与全局调度器：发帖时按帖子的归属用户选用该用户自己的平台账号与登录态；不混用他人通道。
- compose 发布工作台的账号/平台选择器仅列出当前用户的可用通道。
- 调度时段（schedule_time_slots）与每日限额（platform_daily_limits）保持全局共享，不隔离。

## Capabilities

### New Capabilities

- `user-publishing-channels`: 发布通道按用户隔离的行为契约——平台账号归属、扩展配对与登录态归属、发布通道选用、调度器按归属发帖。

### Modified Capabilities

<!-- 依赖 user-content-scoping 提供的帖子归属；该能力在 multi-user-content-isolation 中新增，此处不修改它。 -->

## Impact

- `database.py`：social_accounts schema 与相关函数；登录态存储。
- `agent_hub.py`：配对码/token 模型改为按用户；heartbeat 状态按用户存储。
- `insights_web.py`：`/accounts`、`agent_pair`、`agent_heartbeat`、compose 账号选择、发布入队与调度器逻辑。
- MultiPost 扩展侧（`D:\git3\MultiPost-Extension`）：目标是不改协议——token 语义从"全局唯一"变为"绑定某用户"，扩展无需感知；若必须改动，改动最小化。
- 多用户多扩展实例可能需要多个浏览器 profile（design 阶段确认）。
