# Task 10 报告：Web 层——sources/thumbnails/briefs/schedule 路由

**Status: DONE_WITH_CONCERNS**（实现与验证全部完成；验证脚本与 gate 套件各有一处环境性问题，见「偏差与顾虑」）

**Commit:** `5e6bdc6` Web 层：sources/thumbnails/briefs/schedule 路由按 current_user 过滤
（仅 `git add insights_web.py`；作者 `l0lsec <51931886+l0lsec@users.noreply.github.com>`；无任何 attribution）

## 改动内容（D:\git3\Insights\insights_web.py，+72/-40）

全部按 brief 函数符号定位（brief 行号锚点确实漂移，已按符号落位）：

| 路由 | 改动 |
|------|------|
| `schedule_page` | `uid` hoist；`database.list_scheduled_posts(uid, date_from=first, date_to=last, sort_order='asc')`；`list_time_slots`/每日限额保持全局未动 |
| `sources_page` | `list_url_sources(uid)`、`count_standalone_posts_by_source_urls(urls, uid)`、YouTube 分支 `get_episode(url, uid)` |
| `source_posts` | `get_url_source(id, uid)` 404（文案不变）；`list_standalone_posts_by_source_url(url, uid)`；`get_posted_info_for_standalone_posts(ids, uid)` |
| `add_source` | 重复检查 `get_url_source_by_url(url, uid)`——同 URL 双用户不再 409；YouTube 分支与非 YouTube 分支两处 `add_url_source(..., user_id=uid)` |
| `get_source` / `delete_source` | get 带 uid 404；`delete_url_source(source_id, uid)`（数据层 uid 必填） |
| `transcribe_source` | get 带 uid 404；`get_episode(url, uid)`；`queue_episode(..., user_id=uid)`；task_queue item 的 `user_id` 直接用 uid（username 仍取 `current_user()`） |
| `reextract_source` | get 带 uid 404；两处 `update_url_source_content(source_id=..., user_id=uid, ...)` |
| `thumbnails_generate` | `add_generated_thumbnail(..., user_id=uid)` 落库归属 |
| `thumbnails_saved_list` / `thumbnails_saved_delete` | `list_generated_thumbnails(uid, limit=100)`；`get_generated_thumbnail(id, uid)` 404 + `delete_generated_thumbnail(id, uid)` |
| `briefs_page` | `list_content_briefs(uid)`；runs 查询 `list_brief_runs(bid, uid, limit=1)` |
| `brief_create` | `data['user_id'] = uid`（原语义保留）；`set_content_brief_schedule(brief_id, uid, ...)`；内层 `get_content_brief(brief_id, uid)` |
| `brief_edit` | `get_content_brief(brief_id, uid)` 404；`update_content_brief(brief_id, uid, **data)`；`set_content_brief_schedule(brief_id, uid, ...)` |
| `brief_delete` | 新增先 `get_content_brief(brief_id, uid)` 404，再 `delete_content_brief(brief_id, uid)` |
| `brief_toggle` | get 带 uid 404；`set_content_brief_enabled(brief_id, uid, enabled)`；schedule 带 uid |
| `brief_run_now` | get / `get_active_brief_run(brief_id, uid)` 带 uid；`create_brief_run(brief_id, uid, trigger='manual')`——runner 即 brief 主人；`_bg` 闭包仍捕获 `runner`（usage 归属不变） |
| `brief_runs` | 新增 brief 归属校验 `get_content_brief(brief_id, uid)` 404（否则他人 brief 返回空列表而非 404，验证脚本要求 404）；`list_brief_runs(brief_id, uid, limit=20)` |
| `brief_run_status` | `get_brief_run(run_id, uid)` 404——run 归属校验拦住跨用户轮询 |

未动：`/activity` `/usage`（Task 11）、`content_agent_worker`（Task 12/13）、Task 1-9 已接线代码、`thumbnails_fetch_metadata`/`thumbnails_suggest_prompt`（无数据库调用，brief 列出但无落库点可改）。

## TDD 过程

### RED（brief Step 2 原文脚本，改造前）

命令：`venv/Scripts/python.exe -c "<brief Step 2 脚本原文>"`

失败摘要：

```
File "D:\git3\Insights\insights_web.py", line 6469, in get_source
    source = get_url_source(source_id)
TypeError: get_url_source() missing 1 required positional argument: 'user_id'
Traceback (most recent call last):
  File "<string>", line 11, in <module>
AssertionError
```

第 11 行即 `assert client.get('/sources/%d' % sid).status_code == 404`——他人访问 source 得 500（数据层签名已必填 uid 而路由未接线），失败原因正确：路由侧改造缺失。

### GREEN（改造后）

命令：同上（测试侧加 trafilatura stub，见下节）。

输出：

```
WEB_SOURCES_BRIEFS_SCOPED_OK
```

4 个跨用户 404 断言、briefs 页隔离断言、同 URL 第二用户 200 + `list_url_sources(other)` 恰 1 条，全部通过。

### 收尾验证

- 冒烟：`venv/Scripts/python.exe -c "import insights_web"` 通过（无输出）。
- gate 套件：`run_all_gates.py` 基线对比——改动前后同为 **66 of 79 失败且失败清单逐项一致**。失败主因是并行会话已删除 `social_publisher`（`'NoneType' object has no attribute 'get_linkedin_client'`、`database has no attribute 'save_instagram_token'` 等），属既有失效，非本任务引入（memory「Sidebar redesign 2026-10-14」亦有记录）。通过项含 `check_usage.py scoping → USAGE_SCOPING_OK`。
- `check_brief_fallback.py` 失败点在 `database.create_content_brief(...)` seed 未传 user_id（Task 5 起必填），traceback 全在 database 层，与 insights_web.py 无关；基线（stash 我的改动后）同样失败。

## 偏差与顾虑

1. **验证脚本未逐字跑通，测试侧补了两处装置修复**（生产代码零改动）：
   - **trafilatura 网络依赖**：brief 脚本 `client.post('/sources', data={'url': 'http://s'})` 走非 YouTube 分支会真实抓取，本机 DNS 解析失败 → `trafilatura.fetch_url` 返回 None → 路由 400 ≠ 200。这是 brief 脚本漏了网络隔离（`_accounts_gate` 自己的设计哲学就是 "makes no network calls"）。修复：测试内 stub `trafilatura.fetch_url/extract/extract_metadata`，脚本其余部分逐字保留。409→200 的核心断言在 stub 前后语义一致（改造后裸跑时该步返回 400 而非 409，已证明不再 409）。
   - **`create_user` 返回 int**：brief 脚本 `other = database.create_user(...)` 后用 `other['id']`，而 `database.create_user` 返回 int uid（brief 脚本自己的 session 行 `s['user_id'] = other` 也是按 int 用的）。`other['id']` 会 `TypeError: 'int' object is not subscriptable`。修复：直接用 `other`。RED 阶段因第 11 行先失败，此问题被掩盖。
   建议：brief 模板后续为含抓取的路由写验证脚本时预置 trafilatura stub，并统一 `create_user` 的用法。
2. **`check_brief_fallback.py` 需要随 Task 5 更新 seed**（未传 user_id）：不属本任务文件范围（只允许改 insights_web.py），留给协调者决定归入哪个后续 task。
3. gate 套件 66 个既有失败请勿记在本任务头上——已用 stash 基线证明清单一致。
