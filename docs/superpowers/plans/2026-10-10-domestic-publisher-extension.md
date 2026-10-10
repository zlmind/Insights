# 国内发布扩展（M1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 服务器与自研 MV3 扩展之间打通 M1 链路——入队按平台打 `executor` 标记、claim 按执行者过滤、扩展协议客户端对真实 HTTP 走完 heartbeat→claim→events、公众号适配器搬运并落草稿箱。

**Architecture:** 复用现有 `/agent/*` 协议与 `agent_jobs` 队列；`domestic_publisher` 入队时按新设置 `DOMESTIC_EXECUTORS` 给 payload 打 `executor`；`claim_agent_job` 增加 executor/platforms 过滤；`extension/` 下用 esbuild 构建 MV3 扩展（service worker 编排 + content script 适配器），适配器代码搬运自 MultiPost-Extension（Apache-2.0）。

**Tech Stack:** Python/Flask/SQLite（现有）、TypeScript + esbuild + vitest（扩展）、无框架 MV3。

**Spec:** `docs/superpowers/specs/2026-10-10-domestic-publisher-extension-design.md`

## Global Constraints

- 提交身份必须是 `l0lsec <51931886+l0lsec@users.noreply.github.com>`（`git config user.name/user.email`，本仓库已配置）；提交信息禁止 `Co-Authored-By:`/`Claude-Session:`/模型名（CLAUDE.md 规则）
- 搬运的 MultiPost 文件必须保留其 Apache-2.0 版权头，并在 `extension/NOTICE` 记录上游仓库与 `git ls-remote` 取到的 commit SHA；禁止搬运 wechatsync（GPL-3.0）的任何代码
- 每个新 gate 脚本必须注册进 `scripts/run_all_gates.py`，否则 `gate_inventory_problems` 会判失败
- Python 门一律用 venv 解释器跑：`venv/Scripts/python.exe scripts/<gate>.py`（系统 python 缺 flask）
- 扩展构建零运行时依赖除 `marked`（markdown→HTML）；MV3 禁远程代码/eval
- 服务器失败分类沿用现有码：`needs_login`/`sync_failed`/`timeout`/`unknown_target`，不发明新码
- 草稿语义：任何 `draft:true` 结果必须真的落在平台草稿箱；公众号走 `operate_appmsg`（上游即建草稿），不点发布

## Review Focus

1. claim 候选里混入畸形 payload（非 JSON、非对象）——必须跳过该行继续找，不能让整个 claim 事务 500 → Task 2 测试钉住
2. 心跳缺 `tools` 字段（扩展早期/异常客户端）——`platform_login_states()` 必须返回 `{}` 不崩溃 → Task 2 测试钉住
3. `DOMESTIC_EXECUTORS` 存了非法 JSON——入队必须按「未打标」处理，不能 500 → Task 1 测试钉住
4. 平台已毕业到扩展但扩展离线——旧 agent 不得领走该 job，job 留在 pending 可见 → Task 2 测试钉住
5. 公众号 Cookie 失效（页面 HTML 无 token 标记）——适配器必须返回 `needs_login`（不可重试），不是 `sync_failed` → Task 8 测试钉住

---

### Task 1: 服务器——`DOMESTIC_EXECUTORS` 设置与入队打标

**Files:**
- Modify: `appconfig.py`（`SETTINGS` 的 domestic 组）
- Modify: `domestic_publisher.py`（`_payload_for`、新增 `_executor_map`、`enqueue_card_push` 传 db_path）
- Test: `scripts/check_domestic_publisher.py`（追加断言）

**Interfaces:**
- Produces: `domestic_publisher._executor_map(db_path=None) -> dict[str, str]`（只保留值为 `"extension"` 的键）；payload 字段 `executor: "extension"`（未打标 = 旧 agent）。Task 2 的 claim 过滤消费 `executor` 字段。

- [ ] **Step 1: 写失败测试**（追加到 `check_domestic_publisher.py` 的 `main()` 末尾、`print` 之前）

```python
    # --- executor stamping: a graduated platform's jobs are marked for the
    # extension, everyone else's stay on the old agent lane ---
    database.set_setting("DOMESTIC_EXECUTORS", json.dumps({"wechat": "extension"}))
    row4 = _seed_post(database)
    stamped = dp.enqueue_card_push(row4)
    payload = json.loads(database.get_agent_job(stamped["job_id"])["payload"])
    check(payload.get("executor") == "extension",
          "a graduated platform's payload is stamped for the extension")

    row5 = database.add_standalone_post("freeform", "gate-seed", "zhihu", "正文")
    row5 = database.get_standalone_post(row5)
    unstamped = dp.enqueue_card_push(row5)
    payload = json.loads(database.get_agent_job(unstamped["job_id"])["payload"])
    check("executor" not in payload,
          "an ungraduated platform's payload stays on the old agent lane")

    database.set_setting("DOMESTIC_EXECUTORS", "{not json")
    row6 = _seed_post(database)
    broken = dp.enqueue_card_push(row6)
    payload = json.loads(database.get_agent_job(broken["job_id"])["payload"])
    check("executor" not in payload,
          "a broken executor map reads as 'nothing graduated', not a 500")

    # Dedupe still holds with stamping on: same setting, same payload string.
    database.set_setting("DOMESTIC_EXECUTORS", json.dumps({"wechat": "extension"}))
    row7 = _seed_post(database)
    a = dp.enqueue_card_push(row7)
    b = dp.enqueue_card_push(row7)
    check(b["already"] is True and b["job_id"] == a["job_id"],
          "stamped pushes dedupe like unstamped ones")
    database.set_setting("DOMESTIC_EXECUTORS", "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/Scripts/python.exe scripts/check_domestic_publisher.py`
Expected: FAIL（`a graduated platform's payload is stamped for the extension`）

- [ ] **Step 3: 最小实现**

`appconfig.py` 的 `WECHATSYNC_ENABLED_PLATFORMS` 条目后追加：

```python
    "DOMESTIC_EXECUTORS": {
        "group": "domestic", "label": "毕业到扩展的平台",
        "default": "",
        "help": 'JSON 映射，如 {"wechat": "extension"}。列出的平台由浏览器扩展执行，'
                "其余走本机 agent。留空 = 全部走本机 agent。",
    },
```

`domestic_publisher.py`：

```python
def _executor_map(db_path=None) -> dict:
    """``{platform: "extension"}`` for platforms graduated to the extension.

    Anything unreadable reads as nothing graduated: a broken setting must
    never block a push.
    """
    kwargs = {} if db_path is None else {"db_path": db_path}
    raw = database.get_setting("DOMESTIC_EXECUTORS", "", **kwargs) or ""
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: v for k, v in parsed.items() if v == "extension"}
```

`_payload_for(row)` 改为带 `db_path` 并在末尾打标；`enqueue_card_push` 里调用处改为 `_payload_for(row, db_path=kwargs.get("db_path"))`：

```python
def _payload_for(row, db_path=None) -> dict:
    keys = row.keys() if hasattr(row, "keys") else ()
    title = row["title"] if "title" in keys else None
    payload = {
        "title": title or display_name(row["platform"]) + "草稿",
        "items": [{"target": row["platform"], "content": row["content"] or ""}],
        "post_id": row["id"],
    }
    if _executor_map(db_path).get(row["platform"]) == "extension":
        payload["executor"] = "extension"
    return payload
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/Scripts/python.exe scripts/check_domestic_publisher.py`
Expected: PASS，末行 `DOMESTIC_PUBLISHER_OK`

- [ ] **Step 5: Commit**

```bash
git add appconfig.py domestic_publisher.py scripts/check_domestic_publisher.py
git commit -m "Queue: stamp graduated platforms' jobs for the publisher extension"
```

---

### Task 2: 服务器——claim 按 executor 与平台能力过滤

**Files:**
- Modify: `database.py`（`claim_agent_job` + 新增 `_job_matches_executor`）
- Modify: `insights_web.py:10368-10385`（`agent_claim` 传新字段）
- Test: `scripts/check_agent_hub.py`（追加断言）

**Interfaces:**
- Consumes: Task 1 的 payload `executor` 字段
- Produces: `database.claim_agent_job(*, kinds, agent, stale_minutes, executor=None, platforms=None, db_path=DB_PATH)`——`executor=None` 等价 `"agent"`（旧 agent 请求体不变即不受影响）；`platforms=None` 不按平台过滤。扩展请求体形如 `{"agent": "insights-ext", "executor": "extension", "platforms": ["wechat"]}`。

- [ ] **Step 1: 写失败测试**（追加到 `check_agent_hub.py` 事件断言之后、心跳断言之前）

```python
    # --- executor filtering: the extension and the old agent never take
    # each other's jobs, even though they share one queue ---
    ext_job = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "wechat", "content": "x"}],
                                    "executor": "extension"}))
    old_job = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "zhihu", "content": "x"}]}))
    r = client.post("/agent/jobs/claim", headers=AUTH,
                    json={"agent": "insights-ext", "executor": "extension",
                          "platforms": ["wechat"]})
    check(r.status_code == 200 and r.get_json()["id"] == ext_job,
          "the extension claims only extension-stamped jobs")
    r = client.post("/agent/jobs/claim", headers=AUTH, json={"agent": "layne-win"})
    check(r.status_code == 200 and r.get_json()["id"] == old_job,
          "the old agent's unmarked body still claims unstamped jobs")

    # A platform the extension does not declare is held, not handed over:
    # graduation moved the platform to the extension, so the old agent must
    # never pick it up, extension offline or not.
    database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "zhihu", "content": "x"}],
                                    "executor": "extension"}))
    r = client.post("/agent/jobs/claim", headers=AUTH,
                    json={"agent": "insights-ext", "executor": "extension",
                          "platforms": ["wechat"]})
    check(r.status_code == 204,
          "a stamped job beyond the claimant's platforms is held")

    # A candidate with a broken payload must be skipped, not fatal.
    database.enqueue_agent_job("article_sync", "{not json")
    r = client.post("/agent/jobs/claim", headers=AUTH, json={"agent": "layne-win"})
    check(r.status_code == 204,
          "a broken payload in the candidate window is skipped, not a 500")

    # An extension-shaped heartbeat (tools keyed by the extension's name)
    # feeds the same login-state view; a tools-less heartbeat is tolerated.
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True, "version": "0.1.0",
            "platforms": [{"id": "wechat", "logged_in": True}]}}})
    check(agent_hub.platform_login_states().get("wechat") is True,
          "the extension's heartbeat feeds platform login states")
    client.post("/agent/heartbeat", headers=AUTH, json={"agent": "x"})
    check(agent_hub.platform_login_states() == {},
          "a heartbeat without tools yields empty login states, not a crash")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/Scripts/python.exe scripts/check_agent_hub.py`
Expected: FAIL（`the extension claims only extension-stamped jobs`——无过滤时扩展领到 `old_job` 或先入队的那个）

- [ ] **Step 3: 最小实现**

`database.py`，`claim_agent_job` 上方新增：

```python
def _job_matches_executor(payload_text, executor, platforms):
    """Whether one queued job may go to this claimant.

    ``executor`` defaults to the old agent lane, so an unmarked request body
    and an unstamped payload meet on "agent" without either side changing.
    A payload that does not parse is nobody's job: skip it rather than let
    one broken row wedge the claim transaction.
    """
    try:
        payload = json.loads(payload_text or "{}")
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    if (payload.get("executor") or "agent") != (executor or "agent"):
        return False
    if platforms is None:
        return True
    targets = [i.get("target") for i in payload.get("items") or []
               if isinstance(i, dict)]
    return bool(targets) and all(t in platforms for t in targets)
```

`claim_agent_job` 签名加 `executor=None, platforms=None`；候选查询改为取一批再过滤：

```python
            rows = conn.execute(
                f"""
                SELECT id, payload FROM agent_jobs
                WHERE status = 'pending' AND kind IN ({marks})
                  AND (run_after IS NULL OR run_after <= ?)
                ORDER BY id LIMIT 100
                """,
                (*kinds, now),
            ).fetchall()
            chosen = next((rid for rid, text in rows
                           if _job_matches_executor(text, executor, platforms)), None)
            if chosen is None:
                conn.rollback()
                return None
```

后续 `UPDATE ... WHERE id = ?` 与 `get_agent_job` 用 `chosen`。

`insights_web.py` 的 `agent_claim` 在取 `body` 之后改为：

```python
    platforms = body.get("platforms")
    if not isinstance(platforms, list) or not all(isinstance(p, str) for p in platforms):
        platforms = None
    job = database.claim_agent_job(
        kinds=("article_sync", "video_publish"),
        agent=body.get("agent", ""),
        executor=body.get("executor") if isinstance(body.get("executor"), str) else None,
        platforms=platforms,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/Scripts/python.exe scripts/check_agent_hub.py`，再跑 `venv/Scripts/python.exe scripts/check_agent_jobs.py`（防旧语义回归）
Expected: 两个都 PASS（`AGENT_HUB_OK`、`AGENT_JOBS_OK`）

- [ ] **Step 5: Commit**

```bash
git add database.py insights_web.py scripts/check_agent_hub.py
git commit -m "Queue: claim filters by executor so the extension and the agent coexist"
```

---

### Task 3: 文案——推送端从「本机代理」改为执行者中立

**Files:**
- Modify: `insights_web.py:10194`（409 消息）
- Modify: `templates/partials/post_item.html:183`（禁用 title）
- Modify: `templates/agent_jobs.html:9`（在线/离线文案）
- Modify: `templates/compose.html:965`（form-text）
- Test: `scripts/check_agent_ui.py`、`scripts/check_agent_push.py`（改断言）

**Interfaces:**
- Consumes: 无（纯文案）。迁移期两种执行者都可能在线，文案不点名单一工具。

- [ ] **Step 1: 先改测试断言（失败先行）**

`check_agent_ui.py`：`'本机代理离线' in html` 改为 `'推送离线' in html`（两处：offline 存在 / online 不存在）。
`check_agent_push.py`：`"离线" in r.get_json()["error"]` 保持不变（新文案仍含「离线」），无需改。

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/Scripts/python.exe scripts/check_agent_ui.py`
Expected: FAIL（`while offline the disabled button names the reason`）

- [ ] **Step 3: 改四处文案**

`insights_web.py:10194`：

```python
        return jsonify({"error": "推送离线：请启动浏览器扩展（Insights 发布助手），或本机 agent"}), 409
```

`templates/partials/post_item.html:182-183`：

```jinja
                                        {% if not agent_online %}disabled
                                            title="推送离线：请启动浏览器扩展（Insights 发布助手）或本机 agent"
```

`templates/agent_jobs.html:9`：

```jinja
            {{ '推送端在线' if agent_online else '推送端离线' }}
```

`templates/compose.html:965`：把 `（需本机运行 agent）` 改为 `（需浏览器扩展或本机 agent 在线）`。

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/Scripts/python.exe scripts/check_agent_ui.py && venv/Scripts/python.exe scripts/check_agent_push.py`
Expected: `AGENT_UI_OK`、`AGENT_PUSH_OK`

- [ ] **Step 5: Commit**

```bash
git add insights_web.py templates/partials/post_item.html templates/agent_jobs.html templates/compose.html scripts/check_agent_ui.py
git commit -m "UI: name the publishing executors neutrally while both lanes live"
```

---

### Task 4: 扩展脚手架与构建

**Files:**
- Create: `extension/package.json`、`extension/tsconfig.json`、`extension/build.mjs`、`extension/src/manifest.json`、`extension/src/version.ts`、`extension/src/version.test.ts`、`extension/NOTICE`
- Modify: `.gitignore`（追加两行）
- Create: `scripts/check_extension_tests.py`
- Modify: `scripts/run_all_gates.py`（AGENT_GATES 注册）

**Interfaces:**
- Produces: `extension/src/version.ts` 导出 `export const VERSION = "0.1.0"`；`npm run build` 产出 `dist/`（service-worker.js、content/bridge.js、options/）；门 token `EXTENSION_TESTS_OK`。后续任务所有模块从 `./version` 引版本号。

- [ ] **Step 1: 建文件**

`extension/package.json`：

```json
{
  "name": "insights-publisher-extension",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "node build.mjs",
    "test": "vitest run"
  },
  "dependencies": {
    "marked": "^14.1.0"
  },
  "devDependencies": {
    "esbuild": "^0.24.0",
    "typescript": "^5.6.0",
    "vitest": "^2.1.0"
  }
}
```

`extension/tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2022", "module": "ESNext", "moduleResolution": "Bundler",
    "strict": true, "skipLibCheck": true, "noEmit": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "types": ["chrome", "vitest/globals"]
  },
  "include": ["src"]
}
```

`extension/build.mjs`：

```js
import * as esbuild from "esbuild";
import { cpSync, mkdirSync } from "node:fs";

mkdirSync("dist/content", { recursive: true });
mkdirSync("dist/options", { recursive: true });
mkdirSync("dist/lib", { recursive: true });
const common = { bundle: true, target: "chrome120", logLevel: "info" };
await esbuild.build({ ...common, entryPoints: ["src/background/service-worker.ts"],
                      outfile: "dist/service-worker.js", format: "esm" });
await esbuild.build({ ...common, entryPoints: ["src/content/bridge.ts"],
                      outfile: "dist/content/bridge.js", format: "iife" });
await esbuild.build({ ...common, entryPoints: ["src/options/options.ts"],
                      outfile: "dist/options/options.js", format: "iife" });
// The protocol gate drives this bundle under node against a real HTTP server.
await esbuild.build({ ...common, entryPoints: ["src/lib/protocol.ts"],
                      outfile: "dist/lib/protocol.js", format: "esm" });
cpSync("src/manifest.json", "dist/manifest.json");
cpSync("src/options/options.html", "dist/options/options.html");
```

`extension/src/manifest.json`：

```json
{
  "manifest_version": 3,
  "name": "Insights 发布助手",
  "version": "0.1.0",
  "description": "把 Insights 的文章推进公众号等国内平台草稿箱",
  "permissions": ["storage", "alarms"],
  "host_permissions": ["https://mp.weixin.qq.com/*"],
  "optional_host_permissions": ["https://*/*", "http://localhost/*", "http://127.0.0.1/*"],
  "background": { "service_worker": "service-worker.js", "type": "module" },
  "content_scripts": [
    { "matches": ["https://mp.weixin.qq.com/*"], "js": ["content/bridge.js"],
      "run_at": "document_idle" }
  ],
  "options_page": "options/options.html"
}
```

`extension/src/version.ts`：

```ts
export const VERSION = "0.1.0";
```

`extension/src/version.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import { VERSION } from "./version";

describe("version", () => {
  it("matches the manifest version", () => {
    expect(VERSION).toBe("0.1.0");
  });
});
```

`extension/NOTICE`：

```text
This extension includes code ported from MultiPost-Extension
(https://github.com/leaperone/MultiPost-Extension), licensed under the
Apache License, Version 2.0. Ported files carry their original header.
Upstream commit at time of port: see extension/upstream/UPSTREAM_SHA.
```

`.gitignore` 追加：

```
extension/node_modules/
extension/dist/
extension/upstream/
```

- [ ] **Step 2: 安装并验证测试/构建**

Run: `cd extension && npm install --no-audit --fund=false && npm test && npm run build`
Expected: vitest 1 项通过；`dist/manifest.json`、`dist/service-worker.js`、`dist/content/bridge.js`、`dist/lib/protocol.js` 存在（protocol.ts 尚不存在，本任务先放一个只 re-export 版本号的占位模块 `extension/src/lib/protocol.ts`：`export const __placeholder = true;`，Task 5 重写）

- [ ] **Step 3: 写门包装脚本** `scripts/check_extension_tests.py`：

```python
"""Gate for the publisher extension: vitest suite passes and dist builds.

    python scripts/check_extension_tests.py    EXTENSION_TESTS_OK

Runs `npm test` and `npm run build` inside extension/. A missing node_modules
fails loudly — a gate that silently skipped would read as coverage.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(os.path.dirname(HERE), "extension")


def _run(args):
    shell = os.name == "nt"
    proc = subprocess.run(args, cwd=EXT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", shell=shell)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main():
    if not os.path.isdir(os.path.join(EXT, "node_modules")):
        print("extension/node_modules missing — run: cd extension && npm install",
              file=sys.stderr)
        return 1
    code, out = _run(["npm", "test"])
    if code != 0:
        print(out, file=sys.stderr)
        return 1
    code, out = _run(["npm", "run", "build"])
    if code != 0:
        print(out, file=sys.stderr)
        return 1
    for artifact in ("dist/manifest.json", "dist/service-worker.js",
                     "dist/content/bridge.js", "dist/lib/protocol.js"):
        if not os.path.isfile(os.path.join(EXT, *artifact.split("/"))):
            print(f"build did not produce {artifact}", file=sys.stderr)
            return 1
    print("EXTENSION_TESTS_OK")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 注册门** —— `run_all_gates.py` 的 `AGENT_GATES` 末尾追加：

```python
    ("check_extension_tests.py", "EXTENSION_TESTS_OK",
     "the publisher extension's vitest suite passes and esbuild emits dist/"),
```

- [ ] **Step 5: 跑门确认通过**

Run: `venv/Scripts/python.exe scripts/check_extension_tests.py`
Expected: `EXTENSION_TESTS_OK`

- [ ] **Step 6: Commit**

```bash
git add extension/package.json extension/tsconfig.json extension/build.mjs extension/src/manifest.json extension/src/version.ts extension/src/version.test.ts extension/src/lib/protocol.ts extension/NOTICE .gitignore scripts/check_extension_tests.py scripts/run_all_gates.py package-lock.json
git commit -m "Extension: scaffold the MV3 publisher with an esbuild build and a vitest gate"
```

（`package-lock.json` 生成在 `extension/` 内，路径为 `extension/package-lock.json`；按实际生成路径 add。）

---

### Task 5: 扩展——settings 与协议客户端（可注入 fetch）

**Files:**
- Create: `extension/src/lib/settings.ts`、`extension/src/lib/protocol.ts`（重写占位）、`extension/src/lib/protocol.test.ts`
- Modify: `extension/src/manifest.json`（无改动，版本仍 0.1.0）

**Interfaces:**
- Produces（Task 7、10、9 消费）:
  - `loadSettings(storage: StorageLike): Promise<Settings>`；`saveSettings(storage, s)`；`interface Settings { serverUrl: string; token: string }`；`interface StorageLike { get(keys: string[]): Promise<Record<string, unknown>>; set(items: Record<string, unknown>): Promise<void> }`
  - `class ProtocolClient { constructor(settings: Settings, fetchImpl?: typeof fetch); heartbeat(payload: object): Promise<void>; claim(body: object): Promise<Job | null>; sendEvent(jobId: number, event: object): Promise<void> }`；`interface Job { id: number; kind: string; payload: string }`
  - 空 token / 空 serverUrl 时 `heartbeat` 抛 `Error("未配置服务器或密钥")`——SW 据此进入退避

- [ ] **Step 1: 写失败测试** `extension/src/lib/protocol.test.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { ProtocolClient, loadSettings } from "./protocol";

const settings = { serverUrl: "https://insights.example", token: "t0" };

function fetchOk(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(new Response(JSON.stringify(body),
    { status, headers: { "Content-Type": "application/json" } }));
}

describe("ProtocolClient", () => {
  it("sends the bearer header and JSON body on heartbeat", async () => {
    const f = fetchOk({ ok: true });
    await new ProtocolClient(settings, f as unknown as typeof fetch)
      .heartbeat({ agent: "insights-ext" });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("https://insights.example/agent/heartbeat");
    expect((init as RequestInit).headers!["Authorization"]).toBe("Bearer t0");
    expect(JSON.parse((init as RequestInit).body as string))
      .toEqual({ agent: "insights-ext" });
  });

  it("maps a 204 claim to null and a 200 claim to the job", async () => {
    const noJob = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await new ProtocolClient(settings, noJob as typeof fetch).claim({}))
      .toBeNull();
    const job = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ id: 7, kind: "article_sync", payload: "{}" }), { status: 200 }));
    expect((await new ProtocolClient(settings, job as typeof fetch).claim({}))!.id)
      .toBe(7);
  });

  it("throws on HTTP errors and on missing configuration", async () => {
    const f = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }));
    await expect(new ProtocolClient(settings, f as typeof fetch).heartbeat({}))
      .rejects.toThrow("HTTP 401");
    await expect(new ProtocolClient({ serverUrl: "", token: "" },
      fetchOk({}) as typeof fetch).heartbeat({})).rejects.toThrow("未配置");
  });

  it("loadSettings trims the server URL and reads chrome storage", async () => {
    const storage = {
      get: async (keys: string[]) =>
        ({ INSIGHTS_SERVER_URL: " https://s.example/ ", INSIGHTS_AGENT_TOKEN: "t" }),
      set: async (_i: Record<string, unknown>) => {},
    };
    const s = await loadSettings(storage as never);
    expect(s).toEqual({ serverUrl: "https://s.example", token: "t" });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd extension && npx vitest run src/lib/protocol.test.ts`
Expected: FAIL（模块不存在或导出缺失）

- [ ] **Step 3: 实现** `extension/src/lib/protocol.ts`：

```ts
import { VERSION } from "../version";

export interface Settings { serverUrl: string; token: string; }

export interface StorageLike {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

export interface Job { id: number; kind: string; payload: string; }

const STORAGE_KEYS = ["INSIGHTS_SERVER_URL", "INSIGHTS_AGENT_TOKEN"];

export async function loadSettings(storage: StorageLike): Promise<Settings> {
  const raw = await storage.get(STORAGE_KEYS);
  return {
    serverUrl: String(raw["INSIGHTS_SERVER_URL"] || "").trim().replace(/\/+$/, ""),
    token: String(raw["INSIGHTS_AGENT_TOKEN"] || ""),
  };
}

export async function saveSettings(storage: StorageLike, s: Settings): Promise<void> {
  await storage.set({
    INSIGHTS_SERVER_URL: s.serverUrl.trim().replace(/\/+$/, ""),
    INSIGHTS_AGENT_TOKEN: s.token,
  });
}

export function configured(s: Settings): boolean {
  return Boolean(s.serverUrl && s.token);
}

export class ProtocolClient {
  private s: Settings;
  private f: typeof fetch;

  constructor(settings: Settings, fetchImpl: typeof fetch = fetch) {
    this.s = settings;
    this.f = fetchImpl;
  }

  private async post(path: string, payload: object, timeoutMs = 15000): Promise<Response> {
    if (!configured(this.s)) throw new Error("未配置服务器或密钥");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await this.f(this.s.serverUrl + path, {
        method: "POST",
        headers: { "Content-Type": "application/json",
                   "Authorization": "Bearer " + this.s.token },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  async heartbeat(payload: object): Promise<void> {
    const resp = await this.post("/agent/heartbeat", payload);
    if (!resp.ok) throw new Error(`/agent/heartbeat -> HTTP ${resp.status}`);
  }

  async claim(body: object): Promise<Job | null> {
    const resp = await this.post("/agent/jobs/claim", body);
    if (resp.status === 204) return null;
    if (!resp.ok) throw new Error(`/agent/jobs/claim -> HTTP ${resp.status}`);
    return (await resp.json()) as Job;
  }

  async sendEvent(jobId: number, event: object): Promise<void> {
    const resp = await this.post(`/agent/jobs/${jobId}/events`, event);
    if (!resp.ok) throw new Error(`/agent/jobs/${jobId}/events -> HTTP ${resp.status}`);
  }

  static clientName(): string {
    return `insights-ext ${VERSION}`;
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd extension && npx vitest run`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add extension/src/lib/protocol.ts extension/src/lib/protocol.test.ts
git commit -m "Extension: settings store and protocol client over injected fetch"
```

---

### Task 6: 扩展——公众号登录检测

**Files:**
- Create: `extension/src/lib/login.ts`、`extension/src/lib/login.test.ts`、`extension/src/lib/fixtures/weixin-logged-in.html`、`extension/src/lib/fixtures/weixin-logged-out.html`

**Interfaces:**
- Produces: `parseWeixinToken(html: string): string`（空串 = 未登录）；`checkWeixinLogin(fetchImpl: typeof fetch): Promise<boolean>`。Task 7 心跳与 Task 8 适配器共用（适配器内不再重复解析逻辑）。

- [ ] **Step 1: 造 fixture 并写失败测试**

`weixin-logged-out.html`：`<html><body><title>微信公众平台</title></body></html>`。
`weixin-logged-in.html`：含一行 `window.wx.commonData = { t: "123456", nick_name: "demo", ticket: "abc", user_name: "gh_demo" };`

`extension/src/lib/login.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { checkWeixinLogin, parseWeixinToken } from "./login";

const dir = new URL("./fixtures/", import.meta.url);
const loggedIn = readFileSync(new URL("weixin-logged-in.html", dir), "utf-8");
const loggedOut = readFileSync(new URL("weixin-logged-out.html", dir), "utf-8");

describe("parseWeixinToken", () => {
  it("finds the token in a logged-in page", () => {
    expect(parseWeixinToken(loggedIn)).toBe("123456");
  });
  it("returns empty on a logged-out page", () => {
    expect(parseWeixinToken(loggedOut)).toBe("");
  });
});

describe("checkWeixinLogin", () => {
  it("is true when the fetched homepage carries a token", async () => {
    const f = (async () => new Response(loggedIn)) as unknown as typeof fetch;
    await expect(checkWeixinLogin(f)).resolves.toBe(true);
  });
  it("is false on network failure rather than throwing", async () => {
    const f = (async () => { throw new Error("down"); }) as unknown as typeof fetch;
    await expect(checkWeixinLogin(f)).resolves.toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd extension && npx vitest run src/lib/login.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现** `extension/src/lib/login.ts`：

```ts
// The mp.weixin.qq.com homepage embeds the CSRF token only for a logged-in
// session; its shape is what the ported adapter also parses (weixin.ts's
// readInfo). One parser, two consumers: heartbeat dots and the adapter.
const TOKEN_PATTERNS = [
  /window\.wx\.commonData\s*=\s*\{[^}]*?\bt:\s*["'](\d+)["']/,
  /"&token\s*=\s*(\d+)"/,
];

export function parseWeixinToken(html: string): string {
  for (const re of TOKEN_PATTERNS) {
    const m = html.match(re);
    if (m) return m[1];
  }
  return "";
}

export const WEIXIN_HOME = "https://mp.weixin.qq.com/";

export async function checkWeixinLogin(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  try {
    const resp = await fetchImpl(WEIXIN_HOME, { credentials: "include" });
    if (!resp.ok) return false;
    return Boolean(parseWeixinToken(await resp.text()));
  } catch {
    return false;
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd extension && npx vitest run`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add extension/src/lib/login.ts extension/src/lib/login.test.ts extension/src/lib/fixtures
git commit -m "Extension: weixin login detection from the mp homepage marker"
```

---

### Task 7: 扩展——service worker 编排（心跳 + claim + 事件回传）

**Files:**
- Create: `extension/src/background/orchestrator.ts`、`extension/src/background/orchestrator.test.ts`、`extension/src/background/service-worker.ts`

**Interfaces:**
- Consumes: Task 5 的 `ProtocolClient`/`loadSettings`，Task 6 的 `checkWeixinLogin`
- Produces:
  - `orchestrator.ts` 导出（可测核心，chrome API 全部经参数注入）：`PLATFORM_IDS = ["wechat"]`；`buildHeartbeat(logins: Record<string, boolean>): object`；`claimBody(platforms: string[]): object`；`runCycle(deps: CycleDeps): Promise<boolean>`；`publishJob(deps: PublishDeps, job: Job): Promise<void>`
  - `interface CycleDeps { client: { heartbeat(p: object): Promise<void>; claim(b: object): Promise<Job | null> }; checkLogins(): Promise<Record<string, boolean>>; busy: { current: number | null } }`
  - `interface PublishDeps { client: 同上; publishOne(target: string, item: { target: string; content: string }, title: string): Promise<PlatformResult> }`；`PlatformResult = { platform: string; success: boolean; draft: boolean; detail?: string; error?: string; code?: string }`
  - `publishJob` 归一：全成 → `{"type":"done","status":"succeeded"}`；有成有败 → `partial`；全败 → `failed`；异常 → `{"type":"failed","retryable":true}`
  - `service-worker.ts` 是薄壳：setInterval 5s claim 循环 + `chrome.alarms.create("ext-poll", {periodInMinutes: 0.5})` 兜底 + `publishOne` 实现（Task 8 前先返回 `unknown_target` 占位——M1 只有 wechat，占位在该任务内替换为真实桥接调用）

- [ ] **Step 1: 写失败测试** `extension/src/background/orchestrator.test.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { buildHeartbeat, claimBody, publishJob, runCycle } from "./orchestrator";

const job = (id: number, executor?: string) => ({
  id, kind: "article_sync",
  payload: JSON.stringify({ title: "T",
    items: [{ target: "wechat", content: "c" }], ...(executor ? { executor } : {}) }),
});

function makeClient(claimResult: unknown) {
  return {
    heartbeat: vi.fn().mockResolvedValue(undefined),
    claim: vi.fn().mockResolvedValue(claimResult),
    sendEvent: vi.fn().mockResolvedValue(undefined),
  };
}

describe("heartbeat and claim shapes", () => {
  it("builds the same heartbeat shape as the python agent", () => {
    expect(buildHeartbeat({ wechat: true })).toEqual({
      agent: "insights-ext", busy: null,
      tools: { extension: { available: true, platformIds: ["wechat"],
        platforms: [{ id: "wechat", logged_in: true }] } },
    });
  });
  it("claims as the extension with its platform capabilities", () => {
    expect(claimBody(["wechat"])).toEqual({
      agent: "insights-ext", executor: "extension", platforms: ["wechat"] });
  });
});

describe("runCycle", () => {
  it("heartbeats then claims; an idle queue returns false", async () => {
    const client = makeClient(null);
    const deps = { client, checkLogins: async () => ({ wechat: true }),
                   busy: { current: null } };
    expect(await runCycle(deps)).toBe(false);
    expect(client.heartbeat.mock.calls[0][0].tools.extension.available).toBe(true);
    expect(client.claim.mock.calls[0][0].executor).toBe("extension");
  });

  it("publishes a claimed job with started and done events", async () => {
    const client = makeClient(job(7));
    const publishOne = vi.fn().mockResolvedValue(
      { platform: "wechat", success: true, draft: true, detail: "已保存草稿" });
    await runCycle({ client, checkLogins: async () => ({}),
                     busy: { current: null }, publishOne });
    const types = client.sendEvent.mock.calls.map((c) => c[1].type);
    expect(types).toEqual(["started", "done"]);
    expect(client.sendEvent.mock.calls[1][1].status).toBe("succeeded");
  });

  it("never runs two jobs at once", async () => {
    const client = makeClient(job(7));
    const deps = { client, checkLogins: async () => ({}),
                   busy: { current: 3 }, publishOne: vi.fn() };
    expect(await runCycle(deps)).toBe(false);
    expect(client.claim).not.toHaveBeenCalled();
  });
});

describe("publishJob result normalization", () => {
  const deps = () => ({ client: makeClient(null),
    publishOne: vi.fn().mockResolvedValue(
      { platform: "wechat", success: true, draft: true }) });

  it("partial when some targets fail", async () => {
    const d = deps();
    const twoItems = { id: 8, kind: "article_sync",
      payload: JSON.stringify({ title: "T", items: [
        { target: "wechat", content: "a" }, { target: "wechat", content: "b" }] }) };
    d.publishOne.mockResolvedValueOnce(
      { platform: "wechat", success: false, draft: false,
        code: "needs_login", error: "未登录" });
    await publishJob(d, twoItems);
    const done = d.client.sendEvent.mock.calls[1][1];
    expect(done.status).toBe("partial");
    expect(done.results[0].code).toBe("needs_login");
  });

  it("all targets failing lands failed with the code kept", async () => {
    const d = deps();
    d.publishOne.mockResolvedValue(
      { platform: "wechat", success: false, draft: false,
        code: "needs_login", error: "未登录" });
    await publishJob(d, job(8));
    const done = d.client.sendEvent.mock.calls[1][1];
    expect(done.status).toBe("failed");
    expect(done.results[0].code).toBe("needs_login");
  });

  it("maps an exception to a retryable failed event", async () => {
    const d = deps();
    d.publishOne.mockRejectedValue(new Error("boom"));
    await publishJob(d, job(9));
    const evt = d.client.sendEvent.mock.calls[1][1];
    expect(evt.type).toBe("failed");
    expect(evt.retryable).toBe(true);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd extension && npx vitest run src/background/orchestrator.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现** `extension/src/background/orchestrator.ts`：

```ts
import { Job, ProtocolClient } from "../lib/protocol";

export const AGENT_NAME = "insights-ext";
export const PLATFORM_IDS = ["wechat"];

export interface PlatformResult {
  platform: string; success: boolean; draft: boolean;
  detail?: string; error?: string; code?: string;
}

export function buildHeartbeat(logins: Record<string, boolean>): object {
  return {
    agent: AGENT_NAME,
    busy: null,
    tools: {
      extension: {
        available: true,
        platformIds: PLATFORM_IDS,
        platforms: PLATFORM_IDS.map((id) => ({ id, logged_in: !!logins[id] })),
      },
    },
  };
}

export function claimBody(platforms: string[]): object {
  return { agent: AGENT_NAME, executor: "extension", platforms };
}

interface BaseClient {
  heartbeat(p: object): Promise<void>;
  claim(b: object): Promise<Job | null>;
  sendEvent(jobId: number, event: object): Promise<void>;
}

export interface CycleDeps {
  client: BaseClient;
  checkLogins(): Promise<Record<string, boolean>>;
  busy: { current: number | null };
  beatClock?: { lastBeat: number };
  beatEverySeconds?: number;
  publishOne?(target: string, item: { target: string; content: string },
              title: string): Promise<PlatformResult>;
}

export async function runCycle(deps: CycleDeps): Promise<boolean> {
  if (deps.busy.current !== null) return false;
  // The claim loop runs every 5s, but the heartbeat (and the login probes it
  // carries) stay on their own 30s cadence: the mp homepage must not be
  // fetched five times more often than the spec's heartbeat window needs.
  const clock = deps.beatClock || { lastBeat: 0 };
  const every = (deps.beatEverySeconds ?? 30) * 1000;
  if (Date.now() - clock.lastBeat >= every) {
    clock.lastBeat = Date.now();
    let logins: Record<string, boolean> = {};
    try {
      logins = await deps.checkLogins();
    } catch { /* a lost login probe must not kill the beat */ }
    await deps.client.heartbeat(buildHeartbeat(logins)).catch(() => {});
  }
  const job = await deps.client.claim(claimBody(PLATFORM_IDS));
  if (!job) return false;
  deps.busy.current = job.id;
  try {
    await publishJob({ client: deps.client, publishOne: deps.publishOne! }, job);
  } finally {
    deps.busy.current = null;
  }
  return true;
}

export interface PublishDeps {
  client: BaseClient;
  publishOne(target: string, item: { target: string; content: string },
             title: string): Promise<PlatformResult>;
}

function unknownTarget(target: string): PlatformResult {
  return { platform: target, success: false, draft: false,
           code: "unknown_target", error: `未知平台 ${target}` };
}

export async function publishJob(deps: PublishDeps, job: Job): Promise<void> {
  await deps.client.sendEvent(job.id,
    { type: "started", agent: AGENT_NAME, tool: "extension" });
  let payload: { title?: string; items?: { target: string; content: string }[] };
  try {
    payload = JSON.parse(job.payload || "{}");
  } catch {
    payload = {};
  }
  const title = payload.title || "未命名";
  const results: PlatformResult[] = [];
  try {
    for (const item of payload.items || []) {
      const known = PLATFORM_IDS.includes(item.target);
      results.push(known
        ? await deps.publishOne(item.target, item, title)
        : unknownTarget(item.target));
    }
  } catch (exc) {
    await deps.client.sendEvent(job.id,
      { type: "failed", error: String(exc), retryable: true });
    return;
  }
  const ok = results.filter((r) => r.success).length;
  const status = results.length && ok === results.length ? "succeeded"
    : ok > 0 ? "partial" : "failed";
  await deps.client.sendEvent(job.id, { type: "done", status, results });
}
```

`extension/src/background/service-worker.ts`（薄壳；`publishOne` 的真实实现在 Task 8，这里先占位）：

```ts
import { checkWeixinLogin } from "../lib/login";
import { configured, loadSettings, ProtocolClient } from "../lib/protocol";
import { PLATFORM_IDS, PlatformResult, runCycle } from "./orchestrator";

const busy = { current: null as number | null };
const beatClock = { lastBeat: 0 };
// Login probes hit the mp homepage: cache for 30s so the 5s claim loop and
// the 30s heartbeat share one fetch at most.
let loginCache = { at: 0, value: {} as Record<string, boolean> };

async function checkLogins(): Promise<Record<string, boolean>> {
  if (Date.now() - loginCache.at < 30000) return loginCache.value;
  const logins: Record<string, boolean> = {};
  for (const id of PLATFORM_IDS) {
    logins[id] = id === "wechat" ? await checkWeixinLogin() : false;
  }
  loginCache = { at: Date.now(), value: logins };
  return logins;
}

async function cycle(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  if (!configured(settings)) return;
  const client = new ProtocolClient(settings);
  await runCycle({ client, checkLogins, busy, beatClock,
                   publishOne: publishViaBridgePlaceholder });
}

// Placeholder until the bridge lands: refuse honestly instead of pretending.
async function publishViaBridgePlaceholder(target: string): Promise<never> {
  throw Object.assign(new Error(`平台 ${target} 的适配器尚未接入`), { code: "unknown_target" });
}

chrome.alarms.create("ext-poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => { void cycle(); });
setInterval(() => { void cycle(); }, 5000);
void cycle();
```

- [ ] **Step 4: 跑测试并构建**

Run: `cd extension && npx vitest run && npm run build`
Expected: 测试全 PASS；构建成功

- [ ] **Step 5: Commit**

```bash
git add extension/src/background
git commit -m "Extension: orchestrator heartbeats, claims as the extension and reports events"
```

---

### Task 8: 扩展——公众号适配器搬运与 content bridge

**Files:**
- Create: `extension/upstream/UPSTREAM_SHA`、`extension/src/content/adapters/weixin.ts`（搬运+改造）、`extension/src/content/bridge.ts`、`extension/src/lib/markdown.ts`、`extension/src/lib/markdown.test.ts`、`extension/src/content/adapters/weixin.test.ts`
- Modify: `extension/src/background/service-worker.ts`（`publishOne` 换成真实桥接）、`extension/NOTICE`
- Create: `extension/E2E.md`（真机验收清单）

**Interfaces:**
- Consumes: Task 6 的 `parseWeixinToken`；Task 7 的 `PlatformResult`
- Produces:
  - `markdown.ts`: `markdownToHtml(md: string): string`（marked）、`buildDigest(md: string, max = 120): string`（剥标记取前 120 字）
  - `adapters/weixin.ts`: `export async function publishWeixinArticle(payload: { title: string; content: string }): Promise<{ ok: true; detail: string } | { ok: false; code: "needs_login" | "sync_failed" | "timeout"; error: string }>`——在 mp.weixin.qq.com 页面上下文执行
  - `bridge.ts`: 监听 SW 消息 `{ type: "publish", payload }`，调适配器并回 `{ ok, ... }`
  - `service-worker.ts` 的 `publishOne("wechat", ...)`: 开后台标签页 `https://mp.weixin.qq.com/`（`active: false`）→ 等 `status === "complete"` → `chrome.tabs.sendMessage` → 收结果 → `finally` 关标签页；把 `{ ok: false, code }` 映射为 `PlatformResult`（`draft: true` 仅当 `ok: true`）

- [ ] **Step 1: 记录上游版本并取源文件**

```bash
mkdir -p extension/upstream extension/src/content/adapters
git ls-remote https://github.com/leaperone/MultiPost-Extension HEAD | cut -f1 > extension/upstream/UPSTREAM_SHA
python - <<'PY'
import json, urllib.request
def fetch(path, out):
    req = urllib.request.Request(
        f"https://api.github.com/repos/leaperone/MultiPost-Extension/contents/{path}?ref=main")
    d = json.load(urllib.request.urlopen(req))
    import base64
    open(out, "w", encoding="utf-8", newline="\n").write(base64.b64decode(d["content"]).decode("utf-8"))
fetch("src/sync/article/weixin.ts", "extension/upstream/weixin.upstream.ts")
PY
```

- [ ] **Step 2: 通读 `extension/upstream/weixin.upstream.ts` 并按以下规则改造到 `adapters/weixin.ts`**

改造规则（逐条执行，写进文件头注释）：
1. 文件头保留上游 Apache-2.0 版权声明 + 一行 `Ported from MultiPost-Extension src/sync/article/weixin.ts, adapted for the Insights publisher (draft-first, no float UI changes removed where they touched the DOM only).`
2. 入口改为 `publishWeixinArticle({title, content})`：内部先 `parseWeixinToken` 复用 Task 6 逻辑——解析入口 `readInfo()` 改为调用 `parseWeixinToken(document.documentElement.innerHTML)`；**token 为空立即返回 `{ ok: false, code: "needs_login", error: "公众号未登录，请在本浏览器登录 mp.weixin.qq.com" }`**（Review Focus #5）
3. `SyncData` 不再从上游引入：文件内定义最小 `ArticleData` 形状（`{ title: string; digest: string; htmlContent: string; images?: {url: string}[]; cover?: {url: string} }`），`digest` 用 `buildDigest(content)`，`htmlContent` 用 `markdownToHtml(content)`
4. 上游的封面裁剪/`share_imageinfo` 逻辑保留但允许无封面：`articleData.cover` 缺省时跳过封面字段（沿用上游对该分支的任何空值处理；若上游硬依赖 cover，则取正文第一张图，正文无图时用内置 1×1 兜底图并在此注释说明）
5. 删除上游悬浮进度提示（shadow DOM tip）与 console.log 调试输出；保留 `operate_appmsg` 建草稿调用（`sub=create`，即草稿语义，**不得**改为发布/群发端点）
6. 返回值映射：`result.appMsgId` 存在 → `{ ok: true, detail: "已保存到公众号草稿箱" }`；`base_resp.err_msg` 含「登录」/「token」→ `needs_login`；其余 → `sync_failed` 带 err_msg；`fetch` 异常/超时（外层 240s AbortController）→ `timeout`
7. 上游内联的图片上传函数（`uploadImageBySource`/`uploadImage`/`getImageInfo`/裁剪计算）原样保留为模块内私有函数

- [ ] **Step 3: 写纯逻辑测试** `extension/src/lib/markdown.test.ts` 与 `extension/src/content/adapters/weixin.test.ts`：

```ts
// markdown.test.ts
import { describe, expect, it } from "vitest";
import { buildDigest, markdownToHtml } from "./markdown";

describe("markdownToHtml", () => {
  it("renders headings, paragraphs and quotes", () => {
    const html = markdownToHtml("# 标题\n\n正文段落\n> 引用");
    expect(html).toContain("<h1>标题</h1>");
    expect(html).toContain("<p>正文段落</p>");
    expect(html).toContain("<blockquote>");
  });
});

describe("buildDigest", () => {
  it("strips markdown and truncates to 120 chars", () => {
    const md = "# " + "标".repeat(200);
    expect(buildDigest(md)).toHaveLength(120);
    expect(buildDigest(" short ")).toBe("short");
  });
});
```

```ts
// weixin.test.ts — 纯函数部分；适配器主体靠 Task 11 的真机门
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { classifyWeixinError } from "./weixin";

const loggedOut = readFileSync(
  new URL("../../lib/fixtures/weixin-logged-out.html", import.meta.url), "utf-8");

describe("classifyWeixinError", () => {
  it("maps login-ish base_resp messages to needs_login", () => {
    expect(classifyWeixinError("invalid token", "")).toEqual({
      code: "needs_login", error: "invalid token" });
    expect(classifyWeixinError("登录已过期", "")).toEqual({
      code: "needs_login", error: "登录已过期" });
  });
  it("maps everything else to sync_failed", () => {
    expect(classifyWeixinError("内容包含敏感词", "").code).toBe("sync_failed");
  });
  it("an empty token from the page is needs_login", () => {
    expect(classifyWeixinError("", loggedOut).code).toBe("needs_login");
  });
});
```

（`classifyWeixinError(errMsg: string, pageHtml: string): { code: "needs_login" | "sync_failed"; error: string }` 是从改造中提取的纯函数：`pageHtml` 无 token 或 errMsg 含「登录/token」→ `needs_login`。）

- [ ] **Step 4: 跑测试确认通过并构建**

Run: `cd extension && npx vitest run && npm run build`
Expected: 全 PASS；构建成功

- [ ] **Step 5: 接线 bridge 与 service worker**

`extension/src/content/bridge.ts`：

```ts
import { publishWeixinArticle } from "./adapters/weixin";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "publish") return false;
  publishWeixinArticle(msg.payload as { title: string; content: string })
    .then(sendResponse)
    .catch((exc) => sendResponse({ ok: false, code: "sync_failed", error: String(exc) }));
  return true;  // async response
});
```

`service-worker.ts` 的 `publishOne` 真实实现替换占位：

```ts
const WEIXIN_HOME = "https://mp.weixin.qq.com/";

async function publishOne(target: string,
                          item: { target: string; content: string },
                          title: string): Promise<PlatformResult> {
  if (target !== "wechat") {
    return { platform: target, success: false, draft: false,
             code: "unknown_target", error: `未知平台 ${target}` };
  }
  const tab = await chrome.tabs.create({ url: WEIXIN_HOME, active: false });
  try {
    await waitTabComplete(tab.id!);
    const resp = await chrome.tabs.sendMessage(tab.id!,
      { type: "publish", payload: { title, content: item.content } });
    return resp?.ok
      ? { platform: "wechat", success: true, draft: true, detail: resp.detail }
      : { platform: "wechat", success: false, draft: false,
          code: resp?.code || "sync_failed", error: resp?.error || "适配器无响应" };
  } catch (exc) {
    return { platform: "wechat", success: false, draft: false, code: "sync_failed",
             error: String(exc) };
  } finally {
    chrome.tabs.remove(tab.id!).catch(() => {});
  }
}

function waitTabComplete(tabId: number, timeoutMs = 30000): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("标签页加载超时"));
    }, timeoutMs);
    const listener = (id: number, info: chrome.tabs.TabChangeInfo) => {
      if (id === tabId && info.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}
```

- [ ] **Step 6: 再跑测试与构建，写真机验收清单** `extension/E2E.md`：

```markdown
# 真机验收（M1·公众号）

前置：dist/ 已构建；chrome://extensions 开发者模式加载 dist/；扩展选项页填服务器地址与
AGENT_TOKEN；本浏览器已扫码登录 mp.weixin.qq.com。

1. 扩展选项页显示公众号「已登录」。
2. Insights compose 页对公众号卡片点「推送到公众号草稿箱」。
3. 预期：后台开一个 mp 标签页、自动填图文、出现「已推入草稿」结果；标签页自动关闭。
4. 到 mp.weixin.qq.com 后台「内容与互动-草稿箱」确认：标题、摘要、正文、图片完整，
   状态为草稿（未群发）。
5. 退出公众号登录后重推：compose 显示「未登录」，扩展不建标签页（心跳已灰）。
6. 连续 3 次验收通过 → 在服务器设置 DOMESTIC_EXECUTORS 写 {"wechat": "extension"} 完成
   wechat 毕业。
```

- [ ] **Step 7: Commit**

```bash
git add extension/upstream/UPSTREAM_SHA extension/src/content extension/src/lib/markdown.ts extension/src/lib/markdown.test.ts extension/src/background/service-worker.ts extension/NOTICE extension/E2E.md
git commit -m "Extension: port the weixin draft adapter and bridge it to the service worker"
```

---

### Task 9: 扩展——设置页

**Files:**
- Create: `extension/src/options/options.html`、`extension/src/options/options.ts`、`extension/src/options/options.test.ts`

**Interfaces:**
- Consumes: Task 5 的 `loadSettings`/`saveSettings`/`ProtocolClient`，Task 6 的 `checkWeixinLogin`
- Produces: 选项页两个输入（服务器地址、AGENT_TOKEN 密码框）+「保存并测试」按钮 + 公众号登录状态行。保存时对服务器来源 `chrome.permissions.request({origins: [origin + "/*"]})`，拒绝则不保存并提示。

- [ ] **Step 1: 写失败测试**（纯逻辑部分：权限 origin 推导与状态文案）

```ts
// options.test.ts
import { describe, expect, it } from "vitest";
import { originPattern, statusText } from "./options";

describe("originPattern", () => {
  it("derives the match pattern from the server URL", () => {
    expect(originPattern("https://insights.example/")).toBe("https://insights.example/*");
    expect(originPattern("http://localhost:8000")).toBe("http://localhost:8000/*");
  });
  it("returns null for a non-http URL", () => {
    expect(originPattern("ftp://x")).toBeNull();
  });
});

describe("statusText", () => {
  it("renders saved, unauthorized and offline states", () => {
    expect(statusText("ok")).toBe("已连接");
    expect(statusText("unauthorized")).toBe("密钥被拒绝（401），请核对 AGENT_TOKEN");
    expect(statusText("unreachable")).toBe("服务器不可达，请检查地址");
  });
});
```

- [ ] **Step 2: 跑测试确认失败** → **Step 3: 实现**

`options.ts` 导出上述两个纯函数并实现页面逻辑：

```ts
export function originPattern(serverUrl: string): string | null {
  try {
    const u = new URL(serverUrl);
    return u.protocol.startsWith("http") ? `${u.protocol}//${u.host}/*` : null;
  } catch {
    return null;
  }
}

export function statusText(state: "ok" | "unauthorized" | "unreachable" | "saving"): string {
  if (state === "ok") return "已连接";
  if (state === "unauthorized") return "密钥被拒绝（401），请核对 AGENT_TOKEN";
  if (state === "unreachable") return "服务器不可达，请检查地址";
  return "保存中…";
}
```

页面逻辑：加载时回填设置；点击保存 → `originPattern` 为 null 则报错；`chrome.permissions.request` 通过后 `saveSettings` → 发一条测试 heartbeat → 按 401/网络错误/成功渲染 `statusText`；随后 `checkWeixinLogin()` 渲染「公众号：已登录/未登录」。

`options.html`：最小表单（两个 input、一个按钮、两个状态行），无框架。

- [ ] **Step 4: 跑测试并构建**

Run: `cd extension && npx vitest run && npm run build`
Expected: 全 PASS；`dist/options/options.html` 与 `dist/options/options.js` 存在

- [ ] **Step 5: Commit**

```bash
git add extension/src/options
git commit -m "Extension: options page pairs with the server and shows login state"
```

---

### Task 10: 协议真机门——编译后的客户端对真实 HTTP

**Files:**
- Create: `scripts/check_extension_protocol.py`、`scripts/extension_protocol_driver.mjs`
- Modify: `scripts/run_all_gates.py`（注册）

**Interfaces:**
- Consumes: `extension/dist/lib/protocol.js`（Task 4 构建产物）、Task 2 的 claim 过滤
- Produces: 门 token `EXTENSION_PROTOCOL_OK`

- [ ] **Step 1: 写 Node 驱动** `scripts/extension_protocol_driver.mjs`：

```js
// Drives the compiled protocol client against a real HTTP server passed as
// argv[2]; argv[3] is the token. Fails loudly on any step; prints DRIVER_OK.
import { ProtocolClient, loadSettings } from "../extension/dist/lib/protocol.js";

const [baseUrl, token] = process.argv.slice(2);
const fakeStorage = {
  get: async () => ({ INSIGHTS_SERVER_URL: baseUrl, INSIGHTS_AGENT_TOKEN: token }),
  set: async () => {},
};
const settings = await loadSettings(fakeStorage);
const client = new ProtocolClient(settings);

await client.heartbeat({ agent: "insights-ext",
  tools: { extension: { available: true, platformIds: ["wechat"],
    platforms: [{ id: "wechat", logged_in: true }] } } });

const miss = await client.claim({ agent: "insights-ext", executor: "extension",
  platforms: ["wechat"] });
if (miss !== null) throw new Error("expected 204 on an empty matching queue");

const job = await client.claim({ agent: "insights-ext", executor: "extension",
  platforms: ["wechat"] });
if (!job || job.kind !== "article_sync") throw new Error("expected the stamped job");
await client.sendEvent(job.id, { type: "started", agent: "insights-ext",
  tool: "extension" });
await client.sendEvent(job.id, { type: "done", status: "succeeded",
  results: [{ platform: "wechat", success: true, draft: true }] });
console.log("DRIVER_OK");
```

- [ ] **Step 2: 写门脚本** `scripts/check_extension_protocol.py`（骨架照 `check_agent_agent.py` 的 HTTPServer 模式，全量代码）：

```python
"""Gate for the compiled extension protocol client over real HTTP.

    python scripts/check_extension_protocol.py    EXTENSION_PROTOCOL_OK

The node driver (extension_protocol_driver.mjs) runs the built
dist/lib/protocol.js against this server: heartbeat accepted, claim honours
the executor filter, events recorded. Requires `npm run build` first — run
check_extension_tests.py before this one.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "ext-gate-token"
_state = {"jobs": [], "events": [], "heartbeats": []}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._json(401, {"error": "unauthorized"})
        if self.path == "/agent/heartbeat":
            _state["heartbeats"].append(body)
            return self._json(200, {"ok": True})
        if self.path == "/agent/jobs/claim":
            executor = body.get("executor") or "agent"
            platforms = body.get("platforms")
            for i, job in enumerate(list(_state["jobs"])):
                payload = json.loads(job["payload"])
                if (payload.get("executor") or "agent") != executor:
                    continue
                targets = [it["target"] for it in payload.get("items", [])]
                if platforms and not all(t in platforms for t in targets):
                    continue
                _state["jobs"].pop(i)
                return self._json(200, job)
            self.send_response(204)
            self.end_headers()
            return
        if self.path.startswith("/agent/jobs/") and self.path.endswith("/events"):
            _state["events"].append(body)
            return self._json(200, {})
        return self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def main():
    from _accounts_gate import check

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _state["jobs"].append({
        "id": 1, "kind": "article_sync",
        "payload": json.dumps({"title": "T",
                               "items": [{"target": "wechat", "content": "x"}],
                               "executor": "extension"})})
    driver = os.path.join(HERE, "extension_protocol_driver.mjs")
    proc = subprocess.run(
        ["node", driver, f"http://127.0.0.1:{srv.server_port}", TOKEN],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, shell=(os.name == "nt"), timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    check(proc.returncode == 0 and "DRIVER_OK" in out,
          f"the compiled client completes heartbeat→claim→events ({out.strip()[:300]})")
    check(any(h.get("tools", {}).get("extension") for h in _state["heartbeats"]),
          "the heartbeat arrived in the extension's shape")
    check(len(_state["events"]) == 2
          and _state["events"][0]["type"] == "started"
          and _state["events"][1]["status"] == "succeeded",
          "started and done events were recorded")
    print("EXTENSION_PROTOCOL_OK")
    srv.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 跑门确认失败→修复→通过**

Run: `venv/Scripts/python.exe scripts/check_extension_protocol.py`
Expected: 先确认 `extension/dist/lib/protocol.js` 存在（先跑 `cd extension && npm run build`），然后 `EXTENSION_PROTOCOL_OK`

- [ ] **Step 4: 注册门** —— `AGENT_GATES` 再追加：

```python
    ("check_extension_protocol.py", "EXTENSION_PROTOCOL_OK",
     "the compiled extension client heartbeats, claims only its lane and reports events over real HTTP"),
```

- [ ] **Step 5: Commit**

```bash
git add scripts/check_extension_protocol.py scripts/extension_protocol_driver.mjs scripts/run_all_gates.py
git commit -m "Gate: drive the compiled extension protocol client against real HTTP"
```

---

### Task 11: 全量门回归与收尾

**Files:**
- Modify: 无新代码；只修回归

- [ ] **Step 1: 跑全量门**

Run: `venv/Scripts/python.exe scripts/run_all_gates.py`
Expected: `ALL_GATES_OK`（任何 FAIL 都在本任务内修复——修时遵循 TDD：先让失败的门表达新期望，再改实现）

- [ ] **Step 2: TS 类型检查**

Run: `cd extension && npx tsc --noEmit`
Expected: 无错误（manifest 的 chrome 类型需要 `npm i -D @types/chrome`，缺则安装）

- [ ] **Step 3: 收尾检查与提交**

```bash
git log origin/main..HEAD --format=%B | grep -iE '^(Co-Authored-By|Claude-Session):' || echo CLEAN
git log origin/main..HEAD --format='%an <%ae> | %cn <%ce>' | sort -u
git status --short   # 确认并行会话的模板改动未被裹挟提交
```

Expected: trailer 无输出（CLEAN）、作者只有 l0lsec no-reply 一行、工作区只剩并行会话的文件。

- [ ] **Step 4: Commit（如有修复）**

```bash
git add -A -- ':!templates' ':!static'   # 只加本任务实际修改的文件，勿裹挟并行会话改动
git commit -m "Gates: full-suite pass for the publisher extension M1 slice"
```

---

## 里程碑之外的已知边界（写给执行者）

- M2（知乎）/M3（头条）/M4（小红书）不在本计划；`PLATFORM_IDS` 与 `PLATFORMS` 注册表就是它们的接入点
- 真机验收（`extension/E2E.md`）需要所有者扫码登录后人工执行，无法自动化；它是「毕业」的判定依据，不是门的替代品
- `WECHATSYNC_ENABLED_PLATFORMS` 的改名迁移（`DOMESTIC_ENABLED_PLATFORMS`）在全毕业时做，不在本计划
