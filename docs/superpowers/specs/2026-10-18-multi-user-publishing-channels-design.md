---
comet_change: multi-user-publishing-channels
role: technical-design
canonical_spec: openspec
---

# 技术设计：发布通道按用户隔离（multi-user-publishing-channels）

- 日期：2026-10-18
- 状态：已经用户确认（brainstorm 两轮澄清 + 方案确认，2026-10-18）
- 上游事实源：`openspec/changes/multi-user-publishing-channels/`（proposal / design / tasks）；本文是面向实施的技术展开，冲突时以 OpenSpec 产物为准
- 前置：multi-user-content-isolation（①）已归档——内容已按用户隔离，本文只管发布通道

## 1. 意图与背景

内容数据已按用户隔离，但发布通道仍是全局共享的：用户 A 能在账号页看到并使用用户 B 连接的平台账号发帖。本设计把四类通道资产全部按用户隔离：平台账号记录（social_accounts）、扩展配对 token、heartbeat 登录态、agent job 认领。扩展（MultiPost）侧协议零改动。

## 2. 现状（代码事实，行号为 2026-10-18 基线）

### 2.1 单用户假设的五处承载点

| # | 承载点 | 位置 | 现状 |
|---|--------|------|------|
| 1 | 全局唯一 token | `agent_hub.py:29` `agent_token()`，settings 表 / env `AGENT_TOKEN` | 常量比较，无归属 |
| 2 | token 校验 | `agent_hub.py:37` `check_agent_auth()` | `== agent_token()`，不解析身份 |
| 3 | heartbeat | `agent_hub.py:53-110`（`_LAST_HEARTBEAT` + `_TOOL_BEATS`） | 内存单快照，全站共享；`platform_login_states()` probed 胜 unprobed、新胜旧、过期删除防泄漏；`LOGIN_STATE_URLS:113` 服务端固定 5 平台 |
| 4 | 配对 | `agent_hub.py:161/171` `create_pairing_code` / `redeem_pairing_code` | 内存单码（TTL 5 分钟、限 5 次尝试），redeem 返回**同一个全局 token** |
| 5 | 数据归属 | `database.py:594` social_accounts 建表、`:613` 唯一索引 `idx_social_accounts_identity(platform, external_id)`；账号函数群 `:4445-4569`；`enqueue_agent_job:4637`（带 created_by）、`claim_agent_job:4676`（按 kinds/agent/stale/executor/platforms 过滤） | social_accounts 与 agent_jobs 均无 user 维度 |

消费端：`insights_web.py:2307` `/accounts`、`:6163/6174` pair 端点、`:6209` heartbeat 端点、`:6215` `/agent/jobs/claim`、`:6243` job events；`templates/base.html:642` `agent_online` 全局判定驱动状态条。compose 选择器与发布入队/调度器同读全局视图。

### 2.2 扩展侧事实（零改动目标的依据）

配对客户端在**本仓库** `extension/`（`src/lib/protocol.ts`），不是 MultiPost-Extension 上游仓——tasks 4.1 的复核对象以此为准。已核验：redeem 期待 `{"token": string}`、token 全程不透明、heartbeat 载荷仅含每平台 `logged_in/probed` 布尔值（`orchestrator.ts:25-40` buildHeartbeat）**不含账号身份**、claim/events 形状与 D2/D8 兼容——设计层面零改动成立，build 阶段仍按 tasks 4.1 实测确认。

## 3. 目标 / 非目标 / 反目标

**目标**
- 每用户独立的扩展 token（可多个，多浏览器 profile）、登录态视图、平台账号记录、job 认领；互不可见、互不可用。
- 发布严格按帖主通道：他人通道不可降级兜底，通道不可用保持排队并标注原因。
- 存量零中断：owner 的已配对扩展与已连平台账号行为不变（划归 admin）。
- 扩展协议零改动。

**非目标**
- 不隔离调度时段（schedule_time_slots）与每日限额（platform_daily_limits）——全局资源，沿用 ① §3 约束。
- 不做浏览器 profile 自动管理（文档说明即可）。
- 不为 heartbeat 引入持久化（重启即失，维持现状语义）。
- 不改 MultiPost 扩展代码（若验证发现必须改，最小化并单独说明）。

**反目标**
- 任何"他人通道兜底发送"路径——违反隔离语义且易误发。
- token 明文落库。
- 扩展侧感知"用户"概念。

## 4. 已定决策（用户 2026-10-18 拍板）

### D1 扩展绑定形态：通用插件，谁配对归谁
配对码由哪个登录用户生成，redeem 换到的 token 即绑定该用户；同一浏览器换人使用 = 退出重配对。与既有「一号制」决策（切换=浏览器退出重登）同构。

### D2 token 模型：新表 `agent_tokens`，替代全局常量
```sql
CREATE TABLE IF NOT EXISTS agent_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,      -- sha256，落库前哈希（沿用 openspec design 决策 1）
    user_id INTEGER NOT NULL,
    label TEXT DEFAULT '',                -- 展示用（"owner 的 Chrome"）
    created_at TEXT NOT NULL,
    last_seen TEXT,                       -- 心跳时刷新
    revoked INTEGER DEFAULT 0
);
```
- 一人多 token（多浏览器 profile）；`/accounts` 列出本人 token 并可吊销（`revoked=1`）。
- `check_agent_auth()` 改为：读 Bearer → sha256 → 查表（未吊销）→ 解析出 user_id。返回值从 `bool` 变为「用户上下文或 None」——所有 `/agent/*` 端点随之改签名。
- 每 /agent/* 请求直查一次表（SQLite 本地，先不做缓存；量大再加内存缓存）。
- `agent_token()`（`agent_hub.py:29`）保留为**存量迁移专用**：仅启动迁移读取旧全局 token 值，运行时不再参与校验。

### D3 配对流程：铸造时绑定，兑换时继承
- `/agent/pair/code`（已登录会话）铸造配对码时绑定 `current_user`。
- `/agent/pair` redeem 端点保持匿名可调（扩展浏览器无会话），继承码的归属；兑换成功签发该用户的新 token 行。
- 码仍是内存单码、一次性、TTL 5 分钟、限 5 次尝试（沿用现状模型）——匿名 redeem 的安全性完全依赖短时效一次性码，现状已接受。

### D4 heartbeat：按 token→user 分桶
- 内存结构 `user_id -> {tool -> beat}`（token 解析出 user 后落入用户桶）；复用现有过期清理模式防泄漏。
- `platform_login_states()` / `login_states_view()` / `agent_online()` 增加 user_id 参数；`base.html` 状态条读当前用户。
- python agent（若仍存活）持旧全局 token 上报 → 归入 admin 名下，行为延续无泄露。

### D5 agent_jobs：补 user 维度，claim 只认领本人
- `agent_jobs` 加 `user_id` 列（启动迁移回填 admin）。
- `enqueue_agent_job` 带帖主 user_id；`claim_agent_job` 的 WHERE 追加 `user_id = token 解析出的用户`；kinds/platforms 过滤保留。
- 存量 job 划归 admin。

### D6 social_accounts：遗留数据隔离，不是通道判定依据（用户 2026-10-18 拍板修正）
事实修正：`upsert_social_account` 在生产代码中**没有任何调用方**——国内平台的「连接」从来就是浏览器登录 → 扩展 heartbeat 上报，从不落 social_accounts 行；心跳载荷也不含账号身份（无法自动建档出真 ID）。
- **通道判定只看本人 heartbeat 登录态**（登录即连接，与现状一致）；social_accounts 不再是发帖必要条件。
- 表仍做隔离迁移：加 `user_id` 列 + 唯一索引放宽为 `(platform, external_id, user_id)` + 存量行回填 admin——目的仅是遗留/展示数据不跨用户泄露。
- list/delete 按当前用户过滤；不新增建档机制（自动/手动均不做，本期限定）。

### D7 存量处置：全部划归 admin（用户拍板）
- 旧全局 `AGENT_TOKEN`：启动时若设置且 `agent_tokens` 无对应行 → 为 admin（第一个账号）签发等价 token 记录。已配对扩展不断线。
- 现有 `social_accounts` 行：回填 `user_id = admin`。
- 现有 `agent_jobs` 行：回填 `user_id = admin`。
- owner 零中断；新用户各自配对 + 连接。

### D8 边界：不隔离项与扩展协议
- 调度时段、每日限额保持全局；调度器改造限定在「通道解析」一层。
- 扩展协议零改动：redeem 响应形状不变；token 对扩展不透明。

### D9 Builder check 裁定（2026-10-18，写入即定稿）
1. **排队原因渲染时派生，不入库**：登录态是活的，存库会过期；队列页/卡片现场比对帖主本人 heartbeat 现算原因（"平台未连接/登录态异常"），零 schema 变更。immediate push 通道不可用时的 409 行为保持不变（本就内联报错）。
2. **agent job 读路径一并收口**：`/agent-jobs` 队列页（`insights_web.py:6197`，现全量返回、payload 含帖子正文——①之后已是跨用户泄露通道）与 `/agent-jobs/<id>.json` 按当前用户过滤（沿用 ① 内容页惯例：admin 同权不特判）；`/agent/jobs/<id>/events` 增加 job 归属校验（A 的 token 不得给 B 的 job 回报）。
3. **「全局调度器」= 入队/run_after 路径**：现状 `SCHEDULABLE_PLATFORMS = ()`（`insights_web.py:4621`）、`content_agent.SCHEDULABLE = set()`，不存在服务端调度器；tasks 3.3 落地为「enqueue/run_after 路径带帖主 user_id，扩展按归属认领」，不新建调度器。时段/限额保持全局在现状下为零工作量，确认即可不排任务。

## 5. 发布链路（改造后）

```
发帖（compose/入队）
  → 按 post.user_id 解析本人通道（= 本人 heartbeat 登录态，D6 修正）
  → 通道可用 → enqueue_agent_job(user_id=帖主)
  → 扩展 claim（token→user，只领本人 job）
  → 浏览器内发布 → events 回报（校验 job 归属）
  通道不可用 → queued job 保持排队；队列页渲染时派生标注原因（"平台未连接/登录态异常"），不借用他人通道
```

compose 平台/账号选择器：平台可用性 = 该用户 heartbeat 登录态，仅列本人可用平台。

## 6. 测试策略

双账号 E2E（isolated app + test client，沿用 ① 测试基建）：
1. 各自配对 → token 互不相通（A token 校验 401 于 B 语境）
2. A token 认领不到 B 的 job；A token 不能给 B 的 job 回报 events
3. heartbeat 登录态互不可见（A 离线不影响 B 在线显示）；/agent-jobs 与单 job 读取按用户过滤，跨用户 404
4. 遗留 social_accounts 行按用户过滤（B 看不到 admin 的存量行）；同平台同账号两用户各一行（唯一键放宽，DB 层验证）
5. 存量迁移幂等：全局 token 映射 admin 一次性；二次启动不重复回填
6. 吊销生效：revoked=1 后该 token 立即 401
7. B 通道（登录态）不可用时 A 帖子保持排队且页面渲染出原因标注，绝不发到 B 平台账号
8. 扩展零改动：本仓 extension/ 的 redeem/claim/events 载荷形状不变，双 token 双用户实测各领各的 job

## 7. 风险与兜底

| 风险 | 兜底 |
|------|------|
| /agent/* 每请求查表变慢 | SQLite 本地毫秒级；先直查，量大再加进程内缓存（记录在 tasks 注释，不做提前优化） |
| 心跳分桶内存泄漏（用户/token 无限增长） | 复用现有过期清理模式（window 外删除），admin token 吊销后桶随过期清理消失 |
| redeem 匿名端点被扫码 | 一次性 + 5 分钟 TTL + 5 次尝试上限（现状不变），风险已接受 |
| python agent 旧 token 上报 | 映射 admin，行为延续 |
| 扩展 redeem/claim/events 形状有出入（零改动目标破裂） | 设计层已核验兼容（§2.2）；build 按 tasks 4.1 实测本仓 extension/，必要时最小改动并单独说明 |
| 迁移破坏 owner 现有配对 | 迁移前备份 `insights.db`（openspec design Migration Plan）；全局 token 等价映射 admin 保证不断线 |

## 8. 交付物

- `database.py`：agent_tokens 建表 + social_accounts/agent_jobs 加列迁移 + 账号/job 函数签名扩展
- `agent_hub.py`：token 校验改查表解析用户、配对码绑定铸造者、heartbeat 分桶、登录态视图按用户
- `insights_web.py`：/agent/* 端点按 token 解析用户、/accounts 按用户过滤+token 管理、/agent-jobs 读路径按用户过滤、compose 选择器过滤、发布入队/run_after 按帖主
- `templates/`：accounts 页 token 列表/吊销 UI、base.html 状态条按用户、队列页渲染时派生通道原因标注
- delta spec：`openspec/changes/multi-user-publishing-channels/specs/user-publishing-channels/spec.md`
- 本仓 extension/（`src/lib/protocol.ts` 等）：目标零改动
