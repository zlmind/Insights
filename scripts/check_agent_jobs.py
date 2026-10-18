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
    gate_uid = database.get_user_by_username("gate")["id"]

    # --- enqueue + get ---
    jid = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "t", "items": [{"target": "wechat", "content": "hi"}]}),
        created_by=None, user_id=gate_uid,
    )
    check(isinstance(jid, int) and jid > 0, "enqueue returns a job id")
    job = database.get_agent_job(jid)
    check(job["status"] == "pending", "new job is pending")
    check(json.loads(job["payload"])["items"][0]["target"] == "wechat",
          "payload round-trips as JSON")

    # --- claim is exclusive ---
    first = database.claim_agent_job(user_id=gate_uid, agent="a1")
    check(first and first["id"] == jid and first["status"] == "claimed",
          "first claim takes the pending job")
    second = database.claim_agent_job(user_id=gate_uid, agent="a2")
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
                                    max_attempts=2, user_id=gate_uid)
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
    j3 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                   user_id=gate_uid)
    job = database.record_agent_job_event(
        j3, {"type": "failed", "error": "no tool", "retryable": False})
    check(job["status"] == "failed", "non-retryable failure is terminal")

    # --- stale reclaim ---
    j4 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                   user_id=gate_uid)
    database.claim_agent_job(user_id=gate_uid, agent="dead-agent")
    row = database.get_agent_job(j4)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE agent_jobs SET claimed_at = ? WHERE id = ?",
                     ("2000-01-01T00:00:00", j4))
    revived = database.claim_agent_job(user_id=gate_uid, agent="a2",
                                        stale_minutes=15)
    check(revived and revived["id"] == j4,
          "a claim older than stale_minutes is handed out again")

    # --- a running job whose agent died is reclaimed too ---
    # claimed_at survives the started event, so the same staleness rule that
    # recovers a killed claim recovers a killed run; otherwise the row would
    # sit running forever and its dedupe lane would block every re-push.
    j6 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                   user_id=gate_uid)
    check(database.claim_agent_job(user_id=gate_uid,
                                    agent="a1")["id"] == j6,
          "the fresh job is the one claimed")
    database.record_agent_job_event(j6, {"type": "started"})
    check(database.get_agent_job(j6)["status"] == "running",
          "started moves the job to running")
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE agent_jobs SET claimed_at = ? WHERE id = ?",
                     ("2000-01-01T00:00:00", j6))
    revived = database.claim_agent_job(user_id=gate_uid, agent="a4",
                                        stale_minutes=15)
    check(revived and revived["id"] == j6 and revived["status"] == "claimed",
          "a running job past its claim deadline is handed out again")

    # --- a terminal job ignores late events ---
    # After a reclaim the old and the new agent may both report; a finished
    # row must not move again, and the reporter is not told off for it.
    before = database.get_agent_job(jid)          # partial, from the done above
    after = database.record_agent_job_event(jid, {"type": "started"})
    check(dict(after) == dict(before),
          "a late started on a terminal job changes nothing")
    before = database.get_agent_job(j3)           # failed, terminal
    after = database.record_agent_job_event(
        j3, {"type": "failed", "error": "late", "retryable": True})
    check(dict(after) == dict(before),
          "a late failed on a terminal job changes nothing")

    # --- run_after gates claiming ---
    j5 = database.enqueue_agent_job("article_sync", json.dumps({"items": []}),
                                    run_after="2099-01-01T00:00:00",
                                    user_id=gate_uid)
    check(database.claim_agent_job(user_id=gate_uid,
                                    agent="a1") is None,
          "a future run_after is not claimable yet")

    # --- the events door refuses a body that is not an object ---
    # The bearer door takes a per-user token from agent_tokens, not the setting.
    database.create_agent_token(gate_uid, "gate-token",
                                db_path=database.DB_PATH)
    r = client.post(f"/agent/jobs/{j6}/events", json=["not", "an", "object"],
                    headers={"Authorization": "Bearer gate-token"})
    check(r.status_code == 400, "a non-object event body is a 400, not a 500")

    # --- list ---
    jobs = database.list_agent_jobs(limit=10)
    check(len(jobs) >= 5 and jobs[0]["id"] >= jobs[-1]["id"],
          "list returns newest-first rows")

    print("AGENT_JOBS_OK")


if __name__ == "__main__":
    main()
