---
change: admin-user-management
design-doc: docs/superpowers/specs/2026-10-20-admin-user-management-design.md
base-ref: 7569977bb40165d2a7cef2e984af2adad9b00c3f
archived-with: 2026-10-20-admin-user-management
---

# Admin 用户管理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin 在一个页面完成用户列表、重置密码、删除用户（连数据带本地文件一起删）；非 admin（含未登录）对该页面与接口一律 404。

**Architecture:** 纯新增。数据层在 `database.py` 加级联删除/密码重置/判定函数（单事务逐表 DELETE，SQLite 外键从未启用，一切级联显式做）；web 层在 `insights_web.py` 加独立 404 门控装饰器与 4 条路由；UI 为一个 `.pd` 模板 + base.html 侧边栏条件区块 + zh.json 词条；验证走项目 gate 惯例 `scripts/check_admin_user_management.py`（`data` / `e2e` 两节）。

**Tech Stack:** Flask + sqlite3 + werkzeug（`generate_password_hash`/`check_password_hash`）；gate 复用 `scripts/_accounts_gate.py` 的 `isolated_app()` / `check()`。

**Spec:** `openspec/changes/admin-user-management/specs/admin-user-management/spec.md`（需求事实源）+ 上列 design-doc。

## Global Constraints

- SQLite `PRAGMA foreign_keys` 从未启用：**不可依赖任何 FK 级联**，删除必须逐表显式 `DELETE ... WHERE user_id = ?`。
- 删除级联 = 单事务 15 表 DELETE + users 行：`_CONTENT_TABLES` 11 表（feeds, episodes, articles, social_posts, standalone_posts, url_sources, uploaded_images, generated_thumbnails, prompt_library, content_brief_runs, scheduled_posts）+ content_briefs + ② 通道 3 表（social_accounts, agent_jobs, agent_tokens）；activity_log / usage_events **保留**且 `user_id=NULL, username=NULL`。
- 本地文件清理在**事务提交后**：uploaded_images `storage='local'` 行 → `static/uploads/<basename(url)>`；generated_thumbnails 行 → `<repo>/<image_relpath>`（即 `static/uploads/youtube_thumbnails/`）。失败仅记日志，不回滚。
- 自我保护：禁删当前登录 admin 自己；禁删最后一个 `is_admin` 账号（路由层双保险 + 数据层谓词）。
- 访问控制：新路由用独立装饰器 `require_admin_or_404`（复用 `require_admin` 判定但 `abort(404)`），四个端点加入 `PUBLIC_ENDPOINTS` 以绕过全局登录重定向——**未登录也必须 404 且与不存在资源逐字节一致**（spec 场景明写「含未登录」；全局中间件对未登录是 302/401，不满足 spec，故必须走 PUBLIC_ENDPOINTS + 视图自身门控）。`/settings` 的 `require_admin` 403 行为**不可动**。
- 无自定义 404 handler：`abort(404)` 响应即与不存在资源逐字节一致（insights_web.py 现状，直接依赖）。
- 密码：werkzeug `generate_password_hash` 输入即改，不校验旧密码、无邮件；被重置者现有 session 有效至自然过期（Flask 无服务端会话态，接受并已写入设计）。
- 删除确认 = 输入目标用户名（strip 后**大小写敏感**精确比较）+ 逐表计数预览。
- promote/demote admin **不在本期**；「转让」逃生门 = 手工 SQL `UPDATE users SET is_admin=1 WHERE username=?`，只写进文档。
- gate 惯例：`venv/Scripts/python.exe scripts/check_admin_user_management.py <section>` 从仓库根运行；分节 + OK token；临时目录/内存库，不联网、不触真实 insights.db、不写仓库 `static/uploads/`（文件清理用 `files_root` 参数指向临时目录）。
- UI：模板持英文原文，`.pd` 组件库（页面套 `<div class="pd">`），base.html 侧边栏 NAV 条件追加 admin 区块（仅 `current_user['is_admin']` 渲染），zh.json 词条锚定 `^...$` 精确匹配、动态文案用捕获组；**规则顺序敏感**：与既有规则同串时更长的在前，新增规则统一追加到数组末尾。
- 提交：中文短消息，无任何 attribution trailer（仓库 CLAUDE.md 硬性规定）。

## Review Focus

spec 未逐条覆盖、最可能咬人的五类输入（均已落到拥有该代码的任务测试里）：

1. **不存在的 user_id 打 stats/password/delete 三个接口** → 必须普通 404（与不存在路径同响应体），不是 500。Task 3/4 各自钉。
2. **确认输入带首尾空白或大小写不符**（" Admin " / "admin" vs "Admin"）→ strip 宽容空白，大小写敏感判不符即拒，一个字节都不删。Task 4 钉。
3. **本地文件在磁盘上已丢失**（手工清过/重复删）→ unlink 抛 FileNotFoundError 仅记日志，删除流程照常成功。Task 1 钉（`files_root` 下不存在的路径）+ Task 4 e2e 经路由再钉一次。
4. **被删用户正登录着** → 其下一个请求 `current_user()` 得 None，既有全局中间件弹回登录页（无服务端会话态，无需新代码，但必须钉住不回归）。Task 4 钉。
5. **双 admin 并发互删的窗口** → 两次删除各自读到 count=2 都放行，可能剩 0 admin。SQLite 单写串行但检查在各自事务内，窗口不可靠复现、不加测试；接受（`is_admin` 可手工 SQL 恢复），删除弹层文案不承诺并发安全。

---

### Task 1: 数据层——级联删除、密码重置、admin 判定 + gate `data` 节

**Files:**
- Modify: `database.py`（插在 `update_last_login` 之后、`# Activity log` 分节注释之前，约 :3779）
- Create: `scripts/check_admin_user_management.py`
- Modify: `scripts/run_all_gates.py`（新增 `ADMIN_USER_GATES` 组并入 `ALL_GATES`，仿 ：305 的 `ISOLATION_GATES` 写法）

**Interfaces:**
- Consumes: 现有 `database.py` 惯例——每函数自带 `db_path: str = DB_PATH` kwarg、`with sqlite3.connect(db_path) as conn` 事务；种子写入用既有 helper（`add_feed`/`queue_episode`/`add_article`/`add_social_post`/`add_standalone_post`/`add_url_source`/`add_uploaded_image`/`add_generated_thumbnail`/`add_library_prompt`/`create_content_brief`/`create_brief_run`/`add_scheduled_post`/`upsert_social_account`/`enqueue_agent_job`/`create_agent_token`/`log_activity`/`log_usage`，全部接受 `user_id=` 与 `db_path=`）。
- Produces（Task 2-4 与 gate 后续节依赖的精确签名）:
  - `_DELETE_USER_TABLES: tuple[str, ...]`——15 表元组（模块级私有常量，注释指回 `init_db` 内的 `_CONTENT_TABLES`（~:718）提醒漂移同步）。
  - `update_user_password(user_id: int, password_hash: str, *, db_path: str = DB_PATH) -> bool`——更新成功返回 True。
  - `is_last_admin(user_id: int, *, db_path: str = DB_PATH) -> bool`——目标不存在或非 admin 返回 False；目标是 admin 且 `COUNT(*) FROM users WHERE is_admin=1` 为 1 返回 True。
  - `count_user_rows(user_id: int, *, db_path: str = DB_PATH) -> Dict[str, int]`——键为 `_DELETE_USER_TABLES` 的 15 个表名，值为其 `WHERE user_id = ?` 行数。
  - `delete_user(user_id: int, *, db_path: str = DB_PATH, files_root: Optional[str] = None) -> Dict[str, int]`——返回逐表删除行数（15 表 + `"users"`）；`files_root=None` 时取 `os.path.dirname(os.path.abspath(__file__))`（生产=仓库根，gate=临时目录）。

- [x] **Step 1: 写 gate 骨架 + `data` 节（预期失败）**

创建 `scripts/check_admin_user_management.py`，骨架仿 `scripts/check_content_isolation.py`：UTF-8 `sys.stdout.reconfigure`、两级 `sys.path.insert`（`scripts/` 与仓库根）、`from _accounts_gate import isolated_app, check`、`REPO` 常量、模块 docstring 写明两节的运行方式与 token、底部 `SECTIONS` 字典 + `__main__` 无参时逐节起子进程跑全节（照抄 `scripts/check_channel_isolation.py` 的 `__main__` 块，含 `import subprocess`）。

`section_data()` 全程 `tempfile.mkdtemp` + `database.init_db(temp_db)`，不碰 `isolated_app`（本节不起 web）：

1. 建号：`create_user("gate", hash)`（首号自动 admin）、`create_user("victim", hash)`、`create_user("keeper", hash)`；SQL 置 keeper `is_admin=1`。
2. 文件夹：`files_root = <tmp>/site`，写出 `files_root/static/uploads/victim_local.png`、`files_root/static/uploads/youtube_thumbnails/victim_thumb.png`、`files_root/static/uploads/keeper_local.png`、以及一个**不存在的** `files_root/static/uploads/ghost.png`（Review Focus 3）。
3. 为 victim 在 15 表各播 1 行（用 Interfaces 列出的 helper，`db_path=temp_db`）：`add_uploaded_image("victim_local.png", "/static/uploads/victim_local.png", "local", user_id=victim)`；`add_uploaded_image("ghost.png", "/static/uploads/ghost.png", "local", user_id=victim)`；`add_generated_thumbnail(..., "static/uploads/youtube_thumbnails/victim_thumb.png", user_id=victim)`；`upsert_social_account("wechat", "vx-1", user_id=victim)`；`enqueue_agent_job("article_sync", "{}", user_id=victim)`；`create_agent_token(victim, "victim-token")`；其余按 helper 签名各一行。keeper 播对照行：`add_uploaded_image("keeper_local.png", "/static/uploads/keeper_local.png", "local", ...)`、`create_agent_token(keeper, "keeper-token")`。
4. 审计：`log_activity("zx", user_id=victim, username="victim", db_path=...)`、`log_usage(mode="proactive", category="compose", provider="g", model="m", user_id=victim, username="victim", db_path=...)`。
5. `before = database.count_user_rows(victim, db_path=temp_db)`；断言 15 个键各 == 1。
6. `counts = database.delete_user(victim, db_path=temp_db, files_root=files_root)`；断言 `counts` 中 15 表全 == 1 且 `counts["users"] == 1`。
7. 库态断言：15 表 `COUNT(*) WHERE user_id = victim` 全 0；`users` 无 victim 行；activity_log / usage_events 中原 victim 行仍在且 `user_id IS NULL AND username IS NULL`；`find_agent_user_by_token("victim-token", db_path=temp_db) is None`；`find_agent_user_by_token("keeper-token", ...) == keeper`；keeper 的行与文件幸存。
8. 文件断言：victim 两个文件已 unlink；`ghost.png` 本就不存在——`delete_user` 不抛错（Review Focus 3）；keeper 文件还在。
9. `update_user_password(victim 不适用)`→ 对 keeper：取旧 hash → `update_user_password(keeper, generate_password_hash("np"), db_path=temp_db) is True` → 库中 hash 已变且 `check_password_hash(new, "np")` True、对旧密码 False。
10. `is_last_admin` 真值表：`is_last_admin(victim已删, ...) is False`；`is_last_admin(999999, ...) is False`（不存在）；仅 gate+keeper 两个 admin 时对两者均 False；SQL 把 keeper 置 `is_admin=0` 后 `is_last_admin(gate)` True、`is_last_admin(keeper)` False。
11. 边界：`delete_user(999999, db_path=temp_db, files_root=files_root)` 不抛错、各键 0、`users` 键 0。

- [x] **Step 2: 跑 `data` 节验证失败**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py data`
Expected: FAIL——`AttributeError: module 'database' has no attribute 'delete_user'`（或首个缺失函数名）。

- [x] **Step 3: 实现 `database.py` 四个函数**

在 `update_last_login` 之后插入（签名以上面 Interfaces 为准，逐字实现以下裁决，这是签名+测试不足以完全确定的算法）：

```python
# 删除级联的 15 表清单：init_db 里的 _CONTENT_TABLES（见上方迁移段）11 表
# + content_briefs + multi-user-publishing-channels 的通道 3 表。两张清单
# 必须同步漂移：往 _CONTENT_TABLES 加表时这里也要加。
_DELETE_USER_TABLES = (
    "feeds", "episodes", "articles", "social_posts", "standalone_posts",
    "url_sources", "uploaded_images", "generated_thumbnails", "prompt_library",
    "content_brief_runs", "scheduled_posts",
    "content_briefs", "social_accounts", "agent_jobs", "agent_tokens",
)

def update_user_password(user_id: int, password_hash: str, *,
                         db_path: str = DB_PATH) -> bool:
    """Overwrite a user's password hash. True if the row existed."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )
        conn.commit()
        return cur.rowcount == 1

def is_last_admin(user_id: int, *, db_path: str = DB_PATH) -> bool:
    """True only when ``user_id`` is an admin and the instance has no other."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not row[0]:
            return False
        admins = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
        return admins == 1

def count_user_rows(user_id: int, *, db_path: str = DB_PATH) -> Dict[str, int]:
    """Per-table row counts the delete confirmation dialog previews."""
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ?",
                (user_id,)).fetchone()[0]
            for table in _DELETE_USER_TABLES
        }

def delete_user(user_id: int, *, db_path: str = DB_PATH,
                files_root: Optional[str] = None) -> Dict[str, int]:
    """Delete a user and every row they own, in one transaction.

    FKs are never enforced in this project (PRAGMA foreign_keys has never
    been on), so the cascade is explicit, table by table. activity_log and
    usage_events rows survive but lose their identity (user_id and username
    both set to NULL). Local files behind uploaded_images(storage='local')
    and generated_thumbnails rows are unlinked AFTER the commit; a missing
    or unremovable file is logged and skipped — the database is already
    committed and must not roll back for a filesystem side-effect.
    """
    if files_root is None:
        files_root = os.path.dirname(os.path.abspath(__file__))
    counts: Dict[str, int] = {}
    pending_files: List[str] = []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT url FROM uploaded_images "
            "WHERE user_id = ? AND storage = 'local' AND url IS NOT NULL",
            (user_id,)).fetchall()
        for (url,) in rows:
            # basename() pins the path under static/uploads whatever the row says.
            pending_files.append(os.path.join(
                files_root, "static", "uploads", os.path.basename(url)))
        rows = conn.execute(
            "SELECT image_relpath FROM generated_thumbnails "
            "WHERE user_id = ? AND image_relpath IS NOT NULL AND image_relpath != ''",
            (user_id,)).fetchall()
        for (relpath,) in rows:
            path = os.path.normpath(os.path.join(files_root, relpath))
            if path.startswith(os.path.normpath(files_root)):
                pending_files.append(path)
        for table in _DELETE_USER_TABLES:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            counts[table] = cur.rowcount
        for table in ("activity_log", "usage_events"):
            conn.execute(
                f"UPDATE {table} SET user_id = NULL, username = NULL "
                "WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        counts["users"] = cur.rowcount
        conn.commit()
    for path in pending_files:
        try:
            os.remove(path)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "delete_user: could not remove %s: %s", path, exc)
    return counts
```

同时在 `database.py` 顶部 import 区加 `import logging`（现无；`os`/`List`/`Optional`/`Dict` 已有）。

- [x] **Step 4: 跑 `data` 节验证通过**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py data`
Expected: 打印断言摘要 + `ADMIN_USER_MANAGEMENT_DATA_OK`，退出码 0。

- [x] **Step 5: 注册 gate 并跑 inventory 自检**

`scripts/run_all_gates.py`：在 `CHANNEL_GATES` 之后加组并入 `ALL_GATES`：

```python
# Admin user management: cascade delete + password reset + admin guards.
ADMIN_USER_GATES = [
    ("check_admin_user_management.py", "ADMIN_USER_MANAGEMENT_DATA_OK",
     "delete_user 单事务级联清 15 表+users，审计两表去身份化保留，local 文件清理，"
     "密码重置生效，末位 admin 判定正确",
     ("data",)),
]
```

`ALL_GATES` 元组拼接追加 `+ ADMIN_USER_GATES`。注意 `delete_user` 的 SQL 文本含 `user_id`，`check_content_isolation.py scan` 节不会报它（activity_log/usage_events 不在 `_SCAN_TABLES`），无需改白名单。

Run: `venv/Scripts/python.exe scripts/run_all_gates.py --inventory`（若该脚本无 `--inventory` 入口，则运行 `venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'scripts'); from run_all_gates import gate_inventory_problems; print(gate_inventory_problems())"`）
Expected: 无 unregistered / missing。

- [x] **Step 6: Commit**

```bash
git add database.py scripts/check_admin_user_management.py scripts/run_all_gates.py
git commit -m "用户管理数据层：delete_user 单事务级联 15 表+users、审计去身份化、提交后清本地文件；密码重置与末位 admin 判定"
```

---

### Task 2: 404 门控装饰器 + 用户列表页（路由/模板/侧边栏/zh.json）+ gate `e2e` 节

**Files:**
- Modify: `insights_web.py`（`PUBLIC_ENDPOINTS` :314、`require_admin` 之后 :631、`instance_settings` 视图之后加路由）
- Create: `templates/admin_users.html`
- Modify: `templates/base.html`（NAV 定义 :589 之后）
- Modify: `static/i18n/zh.json`（rules 数组末尾追加）
- Modify: `scripts/check_admin_user_management.py`（加 `section_e2e`）
- Modify: `scripts/run_all_gates.py`（`ADMIN_USER_GATES` 追加 e2e 条目）

**Interfaces:**
- Consumes: Task 1 无直接依赖（本任务列表页只用既有 `list_users()`）；`_accounts_gate.isolated_app()` 返回 `(dir, database, web, publisher, client)`，`client` 已登录首号 `gate`（默认**非** admin，需 SQL 置位）。
- Produces:
  - 装饰器 `require_admin_or_404(view)`——判定同 `require_admin`（`current_user()` 为 None 或非 admin），但 `abort(404)`。
  - 路由 `GET /admin/users` → 视图函数名 `admin_users_page`，渲染 `admin_users.html`，上下文 `users=list_users()`。
  - `PUBLIC_ENDPOINTS` 增 `"admin_users_page"`（附注释：admin 端点自带 404 门控，绕过全局登录重定向以满足「未登录也 404」）。
  - 后续任务的三个端点名（本任务先占注释不占集合）：`admin_user_stats` / `admin_reset_password` / `admin_delete_user`。
  - gate e2e 基线取法：`missing = client.get("/definitely-not-a-route")`，断言用 `r.get_data() == missing.get_data()`（GET）与 `missing_post = client.post("/definitely-not-a-route")`（POST）。

- [x] **Step 1: 写 `section_e2e`（列表 + 404 场景，预期失败）**

`check`/`isolated_app` 已在 Task 1 导入。场景：

1. `directory, database, web, publisher, client = isolated_app()`；SQL 置 `gate` 为 admin；`uid_v = database.create_user("victim", generate_password_hash("p"), db_path=database.DB_PATH)`；`client_v = web.app.test_client()` 并 `session_transaction` 注入 `user_id = uid_v`；`anon = web.app.test_client()`（无 session）。
2. admin `GET /admin/users` → 200，页面含 `"victim"` 与 `"gate"` 用户名及表头 `Username`。
3. victim `GET /admin/users` → 404 且 body 与 admin 视角 `GET /definitely-not-a-route` 逐字节一致。
4. anon `GET /admin/users` → 404 且与同一基线逐字节一致（Review Focus/Global Constraints：未登录也 404）。
5. victim 的 `GET /`（任意普通页）不含字符串 `"/admin/users"`（侧边栏零泄漏）；admin 的 `GET /` 含它。
6. zh.json 完整性：`json.load(open(REPO/static/i18n/zh.json))`，遍历 `rules` 全部 `re.compile(r["re"])` 成功；且本任务新增的每条精确规则对对应英文字符串 `re.match` 命中（钉住字典不与模板脱钩）。

末尾打印 `ADMIN_USER_MANAGEMENT_E2E_OK`。`run_all_gates.py` 的 `ADMIN_USER_GATES` 追加：

```python
    ("check_admin_user_management.py", "ADMIN_USER_MANAGEMENT_E2E_OK",
     "admin 列表可见，非 admin 与未登录对页面 404 且与不存在逐字节一致，侧边栏仅 admin 渲染",
     ("e2e",)),
```

- [x] **Step 2: 跑 e2e 验证失败**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py e2e`
Expected: FAIL——404（路由不存在时 Flask 默认也是 404，但场景 2/5/6 会先失败：页面/模板/词条缺失）。

- [x] **Step 3: 实现装饰器与路由**

`insights_web.py` 三处：

a) `PUBLIC_ENDPOINTS`（:314）加 `"admin_users_page"`，集合上方加注释：

```python
# Admin endpoints 404-gate themselves (require_admin_or_404) instead of using
# the global login redirect, so an anonymous or non-admin probe gets the same
# 404 body as a nonexistent path and learns nothing — spec: 含未登录也 404。
```

b) `require_admin`（:622-630）之后并排加：

```python
def require_admin_or_404(view):
    """Admin-only like require_admin, but 404s so the page never "exists"."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None or not user["is_admin"]:
            abort(404)
        return view(*args, **kwargs)
    return wrapped
```

c) `instance_settings` 视图之后加（本任务只加第一条路由；新函数调用统一走 `database.` 模块前缀，便于 gate 定位、不动 import 块）：

```python
# ── User management (admin; 404 for everyone else) ────────────────────────

@app.route("/admin/users", methods=["GET"])
@require_admin_or_404
def admin_users_page():
    return render_template("admin_users.html", users=list_users())
```

`list_users` 加入顶部 `from database import (...)` 名单（既有名称走既有惯例）。

- [x] **Step 4: 实现 `templates/admin_users.html`**

```jinja
{% extends "base.html" %}
{% block title %}User Management | Insights{% endblock %}
{% block header %}User Management{% endblock %}
{% block content %}
<div class="pd">
    {% with msgs = get_flashed_messages() %}
        {% for m in msgs %}<div class="alert alert-warning">{{ m }}</div>{% endfor %}
    {% endwith %}
    <div class="card">
        <div class="card-head"><b>Users</b><span class="pill">{{ users|length }}</span></div>
        <div class="card-body" style="padding-top:1rem">
            <table class="table table-sm align-middle">
                <thead><tr>
                    <th>Username</th><th>Admin</th><th>Created</th><th>Last login</th><th>Actions</th>
                </tr></thead>
                <tbody>
                {% for u in users %}
                    <tr>
                        <td><strong>{{ u['username'] }}</strong></td>
                        <td>{% if u['is_admin'] %}<span class="pill">Admin</span>{% else %}<span class="status-pill is-off">—</span>{% endif %}</td>
                        <td>{{ u['created_at'] or '—' }}</td>
                        <td>{{ u['last_login_at'] or '—' }}</td>
                        <td>
                            {# Task 3 加 Reset password 表单；Task 4 加 Delete 按钮 #}
                        </td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
{% endblock %}
```

- [x] **Step 5: base.html 侧边栏 admin 区块**

`templates/base.html` 的 `NAV` 集合定义（:589 `] %}`）之后、`on()` 宏之前插（顶层 `{% if %}` 不产生作用域，赋值对后续 sidebar/palette 生效；`users` 图标 :630 已存在）：

```jinja
{% if current_user and current_user['is_admin'] %}
    {% set NAV = NAV + [{'group': 'Admin', 'items': [
        {'label': 'User Management', 'ic': 'users',
         'desc': 'Accounts, passwords, removal',
         'href': url_for('admin_users_page'), 'prefixes': ['/admin/users']},
    ]}] %}
{% endif %}
```

- [x] **Step 6: zh.json 词条（本任务字符串）**

`static/i18n/zh.json` 的 `rules` 数组**末尾**追加（全部锚定，防与既有泛规则互吞）：

```json
  {"re": "^User Management$", "to": "用户管理"},
  {"re": "^Users$", "to": "用户"},
  {"re": "^Username$", "to": "用户名"},
  {"re": "^Admin$", "to": "管理员"},
  {"re": "^Created$", "to": "创建时间"},
  {"re": "^Last login$", "to": "最近登录"},
  {"re": "^Actions$", "to": "操作"}
```

- [x] **Step 7: 跑 e2e 与 data 验证通过**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py e2e` 与 `... data`（data 回归）
Expected: 各自打印 `ADMIN_USER_MANAGEMENT_E2E_OK` / `ADMIN_USER_MANAGEMENT_DATA_OK`。再跑 `venv/Scripts/python.exe scripts/check_content_isolation.py scan`（database.py 未动则恒过，保险）。

- [x] **Step 8: Commit**

```bash
git add insights_web.py templates/admin_users.html templates/base.html static/i18n/zh.json scripts/check_admin_user_management.py scripts/run_all_gates.py
git commit -m "用户管理页：require_admin_or_404 门控（未登录/非 admin 均 404 同响应体）+ 列表路由/模板/侧边栏 admin 区块/zh 词条"
```

---

### Task 3: 重置密码路由 + 表单 + e2e 场景

**Files:**
- Modify: `insights_web.py`（`PUBLIC_ENDPOINTS`、flask import 行加 `flash`、`admin_users_page` 之后加路由）
- Modify: `templates/admin_users.html`（Actions 列加表单）
- Modify: `static/i18n/zh.json`
- Modify: `scripts/check_admin_user_management.py`（e2e 追加场景）

**Interfaces:**
- Consumes: Task 1 `update_user_password(user_id, password_hash, *, db_path)`；既有 `get_user_by_id`（insights_web 已 import）与 `generate_password_hash`（:38 已 import）。
- Produces: 路由 `POST /admin/users/<int:user_id>/password` → 视图 `admin_reset_password`；表单字段 `new_password`；成功/失败均 `flash()` 后 `redirect(url_for("admin_users_page"))`；`PUBLIC_ENDPOINTS` 增 `"admin_reset_password"`。

- [x] **Step 1: 追加 e2e 场景（预期失败）**

1. admin `POST /admin/users/<uid_v>/password` data `{"new_password": "np123"}` → 302 → follow_redirects 后页面含 flash 文本 `Password reset for victim.`；库中 `check_password_hash(victim 新 hash, "np123")` True、对旧 `"p"` False（`get_user_by_id` 取行验证）。
2. victim 旧 session 仍有效（spec 场景）：重置后 victim client `GET /` → 200（无服务端会话态的既定行为，钉住不回归）。
3. 空 `new_password`（`""` 与纯空格两种）→ hash 不变，页面含 `New password cannot be empty.`。
4. 对不存在 id `POST /admin/users/999999999/password` → 404 且 body 与 `POST /definitely-not-a-route` 基线逐字节一致（Review Focus 1）。
5. victim `POST` 同端点 → 404 与 POST 基线一致；anon `POST` 同端点 → 404 与 anon 的 POST 基线一致。

- [x] **Step 2: 跑 e2e 验证失败**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py e2e`
Expected: FAIL——场景 1 起 404/找不到文本。

- [x] **Step 3: 实现路由**

flask import 行加 `flash`；`PUBLIC_ENDPOINTS` 增 `"admin_reset_password"`；`admin_users_page` 之后：

```python
@app.route("/admin/users/<int:user_id>/password", methods=["POST"])
@require_admin_or_404
def admin_reset_password(user_id: int):
    user = get_user_by_id(user_id)
    if user is None:
        abort(404)
    new_password = request.form.get("new_password") or ""
    if not new_password.strip():
        flash("New password cannot be empty.")
        return redirect(url_for("admin_users_page"))
    update_user_password(user_id, generate_password_hash(new_password))
    flash(f"Password reset for {user['username']}.")
    return redirect(url_for("admin_users_page"))
```

（`update_user_password` 经 `from database import` 加入——本任务起新数据函数逐个补进 import 名单，与既有惯例一致。）

- [x] **Step 4: 模板 Actions 列加表单**

`admin_users.html` 的 Actions 单元格替换为（原生 details 折叠，零 JS）：

```jinja
<details class="pd-reset">
    <summary class="btn btn-outline btn-sm">Reset password</summary>
    <form method="post" action="{{ url_for('admin_reset_password', user_id=u['id']) }}"
          class="field" style="margin-top:.4rem; min-width:14rem">
        <label for="pw-{{ u['id'] }}">New password</label>
        <input class="input" type="password" id="pw-{{ u['id'] }}" name="new_password"
               autocomplete="new-password" required>
        <button class="btn btn-primary btn-sm" type="submit">Reset password</button>
    </form>
</details>
```

- [x] **Step 5: zh.json 词条**

rules 末尾追加：

```json
  {"re": "^New password$", "to": "新密码"},
  {"re": "^Reset password$", "to": "重置密码"},
  {"re": "^New password cannot be empty\\.$", "to": "新密码不能为空。"},
  {"re": "^Password reset for (.+)\\.$", "to": "已重置 $1 的密码。"}
```

并把两条新文案规则加进 e2e 的 zh.json 场景清单（`re.match` 命中对应英文串）。

- [x] **Step 6: 跑 e2e 验证通过**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py e2e`
Expected: `ADMIN_USER_MANAGEMENT_E2E_OK`。

- [x] **Step 7: Commit**

```bash
git add insights_web.py templates/admin_users.html static/i18n/zh.json scripts/check_admin_user_management.py
git commit -m "用户管理：admin 重置任意用户密码（werkzeug 哈希输入即改，不校验旧密码；非 admin/未登录 404）"
```

---

### Task 4: 删除用户——量级预览、二次确认、守卫、文件/token 失效 e2e

**Files:**
- Modify: `insights_web.py`（`PUBLIC_ENDPOINTS`、import 名单补 `is_last_admin`、两条路由）
- Modify: `templates/admin_users.html`（Delete 按钮 + 确认弹层 + 内联 JS）
- Modify: `static/i18n/zh.json`
- Modify: `scripts/check_admin_user_management.py`（e2e 追加场景）

**Interfaces:**
- Consumes: Task 1 `database.count_user_rows(user_id, *, db_path)`、`database.delete_user(user_id, *, db_path)`、`is_last_admin(user_id, *, db_path)`、既有 `find_agent_user_by_token(token, db_path)`；`jsonify`（insights_web 已 import）。
- Produces: 路由 `GET /admin/users/<int:user_id>/stats` → `admin_user_stats`（JSON `{"username": str, "counts": {table: int}}`）；路由 `POST /admin/users/<int:user_id>/delete` → `admin_delete_user`（表单字段 `confirm_username`）；守卫顺序：目标不存在→404，自己→拒，`is_last_admin`→拒，确认串（strip 后）≠目标用户名→拒，全部通过才调 `database.delete_user`。`PUBLIC_ENDPOINTS` 增 `"admin_user_stats"`、`"admin_delete_user"`。

- [x] **Step 1: 追加 e2e 场景（预期失败）**

先为 victim 播数据：15 表各 1 行（同 data 节 helper，`db_path=database.DB_PATH`），`upsert_social_account`/`enqueue_agent_job`/`create_agent_token(victim, "victim-e2e-token")` 各一，`log_activity`/`log_usage` 各一；uploaded_images 播一行 cloudinary + 一行 `storage='local', url='/static/uploads/e2e-gone.png'`（磁盘上不存在——Review Focus 3 经路由复验）。

1. admin `GET /admin/users/<uid_v>/stats` → 200 JSON：`username == "victim"`、`counts` 键集 == 15 表、各值 == 1。
2. 不存在 id：admin `GET /admin/users/999999999/stats` 与 admin `POST /admin/users/999999999/delete` 均 → 404 且 body 与基线（GET 用 GET 基线、POST 用 POST 基线）逐字节一致；victim `GET .../stats` → 404 同 GET 基线（Review Focus 1 全覆盖三个接口的 404，而非 500）。
3. 确认不匹配：admin `POST .../<uid_v>/delete` `{"confirm_username": "wrong"}` → 302，页面含 `Username confirmation does not match`，库中 victim 行原样（15 表计数不变）。
4. 大小写不符：`{"confirm_username": "Victim"}` → 同样拒绝、零删除（Review Focus 2）。
5. 禁删自己：admin `POST .../<admin_id>/delete` `{"confirm_username": "gate"}` → 页面含 `You cannot delete your own account.`，users 行数不变（spec「禁止删除自己」；同时即「最后一个 admin 尝试删除仅剩的自己」场景——self 守卫先拦，`is_last_admin` 为纵深防御，其谓词已由 data 节钉死）。
6. 正式删除（strip 宽容在此验证）：admin `POST .../<uid_v>/delete` `{"confirm_username": " victim "}` → 302，页面含 `Deleted victim and`；库断言：15 表 `WHERE user_id = uid_v` 全 0、users 无 victim、activity_log/usage_events 原行仍在且两列均 NULL、`find_agent_user_by_token("victim-e2e-token") is None`（扩展配对立即失效）。
7. victim 文件断言：删除后不抛错即过（ghost 文件本不存在，失败路径仅日志——用 `caplog` 不可行则免检库外效果，data 节已钉真文件）。
8. victim 旧 session 失效：victim client `GET /` → 302 且 Location 含 `/login`（Review Focus 4：stale session 被既有中间件弹回登录）。
9. anon `POST /admin/users/999999999/delete` → 404 与 anon POST 基线逐字节一致（装饰器先于目标查询触发）；victim `POST` 同端点 → 404 同基线。

- [x] **Step 2: 跑 e2e 验证失败**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py e2e`
Expected: FAIL——场景 1 起 404（stats 路由不存在）。

- [x] **Step 3: 实现两条路由**

import 名单补 `is_last_admin`（`count_user_rows`/`delete_user` 保持 `database.` 前缀调用）；`PUBLIC_ENDPOINTS` 增 `"admin_user_stats"`、`"admin_delete_user"`；`admin_reset_password` 之后：

```python
@app.route("/admin/users/<int:user_id>/stats", methods=["GET"])
@require_admin_or_404
def admin_user_stats(user_id: int):
    user = get_user_by_id(user_id)
    if user is None:
        abort(404)
    return jsonify({"username": user["username"],
                    "counts": database.count_user_rows(user_id)})

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@require_admin_or_404
def admin_delete_user(user_id: int):
    user = get_user_by_id(user_id)
    if user is None:
        abort(404)
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.")
        return redirect(url_for("admin_users_page"))
    if is_last_admin(user_id):
        flash("This is the last admin — transfer admin first.")
        return redirect(url_for("admin_users_page"))
    if (request.form.get("confirm_username") or "").strip() != user["username"]:
        flash("Username confirmation does not match — nothing was deleted.")
        return redirect(url_for("admin_users_page"))
    total = sum(database.delete_user(user_id).values())
    flash(f"Deleted {user['username']} and {total} data rows.")
    return redirect(url_for("admin_users_page"))
```

（已知取舍：双 admin 并发互删存在极窄窗口，检查在各自事务内读 count，见 Review Focus 5——不加并发防护。）

- [x] **Step 4: 模板 Delete 按钮 + 确认弹层**

Actions 列在 Reset 表单旁加：

```jinja
<button type="button" class="btn btn-outline btn-sm"
        onclick="openDelete({{ u['id'] }}, '{{ u['username'] }}')">Delete</button>
```

`{% endblock %}` 前（`.pd` 容器内）加弹层与脚本：

```html
<div class="card" id="delModal" hidden style="position:fixed; inset:0; margin:auto;
     max-width:30rem; height:fit-content; z-index:1050; box-shadow:0 8px 40px rgba(0,0,0,.25)">
    <div class="card-head"><b>Delete user</b><span class="spacer"></span>
        <button type="button" class="btn btn-ghost btn-sm" onclick="delModal.hidden=true">Cancel</button></div>
    <div class="card-body">
        <p id="delWho" class="mb-2"></p>
        <p class="small mb-1">Rows to delete</p>
        <ul class="small" id="delCounts" style="columns:2"></ul>
        <div class="field mt-2">
            <label for="delConfirm">Type the username to confirm:</label>
            <input class="input" id="delConfirm" autocomplete="off">
        </div>
    </div>
    <div class="card-foot"><span class="spacer"></span>
        <form method="post" id="delForm">
            <input type="hidden" name="confirm_username" id="delConfirmField">
            <button class="btn btn-primary btn-sm" type="submit">Confirm delete</button>
        </form>
    </div>
</div>
<script>
async function openDelete(uid, uname) {
    const s = await fetch(`/admin/users/${uid}/stats`).then(r => r.json());
    delWho.textContent = `Delete ${s.username}? This cannot be undone.`;
    delCounts.innerHTML = Object.entries(s.counts)
        .filter(([, n]) => n > 0)
        .map(([t, n]) => `<li>${t}: ${n}</li>`).join("")
        || "<li>no data rows</li>";
    delConfirmField.value = ""; delConfirm.value = "";
    delForm.action = `/admin/users/${uid}/delete`;
    delModal.hidden = false;
}
</script>
```

（提交前由 `delConfirm` input 事件同步 `delConfirmField` 亦可——实现者可把同步逻辑加在 input 上，二选一，保持字段名 `confirm_username` 不变。）

- [x] **Step 5: zh.json 词条**

rules 末尾追加：

```json
  {"re": "^Delete$", "to": "删除"},
  {"re": "^Delete user$", "to": "删除用户"},
  {"re": "^Cancel$", "to": "取消"},
  {"re": "^Confirm delete$", "to": "确认删除"},
  {"re": "^Rows to delete$", "to": "将删除的数据"},
  {"re": "^Type the username to confirm:$", "to": "输入用户名以确认："},
  {"re": "^You cannot delete your own account\\.$", "to": "不能删除当前登录的账号。"},
  {"re": "^This is the last admin — transfer admin first\\.$", "to": "这是最后一个管理员，请先转让后再删除。"},
  {"re": "^Username confirmation does not match — nothing was deleted\\.$", "to": "用户名确认不一致，未执行删除。"},
  {"re": "^Deleted (.+) and (\\d+) data rows\\.$", "to": "已删除 $1 及其 $2 行数据。"}
```

（4 条 flash 动态规则同步进 e2e 的 zh.json 场景清单。）

- [x] **Step 6: 跑 e2e / data 验证通过**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py e2e` 与 `... data`
Expected: 两个 token 均打印、退出码 0。

- [x] **Step 7: Commit**

```bash
git add insights_web.py templates/admin_users.html static/i18n/zh.json scripts/check_admin_user_management.py
git commit -m "用户管理：删除用户单事务级联+提交后清文件，量级预览+用户名二次确认，禁删自己/末位 admin（404 门控同前）"
```

---

### Task 5: 全量回归与收尾

**Files:**
- 无新改动（纯验证；若回归暴露问题，回到对应任务修复后重跑本任务）

- [x] **Step 1: 新 gate 无参全节跑（子进程隔离）**

Run: `venv/Scripts/python.exe scripts/check_admin_user_management.py`
Expected: `data`、`e2e` 两节各自子进程通过，退出码 0，无 `FAILED SECTIONS`。

- [x] **Step 2: 相邻 gate 回归（改过 database.py / insights_web.py / base.html 的面）**

Run:
```bash
venv/Scripts/python.exe scripts/check_content_isolation.py schema
venv/Scripts/python.exe scripts/check_content_isolation.py data
venv/Scripts/python.exe scripts/check_content_isolation.py scan
venv/Scripts/python.exe scripts/check_content_isolation.py e2e
venv/Scripts/python.exe scripts/check_channel_isolation.py
venv/Scripts/python.exe scripts/check_settings_ui.py
venv/Scripts/python.exe scripts/check_accounts_ui.py
```
Expected: 各节 OK token（`CONTENT_ISOLATION_*_OK`、`CHANNEL_ISOLATION_*_OK`、settings/accounts UI gate 通过）——base.html 是公共骨架，凡断言侧边栏/页面结构的 gate 都必须过。

- [x] **Step 3: gate inventory 终检**

Run: `venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'scripts'); from run_all_gates import gate_inventory_problems; print(gate_inventory_problems())"`
Expected: `([], [])`。

- [x] **Step 4: 手工验收点（转述给 owner，不阻塞提交）**

- 被重置密码者旧 session 有效至自然过期（设计已接受并注明）。
- 「先转让再删」逃生门：`UPDATE users SET is_admin=1 WHERE username=?`（手工 SQL，已写入设计裁决 7）。

---

## 计划外（明确不做）

- promote/demote admin 的任何 UI 或路由（设计裁决 7）。
- 匿名保留删除选项、强制下线、邮箱/昵称/头像字段、`/settings` 403 语义变更（设计 Non-Goals）。
- 双 admin 并发互删的并发防护（Review Focus 5：接受，手工 SQL 可恢复）。
