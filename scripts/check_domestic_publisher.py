"""Gate for the domestic push entry: payload shape and enqueue idempotency.

    python scripts/check_domestic_publisher.py    DOMESTIC_PUBLISHER_OK

A double click on the push button must not queue two jobs: the second call
returns the first job with already=True until it leaves the queue.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def _seed_post(database, content="# 标题\n\n正文段落，含引号\"和换行。\n"):
    pid = database.add_standalone_post("freeform", "gate-seed", "wechat", content)
    return database.get_standalone_post(pid)


def main():
    directory, database, web, publisher, client = isolated_app()
    import domestic_publisher as dp

    row = _seed_post(database)

    first = dp.enqueue_card_push(row)
    check(first["already"] is False, "first push queues a job")
    job = database.get_agent_job(first["job_id"])
    payload = json.loads(job["payload"])
    check(payload["items"] == [{"target": "wechat",
                                "content": row["content"]}],
          "payload carries the row's copy verbatim, one item per target")
    check(job["kind"] == "article_sync", "push jobs are article_sync")

    second = dp.enqueue_card_push(row)
    check(second["job_id"] == first["job_id"] and second["already"] is True,
          "a queued repeat push returns the same job with already=True")

    # After the job leaves the queue, the same card may be pushed again.
    database.record_agent_job_event(first["job_id"], {"type": "started"})
    database.record_agent_job_event(
        first["job_id"],
        {"type": "done",
         "results": [{"platform": "wechat", "success": True, "draft": True}]})
    third = dp.enqueue_card_push(row)
    check(third["already"] is False and third["job_id"] != first["job_id"],
          "a finished job no longer dedupes")

    # An edited card is a different payload: no dedupe even while queued.
    row2 = _seed_post(database)
    a = dp.enqueue_card_push(row2)
    edited = dict(row2)
    edited["content"] = row2["content"] + "\n追加段落"
    b = dp.enqueue_card_push(edited)
    check(b["already"] is False and b["job_id"] != a["job_id"],
          "edited copy queues as a new job")

    # Lanes are classes, not strings: immediate stays apart from scheduled,
    # while two pushes of the same class dedupe even when their run_after
    # strings differ in shape — a server retry rewrites the stored value in
    # its own format, and that must not split the lane in two.
    row3 = _seed_post(database)
    scheduled = dp.enqueue_card_push(row3, run_after="2099-01-01T01:30:00+00:00")
    check(scheduled["already"] is False, "a scheduled push queues")
    again = dp.enqueue_card_push(row3, run_after="2099-01-01T01:30:00")
    check(again["already"] is True and again["job_id"] == scheduled["job_id"],
          "same-lane pushes dedupe across run_after string formats")
    immediate = dp.enqueue_card_push(row3)
    check(immediate["already"] is False
          and immediate["job_id"] != scheduled["job_id"],
          "an immediate push does not dedupe against a scheduled one")

    # --- executor stamping: a graduated platform's jobs are marked for the
    # extension, everyone else's stay on the old agent lane ---
    database.set_setting("DOMESTIC_EXECUTORS", json.dumps({"wechat": "extension"}))
    row4 = _seed_post(database)
    stamped = dp.enqueue_card_push(row4)
    payload = json.loads(database.get_agent_job(stamped["job_id"])["payload"])
    check(payload.get("executor") == "extension",
          "a graduated platform's payload is stamped for the extension")

    row5 = database.add_standalone_post("freeform", "gate-seed", "zhihu", "正文")
    row5 = database.get_standalone_post(row5)
    unstamped = dp.enqueue_card_push(row5)
    payload = json.loads(database.get_agent_job(unstamped["job_id"])["payload"])
    check("executor" not in payload,
          "an ungraduated platform's payload stays on the old agent lane")

    database.set_setting("DOMESTIC_EXECUTORS", "{not json")
    row6 = _seed_post(database)
    broken = dp.enqueue_card_push(row6)
    payload = json.loads(database.get_agent_job(broken["job_id"])["payload"])
    check("executor" not in payload,
          "a broken executor map reads as 'nothing graduated', not a 500")

    # Dedupe still holds with stamping on: same setting, same payload string.
    database.set_setting("DOMESTIC_EXECUTORS", json.dumps({"wechat": "extension"}))
    row7 = _seed_post(database)
    a = dp.enqueue_card_push(row7)
    b = dp.enqueue_card_push(row7)
    check(b["already"] is True and b["job_id"] == a["job_id"],
          "stamped pushes dedupe like unstamped ones")
    database.set_setting("DOMESTIC_EXECUTORS", "")

    print("DOMESTIC_PUBLISHER_OK")


if __name__ == "__main__":
    main()
