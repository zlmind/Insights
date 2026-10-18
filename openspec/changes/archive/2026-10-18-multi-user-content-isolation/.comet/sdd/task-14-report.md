# Task 14 报告：修复既有 gate 脚本的数据层调用点

Change: multi-user-content-isolation · 批次 F · 2026-10-18
Commit: **4e2b71f** `gate 脚本适配数据层 user 归属签名`（恰 20 文件，450+/249-，作者 l0lsec <51931886+l0lsec@users.noreply.github.com>，无任何 attribution）

## 前置说明

`task-14-brief.md` 未提取（目录中只有 0-13 号）。按 ledger 中 Task 7 的先例（"implementer 从计划原文取用（内容等价）"），本任务以 `docs/superpowers/plans/2026-10-17-multi-user-content-isolation.md` 的 Task 14 原文（L845-866）为 brief 逐字执行：20 文件清单、三条机械规则、Step 1-3 均照办。

## TDD 形态（机械清扫版）

先全量跑 20 文件的 62 个 gate section 取 RED 基线（日志存 `.comet/sdd/logs/`），逐文件修签名后立即单独重跑（修后日志存 `.comet/sdd/logs_after/`）。

## 修改文件清单（恰为计划列出的 20 个）

| 文件 | uid 接入点 | 修的种子调用 |
|---|---|---|
| scripts/check_ig_people.py | rig() 返回 uid（7 处解包）+ section_persist 直取 | 44 处 |
| scripts/check_video.py | schema/instagram/reelopts 直取；routes/fanout/copy 走 `rig.uid` | 28 处 |
| scripts/check_bulk_platform.py | Rig.__init__ 存 self.uid；各 section `db, P, uid = rig.…` | 20 处 |
| scripts/check_queue_groups.py | World 存 self.uid；模块级用 `w.uid` | 51 处 |
| scripts/check_crosspost.py | 4 个 isolated_app 后各取一次 | 16 处 |
| scripts/check_scheduler_accounts.py | 5 个 isolated_app 后各取一次 | 17 处 |
| scripts/check_manual_posts.py | rig() 返回 uid（4 处解包） | 11 处 |
| scripts/check_domestic_publisher.py | main 直取；_seed_post 增 uid 形参 | 6 处 |
| scripts/check_backcompat.py | isolated_app 后直取 | 8 处 |
| scripts/_video_ui_gate.py | `rig.uid`（供 check_video ui 节） | 6 处 |
| scripts/check_accounts_ui.py | isolated_app 后直取 | 5 处 |
| scripts/check_agent_push.py | main 直取 | 4 处 |
| scripts/check_brief_fallback.py | main 直取 | 5 处 |
| scripts/check_accounts_model.py | isolated_app 后直取 | 3 处 |
| scripts/check_compose_paging.py | seeded_app 内直取 | 2 处 |
| scripts/check_agent_ui.py | main 直取 | 2 处 |
| scripts/check_login_without_identity.py | section 1 直取 | 2 处 |
| scripts/check_bulk_platform_ui.py | rig() 内直取 | 2 处 |
| scripts/check_channel_defaults.py | main 直取 | 1 处 |
| scripts/check_compose_js.py | rendered_scripts() 内直取 | 1 处 |

**完整性证明**：AST 遍历（`verify_uid.py`，存本目录）对 20 文件共检出 **218 个作用域函数调用点，0 个缺 user_id**（关键字或位置传参均计入）。`mark_standalone_post_used`、`update_standalone_post_video`、`get_brief_run` 等断言读侧调用也一并补齐。20 文件全部 `py_compile` 通过。

## 每脚本修前/修后失败原因对照表

| 脚本（section） | 修前失败原因（RED） | 修后状态 |
|---|---|---|
| check_brief_fallback | `ValueError: user_id is required for content operations` | **GREEN** BRIEF_FALLBACK_OK |
| check_domestic_publisher | `TypeError: add_standalone_post() missing user_id` | **GREEN** DOMESTIC_PUBLISHER_OK |
| check_agent_push | `TypeError: add_standalone_post() missing user_id` | **GREEN** AGENT_PUSH_OK |
| check_agent_ui | `TypeError: add_standalone_post() missing user_id` | **GREEN** AGENT_UI_OK |
| check_video schema | `TypeError: add_standalone_post() missing user_id` | **GREEN** VIDEO_SCHEMA_OK |
| check_manual_posts video | `TypeError: list_standalone_posts() missing user_id` | **GREEN** MANUAL_POSTS_VIDEO_OK |
| check_manual_posts targets | PASS（纯 node JS 节） | GREEN（不变） |
| check_manual_posts llm | PASS | GREEN（不变） |
| check_video download | PASS（纯下载器节） | GREEN（不变） |
| check_manual_posts route | `TypeError: get_standalone_post() missing user_id` | RED：`FAIL: scheduling a draft-only post must be refused with the way out: None` —— 并行会话**删除了 /schedule/add 路由**（insights_web.py 已无此路由）；签名相关的全部断言（每 target 一行、账号落位、各 refusal 不写行）已在其前通过 |
| check_manual_posts wiring | `TypeError: add_url_source() missing user_id` | RED：`FAIL: the manual pane's video control is missing Reel` —— 并行会话 compose 模板改版移除 Reel 控件；add_url_source 种子已修，源码页断言在其前通过 |
| check_channel_defaults | `TypeError: add_article() missing user_id` | RED：`FAIL: the article channel picker renders` —— 并行会话 39f3d2a「删除无用功能」把 article 页渠道选择器从模板删除 |
| check_ig_people persist | `TypeError: add_scheduled_post() missing user_id` | RED：`AttributeError: database has no attribute 'save_instagram_token'` —— **失败点越过签名处**（迁移夹具的 add_scheduled_post 已过），死在共享 harness connect() 的已删 token 函数 |
| check_ig_people client/none/refuse/reject | `ModuleNotFoundError: instagram_client` | 同左（并行删除的模块，签名点在其后） |
| check_ig_people publish/ui/js | `AttributeError: save_instagram_token` | 同左 |
| check_ig_people queue | `AttributeError: NoneType.get_linkedin_client`（social_publisher 退役） | 同左 |
| check_video router/routes/fanout/copy/ui/newpost | `NoneType.get_linkedin_client` | 同左 |
| check_video linkedin/twitter/threads/facebook | `ModuleNotFoundError: linkedin_client` 等 | 同左 |
| check_video instagram/reelopts | `save_instagram_token` | 同左 |
| check_video compat | `FAIL: a real 2-second clip should be too short…` | 同左（既有断言红，与签名无关，RED 基线即如此） |
| check_bulk_platform ×6 | `NoneType.get_linkedin_client`（harness） | 同左；签名修复为静态验证（AST） |
| check_bulk_platform_ui ×2 / check_compose_js ×3 / check_compose_paging ×4 / check_queue_groups ×7 | 同上 | 同上 |
| check_crosspost / check_scheduler_accounts / check_backcompat / check_accounts_ui | 同上 | 同上 |
| check_accounts_model | `AttributeError: save_linkedin_token` | 同左（connect() 即死，种子点在其后） |
| check_login_without_identity | `AttributeError: get_linkedin_token` | 同左 |

## 全量 gate（计划 Step 2）

`venv/Scripts/python.exe scripts/run_all_gates.py`：**60/79 失败**（本任务前基线约 66/79），gate inventory 干净：`unregistered: []`、`missing: []`（无 unregistered gate 报警）。60 个失败全部为并行会话遗留因（social_publisher 退役、instagram_client/linkedin_client 等 5 个客户端模块删除、save_*_token 等 token 函数删除、article/compose 模板功能删除），无一为 `TypeError … missing user_id / got multiple values`。

## 三个点名脚本的证据

- **check_brief_fallback**：`BRIEF_FALLBACK_OK`（4 处 create_content_brief + get_brief_run 全部按 uid 断言）。修前 `ValueError: user_id is required for content operations`。
- **check_usage**：scoping / pricing / page 三节全绿（USAGE_SCOPING_OK / USAGE_PRICING_OK / USAGE_PAGE_OK），早前任务已修，本任务复核通过；该文件不在 Task 14 的 20 文件清单内，未改动。
- **check_manual_posts**：5 节中 targets/video/llm **GREEN**；route/wiring 两节签名已修（其前全部断言通过），死因是并行会话删除的 /schedule/add 路由与 compose 模板 Reel 控件——非签名原因，符合"修好签名后仍红属预期"。

## 关键改动模式示例

1. 直取（isolated_app 之后）：
   ```python
   directory, database, web, publisher, client = isolated_app()
   uid = database.get_user_by_username('gate')['id']
   ```
2. 种子调用补参（关键词形，位置签名同样适用）：
   ```python
   - database.add_standalone_post("manual", "g", platform, content, db_path=P, account_id=account, **fields)
   + database.add_standalone_post("manual", "g", platform, content, user_id=uid, db_path=P, account_id=account, **fields)
   ```
3. 断言"全表可见"改按 uid：
   ```python
   - before = len(database.list_standalone_posts(db_path=P))
   + before = len(database.list_standalone_posts(user_id=uid, db_path=P))
   ```
4. 复用 rig 的类（check_bulk_platform.Rig / check_queue_groups.World）：`self.uid = self.database.get_user_by_username('gate')['id']`，各 section 经 `rig.uid` / `w.uid` 取用。
5. 语义保持：未删任何断言；`check_ig_people` 迁移夹具库（old.db）上的 add_scheduled_post/get_scheduled_post/list_scheduled_posts 也按同一 uid 传入（夹具行 user_id 一致，暴露新列的断言不变）。

## 范围与遗留

- 只改了 20 个清单文件；未动 database.py / insights_web.py / run_all_gates.py / _accounts_gate.py。
- 精确 `git add` 20 个路径；工作区他人未暂存改动（comet-hook-guard.sh、check_usage.py 等）未纳入。
- **交接给 verify 阶段的事实**：62 个 section 中 9 个 GREEN；其余 53 个红于并行会话的功能删除（约 190 处调用点所在脚本依赖的 social_publisher、5 个平台客户端模块、token 函数、/schedule/add 路由、article 页渠道选择器、compose Reel 控件均已不存在）。这些脚本的签名适配已由 AST 全量核验（218/218），待并行会话的功能去留定局后自然复绿或随其归属处理；"79 全绿"在本任务范围内不可达成（计划 Step 2 的 Expected 前提已并行失效）。
