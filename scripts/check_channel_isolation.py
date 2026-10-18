"""Multi-user publishing channel gate: schema / data / ... sections.

    python scripts/check_channel_isolation.py schema   CHANNEL_ISOLATION_SCHEMA_OK

``schema`` pins the agent_tokens table and the legacy global token migration.
"""

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


SECTIONS = {
    "schema": section_schema,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SECTIONS:
        print(f"usage: check_channel_isolation.py {'|'.join(SECTIONS)}")
        sys.exit(2)
    SECTIONS[name]()
