# Task 6 报告：数据层 content_briefs/content_brief_runs/scheduled_posts 作用域

**Status:** DONE
**Commit:** `3dd6390` 数据层：content_briefs/runs/scheduled_posts 按 user 作用域（仅 `database.py`，+215/-116）

## 改动内容（全部在 D:\git3\Insights\database.py）

briefs（8 处）+ runs（5 处）+ scheduled_posts（20 处含 1 个私有助手），全部走既有 `_require_user(user_id)` 守卫（复用 Task 2 落地的同名函数，未重定义）。

### content_briefs（7 改 + 1 增）
- `create_content_brief(...)`：`user_id` 位置不变（仍在 `max_drafts_per_run` 之后），INSERT 列清单已含 `user_id`（此前 Task 1 已加列，本次把实参落到 `uid`）。
- `update_content_brief(brief_id, user_id, db_path=..., **fields)`：`WHERE id = ? AND user_id = ?`。**并从 `_BRIEF_UPDATABLE` 白名单移除 `"user_id"`**——此前 `update_content_brief(..., user_id=其他用户)` 可改写归属，属隔离漏洞；现 ownership 是作用域不是字段。
- `get_content_brief(brief_id, user_id, ...)`：`WHERE id = ? AND user_id = ?`，跨用户返回 None。
- `list_content_briefs(user_id, ...)`：`WHERE user_id = ? ORDER BY created_at DESC`。
- `delete_content_brief(brief_id, user_id, ...)`：两条 DELETE 均加 `AND user_id = ?`（runs 级联删除同样限定归属，brief 明确要求）。
- `set_content_brief_enabled(brief_id, user_id, enabled, ...)`、`set_content_brief_schedule(brief_id, user_id, next_run_at=None, last_run_at=None, last_run_status=None, ...)`：UPDATE 加 `AND user_id = ?`。
- **新增 `get_content_brief_unscoped(brief_id, db_path=...) -> Optional[sqlite3.Row]`**：无归属过滤，docstring 注明仅供 `content_agent.run_brief` worker 入口先用它取行、再按行上 `user_id` 行事；用户界面一律走 `get_content_brief`。
- **白名单保持**：`get_due_content_briefs(now_iso, db_path=...)` 不加 user 过滤，docstring 补一句裁定理由（worker 队列跨 owner，行上 user_id 决定归属）。

### content_brief_runs（5 个）
- `create_brief_run(brief_id, user_id, trigger='manual', ...)`：INSERT 列清单加 `user_id`（Task 1 已加列，本次写进 INSERT）。
- `finalize_brief_run(run_id, user_id, status, sources_found=0, ...)`：user_id 落在 run_id 后、status 前，UPDATE 加 `AND user_id = ?`。
- `get_brief_run(run_id, user_id, ...)`、`list_brief_runs(brief_id, user_id, limit=20, ...)`、`get_active_brief_run(brief_id, user_id, ...)`：SELECT 均 `AND user_id = ?`。

### scheduled_posts（19 函数 + 1 助手）
- `add_scheduled_post(scheduled_for, post_type, user_id, social_post_id=None, article_id=None, standalone_post_id=None, platform='linkedin', status='pending', linkedin_post_urn=None, account_id=None, db_path=...)`：user_id 插在 post_type 之后（brief 指定位置），INSERT 列清单加 `user_id`。
- `list_scheduled_posts(user_id, status=None, platform=None, date_from=None, date_to=None, sort_order='asc', db_path=...)`：既有全部关键字参数保留（含 brief 未点名但源码已有的 `platform`），`WHERE 1=1` 后固定追加 `AND sp.user_id = ?`，uid 为首参。
- `(id, user_id, ...)` 形态：`get_scheduled_post`、`get_scheduled_group_ids`、`update_scheduled_post_time(id, user_id, scheduled_for)`、`update_scheduled_post_status(id, user_id, status, linkedin_post_urn=None, error_message=None)`、`increment_retry_count`（UPDATE 与回读 SELECT 双处过滤）、`cancel_scheduled_post`、`delete_scheduled_post`。
- `(ids, user_id, ...)` 形态：`get_pending_schedules_for_social_posts`、`get_pending_schedules_for_standalone_posts`、`get_pending_schedule_ids_for_standalone_posts`、`get_posted_info_for_standalone_posts`（均 `AND user_id = ?`，参数 `[*ids, uid]`）。
- 批量/整组形态：`reorder_scheduled_posts(post_ids, user_id, ...)`、`move_posts_to_position(post_ids, user_id, position, ...)`、`delete_scheduled_posts_bulk(post_ids, user_id, ...)`、`redistribute_scheduled_posts(platform, user_id, ...)`（SELECT、清时间 UPDATE、逐条改时均加归属；`get_next_available_slot` 时段配置保持全局）、`clear_pending_scheduled_posts(user_id, ...)`、`cancel_scheduled_post_by_source(post_type, post_id, user_id, platform, ...)`（user_id 紧跟资源 id post_id，与 `(id, user_id, ...)` 惯例一致）、`get_scheduled_posts_for_article(article_id, user_id, ...)`（`WHERE (sp.article_id = ? OR soc.article_id = ?) AND sp.user_id = ?`）。
- 私有助手 `_assign_group_times(conn, groups, times, user_id)`：加 `AND user_id = ?`（reorder/move 的组时间落位写路径，防御性收口，id 来源本已按用户过滤）。
- database.py 内部自调用点同步改签名：`get_scheduled_group_ids`→`list_scheduled_posts(uid, ...)`、`redistribute_scheduled_posts`→`update_scheduled_post_time(post_id, uid, next_slot, db_path=...)`、`reorder_scheduled_posts`/`move_posts_to_position`→`list_scheduled_posts(uid, ...)` 与 `_assign_group_times(..., uid)`。
- **白名单保持**：`get_pending_scheduled_posts(db_path=...)` 不加 user 过滤，docstring 补裁定理由（发布侧消费队列，吃全局时段；行上归属不因此暴露给任何用户界面）。

### 签名约定说明（Optional=None 落位）
仅 1 处：`create_content_brief` 的 `user_id` 前面全是带默认值参数，语法不允许字面必填，按惯例落 `Optional[int] = None` + `_require_user`（docstring 注明 None 默认只为语法存在），None 显式传入抛 `ValueError("user_id is required for content operations")`——这正是验证脚本第一条断言。其余全部函数 user_id 均位于带默认值参数之前，为字面必填 `user_id: int` + `_require_user`。

## TDD 过程

### RED（实现前，特征性失败）
命令（brief Step 2 脚本原文，tempfile 临时库，未碰 insights.db）：

```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
try: database.create_content_brief('n','i', user_id=None, db_path=p); raise SystemExit('none accepted')
except ValueError: pass
...
"
```

失败摘要：

```
none accepted
exit=1
```

失败原因正确：旧签名 `user_id: Optional[int] = None` 且无守卫，`user_id=None` 被静默接受写库，未抛 ValueError——正是「写入口缺归属强制」的特征。

### GREEN（实现后）
同一命令重跑：

```
BRIEFS_SCHEDULE_SCOPING_OK
exit=0
```

覆盖点：brief user_id=None 拒收、跨用户 get_content_brief 为 None、run 跨用户不可读、list_brief_runs 归属内 1 条、scheduled user_id=None 拒收、list 跨用户为空、cancel 跨用户 False、本用户行 status='pending'。

### 补充自检（同一临时库方法，覆盖 brief 脚本未及的实现面）
`EXTENDED_SCOPING_OK, exit=0`：`get_content_brief_unscoped` 返回行且行上 user_id 正确；两个白名单函数跨 owner 仍工作（`get_due_content_briefs` 命中 a 的 brief、`get_pending_scheduled_posts` 看见 a/b 双方到期行）；`update_content_brief`/`set_content_brief_enabled`/`set_content_brief_schedule`/`finalize_brief_run`/`update_scheduled_post_time`/`increment_retry_count`/`cancel_scheduled_post_by_source`/`delete_scheduled_posts_bulk`/`delete_content_brief`（runs 级联）跨用户一律 no-op/False/0。第一轮误报说明：初版脚本用 2099 未来时间断言 `get_pending_scheduled_posts` 返回 2 行——该函数只取 `scheduled_for <= now`，2099 未到期返回 0 是正确行为，属测试数据错误非实现错误，改用已到期时间后通过（实现未改）。

### Step 3 冒烟
`venv/Scripts/python.exe -c "import database"` → `SMOKE_OK`。

## 调用方缺口（仅记录，未改——调用方属批次 C 后续任务）

本次签名变更波及（grep 统计，均不含 database.py 自身）：

- `insights_web.py`（39 处调用，schedule 页/queue/compose 路由）
- `scripts/check_queue_groups.py`（27）、`scripts/check_ig_people.py`（11）、`scripts/check_scheduler_accounts.py`（10）、`scripts/check_bulk_platform.py`（10）、`scripts/check_video.py`（4）、`scripts/check_backcompat.py`（4）、`scripts/check_manual_posts.py`（1）、`scripts/check_crosspost.py`（1）、`scripts/_video_ui_gate.py`（1）
- `content_agent.py`（5 处，`run_brief` worker——后续任务按 brief 改用 `get_content_brief_unscoped` 取行再按行上 user_id 行事）
- `scripts/check_brief_fallback.py`（5 处）

预期这些调用点在调用方任务落地前会以缺 user_id 实参的形式报错（与 Task 2-5 后的状态一致），不属本任务修复范围。

## 提交核验
- `git add database.py` 精确暂存，`git show --stat HEAD` 仅 `database.py` 一个文件（+215/-116）。
- `git log -1`：`3dd6390 l0lsec <51931886+l0lsec@users.noreply.github.com>`，commit message 为 brief 原文，grep 无任何 attribution（Co-Authored-By/Claude/Generated 均 0 命中）。
- 工作区他人未暂存改动（`.claude/skills/comet-hook-guard.sh` 等）未纳入提交。
- 未动 Task 1-5 已完成代码；未动 schema。
