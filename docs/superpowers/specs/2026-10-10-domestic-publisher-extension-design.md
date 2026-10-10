# 国内渠道发布：自建浏览器扩展替换 wechatsync（设计）

日期：2026-10-10
状态：已评审（会话内四节设计逐节确认）
路径判定：architectural（替换国内发布子系统的执行端；服务器↔本机代理缝隙保持不变）

## 1. 背景与动机

现状：compose 页四个国内文章平台（公众号/知乎/头条号/小红书）经
服务器队列 → 本机 Python agent → `@wechatsync/cli` → Chrome 里的 wechatsync
扩展（9527 端口桥接 + MCP token）→ 平台草稿箱。

所有者对这条链路的不满：

1. 配置体验差：装第三方扩展、进它的设置开 MCP 连接拿 token、9527 桥接 token 明文
2. 第三方产品绑架：扩展/官网满是会员等无关功能，「是别人的站点」
3. GPL-3.0：虽为独立进程调用、法律上不传染，但依赖本身不受欢迎

调研结论（2026-10-10）：

- [MultiPost-Extension](https://github.com/leaperone/MultiPost-Extension)
  开源部分为 Apache-2.0，其 `src/sync/article/{weixin,zhihu,toutiao}.ts` 与
  `src/sync/dynamic/rednote.ts` 覆盖全部四个目标平台；weixin 适配器（609 行）
  为同源调用公众号内部 Web 接口（非 DOM 模拟）。**Apache-2.0 允许复制进自研
  扩展**（保留版权声明即可），我方代码不受传染。
- wechatsync 为 GPL-3.0，**不可复制代码**（参考思路可以，搬运不行）。
- 已否决备选：MultiPost 桌面客户端（仍是第三方全家桶，含闭源模块）；
  纯 Playwright UI 自动化（适配器从零写且维护全自扛）；仅公众号官方 API
  （所有者要求四平台全部自动化）。

## 2. 目标与非目标

### 目标

- 四个国内文章平台由 Insights 自有 MV3 浏览器扩展推进各平台草稿态，
  **零第三方运行时依赖**：无 wechatsync、无 MultiPost、无任何第三方账号或 token
- 复用现有 `/agent/*` 协议、任务队列、partial 语义、compose/队列 UI
- 对用户可见语义全部不变：草稿优先、人工确认发布、逐平台结果、
  partial 一等状态、定时按浏览器时区、失败分类驱动重试

### 非目标

- 视频链路（二期；本机 Python agent 保留为其地基）
- Chrome 商店上架（`extension/dist` 开发者模式加载，单用户足够）
- 国内多账号矩阵（粒度仍是平台，登录态模型为「每平台一个浏览器登录」）
- 一键配对码流程（设置页粘贴服务器地址 + token 即可；配对码留作后续增强）

## 3. 总体架构

```
┌─ Insights 服务器（现有 Flask 应用）────────────────────┐
│  compose 页 / 定时路径 → domestic_publisher 入队（打 executor 标记）│
│  agent_jobs 表、/agent/* 端点、重试与回收 —— 全部复用      │
└──────────────────────────────────────────┘
                 ▲ HTTPS 轮询（Bearer AGENT_TOKEN）
┌─ 所有者浏览器（MV3 扩展，开发者模式加载）─────┐
│  extension/                                 │
│   service worker：协议客户端 + 任务编排        │
│   content adapters：四平台适配器（搬运自 MultiPost）│
│   平台登录态 = 浏览器自身 Cookie，扫码登录一次     │
└─────────────────────────────────────┘
```

- **退役**：文章链路的 `WechatSyncDriver` 与本机 Python agent 轮询
  （代码保留不删——视频链路二期复用 agent 跑 CLI 类工具）
- **迁移**：毕业制（见 §9），期间扩展与 Python agent 并存于同一队列，
  靠 `executor` 标记 + claim 过滤互不抢任务
- 协议安全：搬运代码为 Apache-2.0（文件头 + NOTICE 留痕），其余为自有代码，
  服务器与扩展均无 GPL 涉及

## 4. 扩展设计

### 4.1 目录结构（MV3 + TypeScript + Vite，不引 Plasmo 框架）

```
extension/
  manifest.json               MV3
  src/background/service-worker.ts   协议客户端 + 任务编排（大脑）
  src/content/adapters/              四平台适配器（Apache-2.0 头）
    weixin.ts  zhihu.ts  toutiao.ts  rednote.ts
  src/content/bridge.ts              content script ↔ SW 消息桥
  src/lib/protocol.ts                /agent/* 客户端（heartbeat/claim/events）
  src/lib/settings.ts                chrome.storage.local：服务器地址 + AGENT_TOKEN
  src/options/                       设置页：配对、连接状态、平台登录绿点
```

职责边界：**SW 只管「什么时候、给谁、结果如何」；适配器只管「在平台页面
同源上下文里把草稿建出来」**。适配器以 content script 形态运行（Cookie 天然
可用、可调平台内部接口），SW 经消息递 payload、收回逐平台结果。

### 4.2 Service worker 行为

- **领取**：每 5 秒轮询 `/agent/jobs/claim`；MV3 SW 会休眠，30 秒 alarm 兜底
  唤醒；未配置/离线时指数退避
- **心跳**：30 秒一次 `POST /agent/heartbeat`，上报四平台登录态。登录检测
  凭 host 权限直接 fetch 平台首页看登录标记（与适配器读 token 同一招），
  **不开标签页**。60 秒无心跳 → compose 国内分组置灰（服务器现有逻辑不动）
- **执行纪律**：同一时刻一个 job；job 内逐平台串行（风控纪律不变）
- **标签页**：后台打开不抢焦点（`active: false`），该平台执行完即关闭，
  本地无残留

### 4.3 适配器

- 来源与搬运范围：MultiPost-Extension 开源仓库的
  `src/sync/article/{weixin,zhihu,toutiao}.ts`、`src/sync/dynamic/rednote.ts`
  及其依赖的公共转换引擎子集（markdown→富文本、图片管道）
- **草稿语义逐平台钉死**：公众号内部接口本就建草稿（`draft:true` 名副其实）；
  知乎/头条/小红书把上游的「发布」改为「存草稿」——每期里程碑验收时以
  平台后台人工确认为准，钉不死不许标 `draft:true`
- 小红书为图文笔记形态（平台无文章形态），markdown→笔记的内容取舍
  （封面/摘要/正文删减）在 M4 实现前先小样评审

### 4.4 设置页

服务器地址 + AGENT_TOKEN（掩码回显）+ 连接状态 + 四平台登录绿点/灰点 +
版本号。保存服务器地址时按需申请该来源的 optional host 权限。

## 5. 协议：完全复用，一个增量

三个 Token 端点原样复用（`heartbeat` / `jobs/claim` / `jobs/<id>/events`），
事件协议（started / done / failed）与 partial 语义一行不改。

**唯一增量——`executor` 标记**，解决迁移期双执行者并存：

- 现状每个 job 即单平台单 item（`enqueue_card_push` 按卡片行入队），故
  按平台路由即可：入队时按「平台→执行者」映射给 payload 打
  `executor: "extension"`（未打标 = 旧 agent）
- claim 请求体带能力清单 `{"executor": "extension", "platforms": [...]}`
  （已毕业平台集合），服务器只发匹配 job
- 四平台全部毕业后，入队默认打 `extension`，wechatsync 车道退役；
  标记字段保留

服务器侧改动清单（共四处小改）：

1. 入队打 executor 标记（新设置键 `DOMESTIC_EXECUTORS`，值为平台→执行者的
   JSON 映射，如 `{"wechat": "extension"}`；未配置 = 全走旧 agent）
2. claim 按 executor + 能力清单过滤
3. 心跳兼容扩展形状（`tool` 字段改为可选；扩展身份标识 `agent: "insights-ext"`）
4. 文案：「本机代理」→「浏览器扩展」（compose 置灰提示、`/agent-jobs`、设置页）

`AGENT_TOKEN`、队列、退避重试、claimed 超时回收、幂等去重：全部不动。

## 6. 错误分类（沿用现有 taxonomy，不发明新码）

| code | 扩展侧来源 | 重试？ |
|------|-----------|------|
| `needs_login` | 适配器检测到 Cookie 过期/登录跳转 | 否 |
| `sync_failed` | 平台接口拒绝、内容被拒、适配器内部异常 | 是 |
| `timeout` | 标签页/网络超时（适配器自带超时上限） | 是 |
| `unknown_target` | 收到未适配平台（claim 过滤后不应发生，防御性保留） | 否 |

`extension_offline` 无需上报：服务器心跳过期自行推出（现有逻辑）。

## 7. 安全边界

- **AGENT_TOKEN** 存 `chrome.storage.local`（扩展沙箱，页面 JS 不可读），
  仅发往配置的服务器地址（HTTPS）；服务器侧常量时间比对已有。
  扩展不含任何第三方 token
- **host 权限最小化**：四平台域名 + 服务器域名（`optional_host_permissions`
  按需申请）；MV3 纪律：无远程代码、无 eval，搬运代码全部打包
- content script 跑在 isolated world，只经 SW 消息进出
- **数据流不变**：文章内容只在服务器↔所有者浏览器之间传输；适配器同源请求
  只带平台自身 Cookie 打平台自身接口；第三方零涉及

## 8. 测试（沿用仓库 gate 文化）

| 层 | 手段 | 落点 |
|---|------|------|
| 服务器侧 | 纯 Python 门：claim 过滤、入队打标、心跳兼容、文案 | 扩充 `check_agent_hub.py` / `check_domestic_publisher.py` / `check_agent_push.py` |
| 扩展纯逻辑 | vitest：markdown 转换、登录标记解析、结果归一 | `extension/` 内 `npm test`，Python 包装脚本挂进 `run_all_gates.py` |
| 协议客户端 | 门脚本起 Flask test server，Node 跑编译后 `protocol.ts` 走 heartbeat→claim→events 全流程 | 新门 `scripts/check_extension_protocol.py` |
| 适配器 | 纯逻辑单测 + 真实响应 fixture 回放（解析回归）；真发一篇进草稿箱为人工真机门（对标 `INSIGHTS_AGENT_E2E=1`） | 每期里程碑验收清单 |

以上门全部注册进 `scripts/run_all_gates.py`。

## 9. 分期与退役（毕业制）

| 期 | 内容 | 验收标准 |
|---|------|---------|
| M1 | 扩展骨架 + 协议客户端 + 公众号适配器 | 心跳点亮；双执行者并存互不抢任务；真实推送→公众号后台草稿箱出现完整文章（标题/封面/正文图片/格式）；compose 逐平台结果正确 |
| M2 | 知乎专栏 | 推送到「存草稿」而非发布（平台后台人工确认）+ 真实推送通过 |
| M3 | 头条号 | 同 M2 |
| M4 | 小红书图文笔记 | 内容取舍小样评审通过 + 草稿语义确认 + 真实推送通过 |
| 通用 | — | Cookie 过期 → `needs_login`、不重试、compose 提示扫码 |

- **单平台毕业**：该平台在扩展上连续 3 次真实发布验收通过 →
  服务器设置翻转该平台执行者 → 从 wechatsync 车道摘除
- **全部毕业**：入队默认 executor 翻转为扩展；`WechatSyncDriver` 停用
  （代码保留）；`WECHATSYNC_ENABLED_PLATFORMS` 迁移改名为
  `DOMESTIC_ENABLED_PLATFORMS`（兼容读旧键）
- **清理**：四平台全毕业且稳定运行两周后，卸载 npm 包与旧扩展、清理设置项。
  毕业不等于删代码，回退路径始终存在

## 10. 交付形态

`extension/dist` 构建产物由所有者以开发者模式加载；上架商店为可选后续，
不在本期。构建产物不入库（`.gitignore` 追加 `extension/dist/`、`extension/node_modules/`）。
