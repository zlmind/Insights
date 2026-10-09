# 国内文章推送（本机 agent + wechatsync）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** compose 页的 公众号/知乎/头条 卡片一键推送草稿箱：服务器入队 → 本机 agent 轮询领取 → wechatsync CLI 推送 → 逐平台结果回传展示。

**Architecture:** 服务器新增 `agent_jobs` 队列表 + `/agent/*` Token 端点（`agent_hub.py`）；本机新增仓库内 `agent/` 包（纯标准库，轮询拉任务，驱动外部 wechatsync CLI）；compose 卡片占位按钮兑现为真实推送按钮，带心跳离线降级。

**Tech Stack:** Flask + sqlite3（服务器）；Python 标准库 subprocess/urllib（agent）；外部工具 `@wechatsync/cli`（npm 全局安装，GPL-3.0，独立进程调用）。

**Spec:** `docs/superpowers/specs/2026-10-09-domestic-channels-agent-design.md`（含与工作区未提交改动的对齐：平台 ID `wechat`/`zhihu`/`toutiao`，每平台定制文案按 `{target, content}` 逐条推送）

## Global Constraints

- 任务状态机：`pending → claimed → running → succeeded|partial|failed`；终态失败一律 `failed`（spec 中的 `expired` 并入 `failed` 留档）
- 重试：仅 `retryable=true` 的 failed 事件；退避 `60s→300s→900s→3600s`；`max_attempts=5`；`claimed` 超 15 分钟无结果回收为 `pending`
- 心跳：60 秒无心跳视为离线；compose 页据此置灰推送按钮
- 定时推送：`run_at` 来自 `datetime-local` 表单（本地无时区时间），服务器端
  `datetime.fromisoformat(v).astimezone(timezone.utc)` 转 UTC ISO 后落 `run_after`；
  **带 `run_at` 的入队不检查 agent 在线**（定时本义是现在入队、到点由 agent 领走）
- gate 种子数据用 `database.add_standalone_post(...)`；以 `database.py` 现有签名为准调整
  关键字名（先 grep `def add_standalone_post`），不得改被测函数
- agent 轮询间隔默认 5 秒（`AGENT_POLL_SECONDS`）；单飞行（同一时刻最多 1 个 job）
- wechatsync 单次 sync 超时 300 秒；`subprocess` 一律数组参数 `shell=False`
- 临时文件写 `%TEMP%/insights-agent/`，`finally` 必删
- Token 比对用 `hmac.compare_digest`；`/agent/*` 三个端点加入 `PUBLIC_ENDPOINTS`，其余新路由走登录态
- 服务器代码不新增第三方依赖；agent 包只用标准库
- Gate 惯例：每个 `scripts/check_*.py` 退出码 0 且打印自己的 `*_OK` token；必须注册进 `scripts/run_all_gates.py`（未注册的 gate 会让 run_all_gates 直接失败）
- 提交信息祈使句、无归属尾注；作者身份 `l0lsec <51931886+l0lsec@users.noreply.github.com>`（仓库已配置）
- gate 一律跑在 `isolated_app()` 的一次性数据库上，绝不触碰真实 `insights.db`

## Review Focus

spec 未覆盖、但正常人会踩的五个输入，各自的钉死测试落在所属任务里：

1. **同一卡片重复点推送** → 第二次点击应返回已有 pending/claimed job（HTTP 200 + `already: true`），而不是排一个重复任务。测试：Task 2「幂等」步骤。
2. **agent 领取后进程被杀** → job 卡在 claimed；15 分钟后下一次 claim 前被回收为 pending 重发。测试：Task 1「stale 回收」步骤。
3. **目标平台未登录** → 预检直接返回 `needs_login`，**不发起 sync**（避免半成功状态）。测试：Task 5「未登录预检」步骤。
4. **服务器下发了未知 kind**（如未来 `video_publish` 但 agent 是旧版）→ agent 回 `failed` 事件（`unknown_kind`，不可重试），绝不崩溃、绝不静默丢弃。测试：Task 4「未知 kind」步骤。
5. **文案含引号/换行/emoji/markdown 符号** → 内容只经临时文件与 argv 传递，永不进 shell、永不拼接命令字符串；推送后草稿内容与原文逐字符一致（CLI 层面以退出码+fixture 验证）。测试：Task 5「恶意内容」步骤。

---

### Task 1: `agent_jobs` 队列表与队列操作

**Files:**
- Modify: `database.py`（建表语句加进 `init_db` 的大 `executescript` 列表；函数追加在 settings 函数区之后）
- Test: `scripts/check_agent_jobs.py`

**Interfaces:**
- Consumes: 无（自包含，sqlite3 + 现有 house style）
- Produces（后续任务依赖的精确签名）:
  - `enqueue_agent_job(kind: str, payload: str, *, run_after: str | None = None, max_attempts: int = 5, article_id: int | None = None, created_by: int | None = None, db_path: str = DB_PATH) -> int`（返回 job id；`payload` 是 JSON 字符串）
  - `claim_agent_job(*, kinds: tuple = ("article_sync",), agent: str = "", stale_minutes: int = 15, db_path: str = DB_PATH) -> dict | None`（原子认领；无任务返回 None）
  - `record_agent_job_event(job_id: int, event: dict, *, db_path: str = DB_PATH) -> dict`（处理 `started`/`done`/`failed`，返回更新后的 job 行）
  - `get_agent_job(job_id: int, *, db_path: str = DB_PATH) -> dict | None`
  - `list_agent_jobs(*, limit: int = 100, db_path: str = DB_PATH) -> list[dict]`

- [ ] **Step 1: 写失败测试**

`scripts/check_agent_jobs.py` 全文：

```python
"""Gate for the agent_jobs queue: claim atomicity, events, retry, stale reclaim.

    python scripts/check_agent_jobs.py    AGENT_JOBS_OK

The agent hub hands jobs to the local machine over HTTPS, so the queue has to
be exactly-once per claim and self-healing after a dead agent. This gate drives
the real database functions on a throwaway file.
"""

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()

    # --- enqueue + get ---
    jid = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "t", "items": [{"target": "wechat", "content": "hi"}]}),
        created_by=None,
    )
    check(isinstance(jid, int) and jid > 0, "enqueue returns a job id")
    job = database.get_agent_job(jid)
    check(job["status"] == "pending", "new job is pending")
    check(json.loads(job["payload"])["items"][0]["target"] == "wechat",
          "payload round-trips as JSON")

    # --- claim is exclusive ---
    first = database.claim_agent_job(agent="a1")
    check(first and first["id"] == jid and first["status"] == "claimed",
          "first claim takes the pending job")
    second = database.claim_agent_job(agent="a2")
    check(second is None, "second claim finds nothing — claims are exclusive")

    # --- started event ---
    job = database.record_agent_job_event(jid, {"type": "started", "agent": "a1"})
    check(job["status"] == "running", "started event marks the job running")

    # --- done: partial semantics ---
    results = [
        {"platform": "wechat", "success": True, "draft": True},
        {"platform": "zhihu", "success": False, "error": "未登录",
         "code": "needs_login"},
    ]
    job = database.record_agent_job_event(jid, {"type": "done", "results": results})
    check(job["status"] == "partial", "mixed results land as partial")
    check(json.loads(job["result"])[1]["code"] == "needs_login",
          "per-platform results are stored verbatim")

    # --- retryable failure: backoff then terminal ---
    j2 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                    max_attempts=2)
    database.record_agent_job_event(j2, {"type": "started"})
    job = database.record_agent_job_event(
        j2, {"type": "failed", "error": "timeout", "retryable": True})
    check(job["status"] == "pending" and job["attempts"] == 1,
          "retryable failure requeues with attempts=1")
    check(job["run_after"] is not None, "retry carries a run_after gate")
    job = database.record_agent_job_event(
        j2, {"type": "failed", "error": "timeout", "retryable": True})
    check(job["status"] == "failed" and job["attempts"] == 2,
          "attempts exhausted lands as terminal failed")

    # --- non-retryable failure is terminal at once ---
    j3 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}))
    job = database.record_agent_job_event(
        j3, {"type": "failed", "error": "no tool", "retryable": False})
    check(job["status"] == "failed", "non-retryable failure is terminal")

    # --- stale reclaim ---
    j4 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}))
    database.claim_agent_job(agent="dead-agent")
    row = database.get_agent_job(j4)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE agent_jobs SET claimed_at = ? WHERE id = ?",
                     ("2000-01-01T00:00:00", j4))
    revived = database.claim_agent_job(agent="a2", stale_minutes=15)
    check(revived and revived["id"] == j4,
          "a claim older than stale_minutes is handed out again")

    # --- run_after gates claiming ---
    j5 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                    run_after="2099-01-01T00:00:00")
    check(database.claim_agent_job(agent="a1") is None,
          "a future run_after is not claimable yet")

    # --- list ---
    jobs = database.list_agent_jobs(limit=10)
    check(len(jobs) >= 5 and jobs[0]["id"] >= jobs[-1]["id"],
          "list returns newest-first rows")

    print("AGENT_JOBS_OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_jobs.py`
Expected: FAIL —— `AttributeError: module 'database' has no attribute 'enqueue_agent_job'`（或 isolated_app 阶段的等价报错）

- [ ] **Step 3: 建表（database.py 的 init_db）**

在 `init_db` 中 `social_accounts` 建表所在的同一批 `conn.execute(...)` 语句后追加：

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'article_sync',
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                run_after TEXT,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                result TEXT,
                claimed_by TEXT,
                claimed_at TEXT,
                article_id INTEGER,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_jobs_queue "
            "ON agent_jobs(status, run_after, id)"
        )
```

- [ ] **Step 4: 队列函数（database.py 末尾追加）**

```python
# ---------------------------------------------------------------------------
# Agent jobs: the queue the local publishing agent drains over HTTPS.
# ---------------------------------------------------------------------------

_BACKOFF_SECONDS = (60, 300, 900, 3600)


def enqueue_agent_job(kind, payload, *, run_after=None, max_attempts=5,
                      article_id=None, created_by=None, db_path=DB_PATH):
    """Queue one job for the local agent; ``payload`` is a JSON string."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_jobs (kind, payload, status, run_after,
                                    max_attempts, article_id, created_by,
                                    created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (kind, payload, run_after, max_attempts, article_id, created_by,
             now, now),
        )
    return cur.lastrowid


def claim_agent_job(*, kinds=("article_sync",), agent="", stale_minutes=15,
                    db_path=DB_PATH):
    """Hand the oldest due job to one agent, exactly once.

    A claim older than ``stale_minutes`` is recycled first, so a killed agent
    cannot pin a job forever. BEGIN IMMEDIATE keeps two pollers from taking
    the same row even on SQLite builds without RETURNING.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    marks = ",".join("?" for _ in kinds)
    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                UPDATE agent_jobs SET status = 'pending', claimed_by = NULL,
                       claimed_at = NULL, updated_at = ?
                WHERE status = 'claimed'
                  AND claimed_at IS NOT NULL
                  AND claimed_at < ?
                """,
                (now,
                 (datetime.utcnow()
                  - timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")),
            )
            row = conn.execute(
                f"""
                SELECT id FROM agent_jobs
                WHERE status = 'pending' AND kind IN ({marks})
                  AND (run_after IS NULL OR run_after <= ?)
                ORDER BY id LIMIT 1
                """,
                (*kinds, now),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE agent_jobs SET status = 'claimed', claimed_by = ?,
                       claimed_at = ?, updated_at = ? WHERE id = ?
                """,
                (agent, now, now, row[0]),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_agent_job(row[0], db_path=db_path)


def record_agent_job_event(job_id, event, *, db_path=DB_PATH):
    """Apply one agent event (started/done/failed) to a job; returns the row.

    ``done`` carries per-platform ``results``; all-success is succeeded, any
    success with any failure is partial. ``failed`` requeues only when the
    event says ``retryable`` and attempts remain.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    etype = (event or {}).get("type")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"agent job {job_id} does not exist")
        attempts = row["attempts"] or 0
        max_attempts = row["max_attempts"] or 5

        if etype == "started":
            status, run_after, result = "running", row["run_after"], row["result"]
        elif etype == "done":
            results = event.get("results") or []
            ok = [r for r in results if r.get("success")]
            if results and len(ok) == len(results):
                status = "succeeded"
            elif ok:
                status = "partial"
            else:
                status = "failed"
            run_after = row["run_after"]
            result = json.dumps(results, ensure_ascii=False)
        elif etype == "failed":
            result = json.dumps(
                [{"error": event.get("error", "unknown"),
                  "code": event.get("code"), "retryable": bool(event.get("retryable"))}],
                ensure_ascii=False)
            if event.get("retryable") and attempts + 1 < max_attempts:
                attempts += 1
                status = "pending"
                delay = _BACKOFF_SECONDS[min(attempts - 1, len(_BACKOFF_SECONDS) - 1)]
                run_after = (datetime.utcnow()
                             + timedelta(seconds=delay)).isoformat(timespec="seconds")
            else:
                status, run_after = "failed", row["run_after"]
        else:
            raise ValueError(f"unknown agent job event type: {etype!r}")

        conn.execute(
            """
            UPDATE agent_jobs SET status = ?, attempts = ?, run_after = ?,
                   result = COALESCE(?, result), updated_at = ? WHERE id = ?
            """,
            (status, attempts, run_after, result, now, job_id),
        )
    return get_agent_job(job_id, db_path=db_path)


def get_agent_job(job_id, *, db_path=DB_PATH):
    """One agent job row as a dict, or None."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def list_agent_jobs(*, limit=100, db_path=DB_PATH):
    """Agent jobs, newest first."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
```

若 `database.py` 顶部还没有 `from datetime import datetime, timedelta`，补上（保留既有 import 风格）。

- [ ] **Step 5: 跑测试确认通过**

Run: `python scripts/check_agent_jobs.py`
Expected: `AGENT_JOBS_OK`，退出码 0

- [ ] **Step 6: 注册进 run_all_gates.py**

在 `scripts/run_all_gates.py` 仿照 `SETTINGS_GATES` 增加一节（并按该文件现有的汇总机制把它接进总运行列表）：

```python
# The domestic-article agent: queue semantics on the server side.
AGENT_GATES = [
    ("check_agent_jobs.py", "AGENT_JOBS_OK",
     "the agent job queue claims once, retries with backoff and reclaims stale claims"),
]
```

- [ ] **Step 7: 提交**

```bash
git add database.py scripts/check_agent_jobs.py scripts/run_all_gates.py
git commit -m "Agent queue: agent_jobs table with exclusive claims, event-driven retries and stale reclaim"
```

---

### Task 2: 设置项与 `domestic_publisher` 入队模块

**Files:**
- Modify: `appconfig.py`（`SETTING_GROUPS` 与 `SETTINGS`）
- Create: `domestic_publisher.py`
- Test: `scripts/check_domestic_publisher.py`

**Interfaces:**
- Consumes: `database.enqueue_agent_job`（Task 1）、`appconfig.get`
- Produces:
  - `domestic_publisher.DOMESTIC_ARTICLE_PLATFORMS = ("wechat", "zhihu", "toutiao")`
  - `domestic_publisher.DISPLAY_NAMES = {"wechat": "公众号", "xiaohongshu": "小红书", "douyin": "抖音", "zhihu": "知乎", "toutiao": "头条号"}`
  - `domestic_publisher.enqueue_card_push(row, *, run_after: str | None = None, created_by=None, db_path=None) -> dict`——`row` 是 standalone post 行；返回 `{"job_id": int, "already": bool}`；幂等：同一 row 且内容未变时，若存在 `pending|claimed|running` 的同 payload job 则直接返回它；`run_after`（UTC ISO）透传给队列，供定时推送用
  - appconfig 新组 `("domestic", "国内渠道（文章推送）")` 与三键 `AGENT_TOKEN`（secret）、`AGENT_POLL_SECONDS`（默认 "5"）、`WECHATSYNC_ENABLED_PLATFORMS`（默认 "wechat,zhihu,toutiao"）

- [ ] **Step 1: 写失败测试**

`scripts/check_domestic_publisher.py` 全文：

```python
"""Gate for the domestic push entry: payload shape and enqueue idempotency.

    python scripts/check_domestic_publisher.py    DOMESTIC_PUBLISHER_OK

A double click on the push button must not queue two jobs: the second call
returns the first job with already=True until it leaves the queue.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def _seed_post(database, content="# 标题\n\n正文段落，含引号\"和换行。\n"):
    pid = database.add_standalone_post("freeform", "gate-seed", "wechat", content)
    return database.get_standalone_post(pid)


def main():
    directory, database, web, publisher, client = isolated_app()
    import domestic_publisher as dp

    row = _seed_post(database)

    first = dp.enqueue_card_push(row)
    check(first["already"] is False, "first push queues a job")
    job = database.get_agent_job(first["job_id"])
    payload = json.loads(job["payload"])
    check(payload["items"] == [{"target": "wechat",
                                "content": row["content"]}],
          "payload carries the row's copy verbatim, one item per target")
    check(job["kind"] == "article_sync", "push jobs are article_sync")

    second = dp.enqueue_card_push(row)
    check(second["job_id"] == first["job_id"] and second["already"] is True,
          "a queued repeat push returns the same job with already=True")

    # After the job leaves the queue, the same card may be pushed again.
    database.record_agent_job_event(first["job_id"], {"type": "started"})
    database.record_agent_job_event(
        first["job_id"],
        {"type": "done",
         "results": [{"platform": "wechat", "success": True, "draft": True}]})
    third = dp.enqueue_card_push(row)
    check(third["already"] is False and third["job_id"] != first["job_id"],
          "a finished job no longer dedupes")

    # An edited card is a different payload: no dedupe even while queued.
    row2 = _seed_post(database)
    a = dp.enqueue_card_push(row2)
    edited = dict(row2)
    edited["content"] = row2["content"] + "\n追加段落"
    b = dp.enqueue_card_push(edited)
    check(b["already"] is False and b["job_id"] != a["job_id"],
          "edited copy queues as a new job")

    print("DOMESTIC_PUBLISHER_OK")


if __name__ == "__main__":
    main()
```

若 `database.get_standalone_post` 的返回行缺 `platform`/`content` 键（以现有签名为准），调整 `_seed_post`；不得改动被测函数。

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_domestic_publisher.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'domestic_publisher'`

- [ ] **Step 3: appconfig 注册（appconfig.py）**

`SETTING_GROUPS` 在 `("platforms", ...)` 之后插入：

```python
    ("domestic", "国内渠道（文章推送）"),
```

`SETTINGS` 在平台 OAuth 键之后加：

```python
    # ── 国内渠道（文章推送）──
    "AGENT_TOKEN": {
        "group": "domestic", "label": "Agent 共享密钥", "secret": True,
        "help": "本机 agent 与服务器之间的 Bearer Token，两侧必须一致。",
    },
    "AGENT_POLL_SECONDS": {
        "group": "domestic", "label": "Agent 轮询间隔（秒）", "default": "5",
    },
    "WECHATSYNC_ENABLED_PLATFORMS": {
        "group": "domestic", "label": "启用推送的平台",
        "default": "wechat,zhihu,toutiao",
        "help": "逗号分隔；可推送 = 平台在列且本机 agent 在线。",
    },
```

- [ ] **Step 4: domestic_publisher.py 全文**

```python
"""Queue a compose card for the local agent's article pipeline.

The compose page saves one row per (card, platform). For the domestic article
platforms a row stops being "copy it by hand" the moment the local agent is
online: pushing the row enqueues an ``article_sync`` job whose payload is the
row's own copy, verbatim, one ``{target, content}`` item. The agent drives
wechatsync per item and reports per-platform results, so a partial success is
recorded where it happened, not flattened.

Enqueueing is idempotent while a job is still in flight (pending, claimed or
running): pushing twice queues once. Once a job is finished, the same card can
be pushed again — re-pushing after a fix is a normal thing to want.
"""

from __future__ import annotations

import json

import database

DOMESTIC_ARTICLE_PLATFORMS = ("wechat", "zhihu", "toutiao")

DISPLAY_NAMES = {
    "wechat": "公众号",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "zhihu": "知乎",
    "toutiao": "头条号",
}


def display_name(platform: str) -> str:
    return DISPLAY_NAMES.get(platform, (platform or "").capitalize())


def enabled_platforms(db_path=None) -> list[str]:
    """The platforms the admin switched on, in registry order."""
    raw = database.get_setting("WECHATSYNC_ENABLED_PLATFORMS", "") \
        if db_path is None else database.get_setting(
            "WECHATSYNC_ENABLED_PLATFORMS", "", db_path=db_path)
    picked = [p.strip() for p in (raw or "").split(",") if p.strip()]
    return [p for p in DOMESTIC_ARTICLE_PLATFORMS if p in picked]


def _payload_for(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else ()
    title = row["title"] if "title" in keys else None
    return {
        "title": title or display_name(row["platform"]) + "草稿",
        "items": [{"target": row["platform"], "content": row["content"] or ""}],
        "post_id": row["id"],
    }


def enqueue_card_push(row, *, run_after=None, created_by=None, db_path=None):
    """Queue one card row for the agent; idempotent while a job is in flight."""
    kwargs = {} if db_path is None else {"db_path": db_path}
    payload = json.dumps(_payload_for(row), ensure_ascii=False)
    with database.sqlite3.connect(kwargs.get("db_path", database.DB_PATH)) as conn:
        existing = conn.execute(
            """
            SELECT id FROM agent_jobs
            WHERE kind = 'article_sync' AND payload = ?
              AND status IN ('pending', 'claimed', 'running')
            ORDER BY id DESC LIMIT 1
            """,
            (payload,),
        ).fetchone()
    if existing:
        return {"job_id": existing[0], "already": True}
    job_id = database.enqueue_agent_job(
        "article_sync", payload, run_after=run_after,
        article_id=row["article_id"] if "article_id" in row.keys() else None,
        created_by=created_by, **kwargs,
    )
    return {"job_id": job_id, "already": False}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python scripts/check_domestic_publisher.py`
Expected: `DOMESTIC_PUBLISHER_OK`

- [ ] **Step 6: 注册 gate 并提交**

`run_all_gates.py` 的 `AGENT_GATES` 追加：

```python
    ("check_domestic_publisher.py", "DOMESTIC_PUBLISHER_OK",
     "card pushes enqueue verbatim copy and dedupe while a job is in flight"),
```

```bash
git add appconfig.py domestic_publisher.py scripts/check_domestic_publisher.py scripts/run_all_gates.py
git commit -m "Domestic push: settings group and the card-to-queue entry with in-flight dedupe"
```

---

### Task 3: `agent_hub` 与 `/agent/*` Token 端点

**Files:**
- Create: `agent_hub.py`
- Modify: `insights_web.py`（`PUBLIC_ENDPOINTS`（约 457 行）加三个端点名；路由函数加在 compose 路由区附近；compose 视图上下文注入放 Task 6 一起做）
- Test: `scripts/check_agent_hub.py`

**Interfaces:**
- Consumes: Task 1 队列函数、Task 2 设置
- Produces:
  - `agent_hub.check_agent_auth() -> bool`（读 `Authorization: Bearer`，与 `AGENT_TOKEN` 常量时间比对；未配置 Token 一律 False）
  - `agent_hub.record_heartbeat(payload: dict) -> None` / `agent_hub.heartbeat_snapshot() -> dict | None`（`{"age": float, "tools": {...}}`）/ `agent_hub.agent_online(window=60) -> bool`
  - 路由：`POST /agent/heartbeat`、`POST /agent/jobs/claim`、`POST /agent/jobs/<id>/events`——全部 401/JSON；claim 返回 job JSON 或 204

- [ ] **Step 1: 写失败测试**

`scripts/check_agent_hub.py` 全文：

```python
"""Gate for the /agent/* endpoints: bearer auth, claim, events, heartbeat.

    python scripts/check_agent_hub.py    AGENT_HUB_OK

The agent is a foreign client: no session, one shared token. A wrong or
missing token must get 401 on every endpoint, and the token must be read
through appconfig so the settings page can set it.
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

    # --- auth: every endpoint rejects a bad or missing token ---
    for method, path in (("post", "/agent/heartbeat"), ("post", "/agent/jobs/claim")):
        check(client.post(path).status_code == 401,
              f"{path} without a token is 401")
        check(client.post(path, headers={"Authorization": "Bearer nope"})
              .status_code == 401, f"{path} with a wrong token is 401")

    # --- claim: empty queue is 204, a pending job comes back once ---
    check(client.post("/agent/jobs/claim", headers=AUTH).status_code == 204,
          "claim on an empty queue is 204")
    jid = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "wechat", "content": "x"}]}))
    r = client.post("/agent/jobs/claim", headers=AUTH)
    check(r.status_code == 200 and r.get_json()["id"] == jid,
          "claim returns the pending job as JSON")
    check(client.post("/agent/jobs/claim", headers=AUTH).status_code == 204,
          "the claimed job is not handed out twice")

    # --- events move the job ---
    r = client.post(f"/agent/jobs/{jid}/events", headers=AUTH,
                    json={"type": "started", "agent": "layne-win"})
    check(r.status_code == 200, "started event is accepted")
    r = client.post(f"/agent/jobs/{jid}/events", headers=AUTH, json={
        "type": "done",
        "results": [{"platform": "wechat", "success": True, "draft": True}]})
    check(r.status_code == 200 and database.get_agent_job(jid)["status"]
          == "succeeded", "done event with all-success lands as succeeded")
    check(client.post(f"/agent/jobs/{jid}/events", headers=AUTH,
                      json={"type": "exploded"}).status_code == 400,
          "an unknown event type is a 400, not a 500")

    # --- events without the token are still 401 ---
    check(client.post(f"/agent/jobs/{jid}/events",
                      json={"type": "started"}).status_code == 401,
          "events endpoint checks the token too")

    # --- heartbeat: snapshot and staleness ---
    r = client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "layne-win", "tools": {"wechatsync": {
            "available": True,
            "platforms": [{"id": "wechat", "logged_in": True}]}}})
    check(r.status_code == 200, "heartbeat is accepted")
    snap = agent_hub.heartbeat_snapshot()
    check(snap and snap["age"] < 5 and
          snap["tools"]["wechatsync"]["platforms"][0]["id"] == "wechat",
          "snapshot carries tools and a fresh age")
    check(agent_hub.agent_online(window=60) is True, "a fresh heartbeat is online")
    agent_hub._LAST_HEARTBEAT["t"] = time.time() - 120
    check(agent_hub.agent_online(window=60) is False,
          "a heartbeat older than the window is offline")

    print("AGENT_HUB_OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_hub.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agent_hub'`

- [ ] **Step 3: agent_hub.py 全文**

```python
"""The server side of the local publishing agent.

The agent is not a browser session: it is a small process on the owner's
machine that polls in over HTTPS with one shared bearer token. That makes
three things true here. Auth is a constant-time token compare, not the login
wall. The heartbeat is an in-process timestamp, not a table, because its only
readers are the compose page and these endpoints in the same single app
process. And every queue mutation goes through ``database`` so the gate can
exercise the same functions the routes do.
"""

from __future__ import annotations

import hmac
import time

import database

_LAST_HEARTBEAT: dict = {"t": None, "payload": None}

HEARTBEAT_WINDOW_SECONDS = 60


def agent_token(db_path=None) -> str:
    """The configured shared token; empty means the agent door is closed."""
    if db_path is None:
        import appconfig
        return appconfig.get("AGENT_TOKEN") or ""
    return database.get_setting("AGENT_TOKEN", "", db_path=db_path) or ""


def check_agent_auth() -> bool:
    """True when the request carries exactly the configured bearer token."""
    from flask import request
    expected = agent_token()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):], expected)


def record_heartbeat(payload: dict) -> None:
    _LAST_HEARTBEAT["t"] = time.time()
    _LAST_HEARTBEAT["payload"] = payload or {}


def heartbeat_snapshot():
    """``{"age": seconds, "tools": ...}`` for the last heartbeat, or None."""
    if _LAST_HEARTBEAT["t"] is None:
        return None
    return {"age": time.time() - _LAST_HEARTBEAT["t"],
            **(_LAST_HEARTBEAT["payload"] or {})}


def agent_online(window: float = HEARTBEAT_WINDOW_SECONDS) -> bool:
    if _LAST_HEARTBEAT["t"] is None:
        return False
    return (time.time() - _LAST_HEARTBEAT["t"]) <= window


def platform_login_states() -> dict:
    """``{platform_id: logged_in}`` from the heartbeat, {} when offline."""
    snap = heartbeat_snapshot() or {}
    tools = snap.get("tools") or {}
    states = {}
    for tool in tools.values():
        for entry in (tool or {}).get("platforms") or []:
            if isinstance(entry, dict) and entry.get("id"):
                states[entry["id"]] = bool(entry.get("logged_in"))
    return states
```

- [ ] **Step 4: 路由（insights_web.py，compose 路由区之后）**

`PUBLIC_ENDPOINTS`（约 457 行）改为：

```python
PUBLIC_ENDPOINTS = {"login", "signup", "logout", "health",
                    "agent_heartbeat", "agent_claim", "agent_job_events"}
```

路由函数：

```python
# ---------------------------------------------------------------------------
# The local publishing agent's door. Token-authed, session-free; see
# agent_hub. Every handler is a thin shell: the body is one agent_hub or
# database call plus a status code.
# ---------------------------------------------------------------------------


@app.route("/agent/heartbeat", methods=["POST"])
def agent_heartbeat():
    if not agent_hub.check_agent_auth():
        return jsonify({"error": "unauthorized"}), 401
    agent_hub.record_heartbeat(request.get_json(silent=True) or {})
    return jsonify({"ok": True})


@app.route("/agent/jobs/claim", methods=["POST"])
def agent_claim():
    if not agent_hub.check_agent_auth():
        return jsonify({"error": "unauthorized"}), 401
    job = database.claim_agent_job(
        kinds=("article_sync", "video_publish"),
        agent=(request.get_json(silent=True) or {}).get("agent", ""),
    )
    if not job:
        return "", 204
    return jsonify(job)


@app.route("/agent/jobs/<int:job_id>/events", methods=["POST"])
def agent_job_events(job_id: int):
    if not agent_hub.check_agent_auth():
        return jsonify({"error": "unauthorized"}), 401
    event = request.get_json(silent=True) or {}
    try:
        job = database.record_agent_job_event(job_id, event)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)
```

文件顶部 import 区加 `import agent_hub`（与 `import database` 等现有 import 并列）。

- [ ] **Step 5: 跑测试确认通过**

Run: `python scripts/check_agent_hub.py`
Expected: `AGENT_HUB_OK`

- [ ] **Step 6: 注册 gate 并提交**

```python
    ("check_agent_hub.py", "AGENT_HUB_OK",
     "the /agent/* door authenticates by token and moves jobs through events"),
```

```bash
git add agent_hub.py insights_web.py scripts/check_agent_hub.py scripts/run_all_gates.py
git commit -m "Agent hub: token-authed heartbeat, claim and event endpoints"
```

---

### Task 4: `agent/` 包——配置、服务器客户端、执行循环

**Files:**
- Create: `agent/__init__.py`、`agent/config.py`、`agent/api.py`、`agent/worker.py`、`agent/__main__.py`
- Test: `scripts/check_agent_agent.py`

**Interfaces:**
- Consumes: Task 3 的三个 HTTP 端点
- Produces:
  - `agent.config.load_config() -> Config`（dataclass：`server_url, token, agent_name, poll_seconds, sync_timeout, wechatsync_bin`；缺失必填项时 `SystemExit` 带中文可读信息）
  - `agent.api.AgentClient(config)`，方法 `heartbeat(payload: dict) -> None`、`claim() -> dict | None`、`send_event(job_id: int, event: dict) -> None`（urllib + Bearer，超时 15s，非 2xx 抛 `AgentError`）
  - `agent.worker.run_once(client, driver, config) -> bool`（一轮：心跳→claim→执行→回事件；空闲返回 False）；`main(config) -> None`（死循环）
  - 驱动接口（Task 5 实现）：`driver.available() -> ToolStatus`、`driver.platforms() -> list[dict]`、`driver.publish_article(payload: dict) -> list[dict]`

- [ ] **Step 1: 写失败测试**

`scripts/check_agent_agent.py` 全文：

```python
"""Gate for the agent package: config, client transport, worker loop.

    python scripts/check_agent_agent.py    AGENT_AGENT_OK

The worker is exercised against a real HTTP server on localhost serving the
same three endpoints as agent_hub, so the transport (Bearer header, 204 on
empty claim) is tested, not assumed. The driver is a stub here; the real
wechatsync driver has its own gate.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _accounts_gate import check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class StubDriver:
    name = "stub"

    def __init__(self, results=None, error=None):
        self.calls = []
        self._results = results or []
        self._error = error

    def available(self):
        return {"available": True, "version": "test"}

    def platforms(self):
        return [{"id": "wechat", "logged_in": True}]

    def publish_article(self, payload):
        self.calls.append(payload)
        if self._error:
            raise self._error
        return self._results


def _server(jobs, events, state):
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
            if self.path == "/agent/heartbeat":
                state["heartbeats"].append(body)
                return self._json(200, {"ok": True})
            if self.path == "/agent/jobs/claim":
                if self.headers.get("Authorization") != "Bearer t0":
                    return self._json(401, {"error": "unauthorized"})
                if state["claimed"]:
                    self.send_response(204)
                    self.end_headers()
                    return
                state["claimed"] = True
                return self._json(200, jobs.pop(0))
            if self.path.startswith("/agent/jobs/") and self.path.endswith("/events"):
                events.append(body)
                return self._json(200, {})

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    from agent.config import Config
    from agent.api import AgentClient, AgentError
    from agent import worker

    cfg = Config(server_url="http://x", token="t0", agent_name="gate",
                 poll_seconds=0, sync_timeout=5, wechatsync_bin="wechatsync")
    client = AgentClient(cfg)

    # --- transport: bad token is an AgentError, not a silent pass ---
    srv = _server([], [], {"heartbeats": [], "claimed": False})
    cfg_bad = Config(server_url=f"http://127.0.0.1:{srv.server_port}", token="nope",
                     agent_name="gate", poll_seconds=0, sync_timeout=5,
                     wechatsync_bin="w")
    try:
        AgentClient(cfg_bad).claim()
        check(False, "a 401 from the server must raise AgentError")
    except AgentError:
        check(True, "a 401 from the server raises AgentError")

    # --- run_once: claims, reports started, executes, reports done ---
    job = {"id": 7, "kind": "article_sync",
           "payload": json.dumps({"title": "T",
                                  "items": [{"target": "wechat", "content": "c"}]})}
    events, state = [], {"heartbeats": [], "claimed": False}
    srv = _server([job], events, state)
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"
    driver = StubDriver(
        results=[{"platform": "wechat", "success": True, "draft": True}])
    check(worker.run_once(client, driver, cfg) is True,
          "a claimed job makes run_once busy")
    types = [e["type"] for e in events]
    check(types == ["started", "done"], "started then done are reported")
    check(events[1]["status"] == "succeeded", "all-success lands as succeeded")
    check(driver.calls and driver.calls[0]["items"][0]["target"] == "wechat",
          "the driver got the payload verbatim")
    check(state["heartbeats"] and
          state["heartbeats"][0]["tools"]["stub"]["available"] is True,
          "the heartbeat carries tool availability")

    # --- idle cycle ---
    events.clear()
    check(worker.run_once(client, driver, cfg) is False, "an empty queue idles")

    # --- unknown kind: failed event, no crash ---
    job_bad = {"id": 8, "kind": "video_publish",
               "payload": json.dumps({"items": []})}
    events, state = [], {"heartbeats": [], "claimed": False}
    srv = _server([job_bad], events, state)
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"
    worker.run_once(client, StubDriver(), cfg)
    last = events[-1]
    check(last["type"] == "failed" and last["code"] == "unknown_kind"
          and last.get("retryable") is False,
          "an unknown kind fails the job explicitly")

    # --- driver exception: retryable failed event ---
    job = {"id": 9, "kind": "article_sync",
           "payload": json.dumps({"items": []})}
    events, state = [], {"heartbeats": [], "claimed": False}
    srv = _server([job], events, state)
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"
    worker.run_once(client, StubDriver(error=RuntimeError("boom")), cfg)
    last = events[-1]
    check(last["type"] == "failed" and last.get("retryable") is True
          and "boom" in last.get("error", ""),
          "a driver exception is a retryable failed event")

    print("AGENT_AGENT_OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_agent.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agent'`

- [ ] **Step 3: 写 agent 包**

`agent/__init__.py`：

```python
"""The local publishing agent: poll the server, drive the CLIs, report back.

Runs on the owner's Windows machine, never on the server. Standard library
only, so installing it is `python -m agent` from a checkout, not a pip dance.
"""
```

`agent/config.py`：

```python
"""Agent configuration from the environment; required keys fail loudly."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass
class Config:
    server_url: str
    token: str
    agent_name: str
    poll_seconds: float
    sync_timeout: int
    wechatsync_bin: str


def load_config() -> Config:
    server_url = (os.getenv("INSIGHTS_SERVER_URL") or "").rstrip("/")
    token = os.getenv("INSIGHTS_AGENT_TOKEN") or ""
    missing = [name for name, value in
               (("INSIGHTS_SERVER_URL", server_url), ("INSIGHTS_AGENT_TOKEN", token))
               if not value]
    if missing:
        raise SystemExit(
            "缺少环境变量：" + "、".join(missing)
            + "。示例：INSIGHTS_SERVER_URL=https://your-server INSIGHTS_AGENT_TOKEN=... python -m agent")
    return Config(
        server_url=server_url,
        token=token,
        agent_name=os.getenv("AGENT_NAME") or socket.gethostname(),
        poll_seconds=float(os.getenv("AGENT_POLL_SECONDS") or 5),
        sync_timeout=int(os.getenv("AGENT_SYNC_TIMEOUT") or 300),
        wechatsync_bin=os.getenv("WECHATSYNC_BIN") or "wechatsync",
    )
```

`agent/api.py`：

```python
"""The server's /agent/* API, from the agent's side. Standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class AgentError(RuntimeError):
    """The server rejected or could not be reached; the loop keeps running."""


class AgentClient:
    def __init__(self, config):
        self._cfg = config

    def _post(self, path, payload, timeout=15):
        req = urllib.request.Request(
            self._cfg.server_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self._cfg.token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raise AgentError(f"{path} -> HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise AgentError(f"{path} -> {exc.reason}") from exc

    def heartbeat(self, payload: dict) -> None:
        self._post("/agent/heartbeat", payload)

    def claim(self):
        status, body = self._post("/agent/jobs/claim",
                                  {"agent": self._cfg.agent_name})
        if status == 200:
            return body
        return None

    def send_event(self, job_id: int, event: dict) -> None:
        self._post(f"/agent/jobs/{job_id}/events", event)
```

`agent/worker.py`：

```python
"""The claim-execute-report loop. One job at a time, forever."""

from __future__ import annotations

import json
import time


def _tool_report(driver) -> dict:
    status = driver.available()
    return {"available": bool(status.get("available")),
            "version": status.get("version"),
            "error": status.get("error"),
            "platforms": driver.platforms()}


def run_once(client, driver, config) -> bool:
    """One poll cycle. Returns True when a job was claimed and executed."""
    try:
        client.heartbeat({"agent": config.agent_name,
                          "tools": {driver.name: _tool_report(driver)}})
    except Exception:  # noqa: BLE001 - a lost server must not kill the agent
        pass

    job = client.claim()
    if not job:
        return False

    job_id = job["id"]
    client.send_event(job_id, {"type": "started", "agent": config.agent_name,
                               "tool": driver.name})
    try:
        kind = job.get("kind")
        if kind != "article_sync":
            client.send_event(job_id, {
                "type": "failed", "code": "unknown_kind",
                "error": f"这个 agent 只会 article_sync，收到 {kind!r}",
                "retryable": False})
            return True
        payload = json.loads(job.get("payload") or "{}")
        results = driver.publish_article(payload)
        ok = [r for r in results if r.get("success")]
        if results and len(ok) == len(results):
            status = "succeeded"
        elif ok:
            status = "partial"
        else:
            status = "failed"
        client.send_event(job_id, {"type": "done", "status": status,
                                   "results": results})
    except Exception as exc:  # noqa: BLE001 - report, requeue, keep looping
        client.send_event(job_id, {"type": "failed", "error": str(exc),
                                   "retryable": True})
    return True


def main(client, driver, config) -> None:
    print(f"agent {config.agent_name} -> {config.server_url}，Ctrl+C 退出")
    while True:
        try:
            run_once(client, driver, config)
        except Exception:  # noqa: BLE001 - the loop outlives any one cycle
            pass
        time.sleep(config.poll_seconds)
```

`agent/__main__.py`：

```python
"""`python -m agent`: load config, pick the driver, run the loop."""

from __future__ import annotations

from agent.api import AgentClient
from agent.config import load_config
from agent.drivers import get_driver
from agent.worker import main as run_loop


if __name__ == "__main__":
    cfg = load_config()
    run_loop(AgentClient(cfg), get_driver("article_sync"), cfg)
```

（`agent.drivers` 在 Task 5 落地；本任务先建一个可导入的最小占位并在 Task 5 替换其实现——占位：

```python
"""Drivers: one class per external publishing tool."""


def get_driver(kind):
    raise SystemExit("驱动将在 wechatsync 任务中提供；当前没有可用驱动")
```

注意：`check_agent_agent.py` 不导入 `agent/__main__.py`，占位不影响本任务 gate。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python scripts/check_agent_agent.py`
Expected: `AGENT_AGENT_OK`

- [ ] **Step 5: 注册 gate 并提交**

```python
    ("check_agent_agent.py", "AGENT_AGENT_OK",
     "the local agent claims over real HTTP, reports honestly and survives errors"),
```

```bash
git add agent/ scripts/check_agent_agent.py scripts/run_all_gates.py
git commit -m "Agent package: config, token client and the claim-execute-report loop"
```

---

### Task 5: wechatsync 驱动

**Files:**
- Modify: `agent/drivers.py`（整体替换 Task 4 的占位）
- Test: `scripts/check_agent_drivers.py`

**Interfaces:**
- Consumes: Task 4 的驱动接口
- Produces:
  - `agent.drivers.REPO_TO_WS = {"wechat": "weixin", "xiaohongshu": "xiaohongshu", "douyin": "douyin", "zhihu": "zhihu", "toutiao": "toutiao"}`
  - `agent.drivers.WechatSyncDriver(bin_name="wechatsync", timeout=300)`，实现 `available()/platforms()/publish_article(payload)`
  - `agent.drivers.get_driver("article_sync") -> WechatSyncDriver`
  - 失败码：`tool_missing / extension_offline / needs_login / sync_failed / timeout / interactive_prompt / unknown_target`

- [ ] **Step 0: CLI schema spike（先做，输出贴进任务笔记）**

在本机（装好 `npm i -g @wechatsync/cli` 并在 Chrome 扩展启用「MCP 连接」拿到 token 的前提下）各跑一次并记录输出/退出码：

```bash
wechatsync --version
wechatsync platforms --auth
wechatsync sync /tmp/spike.md -p zhihu -t "spike"   # 用一个测试号
```

钉死三件事：`platforms --auth` 的 JSON 结构（键名、authorized 字段名）、sync 成功/失败时 stdout/stderr 各自有什么、超时是否可依赖进程退出。若结构与下文假设不符，只调整 `_logged_in_map` 与 `_classify` 两个函数的实现，不改它们的签名与 gate 的断言语义。**没有 wechatsync 环境时**：按假设 schema 实现，gate 全部用 fixture（本来就是），并在 `check_agent_e2e.py`（Task 8 的真机门）里验证假设。

- [ ] **Step 1: 写失败测试**

`scripts/check_agent_drivers.py` 全文：

```python
"""Gate for the wechatsync driver: parsing, preflight, cleanup — no real CLI.

    python scripts/check_agent_drivers.py    AGENT_DRIVERS_OK

subprocess is faked, so this gate asserts what the driver does with what the
CLI might say: exit codes, logged-out preflight, timeouts, interactive
prompts, and that temp files never outlive a publish.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _accounts_gate import check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeCLI:
    """Stands in for subprocess.run; records argv and file contents."""

    def __init__(self, script):
        self.calls = []
        self.contents = []             # the temp file's text at call time
        self._script = script          # def(args, timeout) -> (code, out, err)

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        for a in args:
            if a.endswith((".md", ".html")):
                with open(a, encoding="utf-8") as fh:
                    self.contents.append(fh.read())
        return self._script(args)


def _driver_with(cli, tmpdir):
    import agent.drivers as drivers
    d = drivers.WechatSyncDriver(bin_name="wechatsync", timeout=5)
    d._run = cli
    d._tmp_root = tmpdir
    d._logged_in_map = lambda: {"weixin": True, "zhihu": True, "toutiao": True}
    return d


def main():
    import agent.drivers as drivers

    payload = {"title": "标题", "items": [{"target": "wechat",
                                           "content": "# T\n\n正文"}]}

    # --- success ---
    cli = FakeCLI(lambda args: (0, "saved", ""))
    d = _driver_with(cli, tempfile.mkdtemp())
    results = d.publish_article(payload)
    check(results[0]["success"] is True and results[0]["draft"] is True,
          "exit 0 is a draft success")
    check(["wechatsync", "sync"] == cli.calls[0][:2] and
          cli.calls[0][cli.calls[0].index("-p") + 1] == "weixin",
          "the repo id wechat maps to wechatsync's weixin, argv is list-form")
    check(cli.contents == ["# T\n\n正文"],
          "the copy reaches the CLI byte-for-byte through the temp file")
    for call_args in cli.calls:
        for a in call_args:
            if a.endswith((".md", ".html")):
                check(not os.path.exists(a), "temp files are cleaned up")

    # --- not logged in: preflight refuses, no sync attempted ---
    d = _driver_with(FakeCLI(lambda args: (0, "", "")), tempfile.mkdtemp())
    d._logged_in_map = lambda: {"weixin": False}
    results = d.publish_article(payload)
    check(results[0]["success"] is False and
          results[0]["code"] == "needs_login",
          "a logged-out target is refused in preflight")
    check(d._run.calls == [], "no sync runs for a logged-out target")

    # --- failure classification ---
    cases = [
        ((1, "请先登录 wechatsync", ""), "sync_failed"),
        ((3, "", "需要扫码确认"), "interactive_prompt"),
        ((2, "", "chrome extension offline"), "extension_offline"),
    ]
    for (code, out, err), want in cases:
        d = _driver_with(FakeCLI(lambda args, c=code, o=out, e=err: (c, o, e)),
                         tempfile.mkdtemp())
        results = d.publish_article(payload)
        check(results[0]["success"] is False and results[0]["code"] == want,
              f"{(code, out, err)!r} classifies as {want}")

    # --- timeout ---
    def slow(args):
        raise TimeoutError()
    d = _driver_with(FakeCLI(slow), tempfile.mkdtemp())
    results = d.publish_article(payload)
    check(results[0]["code"] == "timeout" and
          results[0].get("retryable") is True, "a timeout is retryable")

    # --- multiple items: one CLI run each, per-target results ---
    multi = {"title": "T", "items": [
        {"target": "wechat", "content": "a"},
        {"target": "zhihu", "content": "b"},
        {"target": "unknown", "content": "c"}]}
    cli = FakeCLI(lambda args: (0, "saved", ""))
    d = _driver_with(cli, tempfile.mkdtemp())
    results = d.publish_article(multi)
    check(len(cli.calls) == 2, "only known targets reach the CLI")
    check([r["platform"] for r in results] == ["wechat", "zhihu", "unknown"],
          "one result per target, in order")
    check(results[2]["code"] == "unknown_target", "an unknown target says so")

    # --- platform listing (heartbeat payload) ---
    d = _driver_with(FakeCLI(lambda args: (0, json.dumps([
        {"id": "weixin", "authorized": True},
        {"id": "zhihu", "authorized": False}]), "")), tempfile.mkdtemp())
    states = d.platforms()
    check({"wechat": True, "zhihu": False} ==
          {s["id"]: s["logged_in"] for s in states},
          "platforms maps auth state onto repo ids")

    print("AGENT_DRIVERS_OK")


if __name__ == "__main__":
    main()
```

（写文件时删掉 `platforms` 一节里多余的重复行，保留最后一份 `d._run = FakeCLI(...)` 的写法——上面为对齐版：直接用 `d = _driver_with(FakeCLI(lambda args: (0, json.dumps([...]), "")), tempfile.mkdtemp())`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_drivers.py`
Expected: FAIL —— `AttributeError: ... has no attribute 'WechatSyncDriver'`

- [ ] **Step 3: agent/drivers.py 全文（替换占位）**

```python
"""Drivers: one class per external publishing tool.

A driver owns three things — is the tool here, which of its platforms are
logged in, and how to push one article — and nothing else. The agent loop
never learns a tool's name. When wechatsync dies, a replacement driver lands
here and nothing above this file changes.

wechatsync is GPL-3.0 and is called as a separate CLI process only: no source
copied, no library linked, so Insights' MIT stays clean.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

REPO_TO_WS = {
    "wechat": "weixin",
    "xiaohongshu": "xiaohongshu",
    "douyin": "douyin",
    "zhihu": "zhihu",
    "toutiao": "toutiao",
}

_TEMP_ROOT = os.path.join(tempfile.gettempdir(), "insights-agent")

_INTERACTIVE_MARKERS = ("扫码", "验证码", "scan the qr", "verification code")
_OFFLINE_MARKERS = ("extension offline", "chrome", "扩展", "桥接")


def _run_cli(args, timeout):
    """List-argv, no shell, captured output; timeouts surface as TimeoutError."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError() from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _classify(code, out, err):
    """One CLI outcome -> a result error code, per the spec's taxonomy."""
    blob = f"{out}\n{err}".lower()
    raw = (err or out or f"exit {code}").strip()[:300]
    if any(m in blob for m in _INTERACTIVE_MARKERS):
        return "interactive_prompt", raw
    if any(m in blob for m in _OFFLINE_MARKERS):
        return "extension_offline", raw
    return "sync_failed", raw


class WechatSyncDriver:
    """Drives @wechatsync/cli: platforms --auth for state, sync per target."""

    name = "wechatsync"

    def __init__(self, bin_name="wechatsync", timeout=300):
        self._bin = bin_name
        self._timeout = timeout
        self._run = _run_cli          # swapped by the gate
        self._logged_in_map = self._auth_map   # swapped by the gate
        self._tmp_root = _TEMP_ROOT   # swapped by the gate

    # --- availability (heartbeat) -------------------------------------
    def available(self):
        try:
            code, out, _ = self._run([self._bin, "--version"], 20)
        except (OSError, TimeoutError) as exc:
            return {"available": False, "version": None,
                    "error": f"wechatsync CLI 不可用：{exc}",
                    "hint": "npm install -g @wechatsync/cli"}
        if code != 0:
            return {"available": False, "version": None,
                    "error": f"wechatsync --version 退出码 {code}",
                    "hint": "npm install -g @wechatsync/cli"}
        lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        return {"available": True,
                "version": lines[-1] if lines else None,
                "error": None}

    # --- platforms (heartbeat) -----------------------------------------
    def _auth_map(self):
        """``{ws_id: logged_in}`` from `platforms --auth` JSON."""
        try:
            code, out, _ = self._run([self._bin, "platforms", "--auth"], 30)
        except (OSError, TimeoutError):
            return {}
        if code != 0:
            return {}
        start = out.find("[")
        end = out.rfind("]")
        if start < 0 or end <= start:
            return {}
        try:
            rows = json.loads(out[start:end + 1])
        except ValueError:
            return {}
        return {str(r.get("id")): bool(r.get("authorized")) for r in rows
                if isinstance(r, dict) and r.get("id")}

    def platforms(self):
        auth = self._logged_in_map()
        return [{"id": repo, "logged_in": auth.get(ws, False)}
                for repo, ws in REPO_TO_WS.items()]

    # --- publishing ------------------------------------------------------
    def publish_article(self, payload) -> list[dict]:
        title = (payload.get("title") or "未命名")[:120]
        auth = self._logged_in_map()
        results = []
        for item in payload.get("items") or []:
            target = item.get("target")
            ws_id = REPO_TO_WS.get(target)
            if not ws_id:
                results.append({"platform": target, "success": False,
                                "draft": False, "code": "unknown_target",
                                "error": f"未知平台 {target!r}"})
                continue
            if auth.get(ws_id) is False:
                results.append({"platform": target, "success": False,
                                "draft": False, "code": "needs_login",
                                "error": "该平台在本机浏览器未登录"})
                continue
            results.append(self._sync_one(ws_id, target, title,
                                          item.get("content") or ""))
        return results

    def _sync_one(self, ws_id, target, title, content) -> dict:
        os.makedirs(self._tmp_root, exist_ok=True)
        path = os.path.join(self._tmp_root, f"push-{os.getpid()}-{ws_id}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        try:
            code, out, err = self._run(
                [self._bin, "sync", path, "-p", ws_id, "-t", title],
                self._timeout)
        except TimeoutError:
            return {"platform": target, "success": False, "draft": False,
                    "code": "timeout", "retryable": True,
                    "error": "wechatsync 超时"}
        except OSError as exc:
            return {"platform": target, "success": False, "draft": False,
                    "code": "tool_missing", "error": f"wechatsync 无法启动：{exc}"}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if code == 0:
            return {"platform": target, "success": True, "draft": True,
                    "detail": "已保存到草稿箱，请到平台确认发布"}
        error_code, message = _classify(code, out, err)
        result = {"platform": target, "success": False, "draft": False,
                  "code": error_code, "error": message}
        if error_code in ("timeout",):
            result["retryable"] = True
        return result


def get_driver(kind):
    if kind == "article_sync":
        return WechatSyncDriver()
    raise SystemExit(f"没有能执行 {kind!r} 的驱动")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python scripts/check_agent_drivers.py`
Expected: `AGENT_DRIVERS_OK`

- [ ] **Step 5: 注册 gate 并提交**

```python
    ("check_agent_drivers.py", "AGENT_DRIVERS_OK",
     "the wechatsync driver preflights login, classifies failures and cleans temp files"),
```

```bash
git add agent/drivers.py scripts/check_agent_drivers.py scripts/run_all_gates.py
git commit -m "Wechatsync driver: login preflight, failure taxonomy and per-target sync"
```

---

### Task 6: compose 推送路由 + 知乎/头条平台接入

**Files:**
- Modify: `insights_web.py`（`COMPOSE_PLATFORMS`（约 5685 行）、compose 视图上下文（grep `compose_platforms=list(COMPOSE_PLATFORMS)` 所在函数）、新增推送路由）
- Modify: `prompts_zh.py`（`PLATFORM_GUIDELINES`）
- Modify: `templates/compose.html`（渠道复选框、字数表、渠道说明文案）
- Test: `scripts/check_agent_push.py`

**Interfaces:**
- Consumes: `domestic_publisher.enqueue_card_push`（Task 2）、`agent_hub.agent_online/platform_login_states`（Task 3）
- Produces:
  - `POST /compose/post/<id>/push`（登录态；form `target=wechat|zhihu|toutiao`）→ JSON `{"job_id", "already", "platform"}`；离线时 409 `{"error": "本机代理离线"}`
  - `GET /agent-jobs/<id>.json`（登录态）→ job JSON（compose 轮询）
  - compose 视图上下文新增：`push_platforms`（list[dict]：`{id, label, enabled}`，来自 `domestic_publisher.enabled_platforms()`）、`agent_online`（bool）、`platform_login`（dict）

- [ ] **Step 1: 写失败测试**

`scripts/check_agent_push.py` 全文：

```python
"""Gate for the compose push route and the zhihu/toutiao platform additions.

    python scripts/check_agent_push.py    AGENT_PUSH_OK

The push route is the seam between the compose card and the queue: it must
refuse when the agent is offline, dedupe through domestic_publisher, and
refuse platforms outside the domestic article set.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub

    # A wechat card to push.
    post_id = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                           "正文")

    # --- agent offline: refuse with a readable error ---
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    check(r.status_code == 409 and "离线" in r.get_json()["error"],
          "pushing while the agent is offline is a 409 with a hint")

    # --- but a scheduled push queues even while offline ---
    r = client.post(f"/compose/post/{post_id}/push",
                    data={"target": "wechat",
                          "run_at": "2099-01-01T09:30"})
    check(r.status_code == 200, "a scheduled push queues while offline")
    job = database.get_agent_job(r.get_json()["job_id"])
    check(job["run_after"] and job["run_after"].startswith("2099-01-01T"),
          "the local run_at landed on run_after as UTC")
    r = client.post(f"/compose/post/{post_id}/push",
                    data={"target": "wechat", "run_at": "not-a-date"})
    check(r.status_code == 400, "a malformed run_at is a 400")

    # --- online: enqueues, dedupes, returns the job ---
    agent_hub.record_heartbeat({"tools": {"wechatsync": {"available": True}}})
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    body = r.get_json()
    check(r.status_code == 200 and body["job_id"] > 0 and
          body["already"] is False, "an online push enqueues a job")
    r2 = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    check(r2.get_json()["already"] is True and
          r2.get_json()["job_id"] == body["job_id"],
          "a second push returns the same job")

    # --- the job polls back through the .json endpoint ---
    r = client.get(f"/agent-jobs/{body['job_id']}.json")
    check(r.status_code == 200 and r.get_json()["status"] == "pending",
          "the job status endpoint serves the queue row")

    # --- target validation ---
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "linkedin"})
    check(r.status_code == 400, "a non-domestic target is a 400")
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "douyin"})
    check(r.status_code == 400,
          "a domestic but non-article platform is also refused")

    # --- zhihu/toutiao are now compose platforms ---
    from insights_web import COMPOSE_PLATFORMS
    check("zhihu" in COMPOSE_PLATFORMS and "toutiao" in COMPOSE_PLATFORMS,
          "zhihu and toutiao joined the compose platform set")
    r = client.get("/compose")
    check(b'platform-zhihu' in r.data and b'platform-toutiao' in r.data,
          "the compose page renders checkboxes for the new platforms")

    print("AGENT_PUSH_OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_push.py`
Expected: FAIL —— 404（`/compose/post/<id>/push` 不存在）或断言失败

- [ ] **Step 3: 实现路由（insights_web.py）**

`COMPOSE_PLATFORMS`（约 5685 行）：

```python
COMPOSE_PLATFORMS = ('xiaohongshu', 'douyin', 'wechat', 'zhihu', 'toutiao')
```

compose 视图函数（grep `compose_platforms=list(COMPOSE_PLATFORMS)`）在渲染上下文追加（`push_platforms` 用 dict：模板里 `platform in push_platforms` 走键成员判断）：

```python
        push_platforms={
            p: {"label": domestic_publisher.display_name(p),
                "enabled": p in domestic_publisher.enabled_platforms()}
            for p in ("wechat", "zhihu", "toutiao")},
        agent_online=agent_hub.agent_online(),
        platform_login=agent_hub.platform_login_states(),
```

路由（放 `_compose_publish_one` 之后）：

```python
@app.route('/compose/post/<int:post_id>/push', methods=['POST'])
def compose_push_post(post_id: int):
    """Queue one domestic article card for the local agent.

    The agent, not this process, does the pushing, so an offline agent is a
    409 with the way out — not a queued job that would sit until someone
    starts the agent and nobody remembers why. A ``run_at`` flips that: a
    scheduled push queues regardless of the agent, because waiting for the
    agent to come online is exactly what the schedule is for.
    """
    post = get_standalone_post(post_id)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    target = (request.form.get('target') or '').strip().lower()
    if target not in domestic_publisher.DOMESTIC_ARTICLE_PLATFORMS:
        return jsonify({"error": f"{target or '该平台'} 不支持草稿推送"}), 400
    run_after = None
    run_at = (request.form.get('run_at') or '').strip()
    if run_at:
        try:
            run_after = datetime.fromisoformat(run_at).astimezone(
                timezone.utc).isoformat(timespec='seconds')
        except ValueError:
            return jsonify({"error": "时间格式不对"}), 400
    elif not agent_hub.agent_online():
        return jsonify({"error": "本机代理离线：请在本机启动 agent（python -m agent）"}), 409
    outcome = domestic_publisher.enqueue_card_push(post, run_after=run_after)
    return jsonify({"job_id": outcome["job_id"], "already": outcome["already"],
                    "platform": target})


@app.route('/agent-jobs/<int:job_id>.json')
def agent_job_json(job_id: int):
    job = database.get_agent_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)
```

import 区补 `import agent_hub`、`import domestic_publisher`；`timezone` 若未从 `datetime` 导入则补（`datetime` 本身文件里已在用）。

- [ ] **Step 4: 知乎/头条文案指引（prompts_zh.py 的 PLATFORM_GUIDELINES）**

```python
    "zhihu": "知乎文章：开头直接给结论，正文用小标题分节论证，克制口语，结尾一句观点收束，不用话题标签",
    "toutiao": "头条号文章：标题信息量足可带数字，开头三句给足钩子，段落短节奏快，口语化，结尾引导评论互动",
```

- [ ] **Step 5: compose.html 渠道复选框与字数**

复选框行（约 904–919 行）追加两个 label（样式沿用现有 `platform-checkbox-box`）：

```html
                    <label class="platform-checkbox-box selected" data-platform="zhihu" title="生成知乎文章草稿（中文），可一键推送草稿箱">
                        <span class="check-indicator"></span>
                        <input type="checkbox" class="d-none" value="zhihu" id="platform-zhihu" checked>
                        📖 知乎
                    </label>
                    <label class="platform-checkbox-box selected" data-platform="toutiao" title="生成头条号文章草稿（中文），可一键推送草稿箱">
                        <span class="check-indicator"></span>
                        <input type="checkbox" class="d-none" value="toutiao" id="platform-toutiao" checked>
                        📰 头条号
                    </label>
```

渠道说明（约 920 行）改为：

```html
                <div class="form-text">公众号 / 知乎 / 头条号支持一键推送草稿箱（需本机运行 agent），推送后请到各平台确认发布；小红书 / 抖音生成后复制文案手动发布。</div>
```

compose.html JS 里的平台字数表（grep `xiaohongshu: 1000`）补：

```javascript
        zhihu: 20000,
        toutiao: 20000,
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python scripts/check_agent_push.py`
Expected: `AGENT_PUSH_OK`

- [ ] **Step 7: 注册 gate 并提交**

```python
    ("check_agent_push.py", "AGENT_PUSH_OK",
     "the compose push route queues through the hub and the new platforms render"),
```

```bash
git add insights_web.py prompts_zh.py templates/compose.html scripts/check_agent_push.py scripts/run_all_gates.py
git commit -m "Compose: one-click draft push for wechat/zhihu/toutiao and the two new article platforms"
```

---

### Task 7: 卡片推送按钮、结果展示与 `/agent-jobs` 队列页

**Files:**
- Modify: `templates/partials/post_item.html`（占位按钮区，grep `一键发布即将支持`）
- Modify: `templates/compose.html`（内联 JS：推送 + 轮询 + 结果 chip）
- Create: `templates/agent_jobs.html`
- Modify: `insights_web.py`（`/agent-jobs` 页面路由）
- Test: `scripts/check_agent_ui.py`

**Interfaces:**
- Consumes: Task 6 的两个路由、上下文三元组 `push_platforms/agent_online/platform_login`
- Produces: 页面行为（无新后端签名）

- [ ] **Step 1: 写失败测试**

`scripts/check_agent_ui.py` 全文：

```python
"""Gate for the push button UI, result chips and the agent-jobs page.

    python scripts/check_agent_ui.py    AGENT_UI_OK

The button must exist only for domestic article platforms, must say why when
it is disabled (agent offline / platform logged out), and the card must show
per-platform draft results instead of a bare yes/no.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub
    database.set_setting("WECHATSYNC_ENABLED_PLATFORMS", "wechat,zhihu,toutiao")

    post_id = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                           "正文")

    # --- offline first: the button exists and says why it is disabled ---
    html = client.get("/compose").get_data(as_text=True)
    check('btn-agent-push' in html, "the push button renders")
    check('本机代理离线' in html,
          "while offline the disabled button names the reason")
    check('请到' in html and '确认发布' in html,
          "draft semantics are spelled out: go confirm on the platform")
    check('/agent-jobs' in html, "the queue page is linked")

    # --- online: the same button is enabled and login dots show up ---
    agent_hub.record_heartbeat({"tools": {"wechatsync": {
        "available": True,
        "platforms": [{"id": "wechat", "logged_in": True},
                      {"id": "zhihu", "logged_in": False}]}}})
    html = client.get("/compose").get_data(as_text=True)
    check('本机代理离线' not in html,
          "a fresh heartbeat clears the offline reason")
    check('未登录' in html,
          "a logged-out platform surfaces its login state on the button")

    # A finished job shows per-platform results on the queue page.
    jid = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "T", "items": [{"target": "wechat", "content": "x"}]}))
    database.record_agent_job_event(jid, {
        "type": "done",
        "results": [{"platform": "wechat", "success": True, "draft": True,
                     "detail": "已保存到草稿箱，请到平台确认发布"}]})
    page = client.get("/agent-jobs").get_data(as_text=True)
    check('已保存到草稿箱' in page and '公众号' in page,
          "the queue page renders per-platform results with display names")

    print("AGENT_UI_OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_ui.py`
Expected: FAIL —— `btn-agent-push` 不存在

- [ ] **Step 3: post_item.html 占位按钮兑现（grep `一键发布即将支持` 所在区）**

把草稿平台占位注释块替换为：

```html
                                {# Domestic article platforms push through the
                                   local agent; the button says why whenever it
                                   cannot be used right now. #}
                                {% for entry in group.platforms %}
                                    {% if entry.platform in push_platforms %}
                                    <button type="button"
                                            class="btn btn-sm btn-success btn-agent-push"
                                            data-post-id="{{ entry.id }}"
                                            data-platform="{{ entry.platform }}"
                                            {% if not agent_online %}disabled
                                                title="本机代理离线：请在本机运行 python -m agent"
                                            {% elif platform_login.get(entry.platform) is false %}disabled
                                                title="{{ push_platforms[entry.platform].label }}在本机浏览器未登录，请先登录一次"
                                            {% endif %}>
                                        {% if platform_login.get(entry.platform) is false %}⚠ {% endif %}📤 推送到{{ push_platforms[entry.platform].label }}草稿箱
                                    </button>
                                    <span class="agent-push-result ms-2 small"
                                          data-post-id="{{ entry.id }}"></span>
                                    {% endif %}
                                {% endfor %}
```

（`platform_login` 是 Task 6 上下文里 `agent_hub.platform_login_states()` 的 dict；未登录平台按钮置灰并带 ⚠ 提示。）

- [ ] **Step 4: compose.html 推送 JS（内联脚本区追加）**

```javascript
// Domestic draft push: POST to the route, then poll the job until it leaves
// the queue. The chip carries the per-platform truth, including drafts.
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.btn-agent-push');
    if (!btn || btn.disabled) return;
    const postId = btn.dataset.postId;
    const chip = document.querySelector(`.agent-push-result[data-post-id="${postId}"]`);
    btn.disabled = true;
    chip.textContent = '推送中…';
    try {
        const form = new FormData();
        form.set('target', btn.dataset.platform);
        const r = await fetch(`/compose/post/${postId}/push`, {method: 'POST', body: form});
        const body = await r.json();
        if (!r.ok) { chip.textContent = body.error || '推送失败'; btn.disabled = false; return; }
        if (body.already) chip.textContent = '已在队列中…';
        await pollPushJob(body.job_id, chip, btn);
    } catch (e) {
        chip.textContent = '网络错误：' + e;
        btn.disabled = false;
    }
});

async function pollPushJob(jobId, chip, btn, tries = 80) {
    for (let i = 0; i < tries; i++) {
        await new Promise(res => setTimeout(res, 3000));
        const r = await fetch(`/agent-jobs/${jobId}.json`);
        if (!r.ok) continue;
        const job = await r.json();
        if (['pending', 'claimed', 'running'].includes(job.status)) continue;
        renderPushResults(job, chip);
        btn.disabled = false;
        return;
    }
    chip.textContent = '仍在执行，稍后可在发布队列页查看';
    btn.disabled = false;
}

function renderPushResults(job, chip) {
    const results = JSON.parse(job.result || '[]');
    if (!results.length) { chip.textContent = job.status === 'succeeded' ? '已完成' : '失败'; return; }
    chip.textContent = results.map(r =>
        `${r.success ? '✅' : '❌'} ${r.success ? '已推入草稿，请到平台确认发布' : (r.error || '失败')}`
    ).join('　');
}
```

- [ ] **Step 5: `/agent-jobs` 队列页**

`insights_web.py` 路由：

```python
@app.route('/agent-jobs')
def agent_jobs_page():
    jobs = database.list_agent_jobs(limit=100)
    for job in jobs:
        payload = json.loads(job['payload'] or '{}')
        job['targets'] = '、'.join(
            domestic_publisher.display_name(i.get('target', ''))
            for i in payload.get('items') or [])
        job['title'] = payload.get('title') or ''
        job['results'] = json.loads(job['result'] or '[]')
    return render_template('agent_jobs.html', jobs=jobs,
                           agent_online=agent_hub.agent_online())
```

`templates/agent_jobs.html`（沿用 `base.html` 块结构与现有页面的表格类名，最小实现）：

```html
{% extends 'base.html' %}
{% block content %}
<div class="container py-4">
    <div class="d-flex align-items-center mb-3">
        <h1 class="h3 mb-0">发布队列</h1>
        <span class="ms-3 small {{ 'text-success' if agent_online else 'text-danger' }}">
            {{ '本机代理在线' if agent_online else '本机代理离线' }}
        </span>
    </div>
    <table class="table table-sm align-middle">
        <thead><tr><th>#</th><th>标题</th><th>平台</th><th>状态</th><th>结果</th><th>时间</th></tr></thead>
        <tbody>
        {% for job in jobs %}
        <tr>
            <td>{{ job.id }}</td>
            <td>{{ job.title }}</td>
            <td>{{ job.targets }}</td>
            <td><span class="badge bg-secondary">{{ job.status }}</span></td>
            <td class="small">
                {% for r in job.results %}
                    {{ '✅' if r.success else '❌' }} {{ r.success or r.error }}<br>
                {% endfor %}
            </td>
            <td class="small text-muted">{{ job.updated_at or job.created_at }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}
```

（`base.html` 的内容块名以现有模板为准——先看 `templates/status.html` 用的块名，保持一致。）

compose.html 里加队列页入口（导航或渠道说明后）：`<a href="/agent-jobs" class="small ms-2">发布队列</a>`。

- [ ] **Step 6: 跑测试确认通过**

Run: `python scripts/check_agent_ui.py`
Expected: `AGENT_UI_OK`

- [ ] **Step 7: 注册 gate 并提交**

```python
    ("check_agent_ui.py", "AGENT_UI_OK",
     "the push button renders with its disabled reasons and results show per platform"),
```

```bash
git add templates/partials/post_item.html templates/compose.html templates/agent_jobs.html insights_web.py scripts/check_agent_ui.py scripts/run_all_gates.py
git commit -m "Compose UI: draft push buttons with offline reasons, result chips and the queue page"
```

---

### Task 8: 定时推送 UI（卡片上的 📅 入口）

**Files:**
- Modify: `templates/partials/post_item.html`（Task 7 的推送按钮旁）
- Modify: `templates/compose.html`（JS：`run_at` 传参）
- Test: `scripts/check_agent_push.py`（扩展）

**Interfaces:**
- Consumes: Task 6 的 `run_at` 表单语义（离线可入队、UTC 落 `run_after`）
- Produces: 无新后端签名

- [ ] **Step 1: 扩展失败测试**

在 `scripts/check_agent_push.py` 的 gate 输出前追加 UI 断言（离线心跳状态下）：

```python
    # --- the scheduled-push entry renders for domestic article platforms ---
    agent_hub._LAST_HEARTBEAT["t"] = None          # back to offline
    html = client.get("/compose").get_data(as_text=True)
    check('btn-agent-schedule' in html,
          "the scheduled push entry renders next to the push button")
    check('datetime-local' in html,
          "the schedule entry carries a datetime-local input")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/check_agent_push.py`
Expected: FAIL —— `btn-agent-schedule` 不存在

- [ ] **Step 3: post_item.html 加定时入口（Task 7 的推送按钮块之后）**

```html
                                    {% if entry.platform in push_platforms %}
                                    <span class="d-inline-flex align-items-center gap-1 ms-1 agent-schedule-row"
                                          data-post-id="{{ entry.id }}"
                                          data-platform="{{ entry.platform }}">
                                        <input type="datetime-local"
                                               class="form-control form-control-sm agent-schedule-at"
                                               style="max-width: 13rem;"
                                               aria-label="定时推送时间">
                                        <button type="button"
                                                class="btn btn-sm btn-outline-success btn-agent-schedule"
                                                title="到点后由本机 agent 推送草稿箱；现在入队，agent 离线也可以">
                                            📅 定时推送
                                        </button>
                                    </span>
                                    {% endif %}
```

- [ ] **Step 4: compose.html JS 追加定时分支**

```javascript
// Scheduled domestic push: same route, plus run_at; queuing while the agent
// is offline is the point, so no online check happens client-side either.
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.btn-agent-schedule');
    if (!btn || btn.disabled) return;
    const row = btn.closest('.agent-schedule-row');
    const at = row.querySelector('.agent-schedule-at').value;
    if (!at) { alert('先选一个推送时间'); return; }
    btn.disabled = true;
    try {
        const form = new FormData();
        form.set('target', row.dataset.platform);
        form.set('run_at', at);
        const r = await fetch(`/compose/post/${row.dataset.postId}/push`,
                              {method: 'POST', body: form});
        const body = await r.json();
        if (!r.ok) { alert(body.error || '定时推送失败'); btn.disabled = false; return; }
        btn.textContent = body.already ? '已在队列' : '已定时 ✓';
    } catch (e) { alert('网络错误：' + e); btn.disabled = false; }
});
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python scripts/check_agent_push.py`
Expected: `AGENT_PUSH_OK`（含新增断言）

- [ ] **Step 6: 提交**

```bash
git add templates/partials/post_item.html templates/compose.html scripts/check_agent_push.py
git commit -m "Compose: scheduled draft push entry that queues regardless of the agent"
```

---

### Task 9: 真机门、回归注册与收尾

**Files:**
- Create: `scripts/check_agent_e2e.py`（默认跳过）
- Modify: `scripts/run_all_gates.py`（NOT_GATES 说明或跳过逻辑）
- Modify: `docs/superpowers/specs/2026-10-09-domestic-channels-agent-design.md`（如实现中与 spec 有出入，回写）

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 可交付状态

- [ ] **Step 1: 真机门脚本**

`scripts/check_agent_e2e.py` 全文（没有本机 wechatsync 环境时默认跳过并打印 `AGENT_E2E_SKIP`）：

```python
"""Optional real-CLI gate: push a scratch article to a logged-in platform.

    INSIGHTS_AGENT_E2E=1 python scripts/check_agent_e2e.py    AGENT_E2E_OK

Skipped (AGENT_E2E_SKIP) unless INSIGHTS_AGENT_E2E=1 and wechatsync responds.
Proves the spike's schema assumptions against the real CLI.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main():
    if os.getenv("INSIGHTS_AGENT_E2E") != "1":
        print("AGENT_E2E_SKIP")
        return 0
    from agent.drivers import WechatSyncDriver

    d = WechatSyncDriver()
    status = d.available()
    if not status["available"]:
        print(f"AGENT_E2E_SKIP  # {status['error']}")
        return 0
    results = d.publish_article({
        "title": "Insights agent e2e",
        "items": [{"target": os.getenv("INSIGHTS_AGENT_E2E_TARGET", "zhihu"),
                   "content": "# Insights agent e2e\n\n这是一条真机门测试草稿，可删除。"}],
    })
    print(json.dumps(results, ensure_ascii=False, indent=2))
    assert any(r.get("success") for r in results), "e2e push did not succeed"
    print("AGENT_E2E_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

run_all_gates.py：在 NOT_GATES（若存在该机制，grep `NOT_GATES`）加：

```python
    ("check_agent_e2e.py", "needs a local wechatsync login; run manually with INSIGHTS_AGENT_E2E=1"),
```

- [ ] **Step 2: 全部 gate + 回归**

```bash
python scripts/run_all_gates.py
python scripts/check_manual_posts.py
```

Expected: 全部 OK token，退出码 0（尤其旧的 `check_compose_js.py`/`check_ui_wiring.py` 不被 UI 改动打破；若被打破，修模板直到通过——不许改旧断言语义）

- [ ] **Step 3: spec 回写与提交**

若 Task 5 spike 发现 CLI 输出与假设不符、或任何实现取舍偏离 spec，改 `docs/superpowers/specs/2026-10-09-domestic-channels-agent-design.md` 对应段落（一行都不许含糊）。

```bash
git add scripts/check_agent_e2e.py scripts/run_all_gates.py docs/superpowers/specs/2026-10-09-domestic-channels-agent-design.md
git commit -m "Agent e2e gate and the wrap-up: gates registered, spec reconciled with the build"
```

- [ ] **Step 4: 手工验收清单（写进提交说明或 PR 描述）**

本机侧：`npm i -g @wechatsync/cli`；Chrome 装 wechatsync 扩展并启用「MCP 连接」；登录 公众号/知乎/头条 各一次；`INSIGHTS_SERVER_URL=... INSIGHTS_AGENT_TOKEN=... python -m agent` 常驻。
服务器侧：/settings 国内渠道组设置 `AGENT_TOKEN`；compose 生成公众号卡片 → 点「推送到公众号」→ 到公众号后台草稿箱确认文章存在。
