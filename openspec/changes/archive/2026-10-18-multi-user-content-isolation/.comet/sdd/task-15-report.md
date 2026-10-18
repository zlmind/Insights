# Task 15 报告：check_content_isolation.py gate（schema / data / scan）并入 run_all_gates

- **Status:** DONE_WITH_CONCERNS（功能全部完成并提交；两处与计划文字的偏差，见「偏差」）
- **Commit:** `8edb158` 门禁：内容隔离 schema/数据/静态扫描三 gate 并入总闸
  （`A scripts/check_content_isolation.py`、`M scripts/run_all_gates.py`，恰两文件，作者 `l0lsec <51931886+l0lsec@users.noreply.github.com>`，无 attribution）
- **对应 tasks.md：** 5.1

## 0. 前置说明

`task-15-brief.md` 不存在（`.superpowers/sdd/2026-10-17-multi-user-content-isolation/` 下 brief 只到 task-13）。任务全文取自计划文件
`docs/superpowers/plans/2026-10-17-multi-user-content-isolation.md` 的 `### Task 15` 节（三节设计、白名单、run_all_gates 注册代码均在该节，逐字采用）。

## 1. 交付内容

### `scripts/check_content_isolation.py`（新建，三节各自独立 token）

**schema 节 → `CONTENT_ISOLATION_SCHEMA_OK`**
- 临时库 `init_db` 连跑两遍；12 表（`_CONTENT_TABLES` 11 表 ∪ `content_briefs`）全部有 `user_id` 且无重复列（幂等直接证据）。
- `idx_feeds_url_user` / `idx_episodes_url_user` / `idx_url_sources_url_user` 三个复合唯一索引在位（`PRAGMA index_list`）。
- feeds/episodes/url_sources/**uploaded_images** 四表建表 SQL 均不再含 `url ... UNIQUE`（覆盖评审修复后 uploaded_images 也重建的点）。
- 双用户同 URL 各一行、同用户内仍去重：feeds/episodes/url_sources/uploaded_images 四表逐一验证（走数据层 `add_feed`/`queue_episode`/`add_url_source`/`add_uploaded_image`）。
- 旧 schema 库升级：按 user_id 落地前的建表形状手工造旧库（url TEXT UNIQUE、无 user_id，种子 2 feeds / 1 episode / 1 url_source / 1 uploaded_image）→ `init_db` 后行数逐一不变、schema 断言全过；且升级后的库立即允许第二个用户持有旧库已有 URL（证明全局 UNIQUE 确已移除）；升级库再跑一遍 `init_db` 行数仍不变。

**data 节 → `CONTENT_ISOLATION_DATA_OK`**（`isolated_app()` 三账号：A=gate 首号即 admin、B=colleague、C=root 直 SQL 置 admin）
- 12 张内容表每表至少一条「B 直访 → None」「B 改 → 行不变」（含 False/0 形态：`delete_url_source`/`update_url_source_content`/`delete_uploaded_image`/`delete_generated_thumbnail`/`update_library_prompt`/`delete_library_prompt`/`update_scheduled_post_time`/`cancel_scheduled_post`/`increment_retry_count`/`mark_standalone_post_used`）「B 删 → 行仍在」。
- bulk 只删自己的：feeds/episodes/social_posts/standalone_posts/scheduled_posts 五个 bulk 入口，B 混合传 [A 的 id, B 的 id] → 返回 1，A 的行幸存。
- NULL 归属行：每表种一条 A 的行后 SQL 置 `user_id=NULL`，A/B/C(admin) 三视角经各自 get 函数均不可见，行仍在库且保持无主（`COUNT(*) WHERE id=? AND user_id IS NULL` = 1），并带正对照（A 仍能看到自己未置 NULL 的行）。
- 写入口缺 user_id 抛 ValueError：15 个入口（`_require_user` + 12 表全部写入口，含 `save_episode`/`queue_episode`/`mark_standalone_post_used`/`create_brief_run`）。
- 404 与不存在逐字节一致：B 的 client 访问 A 的 `/article/<id>`、`/sources/<id>` vs 不存在 id，status 404 且 `get_data()` 逐字节相等。

**scan 节 → `CONTENT_ISOLATION_SCAN_OK`**
- `ast.parse` 读 `database.py`；函数级收集 SQL 字面量（含 f-string 字面片段拼回、**剔除 docstring**——docstring 提到 user_id 不算数）。
- 12 张内容表 × 四种模式（`SELECT...FROM` / `UPDATE` / `DELETE FROM` / `INSERT INTO`）：命中且全函数 SQL 文本无 `user_id` → 违例；INSERT 额外校验列清单含 `user_id`（单列分支单独可触发，mutation 3b' 验证）。
- 白名单 7 项（计划 5 项 + 新增 2 项，见偏差 A）；每项必须**仍被命中**（死条目即 fail），且名字必须真实存在于 database.py。

### `scripts/run_all_gates.py`（修改）
- 按计划原文注册 `ISOLATION_GATES`（三条，中文 claim 逐字未动），以 `+ ISOLATION_GATES` 并入 `ALL_GATES` 元组拼接，附英文组注释与既有风格一致。

## 2. TDD 过程（验收型 gate 的 RED 证据）

本任务功能（Task 1–14）已在位，gate 写完即在当前 HEAD 三节全绿——无天然 RED。按要求做 mutation 测试作为 RED 证据，**全部只在临时库/内存进行，未改仓库文件、未触碰 insights.db**（driver 位于系统临时目录 `isolation_mutation.py`，一次性，不入仓库）：

| # | 变异 | 结果 |
|---|------|------|
| 1 | 临时库 `DROP INDEX idx_feeds_url_user` + `ALTER TABLE feeds DROP COLUMN user_id` | RED OK（FAIL: feeds 缺 user_id 列） |
| 2 | 临时库 `DROP INDEX idx_url_sources_url_user` | RED OK（FAIL: 缺复合唯一索引） |
| 3 | 内存把 `database.get_url_source` 换成无 user 过滤的旧形状直查 | RED OK（FAIL: B 直访 A 的 url source 应得 None） |
| 4 | 内存源文本追加无过滤 `SELECT id, url FROM url_sources` 函数 | RED OK（scan 标记 SELECT url_sources） |
| 5 | 内存源文本把 `create_brief_run` 的 INSERT 列清单删掉 user_id | RED OK |
| 6 | 合成源：同函数其余 SQL 带 user_id、唯独 INSERT 列清单缺 | RED OK（命中「列清单缺 user_id」分支） |
| 7 | 内存 pop 白名单 `get_due_content_briefs` | RED OK（既有违例暴露） |

**mutation 结果 7/7 全红。** 之后仓库仅剩本次两文件改动（`git status` 核实），三节复跑全绿。

## 3. 运行输出

三节独立运行（`venv/Scripts/python.exe scripts/check_content_isolation.py {schema|data|scan}`）：

```
迁移幂等：12 表带 user_id，feeds/episodes/url_sources 复合唯一，uploaded_images 一并去 UNIQUE，同 URL 双用户各一行，旧库升级行数不变
CONTENT_ISOLATION_SCHEMA_OK
双账号互不读写删，bulk 只删自己的，NULL 存量对含 admin 的所有视角不可见且行保留，写入口缺 user_id 抛 ValueError，404 与不存在逐字节一致
CONTENT_ISOLATION_DATA_OK
scan: 7 个函数被命中，白名单 7 项全部仍被命中（名单无死条目），12 张内容表 INSERT 均带 user_id 列
CONTENT_ISOLATION_SCAN_OK
```

`run_all_gates.py` 全量：**82 个 gate，60 失败——与注册前的 stash 基线（79 gate，60 失败 + 1 条「新脚本未注册」inventory 问题）失败集合逐名一致**（脚本比对 `identical failure set: True`），我的三条为新增 PASS。计划期望的「全绿」在当前工作区不可达：60 个失败全部源于服务端发布退役（`social_publisher`/`linkedin_client`/`instagram_client`/`twitter_client`/`threads_client`/`facebook_client` 模块已删、`database.save_*_token`/`get_linkedin_token` 已移除——`git show HEAD:database.py` 证实 HEAD 即无这些函数）叠加并行会话未提交改动（记忆条目「并行会话删了 accounts/schedule/social_publisher 致 gate 失效」）。失败原因分布：36× `'NoneType' object has no attribute 'get_linkedin_client'`、6× `no attribute 'save_instagram_token'`、9× `ModuleNotFoundError`（客户端模块）等，无一与内容隔离相关；本任务两文件不进入任何其他 gate 的子进程环境，零新增失败。

## 4. 偏差（计划文字 vs 实现，需评审知悉）

- **A. 白名单 7 项而非计划列的 5 项。** 按计划的 5 项白名单，scan 节对真实 database.py 必然红：`get_next_available_slot` 与 `count_scheduled_posts_for_day` 的 SQL 触及 `scheduled_posts` 且整函数无 user_id。二者是实例级时段分配/平台日限流计算——时段与每日上限是全实例共享资源，必须跨主人计数，只数一个人会让两个用户排进同一格。已在白名单中写明理由；scan 节同时强制「白名单每项必须仍被命中」，防止名单腐化。计划 5 项全部逐一命中、无死条目。
- **B. 计划 Step 5 期望 `run_all_gates.py` 全绿，实测 60 个既有失败。** 见上节：均为平台发布退役与并行会话损坏，不在本任务范围（范围限制禁止触碰他人改动），已用 stash 基线证明失败集合与注册前逐名一致。Task 14 的「全部 PASS」预期在这些模块删除后对 ACCOUNT_GATES 等本就不成立，属 change 级已知问题。

## 5. 其他实现说明

- gate 输出含中文，脚本头部 `sys.stdout.reconfigure(encoding="utf-8")`——`run_gate` 以 UTF-8 解码子进程输出，Windows 默认代码页 GBK 会把中文打成问号。
- f-string 分片假阳性：`f"...IN ({placeholders}) AND user_id = ?"` 的 user_id 在字面片段尾部，按单常量检查会误报 `delete_feeds_bulk`/`get_pending_schedules_for_*`/`list_scheduled_posts` 等真实带过滤的函数——故按函数级 blob 拼接后再匹配，与白名单按函数名的粒度一致；代价是「同函数内一处带 user_id 的 SQL 可掩盖另一处不带」（如 init_db 的遗留数据迁移语句靠 DDL 里的 user_id 通过），这是计划「函数体内 SQL 文本」措辞的固有粒度，已在模块 docstring 写明。
- 临时清理：mutation driver 与分析脚本在系统临时目录，未入库；仓库工作区仅本次两文件改动进入提交，他人未暂存改动未被纳入。
