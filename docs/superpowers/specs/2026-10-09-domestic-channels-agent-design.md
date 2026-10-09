# 国内渠道发布：本机代理架构设计（第一期：文章链路）

日期：2026-10-09
状态：待评审
路径判定：architectural（新子系统：服务器↔本机代理、任务队列、外部工具驱动层）

## 1. 目标与约束

### 目标

把 Insights 的发布范围从 5 个海外 OAuth 平台扩展到国内渠道。第一期只做**文章链路**：
compose 页生成的文章可以一键推进 公众号 / 知乎 / 头条号 等 29+ 文章平台的**草稿箱**，
支持定时，支持逐平台结果反馈。

### 约束（已与所有者确认）

- **不自己写平台客户端**。国内渠道一律复用开源 RPA 工具；第一期选定
  [Wechatsync](https://github.com/wechatsync/Wechatsync)（GPL-3.0，Chrome 扩展 + CLI/MCP，
  用浏览器登录态调平台官方 Web 接口，草稿优先）。
- **部署形态是混合**：Insights 跑在服务器，RPA（浏览器 + 登录态 + CLI）只能跑在所有者的
  本机 Windows。服务器无法直接 spawn 本机进程。
- **架构一次到位，渠道分期**。本期的「服务器排队 + 本机执行 + 结果回传」代理缝隙是后续
  视频渠道（MatrixMedia、social-auto-upload）的同一块地基。
- 协议安全：Insights 是 MIT。对 GPL 工具只做**独立进程的 CLI 调用**（arm's length），
  不复制其源码、不链接其库，无传染。

### 非目标（本期不做）

- 视频渠道（抖音/B站/视频号等）——由 `kind` 字段预留，MatrixMedia/sau 驱动后续接入
- 国内多账号矩阵——wechatsync 的登录态模型是「每平台一个浏览器登录」，无账号概念
- 自动最终发布——wechatsync 语义是草稿箱，最终发布由人在各平台确认（这是合规优点，
  也如实写进 UI 文案）
- MultiPost 扩展桥、备用驱动——列为未来增强

## 2. 工具选型依据（2026-10 实测调研）

| 候选 | 协议 | 覆盖 | 外部调用 | 状态 |
|------|------|------|---------|------|
| Wechatsync | GPL-3.0 | 29+ 文章平台（公众号/知乎/头条/掘金/CSDN/微博/小红书/抖音图文/B站专栏…） | CLI（`@wechatsync/cli`）、MCP、JS SDK | 月更（v2.0.9, 2026-03），机制最稳：Cookie 调平台官方 Web 接口，非 UI 模拟 |
| MatrixMedia | GPL-2.0 | 8 平台视频（含视频号/百家号） | CLI（退出码+`--json`）、HTTP API、MCP | 活跃，第二期视频首选 |
| social-auto-upload | MIT | 抖音/B站/小红书/快手视频+图文 | CLI `sau` | 13k⭐ 但 2026-03 起重构中途，锁 tag 后再作为补充驱动 |

关键事实：RPA 工具的宿命是平台改版即断。因此**驱动必须可替换**——每个渠道对应一个
驱动类，工具死了换驱动映射，不动核心。失败必须如实回传，不假装成功。

## 3. 架构总览

```
┌─ Insights 服务器（现有 Flask 应用）────────────────────┐
│  compose 页（现有）                                     │
│    海外平台 → publish_targets() 同步发布（不动）          │
│    国内文章平台 → domestic_publisher.enqueue() 入队      │
│  agent_hub.py（新）: /agent/* 端点 + Token 鉴权          │
│  agent_jobs 表（新）: 任务队列 + 逐平台结果               │
└──────────────────────────────────────────────┘
                 │ HTTPS 轮询（纯出站，Bearer Token）
┌─ 本机 Windows ────────────────────────────────┐
│  agent/（新目录，python -m agent）                │
│    api.py      拉任务 / 回事件                     │
│    drivers.py  驱动注册表 + wechatsync 驱动        │
│  前置条件: Chrome + wechatsync 扩展 + 桥接 Token     │
└───────────────────────────────────────────────┘
```

新增部分：

- 服务器：`agent_hub.py`（端点+鉴权+队列操作）、`domestic_publisher.py`（入队入口，
  供 compose 路由与定时路径调用）、`agent_jobs` 表、appconfig 设置项
- 本机：`agent/` 独立小包，只依赖标准库 + wechatsync CLI，不在服务器上运行
- 现有代码的改动面：compose 页目标选择器加「国内文章」分组与结果轮询；compose 发布
  路由按目标类型分叉（海外同步 / 国内入队）；`/agent-jobs` 队列与历史页

## 4. 数据模型

### agent_jobs 表

一次发布一个 job，一个 job 含多个目标平台：

```sql
CREATE TABLE agent_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'article_sync',   -- article_sync | video_publish（预留）
    payload TEXT NOT NULL,        -- JSON: {title, items: [{target, content}], article_id}
                                  -- content 为该平台的定制文案（markdown/纯文本），不是一稿多发
    status TEXT DEFAULT 'pending',-- pending|claimed|succeeded|partial|failed|expired
    run_after TEXT,               -- 定时 / 退避重试的时间门
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    result TEXT,                  -- JSON: 逐平台结果数组
    claimed_by TEXT, claimed_at TEXT,
    article_id INTEGER,           -- 可回链 articles 表
    created_by INTEGER,           -- 用户
    created_at TEXT, updated_at TEXT
)
```

- 文章 HTML 内嵌在 payload（文本量级几十 KB），MVP 不做签名下载 URL；视频 job 后续需要
  media URL + agent 下载
- `partial` 是一等状态：N 个目标成功 M 个（M<N）就是 partial，逐平台结果说话
- 入队幂等按 lane 判定：`enqueue_card_push` 只拦同 payload、仍在途（`pending|claimed|running`）
  且 `run_after IS ?` 同车道的重复推送——立即推送与定时推送互不去重；job 结束后可再次推送

### 设置项（appconfig.py 注册表，新分组「国内渠道」）

| 键 | 说明 |
|---|---|
| `AGENT_TOKEN` | agent↔服务器共享密钥（`secret: True`，设置页掩码） |
| `AGENT_POLL_SECONDS` | agent 轮询间隔，默认 5 |
| `WECHATSYNC_ENABLED_PLATFORMS` | 启用的文章平台，默认 `weixin,zhihu,toutiao` |

agent 侧环境变量：`INSIGHTS_SERVER_URL`、`INSIGHTS_AGENT_TOKEN`（与服务器一致）、
可选 `WECHATSYNC_TOKEN`（缺省继承 wechatsync CLI 自己的环境）。

### 不建国内账号模型

MVP 目标粒度是**平台**不是账号（wechatsync 每平台一个浏览器登录）。登录态通过 agent
心跳透传展示，不写 `social_accounts` 表。

## 5. Agent ↔ 服务器 API

所有 `/agent/*` 端点要求 `Authorization: Bearer <AGENT_TOKEN>`，
`hmac.compare_digest` 常量时间比对；豁免登录态（token 即凭证），不影响其余路由。

| 端点 | 方法 | 作用 |
|------|------|------|
| `/agent/heartbeat` | POST | 版本、工具可用性、各平台登录态。服务器缓存最近心跳；**60 秒无心跳 → compose 国内目标置灰** |
| `/agent/jobs/claim` | POST | 原子认领：`BEGIN IMMEDIATE` 事务内先回收超时 `claimed`，再 `SELECT id ... ORDER BY id LIMIT 1` + `UPDATE ... SET status='claimed'` 两步完成（不依赖 SQLite 的 `RETURNING`）；无任务 204 |
| `/agent/jobs/<id>/events` | POST | 执行事件与最终结果 |
| `/agent-jobs/<id>.json` | GET | 单任务状态（**登录态**；compose 页轮询渲染结果用） |
| `/agent-jobs` | GET | 队列与历史页面（登录态） |

UI 轮询端点走登录态而不是 agent Token：它们是页面的一部分；agent 面只有上面三个 Token 端点。

### 事件协议

```jsonc
// started —— 认领后立刻回
{"type": "started", "agent": "layne-win", "tool": "wechatsync 2.0.9"}

// done —— 最终结果，形状对齐 social_publisher._result() + 草稿语义
{"type": "done", "status": "partial", "results": [
  {"platform": "wechat",  "success": true,  "draft": true, "detail": "已保存到公众号草稿箱"},
  {"platform": "zhihu",   "success": true,  "draft": true, "detail": "已保存到知乎草稿箱"},
  {"platform": "toutiao", "success": false, "draft": false,
   "error": "头条号未登录", "code": "needs_login"}
]}

// failed —— agent 侧整体失败，服务器按退避重试
{"type": "failed", "error": "wechatsync CLI 不可用", "code": "tool_missing", "retryable": false}
```

- `code: needs_login` 对齐现有 `needs_reconnect` 的语义位：重试无用，人要去本机浏览器
  登录，服务器对这类目标不重试，UI 引导登录
- `draft: true` 固定事实：成功 = 已进草稿箱，不是已发布

## 6. Agent 内部与驱动抽象

### 驱动接口（agent/drivers.py）

```python
class Driver(Protocol):
    name: str                                    # "wechatsync"
    def available(self) -> ToolStatus             # CLI 在不在、扩展桥通不通、版本
    def platforms(self) -> list[PlatformInfo]     # [{id, label, logged_in}] —— 心跳用
    def publish_article(self, job) -> list[dict]  # 逐平台结果，形状同事件协议 done
    # def publish_video(self, job) -> list[dict]  # 预留：MatrixMedia / sau 实现
```

### wechatsync 驱动执行流程

1. **预检**：`wechatsync platforms --auth` 解析 JSON → 目标平台登录态；未登录平台直接
   返回 `needs_login`，不浪费一次 sync
2. **发布**：HTML 写临时文件（`%TEMP%/insights-agent/<job_id>.html`）→
   `wechatsync sync <file> -p weixin,zhihu -t <标题>`（超时 300s，`shell=False`）
3. **解析**：CLI 输出 + 退出码 → 逐平台成败；崩溃/超时 → 整体 retryable 失败；
   部分成功 → partial，**已成功平台不重发**（服务器重试时 payload.targets 剔除已成功项）。
   `@wechatsync/cli` 的输出结构以实现第一步的 spike 实测为准（跑一次真实 sync 记录
   stdout/退出码形态），驱动解析器钉死在该 schema 上并配 fixture
4. **清理**：`finally` 删临时文件；检测到 CLI 要人工交互（扫码/验证码）→ 判
   `interactive_prompt` 失败，提示到本机手动完成一次登录

### 执行纪律

- 同一时刻只跑一个 job（平台风控敏感，串行最稳）
- agent 无状态：崩溃重启后重新 poll；`claimed` 超 15 分钟无结果由服务器回收为 pending
- 不追工具上游：部署时锁定 wechatsync 版本（sau 尤其如此）

## 7. 与现有链路的整合

- **compose 页**：目标选择器新增「国内文章」分组（绿点=已登录，来自心跳）。点「立即发布」：
  海外目标照旧 `publish_targets()` 同步返回；国内目标入队（`run_after=now`），页面拿
  job id 轮询 `/agent/jobs/<id>` 渲染逐平台结果。一个按钮后面两条路径，用户不感知分叉。
  结果文案：「✓ 已推入公众号草稿箱，请到微信公众平台确认发布」
- **定时发布**：国内目标**不进** `scheduled_posts` 表（混入 weixin 会让
  `scheduled_post_worker` 撞上 "cannot be published to"）。定时创建国内目标直接写
  `agent_jobs`（`run_after=定时时间`），在 `/agent-jobs` 队列面板展示。海外定时流不动。
- **离线降级**：心跳超 60 秒 → compose 国内分组置灰 + 「本机代理离线，请启动 agent」；
  已入队任务不受影响，agent 上线后照常领取
- **平台名映射**：仓库平台 ID 沿用工作区现状（`wechat`/`xiaohongshu`/`douyin`，新增
  `zhihu`/`toutiao`），显示名 wechat→公众号、zhihu→知乎、toutiao→头条号。驱动层维护
  仓库 ID → wechatsync ID 的映射（`wechat→weixin`，其余同名），以 wechatsync 平台表为准
- **工作区锚点（未提交改动已预留）**：`COMPOSE_PLATFORMS = ('xiaohongshu', 'douyin',
  'wechat')`；`DRAFT_ONLY_PLATFORMS` 三平台在卡片上保留发布占位按钮（文案「一键发布
  即将支持」）；compose 页渠道说明写明「将接入开源发布工具」。本期兑现 wechat/zhihu/
  toutiao 的占位按钮为真实推送；xiaohongshu/douyin 维持手动复制（笔记/短文案不是
  wechatsync 文章链路的形态，留给二期）

## 8. 错误处理与安全

### 失败分类（决定重试策略）

| 错误码 | 含义 | 重试？ |
|--------|------|--------|
| `tool_missing` | 本机没装 wechatsync CLI | 否 |
| `extension_offline` | Chrome/扩展没开，桥接不通 | 否 |
| `needs_login` | 目标平台未登录 | 否（needs_login 语义位） |
| `sync_failed` | 平台接口拒绝/内容被拒 | 是 |
| `timeout` | CLI 超 300s | 是 |
| `interactive_prompt` | CLI 要人工交互 | 否 |

### 服务器侧重试

`failed` 且 retryable → `attempts+1`，退避 `1m→5m→15m→60m`，`max_attempts=5` 后标
`failed` 留档；`claimed` 超 15 分钟回收为 pending（agent 崩溃自愈）。

### 安全边界

- agent 纯出站 HTTPS + Bearer Token（常量时间比对）；不用 shell 拼接（subprocess 数组参数）
- 临时文件用完即删；token 不进日志；`AGENT_TOKEN` 设置页掩码
- 文章内容只在服务器↔本机之间传输，不落第三方

## 9. 测试（沿用仓库 gate 脚本文化）

| 脚本 | 覆盖 |
|------|------|
| `scripts/check_agent_hub.py` | Flask test client：鉴权拒绝、原子认领不重复、事件改状态、partial 语义、退避重试、claimed 超时回收、心跳过期 |
| `scripts/check_agent_drivers.py` | 假 subprocess + 输出 fixture：六种失败分类解析、部分成功不重发、临时文件清理、平台列表解析 |
| `scripts/check_agent_ui.py` | compose 国内分组渲染、离线置灰、草稿文案、逐平台结果展示 |
| `scripts/check_agent_e2e.py` | 真机门（`INSIGHTS_AGENT_E2E=1` 才跑，默认跳过）：真 CLI 推测试文到公众号草稿 |
| `regression_suite.py` | 注册以上全部（e2e 除外） |

## 10. 后续路线（不在本期）

1. **第二期·视频链路**：MatrixMedia 驱动（`kind=video_publish`，CLI `matrixmedia cli
   publish` / HTTP API，视频号/百家号/头条视频）+ sau 驱动（抖音/B站/小红书视频，锁 tag）
2. **备用驱动**：MultiPost 扩展桥（Apache-2.0）作为部分渠道的替换路径；compose 页
   「立即手动发」按钮走扩展 API
3. **国内渠道进统一 fan-out**：当驱动稳定后，把国内目标并入 `publish_targets()` 的
   归一化结果流（queued 语义）
4. 定时策略升级：错峰发布、平台级时间窗
