# 平台登录状态条：写作页实时展示各站点登录态（设计）

日期：2026-10-13
状态：已评审（会话内设计逐项确认：位置、单状态语义、竞品词汇、点击修复、探测范围=缺啥补啥；原型 v3 已确认）
路径判定：architectural（跨扩展/服务端/页面三层，新增两侧共用的状态接口与心跳语义）

## 1. 背景与动机

2026-10-11 事故：推送按钮灰死、提示「推送离线」，用户无从知道原因
（扩展 storage 被清、未重新配对、无心跳），排查耗时远超必要。三个结构性缺口：

1. **登录状态无处可看**：`platform_login` 只影响推送按钮的禁用态
   （`templates/partials/post_item.html:186`），没有一处界面正面回答
   「公众号/小红书/抖音……现在能不能推」。
2. **扩展在说谎**：`orchestrator.ts:15` 对知乎/头条/小红书/抖音硬编码
   `logged_in: false` —— 没探测却报未登录。若以此画状态条，四个平台永远是假的「未登录」。
3. **检测能力与未登录不可区分**：`checkWeixinLogin` 把网络失败也折叠成
   `false`，语义上把「没检测」和「未登录」混为一谈。

设计原则（2026-10-13 会话确认）：

- 词汇对齐竞品，不自己造词：wechatsync 官方即「平台登录状态」
  （`wechatsync platforms --auth`、MCP `list_platforms`/`check_auth`）；
  蚁小二类为「账号授权/掉线/重新授权」。执行器、通道、agent、扩展
  **一律不进客户可见文案**（行话只留在悬停解释）。
- **每平台一个状态**，不按执行器分列：wechatsync CLI 经 Chrome 扩展桥接
  读取登录态，两条执行链路的登录态同源于同一个浏览器 —— 分列是伪差异。
- 每个非绿色状态必须带下一步动作（去登录 / 去恢复），不让用户面对无解释的灰。

## 2. 目标与非目标

### 目标

- 写作页顶部一行「平台登录状态」：公众号/知乎/头条号/小红书/抖音五枚 chip，
  30 秒轮询，实时反映登录态
- 状态语义四值：`ok`（已登录）/ `logged_out`（未登录，点击去登录）/
  `unknown`（暂不支持检测）/ `offline`（检测离线，点击去恢复）
- 扩展为全部五平台补真实登录探测（缺啥补啥）：探测失败归 `unknown`，
  与 `logged_out` 严格分开
- 新接口 `GET /agent/login-states.json`：服务端汇总两条心跳的上报，
  附最后上报时间与每平台登录页 URL
- 词汇统一：客户文案只说 平台/登录/已登录/未登录/去登录/检测；
  清理现有漏行话的文案（`post_item.html` 的「本机 agent」等）

### 非目标

- 不做自动登录、不存任何平台账号凭据（登录态仍然只是浏览器 Cookie）
- 不做多账号矩阵（粒度仍是「每平台一个浏览器登录」，与 2026-10-10 spec 一致）
- 状态条本期只上写作页；队列页等他处复用留到后续（接口已按复用设计）
- 不改推送链路本身（按钮禁用逻辑改为读同一份数据，属顺带统一，见 §7）

## 3. 数据模型与接口

### 3.1 心跳语义修正（扩展侧）

`tools.<tool>.platforms[]` 条目扩为 `{id, logged_in, probed}`：

- 探测成功 → `{id, logged_in: true|false, probed: true}`
- 探测网络失败/超时 → `{id, logged_in: false, probed: false}`
- 未实现探测的平台不上报（等价 `probed: false`）

`agent_hub.platform_login_states()` 随之升级为三值视图：
`{platform: {logged_in, probed, at}}`。多工具同报一平台时取
「最新且 probed 优先」：任何一条 probed 上报决定真假；全部未探测才是 unknown。
Python agent 通道（wechatsync `auth` 文本解析）保持原样，天然全是 probed。

### 3.2 状态接口

```
GET /agent/login-states.json        # 登录墙内，页面轮询
{
  "checked_at": 12,                  # 最新一条心跳距今秒数（驱动条头「刚刚检测/X 分钟前」）
  "platforms": {
    "wechat":      {"state": "ok",        "at": 12,  "login_url": "https://mp.weixin.qq.com/"},
    "zhihu":       {"state": "logged_out", "at": 20, "login_url": "https://www.zhihu.com/signin"},
    "toutiao":     {"state": "logged_out", "at": 20, "login_url": "https://mp.toutiao.com/"},
    "xiaohongshu": {"state": "unknown",   "at": null, "login_url": "https://creator.xiaohongshu.com/"},
    "douyin":      {"state": "unknown",    "at": null, "login_url": "https://creator.douyin.com/"}
  }
}
```

判定规则（服务端，单一函数，gate 直测）：

- `offline` 是**全局态**：60 秒心跳窗内无任何执行器在线 → 全平台 `offline`
  （`at` = null），不存在「部分平台离线」的混合态
- 有执行器在线：该平台有 probed 上报 → `ok`/`logged_out`；只有未探测上报或无上报
  → `unknown`；`at` = 该平台最新上报距今秒数
- `login_url` 是服务端固定映射（五个平台各一个），不是执行器给的 —— 与执行器解耦

### 3.3 五平台登录探测（扩展侧，缺啥补啥）

每个平台一个探测函数，签名统一 `check<Platform>Login(): Promise<"yes"|"no"|"unknown">`：

| 平台 | 探测思路 | 参考实现来源 |
|------|----------|--------------|
| 公众号 | `mp.weixin.qq.com` 首页 HTML 找 CSRF token（现状保留） | 自有 `lib/login.ts` |
| 知乎 | 带 Cookie 请求 `www.zhihu.com/api/v4/me`，200 即已登录 | MultiPost `sync/article/zhihu.ts` 的会话判定（Apache-2.0，可搬） |
| 头条号 | `mp.toutiao.com` 页内用户标记（与公众号同法） | wechatsync 思路（只参考不搬，GPL）；MultiPost `toutiao.ts` |
| 小红书 | `creator.xiaohongshu.com` 创作者接口会话检查 | MultiPost `sync/dynamic/rednote.ts`（Apache-2.0） |
| 抖音 | `creator.douyin.com` 会话接口；反爬最凶，失败归 `unknown` 不硬猜 | MultiPost `rednote.ts` 同族；一期允许长期 unknown |

公共约束：

- 30 秒内存缓存（现有 `loginCache` 模式延续），一次心跳至多一轮探测
- `PLATFORM_IDS` 扩为五平台；`checkLogins` 并发探测、单平台失败不影响其余
- 探测只读（GET，带 Cookie），绝不触碰任何写接口
- MultiPost 搬运文件带 Apache-2.0 头 + NOTICE 留痕（沿用 2026-10-10 spec 的合规做法）

## 4. UI：写作页状态条

落点：`/compose` 内容区最上方、生成器卡片之上，独立一行
（原 `connection_strip.html` 已在 UI 重构中移除，本设计不复活它，新建
`templates/partials/login_status_strip.html`）。样式沿用工作区现行浅色 token。

```
账号登录   3 个平台可推送 · 刚刚检测        [公众号✓] [知乎✓] [头条号 去登录↗] [小红书 暂不支持检测] [抖音 暂不支持检测]
```

- chip 四态与动作：
  - `ok` → 绿 chip「平台名」，无动作，悬停=「已登录（X 秒前检测）」
  - `logged_out` → 红 chip，悬停浮出「去登录 ↗」，点击新标签打开 `login_url`
  - `unknown` → 灰 chip「暂不支持检测」，无动作，悬停解释「该平台暂未支持登录检测」
  - `offline` → 虚线灰 chip，悬停浮出「去恢复 ↗」，点击去 `/agent-jobs`（配对入口所在）
- 条头摘要：可推送计数 + 检测新鲜度（「刚刚检测」/「X 分钟前」）；
  全部 `offline` 时摘要变红「检测离线 · 无法确认登录状态」
- 轮询：30 秒一次 `login-states.json`，页面隐藏（`document.hidden`）时暂停
- 悬停 tooltip 保留排障细节（哪条执行链路上报、几秒前）——行话只在这里出现
- chip 本期不绑定卡片滚动（原连接条行为依赖已删除的徽章结构，无对象可滚）

推送按钮的禁用逻辑顺带统一：`post_item.html` 改读 `login-states` 同源数据，
禁用提示语换成词汇表用语（「公众号未登录，去登录」/「检测离线」），
删除「本机 agent」「浏览器扩展」字样。

## 5. 词汇表与文案清理

| 用 | 不用 |
|----|------|
| 平台登录状态 / 账号登录 | 连接状态、执行器状态 |
| 已登录 / 未登录 / 去登录 | 授权、掉线、配对（客户语境） |
| 检测 / 暂不支持检测 | 探测、心跳、上报 |
| 浏览器里的发布助手（悬停解释） | 本机 agent、agent、扩展（客户可见文案） |

清理点：`post_item.html:182-201`、`compose.html:878` 附近提示语、
`agent_jobs.html` 配对按钮旁说明（该页是排障页，行话可保留）。

## 6. 测试

- **gate（Python）**：`scripts/check_login_states.py` 新增 —— 三值合并逻辑、
  60s 窗、offline 判定、`login_url` 映射；并入 `run_all_gates.py`。
  `check_agent_hub.py` 补 `probed` 字段兼容（旧心跳无 `probed` 视为 probed=true，
  保持向后兼容）
- **gate（UI）**：`check_agent_ui.py` 或新脚本断言 compose 页渲染状态条
  partial、四态 class、文案不含行话
- **扩展（vitest）**：五平台探测解析函数逐个单测（HTML/JSON fixture）；
  `probed` 心跳形状进 `orchestrator.test.ts`
- **E2E（手动）**：登录/登出公众号观察 30s 内变绿/变红；停扩展观察全灰 +
  「去恢复」；断网观察 `unknown`（区别于未登录）

## 7. 风险与分期

- **抖音反爬**：会话检查可能不稳定 —— 允许抖音长期 `unknown`，不阻塞整体上线
- **平台改版**：探测标记随页面结构失效 —— 失效表现为 `unknown`（不误报未登录），
  可接受；逐平台修复
- **并行会话的未提交 UI 重构**：本设计落在其上（连接条已删、浅色 token 已就位）；
  实现前先确认工作区已提交或保持稳定，避免踩动对方改动
- 分期：一期 wechat/zhihu/toutiao/xiaohongshu 探测 + 状态条 + 接口；
  抖音探测与按钮禁用统一可在二期收尾
