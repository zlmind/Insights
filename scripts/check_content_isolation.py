"""多用户内容隔离 gate：schema / data / scan / e2e 四节，各打印独立 token。

    python scripts/check_content_isolation.py schema   CONTENT_ISOLATION_SCHEMA_OK
    python scripts/check_content_isolation.py data     CONTENT_ISOLATION_DATA_OK
    python scripts/check_content_isolation.py scan     CONTENT_ISOLATION_SCAN_OK
    python scripts/check_content_isolation.py e2e      CONTENT_ISOLATION_E2E_OK

``schema`` 钉迁移本身：临时库 init_db 连跑两遍（幂等），12 张内容表全部带
user_id 且无重复列；feeds/episodes/url_sources 各有 (url, user_id) 复合唯一
索引，uploaded_images 一并重建后建表 SQL 不再含 url 全局 UNIQUE；两个用户
对同一 URL 各得一行、同一用户内仍去重；手工构造的旧 schema 库（url
TEXT UNIQUE、无 user_id）升级后行数不变，且升级后的库立即允许第二个用户
持有同一 URL。

``data`` 钉数据层：双账号互不读、改、删对方的行（12 张内容表每表至少一条）；
bulk 删除混合传入 id 时只删调用者自己的；存量 NULL 归属行对 A、B 与 admin
三个视角均不可见且行保留在库；写入口缺 user_id 抛 ValueError；跨用户直访
与访问真不存在的资源在代表路由（/article/<id>、/sources/<id>）上返回逐字节
一致的 404。

``scan`` 钉源头：ast.parse 读 database.py，按函数收集 SQL 字面量（含
f-string 的字面片段，剔除 docstring——docstring 里写到 user_id 不算数）。
凡 SQL 触及 12 张内容表（SELECT...FROM / UPDATE / DELETE FROM / INSERT
INTO）的函数，其 SQL 文本必须出现 user_id；INSERT 还要求列清单里带
user_id。违例函数必须榜上有名（白名单），且白名单每一项都必须仍被命中——
某天 SQL 补上了过滤，名单条目就该删掉，防止豁免名单腐化。

``e2e`` 钉端到端：双账号（admin 首号 + 新账号 B）走真路由跑九个场景——
B 五页全空、直访他人四个详情路由 404 且响应体与不存在逐字节一致、同名
prompt 的帖子各自可见、存量 NULL 行双方不可见且仍在库、/enqueue 与 brief
跑批产物按操作者/主人归属且 auto_queue 真落排期归 brief 主人、
backfill-channels 非 admin 404 与不存在同响应体而 admin 可用、/activity
/usage 非 admin 固定本人视角而 admin 全景见双方、同一 URL 双用户
sources/feeds 各一行互不去重。迁移幂等
由 schema 节钉死，e2e 不重复。抓网页面的两处（sources POST、add feed）
在内存里替换 trafilatura / parse_feed_with_fallback，整节仍然不联网。

四节都在临时目录/纯内存上运行：不联网、不触碰真实 insights.db。
schema 节的核心断言抽在 ``_assert_content_schema``，scan 节抽在
``_scan_violations``，供 mutation 测试在临时库/内存里直接驱动；e2e 节
经 ``section_e2e`` 可直接驱动，mutation 同样在内存里做归属破坏。
"""

import ast
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime

# 中文诊断信息按 UTF-8 直出：run_all_gates 以 UTF-8 解码子进程输出，Windows
# 控制台默认代码页会把中文打成问号。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_SOURCE = os.path.join(REPO, "database.py")

FUTURE = "2099-01-01T00:00:00"
FUTURE_2 = "2099-06-01T00:00:00"

# 12 张内容表：_CONTENT_TABLES 11 表 ∪ content_briefs。
_CONTENT_TABLES = ("feeds", "episodes", "articles", "social_posts",
                   "standalone_posts", "url_sources", "uploaded_images",
                   "generated_thumbnails", "prompt_library",
                   "content_brief_runs", "scheduled_posts")
_SCAN_TABLES = _CONTENT_TABLES + ("content_briefs",)

# 出生即带 url TEXT UNIQUE、靠重建换复合唯一的四张表（迁移循环覆盖面）。
_REBUILT_TABLES = ("feeds", "episodes", "url_sources", "uploaded_images")
_COMPOSITE_UNIQUE = {
    "feeds": "idx_feeds_url_user",
    "episodes": "idx_episodes_url_user",
    "url_sources": "idx_url_sources_url_user",
}

# scan 节豁免名单：函数名 → 理由。前五条来自设计文档；后两条是实例级
# 时段/日限流计算——时段与每日上限是全实例共享资源，必须数上所有主人的
# pending 条目，只数一个人的会让两个用户排进同一格。
_SCAN_WHITELIST = {
    "get_due_content_briefs":
        "worker 队列：跨全部主人取到期 brief，行上的 user_id 决定该轮为谁跑",
    "get_content_brief_unscoped":
        "run_brief worker 入口：先按 id 取行，再按行上的 user_id 约束后续每步",
    "get_youtube_episodes_missing_channel":
        "元数据回填：只读缺 channel 的行，不碰归属",
    "set_episode_channel":
        "元数据回填：只写 channel，不碰归属",
    "get_pending_scheduled_posts":
        "发布侧全局队列：到期条目按全局时段消费，行上的 user_id 归属产物",
    "get_next_available_slot":
        "实例级时段分配：时段是共享资源，必须数所有主人的 pending 才不会撞格",
    "count_scheduled_posts_for_day":
        "实例级日限流：平台每日上限是共享预算，跨主人计数才是真实占用",
}

_SQL_PATTERNS = (
    ("SELECT", r"select\b[^;]*\bfrom\s+{t}\b"),
    ("UPDATE", r"update\s+{t}\b"),
    ("DELETE", r"delete\s+from\s+{t}\b"),
    ("INSERT", r"insert\s+(?:or\s+\w+\s+)?into\s+{t}\b"),
)


# ── schema 节 ────────────────────────────────────────────────────────────────

def _assert_content_schema(db_path):
    """12 表 user_id 在位、无重复列，复合唯一索引在位，重建表无全局 UNIQUE。

    schema 节与 mutation 测试共用的核心断言：前者对刚迁移完的临时库调用，
    后者对被故意破坏的临时库调用并期待它 fail。
    """
    with sqlite3.connect(db_path) as conn:
        for table in _SCAN_TABLES:
            cols = [r[1] for r in
                    conn.execute(f"PRAGMA table_info({table})").fetchall()]
            check(bool(cols), f"{table} 表不存在于 {db_path}")
            check("user_id" in cols, f"{table} 缺 user_id 列")
            check(len(cols) == len(set(cols)), f"{table} 出现重复列: {cols}")
        for table, index in _COMPOSITE_UNIQUE.items():
            names = {r[1] for r in
                     conn.execute(f"PRAGMA index_list({table})").fetchall()}
            check(index in names, f"{table} 缺复合唯一索引 {index}")
        for table in _REBUILT_TABLES:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            check(row is not None, f"{table} 表不存在")
            check(not re.search(r"url\s+TEXT\s+UNIQUE", row[0] or "", re.I),
                  f"{table} 建表 SQL 仍含 url 全局 UNIQUE")


def _count(db_path, table, column, value):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
            (value,)).fetchone()[0]


def _total(db_path, table):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# user_id 落地前的建表形状：_NEW_DDL 的列减去 user_id，加回 url TEXT UNIQUE。
_OLD_DDL = (
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        title TEXT,
        feed_type TEXT,
        last_post TEXT,
        item_count INTEGER,
        last_checked TEXT
    )
    """,
    """
    CREATE TABLE episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_id INTEGER,
        url TEXT UNIQUE,
        title TEXT,
        transcript TEXT,
        summary TEXT,
        action_items TEXT,
        status TEXT,
        published TEXT,
        processed_at TEXT,
        channel TEXT,
        FOREIGN KEY(feed_id) REFERENCES feeds(id)
    )
    """,
    """
    CREATE TABLE url_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        title TEXT,
        description TEXT,
        content TEXT,
        og_image TEXT,
        created_at TEXT,
        last_used_at TEXT
    )
    """,
    """
    CREATE TABLE uploaded_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        url TEXT UNIQUE,
        storage TEXT,
        size INTEGER,
        created_at TEXT
    )
    """,
)


def _build_old_schema_db(db_path):
    """按 user_id 落地前的真实形状造一个旧库，塞入种子行。

    返回每张重建表的期望行数：升级是搬运行，不是清库。
    """
    with sqlite3.connect(db_path) as conn:
        for ddl in _OLD_DDL:
            conn.execute(ddl)
        conn.execute("INSERT INTO users (username, password_hash, is_admin) "
                     "VALUES ('legacy', 'x', 1)")
        conn.execute("INSERT INTO feeds (url, title, last_checked) "
                     "VALUES ('https://old.example/a.xml', 'A', '2020-01-01')")
        conn.execute("INSERT INTO feeds (url, title, last_checked) "
                     "VALUES ('https://old.example/b.xml', 'B', '2020-01-01')")
        conn.execute("INSERT INTO episodes (feed_id, url, title, status, channel) "
                     "VALUES (1, 'https://old.example/a.mp3', 'EA', 'complete', "
                     "'Legacy Channel')")
        conn.execute("INSERT INTO url_sources (url, title, created_at) "
                     "VALUES ('https://old.example/page', 'P', '2020-01-01')")
        conn.execute("INSERT INTO uploaded_images (filename, url, storage) "
                     "VALUES ('o.png', 'https://old.example/o.png', 'local')")
        conn.commit()
    return {"feeds": 2, "episodes": 1, "url_sources": 1, "uploaded_images": 1}


def section_schema():
    import database

    directory = tempfile.mkdtemp(prefix="isolation_schema_")
    db_path = os.path.join(directory, "insights.db")

    # 双跑：第二次 init_db 不得新增列、不得破坏任何断言。
    database.init_db(db_path)
    database.init_db(db_path)
    _assert_content_schema(db_path)

    # 双用户同 URL 各一行；同一用户内仍去重（复合唯一键的两面）。
    uid_a = database.create_user("gate", generate_password_hash("p"),
                                 db_path=db_path)
    uid_b = database.create_user("other", generate_password_hash("p"),
                                 db_path=db_path)
    shared = "https://example.com/shared"

    feed_a = database.add_feed(shared, "A", user_id=uid_a, db_path=db_path)
    feed_b = database.add_feed(shared, "B", user_id=uid_b, db_path=db_path)
    check(feed_a != feed_b, f"feeds 双用户同 URL 应各得一行（{feed_a}/{feed_b}）")
    check(database.add_feed(shared, "A2", user_id=uid_a, db_path=db_path) == feed_a,
          "feeds 同用户同 URL 仍应去重")
    check(_count(db_path, "feeds", "url", shared) == 2,
          "feeds 同一 URL 应恰有两行（每用户一行）")

    database.queue_episode(shared, "ep", feed_a, user_id=uid_a, db_path=db_path)
    database.queue_episode(shared, "ep", feed_b, user_id=uid_b, db_path=db_path)
    check(_count(db_path, "episodes", "url", shared) == 2,
          "episodes 双用户同 URL 应各一行")

    database.add_url_source(shared, "t", "d", "c", uid_a, db_path=db_path)
    database.add_url_source(shared, "t", "d", "c", uid_b, db_path=db_path)
    check(_count(db_path, "url_sources", "url", shared) == 2,
          "url_sources 双用户同 URL 应各一行")

    database.add_uploaded_image("a.png", shared, "cloudinary", uid_a,
                                db_path=db_path)
    database.add_uploaded_image("a.png", shared, "cloudinary", uid_b,
                                db_path=db_path)
    check(_count(db_path, "uploaded_images", "url", shared) == 2,
          "uploaded_images 双用户同 URL 应各一行（评审修复：它也重建了）")

    # 旧 schema 库升级：行数不变；升级后的库立即允许第二个用户持有同一 URL。
    old_path = os.path.join(directory, "old.db")
    seeded = _build_old_schema_db(old_path)
    database.init_db(old_path)
    _assert_content_schema(old_path)
    for table, expected in seeded.items():
        check(_total(old_path, table) == expected,
              f"旧库升级后 {table} 行数应为 {expected}，实为 {_total(old_path, table)}")
    # 重建不得丢列：存量库的 channel 是升级链先补上的列，_NEW_DDL/_COPY_COLS
    # 漏掉它就会在重建时连数据一起静默蒸发，随后 YouTube 链路 no such column。
    with sqlite3.connect(old_path) as conn:
        ep_cols = [r[1] for r in
                   conn.execute("PRAGMA table_info(episodes)").fetchall()]
        kept = conn.execute(
            "SELECT channel FROM episodes WHERE url = 'https://old.example/a.mp3'"
        ).fetchone()
        ep_id = conn.execute(
            "SELECT id FROM episodes WHERE url = 'https://old.example/a.mp3'"
        ).fetchone()[0]
    check("channel" in ep_cols, "旧库升级后 episodes 应仍带 channel 列")
    check(kept == ("Legacy Channel",),
          f"旧库升级后 channel 值应保留，实为 {kept!r}")
    # 回填白名单两函数照常可跑：列若被丢，SELECT/UPDATE channel 直接抛
    # no such column。
    check(database.get_youtube_episodes_missing_channel(db_path=old_path) == [],
          "升级库上 channel 回填查询应可运行（旧种子非 YouTube，命中 0 行）")
    database.set_episode_channel(ep_id, "Backfilled Channel", db_path=old_path)
    check(_count(old_path, "episodes", "channel", "Backfilled Channel") == 1,
          "升级库上 set_episode_channel 应可写入")
    newcomer = database.create_user("newcomer", generate_password_hash("p"),
                                    db_path=old_path)
    database.add_feed("https://old.example/a.xml", "newcomer",
                      user_id=newcomer, db_path=old_path)
    check(_count(old_path, "feeds", "url", "https://old.example/a.xml") == 2,
          "升级后的库应允许第二个用户持有旧库已有的 URL")
    before_again = {table: _total(old_path, table) for table in seeded}
    database.init_db(old_path)
    _assert_content_schema(old_path)
    for table, expected in before_again.items():
        check(_total(old_path, table) == expected,
              f"升级库二次 init_db 后 {table} 行数不变（应 {expected}，"
              f"实为 {_total(old_path, table)}）")

    print("迁移幂等：12 表带 user_id，feeds/episodes/url_sources 复合唯一，"
          "uploaded_images 一并去 UNIQUE，同 URL 双用户各一行，旧库升级行数不变，"
          "episodes channel 列与值跨重建保留且回填函数可跑")
    print("CONTENT_ISOLATION_SCHEMA_OK")


# ── data 节 ──────────────────────────────────────────────────────────────────

def section_data():
    directory, database, web, publisher, client = isolated_app()
    admin_id = database.get_user_by_username("gate")["id"]  # 首号即 admin
    other_id = database.create_user("colleague", generate_password_hash("p"),
                                    db_path=database.DB_PATH)
    root_id = database.create_user("root", generate_password_hash("p"),
                                   db_path=database.DB_PATH)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (root_id,))
        conn.commit()
    check(database.get_user_by_id(root_id, db_path=database.DB_PATH)["is_admin"] == 1,
          "root 账号应被置为 admin")

    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = other_id

    a = dict(user_id=admin_id, db_path=database.DB_PATH)
    b = dict(user_id=other_id, db_path=database.DB_PATH)

    def total(table):
        return _total(database.DB_PATH, table)

    def row_field(table, row_id, column):
        with sqlite3.connect(database.DB_PATH) as conn:
            r = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?",
                             (row_id,)).fetchone()
        return None if r is None else r[0]

    # ── 1) B 直访/改/删 A 的行 → None/False/0，行仍在 ──
    feed_a = database.add_feed("https://a.example/feed.xml", "A feed", **a)
    check(database.get_feed_by_id(feed_a, **b) is None, "B 直访 A 的 feed 应得 None")
    database.update_feed_metadata(feed_a, "rss", "hijacked", 99, **b)
    check(row_field("feeds", feed_a, "last_post") is None,
          "B 改 A 的 feed 元数据不得生效")
    database.delete_feed(feed_a, **b)
    check(total("feeds") == 1, "B 删 A 的 feed 不得生效，行仍在")

    database.queue_episode("https://a.example/ep.mp3", "A ep", feed_a, **a)
    ep_a = database.get_episode("https://a.example/ep.mp3", **a)["id"]
    check(database.get_episode_by_id(ep_a, **b) is None,
          "B 直访 A 的 episode 应得 None")
    database.update_episode_status("https://a.example/ep.mp3", "complete", **b)
    check(row_field("episodes", ep_a, "status") == "queued",
          "B 改 A 的 episode 状态不得生效")
    database.delete_episode_by_id(ep_a, **b)
    check(total("episodes") == 1, "B 删 A 的 episode 不得生效，行仍在")

    art_a = database.add_article(None, "A topic", "blog", "A body", **a)
    check(database.get_article(art_a, **b) is None, "B 直访 A 的 article 应得 None")
    check(database.list_articles(user_id=other_id,
                                 db_path=database.DB_PATH) == [],
          "B 的 article 列表应为空")
    database.update_article(art_a, **b, content="hijacked")
    check(row_field("articles", art_a, "content") == "A body",
          "B 改 A 的 article 不得生效")
    database.delete_article(art_a, **b)
    check(total("articles") == 1, "B 删 A 的 article 不得生效，行仍在")

    sp_a = database.add_social_post(art_a, "linkedin", "A post", **a)
    check(database.get_social_post(sp_a, **b) is None,
          "B 直访 A 的 social post 应得 None")
    database.update_social_post(sp_a, "hijacked", **b)
    check(row_field("social_posts", sp_a, "content") == "A post",
          "B 改 A 的 social post 不得生效")
    database.delete_social_post(sp_a, **b)
    check(total("social_posts") == 1, "B 删 A 的 social post 不得生效，行仍在")

    st_a = database.add_standalone_post("freeform", "A prompt", "xiaohongshu",
                                        "A copy", **a)
    check(database.get_standalone_post(st_a, **b) is None,
          "B 直访 A 的 standalone post 应得 None")
    database.update_standalone_post(st_a, **b, content="hijacked")
    check(row_field("standalone_posts", st_a, "content") == "A copy",
          "B 改 A 的 standalone post 不得生效")
    database.mark_standalone_post_used(st_a, True, **b)
    check(row_field("standalone_posts", st_a, "used") == 0,
          "B 标记 A 的 standalone post 为已用不得生效")
    database.delete_standalone_post(st_a, **b)
    check(total("standalone_posts") == 1, "B 删 A 的 standalone post 不得生效，行仍在")

    src_a = database.add_url_source("https://a.example/page", "A page", "d",
                                    "A body", **a)
    check(database.get_url_source(src_a, **b) is None,
          "B 直访 A 的 url source 应得 None")
    last_used = row_field("url_sources", src_a, "last_used_at")
    database.update_url_source_last_used(src_a, **b)
    check(row_field("url_sources", src_a, "last_used_at") == last_used,
          "B 触碰 A 的 source 计时不得生效")
    check(database.update_url_source_content(src_a, **b, title="hax",
                                             description="hax",
                                             content="hax") is False,
          "B 重取 A 的 source 内容应返回 False")
    check(database.delete_url_source(src_a, **b) is False,
          "B 删 A 的 source 应返回 False")

    img_a = database.add_uploaded_image("a.png", "https://cdn.example/a.png",
                                        "cloudinary", **a)
    check(database.get_uploaded_image(img_a, **b) is None,
          "B 直访 A 的 uploaded image 应得 None")
    check(database.delete_uploaded_image(img_a, **b) is False,
          "B 删 A 的 uploaded image 应返回 False")

    th_a = database.add_generated_thumbnail(
        "https://www.youtube.com/watch?v=a", "a", "T", "C", "16:9", "s", "p",
        "thumbs/a.png", **a)
    check(database.get_generated_thumbnail(th_a, **b) is None,
          "B 直访 A 的 thumbnail 应得 None")
    check(database.delete_generated_thumbnail(th_a, **b) is False,
          "B 删 A 的 thumbnail 应返回 False")

    pr_a = database.add_library_prompt("A title", "A body", **a)
    check(database.get_library_prompt(pr_a, **b) is None,
          "B 直访 A 的 library prompt 应得 None")
    check(database.update_library_prompt(pr_a, **b, title="hax",
                                         content="hax") == 0,
          "B 改 A 的 library prompt 应影响 0 行")
    check(database.delete_library_prompt(pr_a, **b) == 0,
          "B 删 A 的 library prompt 应影响 0 行")

    br_a = database.create_content_brief("A brief", "instructions", **a)
    check(database.get_content_brief(br_a, **b) is None,
          "B 直访 A 的 brief 应得 None")
    database.update_content_brief(br_a, **b, name="hijacked")
    check(row_field("content_briefs", br_a, "name") == "A brief",
          "B 改 A 的 brief 不得生效")
    database.set_content_brief_enabled(br_a, **b, enabled=False)
    check(row_field("content_briefs", br_a, "enabled") == 1,
          "B 停用 A 的 brief 不得生效")
    database.delete_content_brief(br_a, **b)
    check(total("content_briefs") == 1, "B 删 A 的 brief 不得生效，行仍在")

    run_a = database.create_brief_run(br_a, **a)
    check(database.get_brief_run(run_a, **b) is None,
          "B 直访 A 的 brief run 应得 None")
    database.finalize_brief_run(run_a, **b, status="success")
    check(row_field("content_brief_runs", run_a, "status") == "running",
          "B 结束 A 的 brief run 不得生效")

    sch_a = database.add_scheduled_post(FUTURE, "standalone", **a,
                                        standalone_post_id=st_a)
    check(database.get_scheduled_post(sch_a, **b) is None,
          "B 直访 A 的 scheduled post 应得 None")
    check(database.update_scheduled_post_time(
        sch_a, **b, scheduled_for="2098-01-01T00:00:00") is False,
        "B 改 A 的排期时间应返回 False")
    check(database.cancel_scheduled_post(sch_a, **b) is False,
          "B 取消 A 的排期应返回 False")
    check(database.increment_retry_count(sch_a, **b) == 0,
          "B 递增 A 的重试计数应得 0")
    database.delete_scheduled_post(sch_a, **b)
    check(total("scheduled_posts") == 1, "B 删 A 的排期不得生效，行仍在")

    # ── 2) bulk 混合传入 id，只删调用者自己的 ──
    feed_b = database.add_feed("https://b.example/feed.xml", "B feed", **b)
    check(database.delete_feeds_bulk([feed_a, feed_b], **b) == 1,
          "feeds bulk 只应删掉 B 自己的一行")
    check(total("feeds") == 1, "A 的 feed 必须在 B 的 bulk 删除后幸存")

    database.queue_episode("https://b.example/ep.mp3", "B ep", feed_b, **b)
    ep_b = database.get_episode("https://b.example/ep.mp3", **b)["id"]
    check(database.delete_episodes_bulk([ep_a, ep_b], **b) == 1,
          "episodes bulk 只应删掉 B 自己的一行")
    check(total("episodes") == 1, "A 的 episode 必须在 B 的 bulk 删除后幸存")

    art_b = database.add_article(None, "B topic", "blog", "B body", **b)
    sp_b = database.add_social_post(art_b, "linkedin", "B post", **b)
    check(database.delete_social_posts_bulk([sp_a, sp_b], **b) == 1,
          "social posts bulk 只应删掉 B 自己的一行")
    check(total("social_posts") == 1, "A 的 social post 必须幸存")

    st_b = database.add_standalone_post("freeform", "B prompt", "xiaohongshu",
                                        "B copy", **b)
    check(database.delete_standalone_posts_bulk([st_a, st_b], **b) == 1,
          "standalone posts bulk 只应删掉 B 自己的一行")
    check(total("standalone_posts") == 1, "A 的 standalone post 必须幸存")

    sch_b = database.add_scheduled_post(FUTURE_2, "standalone", **b,
                                        standalone_post_id=st_b)
    check(database.delete_scheduled_posts_bulk([sch_a, sch_b], **b) == 1,
          "scheduled posts bulk 只应删掉 B 自己的一行")
    check(total("scheduled_posts") == 1, "A 的排期必须在 B 的 bulk 删除后幸存")

    # ── 3) 写入口缺 user_id 抛 ValueError ──
    missing_uid_cases = (
        ("_require_user", lambda: database._require_user(None)),
        ("add_feed", lambda: database.add_feed("https://x.example/f", "t",
                                               user_id=None)),
        ("save_episode", lambda: database.save_episode(
            "https://x.example/e", "t", "tr", "s", [], 1, user_id=None)),
        ("queue_episode", lambda: database.queue_episode(
            "https://x.example/e", "t", 1, user_id=None)),
        ("add_article", lambda: database.add_article(None, "t", "s", "c",
                                                     user_id=None)),
        ("add_social_post", lambda: database.add_social_post(
            art_a, "linkedin", "c", user_id=None)),
        ("add_standalone_post", lambda: database.add_standalone_post(
            "freeform", "p", "xiaohongshu", "c", user_id=None)),
        ("mark_standalone_post_used", lambda: database.mark_standalone_post_used(
            st_a, True, user_id=None)),
        ("add_url_source", lambda: database.add_url_source(
            "https://x.example/p", "t", "d", "c", user_id=None)),
        ("add_uploaded_image", lambda: database.add_uploaded_image(
            "f.png", "https://x.example/a.png", "local", user_id=None)),
        ("add_generated_thumbnail", lambda: database.add_generated_thumbnail(
            "https://www.youtube.com/watch?v=x", "x", "T", "C", "16:9", "s",
            "p", "thumbs/x.png", user_id=None)),
        ("add_library_prompt", lambda: database.add_library_prompt(
            "t", "c", user_id=None)),
        ("create_content_brief", lambda: database.create_content_brief(
            "n", "i", user_id=None)),
        ("create_brief_run", lambda: database.create_brief_run(br_a,
                                                               user_id=None)),
        ("add_scheduled_post", lambda: database.add_scheduled_post(
            FUTURE, "standalone", user_id=None)),
    )
    for label, call in missing_uid_cases:
        try:
            call()
        except ValueError:
            continue
        check(False, f"写入口 {label} 缺 user_id 未抛 ValueError")

    # ── 4) NULL 归属行对 A/B/admin 均不可见，行保留在库 ──
    feed2 = database.add_feed("https://null.example/feed.xml", "null feed", **a)
    database.queue_episode("https://null.example/ep.mp3", "n ep", feed2, **a)
    ep2 = database.get_episode("https://null.example/ep.mp3", **a)["id"]
    art2 = database.add_article(None, "n topic", "blog", "n body", **a)
    sp2 = database.add_social_post(art2, "linkedin", "n post", **a)
    st2 = database.add_standalone_post("freeform", "n prompt", "xiaohongshu",
                                       "n copy", **a)
    src2 = database.add_url_source("https://null.example/page", "n page", "d",
                                   "n body", **a)
    img2 = database.add_uploaded_image("n.png", "https://cdn.example/n.png",
                                       "cloudinary", **a)
    th2 = database.add_generated_thumbnail(
        "https://www.youtube.com/watch?v=n", "n", "T", "C", "16:9", "s", "p",
        "thumbs/n.png", **a)
    pr2 = database.add_library_prompt("n title", "n body", **a)
    br2 = database.create_content_brief("n brief", "i", **a)
    run2 = database.create_brief_run(br2, **a)
    sch2 = database.add_scheduled_post(FUTURE, "standalone", **a,
                                       standalone_post_id=st2)
    nulls = {"feeds": feed2, "episodes": ep2, "articles": art2,
             "social_posts": sp2, "standalone_posts": st2,
             "url_sources": src2, "uploaded_images": img2,
             "generated_thumbnails": th2, "prompt_library": pr2,
             "content_briefs": br2, "content_brief_runs": run2,
             "scheduled_posts": sch2}
    with sqlite3.connect(database.DB_PATH) as conn:
        for table, row_id in nulls.items():
            conn.execute(f"UPDATE {table} SET user_id = NULL WHERE id = ?",
                         (row_id,))
        conn.commit()

    for viewer in (admin_id, other_id, root_id):
        v = dict(user_id=viewer, db_path=database.DB_PATH)
        for row_id, getter in (
                (feed2, database.get_feed_by_id),
                (ep2, database.get_episode_by_id),
                (art2, database.get_article),
                (sp2, database.get_social_post),
                (st2, database.get_standalone_post),
                (src2, database.get_url_source),
                (img2, database.get_uploaded_image),
                (th2, database.get_generated_thumbnail),
                (pr2, database.get_library_prompt),
                (br2, database.get_content_brief),
                (run2, database.get_brief_run),
                (sch2, database.get_scheduled_post)):
            check(getter(row_id, **v) is None,
                  f"视角 {viewer} 对 {getter.__name__} 的 NULL 归属行应不可见")
    with sqlite3.connect(database.DB_PATH) as conn:
        for table, row_id in nulls.items():
            kept = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE id = ? AND user_id IS NULL",
                (row_id,)).fetchone()[0]
            check(kept == 1, f"{table} 的 NULL 行应仍在库且保持无主")
    # 正对照：A 自己仍看得到自己的行。
    check(database.get_feed_by_id(feed_a, **a) is not None,
          "正对照：A 仍能看到自己的 feed")
    check(database.get_article(art_a, **a) is not None,
          "正对照：A 仍能看到自己的 article")
    check(database.get_scheduled_post(sch_a, **a) is not None,
          "正对照：A 仍能看到自己的排期")

    # ── 5) 跨用户与不存在：404 且响应逐字节一致 ──
    missing = 999999999
    r_other = client_b.get(f"/article/{art_a}")
    r_missing = client_b.get(f"/article/{missing}")
    check(r_other.status_code == 404 and r_missing.status_code == 404,
          f"/article 跨用户与缺失都应 404（{r_other.status_code}/{r_missing.status_code}）")
    check(r_other.get_data() == r_missing.get_data(),
          "/article 的跨用户 404 响应必须与不存在逐字节一致")
    s_other = client_b.get(f"/sources/{src_a}")
    s_missing = client_b.get(f"/sources/{missing}")
    check(s_other.status_code == 404 and s_missing.status_code == 404,
          f"/sources 跨用户与缺失都应 404（{s_other.status_code}/{s_missing.status_code}）")
    check(s_other.get_data() == s_missing.get_data(),
          "/sources 的跨用户 404 响应必须与不存在逐字节一致")

    print("双账号互不读写删，bulk 只删自己的，NULL 存量对含 admin 的所有视角"
          "不可见且行保留，写入口缺 user_id 抛 ValueError，404 与不存在逐字节一致")
    print("CONTENT_ISOLATION_DATA_OK")


# ── scan 节 ──────────────────────────────────────────────────────────────────

def _sql_blob(fn):
    """函数体内全部 SQL 字面量的拼接，供模式匹配。

    f-string 按字面片段拼回（插值表达式丢弃）——``f"...IN ({placeholders})
    AND user_id = ?"`` 的 user_id 落在片段尾部，拆开逐段看会误报。docstring
    剔除——注释里写到 user_id 不等于 SQL 里过滤了 user_id。
    """
    doc = None
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        doc = fn.body[0].value
    parts = []
    for node in ast.walk(fn):
        if node is doc:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            parts.extend(v.value for v in node.values
                         if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return "\n".join(parts)


def _scan_violations(source):
    """返回 [(函数名, [命中说明])]：SQL 触及内容表但全函数无 user_id 的，与
    INSERT 列清单缺 user_id 的。

    scan 节与 mutation 测试共用的核心：后者传入篡改过的源文本并期待违例。
    """
    tree = ast.parse(source)
    violations = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        blob = _sql_blob(fn)
        lowered = blob.lower()
        hits = set()
        for table in _SCAN_TABLES:
            for kind, pattern in _SQL_PATTERNS:
                if not re.search(pattern.replace("{t}", table), blob, re.I):
                    continue
                if "user_id" not in lowered:
                    hits.add(f"{kind} {table}：SQL 文本无 user_id")
                elif kind == "INSERT":
                    for m in re.finditer(
                            r"insert\s+(?:or\s+\w+\s+)?into\s+" + table
                            + r"\s*\(([^)]*)\)", blob, re.I):
                        if "user_id" not in m.group(1).lower():
                            hits.add(f"INSERT {table}：列清单缺 user_id")
        if hits:
            violations.append((fn.name, sorted(hits)))
    return violations


def section_scan():
    with open(DATABASE_SOURCE, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    defined = {fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef)}

    violations = _scan_violations(source)
    flagged = {name for name, _ in violations}

    unknown = sorted(set(_SCAN_WHITELIST) - defined)
    check(not unknown,
          f"白名单里的函数在 database.py 中不存在（名字打错了？）: {unknown}")
    stale = sorted(set(_SCAN_WHITELIST) - flagged)
    check(not stale,
          f"白名单条目已无违例可豁免——该删名单或核对 SQL: {stale}")

    offenders = [(name, hits) for name, hits in violations
                 if name not in _SCAN_WHITELIST]
    for name, hits in offenders:
        print(f"VIOLATION {name}: {'; '.join(hits)}")
    check(not offenders,
          f"{len(offenders)} 个函数的 SQL 触及内容表却无 user_id 且不在白名单")

    print(f"scan: {len(flagged)} 个函数被命中，白名单 {len(_SCAN_WHITELIST)} 项"
          f"全部仍被命中（名单无死条目），12 张内容表 INSERT 均带 user_id 列")
    print("CONTENT_ISOLATION_SCAN_OK")


# ── e2e 节 ───────────────────────────────────────────────────────────────────

def section_e2e():
    directory, database, web, publisher, client = isolated_app()

    # ── 场景 1：admin（首号；create_user 无 is_admin 参数，直接 SQL 置 1）
    # + 新账号 B。──
    admin_id = database.get_user_by_username("gate")["id"]
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin_id,))
        conn.commit()
    check(database.get_user_by_id(admin_id, db_path=database.DB_PATH)["is_admin"] == 1,
          "首号 gate 应被置为 admin")
    uid_b = database.create_user("e2e_b", generate_password_hash("p"),
                                 db_path=database.DB_PATH)
    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = uid_b
    print("INFO e2e 1/9 admin 首号 + 新账号 B 就绪")

    a = dict(user_id=admin_id, db_path=database.DB_PATH)
    b = dict(user_id=uid_b, db_path=database.DB_PATH)

    def page(viewer, url):
        response = viewer.get(url)
        check(response.status_code == 200,
              f"GET {url} 应 200，实为 {response.status_code}")
        return response.get_data(as_text=True)

    # admin 种子数据：每个列表页一枚独特标记，B 的任何页面一枚都不许见到。
    feed_a = database.add_feed("https://admin.example/feed.xml",
                               "ZXE2EFEED admin feed", **a)
    art_a = database.add_article(None, "ZXE2EART admin topic", "blog",
                                 "ZXE2EART admin body", **a)
    post_a = database.add_standalone_post("freeform", "ZXE2EPROMPT", "xiaohongshu",
                                          "ZXE2EPOST admin copy", **a)
    database.create_content_brief("ZXE2EBRIEF admin brief", "i", **a)
    src_a = database.add_url_source("https://admin.example/page", "ZXE2ESRC page",
                                    "d", "admin body", **a)
    now = datetime.now()
    database.add_scheduled_post("%04d-%02d-28T09:00:00" % (now.year, now.month),
                                "standalone", **a, standalone_post_id=post_a)

    # ── 场景 2：B 登录后五页全空；正对照 admin 自己可见。──
    markers = ("ZXE2EFEED", "ZXE2EART", "ZXE2EPOST", "ZXE2EBRIEF", "ZXE2ESRC")
    for url in ("/", "/articles", "/compose", "/briefs", "/schedule"):
        text = page(client_b, url)
        for marker in markers:
            check(marker not in text, f"B 的 {url} 泄漏了 admin 种子标记 {marker}")
    check("ZXE2EFEED" in page(client, "/"), "正对照：admin 首页应见自己的 feed")
    check("ZXE2EPOST" in page(client, "/compose"),
          "正对照：admin 的 /compose 应见自己的帖")
    print("INFO e2e 2/9 B 五页全空，admin 种子零泄漏（admin 正对照可见）")

    # ── 场景 3：B 直访 admin 的四个详情路由 → 404，且与访问不存在的 id
    # 响应体逐字节一致（不泄露存在性）。──
    missing = 999999999
    for template, row_id in (("/article/{}", art_a), ("/sources/{}", src_a),
                             ("/feed/{}", feed_a), ("/compose/post/{}", post_a)):
        r_other = client_b.get(template.format(row_id))
        r_none = client_b.get(template.format(missing))
        check(r_other.status_code == 404 and r_none.status_code == 404,
              f"{template} 跨用户与缺失都应 404"
              f"（{r_other.status_code}/{r_none.status_code}）")
        check(r_other.get_data() == r_none.get_data(),
              f"{template} 的跨用户 404 响应必须与不存在逐字节一致")
    print("INFO e2e 3/9 直访他人 article/sources/feed/compose post 均 404 且与不存在同响应体")

    # ── 场景 4：双账号各建同名 prompt 的帖子，/compose 各自只见自己的。──
    database.add_standalone_post("freeform", "ZXE2ESHAREDPROMPT 同名提示",
                                 "xiaohongshu", "ZXE2EACOPY A 的同名帖", **a)
    database.add_standalone_post("freeform", "ZXE2ESHAREDPROMPT 同名提示",
                                 "xiaohongshu", "ZXE2EBCOPY B 的同名帖", **b)
    compose_a = page(client, "/compose")
    compose_b = page(client_b, "/compose")
    check("ZXE2EACOPY" in compose_a and "ZXE2EBCOPY" not in compose_a,
          "同名 prompt 下 A 的 /compose 只应见自己的帖")
    check("ZXE2EBCOPY" in compose_b and "ZXE2EACOPY" not in compose_b,
          "同名 prompt 下 B 的 /compose 只应见自己的帖")
    print("INFO e2e 4/9 同名 prompt 双账号各见各帖")

    # ── 场景 5：存量 NULL——种子行后 SQL 置 user_id=NULL，双方列表/详情
    # 均不可见，行仍在库。──
    null_post = database.add_standalone_post("freeform", "ZXE2ENULL prompt",
                                             "xiaohongshu", "ZXE2ENULLPOST 无主帖",
                                             **a)
    null_feed = database.add_feed("https://null.example/e2e.xml", "ZXE2ENULLFEED", **a)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE standalone_posts SET user_id = NULL WHERE id = ?",
                     (null_post,))
        conn.execute("UPDATE feeds SET user_id = NULL WHERE id = ?", (null_feed,))
        conn.commit()
    for viewer, who in ((client, "admin"), (client_b, "B")):
        check("ZXE2ENULLPOST" not in page(viewer, "/compose"),
              f"{who} 的 /compose 不得出现无主帖")
        check("ZXE2ENULLFEED" not in page(viewer, "/"),
              f"{who} 的首页不得出现无主 feed")
        r = viewer.get(f"/compose/post/{null_post}")
        check(r.status_code == 404, f"{who} 直访无主帖详情应 404（{r.status_code}）")
    with sqlite3.connect(database.DB_PATH) as conn:
        kept = (conn.execute("SELECT COUNT(*) FROM standalone_posts "
                             "WHERE id = ? AND user_id IS NULL",
                             (null_post,)).fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM feeds "
                             "WHERE id = ? AND user_id IS NULL",
                             (null_feed,)).fetchone()[0])
    check(kept == (1, 1), f"无主行必须仍在库且保持无主（{kept}）")
    print("INFO e2e 5/9 存量 NULL 对双方不可见且行仍在库")

    # ── 场景 6：worker 产物归属——admin/B 各自 /enqueue 入队 episode 归各自；
    # B 借 admin 的 feed_id 入队 404；brief 跑批产物归 brief 主人（Task 13 的
    # 假 generate 路径：不联网、不跑真模型）。──
    check(client.get(f"/enqueue?url=https://admin.example/ep.mp3"
                     f"&title=ZXE2EEP&feed_id={feed_a}").status_code == 302,
          "admin 入队自己的 feed 应 302")
    ep_a_row = database.get_episode("https://admin.example/ep.mp3", **a)
    check(ep_a_row is not None and ep_a_row["user_id"] == admin_id,
          "admin 入队的 episode 应归属 admin")
    check(client_b.get(f"/enqueue?url=https://x.example/e.mp3&title=t"
                       f"&feed_id={feed_a}").status_code == 404,
          "B 借 admin 的 feed_id 入队应 404")
    feed_b = database.add_feed("https://b.example/own.xml", "B own feed", **b)
    check(client_b.get(f"/enqueue?url=https://b.example/ep.mp3&title=ZXE2EEPB"
                       f"&feed_id={feed_b}").status_code == 302,
          "B 入队自己的 feed 应 302")
    ep_b_row = database.get_episode("https://b.example/ep.mp3", **b)
    check(ep_b_row is not None and ep_b_row["user_id"] == uid_b,
          "B 入队的 episode 应归属 B")

    import content_agent
    import insights
    import research_engine
    research_engine.run_research = lambda brief, use_local=False: type(
        "R", (), {"items": [], "warnings": ["no_candidates"], "query_plan": []})()
    insights.generate_posts_from_prompt = lambda **kw: {
        "wechat": "ZXE2EBRIEFGEN 生成的帖子"}
    bid_b = database.create_content_brief("ZXE2ERUN brief", "do posts",
                                          content_type="posts", **b)
    summary = content_agent.run_brief(bid_b, trigger="scheduled")
    check(summary.get("posts_created") == 1, f"brief 跑批应产 1 帖（{summary}）")
    owned = [row for row in
             database.list_standalone_posts(uid_b, db_path=database.DB_PATH)
             if row["brief_id"] == bid_b]
    check(len(owned) == 1 and owned[0]["user_id"] == uid_b,
          "brief 跑批产物应恰 1 帖且归属 brief 主人 B")
    check(all(row["brief_id"] != bid_b
              for row in database.list_standalone_posts(admin_id,
                                                        db_path=database.DB_PATH)),
          "brief 跑批不得产出 admin 视角的孤儿帖")

    # ── auto_queue 接线（终审 I1 回归钉）：开启 auto_queue 的 brief 跑批后必须
    # 真落排期且排期行归 brief 主人。此前 _auto_queue 里 get_standalone_post
    # 少传 user_id 的 TypeError 被 run_brief 的 except 吞掉，自动排期静默失效。
    # SCHEDULABLE 生产上刻意为空（国内平台暂无发布 API），内存里临时放开一个
    # 平台只为止于驱动这条链路；时段先清空再放一枚 (今天+3) 星期位的远端格，
    # 下一次出现必在 2-3 天后，稳过默认 24h 审查窗且不受其他槽位干扰。──
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("DELETE FROM time_slot_platforms")
        conn.execute("DELETE FROM schedule_time_slots")
        conn.commit()
    far_dow = (datetime.now().weekday() + 3) % 7
    database.add_time_slot(day_of_week=far_dow, time_slot="23:00", enabled=True,
                           db_path=database.DB_PATH)
    saved_schedulable = content_agent.SCHEDULABLE
    content_agent.SCHEDULABLE = {"wechat"}
    try:
        bid_q = database.create_content_brief(
            "ZXE2EQUEUE brief", "do posts", content_type="posts",
            platforms=["wechat"], auto_queue=True, **b)
        summary_q = content_agent.run_brief(bid_q, trigger="scheduled")
        check(summary_q.get("queued") == 1,
              f"auto_queue brief 应自动排期 1 帖（{summary_q}）")
        with sqlite3.connect(database.DB_PATH) as conn:
            queued_rows = conn.execute(
                "SELECT user_id, platform FROM scheduled_posts "
                "WHERE standalone_post_id IN "
                "(SELECT id FROM standalone_posts WHERE brief_id = ?)",
                (bid_q,)).fetchall()
        check(len(queued_rows) == 1
              and queued_rows[0][0] == uid_b and queued_rows[0][1] == "wechat",
              f"自动排期应恰 1 行且归 brief 主人 B（{queued_rows}）")
    finally:
        content_agent.SCHEDULABLE = saved_schedulable

    # ── backfill-channels 是 worker 白名单函数的实例级维护口（终审 I2 钉）：
    # 两个函数刻意跨用户，路由必须 admin 专属——非 admin 一律 404，且与不存在
    # 路由同响应体（不泄露端点存在）；admin 在空库上照常跑通。──
    r_non_admin = client_b.post("/episodes/backfill-channels")
    r_unmapped = client_b.post("/definitely-not-a-route")
    check(r_non_admin.status_code == 404,
          f"非 admin POST backfill-channels 应 404（{r_non_admin.status_code}）")
    check(r_non_admin.get_data() == r_unmapped.get_data(),
          "非 admin 的 backfill-channels 404 应与不存在路由逐字节一致")
    r_admin = client.post("/episodes/backfill-channels")
    check(r_admin.status_code == 200 and r_admin.get_json().get("success") is True,
          f"admin 应可用 backfill-channels（空库 0 行）（{r_admin.status_code}）")
    print("INFO e2e 6/9 episode/brief 产物按主人归属，auto_queue 真落排期归主人，"
          "backfill-channels 非 admin 404、admin 可用")

    # ── 场景 7：/activity /usage 视角——B 带 ?user_id=<admin>&scope=all 仍只
    # 见自己；admin 全景见双方。──
    database.log_activity("zx_e2e_act", user_id=admin_id, username="gate",
                          path="/zx-e2e-a", db_path=database.DB_PATH)
    database.log_activity("zx_e2e_act", user_id=uid_b, username="e2e_b",
                          path="/zx-e2e-b", db_path=database.DB_PATH)
    database.log_usage(mode="proactive", category="compose", provider="gate",
                       model="zx-e2e-model-a", user_id=admin_id, username="gate",
                       db_path=database.DB_PATH)
    database.log_usage(mode="proactive", category="compose", provider="gate",
                       model="zx-e2e-model-b", user_id=uid_b, username="e2e_b",
                       db_path=database.DB_PATH)
    activity_b = page(client_b, f"/activity?user_id={admin_id}")
    check("/zx-e2e-b" in activity_b and "/zx-e2e-a" not in activity_b,
          "B 的 /activity 应固定本人视角（user_id 参数必须被忽略）")
    activity_admin = page(client, "/activity?scope=all")
    check("/zx-e2e-a" in activity_admin and "/zx-e2e-b" in activity_admin,
          "admin 的 /activity 应见双方")
    usage_b = page(client_b, f"/usage?user_id={admin_id}&scope=all")
    check("zx-e2e-model-b" in usage_b and "zx-e2e-model-a" not in usage_b,
          "B 的 /usage scope=all 应退回本人视角")
    usage_admin = page(client, "/usage?scope=all")
    check("zx-e2e-model-a" in usage_admin and "zx-e2e-model-b" in usage_admin,
          "admin 的 /usage?scope=all 应见双方")
    print("INFO e2e 7/9 /activity /usage：非 admin 固定本人，admin 全景见双方")

    # ── 场景 8：同一 URL 双用户互不去重——sources POST 与 add feed 各自一行，
    # 各自页面只见自己那行。抓网两处在内存里打桩，本节不联网。──
    import trafilatura
    trafilatura.fetch_url = lambda url: "<html><title>ZXE2ESHAREDSRC</title></html>"
    trafilatura.extract = lambda html, **kw: "shared body"
    trafilatura.extract_metadata = lambda html: type(
        "M", (), {"title": "ZXE2ESHAREDSRC page", "description": "d",
                  "image": None})()

    shared_page = "https://shared.example/page"
    check(client.post("/sources", data={"url": shared_page}).status_code == 200,
          "A 存 shared URL 应成功")
    seen_a = page(client, "/sources").count("ZXE2ESHAREDSRC page")
    check(seen_a >= 1, "A 的 /sources 应显示自己的一行")
    check(client_b.post("/sources", data={"url": shared_page}).status_code == 200,
          "B 存同一 URL 应成功（不得被全局去重 409）")
    check(_count(database.DB_PATH, "url_sources", "url", shared_page) == 2,
          "同一 URL 双用户应各一行（sources）")
    check(page(client, "/sources").count("ZXE2ESHAREDSRC page") == seen_a,
          "A 的 /sources 行数不得因 B 的添加而变化")
    check(page(client_b, "/sources").count("ZXE2ESHAREDSRC page") == seen_a,
          "B 的 /sources 应恰见自己的那一行")

    web.parse_feed_with_fallback = lambda url: type(
        "F", (), {"feed": {"title": "ZXE2ESHAREDFEED"}, "entries": []})()
    shared_rss = "https://shared.example/rss.xml"
    check(client.post("/", data={"feed_url": shared_rss}).status_code == 302,
          "A 加 shared RSS 应 302")
    feeds_a = page(client, "/").count("ZXE2ESHAREDFEED")
    check(feeds_a >= 1, "A 的首页应显示自己的 feed")
    check(client_b.post("/", data={"feed_url": shared_rss}).status_code == 302,
          "B 加同一 RSS 应 302")
    check(_count(database.DB_PATH, "feeds", "url", shared_rss) == 2,
          "同一 RSS 双用户应各一行（feeds）")
    check(page(client, "/").count("ZXE2ESHAREDFEED") == feeds_a,
          "A 的首页行数不得因 B 的添加而变化")
    check(page(client_b, "/").count("ZXE2ESHAREDFEED") == feeds_a,
          "B 的首页应恰见自己的那一行")
    print("INFO e2e 8/9 同一 URL/RSS 双用户各一行、各见各行")

    # 场景 9（迁移幂等）由 schema 节钉死，e2e 不重复。
    print("双账号端到端：新账号全页空、跨用户 404 同响应、同名 prompt 各见各帖、"
          "存量 NULL 不可见仍在库、episode/brief 产物按主人归属、auto_queue 落排期归主人、"
          "backfill-channels admin 专属、/activity /usage 视角正确、同 URL 各行各见"
          "（迁移幂等在 schema 节）")
    print("CONTENT_ISOLATION_E2E_OK")


SECTIONS = {
    "schema": section_schema,
    "data": section_data,
    "scan": section_scan,
    "e2e": section_e2e,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SECTIONS:
        print(f"usage: check_content_isolation.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
