# Task 16 报告：端到端双账号验收 gate（e2e 节）+ run_all_gates 注册

- **Status:** DONE
- **Commit:** `d85b5b4` 门禁：多用户内容隔离双账号端到端验收
  （`M scripts/check_content_isolation.py`、`M scripts/run_all_gates.py`，恰两文件 +244/−5，作者 `l0lsec <51931886+l0lsec@users.noreply.github.com>`，无 attribution）
- **对应 tasks.md：** 5.2（本 change 最后一个实现任务）

## 1. 交付内容

### `scripts/check_content_isolation.py`（追加 `e2e` 节 → `CONTENT_ISOLATION_E2E_OK`）

- 模块 docstring 三节改四节，补 e2e 说明（含「抓网两处内存打桩、整节不联网」）与 mutation 驱动入口；`SECTIONS` 注册 `"e2e"`；新增 `from datetime import datetime`（场景 5/6 之外，排期种子要用当月日期，见下）。
- `section_e2e()`：`isolated_app()` 双会话（admin 首号 SQL 置 `is_admin=1` + `create_user('e2e_b')` 新账号 B，`session_transaction` 切换 user_id），一次跑完九场景，逐场景 `check()`：
  1. **双账号就绪**：admin 首号（`database` 无 is_admin 参数，直 SQL 置 1 并回读断言）+ B。
  2. **B 五页全空**：admin 先种五枚独特标记（feed/article/standalone post/brief/url source，另挂一条当月 scheduled post 关联 admin 帖）；B 依次 GET `/`、`/articles`、`/compose`、`/briefs`、`/schedule`，五标记一枚不许出现；正对照 admin 的 `/`、`/compose` 可见自己的标记。
  3. **直访 404 同响应体**：B 直访 admin 的 `/article/<id>`、`/sources/<id>`、`/feed/<id>`、`/compose/post/<id>`，与不存在 id（999999999）两两比对——status 均 404 且 `get_data()` 逐字节一致。
  4. **同名 prompt 隔离**：A、B 各建同名 prompt 的 standalone post，`/compose` 各自只见自己的正文标记。
  5. **存量 NULL**：A 种一帖一 feed 后 SQL 置 `user_id=NULL` → admin 与 B 的列表页（`/compose`、`/`）与详情（`/compose/post/<id>` 404）均不可见，直查 `COUNT(*) WHERE id=? AND user_id IS NULL` 确认行仍在库且保持无主。
  6. **worker 产物归属**：admin `GET /enqueue?url=...&feed_id=<admin feed>` → episodes 行 `user_id=admin`；B 借 admin 的 feed_id 入队 → 404；B 入队自己的 feed → 行 `user_id=B`。brief 跑批复用 Task 13 的假 generate 路径（`research_engine.run_research` 返回 `warnings=['no_candidates']` + `insights.generate_posts_from_prompt` 假实现，不联网不跑真模型）→ `run_brief(bid, trigger='scheduled')` 产 1 帖，`user_id == brief 主人 B`，且 admin 视角无该 brief 的孤儿帖。
  7. **/activity /usage 视角**：各 seed 双方各一条（activity 用同一 action 名、不同 path 标记；usage 用不同 model 名标记）；B 带 `?user_id=<admin>`（activity）/`?user_id=<admin>&scope=all`（usage）→ 只见自己；admin `?scope=all` → 见双方。
  8. **同 URL 双用户互不去重**：`/sources` POST 同一 URL（trafilatura 三函数内存打桩）→ 各 200、DB 2 行、双方页面各见自己那行（以 A 单行时的出现次数为基线自校准，A 页计数不得因 B 添加而变化）；`POST /` add_feed 同一 RSS（`web.parse_feed_with_fallback` 内存打桩，无网络）→ 同样断言。
  9. **迁移幂等**：schema 节已钉死，e2e 不重复（docstring 与总结行均注明）。
- 实现备注：八条场景进度行加 `INFO ` 前缀——`run_gate` 会取子进程输出第一条非 token 行当 PASS summary，且本就过滤 `WARNING/INFO/FLASK_SECRET_KEY` 行；加前缀后总闸 summary 显示中文总结行而非「场景 1/9」，直接运行时仍保留逐场景进度。

### `scripts/run_all_gates.py`

- `ISOLATION_GATES` 末尾按 brief 原文追加 e2e 条目（中文 claim 逐字未动）；组注释由「e2e 节稍后并入」改为如实描述四节。

## 2. TDD 过程（验收型 gate）

gate 为验收性质，功能（Task 1–15）已在位，e2e 写完在当前 HEAD 直跑即应绿；RED 证据按任务裁定用 mutation（对内存模块/临时会话做归属破坏，**不改仓库源码、不碰 insights.db**）。

GREEN 过程中修掉两处**我测试自身的断言缺陷**（产品代码零改动）：

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | 场景 7 红：「B 的 /activity 应固定本人视角」 | `zx_e2e_act_a` 出现在 B 页面的**操作筛选词表**（`distinct_activity_actions()` 实例级 datalist）——action 名是应用固定词表不是行数据；正则核对行单元格只有 B 的 action，行过滤本身正确 | 双方 seed 改用同名 action、以只在行里渲染的 path（`/zx-e2e-a`、`/zx-e2e-b`）作断言标记 |
| 2 | 场景 8 红：「B 的 /sources 应恰见自己的那一行」 | 场景 8 共享源标题标记 `ZXE2ESRC page` 与场景 2 admin 种子源的 `ZXE2ESRC` 前缀相撞，A 页计数含两行 | 共享源改用独立标记 `ZXE2ESHAREDSRC` |

修复后 e2e 全绿（九场景全过，`CONTENT_ISOLATION_E2E_OK`）。

**mutation RED（driver 位于系统临时目录 `isolation_e2e_mutation.py`，一次性不入仓库；三档全红）**：

| 档 | 变异（全部内存/临时目录） | 结果 |
|----|--------------------------|------|
| read | `database.get_standalone_post` 换成无 user 过滤的直查（`from database import` 绑定发生在 insights_web 导入时，补丁先行即生效） | RED OK——`FAIL: /compose/post/{} 跨用户与缺失都应 404（200/404）` |
| own | `database.queue_episode` 包一层把归属改写成库内 MIN(id) 首号 | RED OK——`FAIL: B 入队的 episode 应归属 B` |
| scope | `insights_web._usage_scope_user_id` 恒返回 None（非 admin 也放行 scope=all） | RED OK——`FAIL: B 的 /usage scope=all 应退回本人视角` |

恢复（无需恢复——补丁只在子进程内存里）后 e2e 复跑全绿。

## 3. 运行输出

四节独立运行（`venv/Scripts/python.exe scripts/check_content_isolation.py {schema|data|scan|e2e}`）：

```
CONTENT_ISOLATION_SCHEMA_OK
CONTENT_ISOLATION_DATA_OK
CONTENT_ISOLATION_SCAN_OK
INFO e2e 1/9 admin 首号 + 新账号 B 就绪
INFO e2e 2/9 B 五页全空，admin 种子零泄漏（admin 正对照可见）
INFO e2e 3/9 直访他人 article/sources/feed/compose post 均 404 且与不存在同响应体
INFO e2e 4/9 同名 prompt 双账号各见各帖
INFO e2e 5/9 存量 NULL 对双方不可见且行仍在库
INFO e2e 6/9 episode 与 brief 跑批产物均按操作者/brief 主人归属
INFO e2e 7/9 /activity /usage：非 admin 固定本人，admin 全景见双方
INFO e2e 8/9 同一 URL/RSS 双用户各一行、各见各行
双账号端到端：新账号全页空、跨用户 404 同响应、同名 prompt 各见各帖、存量 NULL 不可见仍在库、episode/brief 产物按主人归属、/activity /usage 视角正确、同 URL 各行各见（迁移幂等在 schema 节）
CONTENT_ISOLATION_E2E_OK
```

**全量验证（基线裁定执行）**：改动前先跑基线（82 gate / 60 失败），最终代码状态再跑全量：**83 gate（+1 即本 e2e），23 PASS / 60 FAIL，失败清单与基线 `diff` 逐字节为空——零新增失败**。四隔离 gate 全 PASS：

```
[PASS] 迁移幂等：12 表带 user_id，feeds/episodes/url_sources 复合唯一，同 URL 双用户各一行  (0.7s)
[PASS] 双账号互不可见，NULL 存量对含 admin 的所有人不可见，bulk 与写入口均不越权  (1.2s)
[PASS] database.py 内容表 SELECT 带 user 过滤、INSERT 带归属列（白名单除外）  (0.3s)
[PASS] 双账号端到端：新账号全页空、直访他人 404 同响应、存量 NULL 不可见、worker 产物归属、/activity /usage 视角  (1.7s)
```

`ALL_GATES_OK` 按控制器裁定不强求：60 个失败与 Task 15 报告的基线同源（服务端发布退役 `social_publisher`/平台客户端模块删除 + 并行会话遗留），与本 change 无关，本任务两文件不进入其他 gate 的子进程环境。

## 4. 九场景逐场景结果

| # | 场景 | 结果 |
|---|------|------|
| 1 | admin 首号 + 新账号 B | PASS（is_admin=1 回读断言 + create_user） |
| 2 | B 五页全空（`/`、`/articles`、`/compose`、`/briefs`、`/schedule`） | PASS（五标记零泄漏；admin 正对照可见） |
| 3 | 直访 404 同响应体（article/sources/feed/compose post × 4） | PASS（404 且与不存在 id `get_data()` 逐字节相等） |
| 4 | 双账号同名 prompt 隔离 | PASS（/compose 各见各帖） |
| 5 | 存量 NULL 不可见且行仍在 | PASS（双方列表+详情均不可见；COUNT 确认无主行仍在） |
| 6 | worker 产物归属（episode + brief 跑批） | PASS（episode 归入队者；跨用户 feed_id 404；brief 产物归 brief 主人、无孤儿） |
| 7 | /activity /usage 视角 | PASS（B 参数被忽略只见自己；admin 全景见双方） |
| 8 | 同 URL 双用户互不去重（sources POST + add_feed） | PASS（各 1 行、各见各行、A 页计数不变） |
| 9 | 迁移幂等 | 由 schema 节钉死（Task 15），e2e 不重复 |

## 5. 观察（不属本任务修复范围）

- `/activity` 页的操作筛选词表（`distinct_activity_actions()`）是实例级的：B 的页面下拉里会出现全实例出现过的 action 名。action 名来自应用固定词表（login/delete 等），不含用户内容，活动**行**本身过滤正确（场景 7 以行内 path 断言钉住）；记录备查，不改（加 user 过滤属行为变更，超出本 change 最小实现）。

## 6. 范围与清洁自查

- 提交恰两文件（`git show --stat` 核对）；工作区他人未暂存改动（`.claude/skills/comet/...`、未跟踪脚本等）未纳入。
- mutation 与全部 gate 均在临时目录/内存运行，真实 `insights.db` 未被触碰（`git status` 无 insights.db 变更）。
- mutation driver 与临时输出在系统临时目录，未入库。
