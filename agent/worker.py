"""The claim-execute-report loop. One job at a time, forever."""

from __future__ import annotations

import json
import sys
import threading
import time

# Shared between the loop and the heartbeat thread: the last tool report the
# loop evaluated and the job being worked on right now (None while idle).
_STATE = {"report": {}, "job_id": None, "thread": None}


def _tool_report(driver) -> dict:
    status = driver.available()
    return {"available": bool(status.get("available")),
            "version": status.get("version"),
            "error": status.get("error"),
            "platforms": driver.platforms()}


def _beat(client, driver, config, state) -> None:
    """One heartbeat: the last tool report plus what the agent is doing.

    The only heartbeat body builder in the agent — the loop and the timer
    thread both send through here, so the server never sees two shapes.
    """
    try:
        client.heartbeat({"agent": config.agent_name,
                          "tools": {driver.name: state["report"]},
                          "busy": state["job_id"]})
    except Exception:  # noqa: BLE001 - a lost server must not kill the agent
        pass


def start_heartbeat(client, driver, config, state=None):
    """Keep a heartbeat alive from a daemon thread; returns the thread.

    A wechatsync sync can hold the loop for five minutes, and the server
    greys the compose buttons after sixty seconds of silence — so the beat
    cannot wait for the loop to come back. The thread re-sends the last tool
    report every ``max(poll_seconds, 15)`` seconds with the job id it is
    working on (``busy``); the loop refreshes that report as it evaluates the
    driver. The first beat is synchronous, so starting the agent announces it
    right away, and the thread is daemon because it has no state of its own
    to flush: it dies with the process.
    """
    state = _STATE if state is None else state
    if state.get("thread") and state["thread"].is_alive():
        return state["thread"]
    state["report"] = _tool_report(driver)
    _beat(client, driver, config, state)

    def _loop():
        interval = max(config.poll_seconds, 15)
        while True:
            time.sleep(interval)
            _beat(client, driver, config, state)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    state["thread"] = thread
    return thread


def run_once(client, driver, config, state=None) -> bool:
    """One poll cycle. Returns True when a job was claimed and executed."""
    state = _STATE if state is None else state
    start_heartbeat(client, driver, config, state)
    state["report"] = _tool_report(driver)
    job = client.claim()
    if not job:
        return False

    job_id = job["id"]
    state["job_id"] = job_id
    try:
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
    finally:
        # Idle again, and the tool report is due for a fresh look: logins
        # changed while the job ran.
        state["job_id"] = None
        state["report"] = _tool_report(driver)
    return True


def main(client, driver, config) -> None:
    print(f"agent {config.agent_name} -> {config.server_url}，Ctrl+C 退出")
    start_heartbeat(client, driver, config)
    last_error = None
    while True:
        try:
            run_once(client, driver, config)
        except Exception as exc:  # noqa: BLE001 - the loop outlives any one cycle
            # A stale proxy, a wrong token and a down server all look the same
            # from outside; say so, but only when the failure is new — a 5s
            # poll must not repeat one line forever.
            key = repr(exc)
            if key != last_error:
                print(f"[agent] {time.strftime('%H:%M:%S')} cycle failed: {key}",
                      file=sys.stderr)
                last_error = key
        else:
            if last_error is not None:
                print(f"[agent] {time.strftime('%H:%M:%S')} cycle recovered",
                      file=sys.stderr)
                last_error = None
        time.sleep(config.poll_seconds)
