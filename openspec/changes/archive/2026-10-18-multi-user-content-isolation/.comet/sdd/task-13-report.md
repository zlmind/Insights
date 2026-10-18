# Task 13 报告：content_agent / research_engine / brief 定时跑批的归属

**Status: DONE**
**Commit: 6ee379c 内容代理与跑批：产物按 brief 主人归属，research 读取限定该用户**
**Files: content_agent.py、research_engine.py、insights_web.py（git add 精确三文件，未裹挟工作区他人改动）**

## TDD 过程

### RED（brief Step 2 脚本，verbatim，改动前运行）

```
venv/Scripts/python.exe -c "…（brief 原文脚本）…"
```

失败摘要（预期原因，非 typo）：

```
File "D:\git3\Insights\content_agent.py", line 220, in run_brief
    row = database.get_content_brief(brief_id)
TypeError: get_content_brief() missing 1 required positional argument: 'user_id'
```

`run_brief` 仍走带 owner 过滤的 `get_content_brief` 且未传归属参数——正是本任务要消除的缺口。

### GREEN（同脚本，改动后运行）

```
BRIEF_ATTRIBUTION_OK
```

断言全过：`posts_created == 1`；帖子 `user_id == other` 且 `brief_id == bid`；runner（gate 用户）名下 `list_standalone_posts == []`（不产孤儿）。

### 补充验证（brief 脚本覆盖不到的 worker 调度分支）

brief 的脚本直接调 `run_brief`，覆盖不到 `content_agent_worker` 里 4 处签名改动。补了一个临时验证（daemon 线程真跑 worker，`CONTENT_AGENT_TICK_SECONDS=1`，monkeypatch 假 run_research / generate_posts_from_prompt，无网络无模型）：due brief（owner=other）→ worker 建 run → 断言：

```
WORKER_ATTRIBUTION_OK
```

- run 行 `user_id == other`、`trigger == 'scheduled'`、`status == 'success'`；
- `last_run_status == 'success'` 且 `next_run_at` 前移（证明 finally 的 `get_brief_run(run_id, owner_id)` + `set_content_brief_schedule(bid, owner_id, ...)` 跑通）。

### 冒烟

`venv/Scripts/python.exe -c "import content_agent, research_engine"` → `IMPORT_OK`。

## 改动清单

### content_agent.py

- `_attach_image(post_id, content, user_id)`：晚导入调用改 `_maybe_attach_link_image(post_id, content, user_id=user_id)`（Task 9 新签名；执行线程无请求上下文）。
- `_persist_posts(..., user_id, ...)`：`add_standalone_post(..., user_id=user_id)`；透传给 `_attach_image`。
- `_auto_queue`：`add_scheduled_post(..., user_id=brief["user_id"], ...)`。
- `run_brief`：
  - 行读取改 `database.get_content_brief_unscoped(brief_id)`（Task 6 白名单 helper）；
  - `owner = brief.get("user_id")`，`if not owner: raise ValueError`（defensive fail-fast，先于建 run，不留悬行）；
  - `create_brief_run(brief_id, owner, trigger=trigger)`；
  - `ResearchBrief(..., user_id=owner)`；
  - 两处 `_persist_posts(..., user_id=owner)`；
  - 两处 `add_article(..., user_id=owner)`；
  - `finalize_brief_run(run_id, owner, status=...)`。

### research_engine.py

- `ResearchBrief` 增加首字段 `user_id: int`（唯一构造点 content_agent 用全关键字传参，位置序不变受影响；无默认值字段按 dataclass 规则须置于带默认字段前）。
- `_web_hits_to_items`：`get_url_source_by_url(canon, brief.user_id)`、`add_url_source(..., user_id=brief.user_id)`。
- `_gather_existing`：`list_url_sources(brief.user_id)`、`list_all_episodes("published", brief.user_id)`。
- `_drop_already_posted(items, stats, brief)`：`count_standalone_posts_by_source_urls(urls, brief.user_id)`；`run_research` 调用点同步。

### insights_web.py

- `content_agent_worker` 调度分支：`owner_id = brief["user_id"]`；NULL-owner 防御（error log + continue，沿 Task 12 worker 的先例）；`get_active_brief_run(bid, owner_id)`、`create_brief_run(bid, owner_id, trigger="scheduled")`。`usage_meter.user_context(owner...)` 链路未动（用量归属沿用）。
- `brief_run_now`：Task 10 已改的 `create_brief_run(brief_id, uid, trigger='manual')` 不动；只把 `_bg` except 兜底 finalize 补上 `uid`（见下）。

## 超出 brief 字面的决策点（均已在代码注释说明）

1. **`research_text = rres.research_brief if items else ""`（原 `if rres`）**——brief 要求脚本 verbatim 执行；假 run_research 对象只有 items/warnings/query_plan，原行在 items 为空时也急切访问 `research_brief` 会 AttributeError → run_brief 吞成 status=error → 断言必挂。`research_text` 的两个消费点都在 `items` 非空分支内，改为消费处求值后生产行为完全等价（真实 ResearchResult 恒带该属性），脚本 verbatim 通过。
2. **worker finally 的 `get_brief_run(run_id, owner_id)` 与 `set_content_brief_schedule(bid, owner_id, ...)`**——brief 只点名 create/get_active 两处，但这两个旧调用在新签名下会 TypeError：finally 抛错被外层 except 吃掉 → schedule 永不前进 → 该 brief 每 tick 重跑。不改则 worker 实际不可用。
3. **worker 开头 NULL-owner 防御 continue + error log**——存量 NULL user_id 的 legacy brief（迁移不回填）一旦 due，会让 `create_brief_run(bid, None)` 在 try 外抛 ValueError，毒化整个 tick（同批其余 brief 全被跳过）。日志 + 跳过与 Task 12「无归属只记日志」先例一致。
4. **`brief_run_now` except 兜底 finalize 补 `uid`**——旧调用 `finalize_brief_run(run_id, status=...)` 在新签名下 TypeError 且被内层 `except: pass` 静默吞掉：manual run 未处理异常时 run 行会永久悬挂在 'running'，挡住后续所有 run 的 active-run guard。runner 即 owner，`uid` 闭包内可得。
5. **run_brief not-found 分支去掉 finalize**——brief 行不存在时无 owner 可归属化 finalize（owner-scoped），改为 error log + 返回 error dict。该路径仅发生于「建 run 后、读行前 brief 被并发删除」的窗口；悬行随 brief 删除不可见（delete_content_brief 级联删 run）。

## 既有 gate 状态（非本任务引入）

`scripts/check_brief_fallback.py` 当前红：失败栈在 seed 阶段 `database.create_content_brief` 缺 `user_id`（`_require_user` 抛 ValueError，Task 4 落地后即红，先于任何 `run_brief` 调用）。属 Task 14「gate 脚本适配数据层签名」范围；本任务未触碰 database.py 与该脚本。

## 对应 tasks.md

- 4.1（content_agent 跑批部分）、4.2（brief 定时跑批查询过滤补齐 + 产物归属）。
