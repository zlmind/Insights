# Task 7 报告 — Web 层：源与文章路由按 current_user 过滤与校验

- Change: multi-user-content-isolation（批次 C 第 3 个）
- Commit: **a59c1a6** `Web 层：源/文章/episode 路由按 current_user 过滤，跨用户 404`（恰 1 文件 `insights_web.py`，+109/−78，作者 l0lsec no-reply，无 attribution）
- 修复 Commit: **761849d** `评审修复：create_article/backfill 补归属接线，uploaded_images 去全局 UNIQUE`（恰 2 文件 `insights_web.py` + `database.py`，+40/−19）——见文末「批次 C 审查修复」节
- 计划锚点: docs/superpowers/plans/2026-10-17-multi-user-content-isolation.md Task 7（行号锚点实际 off-by-one：brief 写 index 1752 实为 1753 等，按函数名定位，无遗漏）

## brief 文件缺失说明

派发时给出的 `D:\git3\Insights\.superpowers\sdd\2026-10-17-multi-user-content-isolation\task-7-brief.md` 不存在（该目录只有 task-0…task-6）。从计划原文 `### Task 7` 全文逐字取用（含 404 语义规则、双账号验证脚本、commit message 原文），`subagent-progress.md` 确认 Task 7 为当前任务。流程照常，非阻塞。

## TDD 过程

### RED（先跑 brief Step 2 双账号验证脚本）

命令：

```bash
venv/Scripts/python.exe -c "<brief Step 2 脚本原文>"
```

失败摘要（特征性失败，签名断裂）：

```
File "D:\git3\Insights\insights_web.py", line 1905, in view_feed
    feed = get_feed_by_id(feed_id)
TypeError: get_feed_by_id() missing 1 required positional argument: 'user_id'
AssertionError: ('feed status', 500)
```

（Task 2-6 数据层收窄后，旧调用点 500——正是本任务要接线的断裂面。）

### GREEN

逐路由改造（20 个路由 + 1 个辅助函数透传），同一脚本输出：

```
WEB_FEEDS_ARTICLES_404_OK
```

提交后复跑（a59c1a6 状态）再次 `WEB_FEEDS_ARTICLES_404_OK` + `import insights_web` 冒烟 `IMPORT_OK`。

### 扩展回归（自我加测，覆盖 brief 脚本未触达的改动面）

`EXTENDED_WEB_CHECKS_OK`，断言面：

- owner 视角：`/feed/<id>`、`/article/<id>`、`/articles`、`/status`、`/feed/<id>/refresh`、`/feeds/refresh-all`、`/article/<id>/edit`、`/episode/<id>/reprocess`、`/episode/<id>/delete`、`/feeds/bulk-delete`、`/episodes/bulk-delete` 全部 200/302。
- 缺参语义保留原 redirect（≠404）：`/enqueue`、`/process_text`、`/process` 缺参均 302。
- 404 不泄露存在性（body 逐字节相等）：view_feed、refresh_feed、reprocess_episode、delete_episode 四条 HTML 路由，他人 id vs 999999 同 body。
- JSON 路由 404 文案一致：`refine_article_with_ai` 他人文章 vs 不存在文章响应体逐字节相等（既有 `{"error": "Article not found"}` 原样保留）。
- `process_text`/`process` 给他人 feed_id → 404（归属校验在网络抓取之前发生）。
- 他人视角 `/status` 不见本人 feed 标题。

注：中途两次断言失败均为我验证脚本自身笔误（`/refresh-all` 应为 `/feeds/refresh-all`；refine 应 POST 不应 GET），产品代码无关，修正后全绿。

## 逐路由落法（与 brief 对照）

| 路由 | 落法 |
|---|---|
| `index` | `uid = current_user()['id']`；POST 三分支 `add_feed(rss_url, title, uid)`、`update_feed_metadata(..., uid)`、YouTube video `add_url_source(..., user_id=uid)`；GET `list_feeds(uid)` |
| `remove_feed` / `bulk_delete_feeds` | `delete_feed(feed_id, uid)` / `delete_feeds_bulk(feed_ids, uid)`（数据层已按 uid 限定，他人 id 为 no-op，不泄露存在性） |
| `refresh_feed` | `get_feed_by_id(feed_id, uid)`，None → `abort(404)`（原为静默 redirect，按 brief 改 404） |
| `refresh_all_feeds` | `list_feeds(uid)`；refresh_worker 闭包捕获 uid 沿用 |
| `view_feed` | `get_feed_by_id(feed_id, uid)` None → `abort(404)`；`refresh_feed_metadata(..., uid)`；循环内 `get_episode(url, uid)` |
| `enqueue_episode` | 缺参 redirect 保留；`get_feed_by_id(feed_id, uid)` None → `abort(404)`；`queue_episode(..., uid)`；task_queue item 不变 |
| `process_text_article` / `process_episode` | 缺参 redirect 保留；feed_id 给定时 `get_feed_by_id(feed_id, uid)` None → `abort(404)`；`get_episode(url, uid)`；`list_articles(eid, uid)`；`save_episode(..., user_id=uid)` |
| `status_page` | `list_all_episodes(order_by=order_by, user_id=uid)`、`list_feeds(uid)` |
| `reprocess_episode` | `get_episode_by_id(id, uid)` None → `abort(404)`（原 redirect，按 brief 改 404）；`reset_episode_for_reprocess(id, uid)` |
| `delete_episode` | 同上 404 后 `delete_episode_by_id(id, uid)` |
| `bulk_delete_episodes` | `delete_episodes_bulk(episode_ids, uid)` |
| `create_article` | 缺参 redirect 保留；episode 归属校验 None → `abort(404)`；`add_article(..., user_id=uid)` |
| `view_article` / `edit_article` | `get_article(id, uid)` None → `abort(404)`；`update_article(id, uid, ...)` |
| `remove_article` | `get_article(id, uid)` 404 后 `delete_social_posts_for_article(id, uid)` + `delete_article(id, uid)`（按 brief 原文，二者均带 uid；数据层 `delete_article` 自身已级联 social_posts，前者按 brief 保留为显式防御） |
| `bulk_replace_posts` | `bulk_replace_post_content(..., user_id=uid, ...)`（JSON 路由，错误文案不变） |
| `refine_article_with_ai` | `get_article(id, uid)`；既有 `{"error": "Article not found"}`-404 文案原样（他人与不存在同文案）；`update_article(id, uid, content=...)` |
| `view_articles` | `list_articles(None, uid)` |

## 偏差与说明（reviewer 关注点）

1. **`refresh_feed_metadata` 加了 `user_id: int` 参数**（brief 路由清单之外）：brief 列出的 `index` POST、`refresh_feed`、`refresh_all_feeds`、`view_feed` 全部经由它写 `update_feed_metadata`（Task 2 已收窄为必填 uid），不透传则这四条路由无法工作。与计划中 `_save_post_for_accounts`（Task 9）、`_maybe_attach_link_image`（Task 13）加参先例同款。grep 确认全部 4 个调用点均已带 uid，无遗留断裂。
2. **`create_article` 用 `get_episode(episode_url, uid)` 而非 brief 字面的 `get_episode_by_id(eid, uid)`**：表单字段实际是 `episode_url`（无 eid），按同一归属校验语义落在真实数据通路上；行号锚点漂移按函数语义处理，与「缺参 redirect / 资源不归属 404」两分规则一致。
3. **`process_text_article`/`process_episode` 的 feed 分支由「查到才用」改为「给定 feed_id 查不到即 `abort(404)`」**：brief 明文「仅在 feed_id 给定时校验归属」；副作用是 feed_id 指向已删除 feed 的旧链接从「静默继续」变为 404，属 brief 要求的语义收紧。
4. **`remove_article` 保留 brief 的双调用**：`delete_article`（Task 3 起）已内部按归属级联删 social_posts，`delete_social_posts_for_article` 先行调用为幂等冗余；按 brief 原文保留（可作 review 裁量点，删之不影响行为）。
5. 登录墙保证：`_auth_and_timer` before_request 对非 PUBLIC_ENDPOINTS 保证 `current_user()` 非 None，故全部路由直接 `current_user()['id']` 无需判 None（与 brief 的 `uid = current_user()['id']` 模式一致）。

## 全量 gate 与基线

`venv/Scripts/python.exe scripts/run_all_gates.py`：**67/79 失败**（Task 2 时记录基线 60/79）。硬证据排除本任务引入恶化：`git stash` 收起本次改动后在 HEAD 版上重跑两个代表性失败 gate——`check_token_isolation.py`（`module 'database' has no attribute 'get_linkedin_token'`）与 `check_compose_paging.py route`（`social_publisher` 已删除 → `publisher` None）——**同样失败**。失败集全部落在 accounts/compose 面（并行会话删除 social_publisher/accounts 功能的已提交遗留 + Task 8/9 未做的 compose 调用点断裂），无一覆盖本任务改动的 feed/article/episode 路由（该面无既有专门 gate，清扫属 Task 14/15 计划内）。Global Constraint 已声明的中间红态，非回归。

## 范围与纪律

- 仅改 `insights_web.py`；`git add insights_web.py` 精确暂存，staged 清单核验恰 1 文件。
- 工作区他人未暂存改动（`.claude/skills/comet/scripts/comet-hook-guard.sh` + 若干 untracked）未纳入提交。
- commit 作者 l0lsec <51931886+l0lsec@users.noreply.github.com>，无任何 attribution trailer。
- 未触碰 Task 1-6 的 database.py、compose/schedule/usage 路由（Task 8-11）、worker（Task 12/13）。
- 环境事件：Task 2 进度记录的 5001 端口 dev Flask flapping 依旧预期（构建期中间态），verify 前需提醒 owner 恢复。

## 批次 C 审查修复（round 1，commit 761849d）

协调者裁定：2 Important + 1 已裁定 schema 修复 + 1 Minor。全部按 TDD（先 RED 特征演示，再修，再 GREEN）。

### RED 证据（修复前）

| 项 | 失败特征 |
|---|---|
| Important 1 | POST `/generate_article`（播客 episode 带 feed_id）→ 500，`insights_web.py:2582` `TypeError: get_feed_by_id() missing 1 required positional argument: 'user_id'` |
| Important 2 | 对存在 url_source 的用户直调 `_backfill_youtube_source('http://src','T','S')` → `TypeError: get_url_source_by_url() missing 1 required positional argument: 'user_id'` |
| Schema | 双用户 `add_uploaded_image` 同一 URL → `IntegrityError: UNIQUE constraint failed: uploaded_images.url` |

（generate_article 以 `web.generate_article = lambda **k: 'AI TEXT'` stub 隔离 OpenAI 网络；RED 阶段 TypeError 发生在 stub 调用之前，演示有效。）

### 修复内容

1. **Important 1**（`insights_web.py` create_article）：`feed = get_feed_by_id(episode['feed_id'], uid) if episode['feed_id'] else None`——episode 归属已验（同路由前文 `get_episode(episode_url, uid)` 404），feed 与 episode 同主。
2. **Important 2**（`_backfill_youtube_source`）：签名加 `user_id: int` 必参（末位），内部 `get_url_source_by_url(url, user_id)`、`update_url_source_content(source['id'], user_id, ...)`。调用点两处：`process_episode` 传 `uid`；`worker` 传 `item['user_id']`（grep 确认 `task_queue.put` 全部三个来源——enqueue_episode、reprocess_episode、YouTube 转写路由——均已带 `user_id` 键；Task 12 将再验证该链路）。
3. **Schema（database.py，控制器已裁定）**：① `uploaded_images` CREATE TABLE `url TEXT UNIQUE` → `url TEXT`，表注释与 `add_uploaded_image` 内注释同步改为按 `(url, user_id)` 去重语义；② 迁移重建清单 `("feeds", "episodes", "url_sources")` → 加入 `"uploaded_images"`（同一 `url\s+TEXT\s+UNIQUE` 正则判据命中旧表即走 12 步重建）；③ `_NEW_DDL["uploaded_images"]` / `_COPY_COLS["uploaded_images"]` 按新 CREATE TABLE 逐列补齐。未加 `(url, user_id)` 复合唯一索引——控制器三步修法未含，去重语义由 `add_uploaded_image` 的应用层回查承担（与 feeds 等表的并发兜底强度差异已录，如需对齐可后续补索引）。
4. **Minor 3**：`refine_article_with_ai`、`bulk_delete_feeds`、`bulk_delete_episodes`、`bulk_replace_posts` 四处统一为路由顶部 `uid = current_user()['id']`，消除内联重复取值。

### GREEN 证据（修复后）

```
REVIEW_FIX_CHECKS_OK        # 三项修复：create_article 播客路径 stub 走通（302 → 文章落库）；
                            # _backfill 直调不抛且 content/description 落库；
                            # 双用户同 URL 素材各得一行、各自 list 只见自己
MIGRATION_REBUILD_OK        # 旧式表（url TEXT UNIQUE + 无 user_id）经 init_db：
                            # UNIQUE 消失、旧行保留（user_id NULL 不归属任何人）、
                            # user_id/media_type 列齐、重建后双用户同 URL 各得一行
WEB_FEEDS_ARTICLES_404_OK   # 原 Task 7 GREEN 脚本复跑
EXTENDED_WEB_CHECKS_OK      # Task 7 扩展回归复跑
IMPORT_OK                   # 冒烟 import database, insights_web
```

### 提交

- `git add insights_web.py database.py`（staged 清单核验恰 2 文件），commit **761849d**，作者 l0lsec no-reply，无 attribution，无 git add -A。
- 验证脚本中的两次笔误（用 NULL 旧 user_id 调 add_uploaded_image、u1 期望行数写成 2）均为脚本自身错误，已修正——产品修复行为一次通过。
