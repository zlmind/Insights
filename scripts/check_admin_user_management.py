"""Admin 用户管理 gate：data / e2e 节，各打印独立 token。

    python scripts/check_admin_user_management.py data     ADMIN_USER_MANAGEMENT_DATA_OK

``data`` 钉数据层：delete_user 单事务级联清空 15 张归属表加 users 行；
activity_log 与 usage_events 的行保留但 user_id、username 一并置 NULL
（审计在，人没了）；uploaded_images(storage='local') 与
generated_thumbnails 指向的本地文件在提交之后 unlink，指向不存在文件的
行只记日志、绝不为一个文件系统副作用回滚已提交的库；keeper 的行、token
与文件一个不动。update_user_password 换哈希即让旧密码失效；
is_last_admin 只在目标是 admin 且全实例再无第二个 admin 时为 True——
目标不存在、非 admin、或仍有并肩 admin 时一律 False。e2e 节钉
/admin/users 的门控：admin 见列表，victim/anon 拿到与不存在路径
逐字节一致的 404，侧边栏入口仅 admin 渲染，zh 词条齐且可编译；
删除用户走量级预览与用户名二次确认，禁删自己/末位 admin，正式删除
清 15 表且 token 与旧 session 立即失效。

本节全程 tempfile.mkdtemp + database.init_db(temp_db)：不起 web、
不联网、不触碰真实 insights.db。
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

# 中文诊断信息按 UTF-8 直出：run_all_gates 以 UTF-8 解码子进程输出，Windows
# 控制台默认代码页会把中文打成问号。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402,F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FUTURE = "2099-01-01T00:00:00"

# delete_user 级联的 15 表。gate 自持一份清单，不 import
# database._DELETE_USER_TABLES——清单本身是被测对象，抄自实现只会自证。
_DATA_TABLES = (
    "feeds", "episodes", "articles", "social_posts", "standalone_posts",
    "url_sources", "uploaded_images", "generated_thumbnails", "prompt_library",
    "content_brief_runs", "scheduled_posts",
    "content_briefs", "social_accounts", "agent_jobs", "agent_tokens",
)


def _user_rows(db_path, table, user_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = ?",
            (user_id,)).fetchone()[0]


# ── data 节 ──────────────────────────────────────────────────────────────────

def section_data():
    import database

    directory = tempfile.mkdtemp(prefix="admin_user_mgmt_data_")
    temp_db = os.path.join(directory, "insights.db")
    database.init_db(temp_db)

    # ── 1) 建号：首号自动 admin；keeper 用 SQL 置 admin；victim 无特权。──
    gate_id = database.create_user("gate", generate_password_hash("p"),
                                   db_path=temp_db)
    victim = database.create_user("victim", generate_password_hash("p"),
                                  db_path=temp_db)
    keeper = database.create_user("keeper", generate_password_hash("p"),
                                  db_path=temp_db)
    with sqlite3.connect(temp_db) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (keeper,))
        conn.commit()
        flags = {row[0]: row[1] for row in
                 conn.execute("SELECT username, is_admin FROM users").fetchall()}
    check(flags == {"gate": 1, "victim": 0, "keeper": 1},
          f"三个账号的 admin 位应恰为 gate/keeper，实为 {flags}")

    # ── 2) 文件夹：victim 两枚真实文件 + 一枚只存在于库里的 ghost；
    #    keeper 一枚对照文件。ghost 不落盘——钉缺失文件不抛错。──
    files_root = os.path.join(directory, "site")
    uploads = os.path.join(files_root, "static", "uploads")
    thumbs = os.path.join(uploads, "youtube_thumbnails")
    os.makedirs(thumbs)
    victim_local = os.path.join(uploads, "victim_local.png")
    victim_thumb = os.path.join(thumbs, "victim_thumb.png")
    keeper_local = os.path.join(uploads, "keeper_local.png")
    ghost = os.path.join(uploads, "ghost.png")
    for path in (victim_local, victim_thumb, keeper_local):
        with open(path, "wb") as fh:
            fh.write(b"png")
    check(not os.path.exists(ghost), "ghost.png 前提：本就不得存在")

    # ── 3) victim 在 15 表各播行。uploaded_images 两行：第二行指向不存在的
    #    ghost.png；keeper 播对照行。──
    feed_v = database.add_feed("https://victim.example/feed.xml", "v feed",
                               user_id=victim, db_path=temp_db)
    database.queue_episode("https://victim.example/ep.mp3", "v ep", feed_v,
                           user_id=victim, db_path=temp_db)
    art_v = database.add_article(None, "v topic", "blog", "v body",
                                 user_id=victim, db_path=temp_db)
    database.add_social_post(art_v, "linkedin", "v post",
                             user_id=victim, db_path=temp_db)
    st_v = database.add_standalone_post("freeform", "v prompt", "xiaohongshu",
                                        "v copy", user_id=victim,
                                        db_path=temp_db)
    database.add_url_source("https://victim.example/page", "v page", "d",
                            "v body", user_id=victim, db_path=temp_db)
    database.add_uploaded_image("victim_local.png",
                                "/static/uploads/victim_local.png",
                                "local", user_id=victim, db_path=temp_db)
    database.add_uploaded_image("ghost.png", "/static/uploads/ghost.png",
                                "local", user_id=victim, db_path=temp_db)
    database.add_generated_thumbnail(
        "https://www.youtube.com/watch?v=v", "v", "T", "C", "16:9", "s", "p",
        "static/uploads/youtube_thumbnails/victim_thumb.png",
        user_id=victim, db_path=temp_db)
    database.add_library_prompt("v title", "v body", user_id=victim,
                                db_path=temp_db)
    brief_v = database.create_content_brief("v brief", "i", user_id=victim,
                                            db_path=temp_db)
    database.create_brief_run(brief_v, user_id=victim, db_path=temp_db)
    database.add_scheduled_post(FUTURE, "standalone", victim,
                                standalone_post_id=st_v, db_path=temp_db)
    database.upsert_social_account("wechat", "vx-1", user_id=victim,
                                   db_path=temp_db)
    database.enqueue_agent_job("article_sync", "{}", user_id=victim,
                               db_path=temp_db)
    database.create_agent_token(victim, "victim-token", db_path=temp_db)
    database.add_uploaded_image("keeper_local.png",
                                "/static/uploads/keeper_local.png",
                                "local", user_id=keeper, db_path=temp_db)
    database.create_agent_token(keeper, "keeper-token", db_path=temp_db)

    # ── 4) 审计两表：行保留但去身份。──
    database.log_activity("zx", user_id=victim, username="victim",
                          db_path=temp_db)
    database.log_usage(mode="proactive", category="compose", provider="g",
                       model="m", user_id=victim, username="victim",
                       db_path=temp_db)

    # ── 5) 预览计数：15 键齐，行数与播种一致（uploaded_images 是 2）。──
    before = database.count_user_rows(victim, db_path=temp_db)
    expected_before = {table: 1 for table in _DATA_TABLES}
    expected_before["uploaded_images"] = 2
    check(set(before) == set(_DATA_TABLES),
          f"count_user_rows 应恰返回 15 表键，实为 {sorted(before)}")
    check(before == expected_before, f"预览计数应与播种一致，实为 {before}")

    # ── 6) 删除：逐表行数精确（15 表 + users），且不因 ghost.png 抛错。──
    counts = database.delete_user(victim, db_path=temp_db,
                                  files_root=files_root)
    expected_counts = dict(expected_before, users=1)
    check(counts == expected_counts, f"删除计数应精确，实为 {counts}")

    # ── 7) 库态：15 表清零、users 无 victim、审计行在但已去身份、token
    #    生死分明、keeper 行幸存。──
    for table in _DATA_TABLES:
        check(_user_rows(temp_db, table, victim) == 0,
              f"删除后 {table} 仍残留 victim 行")
    with sqlite3.connect(temp_db) as conn:
        gone = conn.execute("SELECT COUNT(*) FROM users WHERE id = ?",
                            (victim,)).fetchone()[0]
        audit_activity = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'zx' "
            "AND user_id IS NULL AND username IS NULL").fetchone()[0]
        audit_usage = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE model = 'm' "
            "AND user_id IS NULL AND username IS NULL").fetchone()[0]
        keeper_images = conn.execute(
            "SELECT COUNT(*) FROM uploaded_images WHERE user_id = ?",
            (keeper,)).fetchone()[0]
        keeper_tokens = conn.execute(
            "SELECT COUNT(*) FROM agent_tokens "
            "WHERE user_id = ? AND revoked = 0", (keeper,)).fetchone()[0]
    check(gone == 0, "users 表应无 victim 行")
    check(audit_activity == 1,
          "activity_log 的 victim 行应保留且 user_id/username 双 NULL")
    check(audit_usage == 1,
          "usage_events 的 victim 行应保留且 user_id/username 双 NULL")
    check(database.find_agent_user_by_token("victim-token", db_path=temp_db)
          is None, "victim 的 token 应随删除消失（不是吊销，是行没了）")
    check(database.find_agent_user_by_token("keeper-token", db_path=temp_db)
          == keeper, "keeper 的 token 必须幸存且仍解析到 keeper")
    check(keeper_images == 1, "keeper 的 uploaded_images 行必须幸存")
    check(keeper_tokens == 1, "keeper 的 agent_tokens 行必须幸存")

    # ── 8) 文件：victim 两枚被清，ghost 本就不存在且全程未抛错，keeper 在。──
    check(not os.path.exists(victim_local), "victim_local.png 应被 unlink")
    check(not os.path.exists(victim_thumb), "victim_thumb.png 应被 unlink")
    check(not os.path.exists(ghost), "ghost.png 不得凭空出现")
    check(os.path.exists(keeper_local), "keeper_local.png 必须幸存")

    # ── 9) 密码重置：keeper 换新密码，旧密码立即失效。──
    check(database.update_user_password(999999, generate_password_hash("x"),
                                        db_path=temp_db) is False,
          "对不存在的用户重置密码应返回 False")
    with sqlite3.connect(temp_db) as conn:
        old_hash = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (keeper,)).fetchone()[0]
    new_hash = generate_password_hash("np")
    check(database.update_user_password(keeper, new_hash, db_path=temp_db)
          is True, "对存在的用户重置密码应返回 True")
    with sqlite3.connect(temp_db) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (keeper,)).fetchone()[0]
    check(stored != old_hash, "库中 password_hash 应已变化")
    check(check_password_hash(stored, "np"), "新密码应通过校验")
    check(not check_password_hash(stored, "p"), "旧密码必须失效")

    # ── 10) is_last_admin 真值表。──
    check(database.is_last_admin(victim, db_path=temp_db) is False,
          "已删除用户 is_last_admin 应 False")
    check(database.is_last_admin(999999, db_path=temp_db) is False,
          "不存在的用户 is_last_admin 应 False")
    check(database.is_last_admin(gate_id, db_path=temp_db) is False,
          "双 admin 并存时 gate 不是末位")
    check(database.is_last_admin(keeper, db_path=temp_db) is False,
          "双 admin 并存时 keeper 不是末位")
    with sqlite3.connect(temp_db) as conn:
        conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (keeper,))
        conn.commit()
    check(database.is_last_admin(gate_id, db_path=temp_db) is True,
          "只剩一个 admin 时 is_last_admin 应 True")
    check(database.is_last_admin(keeper, db_path=temp_db) is False,
          "被摘掉 admin 的用户 is_last_admin 应 False")

    # ── 11) 边界：删除不存在的用户安静返回全 0。──
    empty = database.delete_user(999999, db_path=temp_db,
                                 files_root=files_root)
    check(set(empty) == set(_DATA_TABLES) | {"users"},
          "空删除应仍带全部 16 键")
    check(all(v == 0 for v in empty.values()),
          f"空删除所有键应为 0，实为 {empty}")

    print("delete_user 单事务级联清 15 表+users，审计两表去身份化保留，"
          "local 文件清理对缺失文件不抛错，密码重置生效，末位 admin 判定正确")
    print("ADMIN_USER_MANAGEMENT_DATA_OK")


# ── e2e 节 ──────────────────────────────────────────────────────────────────

# 本任务钉进 zh.json 的精确规则 → 模板里的英文原文。字典与模板靠这里不脱钩：
# 规则不在字典里、或模板字符串改了名，这里都会红。
_E2E_RULES = {
    "User Management": "^User Management$",
    "Users": "^Users$",
    "Username": "^Username$",
    "Admin": "^Admin$",
    "Created": "^Created$",
    "Last login": "^Last login$",
    "Actions": "^Actions$",
    "New password cannot be empty.": "^New password cannot be empty\\.$",
    "Password reset for victim.": "^Password reset for (.+)\\.$",
    "You cannot delete your own account.": "^You cannot delete your own account\\.$",
    "This is the last admin — transfer admin first.":
        "^This is the last admin — transfer admin first\\.$",
    "Username confirmation does not match — nothing was deleted.":
        "^Username confirmation does not match — nothing was deleted\\.$",
    "Deleted victim and 16 data rows.": "^Deleted (.+) and (\\d+) data rows\\.$",
}


def section_e2e():
    directory, database, web, publisher, client = isolated_app()
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = ?",
                     ("gate",))
        conn.commit()
    uid_v = database.create_user("victim", generate_password_hash("p"),
                                 db_path=database.DB_PATH)
    client_v = web.app.test_client()
    with client_v.session_transaction() as session:
        session["user_id"] = uid_v
    anon = web.app.test_client()

    # 404 基线：真实不存在的路径。admin 视角取一次，所有探测与之逐字节比。
    missing = client.get("/definitely-not-a-route")
    check(missing.status_code == 404, "404 基线前提：该路径应确实不存在")

    # admin 看得到列表；victim / anon 拿到与不存在路径同一份字节的 404。
    page = client.get("/admin/users")
    check(page.status_code == 200,
          f"admin GET /admin/users 应 200，实为 {page.status_code}")
    body = page.get_data(as_text=True)
    check("victim" in body and "gate" in body,
          "列表页应含 victim 与 gate 两个用户名")
    check("Username" in body, "列表页应含表头 Username")

    for label, probe in (("victim", client_v), ("anon", anon)):
        r = probe.get("/admin/users")
        check(r.status_code == 404,
              f"{label} GET /admin/users 应 404，实为 {r.status_code}")
        check(r.get_data() == missing.get_data(),
              f"{label} 的 404 响应体应与不存在路径逐字节一致")

    # 侧边栏零泄漏：普通页只对 admin 渲染 admin 入口。
    check(b"/admin/users" not in client_v.get("/").get_data(),
          "victim 的普通页不得出现 /admin/users")
    check(b"/admin/users" in client.get("/").get_data(),
          "admin 的普通页侧边栏应含 /admin/users 入口")

    # zh.json 完整性：规则全部可编译；本任务每条规则在字典里且命中英文原文。
    with open(os.path.join(REPO, "static", "i18n", "zh.json"),
              encoding="utf-8") as fh:
        rules = json.load(fh)["rules"]
    for rule in rules:
        re.compile(rule["re"])
    compiled = {rule["re"] for rule in rules}
    for english, pattern in _E2E_RULES.items():
        check(re.match(pattern, english) is not None,
              f"规则 {pattern!r} 应命中英文原文 {english!r}")
        check(pattern in compiled,
              f"zh.json rules 应含本任务规则 {pattern!r}")

    # ── 重置密码（Task 3）：admin POST 新密码；门控对非 admin/未登录 404。──
    # POST 逐字节 404 基线：Task 2 只建了 GET 基线，表单场景自建 POST 的。
    missing_post = client.post("/definitely-not-a-route")
    check(missing_post.status_code == 404,
          "POST 404 基线前提：该路径应确实不存在")

    # 1) admin 提交新密码 → 302 回列表，flash 报成功，库中哈希换新。
    reset = client.post(f"/admin/users/{uid_v}/password",
                        data={"new_password": "np123"})
    check(reset.status_code == 302,
          f"admin 重置密码应 302 回列表，实为 {reset.status_code}")
    after = client.get(reset.headers["Location"])
    check("Password reset for victim." in after.get_data(as_text=True),
          "重置后的列表页应含成功 flash 文案 Password reset for victim.")
    row = database.get_user_by_id(uid_v, db_path=database.DB_PATH)
    check(check_password_hash(row["password_hash"], "np123"),
          "重置后库中哈希应通过新密码 np123 校验")
    check(not check_password_hash(row["password_hash"], "p"),
          "重置后旧密码 p 必须失效")

    # 2) 重置不打掉登录态：victim 旧 session 仍有效（钉既定行为不回归）。
    check(client_v.get("/").status_code == 200,
          "重置密码后 victim 的旧 session 应仍有效（GET / 应 200）")

    # 3) 空 new_password（空串与纯空格两种）：flash 拒绝文案，哈希不动。
    hash_before_blank = database.get_user_by_id(
        uid_v, db_path=database.DB_PATH)["password_hash"]
    for blank in ("", "   "):
        blank_page = client.post(f"/admin/users/{uid_v}/password",
                                 data={"new_password": blank},
                                 follow_redirects=True)
        check("New password cannot be empty."
              in blank_page.get_data(as_text=True),
              f"空密码 {blank!r} 应 flash New password cannot be empty.")
    hash_after_blank = database.get_user_by_id(
        uid_v, db_path=database.DB_PATH)["password_hash"]
    check(hash_after_blank == hash_before_blank,
          "空密码提交不得改动库中 password_hash")

    # 4) 不存在的 id：404 且与 POST 不存在路径逐字节一致。
    gone = client.post("/admin/users/999999999/password",
                       data={"new_password": "x"})
    check(gone.status_code == 404,
          f"对不存在 id 重置密码应 404，实为 {gone.status_code}")
    check(gone.get_data() == missing_post.get_data(),
          "不存在 id 的 404 响应体应与 POST 不存在路径逐字节一致")

    # 5) 门控：victim / anon POST 同端点各与自己的 POST 基线逐字节一致。
    victim_post_baseline = client_v.post("/definitely-not-a-route")
    r_v = client_v.post(f"/admin/users/{uid_v}/password",
                        data={"new_password": "np123"})
    check(r_v.status_code == 404,
          f"victim POST 重置密码应 404，实为 {r_v.status_code}")
    check(r_v.get_data() == victim_post_baseline.get_data(),
          "victim 的 404 响应体应与其 POST 基线逐字节一致")
    anon_post_baseline = anon.post("/definitely-not-a-route")
    r_a = anon.post(f"/admin/users/{uid_v}/password",
                    data={"new_password": "np123"})
    check(r_a.status_code == 404,
          f"anon POST 重置密码应 404，实为 {r_a.status_code}")
    check(r_a.get_data() == anon_post_baseline.get_data(),
          "anon 的 404 响应体应与其 POST 基线逐字节一致")

    # ── 删除用户（Task 4）：量级预览、二次确认、守卫、token/旧 session 失效。
    # 播种：15 表各 1 行，其中 uploaded_images 两行——cloudinary 一行 +
    # local 一行指向磁盘上不存在的 /static/uploads/e2e-gone.png（删除经
    # 路由不得因缺失文件抛错）；审计两表各一行；token 一枚。──
    feed_v = database.add_feed("https://victim.example/feed.xml", "v feed",
                               user_id=uid_v, db_path=database.DB_PATH)
    database.queue_episode("https://victim.example/ep.mp3", "v ep", feed_v,
                           user_id=uid_v, db_path=database.DB_PATH)
    art_v = database.add_article(None, "v topic", "blog", "v body",
                                 user_id=uid_v, db_path=database.DB_PATH)
    database.add_social_post(art_v, "linkedin", "v post",
                             user_id=uid_v, db_path=database.DB_PATH)
    st_v = database.add_standalone_post("freeform", "v prompt", "xiaohongshu",
                                        "v copy", user_id=uid_v,
                                        db_path=database.DB_PATH)
    database.add_url_source("https://victim.example/page", "v page", "d",
                            "v body", user_id=uid_v, db_path=database.DB_PATH)
    database.add_uploaded_image("e2e-cloud.png",
                                "https://res.cloudinary.com/demo/e2e-cloud.png",
                                "cloudinary", user_id=uid_v,
                                db_path=database.DB_PATH)
    database.add_uploaded_image("e2e-gone.png",
                                "/static/uploads/e2e-gone.png",
                                "local", user_id=uid_v,
                                db_path=database.DB_PATH)
    database.add_generated_thumbnail(
        "https://www.youtube.com/watch?v=v", "v", "T", "C", "16:9", "s", "p",
        "static/uploads/youtube_thumbnails/e2e_thumb.png",
        user_id=uid_v, db_path=database.DB_PATH)
    database.add_library_prompt("v title", "v body", user_id=uid_v,
                                db_path=database.DB_PATH)
    brief_v = database.create_content_brief("v brief", "i", user_id=uid_v,
                                            db_path=database.DB_PATH)
    database.create_brief_run(brief_v, user_id=uid_v,
                              db_path=database.DB_PATH)
    database.add_scheduled_post(FUTURE, "standalone", uid_v,
                                standalone_post_id=st_v,
                                db_path=database.DB_PATH)
    database.upsert_social_account("wechat", "vx-e2e", user_id=uid_v,
                                   db_path=database.DB_PATH)
    database.enqueue_agent_job("article_sync", "{}", user_id=uid_v,
                               db_path=database.DB_PATH)
    database.create_agent_token(uid_v, "victim-e2e-token",
                                db_path=database.DB_PATH)
    database.log_activity("zx", user_id=uid_v, username="victim",
                          db_path=database.DB_PATH)
    database.log_usage(mode="proactive", category="compose", provider="g",
                       model="m", user_id=uid_v, username="victim",
                       db_path=database.DB_PATH)

    seeded = {table: 1 for table in _DATA_TABLES}
    seeded["uploaded_images"] = 2

    # 1) 量级预览：stats 返回 200 JSON，键集与行数与播种一致。
    stats = client.get(f"/admin/users/{uid_v}/stats")
    check(stats.status_code == 200,
          f"admin GET stats 应 200，实为 {stats.status_code}")
    payload = stats.get_json()
    check(payload["username"] == "victim",
          f"stats 应报用户名 victim，实为 {payload.get('username')!r}")
    check(set(payload["counts"]) == set(_DATA_TABLES),
          f"stats counts 应恰含 15 表键，实为 {sorted(payload['counts'])}")
    check(payload["counts"] == seeded,
          f"stats counts 应与播种一致，实为 {payload['counts']}")

    # 2) 不存在 id：stats 与 delete 都 404 且与各自基线逐字节一致（含
    # victim 的 GET stats——三个接口全覆盖 404 而非 500）。
    for probe, label in ((client, "admin"), (client_v, "victim")):
        r = probe.get("/admin/users/999999999/stats")
        check(r.status_code == 404,
              f"{label} GET 不存在 id stats 应 404，实为 {r.status_code}")
        check(r.get_data() == missing.get_data(),
              f"{label} GET 不存在 id stats 的 404 应与 GET 基线逐字节一致")
    gone_del = client.post("/admin/users/999999999/delete")
    check(gone_del.status_code == 404,
          f"admin POST 不存在 id delete 应 404，实为 {gone_del.status_code}")
    check(gone_del.get_data() == missing_post.get_data(),
          "admin POST 不存在 id delete 的 404 应与 POST 基线逐字节一致")

    # 3) 确认串不匹配：拒绝、库中 victim 行原样。
    wrong = client.post(f"/admin/users/{uid_v}/delete",
                        data={"confirm_username": "wrong"})
    check(wrong.status_code == 302,
          f"确认串不匹配应 302 回列表，实为 {wrong.status_code}")
    wrong_page = client.get(wrong.headers["Location"])
    check("Username confirmation does not match"
          in wrong_page.get_data(as_text=True),
          "确认串不匹配应 flash Username confirmation does not match")
    check(database.count_user_rows(uid_v, db_path=database.DB_PATH) == seeded,
          "确认串不匹配不得删除任何行")

    # 4) 大小写不符：确认串大小写敏感，同样拒绝、零删除。
    cased = client.post(f"/admin/users/{uid_v}/delete",
                        data={"confirm_username": "Victim"},
                        follow_redirects=True)
    check("Username confirmation does not match"
          in cased.get_data(as_text=True),
          "大小写不符应同样 flash 拒绝文案")
    check(database.count_user_rows(uid_v, db_path=database.DB_PATH) == seeded,
          "大小写不符不得删除任何行")

    # 5) 禁删自己：admin 对自己提交（确认串正确）也被拦，users 行数不变
    # （self 守卫先拦，is_last_admin 是纵深防御，谓词已由 data 节钉死）。
    with sqlite3.connect(database.DB_PATH) as conn:
        admin_id = conn.execute(
            "SELECT id FROM users WHERE username = 'gate'").fetchone()[0]
        users_before = conn.execute(
            "SELECT COUNT(*) FROM users").fetchone()[0]
    self_del = client.post(f"/admin/users/{admin_id}/delete",
                           data={"confirm_username": "gate"},
                           follow_redirects=True)
    check("You cannot delete your own account."
          in self_del.get_data(as_text=True),
          "删除自己应 flash You cannot delete your own account.")
    with sqlite3.connect(database.DB_PATH) as conn:
        users_after = conn.execute(
            "SELECT COUNT(*) FROM users").fetchone()[0]
    check(users_after == users_before, "删除自己被拒后 users 行数不得变化")

    # 6) 正式删除：确认串两侧空白被 strip 宽容；15 表清零、users 无
    # victim、审计两表去身份保留、token 立即失效。
    deleted = client.post(f"/admin/users/{uid_v}/delete",
                          data={"confirm_username": " victim "})
    check(deleted.status_code == 302,
          f"正式删除应 302 回列表，实为 {deleted.status_code}")
    deleted_page = client.get(deleted.headers["Location"])
    check("Deleted victim and" in deleted_page.get_data(as_text=True),
          "正式删除应 flash Deleted victim and …")
    for table in _DATA_TABLES:
        check(_user_rows(database.DB_PATH, table, uid_v) == 0,
              f"删除后 {table} 仍残留 victim 行")
    with sqlite3.connect(database.DB_PATH) as conn:
        gone_user = conn.execute(
            "SELECT COUNT(*) FROM users WHERE id = ?",
            (uid_v,)).fetchone()[0]
        audit_activity = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'zx' "
            "AND user_id IS NULL AND username IS NULL").fetchone()[0]
        audit_usage = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE model = 'm' "
            "AND user_id IS NULL AND username IS NULL").fetchone()[0]
    check(gone_user == 0, "users 表应无 victim 行")
    check(audit_activity == 1,
          "activity_log 的 victim 行应保留且 user_id/username 双 NULL")
    check(audit_usage == 1,
          "usage_events 的 victim 行应保留且 user_id/username 双 NULL")
    check(database.find_agent_user_by_token("victim-e2e-token",
                                            db_path=database.DB_PATH) is None,
          "victim 的 token 应随删除立即失效")

    # 7) 文件断言：ghost 文件本就不存在，删除全程未抛错即过——真文件的
    # 清理由 data 节钉死，路由对缺失文件只记日志、不回滚。

    # 8) victim 旧 session 失效：被既有全局中间件弹回登录页。
    stale = client_v.get("/")
    check(stale.status_code == 302,
          f"删除后 victim 旧 session 访问 / 应 302，实为 {stale.status_code}")
    check("/login" in (stale.headers.get("Location") or ""),
          "删除后 victim 旧 session 应被弹回 /login")

    # 9) 门控：anon / victim POST delete 与各自 POST 基线逐字节一致
    # （装饰器先于目标查询触发）。
    r_anon = anon.post("/admin/users/999999999/delete")
    check(r_anon.status_code == 404,
          f"anon POST delete 应 404，实为 {r_anon.status_code}")
    check(r_anon.get_data() == anon_post_baseline.get_data(),
          "anon POST delete 的 404 应与其 POST 基线逐字节一致")
    r_victim = client_v.post("/admin/users/999999999/delete")
    check(r_victim.status_code == 404,
          f"victim POST delete 应 404，实为 {r_victim.status_code}")
    check(r_victim.get_data() == victim_post_baseline.get_data(),
          "victim POST delete 的 404 应与其 POST 基线逐字节一致")

    print("admin 列表页仅 admin 可见，victim/anon 对 /admin/users 与不存在"
          "路径拿到逐字节相同的 404，侧边栏入口零泄漏，admin 重置密码生效且"
          "空密码被拒，删除用户量级预览+确认串（strip 宽容、大小写敏感）+"
          "禁删自己，正式删除清 15 表且 token/旧 session 立即失效，"
          "POST 门控同样逐字节 404，zh 词条齐且可编译")
    print("ADMIN_USER_MANAGEMENT_E2E_OK")


SECTIONS = {
    "data": section_data,
    "e2e": section_e2e,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name == "":
        # No section named: run every section in a fresh subprocess each —
        # the sections share module state (heartbeat buckets, chdir), so
        # in-process sequencing would leak between them, same reason
        # run_all_gates spawns one process per gate.
        failed = []
        for section_name in SECTIONS:
            done = subprocess.run(
                [sys.executable, os.path.abspath(__file__), section_name])
            if done.returncode != 0:
                failed.append(section_name)
        if failed:
            print(f"FAILED SECTIONS: {' '.join(failed)}")
            sys.exit(1)
        sys.exit(0)
    if name not in SECTIONS:
        print(f"usage: check_admin_user_management.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
