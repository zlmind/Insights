"""Gate for the login-states view and its endpoint.

    python scripts/check_login_states.py    LOGIN_STATES_OK

The strip's data merges every executor's heartbeat of the viewer's own
bucket: probed reports beat unprobed ones, the freshest wins within a class,
and a dead heartbeat turns every platform offline — never a stale "logged
in". Another user's heartbeat is another browser and lights nobody's strip.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

AUTH = {"Authorization": "Bearer secret-token"}


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub
    database.set_setting("AGENT_TOKEN", "secret-token")
    # The bearer door resolves tokens from agent_tokens, so the setting above
    # only becomes usable through init_db's one-time legacy migration.
    database.init_db(database.DB_PATH)
    uid = database.get_user_by_username("gate")["id"]

    # --- no heartbeat at all: every platform offline, urls still present ---
    data = client.get("/agent/login-states.json").get_json()
    check(data["checked_at"] is None, "without a heartbeat checked_at is null")
    for pid, entry in data["platforms"].items():
        check(entry["state"] == "offline",
              f"{pid} is offline without any heartbeat")
        check(bool(entry["login_url"]), f"{pid} still carries its login url")

    # --- a live heartbeat: per-platform truth, unknown where not probed ---
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True,
            "platforms": [
                {"id": "wechat", "logged_in": True, "probed": True},
                {"id": "zhihu", "logged_in": False, "probed": True},
                {"id": "douyin", "logged_in": False, "probed": False}]}}})
    data = client.get("/agent/login-states.json").get_json()
    check(data["platforms"]["wechat"]["state"] == "ok",
          "a probed logged-in platform reads ok")
    check(data["platforms"]["zhihu"]["state"] == "logged_out",
          "a probed logged-out platform reads logged_out")
    check(data["platforms"]["douyin"]["state"] == "unknown",
          "an unprobed report reads unknown, never logged_out")
    check(data["platforms"]["xiaohongshu"]["state"] == "unknown",
          "a platform nobody reported reads unknown")
    check(isinstance(data["checked_at"], int) and data["checked_at"] < 5,
          "checked_at is the freshest heartbeat age in seconds")

    # Online but tools-less — never offline: fresh reports keep standing,
    # platforms nobody reported stay unknown.
    client.post("/agent/heartbeat", headers=AUTH, json={"agent": "bare"})
    data = client.get("/agent/login-states.json").get_json()
    check(data["platforms"]["wechat"]["state"] == "ok",
          "a tools-less heartbeat keeps the ext's fresh probed report")
    check(data["platforms"]["xiaohongshu"]["state"] == "unknown",
          "a platform no tool reported stays unknown, not offline")
    check(data["checked_at"] is not None,
          "an online tools-less heartbeat still stamps checked_at")

    # Online with no tool report anywhere: unknown everywhere, still not
    # offline — "no data" and "nobody connected" are different sentences.
    agent_hub._USER_TOOL_BEATS.pop(uid, None)
    data = client.get("/agent/login-states.json").get_json()
    check(all(e["state"] == "unknown" for e in data["platforms"].values()),
          "online with no tool reports yields unknown everywhere")
    check(data["checked_at"] is not None, "checked_at survives with no tools")

    # The 60s window boundary — inside is online, past it everything flips.
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True,
            "platforms": [{"id": "wechat", "logged_in": True, "probed": True}]}}})
    agent_hub._LAST_HEARTBEATS[uid]["t"] = time.time() - 59.5
    check(client.get("/agent/login-states.json").get_json()
          ["platforms"]["wechat"]["state"] == "ok",
          "59.5s-old heartbeat is still online")
    agent_hub._LAST_HEARTBEATS[uid]["t"] = time.time() - 60.5
    data = client.get("/agent/login-states.json").get_json()
    check(all(e["state"] == "offline" for e in data["platforms"].values()),
          "60.5s-old heartbeat turns every platform offline")
    check(client.get("/agent/login-states.json").get_json()["checked_at"] is None,
          "an offline heartbeat nulls checked_at")

    # --- login states are per user: another user's beat is invisible ---
    # A's beat just expired above, so A's strip is offline; a fresh beat from
    # B lights B's strip and must not light A's.
    uid_b = database.create_user("ls_b", generate_password_hash("p"),
                                 db_path=database.DB_PATH)
    database.create_agent_token(uid_b, "token-ls-b", db_path=database.DB_PATH)
    client_b = web.app.test_client()
    with client_b.session_transaction() as session:
        session["user_id"] = uid_b
    web.app.test_client().post("/agent/heartbeat",
                               headers={"Authorization": "Bearer token-ls-b"},
                               json={"agent": "insights-ext",
                                     "tools": {"extension": {
                                         "available": True,
                                         "platforms": [{"id": "wechat",
                                                        "logged_in": True,
                                                        "probed": True}]}}})
    data_b = client_b.get("/agent/login-states.json").get_json()
    check(data_b["platforms"]["wechat"]["state"] == "ok",
          "B's own heartbeat reads ok on B's strip")
    data_a = client.get("/agent/login-states.json").get_json()
    check(data_a["platforms"]["wechat"]["state"] == "offline",
          "B's fresh heartbeat does not light A's strip")

    print("LOGIN_STATES_OK")


if __name__ == "__main__":
    main()
