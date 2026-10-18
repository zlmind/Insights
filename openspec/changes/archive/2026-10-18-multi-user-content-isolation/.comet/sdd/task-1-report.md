# Task 1 报告：迁移——11 表加 user_id 列 + 三表重建为 (url, user_id) 复合唯一

**Status: DONE**

- 提交：`8169f54` 迁移：内容域 11 表加可空 user_id 列，feeds/episodes/url_sources 重建为 (url,user_id) 复合唯一（仅 database.py，+124/−7；基线 6ed6c27；message 为计划原文，无任何 attribution）
- TDD：RED 已观看失败 → 实现 → GREEN 通过
- 临时库全部用 tempfile，未触碰 insights.db；无任何 `UPDATE ... SET user_id` 回填语句

---

## 一、实现内容（database.py，7 处编辑）

1. 顶部导入加 `import re`（迁移正则用）。
2. 模块级常量 `_NEW_DDL`（三张表的完整重建 DDL，`<t>_new` 临时名，列与 init_db 的 CREATE TABLE 逐列一致，url 无 UNIQUE、含 user_id）与 `_COPY_COLS`（显式列清单，不 `SELECT *`）。
3. `feeds` CREATE TABLE：`url TEXT UNIQUE` → `url TEXT`，加 `user_id INTEGER REFERENCES users(id)`。
4. `episodes` CREATE TABLE：同上，保留 `FOREIGN KEY(feed_id) REFERENCES feeds(id)`。
5. `url_sources` CREATE TABLE：同上。
6. `uploaded_images` CREATE TABLE：只加 `user_id` 列；**`url TEXT UNIQUE` 有意保留**（UUID 文件名天然唯一，`add_uploaded_image` 靠它做库内去重），并加注释说明。
7. init_db 末尾 app_settings 之前追加迁移块：
   - `_CONTENT_TABLES`（11 表）按 PRAGMA 判重后 `ALTER TABLE ... ADD COLUMN user_id INTEGER REFERENCES users(id)`；
   - feeds/episodes/url_sources 按 sqlite_master 中 `url\s+TEXT\s+UNIQUE` 正则判跳，命中则按**官方 12 步顺序**重建：`DROP TABLE IF EXISTS <t>_new` → `CREATE <t>_new`（`_NEW_DDL`）→ `INSERT INTO <t>_new SELECT <_COPY_COLS> FROM <t>` → `DROP TABLE <t>` → `RENAME <t>_new → <t>`；
   - 其后六个索引：`idx_feeds_url_user`、`idx_episodes_url_user`、`idx_url_sources_url_user`（复合唯一 `(url, user_id)`）+ `idx_articles_user`、`idx_standalone_posts_user`、`idx_episodes_user`（普通），全部 `IF NOT EXISTS`，放在重建块之后保证旧库一次 init_db 即带上。

幂等：加列按 PRAGMA 判重、重建按正则判跳（重建一次后 sqlite_master 不再命中）、索引 IF NOT EXISTS；双跑安全。整个迁移与 init_db 其余语句同事务，中途失败整体回滚。

## 二、TDD Evidence

### RED（实现前，已观看失败）

命令：brief Step 3 断言脚本原样执行（`venv/Scripts/python.exe -c "..."`，临时库 tempfile）。
失败摘要：

```
Traceback (most recent call last):
  File "<string>", line 8, in <module>
AssertionError: feeds
```

失败原因正确：迁移未实现，第一个断言命中的缺失表即 feeds（user_id 列不存在）。

### GREEN（实现后，同一脚本）

命令：与 RED 完全相同。
通过摘要：输出 `MIGRATION_OK`，无 IntegrityError。覆盖：12 张内容表均有 user_id；三表表级 sql 无 UNIQUE 且复合唯一索引在位；users 两行下 feeds/episodes 各插入同 URL 两行（user_id 1/2）成功、COUNT==2。

## 三、Step 3/4/5 验证输出

- **Step 3（双跑幂等 + schema 断言）**：`MIGRATION_OK`（见上）。
- **Step 4（存量库升级，脚本断言全部通过，输出 `LEGACY_UPGRADE_OK`）**：用旧 DDL（`feeds (id, url TEXT UNIQUE, title)` + 一行）建库跑 init_db 后——旧行保留（COUNT==1，`id=1/url/title` 不变、`user_id` 为 NULL，无回填）；user_id 列在位；表级 sql 无 UNIQUE；`idx_feeds_url_user` 在位；episodes 的 `REFERENCES feeds(id)` 与 articles 的 `REFERENCES episodes(id)` 未被 RENAME 改写、无 `*_legacy` 悬挂；迁移库与新库三表列结构（名+类型序）逐一相等；AUTOINCREMENT 序列保留（迁移后同 URL 不同 user 再插 id 续排为 2）。
- **Step 5（冒烟）**：`venv/Scripts/python.exe -c "import database, insights_web"` 通过（SMOKE_OK）。
- 附加检查：`grep "SET user_id" database.py` 无匹配（NO_BACKFILL）；`git diff --cached --name-only` 仅 database.py。

## 四、过程记录：一次 BLOCKED 与解除

首次实现曾被 comet-hook-guard 硬拦截（误伤）：守卫对 change 目录外的文件按字母序取「第一个活跃 change」的 phase，命中当时滞留 design 的 `admin-user-management`（另一滞留 design 的 `multi-user-publishing-channels` 为第二顺位），压过了本 change 合法的 build 阶段（自洽性校验当时已通过）。未绕过、未代改兄弟 change 状态，按规则上报 BLOCKED 并备好全部实现。用户随后批准修复守卫 fallback（多活跃 change 时按 build/verify 阶段判定通用源码写入），重试后放行，流程继续。RED 在阻塞前已完成且失败原因正确，GREEN 在解除后执行。

## 五、计划偏差（一处，控制器已批准）

**12 步重建顺序**：计划 Step 2 片段为先 `ALTER TABLE <t> RENAME TO <t>_legacy`，而计划「要点」写明官方 12 步（新建→拷贝→删旧→改名）。两者矛盾，按要点实现，控制器裁定批准。理由：SQLite ≥3.25 的 `ALTER TABLE RENAME` 会把其他表指向被改名表的外键引用一并改写，本库确认存在 `episodes.feed_id → feeds(id)` 与 `articles.episode_id → episodes(id)`，rename-first 会留下指向 `*_legacy` 的悬挂外键引用（全库未开 `PRAGMA foreign_keys`，错误不会立即爆发，属隐性 schema 损坏）；官方顺序全程不触碰其他表的引用文本。Step 4 已对两处 FK 引用做显式断言。

## 六、变更文件

- `D:\git3\Insights\database.py`（唯一源码变更，提交 8169f54）
- 本报告：`openspec/changes/multi-user-content-isolation/.comet/sdd/task-1-report.md`
- 工作区另有的 `.claude/skills/comet/scripts/comet-hook-guard.sh` 改动属用户修 hook 所留，未纳入本任务提交。

## 七、自审发现 / 顾虑（供后续任务知晓）

1. **查询层行为在 Task 2 前已变化**（计划内）：`add_feed` 的 `INSERT OR IGNORE INTO feeds (url, title)`（约 845-860 行）在复合唯一索引下 user_id 为 NULL 时不再冲突（SQLite 唯一索引 NULL 互异），同 URL 会重复插行，旧行为是取回既有 id；`save_episode` 的 `INSERT OR REPLACE INTO episodes` 同理。调用点按 user_id 过滤/赋值的改写属 Task 2+，建议尽早接手。
2. 无回填：全文件无 `UPDATE ... SET user_id`，旧行 user_id 保持 NULL（Step 4 断言）。
3. Step 3 的 episodes 插行依赖 FK 关闭（未给 feed_id）；Python sqlite3 默认 FK off，且全库未开启 FK，可稳定复现。
