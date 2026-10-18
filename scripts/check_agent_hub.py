"""Gate for the /agent/* endpoints: bearer auth, claim, events, heartbeat.

    python scripts/check_agent_hub.py    AGENT_HUB_OK

The agent is a foreign client: no session, one shared token. A wrong or
missing token must get 401 on every endpoint, and the token must be read
through appconfig so the settings page can set it.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402

AUTH = {"Authorization": "Bearer secret-token"}


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub
    database.set_setting("AGENT_TOKEN", "secret-token")
    # The bearer door resolves tokens from agent_tokens, not the setting: the
    # legacy AGENT_TOKEN above only becomes claimable through init_db's
    # one-time migration, which is exactly the upgrade path real installs take.
    database.init_db(database.DB_PATH)
    uid = database.get_user_by_username("gate")["id"]

    # --- auth: every endpoint rejects a bad or missing token ---
    for method, path in (("post", "/agent/heartbeat"), ("post", "/agent/jobs/claim")):
        check(client.post(path).status_code == 401,
              f"{path} without a token is 401")
        check(client.post(path, headers={"Authorization": "Bearer nope"})
              .status_code == 401, f"{path} with a wrong token is 401")
    # A header arrives decoded as latin-1, so an attacker can put any byte in
    # it; that must stay a JSON 401, not a TypeError 500.
    r = client.post("/agent/jobs/claim",
                    headers={"Authorization": "Bearer tøken"})
    check(r.status_code == 401 and r.get_json()["error"] == "unauthorized",
          "a non-ASCII bearer header is a JSON 401, not a 500")

    # --- claim: empty queue is 204, a pending job comes back once ---
    check(client.post("/agent/jobs/claim", headers=AUTH).status_code == 204,
          "claim on an empty queue is 204")
    # A valid token with a JSON list body must stay a clean 204, not an
    # AttributeError 500 out of `.get` on a non-object body.
    check(client.post("/agent/jobs/claim", headers=AUTH,
                      json=[{"agent": "x"}]).status_code == 204,
          "a JSON list body claims cleanly instead of a 500")
    jid = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "wechat", "content": "x"}]}),
        user_id=uid)
    r = client.post("/agent/jobs/claim", headers=AUTH)
    check(r.status_code == 200 and r.get_json()["id"] == jid,
          "claim returns the pending job as JSON")
    check(client.post("/agent/jobs/claim", headers=AUTH).status_code == 204,
          "the claimed job is not handed out twice")

    # --- events move the job ---
    r = client.post(f"/agent/jobs/{jid}/events", headers=AUTH,
                    json={"type": "started", "agent": "layne-win"})
    check(r.status_code == 200, "started event is accepted")
    r = client.post(f"/agent/jobs/{jid}/events", headers=AUTH, json={
        "type": "done",
        "results": [{"platform": "wechat", "success": True, "draft": True}]})
    check(r.status_code == 200 and database.get_agent_job(jid)["status"]
          == "succeeded", "done event with all-success lands as succeeded")
    check(client.post(f"/agent/jobs/{jid}/events", headers=AUTH,
                      json={"type": "exploded"}).status_code == 400,
          "an unknown event type is a 400, not a 500")

    # --- events without the token are still 401 ---
    check(client.post(f"/agent/jobs/{jid}/events",
                      json={"type": "started"}).status_code == 401,
          "events endpoint checks the token too")

    # --- executor filtering: the extension and the old agent never take
    # each other's jobs, even though they share one queue ---
    ext_job = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "wechat", "content": "x"}],
                                    "executor": "extension"}), user_id=uid)
    old_job = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "zhihu", "content": "x"}]}),
        user_id=uid)
    r = client.post("/agent/jobs/claim", headers=AUTH,
                    json={"agent": "insights-ext", "executor": "extension",
                          "platforms": ["wechat"]})
    check(r.status_code == 200 and r.get_json()["id"] == ext_job,
          "the extension claims only extension-stamped jobs")
    r = client.post("/agent/jobs/claim", headers=AUTH, json={"agent": "layne-win"})
    check(r.status_code == 200 and r.get_json()["id"] == old_job,
          "the old agent's unmarked body still claims unstamped jobs")

    # A platform the extension does not declare is held, not handed over:
    # graduation moved the platform to the extension, so the old agent must
    # never pick it up, extension offline or not.
    database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "zhihu", "content": "x"}],
                                    "executor": "extension"}), user_id=uid)
    r = client.post("/agent/jobs/claim", headers=AUTH,
                    json={"agent": "insights-ext", "executor": "extension",
                          "platforms": ["wechat"]})
    check(r.status_code == 204,
          "a stamped job beyond the claimant's platforms is held")

    # A candidate with a broken payload must be skipped, not fatal.
    database.enqueue_agent_job("article_sync", "{not json", user_id=uid)
    r = client.post("/agent/jobs/claim", headers=AUTH, json={"agent": "layne-win"})
    check(r.status_code == 204,
          "a broken payload in the candidate window is skipped, not a 500")

    # A matching job behind a full window of the other lane's jobs must
    # still be found: the scan pages, it does not stop at the first batch.
    for _ in range(120):
        database.enqueue_agent_job(
            "article_sync", json.dumps({"items": [{"target": "wechat", "content": "x"}],
                                        "executor": "extension"}), user_id=uid)
    tail_job = database.enqueue_agent_job(
        "article_sync", json.dumps({"items": [{"target": "zhihu", "content": "x"}]}),
        user_id=uid)
    r = client.post("/agent/jobs/claim", headers=AUTH, json={"agent": "layne-win"})
    check(r.status_code == 200 and r.get_json()["id"] == tail_job,
          "a matching job behind 120 other-lane jobs is still claimed")

    # An extension-shaped heartbeat (tools keyed by the extension's name)
    # feeds the same login-state view. A platform entry without `probed`
    # reads as probed: the field post-dates the python agent's heartbeat,
    # and that agent's auth report is a real probe.
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True, "version": "0.1.0",
            "platforms": [{"id": "wechat", "logged_in": True}]}}})
    state = agent_hub.platform_login_states().get("wechat") or {}
    check(state.get("logged_in") is True and state.get("probed") is True,
          "a platform entry without probed still reads as a real probe")
    # A tools-less heartbeat is not a wipe: the extension's still-fresh
    # report keeps counting until its own window runs out.
    client.post("/agent/heartbeat", headers=AUTH, json={"agent": "x"})
    wechat = agent_hub.platform_login_states().get("wechat") or {}
    check(wechat.get("logged_in") is True,
          "a tools-less heartbeat leaves a fresh tool report standing")
    agent_hub._TOOL_BEATS["extension"]["t"] = time.time() - 120
    check(agent_hub.platform_login_states() == {},
          "a tool whose beat passed the window drops out of the view")

    # A probed report beats an unprobed one even when it is older.
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "insights-ext", "tools": {"extension": {
            "available": True,
            "platforms": [{"id": "zhihu", "logged_in": False, "probed": True}]}}})
    client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "layne-win", "tools": {"wechatsync": {
            "available": True,
            "platforms": [{"id": "zhihu", "logged_in": True, "probed": False}]}}})
    zhihu = agent_hub.platform_login_states().get("zhihu") or {}
    check(zhihu.get("logged_in") is False and zhihu.get("probed") is True,
          "a probed report wins over a fresher unprobed one")

    # --- heartbeat: snapshot and staleness ---
    r = client.post("/agent/heartbeat", headers=AUTH, json={
        "agent": "layne-win", "tools": {"wechatsync": {
            "available": True,
            "platforms": [{"id": "wechat", "logged_in": True}]}}})
    check(r.status_code == 200, "heartbeat is accepted")
    snap = agent_hub.heartbeat_snapshot()
    check(snap and snap["age"] < 5 and
          snap["tools"]["wechatsync"]["platforms"][0]["id"] == "wechat",
          "snapshot carries tools and a fresh age")
    check(agent_hub.agent_online(window=60) is True, "a fresh heartbeat is online")
    agent_hub._LAST_HEARTBEAT["t"] = time.time() - 120
    check(agent_hub.agent_online(window=60) is False,
          "a heartbeat older than the window is offline")

    # The agent polls every few seconds; its requests must not fill the
    # activity log (agent_job_events already keeps its own durable record).
    check(database.list_activity(action="agent_heartbeat",
                                 db_path=database.DB_PATH) == [],
          "agent requests are not written to the activity log")

    # --- pairing: the extension bootstraps its token with a one-time code ---
    code = agent_hub.create_pairing_code()
    check(len(code) == 8, "a pairing code is eight characters")
    r = client.post("/agent/pair", json={"code": code})
    check(r.status_code == 200 and r.get_json()["token"] == "secret-token",
          "a valid code redeems the configured token exactly once")
    r = client.post("/agent/pair", json={"code": code})
    check(r.status_code == 403,
          "a code is single-use — the second redeem is refused")

    code = agent_hub.create_pairing_code()
    r = client.post("/agent/pair", json={"code": code.lower()})
    check(r.status_code == 200, "redeem tolerates case and is case-normalized")

    code = agent_hub.create_pairing_code()
    for _ in range(5):
        r = client.post("/agent/pair", json={"code": "wrongwrong"})
    check(r.status_code == 403 and agent_hub._PENDING_PAIRING["code"] is None,
          "five wrong tries kill the pending code")
    r = client.post("/agent/pair", json={"code": code})
    check(r.status_code == 403, "a locked-out code no longer redeems")

    code = agent_hub.create_pairing_code()
    agent_hub._PENDING_PAIRING["expires"] = time.time() - 1
    r = client.post("/agent/pair", json={"code": code})
    check(r.status_code == 403, "an expired code is refused")

    r = client.post("/agent/pair", json=["not", "an", "object"])
    check(r.status_code == 403, "a non-object body is refused, not a 500")

    # Minting a code is a session page action: an anonymous client gets the
    # login wall, the logged-in one gets a code.
    anon = web.app.test_client()
    r = anon.post("/agent-jobs/pair-code")
    check(r.status_code in (301, 302, 401),
          "minting a code requires the session")
    r = client.post("/agent-jobs/pair-code")
    check(r.status_code == 200 and len(r.get_json()["code"]) == 8,
          "a logged-in user mints an eight-character code")

    print("AGENT_HUB_OK")


if __name__ == "__main__":
    main()
