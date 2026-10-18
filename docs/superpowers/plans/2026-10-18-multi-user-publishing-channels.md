---
change: multi-user-publishing-channels
design-doc: docs/superpowers/specs/2026-10-18-multi-user-publishing-channels-design.md
base-ref: c1421e379e10d2b1679d3d111c05ab4736475c85
---

# 发布通道按用户隔离（multi-user-publishing-channels）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把四类通道资产（扩展 token、heartbeat 登录态、social_accounts 记录、agent job）全部按用户隔离，发布严格按帖主通道、他人通道零兜底，扩展协议零改动。

**Architecture:** 新表 `agent_tokens`（sha256 哈希落库、按用户签发、可吊销）替代全局 `AGENT_TOKEN`；`/agent/*` 端点经 Bearer token 查表解析 user_id；heartbeat 内存结构从全局单桶改为 `user_id -> {tool -> beat}`；`agent_jobs`/`social_accounts` 加 `user_id` 列并把存量回填 admin；队列页的通道原因在渲染时派生、不入库；不新建调度器（`SCHEDULABLE_PLATFORMS = ()`，入队/run_after 路径带帖主 user_id 即为 D9.3 的全部落地）。

**Tech Stack:** Python / Flask / SQLite（同步 sqlite3，`with conn` 事务）；gate 脚本 = 仓库惯例的 `scripts/check_*.py` 自证 OK token；扩展侧 TypeScript（本仓 `extension/`，目标零改动）。

**Spec:** `docs/superpowers/specs/2026-10-18-multi-user-publishing-channels-design.md`（9 项决策 D1–D9，含 builder check 裁定，实施事实源）；上游契约 `openspec/changes/multi-user-publishing-channels/`（proposal / design / tasks / delta spec）。执行者两份都读，冲突以 OpenSpec 产物为准。

## Global Constraints

- 所有 Python 一律 `venv/Scripts/python.exe` 运行（普通 python 缺 flask）。
- gate 脚本样板：`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` 后 `from _accounts_gate import isolated_app, check`；涉及中文诊断先 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（参照 `scripts/check_content_isolation.py:44-46`）。gate 通过条件 = exit 0 且打印自己的 OK token。
- 每个 `scripts/check_*.py` 必须注册进 `scripts/run_all_gates.py`（三元组 `(文件, OK token, 一句话)` 或四元组末尾加 section 元组），未注册 gate 会让 run_all_gates 直接失败。
- 扩展协议零改动：`/agent/pair` 响应 `{"token": string}`；claim 204/200 job 形状不变；events 200/400 不变；`extension/src/lib/protocol.ts`、`extension/src/background/orchestrator.ts` 不得修改。
- token 明文不落库：`agent_tokens` 只存 sha256 hex；明文只在签发响应中出现一次。
- 他人通道零兜底：任何登录态 / job / 账号记录读取必须带 user 维度；跨用户访问与资源不存在的 HTTP 响应体逐字节一致（沿用 ① 惯例，均为 `{"error": "not found"}` 404）。
- 每任务结束必须 `venv/Scripts/python.exe scripts/run_all_gates.py` 全绿才能 commit；因行为变更导致既有 gate 失败的，修复属于本任务，不得留到后面。
- 行号锚点为 2026-10-18 基线（base-ref `c1421e37`），实施时以符号名为准。
- commit 不加任何 attribution（仓库 CLAUDE.md 常设规则）。

## Review Focus

规格隐含、但单任务测试最易漏的五类输入，每条已在归属任务的步骤里钉了测试：

1. **匿名 redeem 的码继承语义**：A 铸的码被任意浏览器兑换，签发的必须是 A 的 token（码即授权转移，D3）——钉在 Task 3 步骤 1 的 `test_redeem_issues_minter_token`。
2. **吊销滞后**：revoked=1 的 token 必须在每个 `/agent/*` 请求上立即 401（每请求直查表，不缓存）——钉在 Task 2 `test_revoked_token_is_401_everywhere`。
3. **迁移幂等**：init_db 二连跑不得重复签发 admin token、不得重复回填——钉在 Task 1/Task 5 schema 节的「连跑两遍」断言。
4. **存在性旁路**：跨用户读 job 与读不存在的 job 必须逐字节同响应——钉在 Task 2 `test_cross_user_read_matches_missing_byte_for_byte`。
5. **他人在线误推**：B 的 heartbeat 在线不得让 A 的即时 push 通过、不得让 A 的 job 被 B 认领——钉在 Task 6 `test_other_users_heartbeat_does_not_unlock_push` 与 Task 9 e2e。

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `database.py` | 修改 | agent_tokens 建表+token 函数+迁移（Task 1）；agent_jobs 加 user_id（Task 2）；social_accounts 加 user_id+放宽唯一索引（Task 5） |
| `agent_hub.py` | 修改 | agent_credentials / issue_agent_token / 配对绑定（Task 2、3）；heartbeat 分桶（Task 4） |
| `insights_web.py` | 修改 | /agent/* 端点按 token 解析用户、读路径收口（Task 2）；配对端点（Task 3）；登录态消费端按用户（Task 4）；/accounts 过滤+token 吊销路由（Task 5）；push 链路按帖主（Task 6）；队列页派生原因（Task 7） |
| `domestic_publisher.py` | 修改 | enqueue_card_push 带 user_id（Task 6） |
| `templates/accounts.html` | 修改 | 本人 token 列表 + 吊销 UI（Task 5） |
| `templates/agent_jobs.html` | 修改 | pending 行渲染派生通道原因（Task 7） |
| `templates/base.html` | **不改** | `agent_online`/`login_states` 由 context processor 注入，按用户是数据源变化，模板零改动 |
| `templates/compose.html`、`templates/partials/post_item.html` | **不改** | 同上（`compose.html:703-707` 的 `ok = (state=='ok')` 自动跟随按用户的 login_states） |
| `scripts/check_channel_isolation.py` | 新建 | 本变更的 gate：schema / tokens / heartbeat / accounts / publish / e2e 六节，随任务逐节落地 |
| `scripts/run_all_gates.py` | 修改 | 注册上述 gate 的每个 section |
| 既有 agent 系 gate（`check_agent_hub.py`、`check_agent_jobs.py`、`check_agent_ui.py`、`check_login_states.py`、`check_agent_push.py`、`check_domestic_publisher.py`） | 修改 | 适配新签名与新语义（分散在各任务） |
| `extension/src/**` | **不改** | 零改动目标，Task 8 只验证 |

### tasks.md 11 项 → 计划任务映射

| tasks.md | 计划任务 |
|----------|----------|
| 1.1 agent_tokens 表 + 迁移 | Task 1 |
| 1.2 配对码绑定铸造者 | Task 3 |
| 1.3 公开端点按 token 解析用户 | Task 2 |
| 2.1 heartbeat 按用户存储/查询 | Task 4 |
| 2.2 /accounts 按用户 | Task 5 |
| 2.3 social_accounts 隔离迁移 | Task 5 |
| 3.1 compose 选择器按当前用户 | Task 4（context 注入按用户后 `compose.html:703-707` 自动生效）+ Task 4 gate 断言 |
| 3.2 发布入队/即时发布按帖主 | Task 2（数据层）+ Task 6（路由链路） |
| 3.3 调度按归属、排队标注原因 | Task 2（enqueue/run_after 带 user_id 即 D9.3 全部，无调度器可改）+ Task 7（渲染时派生原因） |
| 4.1 扩展零改动验证 | Task 8 |
| 4.2 端到端验收 | Task 9 |

---

### Task 1: `agent_tokens` 表、token 存取函数与存量全局密钥迁移

**Files:**
- Modify: `database.py`（init_db 内 agent_jobs 建表块之后，约 :621-646；函数放在 agent jobs 函数段附近）
- Test: `scripts/check_channel_isolation.py`（新建，section `schema`）
- Modify: `scripts/run_all_gates.py`（新增注册条目）

**Interfaces:**
- Consumes: 既有 `get_setting`/`app_settings` 表（`database.py:731,743`）。
- Produces（后续任务依赖的精确签名，均在 `database.py`）:
  - `hash_agent_token(token: str) -> str`（sha256 hexdigest）
  - `create_agent_token(user_id: int, token: str, label: str = "", db_path: str = DB_PATH) -> int`
  - `find_agent_user_by_token(token: str, db_path: str = DB_PATH) -> Optional[int]`（未命中/已吊销 → None。命名刻意区别于 Task 2 的 `agent_hub.resolve_agent_user()`：本函数收显式 token 参数，后者读请求上下文）
  - `touch_agent_token(token: str, db_path: str = DB_PATH) -> None`（更新 last_seen）
  - `list_agent_tokens(user_id: int, db_path: str = DB_PATH) -> List[dict]`（返回 id/label/created_at/last_seen/revoked，不含明文与哈希）
  - `revoke_agent_token(token_id: int, user_id: int, db_path: str = DB_PATH) -> bool`（WHERE id=? AND user_id=?，只能吊销本人）

- [ ] **Step 1: 写失败测试**——新建 `scripts/check_channel_isolation.py`，骨架照 `check_content_isolation.py`（UTF-8 reconfigure、`_accounts_gate` 导入、按 `sys.argv[1]` 分节、每节末打印 `CHANNEL_ISOLATION_<SECTION>_OK`）。section `schema` 断言：
  - 临时库 `init_db` 连跑两遍不抛错（幂等）；`agent_tokens` 表存在且含列 `token_hash/user_id/label/created_at/last_seen/revoked`，`token_hash` UNIQUE；
  - `set_setting("AGENT_TOKEN", "legacy-secret")` 后再跑一遍 `init_db` → 恰好出现一行，`find_agent_user_by_token("legacy-secret") == admin_uid`（admin = isolated_app 里 `create_user` 返回的首号）、`find_agent_user_by_token("wrong") is None`；再跑一遍 init_db 仍恰好一行（不重复签发）；
  - 不设 AGENT_TOKEN 的全新库 → `agent_tokens` 零行；
  - `create_agent_token(uid, "t2", "B 的浏览器")` → `find_agent_user_by_token("t2") == uid`；`revoke_agent_token(该行 id, uid)` 后 `find_agent_user_by_token("t2") is None`；`revoke_agent_token(该行 id, 其他用户 id) is False`；
  - `touch_agent_token("t2")` 后 `list_agent_tokens(uid)` 该行 `last_seen` 非空；`list_agent_tokens` 不返回任何等于 "t2" 或其 sha256 的字段（明文/哈希不出现在列表里）。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py schema`，预期 FAIL（表不存在）。

- [ ] **Step 3: 实现**——`database.py`：
  - init_db 内按 D2 原文建表（`token_hash TEXT NOT NULL UNIQUE`、`user_id INTEGER NOT NULL`、`label TEXT DEFAULT ''`、`created_at TEXT NOT NULL`、`last_seen TEXT`、`revoked INTEGER DEFAULT 0`）；
  - 迁移（同一条连接内，幂等）：`SELECT value FROM app_settings WHERE key='AGENT_TOKEN'`，为空回退 `os.environ.get("AGENT_TOKEN")`；值非空且 `SELECT MIN(id) FROM users` 非 NULL 且该 sha256 无行时，INSERT `(hash, admin, '存量全局密钥', now)`；
  - 六个函数按 Interfaces 签名实现（均为一次 `with sqlite3.connect` 的直查，无缓存）。

- [ ] **Step 4: 跑测试确认通过**——同 Step 2 命令，预期打印 `CHANNEL_ISOLATION_SCHEMA_OK`。注册进 run_all_gates.py（`("check_channel_isolation.py", "CHANNEL_ISOLATION_SCHEMA_OK", "agent_tokens 建表与存量全局密钥一次性映射 admin，吊销即失效", ("schema",))`）。

- [ ] **Step 5: 全量 gate + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add database.py scripts/check_channel_isolation.py scripts/run_all_gates.py
git commit -m "agent_tokens 表：按用户签发 token 并把存量全局密钥映射给 admin"
```

---

### Task 2: agent_jobs 归属 + token 解析用户 + `/agent/*` 端点与读路径收口

tasks.md 1.3 + D5 + D9.2（events 归属、读路径过滤）+ 3.2 数据层 + 3.3 的「enqueue/run_after 带 user_id」。

**Files:**
- Modify: `database.py`（agent_jobs 建表块 :621-646；`enqueue_agent_job:4637`、`claim_agent_job:4676`、`list_agent_jobs:4820`）
- Modify: `agent_hub.py:37-50`（`check_agent_auth` → `agent_credentials`）
- Modify: `insights_web.py:6139-6143`（agent_job_json）、`:6197-6207`（agent_jobs_page）、`:6209-6213`（heartbeat 端点）、`:6215-6241`（claim 端点）、`:6243-6253`（events 端点）
- Modify: `domestic_publisher.py:87-118`（`enqueue_card_push` 带 user_id）、`insights_web.py:6110-6137`（`compose_push_post` 传 uid）
- Test: `scripts/check_channel_isolation.py`（section `tokens`）
- Modify（既有 gate 适配）: `scripts/check_agent_hub.py`、`scripts/check_agent_jobs.py`、`scripts/check_agent_ui.py`、`scripts/check_domestic_publisher.py`、`scripts/check_agent_push.py`

**Interfaces:**
- Consumes: Task 1 的 `find_agent_user_by_token`/`create_agent_token`/`touch_agent_token`。
- Produces:
  - `database.enqueue_agent_job(kind, payload, *, run_after=None, max_attempts=5, article_id=None, created_by=None, user_id=None, db_path=DB_PATH)`——`user_id is None` 抛 `ValueError`（NULL job 永远无人认领，静默卡死比炸更糟；沿用 ① 写入口惯例）。
  - `database.claim_agent_job(*, user_id: int, kinds=("article_sync",), agent="", stale_minutes=15, executor=None, platforms=None, db_path=DB_PATH)`——`user_id` 必填 keyword，候选 SELECT 追加 `AND user_id = ?`。
  - `database.list_agent_jobs(*, limit=100, user_id: Optional[int] = None, db_path=DB_PATH)`——None=全量（仅供 gate/内部；页面路由必须传本人）。
  - `agent_hub.agent_credentials() -> tuple[int, str] | None`（读请求 Bearer → sha256 → `database.find_agent_user_by_token`；命中返回 `(user_id, 原始token)`，否则 None。替换 `check_agent_auth`，后者删除。返回原始 token 供 heartbeat 端点调 `database.touch_agent_token(token)` 刷新 last_seen，避免二次解析请求头）。
  - `domestic_publisher.enqueue_card_push(row, *, run_after=None, created_by=None, user_id: int, db_path=None)`——`user_id` 必填 keyword，透传入队；幂等去重 SELECT 追加 `AND user_id = ?`。

- [ ] **Step 1: 写失败测试**——`check_channel_isolation.py` 增加 section `tokens`。双用户：admin（isolated_app 自带）+ `create_user("b", ...)` 得 `uid_b`，`client_b` 用 `session_transaction` 登录 B；`database.create_agent_token(uid, "token-a")`、`create_agent_token(uid_b, "token-b")`。断言：
  - 无/错/吊销 token 打 `/agent/heartbeat`、`/agent/jobs/claim`、`/agent/jobs/<id>/events` 均 401 且 `{"error": "unauthorized"}`；非 ASCII Bearer（`tøken`）是 JSON 401 不是 500；
  - `enqueue_agent_job("article_sync", payload, user_id=uid)` 后：token-a claim 200 拿到该 job；token-b claim 204（认领不到他人 job）；`enqueue_agent_job(..., user_id=None)` 抛 ValueError；
  - events 归属：token-b 对 uid 的 job POST events → 404；对不存在的 job id → 404；两响应体逐字节相等；
  - `GET /agent-jobs/<id>.json`：B 会话读 uid 的 job → 404 与不存在同体；读自己的 → 200；`GET /agent-jobs`（队列页 HTML）B 视角不含 uid 的帖子 title 字样；
  - push 链路带归属：seed 一张 uid 的 standalone 卡片；在线状态一律经 `POST /agent/heartbeat`（带 token-a）构造，**不直调 `record_heartbeat`**（Task 4 会改它的签名为 `(user_id, payload)`，端点路径则两阶段都成立）→ push 200 且 `database.get_agent_job(job_id)["user_id"] == uid`；
  - heartbeat 端点 200 后 `list_agent_tokens(uid)` 对应行 `last_seen` 被刷新（touch 生效）。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py tokens`，预期 FAIL。

- [ ] **Step 3: 实现**——
  - `database.py`：agent_jobs 新库 DDL 加 `user_id INTEGER`；老库 `PRAGMA table_info` 探测后 `ALTER TABLE agent_jobs ADD COLUMN user_id INTEGER`（照抄 `_CONTENT_TABLES` 循环的模式，:657-664）；迁移回填 `UPDATE agent_jobs SET user_id=(SELECT MIN(id) FROM users) WHERE user_id IS NULL AND EXISTS(SELECT 1 FROM users)`（D7 存量划归 admin，NULL 守卫天然幂等）；`enqueue`/`claim`/`list_agent_jobs` 按 Interfaces 改签名。
  - `agent_hub.py`：删 `check_agent_auth`（`agent_token()` :29 保留——其值已被 Task 1 迁移读走，本任务只把 docstring 注释改为「仅存量迁移语义」，函数体不动），新增 `agent_credentials()`。
  - `insights_web.py`：三个 `/agent/*` 端点改 `creds = agent_hub.agent_credentials()`，None → `jsonify({"error": "unauthorized"}), 401`，`uid = creds[0]`；heartbeat 端点加 `database.touch_agent_token(creds[1])`；events 端点先 `database.get_agent_job(job_id)` 归属校验（不存在或 `user_id != uid` → 同一个 `jsonify({"error": "not found"}), 404`）再 `record_agent_job_event`（未知事件类型仍 400，排在归属校验之后）；`agent_job_json` 同样归属校验；`agent_jobs_page` 改 `list_agent_jobs(limit=100, user_id=uid)`。
  - `domestic_publisher.py` + `compose_push_post`：按 Interfaces 传 `user_id=uid`；`compose_push_post` 的 409 在线判定本任务**不动**（Task 6 改为按本人）。

- [ ] **Step 4: 适配既有 gate 并全绿**——
  - `check_agent_hub.py`：`set_setting("AGENT_TOKEN", ...)` 之后补一行 `database.init_db(database.DB_PATH)` 触发迁移（顺带钉迁移语义），`Bearer secret-token` 经迁移继续生效；直接 `enqueue_agent_job` 的调用全部补 `user_id=`（gate 用户）。
  - `check_agent_jobs.py`：去掉对 `set_setting("AGENT_TOKEN", "gate-token", ...)` 的依赖（:124），改 `database.create_agent_token(gate_uid, "gate-token")`；所有 `enqueue_agent_job` 补 `user_id=`、所有 `claim_agent_job` 补 `user_id=gate_uid`。
  - `check_agent_ui.py` :80 的 enqueue 补 `user_id=`；`check_domestic_publisher.py` 对 `enqueue_card_push` 的调用补 `user_id=`；`check_agent_push.py` 的 push 路由断言补归属检查。
- [ ] **Step 5: 跑 tokens 节确认通过**，注册 `("check_channel_isolation.py", "CHANNEL_ISOLATION_TOKENS_OK", "token 解析用户：claim/events/读路径只认本人，跨用户与不存在同响应体", ("tokens",))`。

- [ ] **Step 6: 全量 gate + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add -A
git commit -m "agent job 归属到用户：token 查表解析身份，认领/回报/读取只认本人"
```

---

### Task 3: 配对码绑定铸造者，兑换签发本人 token

tasks.md 1.2，决策 D1/D3/D8。

**Files:**
- Modify: `agent_hub.py:148-191`（`_PENDING_PAIRING`、`create_pairing_code`、`redeem_pairing_code`；新增 `issue_agent_token`）
- Modify: `insights_web.py:6162-6172`（pair-code 路由）、`:6174-6188`（pair 路由）
- Test: `scripts/check_channel_isolation.py`（section `tokens` 追加断言）
- Modify: `scripts/check_agent_hub.py:174-212`（配对段断言重写）

**Interfaces:**
- Consumes: Task 1 `create_agent_token`/`find_agent_user_by_token`。
- Produces:
  - `agent_hub.create_pairing_code(user_id: int) -> str`（码绑定铸造者，TTL 300s / 5 次尝试 / 单 pending 不变）
  - `agent_hub.redeem_pairing_code(code) -> int | None`（返回铸造者 user_id；语义失败仍 None）
  - `agent_hub.issue_agent_token(user_id: int, label: str = "") -> str`——`secrets.token_urlsafe(32)` → `database.create_agent_token` → 只在此刻返回明文

- [ ] **Step 1: 写失败测试**（tokens 节追加）：
  - A（admin 会话）`POST /agent-jobs/pair-code` 得 8 位码；匿名客户端 `POST /agent/pair {"code": ...}` → 200 且 `{"token": <43字符左右明文>}`；`find_agent_user_by_token(该 token) == A 的 uid`（谁配对归谁，D1/D3）；
  - 同一码二次兑换 403；大小写/空格容忍保留；5 次错码烧码、过期码 403（现状语义不变）；
  - 响应体无 `user_id` 等新键（协议形状不变，D8）；`agent_tokens` 表里查不到明文（只存哈希）；
  - B 铸码兑换 → 得到的是 B 的 token，与 A 的 token 互不相同且 `find_agent_user_by_token` 各归各。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py tokens`。

- [ ] **Step 3: 实现**——`_PENDING_PAIRING` 增加 `user_id` 键；`create_pairing_code(user_id)` 存铸造者；`redeem_pairing_code` 成功分支返回 `_PENDING_PAIRING["user_id"]`（不再 `return agent_token()`）；pair 路由 `token = agent_hub.issue_agent_token(uid)` 后原样 `jsonify({"token": token})`；pair-code 路由传 `current_user()["id"]`。

- [ ] **Step 4: 适配 `check_agent_hub.py` 配对段**——`redeem` 返回的不再是 `"secret-token"` 而是新签发 token：断言改为「200 + token 能 resolve 回 gate 用户 + 二次 403」；其余 TTL/烧码断言不变。

- [ ] **Step 5: 跑 tokens 节 + 全量 gate 确认通过**——`venv/Scripts/python.exe scripts/run_all_gates.py`。

- [ ] **Step 6: Commit**

```bash
git add agent_hub.py insights_web.py scripts/check_agent_hub.py scripts/check_channel_isolation.py
git commit -m "配对码绑定铸造者：兑换签发兑换者专属 token，协议形状不变"
```

---

### Task 4: heartbeat 按用户分桶 + 登录态视图按用户 + 消费端切换

tasks.md 2.1 + 3.1（compose 选择器按本人登录态），决策 D4。

**Files:**
- Modify: `agent_hub.py:19-26`（`_LAST_HEARTBEAT`/`_TOOL_BEATS` → `_LAST_HEARTBEATS`/`_USER_TOOL_BEATS`）、`:53-145`（`record_heartbeat`/`heartbeat_snapshot`/`agent_online`/`platform_login_states`/`login_states_view`）
- Modify: `insights_web.py:3016-3017`（context processor）、`:2323,2361`（/accounts）、`:3177-3178`（compose render）、`:6145-6149`（login-states.json）、`:6154-6160`（status.json）、`:6206`（agent_jobs_page）、`:6209-6213`（heartbeat 端点传 uid）
- Test: `scripts/check_channel_isolation.py`（section `heartbeat`）
- Modify: `scripts/check_login_states.py`、`scripts/check_agent_hub.py:119-166`、`scripts/check_agent_push.py:116`、`scripts/check_agent_ui.py:102-104`

**Interfaces:**
- Consumes: Task 2 的端点 uid 解析。
- Produces:
  - `agent_hub.record_heartbeat(user_id: int, payload: dict) -> None`
  - `agent_hub.heartbeat_snapshot(user_id: int) -> dict | None`
  - `agent_hub.agent_online(user_id: int | None = None, window: float = HEARTBEAT_WINDOW_SECONDS) -> bool`（None → False：无身份即无状态）
  - `agent_hub.platform_login_states(user_id: int | None = None) -> dict`（None → `{}`；合并规则 probed 胜 unprobed、新胜旧、过期删除原样保留，全部发生在该用户的桶内；整桶全过期时删除该 user 键防泄漏）
  - `agent_hub.login_states_view(user_id: int | None = None) -> dict`
  - `_LAST_HEARTBEATS: dict[int, dict]`、`_USER_TOOL_BEATS: dict[int, dict[str, dict]]`（旧两个全局名删除）

- [ ] **Step 1: 写失败测试**（section `heartbeat`）：
  - token-a 上报 `{"tools": {"extension": {"platforms": [{"id": "wechat", "logged_in": True, "probed": True}]}}}` → 登录会话 A 的 `GET /agent/login-states.json` 中 wechat 为 `ok`；切换 B 会话 → 全平台 `offline`（登录态互不可见，场景 3）；
  - A 离线（桶内 beat 时间戳拨老）不影响 B 的在线显示；`platform_login_states(uid_b)` 不含 A 上报的平台；
  - `agent_online(None) is False`；`agent_online(uid_a)` 按桶判定过期；
  - 过期清理：把 A 桶唯一 beat 拨过 window → `platform_login_states(uid_a) == {}` 且 `uid_a not in agent_hub._USER_TOOL_BEATS`（桶被删，防泄漏）；
  - compose 选择器按本人（tasks 3.1）：A 会话 `GET /compose` 页里 wechat 平台 checkbox 不带 `disabled`（A 的 wechat ok），B 会话同页带 `disabled`（B 无心跳）——断言响应 HTML 文本；
  - `GET /agent/status.json` 返回当前会话用户的 online/age。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py heartbeat`。

- [ ] **Step 3: 实现**——按 Interfaces 改五个函数与两个内存结构（合并/清理逻辑整体搬进桶内循环，不重写算法）；`insights_web.py` 各消费端统一取 `user = current_user(); uid = user["id"] if user else None` 后传入；heartbeat 端点改 `record_heartbeat(uid, ...)`。`base.html`/`compose.html`/`post_item.html` **零改动**（读注入值）。

- [ ] **Step 4: 适配既有 gate**——`check_login_states.py`：`platform_login_states()` 直调补 uid、`_TOOL_BEATS` 引用（若有）改 `_USER_TOOL_BEATS[uid]`，并补一条双用户互不可见断言；`check_agent_hub.py:119-166` 心跳段同步函数签名；`check_agent_push.py:116` `agent_hub._LAST_HEARTBEAT["t"] = None` 改为对 gate 用户桶置 offline（`_LAST_HEARTBEATS` 键不存在时先置一个再清）；`check_agent_ui.py:102-104` 同理。

- [ ] **Step 5: 跑 heartbeat 节确认通过**，注册 `("check_channel_isolation.py", "CHANNEL_ISOLATION_HEARTBEAT_OK", "心跳按用户分桶：登录态互不可见、离线互不影响、过期整桶清理、compose 按本人可选平台", ("heartbeat",))`。

- [ ] **Step 6: 全量 gate + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add -A
git commit -m "heartbeat 按用户分桶：登录态视图与状态条只看本人"
```

---

### Task 5: social_accounts 隔离迁移 + /accounts 按用户 + token 管理 UI

tasks.md 2.2 + 2.3，决策 D6/D2（吊销 UI）。

**Files:**
- Modify: `database.py`（social_accounts 建表 :594-616、唯一索引 :613、`list_social_accounts:4448`、`upsert_social_account:4498`）
- Modify: `insights_web.py:2306-2362`（/accounts 路由）；新增吊销路由（放 /accounts 路由后）
- Modify: `templates/accounts.html`
- Test: `scripts/check_channel_isolation.py`（section `accounts`）
- Modify: `scripts/check_accounts_*.py` 如有直调 `list_social_accounts()` 且断言跨用户可见性的（run 后按失败修）

**Interfaces:**
- Produces:
  - `social_accounts` 新库 DDL 含 `user_id INTEGER`；老库 ALTER 补列（照 `_CONTENT_TABLES` 模式）；唯一索引 `idx_social_accounts_identity(platform, external_id)` → `DROP INDEX IF EXISTS` 后建 `idx_social_accounts_identity_user ON social_accounts(platform, external_id, user_id)`；回填 `UPDATE social_accounts SET user_id=(SELECT MIN(id) FROM users) WHERE user_id IS NULL AND EXISTS(SELECT 1 FROM users)`（D6 存量划归 admin——注意与 ① 内容表「不回填」不同，这里明确回填）。
  - `list_social_accounts(platform=None, include_removed=False, *, user_id: Optional[int] = None, db_path=DB_PATH)`——user_id 非空时追加 `AND user_id = ?`。
  - `upsert_social_account(..., user_id: Optional[int] = None, db_path=...)`——keyword 追加（生产无调用方，D6 已核实；None 存 NULL，NULL 行对任何 user 过滤不可见，自动隔离）。
  - `POST /accounts/tokens/<int:token_id>/revoke`——会话路由；`database.revoke_agent_token(token_id, uid)` False（他人/不存在）→ 404 `{"error": "not found"}`；成功 → `{"success": True}`。

- [ ] **Step 1: 写失败测试**（section `accounts`）：
  - schema 复用：init_db 二跑后手工 `INSERT INTO social_accounts(platform, external_id, user_id) VALUES ('wechat','wx-1', uid_b)` 与 admin 同 `(platform, external_id)` 不同 user 各一行均成功（唯一键放宽，DB 层验证，场景 4）；旧行（迁移前 seed 的 user_id 为 NULL 的行）升级后 `user_id == admin`；
  - B 会话 `GET /accounts` HTML 不含 admin 存量行的 label/external_id 字样；admin 会话含自己的；
  - B 会话 `list_social_accounts(user_id=uid_b)` 不含 admin 行；
  - 吊销：B `POST /accounts/tokens/<admin 的 token 行 id>/revoke` → 404 与不存在同体；B 吊销自己的 → 200 且该 token 立即 401（衔接 Task 2）、同用户其他 token 不受影响（场景 6）；
  - 页面出现本人 token 列表（label/last_seen 文案）与吊销表单标记。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py accounts`。

- [ ] **Step 3: 实现**——database.py 三处（DDL/索引/回填 + 两个函数签名）；/accounts 路由 `login_states_view(uid)`（Task 4 已做）之外把 `list_social_accounts()` 改 `list_social_accounts(user_id=uid)`，并新增 token 列表数据 `database.list_agent_tokens(uid)` 传入模板；吊销路由按 Interfaces 实现；`templates/accounts.html` 在平台卡片区块后加「已配对的浏览器扩展」区块：每行 label/created_at/last_seen/吊销按钮（表单 POST），文案中文直写（页面现状全中文，无 i18n 键）。

- [ ] **Step 4: 跑 accounts 节确认通过**，注册 `("check_channel_isolation.py", "CHANNEL_ISOLATION_ACCOUNTS_OK", "social_accounts 遗留数据隔离：唯一键放宽、存量归 admin、列表/吊销只认本人", ("accounts",))`。

- [ ] **Step 5: 全量 gate + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add -A
git commit -m "social_accounts 按用户隔离：唯一键放宽、存量归 admin，账号页可管本人 token"
```

---

### Task 6: 发布链路按帖主通道（即时 push 409 按本人 heartbeat）

tasks.md 3.2 路由层收口 + D9.3 的 run_after 路径（调度路径同一端点，入队带 uid 即完成）。

**Files:**
- Modify: `insights_web.py:6110-6137`（`compose_push_post`）
- Test: `scripts/check_channel_isolation.py`（section `publish`）
- Modify: `scripts/check_agent_push.py`（push 行为断言）

**Interfaces:**
- Consumes: Task 2 `enqueue_card_push(user_id=...)`（已必填）、Task 4 `agent_online(user_id)`。
- Produces: 行为契约——即时 push 的 409 判定从「任意人在线」改为「帖主本人在线」（409 机制与文案保持不变，D9.1）；`run_at` 调度路径照旧绕过在线判定直接入队，job 归属帖主。

- [ ] **Step 1: 写失败测试**（section `publish`）：
  - A（admin）无心跳、B 有心跳：A 对自己的卡 `POST /compose/post/<id>/push`（无 run_at）→ 409 `推送离线：请启动浏览器扩展…`（他人在线不解锁，Review Focus 5）；
  - A 有心跳 → 200，`get_agent_job["user_id"] == uid_a`；
  - 带 `run_at` 的调度 push 在 A 无心跳时仍 200 入队且 `user_id == uid_a`（D9.3：run_after 路径带帖主，不新建调度器——用 `grep -c "SCHEDULABLE_PLATFORMS: tuple = ()"` 断言无需改动是可选的静态确认，不进 gate）；
  - B token claim A 的调度 job → 204（认领收口已在 Task 2，这里作为链路串测）。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py publish`。

- [ ] **Step 3: 实现**——`compose_push_post` 两处：`elif not agent_hub.agent_online():` → `elif not agent_hub.agent_online(uid):`；`enqueue_card_push(post, run_after=run_after)` → `enqueue_card_push(post, run_after=run_after, user_id=uid)`。（Task 2 已改 enqueue_card_push 签名，此处只补路由实参的确认与 409 判定。）

- [ ] **Step 4: 跑 publish 节确认通过**，注册 `("check_channel_isolation.py", "CHANNEL_ISOLATION_PUBLISH_OK", "即时推送按帖主心跳判定 409，调度入队带帖主归属，他人通道不可用", ("publish",))`。

- [ ] **Step 5: 全量 gate + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add -A
git commit -m "即时推送按帖主心跳判定离线，调度入队归属帖主"
```

---

### Task 7: 队列页渲染时派生通道原因（不入库）

tasks.md 3.3 的「通道不可用保持排队并标注原因」，D9.1 裁定：渲染时派生、零 schema 变更。

**Files:**
- Modify: `insights_web.py:6197-6207`（`agent_jobs_page` 循环）
- Modify: `templates/agent_jobs.html`（pending 行渲染原因）
- Test: `scripts/check_channel_isolation.py`（section `publish` 追加断言）

**Interfaces:**
- Consumes: Task 4 `platform_login_states(user_id)`、`domestic_publisher.display_name`。
- Produces: 模板数据 `job['pending_reasons']: list[str]`——仅 `status == 'pending'` 的 job 计算；对每个 `payload.items[].target`：帖主本人的 `platform_login_states(job['user_id'])` 里缺失该平台或 `logged_in is False` → 追加 `f"{display_name(target)}：平台未连接/登录态异常"`。claimed/running/succeeded 不标注。

- [ ] **Step 1: 写失败测试**（publish 节追加）：
  - 入队一条 uid_a 的 pending job（wechat/zhihu 两 target），A 无心跳 → `GET /agent-jobs` HTML 含「平台未连接/登录态异常」与「公众号」「知乎」；
  - A 上报 wechat logged_in 心跳后 → HTML 只剩知乎的原因、不再含 wechat 的原因；
  - job 变 `succeeded` 后（events done）不再出现原因标注；
  - B 的心跳（wechat logged_in）不影响 A 页面的原因标注（派生只用帖主桶）。

- [ ] **Step 2: 跑测试确认失败**——`venv/Scripts/python.exe scripts/check_channel_isolation.py publish`。

- [ ] **Step 3: 实现**——`agent_jobs_page` 循环内在既有 `job['targets']` 解析之后按 Interfaces 计算 `job['pending_reasons']`（每 job 至多一次 `platform_login_states(job['user_id'])` 调用，循环外不可缓存跨用户结果）；`agent_jobs.html` 在 pending 行状态区渲染 `{% for reason in job.get('pending_reasons') or [] %}` 一行小字。

- [ ] **Step 4: 跑 publish 节确认通过**（OK token 不变，断言并入既有注册）。

- [ ] **Step 5: 全量 gate + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add -A
git commit -m "队列页渲染时派生排队原因：帖主通道不可用现场标注，不入库"
```

---

### Task 8: 扩展零改动验证（只读 + 既有 gate）

tasks.md 4.1。设计 §2.2 已裁定：配对客户端在**本仓** `extension/`，不是 MultiPost-Extension 上游仓；`D:\git3\MultiPost-Extension` 仅在本仓 extension 缺对应流程时才作参考。

**Files:**
- Read-only: `extension/src/lib/protocol.ts`、`extension/src/background/orchestrator.ts`
- 无代码改动（预期零 diff；若实测发现必须改：停在最小说明，等用户确认后再动）

- [ ] **Step 1: 逐项核对清单**（读源码，把结论记进任务报告）：
  - `pairWithCode`（protocol.ts）POST `/agent/pair` body `{"code"}`、期待响应 `{"token": string}` → Task 3 响应形状未变；
  - token 全程不透明、只进 `Authorization: Bearer` 头 → Task 2 未引入新头/新字段；
  - `buildHeartbeat`（orchestrator.ts:25-40）载荷仅 `tools.extension.platforms[{id, logged_in, probed}]`，无账号身份 → Task 4 分桶只依赖 token，兼容；
  - `claimBody`/claim 204-null/200-Job、`sendEvent` 200/400 → Task 2 行为未变。

- [ ] **Step 2: 跑扩展协议 gate**

```bash
venv/Scripts/python.exe scripts/check_extension_tests.py
venv/Scripts/python.exe scripts/check_extension_protocol.py
```

预期：`EXTENSION_TESTS_OK`、`EXTENSION_PROTOCOL_OK`（后者经真实 HTTP 驱动编译后的客户端，claim/events/heartbeat 全链路）。确认 `git diff --stat extension/` 为空。

- [ ] **Step 3: Commit（若有文档性微调）或直接在任务报告记录结论**——零改动时本任务无 commit。

---

### Task 9: 双用户端到端验收 gate

tasks.md 4.2，设计 §6 场景 1–8（场景 5 迁移幂等已由 Task 1/Task 5 schema 节钉死，e2e 不重复，沿用 ① 惯例）。

**Files:**
- Test: `scripts/check_channel_isolation.py`（section `e2e`）
- Modify: `scripts/run_all_gates.py`（注册最后一节）

**Interfaces:**
- Consumes: 前八个任务的全部行为契约。

- [ ] **Step 1: 写 e2e 节**——双账号（admin + B）走真路由跑完整叙事，一次性串起场景 1–4、6–8：
  1. 各自配对（A 铸码→匿名兑换、B 铸码→匿名兑换）→ `find_agent_user_by_token` 各归各（401 仅对无效/吊销 token；「互不相通」钉在解析身份不同 + claim 互不可见）；
  2. A 入队 → B token claim 204、A token claim 200；B token 对 A job events → 404 同体；
  3. A 上报 wechat ok → B 的 login-states 与 /accounts 均 offline/未登录；`/agent-jobs/<id>.json` 跨用户 404 同体、队列页 B 视角无 A 帖子 title；
  4. admin 存量 social_accounts 行对 B 不可见；同 platform+external_id 双用户各一行；
  6. 吊销 B 的 token → 该 token 立即 401、B 的另一 token 不受影响；
  7. B 在线、A 离线：A 的 push 409；A 带 run_at 入队后 job 停在 pending、队列页出现「平台未连接/登录态异常」、B token claim 拿不到——绝不发到 B 平台；
  8. 扩展零改动：两个「扩展实例」（两个 test client 各持各的 token、body 带 `{"agent": "insights-ext", "executor": "extension", "platforms": ["wechat"]}`）并发 claim，各领各的 job，events 各回各的，双方 job 均到 succeeded。

- [ ] **Step 2: 跑 e2e 节确认通过**——`venv/Scripts/python.exe scripts/check_channel_isolation.py e2e` → `CHANNEL_ISOLATION_E2E_OK`。注册 `("check_channel_isolation.py", "CHANNEL_ISOLATION_E2E_OK", "双用户端到端：配对互不相通、认领/回报/读取不越权、他人在线不解锁推送、双扩展各领各的 job", ("e2e",))`。

- [ ] **Step 3: 全量 gate 终验 + commit**

```bash
venv/Scripts/python.exe scripts/run_all_gates.py
git add -A
git commit -m "双用户发布通道端到端验收 gate"
```

---

## 收尾

- 全部任务后：`venv/Scripts/python.exe scripts/run_all_gates.py` 一次全绿截图/输出留存为 verify 证据；
- `git log c1421e37..HEAD --format='%an <%ae> | %cn <%ce>'` 确认作者身份；`git log c1421e37..HEAD --format=%B | grep -iE '^(Co-Authored-By|Claude-Session):'` 应无输出（仓库 CLAUDE.md 规则）；
- 遗留说明（写入 change 的 verify 记录，不写代码）：多浏览器 profile 同一时间只能配对一个用户，换人 = 退出重配对（D1，与一号制同构）；heartbeat 重启即失为现状语义（非目标）。
