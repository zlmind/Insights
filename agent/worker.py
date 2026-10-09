"""The claim-execute-report loop. One job at a time, forever."""

from __future__ import annotations

import json
import time


def _tool_report(driver) -> dict:
    status = driver.available()
    return {"available": bool(status.get("available")),
            "version": status.get("version"),
            "error": status.get("error"),
            "platforms": driver.platforms()}


def run_once(client, driver, config) -> bool:
    """One poll cycle. Returns True when a job was claimed and executed."""
    try:
        client.heartbeat({"agent": config.agent_name,
                          "tools": {driver.name: _tool_report(driver)}})
    except Exception:  # noqa: BLE001 - a lost server must not kill the agent
        pass

    job = client.claim()
    if not job:
        return False

    job_id = job["id"]
    client.send_event(job_id, {"type": "started", "agent": config.agent_name,
                               "tool": driver.name})
    try:
        kind = job.get("kind")
        if kind != "article_sync":
            client.send_event(job_id, {
                "type": "failed", "code": "unknown_kind",
                "error": f"这个 agent 只会 article_sync，收到 {kind!r}",
                "retryable": False})
            return True
        payload = json.loads(job.get("payload") or "{}")
        results = driver.publish_article(payload)
        ok = [r for r in results if r.get("success")]
        if results and len(ok) == len(results):
            status = "succeeded"
        elif ok:
            status = "partial"
        else:
            status = "failed"
        client.send_event(job_id, {"type": "done", "status": status,
                                   "results": results})
    except Exception as exc:  # noqa: BLE001 - report, requeue, keep looping
        client.send_event(job_id, {"type": "failed", "error": str(exc),
                                   "retryable": True})
    return True


def main(client, driver, config) -> None:
    print(f"agent {config.agent_name} -> {config.server_url}，Ctrl+C 退出")
    while True:
        try:
            run_once(client, driver, config)
        except Exception:  # noqa: BLE001 - the loop outlives any one cycle
            pass
        time.sleep(config.poll_seconds)
