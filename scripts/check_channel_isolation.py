"""Multi-user publishing channel gate: schema / data / ... sections.

    python scripts/check_channel_isolation.py schema   CHANNEL_ISOLATION_SCHEMA_OK
    python scripts/check_channel_isolation.py tokens   CHANNEL_ISOLATION_TOKENS_OK
    python scripts/check_channel_isolation.py heartbeat CHANNEL_ISOLATION_HEARTBEAT_OK
    python scripts/check_channel_isolation.py accounts CHANNEL_ISOLATION_ACCOUNTS_OK

``schema`` pins the agent_tokens table and the legacy global token migration;
``tokens`` pins the /agent/* door's per-user identity: claim, events and the
read paths only ever serve the token's own user, and cross-user reads answer
with the same bytes as reads of things that do not exist; ``heartbeat`` pins
the per-user heartbeat buckets: login states, the online pill and the compose
selector read the viewer's own bucket and nobody else's; ``accounts`` pins
social_accounts' per-user isolation: the widened unique key, the legacy rows
settling with admin, the per-user account list and page, and the token
manager's revocation rules.
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from werkzeug.security import generate_password_hash  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _table_columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _count(db_path, table, column=None, value=None):
    with sqlite3.connect(db_path) as conn:
        if column is None:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)
        ).fetchone()[0]


def section_schema():
    directory, database, web, publisher, client = isolated_app()
    db_path = database.DB_PATH

    # Idempotent: running init_db twice must not error or duplicate.
    database.init_db(db_path)
    database.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        cols = _table_columns(conn, "agent_tokens")
        check("agent_tokens" in [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()], "agent_tokens table does not exist")
        for col in ("token_hash", "user_id", "label", "created_at", "last_seen", "revoked"):
            check(col in cols, f"agent_tokens missing column {col}")

        indexes = {r[1] for r in conn.execute("PRAGMA index_list(agent_tokens)").fetchall()}
        # UNIQUE constraint creates a sqlite autoindex.
        unique_indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agent_tokens'"
        ).fetchall() if r[0].startswith("sqlite_autoindex_")}
        check(bool(unique_indexes), "agent_tokens token_hash is not UNIQUE")

    # Legacy global token migration: AGENT_TOKEN setting -> one row for admin.
    admin_uid = database.get_user_by_username("gate")["id"]
    database.set_setting("AGENT_TOKEN", "legacy-secret", db_path=db_path)
    database.init_db(db_path)
    check(
        database.find_agent_user_by_token("legacy-secret", db_path=db_path) == admin_uid,
        "legacy AGENT_TOKEN should resolve to admin user",
    )
    check(
        database.find_agent_user_by_token("wrong", db_path=db_path) is None,
        "wrong token should not resolve",
    )
    check(_count(db_path, "agent_tokens") == 1,
          "legacy migration should create exactly one agent_tokens row")
    database.init_db(db_path)
    check(_count(db_path, "agent_tokens") == 1,
          "second init_db after migration should not duplicate the legacy row")

    # Fresh database without AGENT_TOKEN has zero rows.
    fresh_path = os.path.join(directory, "fresh.db")
    database.init_db(fresh_path)
    check(_count(fresh_path, "agent_tokens") == 0,
          "fresh database should have no agent_tokens rows")

    # Per-user token lifecycle.
    uid = database.create_user("channel_user", generate_password_hash("p"),
                               db_path=db_path)
    token_id = database.create_agent_token(uid, "t2", "B 的浏览器", db_path=db_path)
    check(token_id is not None and token_id > 0, "create_agent_token should return id")
    check(
        database.find_agent_user_by_token("t2", db_path=db_path) == uid,
        "new token should resolve to its user",
    )

    # Revocation by owner.
    check(database.revoke_agent_token(token_id, uid, db_path=db_path) is True,
          "owner should be able to revoke their own token")
    check(database.find_agent_user_by_token("t2", db_path=db_path) is None,
          "revoked token should not resolve")

    # Revocation by other user must fail.
    other_uid = database.create_user("other_channel_user",
                                     generate_password_hash("p"),
                                     db_path=db_path)
    token_id_2 = database.create_agent_token(uid, "t3", "C 的浏览器", db_path=db_path)
    check(database.revoke_agent_token(token_id_2, other_uid, db_path=db_path) is False,
          "other user must not revoke someone else's token")

    # touch + list.
    database.touch_agent_token("t3", db_path=db_path)
    rows = database.list_agent_tokens(uid, db_path=db_path)
    check(any(r["id"] == token_id_2 and r["last_seen"] is not None for r in rows),
          "touched token should have last_seen set")
    for r in rows:
        for value in r.values():
            check(value not in ("t3", "t2", database.hash_agent_token("t3"),
                                database.hash_agent_token("t2")),
                  "list_agent_tokens must not expose plaintext token or hash")

    print("agent_tokens schema, legacy migration, and lifecycle checks passed")
    print("CHANNEL_ISOLATION_SCHEMA_OK")


def section_tokens():
    """The /agent/* door resolves identity from the bearer token, per user.

    Two users, two tokens. Every endpoint answers only for the token's own
    user: a claim never hands over the other user's job, an event for someone
    else's job is the same 404 as an event for a job that does not exist, and
    the queue pages never leak the other user's titles.
    """
    directory, database, web, publisher, client = isolated_app()
    db_path = database.DB_PATH
    uid = database.get_user_by_username("gate")["id"]
    uid_b = database.create_user("b", generate_password_hash("p"), db_path=db_path)
    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = uid_b
    # The /agent/* endpoints are outside the session wall, so a sessionless
    # client is the honest stand-in for the extension and the local agent.
    agent = web.app.test_client()

    database.create_agent_token(uid, "token-a", db_path=db_path)
    database.create_agent_token(uid_b, "token-b", db_path=db_path)
    A = {"Authorization": "Bearer token-a"}
    B = {"Authorization": "Bearer token-b"}

    # --- auth: every endpoint rejects a missing, wrong or revoked token ---
    revoked = database.create_agent_token(uid, "token-r", db_path=db_path)
    database.revoke_agent_token(revoked, uid, db_path=db_path)
    bad_header_sets = (
        None,                                        # no header at all
        {"Authorization": "Bearer wrong"},          # unknown token
        {"Authorization": "Bearer token-r"},        # revoked token
        {"Authorization": "Bearer tøken"},          # non-ASCII, still JSON 401
    )
    # The job id here does not exist: 401 must come before any job lookup.
    door_paths = ("/agent/heartbeat", "/agent/jobs/claim",
                  "/agent/jobs/999999/events")
    for headers in bad_header_sets:
        for path in door_paths:
            r = agent.post(path, headers=headers, json={})
            check(r.status_code == 401, f"{path} with a bad token is 401")
            check(r.get_json() == {"error": "unauthorized"},
                  f"{path} with a bad token answers the JSON 401 body")

    # --- enqueue: a job without an owner is refused, not silently queued ---
    payload = json.dumps({"title": "uid-secret-title",
                           "items": [{"target": "wechat", "content": "x"}]})
    try:
        database.enqueue_agent_job("article_sync", payload, user_id=None,
                                   db_path=db_path)
        check(False, "enqueue without user_id must raise ValueError")
    except ValueError:
        check(True, "enqueue without user_id raises ValueError")
    jid = database.enqueue_agent_job("article_sync", payload, user_id=uid,
                                     db_path=db_path)

    # --- claim: a token only ever takes its own user's job ---
    # B's claim runs while A's job is the only pending row: skipping it is a
    # 204, never a handover. Only then does B get a job of its own to take.
    r = agent.post("/agent/jobs/claim", headers=B, json={"agent": "b"})
    check(r.status_code == 204,
          "token-b claims the other user's job is a 204, never a handover")
    check(r.get_data() == b"", "the 204 claim carries no body")
    r = agent.post("/agent/jobs/claim", headers=A, json={"agent": "a"})
    check(r.status_code == 200 and r.get_json()["id"] == jid,
          "token-a claims its own user's job")
    jid_b = database.enqueue_agent_job(
        "article_sync", json.dumps({"title": "b-title",
                                    "items": [{"target": "zhihu", "content": "y"}]}),
        user_id=uid_b, db_path=db_path)
    r = agent.post("/agent/jobs/claim", headers=B, json={"agent": "b"})
    check(r.status_code == 200 and r.get_json()["id"] == jid_b,
          "token-b still claims its own user's job")

    # --- events: ownership before anything else ---
    r_own = agent.post(f"/agent/jobs/{jid}/events", headers=A,
                       json={"type": "started", "agent": "a"})
    check(r_own.status_code == 200, "token-a reports on its own user's job")
    r_cross = agent.post(f"/agent/jobs/{jid}/events", headers=B,
                         json={"type": "started", "agent": "b"})
    r_missing = agent.post("/agent/jobs/999999/events", headers=B,
                           json={"type": "started", "agent": "b"})
    check(r_cross.status_code == 404 and r_missing.status_code == 404,
          "another user's job and a missing job are both 404")
    check(r_cross.get_data() == r_missing.get_data(),
          "the two 404 bodies are byte-for-byte identical")
    # The unknown-event 400 stays, but ownership outranks it: a foreign user
    # posting garbage to my job hears "not found", not a protocol complaint.
    r = agent.post(f"/agent/jobs/{jid}/events", headers=B,
                   json={"type": "exploded"})
    check(r.status_code == 404,
          "an unknown event on another user's job is the 404, not a 400")
    r = agent.post(f"/agent/jobs/{jid}/events", headers=A,
                   json={"type": "exploded"})
    check(r.status_code == 400,
          "an unknown event on one's own job is still a 400")

    # --- read paths: the .json row and the queue page are per user ---
    r_cross = client_b.get(f"/agent-jobs/{jid}.json")
    r_missing = client_b.get("/agent-jobs/999999.json")
    check(r_cross.status_code == 404 and r_missing.status_code == 404,
          "B reading A's job and a missing job are both 404")
    check(r_cross.get_data() == r_missing.get_data(),
          "the .json 404 bodies are byte-for-byte identical")
    check(client_b.get(f"/agent-jobs/{jid_b}.json").status_code == 200,
          "B reads its own job's row")
    check(client.get(f"/agent-jobs/{jid}.json").status_code == 200,
          "A reads its own job's row")

    page_b = client_b.get("/agent-jobs").get_data(as_text=True)
    check("uid-secret-title" not in page_b,
          "the queue page never shows another user's job title")
    check("b-title" in page_b, "the queue page shows one's own job title")
    page_a = client.get("/agent-jobs").get_data(as_text=True)
    check("uid-secret-title" in page_a and "b-title" not in page_a,
          "A's queue page shows A's titles only")

    # --- the push chain stamps the pushing user ---
    post_id = database.add_standalone_post("freeform", "push-seed", "wechat",
                                           "正文", user_id=uid)
    # Online state is built through the endpoint, never by reaching into
    # record_heartbeat: the endpoint is the stable seam across refactors.
    r = agent.post("/agent/heartbeat", headers=A, json={
        "agent": "gate", "tools": {"wechatsync": {"available": True}}})
    check(r.status_code == 200, "heartbeat with a valid token is 200")
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    body = r.get_json()
    check(r.status_code == 200 and body.get("job_id"),
          "an online push for the session user queues a job")
    job = database.get_agent_job(body["job_id"], db_path=db_path)
    check(job["user_id"] == uid,
          "a pushed job carries the pushing user's id")

    # --- the heartbeat refreshes the token's last_seen ---
    rows = database.list_agent_tokens(uid, db_path=db_path)
    # token-a is the only active token for uid; the revoked one is skipped.
    # The table never hands out plaintext or hashes, so count, don't read.
    active = [r for r in rows if not r["revoked"]]
    check(len(active) == 1 and active[0]["last_seen"] is not None,
          "a heartbeat refreshes the caller token's last_seen")
    rows_b = database.list_agent_tokens(uid_b, db_path=db_path)
    check(all(r["last_seen"] is None for r in rows_b),
          "another user's token is not touched by someone else's heartbeat")

    # --- pairing: a code is bound to its minter, the redeem mints the
    # redeemer's own token — the extension protocol shape never changes ---
    import agent_hub
    r = client.post("/agent-jobs/pair-code")
    body = r.get_json()
    check(r.status_code == 200 and len(body["code"]) == 8,
          "A mints an eight-character pairing code through the session")
    code_a = body["code"]
    r = agent.post("/agent/pair", json={"code": code_a})
    paired = r.get_json()
    check(r.status_code == 200 and isinstance(paired.get("token"), str)
          and len(paired["token"]) >= 40,
          "an anonymous client redeems A's code for a fresh long token")
    token_paired_a = paired["token"]
    check(database.find_agent_user_by_token(token_paired_a, db_path=db_path)
          == uid,
          "the token from A's code resolves to A, its minter (谁配对归谁)")
    check("user_id" not in paired and set(paired) == {"token"},
          "the pair response carries only the token — no new protocol keys")
    with sqlite3.connect(db_path) as conn:
        blob = " ".join(str(v) for row in conn.execute(
            "SELECT * FROM agent_tokens").fetchall() for v in row)
    check(token_paired_a not in blob,
          "the plaintext token appears nowhere in agent_tokens (hash only)")

    r = agent.post("/agent/pair", json={"code": code_a})
    check(r.status_code == 403, "a pairing code is single-use — second redeem 403")

    # Case and stray-space tolerance is part of the human-typed contract.
    r = client.post("/agent-jobs/pair-code")
    code_tol = r.get_json()["code"]
    spaced = " ".join(code_tol.lower())
    r = agent.post("/agent/pair", json={"code": spaced})
    check(r.status_code == 200,
          "redeem still tolerates lower case and inserted spaces")

    # Five wrong guesses burn the pending code; a locked code stays 403.
    r = client.post("/agent-jobs/pair-code")
    code_burn = r.get_json()["code"]
    for _ in range(5):
        r = agent.post("/agent/pair", json={"code": "wrongwrong"})
    check(r.status_code == 403 and agent_hub._PENDING_PAIRING["code"] is None,
          "five wrong tries kill the pending code")
    check(agent.post("/agent/pair", json={"code": code_burn}).status_code == 403,
          "a burned code no longer redeems")

    # An expired code is refused.
    r = client.post("/agent-jobs/pair-code")
    code_exp = r.get_json()["code"]
    agent_hub._PENDING_PAIRING["expires"] = time.time() - 1
    check(agent.post("/agent/pair", json={"code": code_exp}).status_code == 403,
          "an expired code is refused")

    # B's code yields B's token: the two minted tokens are distinct and each
    # resolves to its own minter.
    r = client_b.post("/agent-jobs/pair-code")
    code_b = r.get_json()["code"]
    r = agent.post("/agent/pair", json={"code": code_b})
    token_paired_b = r.get_json()["token"]
    check(r.status_code == 200 and token_paired_b != token_paired_a,
          "B's redeem mints a different token than A's")
    check(database.find_agent_user_by_token(token_paired_b, db_path=db_path)
          == uid_b,
          "the token from B's code resolves to B")
    check(database.find_agent_user_by_token(token_paired_a, db_path=db_path)
          == uid,
          "A's minted token still resolves to A after B paired")

    print("agent token identity checks passed")
    print("CHANNEL_ISOLATION_TOKENS_OK")


def section_heartbeat():
    """Heartbeats live in per-user buckets: login states, the online pill and
    the compose selector only ever read the viewer's own bucket. Two users,
    two tokens: A's report never moves B's lights, one going stale leaves the
    other untouched, and a bucket whose every beat expired is deleted outright
    so a stale user's platforms leak into nobody's view.
    """
    directory, database, web, publisher, client = isolated_app()
    db_path = database.DB_PATH
    uid = database.get_user_by_username("gate")["id"]
    uid_b = database.create_user("hb_b", generate_password_hash("p"),
                                 db_path=db_path)
    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = uid_b
    # The heartbeat door is outside the session wall, so a sessionless
    # client is the extension's honest stand-in.
    agent = web.app.test_client()
    import agent_hub

    database.create_agent_token(uid, "token-a", db_path=db_path)
    database.create_agent_token(uid_b, "token-b", db_path=db_path)
    A = {"Authorization": "Bearer token-a"}
    B = {"Authorization": "Bearer token-b"}

    # --- A's report drives A's view; B, without its own beat, is all offline ---
    r = agent.post("/agent/heartbeat", headers=A, json={
        "tools": {"extension": {"available": True, "platforms": [
            {"id": "wechat", "logged_in": True, "probed": True}]}}})
    check(r.status_code == 200, "token-a posts a heartbeat")
    data = client.get("/agent/login-states.json").get_json()
    check(data["platforms"]["wechat"]["state"] == "ok",
          "A's session reads its own report: wechat ok")
    data_b = client_b.get("/agent/login-states.json").get_json()
    check(all(e["state"] == "offline" for e in data_b["platforms"].values()),
          "B's session, without its own heartbeat, reads every platform offline")

    # --- one user going stale leaves the other untouched ---
    stale = time.time() - (agent_hub.HEARTBEAT_WINDOW_SECONDS + 1)
    agent_hub._LAST_HEARTBEATS[uid]["t"] = stale
    for beat in agent_hub._USER_TOOL_BEATS[uid].values():
        beat["t"] = stale
    check(agent_hub.agent_online() is False,
          "agent_online(None) is False: no identity, no state")
    check(agent_hub.agent_online(uid) is False,
          "A's own bucket decides A is offline")
    r = agent.post("/agent/heartbeat", headers=B, json={
        "tools": {"extension": {"available": True, "platforms": [
            {"id": "zhihu", "logged_in": True, "probed": True}]}}})
    check(r.status_code == 200, "token-b posts a heartbeat")
    check(agent_hub.agent_online(uid_b) is True,
          "B's own bucket decides B is online while A is stale")
    data_b = client_b.get("/agent/login-states.json").get_json()
    check(data_b["platforms"]["zhihu"]["state"] == "ok",
          "B's view follows B's fresh report, not A's staleness")
    check("wechat" not in agent_hub.platform_login_states(uid_b),
          "B's platform states do not contain the platform A reported")
    check(agent_hub.platform_login_states() == {},
          "platform_login_states(None) is the empty view, not everyone's")

    # --- expiry empties the whole bucket and deletes the user key ---
    check(agent_hub.platform_login_states(uid) == {},
          "A's every beat past the window leaves A's platform states empty")
    check(uid not in agent_hub._USER_TOOL_BEATS,
          "the emptied bucket is deleted: no stale per-user leak")

    # --- the compose selector follows the viewer's own login states ---
    r = agent.post("/agent/heartbeat", headers=A, json={
        "tools": {"extension": {"available": True, "platforms": [
            {"id": "wechat", "logged_in": True, "probed": True}]}}})
    check(r.status_code == 200, "token-a heartbeats again before the page read")
    wechat_a = re.search(r'id="platform-wechat"[^>]*>',
                         client.get("/compose").get_data(as_text=True))
    check(wechat_a and "disabled" not in wechat_a.group(0),
          "A's compose leaves the wechat checkbox enabled (A's wechat ok)")
    wechat_b = re.search(r'id="platform-wechat"[^>]*>',
                         client_b.get("/compose").get_data(as_text=True))
    check(wechat_b and "disabled" in wechat_b.group(0),
          "B's compose disables the wechat checkbox (B has no heartbeat)")

    # --- status.json reports the session user's own online state ---
    status_a = client.get("/agent/status.json").get_json()
    check(status_a["online"] is True and status_a["age"] < 5,
          "A's status.json reads online with A's heartbeat age")
    status_b = client_b.get("/agent/status.json").get_json()
    check(status_b["online"] is True and status_b["age"] < 5,
          "B's status.json reads online with B's heartbeat age")
    agent_hub._LAST_HEARTBEATS[uid]["t"] = stale
    status_a = client.get("/agent/status.json").get_json()
    check(status_a["online"] is False,
          "A's status.json follows A's own bucket going stale")
    status_b = client_b.get("/agent/status.json").get_json()
    check(status_b["online"] is True,
          "B's status.json is untouched by A going stale")

    print("heartbeat per-user bucket checks passed")
    print("CHANNEL_ISOLATION_HEARTBEAT_OK")


def section_accounts():
    """social_accounts is scoped per user: the unique key widens to include
    user_id, legacy rows settle with admin on upgrade, the /accounts page and
    the list read only the viewer's rows, and the token manager on the same
    page revokes one's own tokens and nobody else's.
    """
    directory, database, web, publisher, client = isolated_app()
    db_path = database.DB_PATH
    uid = database.get_user_by_username("gate")["id"]
    uid_b = database.create_user("acct_b", generate_password_hash("p"),
                                 db_path=db_path)
    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = uid_b

    # --- schema: the unique key is (platform, external_id, user_id) --------
    with sqlite3.connect(db_path) as conn:
        cols = _table_columns(conn, "social_accounts")
        check("user_id" in cols, "social_accounts missing column user_id")
        idx_cols = [r[2] for r in conn.execute(
            "PRAGMA index_info(idx_social_accounts_identity_user)").fetchall()]
        check(idx_cols == ["platform", "external_id", "user_id"],
              f"unique index is not (platform, external_id, user_id): {idx_cols}")

    # --- same (platform, external_id) for two users coexists (场景 4) -------
    account_admin = database.upsert_social_account(
        "wechat", "wx-1", display_name="Admin 公众号", label="Admin 号",
        user_id=uid, db_path=db_path)
    account_b = database.upsert_social_account(
        "wechat", "wx-1", display_name="B 公众号", label="B 号",
        user_id=uid_b, db_path=db_path)
    check(account_admin != account_b,
          "the same (platform, external_id) for two users collapsed into one row")

    # --- a legacy pre-migration row (user_id NULL) settles with admin ------
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO social_accounts (platform, external_id, label, user_id) "
            "VALUES ('zhihu', 'legacy-1', '存量知乎', NULL)")
        conn.commit()
    database.init_db(db_path)  # the upgrade pass every boot runs
    with sqlite3.connect(db_path) as conn:
        legacy_uid = conn.execute(
            "SELECT user_id FROM social_accounts "
            "WHERE platform = 'zhihu' AND external_id = 'legacy-1'"
        ).fetchone()[0]
    check(legacy_uid == uid,
          "a legacy NULL-user row did not settle with admin on upgrade")

    # --- the list only serves the viewer's rows -----------------------------
    ids_admin = {r["id"] for r in
                 database.list_social_accounts(user_id=uid, db_path=db_path)}
    ids_b = {r["id"] for r in
             database.list_social_accounts(user_id=uid_b, db_path=db_path)}
    check(account_b in ids_b and account_admin not in ids_b,
          "B's list leaks admin's account (or drops B's own)")
    check(account_admin in ids_admin and account_b not in ids_admin,
          "admin's list leaks B's account (or drops admin's own)")

    # --- the /accounts page is per user --------------------------------------
    page_admin = client.get("/accounts").get_data(as_text=True)
    page_b = client_b.get("/accounts").get_data(as_text=True)
    check("Admin 号" in page_admin,
          "admin's accounts page does not show admin's own account")
    check("Admin 号" not in page_b and "Admin 公众号" not in page_b
          and "wx-1" not in page_b,
          "B's accounts page leaks admin's account label or external_id")
    check("B 号" in page_b, "B's accounts page does not show B's own account")

    # --- token manager: own tokens listed, revocation per user (场景 6) -----
    admin_token = database.create_agent_token(uid, "tok-admin", "Admin 浏览器",
                                              db_path=db_path)
    b_token = database.create_agent_token(uid_b, "tok-b", "B 的浏览器",
                                          db_path=db_path)
    b_other = database.create_agent_token(uid_b, "tok-b2", "B 的另一台",
                                          db_path=db_path)

    page_b = client_b.get("/accounts").get_data(as_text=True)
    check("已配对的浏览器扩展" in page_b and "B 的浏览器" in page_b
          and "吊销" in page_b,
          "the accounts page does not list the user's own paired extensions")
    check("Admin 浏览器" not in page_b,
          "B's token list leaks admin's extension label")

    # B revoking admin's token and a missing token: the same 404 body.
    r_cross = client_b.post(f"/accounts/tokens/{admin_token}/revoke")
    r_missing = client_b.post("/accounts/tokens/999999/revoke")
    check(r_cross.status_code == 404 and r_missing.status_code == 404,
          "revoking another user's token or a missing one is not 404")
    check(r_cross.get_data() == r_missing.get_data(),
          "the two revoke 404 bodies are byte-for-byte identical")
    check(database.find_agent_user_by_token("tok-admin", db_path=db_path) == uid,
          "B's revoke attempt must not kill admin's token")

    # B revokes its own: 200, the token dies immediately, siblings survive.
    r = client_b.post(f"/accounts/tokens/{b_token}/revoke")
    check(r.status_code == 200 and r.get_json() == {"success": True},
          "revoking one's own token does not answer success")
    check(database.find_agent_user_by_token("tok-b", db_path=db_path) is None,
          "a revoked token still resolves (must 401 on next use)")
    check(database.find_agent_user_by_token("tok-b2", db_path=db_path) == uid_b,
          "revoking one token disturbed the user's other token")
    check(any(r["id"] == b_token and r["revoked"]
              for r in database.list_agent_tokens(uid_b, db_path=db_path)),
          "the revoked token is not marked revoked in the list")

    print("social_accounts per-user isolation and token manager checks passed")
    print("CHANNEL_ISOLATION_ACCOUNTS_OK")


def section_publish():
    """The push route judges offline on the post owner's own heartbeat.

    Another user's online agent never unlocks my push, a scheduled push queues
    regardless of anyone's heartbeat and under the owner's id, and the compose
    target chips / default account resolution only ever see the viewer's own
    connected accounts.
    """
    directory, database, web, publisher, client = isolated_app()
    db_path = database.DB_PATH
    uid = database.get_user_by_username("gate")["id"]
    uid_b = database.create_user("pub_b", generate_password_hash("p"),
                                db_path=db_path)
    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = uid_b
    # The heartbeat door is outside the session wall, so a sessionless
    # client is the extension's honest stand-in.
    agent = web.app.test_client()

    database.create_agent_token(uid, "token-a", db_path=db_path)
    database.create_agent_token(uid_b, "token-b", db_path=db_path)
    A = {"Authorization": "Bearer token-a"}
    B = {"Authorization": "Bearer token-b"}

    post_id = database.add_standalone_post("freeform", "pub-seed", "wechat",
                                            "正文", user_id=uid)

    # --- A offline while B heartbeats: A's push is still the 409 ---
    r = agent.post("/agent/heartbeat", headers=B, json={
        "tools": {"wechatsync": {"available": True}}})
    check(r.status_code == 200, "token-b posts a heartbeat")
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    check(r.status_code == 409 and "推送离线" in r.get_json()["error"],
          "another user's online agent does not unlock the owner's push (409)")

    # --- a scheduled push queues while the owner is offline ---
    sched_id = database.add_standalone_post("freeform", "pub-seed-sched",
                                            "wechat", "正文", user_id=uid)
    r = client.post(f"/compose/post/{sched_id}/push",
                    data={"target": "wechat", "run_at": "2099-01-01T09:30"})
    body = r.get_json()
    check(r.status_code == 200 and body.get("job_id"),
          "a scheduled push queues while the owner has no heartbeat")
    job = database.get_agent_job(body["job_id"], db_path=db_path)
    check(job["user_id"] == uid,
          "the scheduled push's job carries the owner's id")
    r = agent.post("/agent/jobs/claim", headers=B, json={"agent": "b"})
    check(r.status_code == 204,
          "token-b claims the owner's scheduled job is a 204, never a handover")

    # --- A online through its own heartbeat: the instant push queues ---
    r = agent.post("/agent/heartbeat", headers=A, json={
        "tools": {"wechatsync": {"available": True}}})
    check(r.status_code == 200, "token-a posts a heartbeat")
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    body = r.get_json()
    check(r.status_code == 200 and body.get("job_id"),
          "the owner's own heartbeat unlocks the push")
    job = database.get_agent_job(body["job_id"], db_path=db_path)
    check(job["user_id"] == uid,
          "the instant push's job carries the owner's id")
    r = agent.post("/agent/jobs/claim", headers=B, json={"agent": "b"})
    check(r.status_code == 204,
          "token-b claims the owner's instant job is a 204, never a handover")

    # --- the compose target chips and default resolution are per user -------
    account_admin = database.upsert_social_account(
        "wechat", "pub-wx-1", display_name="Admin 公众号", label="Admin 推送号",
        user_id=uid, db_path=db_path)
    # A second admin login on the same platform, so the visible labelled chip
    # (show_label) renders on admin's page and not only the JSON payload.
    database.upsert_social_account(
        "wechat", "pub-wx-2", display_name="Admin 小号", label="Admin 小号",
        user_id=uid, db_path=db_path)
    account_b = database.upsert_social_account(
        "wechat", "pub-wx-b", display_name="B 公众号", label="B 推送号",
        user_id=uid_b, db_path=db_path)
    # A platform only admin holds an account on.
    account_admin_zhihu = database.upsert_social_account(
        "zhihu", "pub-zh-1", label="Admin 知乎号", user_id=uid, db_path=db_path)

    def _targets_of(page):
        m = re.search(r"const PUBLISH_TARGETS = (.*?);", page)
        return json.loads(m.group(1)) if m else None

    page_a = client.get("/compose").get_data(as_text=True)
    check("Admin 推送号" in page_a and "Admin 小号" in page_a,
          "admin's compose renders its own labelled account chips")
    targets_a = _targets_of(page_a)
    check(targets_a is not None
          and {t["account_id"] for t in targets_a} >= {account_admin}
          and account_b not in {t["account_id"] for t in targets_a},
          "admin's compose offers only admin's account chips")
    page_b = client_b.get("/compose").get_data(as_text=True)
    check("Admin 推送号" not in page_b and "Admin 小号" not in page_b
          and "Admin 知乎号" not in page_b,
          "B's compose renders neither admin's account label nor its chip")
    targets_b = _targets_of(page_b)
    check(targets_b is not None
          and account_admin not in {t["account_id"] for t in targets_b},
          "B's compose offers neither admin's account label nor its chip value")
    check(account_b in {t["account_id"] for t in targets_b},
          "B's compose still offers B's own account chip")

    from flask import session as flask_session
    with web.app.test_request_context("/compose"):
        flask_session["user_id"] = uid_b
        check(web._default_account_for("wechat") == account_b,
              "B's own default account still resolves for B")
        check(web._default_account_for("zhihu") is None,
              "a platform only admin holds resolves to no account for B")
    with web.app.test_request_context("/compose"):
        flask_session["user_id"] = uid
        check(web._default_account_for("zhihu") is not None,
              "admin still resolves its own zhihu account")
        check(web._default_account_for("wechat") == account_admin,
              "admin resolves its own default wechat account")

    # --- resolve_account_id: the same scoping at the write inlet -----------
    # B naming no account on a platform only admin holds records no account
    # (never admin's id); admin's explicit id is a foreign id for B; B's own
    # account still resolves for B.
    r = client_b.post("/compose/post/create",
                      data={"platforms": "zhihu", "content": "B 裸平台名"})
    check(r.status_code == 200, "B creates a bare-platform zhihu post")
    for pid in r.get_json()["post_ids"]:
        row = database.get_standalone_post(pid, uid_b)
        check(row["account_id"] is None,
              "B's bare zhihu platform must not resolve to admin's account")
    r = client_b.post("/compose/post/create",
                      data={"targets": f"zhihu:{account_admin_zhihu}",
                            "content": "B 传 admin 账号 id"})
    check(r.status_code == 400 and "not connected" in r.get_json()["error"],
          "B hand-crafting admin's account id is the not-connected refusal")
    r = client_b.post("/compose/post/create",
                      data={"targets": f"wechat:{account_b}",
                            "content": "B 自己的账号"})
    check(r.status_code == 200
          and all(t["account_id"] == account_b for t in r.get_json()["targets"]),
          "B's own named account still resolves for B")

    # --- the queue page annotates why a pending job is still pending -------
    # Derived at render time from the post owner's own bucket, never stored:
    # another user's online report never clears it, the owner's own report
    # clears exactly the platforms they reported, and a finished job is
    # history and carries no diagnosis.
    jid_p = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "pending-reason-title",
                    "items": [{"target": "wechat", "content": "x"},
                              {"target": "zhihu", "content": "y"}]}),
        user_id=uid, db_path=db_path)
    page = client.get("/agent-jobs").get_data(as_text=True)
    check("pending-reason-title" in page, "the queue page lists the new job")
    check("平台未连接/登录态异常" in page and "公众号" in page and "知乎" in page,
          "a pending job with no platform states shows per-platform reasons")

    # B's online report is another browser: it never clears A's reasons.
    r = agent.post("/agent/heartbeat", headers=B, json={
        "tools": {"extension": {"available": True, "platforms": [
            {"id": "wechat", "logged_in": True, "probed": True},
            {"id": "zhihu", "logged_in": True, "probed": True}]}}})
    check(r.status_code == 200, "token-b posts a logged-in heartbeat")
    page = client.get("/agent-jobs").get_data(as_text=True)
    check("公众号：平台未连接/登录态异常" in page
          and "知乎：平台未连接/登录态异常" in page,
          "another user's online report never clears the owner's reasons")

    # A's own report clears exactly the platforms A reported.
    r = agent.post("/agent/heartbeat", headers=A, json={
        "tools": {"extension": {"available": True, "platforms": [
            {"id": "wechat", "logged_in": True, "probed": True}]}}})
    check(r.status_code == 200, "token-a reports wechat logged_in")
    page = client.get("/agent-jobs").get_data(as_text=True)
    check("公众号：平台未连接/登录态异常" not in page,
          "the owner's own wechat report clears the wechat reason")
    check("知乎：平台未连接/登录态异常" in page,
          "the platform the owner did not report keeps its reason")

    # A finished job is history, not a diagnosis.
    r = agent.post(f"/agent/jobs/{jid_p}/events", headers=A,
                   json={"type": "done", "results": [
                       {"platform": "wechat", "success": True},
                       {"platform": "zhihu", "success": True}]})
    check(r.status_code == 200, "token-a reports the job done")
    page = client.get("/agent-jobs").get_data(as_text=True)
    check("平台未连接/登录态异常" not in page,
          "a succeeded job carries no pending reasons")

    print("publish route owner-scoped 409/queue and target chip checks passed")
    print("CHANNEL_ISOLATION_PUBLISH_OK")


SECTIONS = {
    "schema": section_schema,
    "tokens": section_tokens,
    "heartbeat": section_heartbeat,
    "accounts": section_accounts,
    "publish": section_publish,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SECTIONS:
        print(f"usage: check_channel_isolation.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
