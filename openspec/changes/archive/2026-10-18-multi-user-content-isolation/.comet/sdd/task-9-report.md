# Task 9 报告：Web 层——Compose 生成/卡片/导入/媒体/推送路由

**Status: DONE_WITH_CONCERNS**（改动全部落地并 GREEN；顾虑见文末，均为批次内已知/计划内状态）

## Commit

- `ddba3ff` Web 层：Compose 生成/导入/媒体/推送路由写入当前用户归属，跨用户 404
- 只暂存了 `insights_web.py`（176 insertions / 118 deletions）；工作区他人未暂存改动（`.claude/skills/comet/scripts/comet-hook-guard.sh`）未纳入。
- 作者/Committer：`l0lsec <51931886+l0lsec@users.noreply.github.com>`；无任何 attribution trailer。

## TDD 过程

### RED（改造前）

命令（brief Step 2 原文）：

```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
... (brief 原文脚本)
"
```

失败摘要：

```
File "D:\git3\Insights\insights_web.py", line 4157, in compose_create_post
    post_ids = [
File "D:\git3\Insights\insights_web.py", line 4158, in <listcomp>
    add_standalone_post(
TypeError: add_standalone_post() missing 1 required positional argument: 'user_id'
Traceback (most recent call last):
  File "<string>", line 11, in <module>
AssertionError
```

失败原因正确：Task 4 已把 `add_standalone_post` 的 `user_id` 变为必填，compose 写路径尚未接线，`/compose/post/create` 直接 500；跨用户 delete/toggle-used 也未 404。

### GREEN（改造后）

命令：同上（唯一偏差见「脚本笔误修正」）。

输出：

```
WEB_COMPOSE_WRITE_SCOPED_OK
```

验证语义全部满足：
1. `other` 登录经真实 `POST /compose/post/create` 手写帖子 → 200，且落库行 `user_id == other`
2. `other` 对 `me` 的帖子 `POST /compose/post/<id>/delete` → 404，`me` 的帖子仍存在
3. `other` 对 `me` 的帖子 `POST /compose/post/<id>/toggle-used` → 404

### 脚本笔误修正（须知）

brief 验证脚本中 `rows[0]['user_id'] == other['id']` 无法运行：`database.create_user()` 返回 `int`（uid，见 `database.py:3608` 签名 `-> int`），`other` 本身就是 int，`other['id']` 抛 `'int' object is not subscriptable`（RED 阶段被更早的 500 掩盖，未暴露）。GREEN 跑法按语义等价改为 `rows[0]['user_id'] == other`、`list_standalone_posts(other, ...)`。建议后续任务修 brief 原文。

## 改动明细（全部在 insights_web.py，按 brief 函数符号逐条落实）

签名变化（brief Interfaces 点名）：
- `save_stock_image_to_library(image_url, user_id, direct_save=False)`——`list_uploaded_images(user_id)` + 三处 `add_uploaded_image(..., user_id=user_id)`
- `_save_post_for_accounts(account_ids, user_id, **kwargs)`——必填，透传 `add_standalone_post(user_id=...)`
- `_maybe_attach_link_image(post_id, content, user_id, sibling_ids=None)`——**必填**；内部 executor 线程 `get_standalone_post(pid, user_id)`、`save_stock_image_to_library(og, user_id=user_id)`、`update_standalone_post_image(target, url, user_id)`
- `_copy_post_to_target(post, platform, account_id, user_id, siblings=())`——透传 `add_standalone_post`

路由/辅助逐条：
- `compose_generate`：hoist uid；URL 分支 `add_url_source(..., user_id=uid)`；保存走 `_save_post_for_accounts(..., user_id=uid)`；`_maybe_attach_link_image(..., user_id=uid)`
- `compose_create_post`：hoist uid；按平台分组改走 `_save_post_for_accounts([account_ids], user_id=uid, ...)`（行为等价：targets 顺序、`account_id=None` 默认账号语义均保持）；补图带 uid
- `compose_render_post_card`：`get_standalone_post(post_id, uid)` 404 同文案；`_post_card_html(..., uid, ...)`
- `compose_toggle_post_platform`：get 带 uid 404；`_card_rows`/`_copy_post_to_target`/`delete_standalone_post(target_id, uid)`/卡片重渲染全带 uid
- `_selected_post_groups(payload, user_id)` + `_bulk_platform_request()` 内部 hoist uid：他人 id 找不到卡片
- `compose_bulk_add_platform`：pending/posted 查询、`get_standalone_post(_copy_post_to_target(..., uid, ...), uid)`、渲染全带 uid
- `compose_bulk_remove_platform`：`get_pending_schedule_ids_for_standalone_posts(row_ids, uid)`、`get_posted_info_for_standalone_posts(row_ids, uid)`、`delete_standalone_posts_bulk(going_ids, uid)`、渲染带 uid
- `compose_import_file`：`get_existing_standalone_content(uid)`（查重按用户隔离）、`add_standalone_post(..., user_id=uid)`、补图带 uid
- `compose_edit_post`：get 带 uid 404；`update_standalone_post(row['id'], uid, new_content)`；补图带 uid
- `compose_update_post_image` / `compose_update_post_video`：get 404 带 uid；经 `_apply_card_image/_apply_card_video(..., uid)`
- `compose_get_stock_images`：get 404 带 uid（无写库）
- `compose_apply_stock_image`：`save_stock_image_to_library(image_url, user_id=uid)` + 卡片图带 uid
- `compose_apply_link_image` / `compose_refresh_source_image`：`save_stock_image_to_library(og_image, user_id=uid)` + 卡片图带 uid
- `compose_upload_image`（两处）与 `_store_uploaded_video`（一处）：`add_uploaded_image(..., user_id=uid)` —— brief「三处」即此（都在请求上下文内 hoist uid）
- `compose_annotate_media` / `compose_search_stock_images` / `compose_stock_images_status`：确认不触 `uploaded_images`/`standalone_posts` 库（annotate 只落盘/Cloudinary），按 brief 不动
- `compose_delete_post`：get 404 + `delete_standalone_post(row['id'], uid)`，`_card_rows(..., uid)`
- `compose_delete_bulk`：`_selected_standalone_posts(data, uid)` + `delete_standalone_posts_bulk(post_ids, uid)`
- `compose_bulk_update_images`：get 带 uid（他人 id 静默跳过，保持批量接口语义）+ update 带 uid
- `compose_toggle_used`：get 404 + `mark_standalone_post_used(row['id'], new_status, user_id=uid)`
- `compose_bulk_toggle_used`：`_selected_standalone_posts(data, uid)` + mark 带 uid
- `compose_push_post`：`get_standalone_post(post_id, uid)` 404 同文案
- `compose_clear_all`：`list_standalone_posts(uid)` + `delete_standalone_posts_bulk(post_ids, uid)`（只清自己的）
- `compose_generate_from_source`：`get_url_source(source_id, uid)` 404 同文案、`update_url_source_last_used(source_id, uid)`、`_save_post_for_accounts(..., user_id=uid)`、补图带 uid
- `_selected_standalone_posts(payload, user_id)`：`list_standalone_posts(user_id)`，他人 id 静默剔除（brief 点名）
- brief 未点名但为使清单内路由可用的辅助（均只被清单内路由调用）：`_card_rows(post, post_ids, user_id)`、`_apply_card_image(post, image_url, user_id)`、`_apply_card_video(post, video_url, user_id)`、`_post_card_html(post_ids, user_id, display_index=None)`（内部 `list_content_briefs(user_id)`、pending/posted 带 uid）

未动（计划内）：
- `source_posts`、`sources_page` 等 sources 区（`get_url_source(source_id)` 余 5 处单参调用）→ Task 10
- `content_agent.py:133` `_maybe_attach_link_image(post_id, content)` 现与必填 user_id 签名暂不匹配 → Task 13 传 brief 主人（brief Interfaces 明确允许）
- Task 1-8 已完成代码（`_filtered_*`、`compose_list_images`、`current_user` 等）一律未动

## 冒烟 + 全量 gate

- `venv/Scripts/python.exe -c "import insights_web"` → `IMPORT_OK`
- 项目套件 `scripts/run_all_gates.py`（PYTHONIOENCODING=utf-8）：**66/79 红**。
  为区分既有红与回归，在临时 worktree（HEAD=6788a08，不含本改动）跑基线：**68/79 红**。
  - 失败主因是批次前遗留：`database` 无 `save_instagram_token` 等（并行会话删 social_publisher/账号体系）、`publisher is None`、部分旧 gate 未跟上数据层新签名——与本次改动无关。
  - 基线红、本次绿的 3 个（extension_tests/extension_protocol/manual_posts targets）是 worktree 缺 untracked 文件所致，非本次修复目标。
  - 本次名单比基线多出的 `check_brief_fallback.py`：基线 worktree 里该文件不存在（untracked，基线日志报「registered but the file does not exist」的 2 个 inventory problem 之一），因此基线并未跑过它；其失败点是该脚本第 66 行直接调 `database.create_content_brief` 缺 user_id（Task 3 数据层改动的遗留失配），**不在本任务文件清单，非本次回归**。
  - 结论：基线可跑且绿的 gate 无一被本次改动弄红。

## 顾虑（DONE_WITH_CONCERNS 原因）

1. `content_agent.py:133` 的 `_maybe_attach_link_image(post_id, content)` 与新必填签名暂不匹配——brief 明确照计划留给 Task 13；在此期间 brief 生成路径的后台补图会 TypeError（被 `_worker` 的 try/except 吃掉记日志，不影响主流程落库）。
2. brief 验证脚本自身笔误（`other['id']` 对 int 取下标），已按语义等价修正后跑通，建议修 brief 原文。
3. `check_brief_fallback.py` / `check_usage.py` 两个 untracked gate 未跟上 Task 3 数据层签名（`create_content_brief` 等），在本次与基线上都会红/缺文件——属其他批次或 gate 修复范围，本任务未动（超出「只允许修改 insights_web.py」边界）。
4. `run_all_gates.py` 全量红（66/79）是仓库当前已知状态（账号体系删除遗留），非本任务引入；建议在 verify 阶段统一清点。
