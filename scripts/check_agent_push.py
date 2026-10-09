"""Gate for the compose push route and the zhihu/toutiao platform additions.

    python scripts/check_agent_push.py    AGENT_PUSH_OK

The push route is the seam between the compose card and the queue: it must
refuse when the agent is offline, dedupe through domestic_publisher, and
refuse platforms outside the domestic article set.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub

    # A wechat card to push.
    post_id = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                           "正文")

    # --- agent offline: refuse with a readable error ---
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    check(r.status_code == 409 and "离线" in r.get_json()["error"],
          "pushing while the agent is offline is a 409 with a hint")

    # --- but a scheduled push queues even while offline ---
    r = client.post(f"/compose/post/{post_id}/push",
                    data={"target": "wechat",
                          "run_at": "2099-01-01T09:30"})
    check(r.status_code == 200, "a scheduled push queues while offline")
    job = database.get_agent_job(r.get_json()["job_id"])
    check(job["run_after"] and job["run_after"].startswith("2099-01-01T"),
          "the local run_at landed on run_after as UTC")
    r = client.post(f"/compose/post/{post_id}/push",
                    data={"target": "wechat", "run_at": "not-a-date"})
    check(r.status_code == 400, "a malformed run_at is a 400")

    # --- online: enqueues, dedupes, returns the job ---
    agent_hub.record_heartbeat({"tools": {"wechatsync": {"available": True}}})
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    body = r.get_json()
    check(r.status_code == 200 and body["job_id"] > 0 and
          body["already"] is False, "an online push enqueues a job")
    r2 = client.post(f"/compose/post/{post_id}/push", data={"target": "wechat"})
    check(r2.get_json()["already"] is True and
          r2.get_json()["job_id"] == body["job_id"],
          "a second push returns the same job")

    # --- the job polls back through the .json endpoint ---
    r = client.get(f"/agent-jobs/{body['job_id']}.json")
    check(r.status_code == 200 and r.get_json()["status"] == "pending",
          "the job status endpoint serves the queue row")

    # --- target validation ---
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "linkedin"})
    check(r.status_code == 400, "a non-domestic target is a 400")
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "douyin"})
    check(r.status_code == 400,
          "a domestic but non-article platform is also refused")

    # --- zhihu/toutiao are now compose platforms ---
    from insights_web import COMPOSE_PLATFORMS
    check("zhihu" in COMPOSE_PLATFORMS and "toutiao" in COMPOSE_PLATFORMS,
          "zhihu and toutiao joined the compose platform set")
    r = client.get("/compose")
    check(b'platform-zhihu' in r.data and b'platform-toutiao' in r.data,
          "the compose page renders checkboxes for the new platforms")

    print("AGENT_PUSH_OK")


if __name__ == "__main__":
    main()
