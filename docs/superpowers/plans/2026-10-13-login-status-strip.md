# Login Status Strip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 写作页顶部一行「平台登录状态」，五个国内平台每平台一个真实状态（已登录/未登录/暂不支持检测/检测离线），每个非绿状态带下一步动作；扩展为五平台补真实登录探测，心跳不再对没探测的平台硬报 `false`。

**Architecture:** 服务端把两条心跳链路（扩展 + python agent）的上报合并成三值视图（`agent_hub.platform_login_states()` → `login_states_view()`），经新端点 `GET /agent/login-states.json` 交给写作页的状态条 partial（服务端注入初始 JSON + 客户端 30s 轮询）。扩展侧新增 `lib/platform-login.ts` 探测库，`orchestrator` 的心跳 platforms 条目扩为 `{id, logged_in, probed}`。

**Tech Stack:** Flask + Jinja（服务端）、原生 JS（状态条轮询）、TypeScript MV3 扩展（vitest + esbuild）、仓库自有 gate 脚本体系（`scripts/_accounts_gate.py` 的 `isolated_app`/`check`）。

**Spec:** `docs/superpowers/specs/2026-10-13-login-status-strip-design.md`（本计划逐条实现它；词汇表见 spec §5，状态判定规则见 §3.2，探测思路见 §3.3）

## Global Constraints

- 运行 Python gate 一律用 venv 解释器：`venv/Scripts/python.exe scripts/<gate>.py`（plain `python` 缺 flask）
- 客户可见文案只允许：平台 / 登录状态 / 已登录 / 未登录 / 去登录 / 检测 / 暂不支持检测 / 检测离线；`agent`、`扩展`、`本机 agent`、`执行器`、`通道`、`心跳` 不进客户可见文案（spec §5；`agent_jobs.html` 排障页与代码注释不受限）
- 状态四值精确为：`ok` / `logged_out` / `unknown` / `offline`；`offline` 是全局态（无任何心跳在线），不存在部分平台 offline 的混合态
- 「未检测 ≠ 未登录」：探测网络失败一律归 `unknown`（扩展侧 `"unknown"`，服务侧 `probed: false`），绝不折叠成 `logged_out`
- 每平台 `login_url` 固定五条：wechat→`https://mp.weixin.qq.com/`，zhihu→`https://www.zhihu.com/signin`，toutiao→`https://mp.toutiao.com/`，xiaohongshu→`https://creator.xiaohongshu.com/`，douyin→`https://creator.douyin.com/`
- 探测只读（GET 带 Cookie），绝不触碰任何平台写接口
- MultiPost/Apache-2.0 代码可搬（留文件头 + NOTICE 留痕）；wechatsync GPL 只参考思路不搬代码
- 提交不带任何 attribution trailer；作者是 l0lsec + `51931886+l0lsec@users.noreply.github.com`（已配置）
- 工作区里有另一会话的未提交 UI 重构 —— Task 1 先整体提交它们，之后每个任务只 `git add` 自己的文件

## Review Focus

按 spec 推断、但没有任何任务测试直接覆盖、最容易咬到真实用户的五类输入（每条已钉进所属任务的测试步骤）：

1. **两个执行器同报一平台、一真一假** —— probed 的上报必须赢过 unprobed 的，即使它更旧（Task 2 Step 1/4）
2. **旧格式心跳（无 `probed` 字段，今天的 python agent 就是）** —— 必须读作 `probed: true`，绝不能因字段缺失把 agent 的真探测降级成 unknown（Task 2 Step 1/4）
3. **心跳年龄恰好在 60 秒窗边界** —— `age > 60` 才算离线，边界内仍在线（Task 3 Step 1/4）
4. **一轮探测里单个平台网络失败** —— 只该平台归 unknown，其余平台真状态保留，且失败结果 30 秒缓存内不得反复重试（Task 7 Step 1/4）
5. **心跳在线但 body 无 tools** —— 全平台 `unknown`、摘要显示「0 个平台可推送」，而不是误显示「检测离线」（Task 3 Step 1/4）

---

### Task 1: 先落地并行会话的未提交 UI 重构（预飞任务）

**Files:**
- Commit only（无新代码）：工作区现有改动（`templates/*.html`、`templates/partials/connection_strip.html` 删除、`static/*.svg`、`extension/src/shared/pairing-view.ts`、`scripts/check_agent_ui.py`）

**Interfaces:**
- Consumes: 无
- Produces: 干净的工作区 —— 后续任务的提交不再混入他人改动

- [ ] **Step 1: 跑全量 gate 作为基线**

Run: `venv/Scripts/python.exe scripts/run_all_gates.py`
Expected: 全部 OK 结尾（每个 gate 打印自己的 `*_OK`）。若有红：**停止**，向 owner 报告哪些 gate 红——那是并行会话遗留的问题，不属于本计划修的范围。

- [ ] **Step 2: 整体提交工作区现有改动**

```bash
git add -A
git commit -m "UI: light restyle across shell pages, compose cards and pairing view; drop the accounts strip"
git log --oneline -1
```

Expected: 一个提交，工作区 clean（`git status --short` 为空）。

---

### Task 2: agent_hub 三值登录视图（心跳 probed 语义）

**Files:**
- Modify: `agent_hub.py:67-76`（`platform_login_states`）
- Modify: `scripts/check_agent_hub.py:119-129`（断言适配新形状）

**Interfaces:**
- Consumes: 现有 `heartbeat_snapshot()`（`{"age": float, "tools": {tool: {"platforms": [{"id", "logged_in", "probed"?}]}}}`）
- Produces: `platform_login_states() -> dict[str, dict]`，每条 `{"logged_in": bool, "probed": bool, "age": float}`。Task 3 的 `login_states_view()` 与 Task 5 的按钮禁用逻辑都读它

- [ ] **Step 1: 在 check_agent_hub.py 写失败断言（含 Review Focus 1、2）**

把 `scripts/check_agent_hub.py` 第 119-129 行的形状断言替换为：

```python
    # An extension-shaped heartbeat (tools keyed by the extension's name)
    # feeds the same login-state view. A platform entry without `probed`
    # reads as probed: the field post-dates the python agent's heartbeat,
    # and that agent's auth report is a real probe (Review Focus 2).
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True, "version": "0.1.0",
            "platforms": [{"id": "wechat", "logged_in": True}]}}})
    state = agent_hub.platform_login_states().get("wechat") or {}
    check(state.get("logged_in") is True and state.get("probed") is True,
          "a platform entry without probed still reads as a real probe")
    client.post("/agent/heartbeat", headers=AUTH, json={"agent": "x"})
    check(agent_hub.platform_login_states() == {},
          "a heartbeat without tools yields empty login states, not a crash")
```

Run: `venv/Scripts/python.exe scripts/check_agent_hub.py`
Expected: FAIL —— 现在 `platform_login_states()` 返回 `{pid: bool}`，`.get("logged_in")` 取不到（断言为 False）。

- [ ] **Step 2: 实现三值合并**

替换 `agent_hub.py` 的 `platform_login_states()`（第 67-76 行）为：

```python
def platform_login_states() -> dict:
    """``{platform: {"logged_in": bool, "probed": bool, "age": float}}``.

    Probed reports beat unprobed ones even when they are older — "we really
    checked" outranks "someone guessed" (spec §3.1). Within one class the
    freshest heartbeat wins. An entry without ``probed`` reads as probed:
    the field post-dates the python agent's heartbeat, whose auth report is
    a real probe.
    """
    snap = heartbeat_snapshot() or {}
    age = snap.get("age", 0.0)
    tools = snap.get("tools") or {}
    merged: dict = {}
    for tool in tools.values():
        for entry in (tool or {}).get("platforms") or []:
            if not (isinstance(entry, dict) and entry.get("id")):
                continue
            probed = bool(entry.get("probed", True))
            candidate = {"logged_in": bool(entry.get("logged_in")),
                         "probed": probed, "age": age}
            current = merged.get(entry["id"])
            if (current is None
                    or (probed and not current["probed"])
                    or (probed == current["probed"] and age < current["age"])):
                merged[entry["id"]] = candidate
    return merged
```

- [ ] **Step 3: 跑 gate 确认变绿**

Run: `venv/Scripts/python.exe scripts/check_agent_hub.py`
Expected: PASS，打印 `AGENT_HUB_OK`。

- [ ] **Step 4: 补一条合并优先级回归（Review Focus 1），仍然绿**

在 `check_agent_hub.py` 的「a heartbeat without tools…」断言之后追加：

```python
    # Review Focus 1: a probed report beats an unprobed one even when older.
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True,
            "platforms": [{"id": "zhihu", "logged_in": False, "probed": True}]}}})
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "layne-win", "tools": {"wechatsync": {
            "available": True,
            "platforms": [{"id": "zhihu", "logged_in": True, "probed": False}]}}})
    zhihu = agent_hub.platform_login_states().get("zhihu") or {}
    check(zhihu.get("logged_in") is False and zhihu.get("probed") is True,
          "a probed report wins over a fresher unprobed one")
```

Run: `venv/Scripts/python.exe scripts/check_agent_hub.py`
Expected: PASS，`AGENT_HUB_OK`。

- [ ] **Step 5: Commit**

```bash
git add agent_hub.py scripts/check_agent_hub.py
git commit -m "Agent hub: login states carry probed, probed beats unprobed, fresh beats stale"
```

---

### Task 3: login_states_view + /agent/login-states.json + 专属 gate

**Files:**
- Modify: `agent_hub.py`（在 `platform_login_states` 之后追加 `LOGIN_STATE_URLS`、`login_states_view`）
- Modify: `insights_web.py:10201-10206`（`agent_job_json` 路由之后加新路由）
- Create: `scripts/check_login_states.py`
- Modify: `scripts/run_all_gates.py:60-74`（`AGENT_GATES` 表注册）

**Interfaces:**
- Consumes: Task 2 的 `platform_login_states()`、现有 `agent_online()`、`heartbeat_snapshot()`
- Produces: `agent_hub.LOGIN_STATE_URLS: dict[str, str]`；`agent_hub.login_states_view() -> {"checked_at": int|None, "platforms": {pid: {"state": str, "at": int|None, "login_url": str}}}`；路由 `GET /agent/login-states.json`（登录墙内）。Task 4 的状态条与 Task 5 的按钮读这个形状

- [ ] **Step 1: 写失败的 gate（含 Review Focus 3、5）**

创建 `scripts/check_login_states.py`：

```python
"""Gate for the login-states view and its endpoint.

    python scripts/check_login_states.py    LOGIN_STATES_OK

The strip's data merges every executor's heartbeat: probed reports beat
unprobed ones, the freshest wins within a class, and a dead heartbeat turns
every platform offline — never a stale "logged in".
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402

AUTH = {"Authorization": "Bearer secret-token"}


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub
    database.set_setting("AGENT_TOKEN", "secret-token")

    # --- no heartbeat at all: every platform offline, urls still present ---
    data = client.get("/agent/login-states.json").get_json()
    check(data["checked_at"] is None, "without a heartbeat checked_at is null")
    for pid, entry in data["platforms"].items():
        check(entry["state"] == "offline",
              f"{pid} is offline without any heartbeat")
        check(bool(entry["login_url"]), f"{pid} still carries its login url")

    # --- a live heartbeat: per-platform truth, unknown where not probed ---
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True,
            "platforms": [
                {"id": "wechat", "logged_in": True, "probed": True},
                {"id": "zhihu", "logged_in": False, "probed": True},
                {"id": "douyin", "logged_in": False, "probed": False}]}}})
    data = client.get("/agent/login-states.json").get_json()
    check(data["platforms"]["wechat"]["state"] == "ok",
          "a probed logged-in platform reads ok")
    check(data["platforms"]["zhihu"]["state"] == "logged_out",
          "a probed logged-out platform reads logged_out")
    check(data["platforms"]["douyin"]["state"] == "unknown",
          "an unprobed report reads unknown, never logged_out")
    check(data["platforms"]["xiaohongshu"]["state"] == "unknown",
          "a platform nobody reported reads unknown")
    check(isinstance(data["checked_at"], int) and data["checked_at"] < 5,
          "checked_at is the freshest heartbeat age in seconds")

    # Review Focus 5: online but tools-less — unknown, not offline.
    client.post("/agent/heartbeat", headers=AUTH, json={"agent": "bare"})
    data = client.get("/agent/login-states.json").get_json()
    check(all(e["state"] == "unknown" for e in data["platforms"].values()),
          "an online tools-less heartbeat yields unknown everywhere")
    check(data["checked_at"] is not None,
          "an online tools-less heartbeat still stamps checked_at")

    # Review Focus 3: the 60s window boundary — inside is online.
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True,
            "platforms": [{"id": "wechat", "logged_in": True, "probed": True}]}}})
    agent_hub._LAST_HEARTBEAT["t"] = time.time() - 59.5
    check(client.get("/agent/login-states.json").get_json()
          ["platforms"]["wechat"]["state"] == "ok",
          "59.5s-old heartbeat is still online")
    agent_hub._LAST_HEARTBEAT["t"] = time.time() - 60.5
    data = client.get("/agent/login-states.json").get_json()
    check(all(e["state"] == "offline" for e in data["platforms"].values()),
          "60.5s-old heartbeat turns every platform offline")
    check(client.get("/agent/login-states.json").get_json()["checked_at"] is None,
          "an offline heartbeat nulls checked_at")

    print("LOGIN_STATES_OK")


if __name__ == "__main__":
    main()
```

Run: `venv/Scripts/python.exe scripts/check_login_states.py`
Expected: FAIL —— `login_states_view`/路由尚不存在（AttributeError 或 404）。

- [ ] **Step 2: 实现 agent_hub 侧**

在 `agent_hub.py` 的 `platform_login_states()` 之后追加：

```python
# The strip's "go log in" targets: fixed server-side, one per platform, so
# the view never depends on an executor to tell users where to log in.
LOGIN_STATE_URLS = {
    "wechat": "https://mp.weixin.qq.com/",
    "zhihu": "https://www.zhihu.com/signin",
    "toutiao": "https://mp.toutiao.com/",
    "xiaohongshu": "https://creator.xiaohongshu.com/",
    "douyin": "https://creator.douyin.com/",
}


def login_states_view() -> dict:
    """The strip's payload: per-platform ok/logged_out/unknown/offline.

    Offline is a global state — no live heartbeat means nobody knows
    anything, so every platform flips at once (spec §3.2). With a live
    heartbeat, only probed reports decide ok/logged_out; everything else
    stays unknown.
    """
    online = agent_online()
    states = platform_login_states()
    platforms = {}
    for pid, url in LOGIN_STATE_URLS.items():
        entry = states.get(pid)
        if not online:
            state, at = "offline", None
        elif entry is None or not entry["probed"]:
            state, at = "unknown", None
        else:
            state = "ok" if entry["logged_in"] else "logged_out"
            at = int(entry["age"])
        platforms[pid] = {"state": state, "at": at, "login_url": url}
    snap = heartbeat_snapshot()
    return {"checked_at": int(snap["age"]) if online and snap else None,
            "platforms": platforms}
```

- [ ] **Step 3: 加路由**

`insights_web.py` 在 `agent_job_json`（`return jsonify(job)` 之后、`agent-jobs/pair-code` 之前）插入：

```python
@app.route('/agent/login-states.json')
def agent_login_states_json():
    """The compose strip's data: one merged per-platform login view."""
    return jsonify(agent_hub.login_states_view())
```

- [ ] **Step 4: 跑 gate 确认变绿**

Run: `venv/Scripts/python.exe scripts/check_login_states.py`
Expected: PASS，`LOGIN_STATES_OK`。

- [ ] **Step 5: 注册进 run_all_gates.py**

`scripts/run_all_gates.py` 的 `AGENT_GATES` 列表里 `check_agent_ui.py` 那行之后加：

```python
    ("check_login_states.py", "LOGIN_STATES_OK",
     "the login-states view merges probed beats unprobed and flips all-offline past the window"),
```

Run: `venv/Scripts/python.exe scripts/check_login_states.py`（确认单独可跑）；再 `venv/Scripts/python.exe scripts/run_all_gates.py` 确认注册无语法错。
Expected: `LOGIN_STATES_OK`，全量表里能看到新 gate。

- [ ] **Step 6: Commit**

```bash
git add agent_hub.py insights_web.py scripts/check_login_states.py scripts/run_all_gates.py
git commit -m "Login states: one merged per-platform view behind /agent/login-states.json"
```

---

### Task 4: 写作页状态条 partial + 挂载 + 轮询

**Files:**
- Create: `templates/partials/login_status_strip.html`
- Modify: `templates/compose.html:5-6`（`{% block content %}` 的 import 行之后挂载）
- Modify: `scripts/check_agent_ui.py`（strip 断言）

**Interfaces:**
- Consumes: Task 3 的 `GET /agent/login-states.json`；模板上下文里的 `login_states`（Task 5 会注入；本任务先用 JSON init 标签容错空值）
- Produces: `#loginStrip` 区块（`aria-label="平台登录状态"`）、`#login-states-init` JSON 标签、30s 轮询自渲染。Task 5 的按钮文案与它共用词汇表

- [ ] **Step 1: 在 check_agent_ui.py 写失败断言**

在 `check_agent_ui.py` 的 offline 断言区（`'推送离线' in html` 附近）追加：

```python
    # The login strip mounts above the generator and carries the
    # vocabulary the spec fixes (platform names, 暂不支持检测, 检测离线).
    check('id="loginStrip"' in html, "the login strip mounts on compose")
    check('/agent/login-states.json' in html,
          "the strip polls the login-states endpoint")
    check('暂不支持检测' in html,
          "the strip knows the unknown state's wording")
    check('账号登录' in html, "the strip's title uses the fixed vocabulary")
```

Run: `venv/Scripts/python.exe scripts/check_agent_ui.py`
Expected: FAIL —— strip 尚不存在。

- [ ] **Step 2: 创建 partial**

创建 `templates/partials/login_status_strip.html`：

```html
{# 平台登录状态条：写作页顶部一行，数据来自 /agent/login-states.json。
   服务端注入初始 JSON（#login-states-init），渲染与 30s 轮询都在这段
   自足脚本里；词汇遵循 spec 的竞品词表：平台/登录/已登录/未登录/检测。 #}
<section class="login-strip" id="loginStrip" aria-label="平台登录状态">
    <style>
        .login-strip { background: var(--surface, #fff); border: 1px solid var(--panel-line, #dee1e8);
                       border-radius: 12px; padding: .6rem .9rem; margin-bottom: 1.25rem; }
        .login-bar { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem .9rem; }
        .login-head { display: flex; flex-direction: column; line-height: 1.2; min-width: 8.5rem; }
        .login-title { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
                       font-weight: 600; color: var(--muted, #5f636f); }
        .login-summary { font-size: .9rem; font-weight: 600; }
        .login-summary.is-offline { color: #dc2626; }
        .login-chips { display: flex; flex-wrap: wrap; gap: .4rem; list-style: none;
                       margin: 0; padding: 0; flex: 1 1 auto; }
        .chip { display: inline-flex; align-items: center; gap: .4rem; padding: .28rem .75rem .28rem .6rem;
                border: 1px solid var(--line-strong, #c9cfd9); border-radius: 999px; background: #fff;
                font-size: .84rem; color: inherit; text-decoration: none; }
        .chip .dot { width: .5rem; height: .5rem; border-radius: 999px; flex: 0 0 auto; }
        .chip.ok { background: #f0fdf4; border-color: #bbf7d0; color: #166534; }
        .chip.ok .dot { background: #16a34a; }
        .chip.bad { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
        .chip.bad .dot { background: #dc2626; }
        .chip.na { color: var(--muted, #5f636f); }
        .chip.na .dot { background: transparent; border: 1.5px solid #9ca3af; }
        .chip.off { background: #f9fafb; color: var(--muted, #5f636f); }
        .chip.off .dot { background: #f9fafb; border: 1.5px dashed #9ca3af; }
        a.chip { cursor: pointer; }
        a.chip:hover { border-color: var(--brand-400, #6d92ef); background: var(--brand-50, #eef2fc); }
        .chip .act { display: none; font-size: .74rem; font-weight: 600; }
        .chip.bad .act { color: #dc2626; }
        .chip.off .act { color: #6b7280; }
        a.chip:hover .act { display: inline; }
    </style>
    <div class="login-bar">
        <div class="login-head">
            <span class="login-title">账号登录</span>
            <span class="login-summary" id="loginSummary">检测中…</span>
        </div>
        <ul class="login-chips" id="loginChips"></ul>
    </div>
    <script id="login-states-init" type="application/json">{{ login_states|tojson if login_states else '{}' }}</script>
    <script>
    (function () {
        var LABEL = { wechat: '公众号', zhihu: '知乎', toutiao: '头条号',
                      xiaohongshu: '小红书', douyin: '抖音' };
        var summary = document.getElementById('loginSummary');
        var chipsEl = document.getElementById('loginChips');

        function esc(s) {
            return String(s).replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                         '"': '&quot;', "'": '&#39;' }[c];
            });
        }
        function atText(at) {
            return (at === null || at === undefined) ? '?' : at;
        }
        function freshness(sec) {
            if (sec === null || sec === undefined) return '';
            if (sec <= 90) return '刚刚检测';
            return Math.round(sec / 60) + ' 分钟前检测';
        }
        function chipHtml(pid, entry) {
            var name = esc(LABEL[pid] || pid);
            var s = entry.state, url = esc(entry.login_url || '');
            if (s === 'ok')
                return '<li class="chip ok" title="已登录（' + atText(entry.at) +
                       ' 秒前检测）"><span class="dot"></span>' + name + '</li>';
            if (s === 'logged_out')
                return '<li><a class="chip bad" href="' + url +
                       '" target="_blank" rel="noopener" title="在浏览器里登录' + name +
                       '后，约 30 秒内这里自动变绿"><span class="dot"></span>' + name +
                       ' <span class="act">去登录 ↗</span></a></li>';
            if (s === 'unknown')
                return '<li class="chip na" title="该平台暂未支持登录检测，状态未知（不是未登录）">' +
                       '<span class="dot"></span>' + name + ' 暂不支持检测</li>';
            return '<li><a class="chip off" href="/agent-jobs" title="检测离线：发布助手未连接，' +
                   '登录状态无法确认。去发布队列页重新配对"><span class="dot"></span>' + name +
                   ' <span class="act">去恢复 ↗</span></a></li>';
        }
        function render(data) {
            if (!data || !data.platforms) return; // no initial payload yet
            var plats = data.platforms;
            var offline = data.checked_at === null || data.checked_at === undefined;
            var okCount = 0;
            var html = '';
            Object.keys(LABEL).forEach(function (pid) {
                var entry = plats[pid] || { state: 'unknown', at: null, login_url: '' };
                if (entry.state === 'ok') okCount++;
                html += chipHtml(pid, entry);
            });
            chipsEl.innerHTML = html;
            if (offline) {
                summary.textContent = '检测离线 · 无法确认登录状态';
                summary.classList.add('is-offline');
            } else {
                summary.textContent = okCount + ' 个平台可推送 · ' + freshness(data.checked_at);
                summary.classList.remove('is-offline');
            }
        }
        function poll() {
            fetch('/agent/login-states.json')
                .then(function (r) { return r.json(); })
                .then(render)
                .catch(function () { /* a lost poll keeps the last render */ });
        }
        try {
            render(JSON.parse(document.getElementById('login-states-init').textContent));
        } catch (e) { /* malformed init: the first poll will paint it */ }
        setInterval(function () { if (!document.hidden) poll(); }, 30000);
    })();
    </script>
</section>
```

- [ ] **Step 3: 挂载进 compose.html**

`templates/compose.html` 第 6 行的 import 行之后（`{% from 'partials/post_item.html' import ... %}` 的下一行）插入：

```jinja
{% include 'partials/login_status_strip.html' %}
```

- [ ] **Step 4: 跑 gate**

Run: `venv/Scripts/python.exe scripts/check_agent_ui.py`
Expected: PASS，`AGENT_UI_OK` —— strip 自带「账号登录」标题、轮询端点与 unknown 文案，Step 1 的四条断言全部转绿。若 `'暂不支持检测'` 一条红，查 partial 里 unknown 分支的拼写。

- [ ] **Step 5: 手动目检（本地起服务）**

Run: `venv/Scripts/python.exe insights_web.py`，浏览器开 `http://localhost:5001/compose`
Expected: 生成器卡片上方出现状态条；无心跳时五枚灰 chip + 红色「检测离线 · 无法确认登录状态」。

- [ ] **Step 6: Commit**

```bash
git add templates/partials/login_status_strip.html templates/compose.html scripts/check_agent_ui.py
git commit -m "Compose: login status strip with per-platform chips, go-login and go-recover actions"
```

---

### Task 5: 推送按钮读同一份数据 + 客户文案清理

**Files:**
- Modify: `templates/partials/post_item.html:178-204`（按钮禁用条件与文案）
- Modify: `templates/compose.html`（「一键推送草稿箱（需浏览器扩展或本机 agent 在线）」提示行）
- Modify: `insights_web.py:5925-5926`（context processor：`platform_login` → `login_states`；`agent_online` 保留 —— `base.html:461` 的 pair-strip 与 `agent_jobs.html` 依赖它）
- Modify: `scripts/check_agent_ui.py`（离线/未登录断言改新文案 + 行话清除断言）

**Interfaces:**
- Consumes: Task 2/3 的 `login_states_view()`（context processor 注入为 `login_states`）
- Produces: 卡片按钮语义 —— `offline`→禁用「检测离线」、`logged_out`→禁用「未登录」、`unknown`→不禁用 + 「登录状态未知」提示、`ok`→正常。spec §4 的按钮统一

- [ ] **Step 1: 写失败断言**

`check_agent_ui.py` offline 区的 `'推送离线' in html` 断言改为：

```python
    check('检测离线' in html,
          "while offline the disabled button names the reason in strip vocabulary")
```

online 区（heartbeat 种子之后）追加：

```python
    check('本机 agent' not in html,
          "client-facing copy never says agent")
    check('登录状态未知' not in html or '暂不支持登录检测' in html,
          "unknown-state wording, when present, follows the vocabulary")
```

Run: `venv/Scripts/python.exe scripts/check_agent_ui.py`
Expected: FAIL（「检测离线」尚未出现在按钮文案；「本机 agent」仍在 compose html）。

- [ ] **Step 2: 换 context processor 注入**

`insights_web.py` `_inject_publish_targets` 的返回字典里：

```python
            'agent_online': agent_hub.agent_online(),
            'platform_login': agent_hub.platform_login_states()}
```

改为：

```python
            'agent_online': agent_hub.agent_online(),
            'login_states': agent_hub.login_states_view()}
```

- [ ] **Step 3: 改 post_item.html 按钮**

`post_item.html` 第 178-190 行的按钮改为：

```jinja
                            {% for entry in push_entries %}
                            <span class="push-group">
                                {% set ls = login_states.platforms.get(entry.platform) if login_states else none %}
                                <button type="button"
                                        class="btn btn-sm btn-success btn-agent-push"
                                        data-post-id="{{ entry.id }}"
                                        data-platform="{{ entry.platform }}"
                                        {% if ls and ls.state == 'offline' %}disabled
                                            title="检测离线：发布助手未连接，去发布队列页恢复"
                                        {% elif not push_platforms[entry.platform].enabled %}disabled
                                            title="该平台未在设置页启用"
                                        {% elif ls and ls.state == 'logged_out' %}disabled
                                            title="{{ push_platforms[entry.platform].label }}未登录，请先在浏览器登录一次"
                                        {% elif ls and ls.state == 'unknown' %}
                                            title="登录状态未知：该平台暂不支持登录检测，推送前请自行确认已登录"
                                        {% endif %}>
                                    {% if ls and ls.state == 'logged_out' %}⚠ {% endif %}📤 推送到{{ push_platforms[entry.platform].label }}草稿箱
                                </button>
```

同文件约第 201 行定时推送按钮的 title：

```jinja
                                            title="到点后自动推送草稿箱；现在入队，离线也可以">
```

- [ ] **Step 4: 清理 compose.html 提示行**

`templates/compose.html` 里搜「需浏览器扩展或本机 agent 在线」，整句改为：

```
公众号 / 知乎 / 头条号支持一键推送草稿箱（需浏览器里的发布助手在线），推送后请到各平台确认发布；小红书 / 抖音生成后复制文案手动发布。
```

- [ ] **Step 5: 全仓扫尾**

Run: `grep -rn "本机 agent\|推送离线" templates/ scripts/ | grep -v agent_jobs.html`
Expected: 无输出（`agent_jobs.html` 是排障页，行话豁免；如有其他命中，逐处按词汇表改）。

- [ ] **Step 6: 跑 gate**

Run: `venv/Scripts/python.exe scripts/check_agent_ui.py && venv/Scripts/python.exe scripts/check_agent_push.py && venv/Scripts/python.exe scripts/check_bulk_platform_ui.py`
Expected: 三个 gate 全 OK。`check_agent_push.py` 与 `check_bulk_platform_ui.py` 不断言按钮文案（已核实），跑它们只为确认按钮条件改动无回归；红了就是行为改坏，回去修实现而不是改断言。

- [ ] **Step 7: Commit**

```bash
git add templates/partials/post_item.html templates/compose.html insights_web.py scripts/check_agent_ui.py scripts/check_agent_push.py scripts/check_bulk_platform_ui.py
git commit -m "Push buttons read the merged login view; client copy drops executor jargon"
```

---

### Task 6: 扩展五平台探测库 platform-login.ts

**Files:**
- Create: `extension/src/lib/platform-login.ts`
- Create: `extension/src/lib/platform-login.test.ts`
- Modify: `extension/src/lib/login.ts`（追加 verdict 版 weixin 检查；保留 `parseWeixinToken`）
- Modify: `extension/src/lib/login.test.ts`（weixin 断言换三值）
- Modify: `extension/src/manifest.json:7`（host_permissions 扩四域）

**Interfaces:**
- Consumes: 无（自足库）
- Produces: `export type LoginVerdict = "yes" | "no" | "unknown"`；`zhihuVerdict(status: number, body: string): LoginVerdict`；`portalVerdict(finalUrl: string, ok: boolean): LoginVerdict`；`checkZhihuLogin(fetchImpl?): Promise<LoginVerdict>`；`checkPortalLogin(url: string, fetchImpl?): Promise<LoginVerdict>`；`login.ts` 新增 `weixinVerdict(token: string): LoginVerdict`、`checkWeixinVerdict(fetchImpl?): Promise<LoginVerdict>`。Task 7 的 `PLATFORM_CHECKS` 逐名引用这些

- [ ] **Step 1: 写失败的 vitest**

创建 `extension/src/lib/platform-login.test.ts`：

```typescript
import { describe, expect, it } from "vitest";
import { portalVerdict, zhihuVerdict } from "./platform-login";

describe("zhihuVerdict", () => {
  it("is yes when /api/v4/me answers 200 with an id", () => {
    expect(zhihuVerdict(200, JSON.stringify({ id: "u1", name: "x" }))).toBe("yes");
  });
  it("is no on 401/403", () => {
    expect(zhihuVerdict(401, "")).toBe("no");
    expect(zhihuVerdict(403, "")).toBe("no");
  });
  it("is unknown on other statuses, bodies without id, or bad json", () => {
    expect(zhihuVerdict(500, "")).toBe("unknown");
    expect(zhihuVerdict(200, "{}")).toBe("unknown");
    expect(zhihuVerdict(200, "not json")).toBe("unknown");
  });
});

describe("portalVerdict", () => {
  it("is no when the portal bounced to a login page", () => {
    expect(portalVerdict("https://mp.toutiao.com/auth/page/login/?x=1", true)).toBe("no");
    expect(portalVerdict("https://creator.douyin.com/?entry=sso", true)).toBe("no");
    expect(portalVerdict("https://passport.xiaohongshu.com/login", true)).toBe("no");
  });
  it("is yes when the portal served a creator page directly", () => {
    expect(portalVerdict("https://creator.xiaohongshu.com/publish/publish", true)).toBe("yes");
  });
  it("is unknown when the fetch did not succeed", () => {
    expect(portalVerdict("https://creator.xiaohongshu.com/", false)).toBe("unknown");
  });
});
```

Run: `cd extension && npm test`
Expected: FAIL —— `./platform-login` 不存在。

- [ ] **Step 2: 实现 platform-login.ts**

创建 `extension/src/lib/platform-login.ts`：

```typescript
// Per-platform login probes for the heartbeat. Every check returns a
// three-valued verdict: "no" means "we probed and the site says logged
// out" — a network error is "unknown", never "no". The old code collapsed
// both, which made the server lie about platforms nobody checked.
export type LoginVerdict = "yes" | "no" | "unknown";

// Zhihu: the session endpoint answers 200 {id: ...} only with cookies.
export function zhihuVerdict(status: number, body: string): LoginVerdict {
  if (status === 401 || status === 403) return "no";
  if (status !== 200) return "unknown";
  try {
    const me = JSON.parse(body) as { id?: unknown };
    return typeof me.id === "string" && me.id ? "yes" : "unknown";
  } catch {
    return "unknown";
  }
}

export async function checkZhihuLogin(fetchImpl: typeof fetch = fetch): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl("https://www.zhihu.com/api/v4/me",
                              { credentials: "include" });
    return zhihuVerdict(r.status, await r.text());
  } catch {
    return "unknown";
  }
}

// Creator portals bounce logged-out visitors to a login/sso page; the
// final URL after redirects is the marker. A page that loads without a
// bounce reads as logged in — douyin's SPA may do exactly that while
// logged out, an accepted false-yes for now (spec §7); the queue result
// is the backstop a person actually sees.
export function portalVerdict(finalUrl: string, ok: boolean): LoginVerdict {
  if (!ok) return "unknown";
  return /login|sso|passport/i.test(finalUrl) ? "no" : "yes";
}

export async function checkPortalLogin(url: string,
                                       fetchImpl: typeof fetch = fetch): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl(url, { credentials: "include", redirect: "follow" });
    return portalVerdict(r.url, r.ok);
  } catch {
    return "unknown";
  }
}
```

- [ ] **Step 3: login.ts 加 verdict 版 weixin**

`extension/src/lib/login.ts` 追加（保留 `parseWeixinToken` 与 `TOKEN_PATTERNS`；`checkWeixinLogin` 先留着，Task 7 摘除引用后删除）：

```typescript
import type { LoginVerdict } from "./platform-login";

export function weixinVerdict(token: string): LoginVerdict {
  return token ? "yes" : "no";
}

export async function checkWeixinVerdict(fetchImpl: typeof fetch = fetch): Promise<LoginVerdict> {
  try {
    const resp = await fetchImpl(WEIXIN_HOME, { credentials: "include" });
    // The homepage answers 200 either way; only its shape knows.
    if (!resp.ok) return "unknown";
    return weixinVerdict(parseWeixinToken(await resp.text()));
  } catch {
    return "unknown";
  }
}
```

（`import type` 行放在文件顶部与现有注释之间。）

- [ ] **Step 4: 改 login.test.ts 为三值断言**

`extension/src/lib/login.test.ts` 的 `checkWeixinLogin` describe 整块替换为：

```typescript
import { checkWeixinVerdict, parseWeixinToken, weixinVerdict } from "./login";

describe("weixinVerdict", () => {
  it("reads a token as logged in", () => {
    expect(weixinVerdict("123456")).toBe("yes");
  });
  it("reads an empty token as logged out — the page answered, so this is a real probe", () => {
    expect(weixinVerdict("")).toBe("no");
  });
});

describe("checkWeixinVerdict", () => {
  it("is yes when the fetched homepage carries a token", async () => {
    const f = (async () => new Response(loggedIn)) as unknown as typeof fetch;
    await expect(checkWeixinVerdict(f)).resolves.toBe("yes");
  });
  it("is no when the homepage loads without a token", async () => {
    const f = (async () => new Response(loggedOut)) as unknown as typeof fetch;
    await expect(checkWeixinVerdict(f)).resolves.toBe("no");
  });
  it("is unknown on network failure rather than lying", async () => {
    const f = (async () => {
      throw new Error("down");
    }) as unknown as typeof fetch;
    await expect(checkWeixinVerdict(f)).resolves.toBe("unknown");
  });
});
```

- [ ] **Step 5: manifest 扩 host_permissions**

`extension/src/manifest.json` 第 7 行改为：

```json
  "host_permissions": ["https://mp.weixin.qq.com/*", "https://www.zhihu.com/*",
                       "https://mp.toutiao.com/*", "https://creator.xiaohongshu.com/*",
                       "https://creator.douyin.com/*"],
```

- [ ] **Step 6: 跑 vitest**

Run: `cd extension && npm test`
Expected: PASS（新平台探测 + 改写的 weixin 三值都绿）。

- [ ] **Step 7: Commit**

```bash
git add extension/src/lib/platform-login.ts extension/src/lib/platform-login.test.ts extension/src/lib/login.ts extension/src/lib/login.test.ts extension/src/manifest.json
git commit -m "Extension: three-valued login probes for zhihu, toutiao, xiaohongshu and douyin"
```

---

### Task 7: orchestrator/service-worker 接线（PLATFORM_IDS 五平台 + probed 心跳 + 可测缓存）

**Files:**
- Modify: `extension/src/background/orchestrator.ts`（`PLATFORM_IDS`、`Heartbeat`、`buildHeartbeat`、`CycleDeps.checkLogins`、新增 `cachedCheckLogins`）
- Modify: `extension/src/background/service-worker.ts`（`checkLogins` 换成五平台 `PLATFORM_CHECKS` + 缓存调用；删 `checkWeixinLogin` 引用）
- Modify: `extension/src/lib/login.ts`（删掉已无人引用的旧 `checkWeixinLogin`）
- Modify: `extension/src/background/orchestrator.test.ts`（buildHeartbeat 新形状 + 缓存回归）

**Interfaces:**
- Consumes: Task 6 的 `LoginVerdict`/`checkWeixinVerdict`/`checkZhihuLogin`/`checkPortalLogin`
- Produces: `PLATFORM_IDS = ["wechat", "zhihu", "toutiao", "xiaohongshu", "douyin"]`；`buildHeartbeat(verdicts: Record<string, LoginVerdict>, busy)` 心跳 platforms 带 `probed`；`cachedCheckLogins(checks, cache, now, ttlMs?): Promise<LoginVerdicts>`（`LoginVerdicts = Record<string, LoginVerdict>`）。服务端 Task 2 已兼容新旧两种心跳

- [ ] **Step 1: 写失败的 orchestrator 测试（含 Review Focus 4）**

`orchestrator.test.ts` 里 `buildHeartbeat` 的断言整块替换为：

```typescript
    expect(buildHeartbeat({ wechat: "yes", zhihu: "no", douyin: "unknown" }, null)).toEqual({
      agent: "insights-ext",
      busy: null,
      tools: {
        extension: {
          available: true,
          platformIds: ["wechat", "zhihu", "toutiao", "xiaohongshu", "douyin"],
          platforms: [
            { id: "wechat", logged_in: true, probed: true },
            { id: "zhihu", logged_in: false, probed: true },
            { id: "toutiao", logged_in: false, probed: false },
            { id: "xiaohongshu", logged_in: false, probed: false },
            { id: "douyin", logged_in: false, probed: false },
          ],
        },
      },
    });
    expect(buildHeartbeat({ wechat: "yes", zhihu: "no", douyin: "unknown" }, 7).busy).toBe(7);
```

并在同文件新增（import 行加 `cachedCheckLogins`）：

```typescript
describe("cachedCheckLogins", () => {
  it("a failing probe reads unknown without poisoning the shared cache", async () => {
    const cache = { at: 0, value: {} as Record<string, LoginVerdict> };
    let zhihuCalls = 0;
    const checks = {
      wechat: async () => "yes" as LoginVerdict,
      zhihu: async () => {
        zhihuCalls++;
        throw new Error("down");
      },
    };
    const first = await cachedCheckLogins(checks, cache, 1000);
    expect(first).toEqual({ wechat: "yes", zhihu: "unknown" });
    const second = await cachedCheckLogins(checks, cache, 20000); // within ttl
    expect(second).toEqual({ wechat: "yes", zhihu: "unknown" });
    expect(zhihuCalls).toBe(1); // served from cache, not re-probed
    const third = await cachedCheckLogins(checks, cache, 40000); // ttl passed
    expect(zhihuCalls).toBe(2);
  });
});
```

（文件顶部 import 补：`import { cachedCheckLogins, ... } from "./orchestrator";` 与 `import type { LoginVerdict } from "../lib/platform-login";`。）

文件中其余 `checkLogins: async () => ({ wechat: true })` 全部改为 `checkLogins: async () => ({ wechat: "yes" })`（共 4 处，`vi.fn(...)` 包裹的两处同样改）。

Run: `cd extension && npm test`
Expected: FAIL —— `cachedCheckLogins` 不存在、buildHeartbeat 形状是旧二值。

- [ ] **Step 2: 改 orchestrator.ts**

`PLATFORM_IDS` 替换：

```typescript
export const PLATFORM_IDS = ["wechat", "zhihu", "toutiao", "xiaohongshu", "douyin"];
```

`Heartbeat` 接口与 `buildHeartbeat` 替换：

```typescript
export type LoginVerdicts = Record<string, "yes" | "no" | "unknown">;

export interface Heartbeat {
  agent: string;
  busy: number | null;
  tools: { extension: { available: boolean; platformIds: string[];
    platforms: { id: string; logged_in: boolean; probed: boolean }[] } };
}

export function buildHeartbeat(verdicts: LoginVerdicts,
                               busyJobId: number | null): Heartbeat {
  return {
    agent: AGENT_NAME,
    busy: busyJobId,
    tools: {
      extension: {
        available: true,
        platformIds: PLATFORM_IDS,
        platforms: PLATFORM_IDS.map((id) => ({
          id,
          logged_in: verdicts[id] === "yes",
          probed: verdicts[id] === "yes" || verdicts[id] === "no",
        })),
      },
    },
  };
}
```

`CycleDeps.checkLogins` 签名改为 `checkLogins(): Promise<LoginVerdicts>;`（`runCycle` 内 `buildHeartbeat(logins, ...)` 调用不变）。文件尾部新增：

```typescript
export interface LoginCache { at: number; value: LoginVerdicts; }

// One shared 30s cache for the five login probes; a probe that throws
// reads as "unknown" and must not retry inside the ttl — the beat runs
// every 5s and five live fetches each tick would hammer the portals.
export async function cachedCheckLogins(
  checks: Record<string, () => Promise<LoginVerdict>>,
  cache: LoginCache, now: number, ttlMs = 30000): Promise<LoginVerdicts> {
  if (now - cache.at < ttlMs) return cache.value;
  const entries = await Promise.all(Object.keys(checks).map(async (id) => {
    try {
      return [id, await checks[id]()] as const;
    } catch {
      return [id, "unknown" as const];
    }
  }));
  cache.at = now;
  cache.value = Object.fromEntries(entries) as LoginVerdicts;
  return cache.value;
}
```

（`LoginVerdict` 类型固定从 `../lib/platform-login` import type，不在 orchestrator 里重复定义；`LoginVerdicts` 的元素类型即它。）

- [ ] **Step 3: 改 service-worker.ts**

`checkLogins`/`loginCache` 整块替换为：

```typescript
import { checkPortalLogin, checkWeixinVerdict, checkZhihuLogin, LoginVerdict } from "../lib/platform-login";
import { backoffSeconds, cachedCheckLogins, LoginCache, LoginVerdicts,
         PLATFORM_IDS, PlatformResult, runCycle } from "./orchestrator";

// One shared 30s cache: the 5s claim loop and the 30s heartbeat share at
// most one round of the five probes.
const loginCache: LoginCache = { at: 0, value: {} };

const PLATFORM_CHECKS: Record<string, () => Promise<LoginVerdict>> = {
  wechat: () => checkWeixinVerdict(),
  zhihu: () => checkZhihuLogin(),
  toutiao: () => checkPortalLogin("https://mp.toutiao.com/"),
  xiaohongshu: () => checkPortalLogin("https://creator.xiaohongshu.com/"),
  douyin: () => checkPortalLogin("https://creator.douyin.com/"),
};

async function checkLogins(): Promise<LoginVerdicts> {
  return cachedCheckLogins(PLATFORM_CHECKS, loginCache, Date.now());
}
```

（删掉旧的 `checkLogins` 函数、`loginCache` 对象字面量与对 `checkWeixinLogin` 的 import；`cycle()` 内 `runCycle({ client, checkLogins, ... })` 不变。）

- [ ] **Step 4: 删 login.ts 的旧 checkWeixinLogin**

`extension/src/lib/login.ts` 删除 `checkWeixinLogin` 函数（Task 6 后无引用；`grep -rn "checkWeixinLogin" extension/src` 确认零命中再删）。

- [ ] **Step 5: 跑 vitest + 构建**

Run: `cd extension && npm test && npm run build`
Expected: 测试全绿；`dist/service-worker.js` 重新生成（`ls -la extension/dist/service-worker.js` 时间戳更新）。

- [ ] **Step 6: Commit**

```bash
git add extension/src/background/orchestrator.ts extension/src/background/service-worker.ts extension/src/background/orchestrator.test.ts extension/src/lib/login.ts
git commit -m "Extension: heartbeat carries probed verdicts for all five platforms behind one shared cache"
```

---

### Task 8: 全量 gate + 手动 E2E 清单

**Files:**
- 无新改动（验证任务；如 E2E 暴露探测标记失准，修 `platform-login.ts` 的判定函数并补 fixture 重跑 Task 6/7 的测试）

**Interfaces:**
- Consumes: 前 7 个任务的全部产物
- Produces: 一条可以告诉 owner「已上线」的验证记录

- [ ] **Step 1: 全量 gate**

Run: `venv/Scripts/python.exe scripts/run_all_gates.py`
Expected: 全绿，含新 `LOGIN_STATES_OK` 与 `AGENT_UI_OK`、`EXTENSION_TESTS_OK`。

- [ ] **Step 2: 提交前推送卫生检查（仓库规则）**

Run: `git log origin/main..HEAD --format=%B | grep -iE '^(Co-Authored-By|Claude-Session):'; git log origin/main..HEAD --format='%an <%ae> | %cn <%ce>' | sort -u`
Expected: 第一条无输出；第二条只有 `l0lsec <51931886+l0lsec@users.noreply.github.com>`。

- [ ] **Step 3: 手动 E2E（列给 owner，按序执行并记录结果）**

1. `venv/Scripts/python.exe insights_web.py`，登录后在 `/compose` 确认状态条：扩展离线时应为五枚「离线」chip + 红「检测离线 · 无法确认登录状态」
2. Chrome 重载 `extension/dist`（chrome://extensions → 重新加载），确认扩展已配对；30 秒内刷新写作页 —— 公众号/知乎/头条号/小红书/抖音各显示真实状态
3. 退出公众号登录 → 30 秒内 chip 变红「去登录 ↗」；点击直达 `mp.weixin.qq.com`；重新登录 → 30 秒内变绿
4. 停掉 Chrome（或禁用扩展）60 秒 → 状态条整体转「检测离线」，卡片推送按钮变灰且提示「检测离线」
5. 抖音未登录时观察 chip：允许「已登录」误报（spec §7 已接受的折中），记录实际表现留档

- [ ] **Step 4: 收尾提交（如 E2E 有修正）**

```bash
git status --short   # 确认只剩有意提交的文件
git add -A && git commit -m "Login strip: E2E fixes from manual verification"
```

（若无修正则跳过。）
