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
        "article_sync", json.dumps({"items": [{"target": "wechat", "content": "x"}]}))
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

    print("AGENT_HUB_OK")


if __name__ == "__main__":
    main()
