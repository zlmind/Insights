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

    # --- run_at is read in the browser's timezone, via the submitted offset ---
    # The form posts datetime-local's naive wall time plus
    # Date#getTimezoneOffset() in minutes (UTC+8 -> -480), so the server never
    # guesses the zone from where it happens to run.
    tz_post = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                           "正文")
    r = client.post(f"/compose/post/{tz_post}/push",
                    data={"target": "wechat", "run_at": "2099-01-01T09:30",
                          "tz_offset": "-480"})
    check(r.status_code == 200 and r.get_json()["already"] is False,
          "a scheduled push carrying the browser offset queues")
    job = database.get_agent_job(r.get_json()["job_id"])
    check(job["run_after"] and job["run_after"].startswith("2099-01-01T01:30"),
          "a UTC+8 browser's 09:30 lands as 01:30Z whatever the server runs")
    utc_post = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                            "正文")
    r = client.post(f"/compose/post/{utc_post}/push",
                    data={"target": "wechat", "run_at": "2099-01-01T09:30",
                          "tz_offset": "0"})
    job = database.get_agent_job(r.get_json()["job_id"])
    check(job["run_after"] and job["run_after"].startswith("2099-01-01T09:30"),
          "a UTC browser's 09:30 lands as 09:30Z")

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
    # A wechat card aimed at zhihu would push the row's copy to the wrong
    # box: the card's own platform is the only target its content is for.
    r = client.post(f"/compose/post/{post_id}/push", data={"target": "zhihu"})
    check(r.status_code == 400 and "不是这个平台" in r.get_json()["error"],
          "pushing a card at a platform its row is not for is a 400")

    # --- a platform the admin did not enable is refused ---
    zhihu_post = database.add_standalone_post("freeform", "gate-seed", "zhihu",
                                              "正文")
    database.set_setting("WECHATSYNC_ENABLED_PLATFORMS", "wechat",
                         db_path=database.DB_PATH)
    r = client.post(f"/compose/post/{zhihu_post}/push",
                    data={"target": "zhihu"})
    check(r.status_code == 400 and "启用" in r.get_json()["error"],
          "a platform not enabled on the settings page is a 400 with the reason")
    database.delete_setting("WECHATSYNC_ENABLED_PLATFORMS",
                            db_path=database.DB_PATH)

    # --- zhihu/toutiao are now compose platforms ---
    from insights_web import COMPOSE_PLATFORMS
    check("zhihu" in COMPOSE_PLATFORMS and "toutiao" in COMPOSE_PLATFORMS,
          "zhihu and toutiao joined the compose platform set")
    r = client.get("/compose")
    check(b'platform-zhihu' in r.data and b'platform-toutiao' in r.data,
          "the compose page renders checkboxes for the new platforms")

    # --- the scheduled-push entry renders for domestic article platforms ---
    agent_hub._LAST_HEARTBEAT["t"] = None          # back to offline
    html = client.get("/compose").get_data(as_text=True)
    check('btn-agent-schedule' in html,
          "the scheduled push entry renders next to the push button")
    check('datetime-local' in html,
          "the schedule entry carries a datetime-local input")

    print("AGENT_PUSH_OK")


if __name__ == "__main__":
    main()
