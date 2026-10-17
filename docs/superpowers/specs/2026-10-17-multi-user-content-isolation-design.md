---
comet_change: multi-user-content-isolation
role: technical-design
canonical_spec: openspec
---

# 多用户内容隔离 — 技术设计

- Date: 2026-10-17
- Change: multi-user-content-isolation
- Canonical spec: openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md
- 检查点: openspec/changes/multi-user-content-isolation/.comet/handoff/brainstorm-summary.md（用户已确认）

## 1. 意图与背景

Insights 已有认证体系（`users` 表、session 登录门槛、`current_user()`、`PUBLIC_ENDPOINTS` 白名单），但业务数据完全共享：任何注册账号登录后都能看到全部文章、素材、brief、定时任务。本设计把内容域数据按登录用户隔离——每个用户只看到自己的内容。

认证链路现状（已代码核实）：

- `session['user_id']` → `current_user()`（缓存于 flask.g），全站登录门槛已生效
- `activity_log` / `usage_events` 已带 `user_id`；`content_briefs` 有列但查询不过滤
- 其余内容表完全无归属概念；`database.py` 全部函数以 `db_path` 为唯一作用域参数
- `episodes` 以全局 URL 唯一键去重（`queue_episode` 的 `INSERT OR IGNORE` 依赖该唯一索引）
- recent prompts 非独立表，派生自 `standalone_posts`（`source_type='freeform'`）
- SQLite 单文件，启动时 in-place 迁移是既有惯例（`PRAGMA table_info` → `ALTER TABLE`）
- 已知安全缺口：`/activity` 无 admin 门控且接受任意 `user_id` 筛选（本设计收口）

## 2. 目标 / 非目标 / 反目标

**目标**

- 内容域数据严格按登录用户隔离：列表只见自己的，单资源直访他人的返回 404。
- 后台生成/跑批的内容归属触发它的用户。
- 存量数据不迁移：NULL 归属对所有人不可见，数据保留可手工划归。
- admin 在内容页面与其他用户完全同权（纯隔离）；仅用量/活动页保留「全部」视角。

**非目标**

- 不隔离 social_accounts、扩展配对、登录态（change ② `multi-user-publishing-channels`）。
- 不拆分调度时段/每日限额（保持全局）。
- 不做用户管理页（change ③ `admin-user-management`）。
- 不引入 ORM、不改数据库；保持 raw sqlite3。
- 不改登录/注册/会话机制。

**反目标（出现即失败）**

- 任何用户在任何列表/接口看到他人的内容行。
- 跨用户探测能区分「存在但无权」与「不存在」。
- 系统产生 NULL 归属的**新**数据。

## 3. 约束

- 保持 raw sqlite3 单文件；迁移 = 启动时幂等 in-place。
- 全局 `AGENT_TOKEN` 语义与扩展配对协议本 change 不动（change ② 承载）。
- 调度时段/每日限额保持全局共享。
- 存量数据不迁移（用户 2026-10-17 改定）。

## 4. 决策归属

**用户已定（不可改）**

1. 全量内容隔离（12 表 + episodes 复合键）；平台账号/扩展/登录态不隔离。
2. 存量数据不迁移：NULL 归属对所有人（含 admin）不可见；数据保留，将来可手工 SQL 划归或清理。
3. 同源多用户互不相干：两个用户订阅同一 RSS = 各自独立行，重复抓取可接受。
4. admin 纯隔离：内容页同权；仅用量/活动页有「全部」视角。
5. 删除用户连数据删（change ③ 承载，本 change 的表清单即其删除范围依据）。

**留给实现者（实现细节裁量）**

- 哪些大表补 `user_id` 索引（建议：articles、episodes、standalone_posts 等高频查询表）。
- 数据层函数签名的具体形态（建议 `user_id` 必填位置参数，而非可选默认值）。
- gate 脚本的扫描实现方式（AST 或正则均可，能兜住回归即可）。

## 5. 技术方案

### 5.1 数据模型

11 张表加可空 `user_id` 列（`content_briefs` 列已存在，共 12 表覆盖）：

| 表 | 内容 |
|----|------|
| feeds | RSS 订阅源 |
| episodes | 抓取条目队列 |
| articles | 生成的文章 |
| social_posts | 平台帖子 |
| standalone_posts | 发布工作台帖子（recent prompts 的数据源） |
| url_sources | URL 源 |
| uploaded_images | 上传图片 |
| generated_thumbnails | 生成封面 |
| prompt_library | prompt 库 |
| content_brief_runs | brief 跑批记录 |
| scheduled_posts | 定时发布 |

列定义统一：`user_id INTEGER REFERENCES users(id)`（可空，默认 NULL）。启动时幂等加列：`PRAGMA table_info` 检测 → `ALTER TABLE ADD COLUMN`。

**episodes 唯一键变更**：现有 url 全局唯一索引改为 `(url, user_id)` 复合唯一索引。迁移内先探测旧索引名并 drop，再建复合索引。SQLite 唯一索引中 NULL 互不相等，NULL 旧行不会相互冲突；两个用户订阅同一 URL 各持一行，互不去重。

**recent prompts** 不是独立表——派生自 `standalone_posts`（`source_type='freeform'`），帖子隔离后自动隔离，无需单独处理。

### 5.2 数据层作用域（第一道防线）

纵深防御：作用域下沉到 `database.py` 函数签名，路由层传参是第二道防线，任一层遗漏都不会单独造成泄漏。

- `list_*`：`user_id` 必填参数，`WHERE user_id = ?`。
- `get_* / update_* / delete_*`：归属校验，不匹配按「不存在」处理（返回 None/False，由路由层转 404）。
- **写入口强制归属**：create/import/enqueue/save 签名要求 `user_id`，缺失抛异常——不静默容忍 NULL，杜绝后台产孤儿。
- `content_briefs` / `content_brief_runs`：现有查询补 user 过滤。
- bulk 操作（批量删除/更新）必须限定 `user_id`，禁止跨用户批处理。
- 上传/生成文件留在 `static/uploads/`（UUID 文件名），归属以 DB 行为准，不做文件搬移。

### 5.3 Web 层

- 内容路由（约 60 条）从 `current_user()` 取归属传给数据层；列表天然只见自己。
- 跨用户单资源访问 → **404**，不用 403（不泄露存在性）。
- `PUBLIC_ENDPOINTS` 与登录门槛不变。

### 5.4 后台归属

- 归属在**入队/落库时固化**，worker 只按行上归属行事，绝不在线程里恢复「当前用户」。
- feed 轮询（含 proactive worker）：episode 归属 feed 的 `user_id`。
- url_sources 产 episode（`feed_id=None` 路径）：归属 url_source 行的 `user_id`。
- content_agent worker：按 episode/brief 行上归属干活，产物（drafts/articles）写归属；用量归属沿用 `brief["user_id"]` 链路。
- brief 定时跑批：查询过滤补齐，产物归属 brief 主人。

### 5.5 用量/活动页

- 非 admin：固定自己视角，忽略任何 `user_id`/`scope` 参数（收口 `/activity` 现状缺口）。
- admin：可选「全部」（scope=all）或按用户筛选。
- usage/activity 历史记录不受加列影响（本就带 `user_id`）。

## 6. 边界与条件

- **NULL 孤儿行**：任何 list/get/update/delete 均不可见（含 admin）；写入口不允许再产生。
- **重复订阅**：A、B 订阅同一 feed → 两条 feeds、各自的 episodes；同 URL 各一行（复合唯一键允许）。
- **删除用户**：change ③ 按本 change 表清单级联 `DELETE WHERE user_id = ?`。
- **回滚**：还原代码 + 还原部署前备份的 `insights.db`（加列不做在线回退，以备份为准）。

## 7. 测试策略

**gate 脚本**（并入 `run_all_gates.py`）：

- 扫描 `database.py` 内容表的 `SELECT` 是否带 `user_id` 过滤。
- 扫描内容域 `INSERT` 是否含 `user_id` 列。

**Flask test client 双账号 E2E**：

- 注册 admin + 新账号；新账号全页空列表（sources/articles/compose/briefs/schedule）。
- 新账号直访 admin 资源 URL → 404；双账号各建数据互不可见。
- 入队 episode → worker 产物归属触发者；brief 跑批产物归属 brief 主人。
- 存量 NULL 数据对双方均不可见。
- `/activity`、`/usage`：非 admin 忽略 `user_id`/`scope` 参数；admin「全部」可见。
- 同 URL 双用户重入队不互相去重；迁移连续两次启动不报错、不加重复列。

## 8. 风险与兜底

| 风险 | 兜底 |
|------|------|
| 查询路径遗漏 → 泄漏 | 按表分批提交 + gate 脚本扫描 SELECT |
| worker 丢归属 → NULL 孤儿 | 写入口签名强制 + gate 扫描 INSERT |
| episodes 索引切换踩坏去重 | 迁移内显式 drop 旧索引 + E2E 覆盖同 URL 重入队 |
| 签名改动量大难审查 | tasks 按表分批，每批一个 commit |

## 9. 交付物

- `database.py`：幂等加列迁移 + episodes 复合索引 + 数据层作用域。
- `insights_web.py`：路由传 `current_user()`、跨用户 404、`/activity` 与 usage 的 admin 视角收口。
- `content_agent.py` / `insights.py`：worker 按行上归属执行与落库。
- `scripts/check_content_isolation.py`（gate，并入 `run_all_gates.py`）。
- delta spec：`openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md`。
