"""Multi-user publishing channel gate: schema / data / ... sections.

    python scripts/check_channel_isolation.py schema   CHANNEL_ISOLATION_SCHEMA_OK
    python scripts/check_channel_isolation.py tokens   CHANNEL_ISOLATION_TOKENS_OK

``schema`` pins the agent_tokens table and the legacy global token migration;
``tokens`` pins the /agent/* door's per-user identity: claim, events and the
read paths only ever serve the token's own user, and cross-user reads answer
with the same bytes as reads of things that do not exist.
"""

import json
import os
import sqlite3
import sys
import tempfile

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

    print("agent token identity checks passed")
    print("CHANNEL_ISOLATION_TOKENS_OK")


SECTIONS = {
    "schema": section_schema,
    "tokens": section_tokens,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SECTIONS:
        print(f"usage: check_channel_isolation.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
