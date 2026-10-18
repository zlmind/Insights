# Task 8 报告：Web 层——compose 列表/过滤/prompt/图库路由

**Status: DONE**
**Commit:** `6788a08` Web 层：Compose 列表/搜索/prompt/图库路由按 current_user 过滤（仅 `insights_web.py`，+48/-31）

## TDD 过程

**RED**（先跑 brief Step 2 双账号脚本，确认失败）：

```
venv/Scripts/python.exe -c "<brief Step 2 脚本>"
```

失败摘要：`/compose` 抛 500 —— `TypeError: list_standalone_posts() missing 1 required positional argument: 'user_id'`（insights_web.py compose_page），随后脚本第 9 行 `assert '正文' in r.data` AssertionError。失败原因正确（路由尚未接线 Task 4/5/6 新签名）。

**GREEN**（同脚本复跑）：

```
WEB_COMPOSE_LIST_SCOPED_OK
```

**补充探针**（超集验证，覆盖 brief 脚本没覆盖的本任务路由，双账号走真实 HTTP）：

```
WEB_COMPOSE_EXTRA_PROBES_OK
```

覆盖点：`/compose/posts/ids` count 隔离；`/compose/posts/more` HTML 不含他人帖子；`/compose/posts/search` matched_posts=0；`/compose/posts/replace` 对他人帖 affected_count=0 且内容未变、对本人帖正常替换；`/compose/library` 列表隔离；`/compose/library/update`、`/compose/library/delete` 对他人 prompt 均 404 且响应体同为 `{"error": "Prompt not found"}`；`/compose/list-images` DB 图按 uid 隔离；`/compose/recent-prompts` 隔离；`/compose/clear-prompts` 只清本人；library save/delete 自身路径 200/404 正常。

**冒烟**：`venv/Scripts/python.exe -c "import insights_web"` → `IMPORT_OK`。

## 改动清单（逐路由）

全部按模式 `uid = current_user()['id']`（路由/函数顶部 hoist）：

- `compose_page`：`list_standalone_posts(uid)`、`list_content_briefs(uid)`、`list_url_sources(uid)`、`get_url_source(id, uid)`（他人 source → selected_source 为 None，页面不带选中源）、`list_recent_prompts(uid, limit=20)`、`list_recent_image_prompts(uid, limit=10)`；同路由内 `get_pending_schedules_for_standalone_posts(page_ids, uid)`、`get_posted_info_for_standalone_posts(page_ids, uid)`（数据层新签名必须带，否则本路由 500，brief 未单列但属该函数体）。
- `compose_posts_more`：uid hoist；scheduled/posted-info 带 uid；`list_content_briefs(uid)`。
- `_filtered_post_groups`：函数内取 uid（供 more/ids/search/replace 及 Task 9 的 `_selected_post_groups` 复用）；`list_standalone_posts(uid)`、queued 过滤的 scheduled-info 带 uid。
- `_filtered_standalone_posts`：无改动——它只委托 `_filtered_post_groups`，作用域已在上游生效。
- `compose_posts_ids` / `compose_posts_search`：无直接数据调用（经 `_filtered_post_groups`），无改动需求。
- `compose_posts_replace`：uid hoist；`bulk_replace_post_content(..., user_id=uid, ...)`。
- `compose_recent_prompts`：`list_recent_prompts(uid, limit=20)`。
- `compose_clear_prompts`：`clear_recent_prompts(uid)`。
- `compose_delete_prompt`：`delete_prompt_by_content(content, uid)`。
- `compose_delete_prompts_bulk`：`delete_prompts_bulk(prompts, uid)`。
- `_serialize_library_prompts`：函数内取 uid；`list_library_prompts(uid)`（4 个调用点全是请求上下文路由）。
- `compose_library_save`：`add_library_prompt(title, content, uid)`。
- `compose_library_update`：`get_library_prompt(prompt_id, uid)` 404（文案不变）+ `update_library_prompt(prompt_id, uid, title, content)`。
- `compose_library_delete`：`delete_library_prompt(prompt_id, uid)`，按受影响行数判 404（brief 允许的两种方式之二），文案复用 update 的 `"Prompt not found"`。
- `compose_get_post`：`get_standalone_post(post_id, uid)`，404 文案 `{"error": "Post not found"}` 不变。
- `compose_list_images`：`list_uploaded_images(uid)`。

## 范围边界（未动，留后续任务）

- `_selected_standalone_posts`（~L3381 仍调无参 `list_standalone_posts()`）：Task 9 brief 的 Files 明确列了它（“get 带 uid，他人 id 静默剔除”），本任务未动。中间态下依赖它的 `/compose/posts/delete-bulk`、`/compose/posts/bulk-toggle-used` 会 500，由 Task 9 收口。
- `_post_card_html`（`list_content_briefs()` 无参）、`_selected_post_groups`（`list_standalone_posts()` 无参）、`compose_clear_all`、`save_stock_image_to_library`：Task 9。
- `sources_page`（`list_url_sources()`/`list_content_briefs()` 无参）、`briefs_page`：Task 10。
- `/sources/<id>` 的 `get_source`/`delete_source` 现以单参调 `get_url_source`（会 500）：Task 10 brief 明确覆盖（“get 带 uid，404 文案不变”），非本任务引入。

## 项目级 gate 套件

`venv/Scripts/python.exe scripts/run_all_gates.py` → **67/79 FAIL**。用 `git stash push -- insights_web.py` 对比基线（无本任务改动）复跑：**同样 67/79 FAIL，失败清单逐项一致**——全部为既有失败，非本任务引入。典型根因：`AttributeError: module 'database' has no attribute 'save_instagram_token'`、`_accounts_gate.install_fake_clients` 里 `publisher.get_linkedin_client`（publisher=None），属并行 change（accounts 模型）造成的基建破坏，与本任务无关。

## 备注

- diff 无尾随空白；提交只含 `insights_web.py`（`git add insights_web.py` 精确暂存），工作区他人未暂存改动（`.claude/skills/comet/scripts/comet-hook-guard.sh`）未纳入。
- commit 作者 `l0lsec <51931886+l0lsec@users.noreply.github.com>`，message 单行无任何 attribution。
- 探针中踩过两次断言自身写错的坑（`compose_list-images` 本地磁盘回扫分支按设计共享、brief 只要求数据库图作用域；session 存 Row 需存 id），均已修正，不影响产品代码。
