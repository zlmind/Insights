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

    print("DOMESTIC_PUBLISHER_OK")


if __name__ == "__main__":
    main()
