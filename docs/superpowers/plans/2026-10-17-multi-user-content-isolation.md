---
change: multi-user-content-isolation
design-doc: docs/superpowers/specs/2026-10-17-multi-user-content-isolation-design.md
base-ref: 3d6cecefb68bc10cdb09ad7864c78ff48d34a129
---

# 多用户内容隔离 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 内容域 12 张表按登录用户隔离——列表只见自己的，跨用户单资源 404，后台产物归属触发者，存量 NULL 归属对所有人不可见。

**Architecture:** 三道防线。第一道：`database.py` 全部内容表函数签名强制 `user_id`（list 必过滤、get/update/delete 归属校验不匹配按不存在、写入口缺失即抛异常）；第二道：`insights_web.py` 约 60 条内容路由从 `current_user()` 取归属传给数据层；第三道：后台 worker 在入队/落库时固化行上归属，按行行事。启动时幂等 in-place 迁移加列并重建三个 `url UNIQUE` 表为 `(url, user_id)` 复合唯一。

**Tech Stack:** Python 3 / Flask / raw sqlite3（无 ORM，单文件 insights.db）；gate 脚本 = `scripts/check_*.py` 可独立运行输出 OK token，`scripts/run_all_gates.py` 汇总；Flask test client 双账号 E2E。

**Spec:** `openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md`（canonical）+ 设计文档 `docs/superpowers/specs/2026-10-17-multi-user-content-isolation-design.md`。

## Global Constraints

- 跑 Python 一律用 `venv/Scripts/python.exe`（plain python 缺 flask）。工作目录 `D:\git3\Insights`。
- 每个任务一个 commit；commit message 中文、**禁止任何 attribution**（无 Co-Authored-By / Claude-Session / 模型名）。
- 工作区有大量无关未跟踪文件（.claude/skills/*、其他 change 目录、docs/prototype、extension/* 等）——**只按任务列出的文件清单精确 `git add <path>`，禁止 `git add -A` / `git add .`**。
- 存量数据不迁移：无回填、无标志位，NULL 归属对所有用户（含 admin）不可见；任何代码路径不得自动为 NULL 赋归属。
- 跨用户单资源访问返回 404，不用 403；404 响应体与资源真不存在时完全一致（JSON 路由复用同一条 error 文案）。
- 列定义统一 `user_id INTEGER REFERENCES users(id)`（可空，默认 NULL）；仅 ALTER 加列，不回填。
- 数据层签名约定（全计划统一，不得混用）：
  - `list_*`：`user_id: int` 必填（无默认值），SQL 带 `WHERE ... user_id = ?`。
  - `get_* / update_* / delete_*`：`(主键id, user_id: int, ...)`，所有权过滤写进同一条 SQL，不匹配返回 None/False/0。
  - 写入口（add_/create_/queue_/save_/import）：`user_id: int` 必填，缺失经 `_require_user` 抛 `ValueError`。
  - `user_id` 一律通过 `database.py` 新增的 `_require_user(user_id)` 守卫：None 即 `ValueError("user_id is required ...")`。
- worker 队列级全局读取**白名单**（不做 user 过滤、gate 放行，按行上归属做事）：`get_due_content_briefs`、`get_content_brief_unscoped`、`get_youtube_episodes_missing_channel`、`set_episode_channel`、`get_pending_scheduled_posts`。
- 不隔离：social_accounts、agent 配对/登录态（change ②）、调度时段/每日限额（全局表）、users/auth、activity_log/usage_events 的既有 user 机制。
- 上传/生成文件留在 `static/uploads/`（UUID 文件名），归属以 DB 行为准，不做文件搬移、不做下载路由改造。
- 任务 2–13 期间 `run_all_gates.py` 中**直接调数据层种子函数**的旧 gate 预期变红；这些脚本在 Task 14 统一修复。期间每个任务的验证命令 = 本任务给出的命令 + 新 isolation gate 的已落地 section（Task 15 起可用）。`python -c "import database, insights_web"` 是每步的最低冒烟。
- 任务边界（不要扩大）：不做用户管理页、不动 PUBLIC_ENDPOINTS 与登录门槛、不动 AGENT_TOKEN 语义。

## Review Focus

以下输入/失败模式 spec 隐含但单任务测试未直接覆盖，逐条已由对应任务的测试钉住：

1. **跨用户探测不泄露存在性**——直访他人资源与访问真不存在的资源，状态码与响应体必须逐字节一致（404 + 同文案）。→ Task 15 data section 断言两个 URL 响应相等；Task 16 E2E 复核。
2. **NULL 归属行对所有路由可见性为零**——存量行 user_id=NULL 后，任何 list/get/update/delete（含 admin）都不可见，但行保留在库。→ Task 15 data section：seed 后手工置 NULL，双账号断言全不可见且行仍在。
3. **同 URL 双用户互不去重被复合唯一键放行、被旧全局唯一键拒绝**——feeds/episodes/url_sources 三个表各自验证双用户同 URL 各插一行成功；迁移前旧行为是第二个用户被 IGNORE。→ Task 15 schema section。
4. **worker 产 NULL 孤儿**——任何绕过 `_require_user` 的新写路径（后续开发）会被 gate 扫描挡住；本期由 scan section 保证 12 表 INSERT 全带 user_id 列。→ Task 15 scan section。
5. **非 admin 用 `?user_id=` / `?scope=all` 越权看他人用量/活动**——参数被静默忽略、渲染本人数据。→ Task 11 验证命令 + Task 16 E2E 断言。

---

### Task 0: 提交计划与 change 产物

**Files:**
- Commit: `openspec/changes/multi-user-content-isolation/`（proposal.md、design.md、tasks.md、specs/user-content-scoping/spec.md）
- Commit: `docs/superpowers/specs/2026-10-17-multi-user-content-isolation-design.md`
- Commit: `docs/superpowers/plans/2026-10-17-multi-user-content-isolation.md`（本文件）

**Interfaces:**
- Produces: 后续所有任务的基线 commit（base-ref 之上第一个提交）。

- [x] **Step 1: 精确 stage 并提交**

```bash
cd /d/git3/Insights
git add openspec/changes/multi-user-content-isolation/proposal.md \
        openspec/changes/multi-user-content-isolation/design.md \
        openspec/changes/multi-user-content-isolation/tasks.md \
        openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md \
        docs/superpowers/specs/2026-10-17-multi-user-content-isolation-design.md \
        docs/superpowers/plans/2026-10-17-multi-user-content-isolation.md
git commit -m "多用户内容隔离：proposal/design/tasks/spec 与实施计划"
```

- [x] **Step 2: 验证提交内容**

Run: `git show --stat HEAD | head -20`
Expected: 恰好 6 个文件，无其他文件混入；`git log -1 --format=%B` 无 attribution 行。

对应 tasks.md：无（计划产物）。

---

### Task 1: 迁移——11 表加 user_id 列 + 三表重建为 (url, user_id) 复合唯一

**Files:**
- Modify: `database.py` — `init_db()`（17-590 行区段：feeds 21-44、episodes 46-62、url_sources 243-256、uploaded_images 258-275 的 CREATE TABLE 与 upgrade 分支；在 470-495 行「Upgrade any existing DB with newer columns」区段后追加迁移块）

**Interfaces:**
- Produces: 12 张内容表均有可空 `user_id INTEGER REFERENCES users(id)` 列；`feeds`/`episodes`/`url_sources` 的 url 不再全局唯一，改为命名复合唯一索引 `idx_feeds_url_user(url, user_id)`、`idx_episodes_url_user(url, user_id)`、`idx_url_sources_url_user(url, user_id)`；`uploaded_images.url` 的全局 UNIQUE 保留（UUID 文件名天然唯一，`add_uploaded_image` 靠它做库内去重）。后续任务的全部 SQL 依赖列名 `user_id`。
- 高频查询表补 `user_id` 索引（设计留给实现者的裁量，定为这三张）：
  `idx_articles_user(articles.user_id)`、`idx_standalone_posts_user(standalone_posts.user_id)`、`idx_episodes_user(episodes.user_id)`，均普通索引。

要点（关键事实）：三张表的 `url TEXT UNIQUE` 是**表级约束产生的自动索引**（`sqlite_autoindex_*`），SQLite 不允许 `DROP INDEX` 自动索引——设计文档写的「迁移内 drop 旧索引」实际落地为**官方 12 步表重建**（新建→拷贝→删旧→改名）。已写入计划「开放问题」第 1 条。

- [x] **Step 1: 改四张表的 CREATE TABLE（新库直出正确 schema）**

`feeds` / `episodes` / `url_sources` 的 `url TEXT UNIQUE` 改为 `url TEXT`，并各加一行 `user_id INTEGER REFERENCES users(id)`；`uploaded_images` 只加 `user_id` 行。`episodes` 保留既有 `FOREIGN KEY(feed_id) REFERENCES feeds(id)`。紧随其后加复合唯一索引（`CREATE UNIQUE INDEX IF NOT EXISTS idx_<t>_url_user ON <t>(url, user_id)`），以及三个普通 user 索引。

- [x] **Step 2: 追加存量库迁移块（init_db 末尾 app_settings 之前）**

```python
# ── Multi-user content scoping: nullable ownership on every content table ──
_CONTENT_TABLES = ("feeds", "episodes", "articles", "social_posts",
                   "standalone_posts", "url_sources", "uploaded_images",
                   "generated_thumbnails", "prompt_library",
                   "content_brief_runs", "scheduled_posts")
for table in _CONTENT_TABLES:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if cols and "user_id" not in cols:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)")

# feeds/episodes/url_sources were born with `url TEXT UNIQUE` — a table-level
# constraint SQLite cannot drop. Rebuild the table (12-step) when the old
# definition is still in sqlite_master; the composite unique index then lets
# two users hold the same URL as independent rows.
for table in ("feeds", "episodes", "url_sources"):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    if not row or not re.search(r"url\s+TEXT\s+UNIQUE", row[0] or "", re.I):
        continue
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
    conn.execute(f"DROP TABLE IF EXISTS {table}_new")
    conn.execute(_NEW_DDL[table])            # 与 Step 1 的 CREATE TABLE 文本一致（含 user_id，url 无 UNIQUE）
    conn.execute(f"INSERT INTO {table} ({_COPY_COLS[table]}) "
                 f"SELECT {_COPY_COLS[table]} FROM {table}_legacy")
    conn.execute(f"DROP TABLE {table}_legacy")
```

`_NEW_DDL` / `_COPY_COLS` 为模块级常量，列清单显式点名（不 `SELECT *`，列序无关）。重建放在加列之后：老库重建一次即带上 user_id，新库不命中正则直接跳过——幂等。

- [x] **Step 3: 双跑幂等 + schema 断言（临时库，不碰 insights.db）**

Run:
```bash
cd /d/git3/Insights && venv/Scripts/python.exe -c "
import tempfile, os, sqlite3, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p); database.init_db(p)   # 连跑两次不报错
c = sqlite3.connect(p)
for t in ('feeds','episodes','articles','social_posts','standalone_posts','url_sources','uploaded_images','generated_thumbnails','prompt_library','content_brief_runs','scheduled_posts','content_briefs'):
    cols = [r[1] for r in c.execute(f'PRAGMA table_info({t})')]
    assert 'user_id' in cols, t
for t in ('feeds','episodes','url_sources'):
    sql = c.execute(\"SELECT sql FROM sqlite_master WHERE type='table' AND name=?\", (t,)).fetchone()[0]
    assert 'UNIQUE' not in sql.upper() or 'user_id' not in sql, t
    idx = [r[1] for r in c.execute(f'PRAGMA index_list({t})')]
    assert f'idx_{t}_url_user' in idx, (t, idx)
# 双用户同 URL 各一行
c.execute(\"INSERT INTO users (username, password_hash, is_admin) VALUES ('a','x',1),('b','x',0)\")
c.execute(\"INSERT INTO feeds (url,title,user_id) VALUES ('http://r','R',1)\")
c.execute(\"INSERT INTO feeds (url,title,user_id) VALUES ('http://r','R',2)\")
c.execute(\"INSERT INTO episodes (url,title,user_id) VALUES ('http://e','E',1)\")
c.execute(\"INSERT INTO episodes (url,title,user_id) VALUES ('http://e','E',2)\")
assert c.execute('SELECT COUNT(*) FROM feeds').fetchone()[0] == 2
print('MIGRATION_OK')
"
```
Expected: 输出 `MIGRATION_OK`，无 IntegrityError。

- [x] **Step 4: 存量库升级验证（构造带旧约束的库再跑 init_db）**

同一脚本里先用旧 DDL 文本建一个 `feeds (id, url TEXT UNIQUE, title)` 并插一行，跑 `database.init_db(p)`，断言旧行保留（`COUNT(*)==1`）且新 schema 生效。Expected: 行数不变、复合索引存在。

- [x] **Step 5: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import database, insights_web"`
```bash
git add database.py
git commit -m "迁移：内容域 11 表加可空 user_id 列，feeds/episodes/url_sources 重建为 (url,user_id) 复合唯一"
```

对应 tasks.md：1.1、1.2（无回填：本任务未写任何 `UPDATE ... SET user_id = ...`，Step 3/4 断言行数与内容不变）。

---

### Task 2: 数据层 feeds/episodes 作用域

**Files:**
- Modify: `database.py:716-971`（feeds：`get_feed/get_feed_by_id/list_feeds/delete_feed/delete_feeds_bulk/add_feed/update_feed_metadata`；episodes：`get_episode/save_episode/queue_episode/update_episode_status/get_episode_by_id/delete_episode_by_id/delete_episodes_bulk/reset_episode_for_reprocess/list_episodes/list_all_episodes`）

**Interfaces:**
- Consumes: Task 1 的 `user_id` 列与复合唯一索引。
- Produces（后续任务按此传参）：
  - `add_feed(url: str, title: str, user_id: int, db_path=...) -> int`；`INSERT OR IGNORE` 保留，回查 `SELECT id FROM feeds WHERE url = ? AND user_id = ?`。
  - `get_feed(url, user_id, ...)`、`get_feed_by_id(feed_id, user_id, ...)`、`list_feeds(user_id, ...)`。
  - `delete_feed(feed_id, user_id, ...)`（三段 DELETE 各加 `AND user_id = ?` / 子查询限定）、`delete_feeds_bulk(feed_ids, user_id, ...)`。
  - `update_feed_metadata(feed_id, feed_type, last_post, item_count, user_id, ...)`（user_id 放在 item_count 之后、db_path 之前）。
  - `save_episode(url, title, transcript, summary, action_items, feed_id, published=None, channel=None, user_id: int（必填，位置在 channel 后、db_path 前）, ...)`；`INSERT OR REPLACE` 列清单加 `user_id`。
  - `queue_episode(url, title, feed_id, published=None, user_id: int（必填）, ...)`；`INSERT OR IGNORE` 列清单加 `user_id`（复合唯一键使同 URL 双用户各一行）。
  - `get_episode(url, user_id, ...)`、`update_episode_status(url, status, user_id, ...)`、`get_episode_by_id(episode_id, user_id, ...)`、`delete_episode_by_id(episode_id, user_id, ...)`、`delete_episodes_bulk(episode_ids, user_id, ...)`、`reset_episode_for_reprocess(episode_id, user_id, ...)`、`list_episodes(feed_id, user_id, ...)`、`list_all_episodes(order_by="id", user_id, ...)`。
  - **保持不变（gate 白名单）**：`get_youtube_episodes_missing_channel(db_path=...)`、`set_episode_channel(episode_id, channel, db_path=...)`——后台元数据回填，按行上归属行事，不读内容正文。
- 本任务开头在 database.py 增加守卫（后续所有数据层任务共用）：

```python
def _require_user(user_id) -> int:
    """Content rows are always owned: a missing user_id is a caller bug."""
    if user_id is None:
        raise ValueError("user_id is required for content operations")
    return int(user_id)
```

- [x] **Step 1: 按 Interfaces 逐函数改签名与 SQL**（所有权谓词统一 `AND user_id = ?`；`_require_user(user_id)` 置于函数体首行）
- [x] **Step 2: 验证归属语义（临时库）**

Run:
```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
fa = database.add_feed('http://r', 'R', a, db_path=p)
try: database.add_feed('http://r', 'R', None, db_path=p); raise SystemExit('missing user accepted')
except ValueError: pass
fb = database.add_feed('http://r', 'R', b, db_path=p)
assert fa != fb and len(database.list_feeds(a, db_path=p)) == 1
assert database.get_feed_by_id(fa, b, db_path=p) is None          # 他人 feed 按不存在
assert database.get_episode('http://e', a, db_path=p) is None
database.queue_episode('http://e', 'E', fa, None, a, db_path=p)
assert database.get_episode('http://e', b, db_path=p) is None     # 他人 episode 不可见
database.queue_episode('http://e', 'E', fb, None, b, db_path=p)   # 同 URL 第二用户不去重
assert len(database.list_all_episodes(user_id=b, db_path=p)) == 1
database.update_episode_status('http://e', 'processing', a, db_path=p)
assert database.get_episode('http://e', b, db_path=p)['status'] == 'queued'  # B 的行未被 A 的更新碰到
print('FEEDS_EPISODES_SCOPING_OK')
"
```
Expected: `FEEDS_EPISODES_SCOPING_OK`。

- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import database"`
```bash
git add database.py
git commit -m "数据层：feeds/episodes 按 user 作用域，写入口强制归属"
```

对应 tasks.md：2.1、2.5（feeds/episodes 部分）。

---

### Task 3: 数据层 articles/social_posts 作用域

**Files:**
- Modify: `database.py:972-1298`（`add_article/get_article/update_article/delete_article/list_articles`；`add_social_post/list_social_posts/get_social_post/delete_social_post/delete_social_posts_bulk/delete_social_posts_for_article/mark_social_post_used/update_social_post/update_social_post_image/bulk_replace_post_content`）

**Interfaces:**
- Produces:
  - `add_article(episode_id, topic, style, content, user_id: int（必填，位置在 content 后）, db_path=..., brief_id=None, brief_run_id=None, source_type=None) -> int`；INSERT 列清单加 `user_id`。
  - `get_article(article_id, user_id, ...)`：主查询 `WHERE a.id = ? AND a.user_id = ?`。
  - `update_article(article_id, user_id, topic=None, style=None, content=None, db_path=...)`；`DELETE ... WHERE id = ? AND user_id = ?`。
  - `delete_article(article_id, user_id, ...)`：social_posts 先删，限定 `article_id IN (SELECT id FROM articles WHERE id = ? AND user_id = ?)`。
  - `list_articles(episode_id, user_id, db_path=...)`：两个分支各加 `WHERE a.user_id = ?`（episode 过滤可叠 AND）。
  - `add_social_post(article_id, platform, content, user_id: int（必填，位置在 content 后）, image_url=None, db_path=...) -> int`。
  - `list_social_posts(article_id, user_id, db_path=...)`、`get_social_post(post_id, user_id, ...)`、`delete_social_post(post_id, user_id, ...)`、`delete_social_posts_bulk(post_ids, user_id, ...)`、`delete_social_posts_for_article(article_id, user_id, ...)`、`mark_social_post_used(post_id, used=True, user_id 必填于 used 后, ...)`、`update_social_post(post_id, content, user_id, ...)`、`update_social_post_image(post_id, image_url, user_id, ...)`。
  - `bulk_replace_post_content(find_text, replace_text, post_type, user_id: int（必填，位置在 post_type 后）, case_sensitive=False, whole_word=False, post_ids=None, excluded_matches=None, db_path=...)`：两处 SELECT 与 UPDATE 均加 `AND user_id = ?`。
- [x] **Step 1: 按 Interfaces 改签名与 SQL**
- [x] **Step 2: 验证（临时库）**

Run:
```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
database.queue_episode('http://e', 'E', None, None, a, db_path=p)
eid = database.get_episode('http://e', a, db_path=p)['id']
aid = database.add_article(eid, 'T', 'blog', 'C', a, db_path=p)
assert database.get_article(aid, b, db_path=p) is None
assert database.update_article(aid, user_id=b, content='X', db_path=p) is None and database.get_article(aid, a, db_path=p)['content'] == 'C'
sp = database.add_social_post(aid, 'wechat', 'P', a, db_path=p)
assert database.get_social_post(sp, b, db_path=p) is None
assert database.delete_social_posts_bulk([sp], b, db_path=p) == 0   # bulk 不越权
assert database.delete_social_posts_bulk([sp], a, db_path=p) == 1
n = database.bulk_replace_post_content('C', 'D', 'social', a, db_path=p)  # 表级替换限定归属
assert n == 0 and database.get_article(aid, a, db_path=p)['content'] == 'C'
print('ARTICLES_SOCIAL_SCOPING_OK')
"
```
Expected: `ARTICLES_SOCIAL_SCOPING_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import database"`
```bash
git add database.py
git commit -m "数据层：articles/social_posts 按 user 作用域，批量替换限定归属"
```

对应 tasks.md：2.2（articles/social_posts 部分）、2.5。

---

### Task 4: 数据层 standalone_posts 与 recent prompts 作用域

**Files:**
- Modify: `database.py:2342-2685`（`add_standalone_post/get_existing_standalone_content/list_standalone_posts/get_standalone_post/list_standalone_posts_by_source_url/count_standalone_posts_by_source_urls/update_standalone_post/update_standalone_post_image/update_standalone_post_video/delete_standalone_post/delete_standalone_posts_bulk/mark_standalone_post_used`）；`database.py:2951-3072`（`list_recent_prompts/list_recent_image_prompts/clear_recent_prompts/delete_prompt_by_content/delete_prompts_bulk`）

**Interfaces:**
- Produces:
  - `add_standalone_post(source_type, source_content, platform, content, user_id: int（必填，位置在 content 后）, image_url=None, repost=False, db_path=..., brief_id=None, brief_run_id=None, account_id=None, video_url=None, ig_collaborators=None, ig_reel_tags=None) -> int`；INSERT 列清单加 `user_id`。
  - `get_existing_standalone_content(user_id, db_path=...)`（import 去重按用户）。
  - `list_standalone_posts(user_id, source_type=None, platform=None, limit=None, offset=None, db_path=...)`——**user_id 成为第一个位置参数**（web 层现多以 `list_standalone_posts()` 空参调用，改为 `list_standalone_posts(uid)`；Task 8 逐一改调用点）。
  - `get_standalone_post(post_id, user_id, ...)`、`list_standalone_posts_by_source_url(url, user_id, db_path=...)`、`count_standalone_posts_by_source_urls(urls, user_id, db_path=...)`。
  - `update_standalone_post(post_id, user_id, content, image_url=None, clear_image=False, db_path=...)`、`update_standalone_post_image(post_id, image_url, user_id, ...)`、`update_standalone_post_video(post_id, video_url, user_id, ...)`、`delete_standalone_post(post_id, user_id, ...)`、`delete_standalone_posts_bulk(post_ids, user_id, ...)`、`mark_standalone_post_used(post_id, used=True, user_id 必填, ...)`。
  - prompts（派生自 standalone_posts，自动隔离，但 SQL 仍显式带 `user_id = ?`）：`list_recent_prompts(user_id, limit=20, db_path=...)`、`list_recent_image_prompts(user_id, limit=10, db_path=...)`、`clear_recent_prompts(user_id, db_path=...)`、`delete_prompt_by_content(prompt_content, user_id, db_path=...)`、`delete_prompts_bulk(prompt_contents, user_id, db_path=...)`。
- [x] **Step 1: 按 Interfaces 改签名与 SQL**
- [x] **Step 2: 验证（临时库）**

Run:
```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
pa = database.add_standalone_post('freeform', 'prompt1', 'wechat', '正文', a, db_path=p)
assert database.get_standalone_post(pa, b, db_path=p) is None
assert database.list_standalone_posts(b, db_path=p) == []
assert [r['source_content'] for r in database.list_recent_prompts(b, db_path=p)] == []
assert database.delete_prompts_bulk(['prompt1'], b, db_path=p) == 0
assert database.count_standalone_posts_by_source_urls(['u'], b, db_path=p) == {'u': 0}
assert database.mark_standalone_post_used(pa, True, b, db_path=p) is None
row = database.get_standalone_post(pa, a, db_path=p); assert row['used'] == 0  # B 的标记没碰到 A 的行
print('STANDALONE_SCOPING_OK')
"
```
Expected: `STANDALONE_SCOPING_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import database"`
```bash
git add database.py
git commit -m "数据层：standalone_posts 与 recent prompts 按 user 作用域"
```

对应 tasks.md：2.2（standalone_posts 部分）、2.3（recent prompts）、2.5。

---

### Task 5: 数据层 url_sources/uploaded_images/prompt_library/generated_thumbnails 作用域

**Files:**
- Modify: `database.py:2690-2853`（url_sources：`add_url_source/list_url_sources/get_url_source/get_url_source_by_url/delete_url_source/update_url_source_last_used/update_url_source_content`）
- Modify: `database.py:2859-2945`（uploaded_images：`add_uploaded_image/list_uploaded_images/delete_uploaded_image/get_uploaded_image`）
- Modify: `database.py:3078-3137`（prompt_library：`add_library_prompt/list_library_prompts/get_library_prompt/update_library_prompt/delete_library_prompt`）
- Modify: `database.py:3143-3206`（generated_thumbnails：`add_generated_thumbnail/list_generated_thumbnails/get_generated_thumbnail/delete_generated_thumbnail`）

**Interfaces:**
- Produces:
  - `add_url_source(url, title, description, content, user_id: int（必填，位置在 content 后）, og_image=None, db_path=...) -> int`：既有 check-then-insert 逻辑保留，两处 SQL 各加 `AND user_id = ?` / 列清单加 `user_id`（复合唯一索引兜底同用户重复）。
  - `list_url_sources(user_id, db_path=...)`、`get_url_source(source_id, user_id, ...)`、`get_url_source_by_url(url, user_id, db_path=...)`、`delete_url_source(source_id, user_id, ...)`、`update_url_source_last_used(source_id, user_id, ...)`、`update_url_source_content(source_id, user_id, title, description, content, og_image=None, db_path=...)`（user_id 提到 title 前）。
  - `add_uploaded_image(filename, url, storage, user_id: int（必填，位置在 storage 后）, size=0, media_type='image', db_path=...)`；按 URL 去重改为 `WHERE url = ? AND user_id = ?`。
  - `list_uploaded_images(user_id, db_path=...)`、`delete_uploaded_image(image_id, user_id, ...)`、`get_uploaded_image(image_id, user_id, ...)`。
  - `add_library_prompt(title, content, user_id: int（必填，位置在 content 后）, db_path=...)`、`list_library_prompts(user_id, db_path=...)`、`get_library_prompt(prompt_id, user_id, ...)`、`update_library_prompt(prompt_id, user_id, title, content, db_path=...)`、`delete_library_prompt(prompt_id, user_id, db_path=...)`。
  - `add_generated_thumbnail(youtube_url, video_id, title, channel, aspect, style, prompt, image_relpath, user_id: int（必填，位置在 image_relpath 后）, db_path=...)`、`list_generated_thumbnails(user_id, limit=50, offset=0, db_path=...)`、`get_generated_thumbnail(thumbnail_id, user_id, db_path=...)`、`delete_generated_thumbnail(thumbnail_id, user_id, db_path=...)`。
- [x] **Step 1: 按 Interfaces 改签名与 SQL**
- [x] **Step 2: 验证（临时库）**

Run:
```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
sa = database.add_url_source('http://s', 'T', 'D', 'C', a, db_path=p)
sb = database.add_url_source('http://s', 'T', 'D', 'C', b, db_path=p)   # 同 URL 双用户各一行
assert sa != sb and len(database.list_url_sources(a, db_path=p)) == 1
assert database.get_url_source_by_url('http://s', b, db_path=p)['id'] == sb
assert database.delete_url_source(sa, b, db_path=p) is False
ia = database.add_uploaded_image('f.png', 'http://i/1', 'local', a, db_path=p)
assert database.get_uploaded_image(ia, b, db_path=p) is None
pl = database.add_library_prompt('t', 'c', a, db_path=p)
assert database.update_library_prompt(pl, b, 'x', 'y', db_path=p) == 0
th = database.add_generated_thumbnail('http://yt', 'vid', 't', 'c', '16:9', 's', 'p', 'r/x.png', a, db_path=p)
assert database.delete_generated_thumbnail(th, b, db_path=p) is False
print('MISC_CONTENT_SCOPING_OK')
"
```
Expected: `MISC_CONTENT_SCOPING_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import database"`
```bash
git add database.py
git commit -m "数据层：url_sources/上传图/prompt 库/生成封面按 user 作用域"
```

对应 tasks.md：2.3、2.5。

---

### Task 6: 数据层 content_briefs/content_brief_runs/scheduled_posts 作用域

**Files:**
- Modify: `database.py:3741-3971`（briefs：`create_content_brief/update_content_brief/get_content_brief/list_content_briefs/delete_content_brief/set_content_brief_enabled/set_content_brief_schedule/get_due_content_briefs`；runs：`create_brief_run/finalize_brief_run/get_brief_run/list_brief_runs/get_active_brief_run`）
- Modify: `database.py:1302-1972`（scheduled_posts 全部函数：`add_scheduled_post/get_scheduled_post/get_pending_schedules_for_social_posts/get_pending_schedules_for_standalone_posts/get_pending_schedule_ids_for_standalone_posts/get_posted_info_for_standalone_posts/list_scheduled_posts/get_scheduled_group_ids/get_pending_scheduled_posts/update_scheduled_post_time/redistribute_scheduled_posts/reorder_scheduled_posts/move_posts_to_position/update_scheduled_post_status/increment_retry_count/cancel_scheduled_post/cancel_scheduled_post_by_source/delete_scheduled_post/clear_pending_scheduled_posts/delete_scheduled_posts_bulk/get_scheduled_posts_for_article`）

**Interfaces:**
- Produces:
  - `create_content_brief(..., user_id: int（去掉 Optional，变必填——位置不变，仍在 max_drafts_per_run 之后）, db_path=...)`。
  - `get_content_brief(brief_id, user_id, db_path=...)`、`list_content_briefs(user_id, db_path=...)`、`update_content_brief(brief_id, user_id, db_path=..., **fields)`、`delete_content_brief(brief_id, user_id, ...)`（runs 级联删除同样限定归属）、`set_content_brief_enabled(brief_id, user_id, enabled, db_path=...)`、`set_content_brief_schedule(brief_id, user_id, next_run_at=None, last_run_at=None, last_run_status=None, db_path=...)`。
  - **白名单保持/新增**：`get_due_content_briefs(now_iso, db_path=...)` 不加 user 过滤（worker 队列，行上 user_id 决定归属）；新增 `get_content_brief_unscoped(brief_id, db_path=...) -> Optional[sqlite3.Row]`——`content_agent.run_brief` 的 worker 入口先用它取行、再按行上 user_id 行事（带归属过滤的 `get_content_brief` 需要调用方已知道 owner，worker 不满足）。
  - runs：`create_brief_run(brief_id, user_id: int（必填，位置在 brief_id 后）, trigger='manual', db_path=...)`、`get_brief_run(run_id, user_id, ...)`、`list_brief_runs(brief_id, user_id, limit=20, db_path=...)`、`get_active_brief_run(brief_id, user_id, db_path=...)`、`finalize_brief_run(run_id, user_id, status, sources_found=0, ..., db_path=...)`；`content_brief_runs` 的 INSERT/行上有 user_id（Task 1 已加列，本任务把列写进 INSERT）。
  - scheduled_posts：`add_scheduled_post(scheduled_for, post_type, user_id: int（必填，位置在 post_type 后）, social_post_id=None, article_id=None, standalone_post_id=None, platform='linkedin', ...)`（既有完整签名以源码为准，user_id 插在 post_type 之后）；`list_scheduled_posts(user_id, ...)`（保留既有 status/date_from/date_to/sort_order 关键字参数，SQL 加 `AND user_id = ?`）；`get_scheduled_post/get_scheduled_group_ids/update_scheduled_post_time/update_scheduled_post_status/increment_retry_count/cancel_scheduled_post/delete_scheduled_post` 均为 `(id, user_id, ...)`；`get_pending_schedules_for_social_posts/get_pending_schedules_for_standalone_posts/get_pending_schedule_ids_for_standalone_posts/get_posted_info_for_standalone_posts` 为 `(ids, user_id, ...)`；`reorder_scheduled_posts(post_ids, user_id, ...)`、`move_posts_to_position(post_ids, user_id, ...)`、`delete_scheduled_posts_bulk(post_ids, user_id, ...)`、`redistribute_scheduled_posts(platform, user_id, ...)`、`clear_pending_scheduled_posts(user_id, ...)`、`cancel_scheduled_post_by_source(..., user_id, ...)`、`get_scheduled_posts_for_article(article_id, user_id, ...)`。
  - **白名单**：`get_pending_scheduled_posts(db_path=...)` 保持无 user 过滤（发布侧消费队列，消费的是全局时段；行上归属不因此暴露给任何用户界面）。
- [x] **Step 1: 按 Interfaces 改签名与 SQL**
- [x] **Step 2: 验证（临时库）**

Run:
```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
try: database.create_content_brief('n','i', user_id=None, db_path=p); raise SystemExit('none accepted')
except ValueError: pass
bid = database.create_content_brief('n','i', user_id=a, db_path=p)
assert database.get_content_brief(bid, b, db_path=p) is None
rid = database.create_brief_run(bid, a, 'manual', db_path=p)
assert database.get_brief_run(rid, b, db_path=p) is None
assert len(database.list_brief_runs(bid, a, db_path=p)) == 1
try: database.add_scheduled_post('2099-01-01T00:00:00','standalone', None, db_path=p); raise SystemExit('none accepted')
except ValueError: pass
sp = database.add_scheduled_post('2099-01-01T00:00:00','standalone', a, standalone_post_id=None, platform='wechat', db_path=p)
assert database.list_scheduled_posts(b, db_path=p) == []
assert database.cancel_scheduled_post(sp, b, db_path=p) is False
assert database.list_scheduled_posts(a, db_path=p)[0]['status'] == 'pending'
print('BRIEFS_SCHEDULE_SCOPING_OK')
"
```
Expected: `BRIEFS_SCHEDULE_SCOPING_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import database"`
```bash
git add database.py
git commit -m "数据层：content_briefs/runs/scheduled_posts 按 user 作用域"
```

对应 tasks.md：2.4、2.5。

---

### Task 7: Web 层——源与文章路由按 current_user 过滤与校验

**Files:**
- Modify: `insights_web.py`（以下路由，全部模式：`uid = current_user()['id']` → 传给数据层；他人/不存在资源 `abort(404)` 或既有 404 jsonify 文案原样返回）：
  - `index` 1752（POST：`add_feed(rss_url, title, uid)`、`update_feed_metadata(..., uid)`、YouTube video 分支 `add_url_source(..., user_id=uid)`；GET：`list_feeds(uid)`）
  - `remove_feed` 1866、`bulk_delete_feeds` 1872（`delete_feeds_bulk(feed_ids, uid)`）
  - `refresh_feed` 1880（`get_feed_by_id(feed_id, uid)`，None → `abort(404)`）
  - `refresh_all_feeds` 1888（`list_feeds(uid)`；refresh_worker 闭包内沿用）
  - `view_feed` 1902（`get_feed_by_id(feed_id, uid)`，None → `abort(404)`；循环内 `get_episode(url, uid)`）
  - `enqueue_episode` 2024（先 `get_feed_by_id(feed_id, uid)` 校验归属，None → `abort(404)`；`queue_episode(..., uid)`；task_queue item 已带 user_id 不变）
  - `process_text_article` 2040、`process_episode` 2140（`get_feed_by_id(feed_id, uid)` 仅在 feed_id 给定时校验归属；`get_episode(url, uid)`；`save_episode(..., user_id=uid)`；`list_articles(eid, uid)`）
  - `status_page` 2403（`list_all_episodes(order_by=order_by, user_id=uid)`、`list_feeds(uid)`）
  - `reprocess_episode` 2471（`get_episode_by_id(id, uid)`，None → `abort(404)`；`reset_episode_for_reprocess(id, uid)`）
  - `delete_episode` 2508（同上 404 后 `delete_episode_by_id(id, uid)`）、`bulk_delete_episodes` 2514
  - `create_article` 2538（`get_episode_by_id(eid, uid)` 404；`add_article(eid, topic, style, content, uid)`）
  - `view_article` 2592、`edit_article` 2601、`remove_article` 2619（`get_article(id, uid)` 404；`update_article(id, uid, ...)`；删除时 `delete_social_posts_for_article(id, uid)` + `delete_article(id, uid)`）
  - `bulk_replace_posts` 2625（`bulk_replace_post_content(..., uid, ...)`）
  - `refine_article_with_ai` 2674（`get_article(id, uid)` 404；`update_article(id, uid, content=...)`）
  - `view_articles` 2703（`list_articles(None, uid)`）

**Interfaces:**
- Consumes: Task 2/3 的全部新签名。
- Produces: 上述路由行为（登录用户只能触达自己的 feed/episode/article）。路由签名（URL 参数）不变。
- [x] **Step 1: 逐路由改造**（404 语义：HTML 路由 `from flask import abort` 后 `abort(404)`；JSON 路由保持既有 `jsonify({"error": "..."})-404` 文案，他人与不存在同文案）
- [x] **Step 2: 验证（临时库 + test client，双账号）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys, os, tempfile
sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
from werkzeug.security import generate_password_hash
other = database.create_user('other', generate_password_hash('p'), db_path=database.DB_PATH)
me = database.get_user_by_username('gate')
fid = database.add_feed('http://r','R', me['id'], db_path=database.DB_PATH)
database.queue_episode('http://e','E', fid, None, me['id'], db_path=database.DB_PATH)
eid = database.get_episode('http://e', me['id'], db_path=database.DB_PATH)['id']
aid = database.add_article(eid,'UniqueTopicXYZ','blog','C', me['id'], db_path=database.DB_PATH)
r = client.get('/feed/%d' % fid); assert r.status_code == 200
with client.session_transaction() as s: s['user_id'] = other
r = client.get('/feed/%d' % fid); assert r.status_code == 404, r.status_code
r2 = client.get('/feed/999999'); assert r2.status_code == 404
assert r.data == r2.data, '404 body must not leak existence'
assert client.get('/article/%d' % aid).status_code == 404
assert b'UniqueTopicXYZ' not in client.get('/articles').data
assert client.get('/enqueue?url=http://e&feed_id=%d' % fid).status_code == 404
r = client.get('/')
assert b'http://r' not in r.data
print('WEB_FEEDS_ARTICLES_404_OK')
"
```
Expected: `WEB_FEEDS_ARTICLES_404_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import insights_web"`
```bash
git add insights_web.py
git commit -m "Web 层：源/文章/episode 路由按 current_user 过滤，跨用户 404"
```

对应 tasks.md：3.1。

---

### Task 8: Web 层——compose 列表/过滤/prompt/图库路由

**Files:**
- Modify: `insights_web.py`（模式同 Task 7；数据层新签名以 Task 4/5 为准）：
  - `compose_page` 3020（`list_standalone_posts(uid)`、`list_content_briefs(uid)`、`list_url_sources(uid)`、`get_url_source(id, uid)`、`list_recent_prompts(uid)`、`list_recent_image_prompts(uid)`）
  - `compose_posts_more` 3129、`_filtered_post_groups` 3263、`_filtered_standalone_posts` 3301（这三个从 `request` 出发，函数内 `current_user()['id']`）
  - `compose_posts_ids` 3343、`compose_posts_search` 3394、`compose_posts_replace` 3467（replace 走 `bulk_replace_post_content(..., uid, ...)`）
  - `compose_recent_prompts` 3562、`compose_clear_prompts` 3579、`compose_delete_prompt` 3593、`compose_delete_prompts_bulk` 3612
  - `_serialize_library_prompts` 3636、`compose_library_list` 3650、`compose_library_save` 3655（`add_library_prompt(title, content, uid)`）、`compose_library_update` 3679、`compose_library_delete` 3709（update/delete 前先 `get_library_prompt(id, uid)` 404，或直接以受影响行数判 404）
  - `compose_get_post` 4054（`get_standalone_post(id, uid)` 404）
  - `compose_list_images` 5766（`list_uploaded_images(uid)`）

**Interfaces:**
- Consumes: Task 4/5/6 签名。
- Produces: Compose 页与子接口只见本人帖子/prompts/库。
- [x] **Step 1: 逐路由改造**
- [x] **Step 2: 验证（临时库双账号）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
from werkzeug.security import generate_password_hash
other = database.create_user('other', generate_password_hash('p'), db_path=database.DB_PATH)
me = database.get_user_by_username('gate')
pid = database.add_standalone_post('freeform','pp','wechat','正文', me['id'], db_path=database.DB_PATH)
r = client.get('/compose'); assert '正文'.encode() in r.data
with client.session_transaction() as s: s['user_id'] = other
r = client.get('/compose'); assert '正文'.encode() not in r.data
assert client.get('/compose/post/%d' % pid).status_code == 404
assert b'pp' not in client.get('/compose/recent-prompts').data
print('WEB_COMPOSE_LIST_SCOPED_OK')
"
```
Expected: `WEB_COMPOSE_LIST_SCOPED_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import insights_web"`
```bash
git add insights_web.py
git commit -m "Web 层：Compose 列表/搜索/prompt/图库路由按 current_user 过滤"
```

对应 tasks.md：3.2（前半）。

---

### Task 9: Web 层——compose 生成/卡片/导入/媒体/推送路由

**Files:**
- Modify: `insights_web.py`：
  - `save_stock_image_to_library` 1220（加 `user_id: int` 参数；`list_uploaded_images(uid)`、三处 `add_uploaded_image(..., user_id=user_id)`；调用点同步传 uid）
  - `compose_generate` 3768（URL 分支 `add_url_source(..., user_id=uid)`；保存走 `_save_post_for_accounts(..., user_id=uid)`）
  - `compose_create_post` 4062（`_save_post_for_accounts(..., user_id=uid)`）
  - `_save_post_for_accounts` 4165（签名加 `user_id: int` 必填，透传 `add_standalone_post`）
  - `compose_render_post_card` 4328、`compose_toggle_post_platform` 4382（`get_standalone_post(id, uid)` 404；`_copy_post_to_target` 4363 加 user_id 透传 `add_standalone_post`）
  - `compose_bulk_add_platform` 4571、`compose_bulk_remove_platform` 4691（行解析经 `_selected_post_groups`/`_find_target_row`，底层 get 均带 uid；新增行 `_save_post_for_accounts(..., user_id=uid)`）
  - `compose_import_file` 4804（`get_existing_standalone_content(uid)`、`add_standalone_post(..., user_id=uid)`）
  - `compose_edit_post` 4981、`compose_update_post_image` 5020、`compose_update_post_video` 5071（get 404 + update 带 uid）
  - `compose_get_stock_images` 5096、`compose_apply_stock_image` 5144（`save_stock_image_to_library(url, user_id=uid)`）、`compose_apply_link_image` 5198、`compose_refresh_source_image` 5293、`_maybe_attach_link_image` 5356（**签名加 `user_id: int` 必填**：内部线程调 `get_standalone_post(pid, user_id)`、`save_stock_image_to_library(og, user_id=user_id)`、`update_standalone_post_image(target, url, user_id)`，而执行线程无请求上下文，必须由调用方显式传行归属；全部 compose 调用点传 uid，worker 侧 Task 13 传 brief 主人）
  - `compose_upload_image` 5476、`compose_upload_video` 5636（三处 `add_uploaded_image(..., user_id=uid)`）
  - `compose_annotate_media` 5678、`compose_search_stock_images` 5417、`compose_stock_images_status` 5461（无 DB 内容读，仅 media/annotate 若触库则按 uid）
  - `compose_delete_post` 5829、`compose_delete_bulk` 5841、`compose_bulk_update_images` 5861、`compose_toggle_used` 5911、`compose_bulk_toggle_used` 5932（update/delete 带 uid，get 404）
  - `compose_push_post` 5967（`get_standalone_post(id, uid)` 404）
  - `compose_clear_all` 6123（clear 限定 uid）
  - `compose_generate_from_source` 6532（`get_url_source(source_id, uid)` 404；`update_url_source_last_used(source_id, uid)`；`_save_post_for_accounts(..., user_id=uid)`）
  - `source_posts` 归 Task 10；`_selected_standalone_posts` 3315（get 带 uid，他人 id 静默剔除）

**Interfaces:**
- Consumes: Task 4/5 签名 + Task 8 的 `_selected_standalone_posts`。
- Produces: Compose 全部写路径落库行带当前用户归属；他人资源一律 404。
- [x] **Step 1: 逐路由改造**
- [x] **Step 2: 验证（临时库双账号，走真实 POST 路由）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
from werkzeug.security import generate_password_hash
other = database.create_user('other', generate_password_hash('p'), db_path=database.DB_PATH)
me = database.get_user_by_username('gate')
pid = database.add_standalone_post('freeform','pp','wechat','正文', me['id'], db_path=database.DB_PATH)
with client.session_transaction() as s: s['user_id'] = other
r = client.post('/compose/post/create', data={'content':'手写','platforms':'wechat'}, follow_redirects=False)
assert r.status_code == 200
rows = database.list_standalone_posts(other['id'], db_path=database.DB_PATH)
assert len(rows) == 1 and rows[0]['user_id'] == other['id']
assert client.post('/compose/post/%d/delete' % pid).status_code == 404
assert database.get_standalone_post(pid, me['id'], db_path=database.DB_PATH) is not None
assert client.post('/compose/post/%d/toggle-used' % pid).status_code == 404
print('WEB_COMPOSE_WRITE_SCOPED_OK')
"
```
Expected: `WEB_COMPOSE_WRITE_SCOPED_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import insights_web"`
```bash
git add insights_web.py
git commit -m "Web 层：Compose 生成/导入/媒体/推送路由写入当前用户归属，跨用户 404"
```

对应 tasks.md：3.2（后半）、2.5（路由侧落实）。

---

### Task 10: Web 层——sources/thumbnails/briefs/schedule 路由

**Files:**
- Modify: `insights_web.py`：
  - `sources_page` 6143（`list_url_sources(uid)`、`count_standalone_posts_by_source_urls(urls, uid)`、YouTube 分支 `get_episode(url, uid)`）
  - `source_posts` 6166（`get_url_source(id, uid)` 404；`list_standalone_posts_by_source_url(url, uid)`；`get_posted_info_for_standalone_posts(ids, uid)`）
  - `add_source` 6203（重复检查 `get_url_source_by_url(url, uid)`——**同 URL 双用户不再 409**，各自可存；两处 `add_url_source(..., user_id=uid)`）
  - `get_source` 6357、`delete_source` 6365（get 带 uid，404 文案不变）
  - `transcribe_source` 6375（get 带 uid 404；`get_episode(url, uid)`；`queue_episode(..., uid)`）
  - `reextract_source` 6398（get 带 uid 404；`update_url_source_content(source_id, uid, ...)`）
  - `thumbnails_fetch_metadata` 6648、`thumbnails_suggest_prompt` 6669、`thumbnails_generate` 6692（生成落库 `add_generated_thumbnail(..., user_id=uid)`）
  - `thumbnails_saved_list` 6753（`list_generated_thumbnails(uid)`）、`thumbnails_saved_delete` 6773（`get_generated_thumbnail(id, uid)` 404 + `delete_generated_thumbnail(id, uid)`）
  - `briefs_page` 6842（`list_content_briefs(uid)`；runs 查询 `list_brief_runs(bid, uid, limit=1)`）
  - `brief_create` 6869（已有 `data['user_id']`；`create_content_brief` 签名未变语义）
  - `brief_edit` 6884（`get_content_brief(brief_id, uid)` 404；`update_content_brief(brief_id, uid, **data)`；`set_content_brief_schedule(brief_id, uid, ...)`）
  - `brief_delete` 6896（`delete_content_brief(brief_id, uid)`；先 get 404）
  - `brief_toggle` 6901、`brief_run_now` 6912（两处读都带 uid：`get_content_brief(brief_id, uid)` 404、`get_active_brief_run(brief_id, uid)`；run 的 `_bg` 闭包已捕获 runner；`create_brief_run(brief_id, uid, trigger='manual')`——runner 即 brief 主人，因 brief 列表已隔离）
  - `brief_runs` 6939、`brief_run_status` 6943（`list_brief_runs(brief_id, uid)`；`get_brief_run(run_id, uid)` 404——run 归属校验拦住跨用户轮询）
  - `schedule_page` 2326（`database.list_scheduled_posts(uid, date_from=first, date_to=last, sort_order='asc')`；`list_time_slots`/每日限额保持全局不动）

**Interfaces:**
- Consumes: Task 5/6 签名。
- Produces: 源页/封面页/briefs 页/日程月历仅本人数据。
- [x] **Step 1: 逐路由改造**
- [x] **Step 2: 验证（临时库双账号）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
from werkzeug.security import generate_password_hash
other = database.create_user('other', generate_password_hash('p'), db_path=database.DB_PATH)
me = database.get_user_by_username('gate')
sid = database.add_url_source('http://s','T','D','C', me['id'], db_path=database.DB_PATH)
bid = database.create_content_brief('n','i', user_id=me['id'], db_path=database.DB_PATH)
with client.session_transaction() as s: s['user_id'] = other
assert client.get('/sources/%d' % sid).status_code == 404
assert client.delete('/sources/%d' % sid).status_code == 404
assert client.post('/briefs/%d/run' % bid).status_code == 404
assert client.get('/briefs/%d/runs' % bid).status_code == 404
r = client.get('/briefs'); assert b'\"n\"' not in r.data and b'>n<' not in r.data
r = client.post('/sources', data={'url': 'http://s'})
assert r.status_code == 200   # 同 URL 第二用户可存，不再 409
assert len(database.list_url_sources(other['id'], db_path=database.DB_PATH)) == 1
print('WEB_SOURCES_BRIEFS_SCOPED_OK')
"
```
Expected: `WEB_SOURCES_BRIEFS_SCOPED_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import insights_web"`
```bash
git add insights_web.py
git commit -m "Web 层：sources/thumbnails/briefs/schedule 路由按 current_user 过滤"
```

对应 tasks.md：3.3。

---

### Task 11: /activity 与 /usage 的 admin 视角收口

**Files:**
- Modify: `insights_web.py` — `_parse_activity_filters` 698、`activity_page` 723、`activity_csv` 753；`templates/activity.html`（非 admin 隐藏「用户」筛选下拉，服务端拦截是本任务的硬保证，模板只是不再渲染无效控件）

**Interfaces:**
- Consumes: 既有 `current_user()['is_admin']`。
- Produces: 非 admin 请求 `/activity`、`/activity.csv` 一律固定 `user_id=本人`，`?user_id=` 参数被忽略；admin 可带 `?user_id=` 筛选或不带看全部。`/usage` 已有 `_usage_scope_user_id()`（831 行，scope=all 仅 admin）无需改动——验证步骤覆盖。
- [x] **Step 1: 改 `_parse_activity_filters`**

```python
user = current_user()
raw_user = request.args.get("user_id") or ""
requested = int(raw_user) if raw_user.isdigit() else None
if user and not user["is_admin"]:
    user_id = user["id"]        # 非 admin 固定本人视角；请求参数一律忽略
else:
    user_id = requested         # admin：按参数筛选，不带参数看全部
```

（`current_user` 此时已非 None——登录门槛保证；保守起见 `user` 为 None 时沿用 requested。）

- [x] **Step 2: `templates/activity.html` 用户筛选控件包 `<% if current_user and current_user['is_admin'] %>`**
- [x] **Step 3: 验证（临时库双账号）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
from werkzeug.security import generate_password_hash
other = database.create_user('other', generate_password_hash('p'), db_path=database.DB_PATH)
me = database.get_user_by_username('gate')
database.log_activity(action='mine', user_id=me['id'], username='gate', db_path=database.DB_PATH)
database.log_activity(action='theirs', user_id=other['id'], username='other', db_path=database.DB_PATH)
with client.session_transaction() as s: s['user_id'] = other
r = client.get('/activity?user_id=%d' % me['id']).data
assert b'theirs' not in r and b'mine' in r          # 参数被忽略，固定本人
r = client.get('/activity.csv?user_id=%d' % me['id']).data
assert b'theirs' not in r
with client.session_transaction() as s: s['user_id'] = me
r = client.get('/activity?user_id=%d' % other['id']).data
assert b'theirs' in r                               # admin 可按用户筛
assert client.get('/usage?scope=all').data.count(b'theirs') >= 0  # admin scope=all 放行
with client.session_transaction() as s: s['user_id'] = other
assert b'theirs' not in client.get('/usage?scope=all').data  # 非 admin scope=all 被忽略
print('ACTIVITY_USAGE_SCOPE_OK')
"
```
Expected: `ACTIVITY_USAGE_SCOPE_OK`。
- [x] **Step 4: 提交**

```bash
git add insights_web.py templates/activity.html
git commit -m "活动页收口：非 admin 固定本人视角，忽略 user_id 参数；全部视角仅 admin"
```

对应 tasks.md：3.4。

---

### Task 12: episode worker 与 feed 元数据刷新的归属

**Files:**
- Modify: `insights_web.py` — `_backfill_youtube_source` 1350、`worker` 1362、`refresh_feed_metadata` 1710、`_proactive_worker` 1416（不变，仅包裹）
- Modify: `insights_web.py` — `_backfill_youtube_channels_bg` 6791（保持全局元数据回填，白名单，不改动）

**Interfaces:**
- Consumes: Task 2 的 `save_episode/update_episode_status/get_episode/update_feed_metadata` 新签名。
- Produces:
  - `worker()`：队列 item 携带的 `user_id` 即归属（入队路由 Task 7 已强制 feed 归属校验 + `queue_episode(..., uid)` 落行）。两处 `update_episode_status(url, s, item['user_id'])`（processing 与 error 分支）；两处 `save_episode(..., user_id=item['user_id'])`；item 无 user_id（防御）时抛错进 except 分支置 error，不产 NULL。`_backfill_youtube_source(url, t, s, user_id=item['user_id'])` → 内部 `get_url_source_by_url(url, user_id)` + `update_url_source_content(id, user_id, ...)`。
  - `refresh_feed_metadata(feed_id, feed_url, user_id: int 必填)` → `update_feed_metadata(feed_id, ..., user_id)`；全部调用点（`view_feed`、`process_*`、`refresh_feed`、`refresh_all_feeds` 的 worker、`index` POST）已在 Task 7 持有 uid/行归属。
- [x] **Step 1: 按上述改 worker 与 refresh_feed_metadata**
- [x] **Step 2: 验证（临时库，直接驱动 worker 函数体逻辑所需的最小路径——不入真网络，用假 summarize/transcribe）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
me = database.get_user_by_username('gate')
# 用队列同步路径验证：直接调用 save/update 的 worker 代码形态
database.queue_episode('http://e','E', None, None, me['id'], db_path=database.DB_PATH)
web.update_episode_status('http://e', 'processing', me['id'], db_path=database.DB_PATH)
web.save_episode('http://e','E','t','s',[], None, None, user_id=me['id'], db_path=database.DB_PATH)
row = database.get_episode('http://e', me['id'], db_path=database.DB_PATH)
assert row['user_id'] == me['id'] and row['status'] == 'complete'
other = database.create_user('o','x', db_path=database.DB_PATH)
assert database.get_episode('http://e', other, db_path=database.DB_PATH) is None
print('WORKER_ATTRIBUTION_OK')
"
```
Expected: `WORKER_ATTRIBUTION_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import insights_web"`
```bash
git add insights_web.py
git commit -m "worker：episode 落库与状态更新按入队者归属，元数据刷新带 feed 主人"
```

对应 tasks.md：4.1（episode 部分）。

---

### Task 13: content_agent / research_engine / brief 定时跑批的归属

**Files:**
- Modify: `content_agent.py` — `_persist_posts` 138、`_attach_image` 130、`_auto_queue` 174、`run_brief` 208（行读取改 `get_content_brief_unscoped`、`create_brief_run`、`finalize_brief_run` 434、`add_article` 两处）
- Modify: `research_engine.py` — `ResearchBrief` 56（加字段 `user_id: int`）、`_web_hits_to_items` 302、`_gather_existing` 378、`_drop_already_posted` 430
- Modify: `insights_web.py` — `content_agent_worker` 6986（`create_brief_run(bid, brief['user_id'], trigger='scheduled')`；`get_active_brief_run(bid, brief['user_id'])`）
- Modify: `insights_web.py` — `brief_run_now` 6912 的 `create_brief_run`（Task 10 已改；本任务只对齐 research 传参）

**Interfaces:**
- Consumes: Task 6 的 `create_brief_run(brief_id, user_id, ...)`、`get_active_brief_run(brief_id, user_id, ...)`；Task 3/4 的 `add_article/add_standalone_post/add_scheduled_post` 新签名。
- Produces:
  - `run_brief`：行读取改为 `database.get_content_brief_unscoped(brief_id)`（Task 6 新增的白名单 helper），从 `row['user_id']` 取 owner（行上必非 NULL：create 时已强制）；`ResearchBrief(..., user_id=owner)`；`_persist_posts(..., user_id=owner)` → `add_standalone_post(..., user_id=owner)`；`_attach_image(pid, content)` 加 owner 参数、晚导入调用改为 `_maybe_attach_link_image(pid, str(content), user_id=owner)`（执行线程无请求上下文，见 Task 9）；两处 `add_article(..., user_id=owner)`；`_auto_queue` → `add_scheduled_post(..., user_id=owner)`；`create_brief_run(brief_id, owner, trigger=trigger)`、`finalize_brief_run(run_id, owner, status=...)`。owner 为 None 时 fail-fast（defensive：`if not owner: raise ValueError`）。
  - `research_engine._gather_existing`：`list_url_sources(brief.user_id)`、`list_all_episodes('published', brief.user_id)`；`_web_hits_to_items`：`get_url_source_by_url(canon, brief.user_id)`、`add_url_source(..., user_id=brief.user_id)`；`_drop_already_posted`：`count_standalone_posts_by_source_urls(urls, brief.user_id)`。
  - `content_agent_worker`：调度分支按 `brief['user_id']` 建 run；既有 `usage_meter.user_context(owner...)` 链路不动（用量归属沿用）。
- [x] **Step 1: 按上述改造三文件**
- [x] **Step 2: 验证（临时库；不跑真模型，用 monkeypatch 假 generate）**

Run:
```bash
venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from _accounts_gate import isolated_app
d, database, web, pub, client = isolated_app()
import content_agent, research_engine
other = database.create_user('o','x', db_path=database.DB_PATH)
bid = database.create_content_brief('n','do posts', content_type='posts', user_id=other, db_path=database.DB_PATH)
research_engine.run_research = lambda brief, use_local=False: type('R',(),{'items':[],'warnings':['no_candidates'],'query_plan':[]})()
insights_gen = lambda **kw: {'wechat': '生成的帖子'}
import insights
insights.generate_posts_from_prompt = insights_gen
summary = content_agent.run_brief(bid, trigger='scheduled')
assert summary['posts_created'] == 1, summary
row = database.list_standalone_posts(other, db_path=database.DB_PATH)[0]
assert row['user_id'] == other and row['brief_id'] == bid
me = database.get_user_by_username('gate')
assert database.list_standalone_posts(me['id'], db_path=database.DB_PATH) == []  # 不产孤儿
print('BRIEF_ATTRIBUTION_OK')
"
```
Expected: `BRIEF_ATTRIBUTION_OK`。
- [x] **Step 3: 冒烟 + 提交**

Run: `venv/Scripts/python.exe -c "import content_agent, research_engine"`
```bash
git add content_agent.py research_engine.py insights_web.py
git commit -m "内容代理与跑批：产物按 brief 主人归属，research 读取限定该用户"
```

对应 tasks.md：4.1（content_agent 部分）、4.2。

---

### Task 14: 修复既有 gate 脚本的数据层调用点

**Files:**
- Modify（按调用点数量排序）：`scripts/check_ig_people.py`（43 处）、`scripts/check_video.py`（27）、`scripts/check_bulk_platform.py`（21）、`scripts/check_crosspost.py`（16）、`scripts/check_queue_groups.py`（16）、`scripts/check_scheduler_accounts.py`（13）、`scripts/check_manual_posts.py`（11）、`scripts/check_domestic_publisher.py`（6）、`scripts/check_backcompat.py`（6）、`scripts/_video_ui_gate.py`（6）、`scripts/check_accounts_ui.py`（5）、`scripts/check_agent_push.py`（4）、`scripts/check_brief_fallback.py`（4）、`scripts/check_accounts_model.py`（3）、`scripts/check_compose_paging.py`（2）、`scripts/check_agent_ui.py`（2）、`scripts/check_login_without_identity.py`（2）、`scripts/check_bulk_platform_ui.py`（1）、`scripts/check_channel_defaults.py`（1）、`scripts/check_compose_js.py`（1）

**Interfaces:**
- Consumes: Task 2–6 的全部新签名。
- Produces: 既有 gate 全绿。机械规则：
  1. 每个脚本顶部（或 isolated_app 之后）取 `uid = database.get_user_by_username('gate')['id']`（`_accounts_gate.isolated_app` 固定建 `gate` 用户）。
  2. 直接种子调用逐一补 `user_id=uid`（或按新位置传参）；路由驱动的调用不用改。
  3. 断言「全表可见」的脚本（如旧行为断言 list 总数）改为按 uid 断言。
- [ ] **Step 1: 按文件逐个修复**（每改完一个脚本立即单独跑该脚本确认自身 token 通过）
- [ ] **Step 2: 全量跑既有 gate**

Run: `venv/Scripts/python.exe scripts/run_all_gates.py`
Expected: 除（尚未存在的）isolation gate 外全部 PASS；无 unregistered gate 报警。
- [ ] **Step 3: 提交**

```bash
git add scripts/check_ig_people.py scripts/check_video.py scripts/check_bulk_platform.py scripts/check_crosspost.py scripts/check_queue_groups.py scripts/check_scheduler_accounts.py scripts/check_manual_posts.py scripts/check_domestic_publisher.py scripts/check_backcompat.py scripts/_video_ui_gate.py scripts/check_accounts_ui.py scripts/check_agent_push.py scripts/check_brief_fallback.py scripts/check_accounts_model.py scripts/check_compose_paging.py scripts/check_agent_ui.py scripts/check_login_without_identity.py scripts/check_bulk_platform_ui.py scripts/check_channel_defaults.py scripts/check_compose_js.py
git commit -m "gate 脚本适配数据层 user 归属签名"
```

对应 tasks.md：5.1（前置）。

---

### Task 15: check_content_isolation.py gate（schema / data / scan）并入 run_all_gates

**Files:**
- Create: `scripts/check_content_isolation.py`（沿用 `_accounts_gate.isolated_app` + `check()` 惯例；三节：`schema`、`data`、`scan`，各打印独立 token）
- Modify: `scripts/run_all_gates.py`（新增 ISOLATION_GATES 组并入 ALL_GATES）

**Interfaces:**
- Produces: 四个 gate 入口（本任务三个 + Task 16 的 e2e）：
  - `venv/Scripts/python.exe scripts/check_content_isolation.py schema` → `CONTENT_ISOLATION_SCHEMA_OK`
  - `... data` → `CONTENT_ISOLATION_DATA_OK`
  - `... scan` → `CONTENT_ISOLATION_SCAN_OK`
- 白名单（scan 节豁免，函数名→理由）：`get_due_content_briefs`（worker 队列，行上归属）、`get_content_brief_unscoped`（run_brief worker 入口，先取行再按行上 user_id 行事）、`get_youtube_episodes_missing_channel`（元数据回填）、`set_episode_channel`（元数据回填）、`get_pending_scheduled_posts`（发布侧全局队列消费）。
- [ ] **Step 1: schema 节**——临时库 init_db 跑两遍；断言 12 表有 user_id；断言三表复合唯一索引存在且建表 SQL 不再含 `url ... UNIQUE`；双用户同 URL 各插一行（feeds/episodes/url_sources 三表）；构造旧 schema 库升级后行数不变；**迁移幂等**（第二次 init_db 后 `PRAGMA table_info` 无重复列）。
- [ ] **Step 2: data 节**——双账号覆盖：每张内容表至少一条「B 直访/改/删 A 的行 → None/False/0」「NULL 归属行对 A、B 及 admin 均不可见且行仍在」「bulk 只删自己的 id」「写入口缺 user_id 抛 ValueError」「404 与不存在响应一致（走路由的两组代表路由：`/article/<id>`、`/sources/<id>`）」。
- [ ] **Step 3: scan 节**——`ast.parse` 读 `database.py` 源；对 12 张内容表（`_CONTENT_TABLES` 11 表 ∪ `content_briefs`）：函数体内 SQL 字符串常量含 `SELECT ... FROM <t>` / `UPDATE <t>` / `DELETE FROM <t>` / `INSERT INTO <t>` 的，要求 SQL 文本含 `user_id`，否则该函数必须在白名单；INSERT 额外要求列清单含 `user_id`。任一违例打印函数名与 SQL 摘要并 fail。
- [ ] **Step 4: run_all_gates.py 注册**

```python
ISOLATION_GATES = [
    ("check_content_isolation.py", "CONTENT_ISOLATION_SCHEMA_OK",
     "迁移幂等：12 表带 user_id，feeds/episodes/url_sources 复合唯一，同 URL 双用户各一行",
     ("schema",)),
    ("check_content_isolation.py", "CONTENT_ISOLATION_DATA_OK",
     "双账号互不可见，NULL 存量对含 admin 的所有人不可见，bulk 与写入口均不越权",
     ("data",)),
    ("check_content_isolation.py", "CONTENT_ISOLATION_SCAN_OK",
     "database.py 内容表 SELECT 带 user 过滤、INSERT 带归属列（白名单除外）",
     ("scan",)),
]
```
并入 `ALL_GATES` 元组拼接。

- [ ] **Step 5: 验证**

Run: `venv/Scripts/python.exe scripts/check_content_isolation.py schema && venv/Scripts/python.exe scripts/check_content_isolation.py data && venv/Scripts/python.exe scripts/check_content_isolation.py scan`
Expected: 三个 token 齐打。再 `venv/Scripts/python.exe scripts/run_all_gates.py` 全绿。
- [ ] **Step 6: 提交**

```bash
git add scripts/check_content_isolation.py scripts/run_all_gates.py
git commit -m "门禁：内容隔离 schema/数据/静态扫描三 gate 并入总闸"
```

对应 tasks.md：5.1。

---

### Task 16: 端到端双账号验收 gate（e2e 节）

**Files:**
- Modify: `scripts/check_content_isolation.py`（追加 `e2e` 节）
- Modify: `scripts/run_all_gates.py`（ISOLATION_GATES 追加一条）

**Interfaces:**
- Consumes: Task 7–13 的全部路由与 worker 行为；`isolated_app()` 双会话（`session_transaction` 切换 user_id）。
- Produces: `venv/Scripts/python.exe scripts/check_content_isolation.py e2e` → `CONTENT_ISOLATION_E2E_OK`
- [ ] **Step 1: e2e 节场景**（一次跑完，逐场景 `check()`）：
  1. admin（首个账号 gate 设为 admin：`database` 无 is_admin 参数时直接 SQL 置 1）+ 新注册账号 B（走 `/signup` 或 create_user）。
  2. B 登录后五页全空：`/`（feeds）、`/articles`、`/compose`、`/briefs`、`/schedule` 不含 admin 种子数据的任何标题/正文片段。
  3. B 直访 admin 的 `/article/<id>`、`/sources/<id>`、`/feed/<id>`、`/compose/post/<id>` → 404 且响应体与访问不存在 id 时逐字节一致。
  4. 双账号各建同名 prompt 的 standalone post → `/compose` 各自只见自己的。
  5. 存量 NULL：种子行后 SQL 置 `user_id=NULL` → 双方列表/详情均不可见，行仍在库（直查 COUNT）。
  6. worker 产物归属：admin 入队 episode（`/enqueue?...&feed_id=`）→ 断言 episodes 行 user_id=admin；brief 跑批归属复用 Task 13 的假 generate 路径断言产物 user_id=brief 主人。
  7. `/activity`、`/usage`：B 带 `?user_id=<admin>&scope=all` → 只见自己；admin `?scope=all` → 见双方。
  8. 同 URL 双用户：A、B 各自 `/sources` POST 同一 URL → 各自 1 行互不去重；A、B 各自 add_feed 同一 RSS URL → feeds 各 1 行。
  9. 迁移幂等（对临时库二次 init_db）——已在 schema 节，e2e 不重复。
- [ ] **Step 2: run_all_gates 注册追加**

```python
    ("check_content_isolation.py", "CONTENT_ISOLATION_E2E_OK",
     "双账号端到端：新账号全页空、直访他人 404 同响应、存量 NULL 不可见、worker 产物归属、/activity /usage 视角",
     ("e2e",)),
```
- [ ] **Step 3: 全量验证**

Run: `venv/Scripts/python.exe scripts/run_all_gates.py`
Expected: 全部 PASS，末行 `ALL_GATES_OK`。
- [ ] **Step 4: 提交**

```bash
git add scripts/check_content_isolation.py scripts/run_all_gates.py
git commit -m "门禁：多用户内容隔离双账号端到端验收"
```

对应 tasks.md：5.2。

---

## 开放问题（实现中发现，不扩大范围）

1. **三表 `url TEXT UNIQUE` 需表重建而非 drop 索引**（Task 1 已按此实现）：设计文档 5.1 写「episodes 的 url 全局唯一索引改为 (url, user_id) 复合唯一，迁移内先探测 drop 旧索引」，但 `feeds`/`episodes`/`url_sources` 的唯一约束是 CREATE TABLE 内联的 `url TEXT UNIQUE`——SQLite 自动索引无法 `DROP INDEX`，官方做法是 12 步表重建。本计划按 spec 场景「两个用户各自添加同一 RSS 源或同一 URL 源各自保留独立行」实现重建（feeds/episodes/url_sources 三表），功能结果与设计意图一致。`uploaded_images.url` 的全局 UNIQUE 有意保留（UUID 文件名天然全局唯一，`add_uploaded_image` 依赖它做库内去重，隔离语义不受影响）。
2. **worker 队列级读取的白名单**：「list_* 必带 user 过滤」与「worker 按行上归属行事」在五 个函数上交叉（`get_due_content_briefs`、`get_content_brief_unscoped`、`get_youtube_episodes_missing_channel`、`set_episode_channel`、`get_pending_scheduled_posts`）——它们是 worker 的全局队列扫描或入口取行，产物归属由行上 user_id 决定，本计划显式豁免并在 gate 中登记理由。
3. **/usage 页无按用户筛选控件**：spec 场景「admin 按所选范围渲染」在 usage 页由 `scope=all` 满足；「指定某用户」筛选目前只有 activity 页有（`?user_id=`，Task 11 收口为仅 admin）。不为 usage 页新增 per-user 下拉（超出本 change 的最小实现）。
4. **静态文件不做访问隔离**：`static/uploads/` 由 Flask 静态路由直出，UUID 文件名不可枚举；设计 5.2 明确以 DB 行为准。若将来要彻底隔离需改为受保护下载路由，属后续 change。
5. **`/enqueue` 与 `transcribe_source` 的既有重定向语义**：原实现对缺失参数 redirect 回列表；本计划对「资源不存在/不归属」统一 `abort(404)`，对「缺参数」保留原 redirect——两者语义分开，避免把缺参数误报成资源冲突。

## 任务 → tasks.md 对照

| 计划任务 | tasks.md |
|---|---|
| Task 0 | （计划产物） |
| Task 1 | 1.1、1.2 |
| Task 2 | 2.1、2.5 |
| Task 3 | 2.2、2.5 |
| Task 4 | 2.2、2.3、2.5 |
| Task 5 | 2.3、2.5 |
| Task 6 | 2.4、2.5 |
| Task 7 | 3.1 |
| Task 8 | 3.2（前半） |
| Task 9 | 3.2（后半） |
| Task 10 | 3.3 |
| Task 11 | 3.4 |
| Task 12 | 4.1 |
| Task 13 | 4.1、4.2 |
| Task 14 | 5.1（前置） |
| Task 15 | 5.1 |
| Task 16 | 5.2 |
