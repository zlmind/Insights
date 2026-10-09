"""Gate for the agent package: config, client transport, worker loop.

    python scripts/check_agent_agent.py    AGENT_AGENT_OK

The worker is exercised against a real HTTP server on localhost serving the
same three endpoints as agent_hub, so the transport (Bearer header, 204 on
empty claim) is tested, not assumed. The driver is a stub here; the real
wechatsync driver has its own gate.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _accounts_gate import check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class StubDriver:
    name = "stub"

    def __init__(self, results=None, error=None):
        self.calls = []
        self._results = results or []
        self._error = error

    def available(self):
        return {"available": True, "version": "test"}

    def platforms(self):
        return [{"id": "wechat", "logged_in": True}]

    def publish_article(self, payload):
        self.calls.append(payload)
        if self._error:
            raise self._error
        return self._results


def _server(jobs, events, state):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if self.path == "/agent/heartbeat":
                state["heartbeats"].append(body)
                return self._json(200, {"ok": True})
            if self.path == "/agent/jobs/claim":
                if self.headers.get("Authorization") != "Bearer t0":
                    return self._json(401, {"error": "unauthorized"})
                if state["claimed"]:
                    self.send_response(204)
                    self.end_headers()
                    return
                state["claimed"] = True
                return self._json(200, jobs.pop(0))
            if self.path.startswith("/agent/jobs/") and self.path.endswith("/events"):
                events.append(body)
                return self._json(200, {})

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    from agent.config import Config
    from agent.api import AgentClient, AgentError
    from agent import worker

    cfg = Config(server_url="http://x", token="t0", agent_name="gate",
                 poll_seconds=0, sync_timeout=5, wechatsync_bin="wechatsync")
    client = AgentClient(cfg)

    # --- transport: bad token is an AgentError, not a silent pass ---
    srv = _server([], [], {"heartbeats": [], "claimed": False})
    cfg_bad = Config(server_url=f"http://127.0.0.1:{srv.server_port}", token="nope",
                     agent_name="gate", poll_seconds=0, sync_timeout=5,
                     wechatsync_bin="w")
    try:
        AgentClient(cfg_bad).claim()
        check(False, "a 401 from the server must raise AgentError")
    except AgentError:
        check(True, "a 401 from the server raises AgentError")

    # --- run_once: claims, reports started, executes, reports done ---
    job = {"id": 7, "kind": "article_sync",
           "payload": json.dumps({"title": "T",
                                  "items": [{"target": "wechat", "content": "c"}]})}
    events, state = [], {"heartbeats": [], "claimed": False}
    srv = _server([job], events, state)
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"
    driver = StubDriver(
        results=[{"platform": "wechat", "success": True, "draft": True}])
    check(worker.run_once(client, driver, cfg) is True,
          "a claimed job makes run_once busy")
    types = [e["type"] for e in events]
    check(types == ["started", "done"], "started then done are reported")
    check(events[1]["status"] == "succeeded", "all-success lands as succeeded")
    check(driver.calls and driver.calls[0]["items"][0]["target"] == "wechat",
          "the driver got the payload verbatim")
    check(state["heartbeats"] and
          state["heartbeats"][0]["tools"]["stub"]["available"] is True,
          "the heartbeat carries tool availability")

    # --- idle cycle ---
    events.clear()
    check(worker.run_once(client, driver, cfg) is False, "an empty queue idles")

    # --- unknown kind: failed event, no crash ---
    job_bad = {"id": 8, "kind": "video_publish",
               "payload": json.dumps({"items": []})}
    events, state = [], {"heartbeats": [], "claimed": False}
    srv = _server([job_bad], events, state)
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"
    worker.run_once(client, StubDriver(), cfg)
    last = events[-1]
    check(last["type"] == "failed" and last["code"] == "unknown_kind"
          and last.get("retryable") is False,
          "an unknown kind fails the job explicitly")

    # --- driver exception: retryable failed event ---
    job = {"id": 9, "kind": "article_sync",
           "payload": json.dumps({"items": []})}
    events, state = [], {"heartbeats": [], "claimed": False}
    srv = _server([job], events, state)
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"
    worker.run_once(client, StubDriver(error=RuntimeError("boom")), cfg)
    last = events[-1]
    check(last["type"] == "failed" and last.get("retryable") is True
          and "boom" in last.get("error", ""),
          "a driver exception is a retryable failed event")

    print("AGENT_AGENT_OK")


if __name__ == "__main__":
    main()
