"""Gate for the agent_jobs queue: claim atomicity, events, retry, stale reclaim.

    python scripts/check_agent_jobs.py    AGENT_JOBS_OK

The agent hub hands jobs to the local machine over HTTPS, so the queue has to
be exactly-once per claim and self-healing after a dead agent. This gate drives
the real database functions on a throwaway file.
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()

    # --- enqueue + get ---
    jid = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "t", "items": [{"target": "wechat", "content": "hi"}]}),
        created_by=None,
    )
    check(isinstance(jid, int) and jid > 0, "enqueue returns a job id")
    job = database.get_agent_job(jid)
    check(job["status"] == "pending", "new job is pending")
    check(json.loads(job["payload"])["items"][0]["target"] == "wechat",
          "payload round-trips as JSON")

    # --- claim is exclusive ---
    first = database.claim_agent_job(agent="a1")
    check(first and first["id"] == jid and first["status"] == "claimed",
          "first claim takes the pending job")
    second = database.claim_agent_job(agent="a2")
    check(second is None, "second claim finds nothing — claims are exclusive")

    # --- started event ---
    job = database.record_agent_job_event(jid, {"type": "started", "agent": "a1"})
    check(job["status"] == "running", "started event marks the job running")

    # --- done: partial semantics ---
    results = [
        {"platform": "wechat", "success": True, "draft": True},
        {"platform": "zhihu", "success": False, "error": "未登录",
         "code": "needs_login"},
    ]
    job = database.record_agent_job_event(jid, {"type": "done", "results": results})
    check(job["status"] == "partial", "mixed results land as partial")
    check(json.loads(job["result"])[1]["code"] == "needs_login",
          "per-platform results are stored verbatim")

    # --- retryable failure: backoff then terminal ---
    j2 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                    max_attempts=2)
    database.record_agent_job_event(j2, {"type": "started"})
    job = database.record_agent_job_event(
        j2, {"type": "failed", "error": "timeout", "retryable": True})
    check(job["status"] == "pending" and job["attempts"] == 1,
          "retryable failure requeues with attempts=1")
    check(job["run_after"] is not None, "retry carries a run_after gate")
    job = database.record_agent_job_event(
        j2, {"type": "failed", "error": "timeout", "retryable": True})
    check(job["status"] == "failed" and job["attempts"] == 2,
          "attempts exhausted lands as terminal failed")

    # --- non-retryable failure is terminal at once ---
    j3 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}))
    job = database.record_agent_job_event(
        j3, {"type": "failed", "error": "no tool", "retryable": False})
    check(job["status"] == "failed", "non-retryable failure is terminal")

    # --- stale reclaim ---
    j4 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}))
    database.claim_agent_job(agent="dead-agent")
    row = database.get_agent_job(j4)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE agent_jobs SET claimed_at = ? WHERE id = ?",
                     ("2000-01-01T00:00:00", j4))
    revived = database.claim_agent_job(agent="a2", stale_minutes=15)
    check(revived and revived["id"] == j4,
          "a claim older than stale_minutes is handed out again")

    # --- run_after gates claiming ---
    j5 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                    run_after="2099-01-01T00:00:00")
    check(database.claim_agent_job(agent="a1") is None,
          "a future run_after is not claimable yet")

    # --- list ---
    jobs = database.list_agent_jobs(limit=10)
    check(len(jobs) >= 5 and jobs[0]["id"] >= jobs[-1]["id"],
          "list returns newest-first rows")

    print("AGENT_JOBS_OK")


if __name__ == "__main__":
    main()
