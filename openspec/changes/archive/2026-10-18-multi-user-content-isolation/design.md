# Design: multi-user-content-isolation

## Context

认证层已就绪（`users` 表、session 门槛、`current_user()`），`activity_log`/`usage_events` 已带 `user_id`，`content_briefs` 有列但查询不过滤。其余内容表完全无归属概念；`database.py` 全部函数以 `db_path` 为唯一作用域参数，Web 层路由直接按 ID 读写。SQLite 单文件、启动时 in-place 迁移是既有惯例（`PRAGMA table_info` → `ALTER TABLE`）。

## Goals / Non-Goals

**Goals:**

- 内容域数据严格按登录用户隔离；列表只见自己的，单资源直访他人的返回 404。
- 后台生成/跑批的内容归属到触发它的用户。
- 存量数据不迁移：旧数据 NULL 归属对所有用户（含 admin）不可见，视为不可见孤儿而非公共数据；数据保留在库中，可手工 SQL 划归。
- admin 在内容页面与其他用户完全同权（纯隔离）；仅用量/活动页保留「全部」视角。

**Non-Goals:**

- 不隔离 social_accounts、扩展配对、登录态（后续 change `multi-user-publishing-channels`）。
- 不拆分调度时段/每日限额（保持全局）。
- 不做用户管理页（后续 change `admin-user-management`）。
- 不引入 ORM 或改用其他数据库；保持 raw sqlite3。

## Decisions

1. **归属列统一命名 `user_id`**，与 `content_briefs`/`usage_events` 现状一致，可空（FK `SET NULL`）。不用 `owner_id` 等新命名，避免同库两套词汇。
2. **作用域下沉到数据层函数签名**：`list_*` 增加 `user_id` 必填过滤；`get_*/update_*/delete_*` 增加归属校验，不匹配时按"不存在"处理。路由层仍需传参校验，但不依赖路由层作为唯一防线（避免遗漏某条路由即泄漏）。
3. **迁移 = 启动时 in-place 加列，无回填、无标志位**（2026-10-17 用户改定）。加列沿用现有 `PRAGMA table_info` 检测模式；episodes 的 url 全局唯一索引改为 `(url, user_id)` 复合唯一（先探测 drop 旧索引）。
4. **后台 worker 归属在入队时固化**：episode/content 行创建时写入触发用户的 `user_id`，worker 只按行上归属行事；brief 跑批沿用现有 `brief["user_id"]` 归属（补上查询过滤）。不尝试在 worker 线程里恢复"当前用户"上下文。
5. **跨用户访问一律 404**，不用 403——不向探测者泄露资源存在性。
6. **上传文件不迁移目录结构**：继续统一存 `static/uploads/`（UUID 文件名），归属以 DB 行为准。避免文件搬移和 URL 断链。
7. **「全部」视角收口**：usage/activity 的 scope=all 仅 admin 可选；非 admin 请求带该参数时按"我的"处理。

## Risks / Trade-offs

- [查询路径遗漏导致数据泄漏] → 任务清单按表逐张覆盖；收尾增加一个 gate 脚本扫描 `database.py` 中内容表的 `SELECT` 是否带 `user_id` 过滤，并入 `run_all_gates.py`。
- [后台 worker 忘记带归属，产出 NULL 孤儿数据] → 入队函数（唯一写入口）强制要求 `user_id`；gate 脚本同时检查 `INSERT` 是否含 `user_id` 列。
- [episodes 索引切换影响 INSERT OR IGNORE 去重] → 迁移内显式 drop 旧 url 唯一索引再建 (url, user_id) 复合唯一；E2E 覆盖同 URL 重入队。
- [签名改动量大，一次性 PR 难以审查] → tasks 按表分批提交，每批一个 commit。

## Migration Plan

1. 部署前手动备份 `insights.db`（回滚 = 还原备份）。
2. 启动时自动加列（幂等），不做回填。
3. 存量行 `user_id` 保持 NULL，对所有用户不可见（按不存在处理）；数据保留在库中，可手工 SQL 划归或清理。

## Open Questions

- 无（「全部」视角、存量不迁移均已由用户确认）。
