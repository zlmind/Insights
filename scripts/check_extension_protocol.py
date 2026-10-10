"""Gate for the compiled extension protocol client over real HTTP.

    python scripts/check_extension_protocol.py    EXTENSION_PROTOCOL_OK

The node driver (extension_protocol_driver.mjs) runs the built
dist/lib/protocol.js against this server: heartbeat accepted, the claim
honours the executor filter over the wire, events recorded. Requires
`npm run build` first — run check_extension_tests.py before this one.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "ext-gate-token"
_state = {"jobs": [], "events": [], "heartbeats": []}


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
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._json(401, {"error": "unauthorized"})
        if self.path == "/agent/heartbeat":
            _state["heartbeats"].append(body)
            return self._json(200, {"ok": True})
        if self.path == "/agent/jobs/claim":
            executor = body.get("executor") or "agent"
            platforms = body.get("platforms")
            for i, job in enumerate(list(_state["jobs"])):
                payload = json.loads(job["payload"])
                if (payload.get("executor") or "agent") != executor:
                    continue
                targets = [it["target"] for it in payload.get("items", [])]
                if platforms and not all(t in platforms for t in targets):
                    continue
                _state["jobs"].pop(i)
                return self._json(200, job)
            self.send_response(204)
            self.end_headers()
            return
        if self.path.startswith("/agent/jobs/") and self.path.endswith("/events"):
            _state["events"].append(body)
            return self._json(200, {})
        return self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def main():
    from _accounts_gate import check

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # The agent-lane job sits in front: a filterless claimer would take it,
    # so the driver returning the extension job proves the filter.
    _state["jobs"].append({
        "id": 1, "kind": "article_sync",
        "payload": json.dumps({"title": "Old lane",
                               "items": [{"target": "zhihu", "content": "x"}]})})
    _state["jobs"].append({
        "id": 2, "kind": "article_sync",
        "payload": json.dumps({"title": "T",
                               "items": [{"target": "wechat", "content": "x"}],
                               "executor": "extension"})})
    driver = os.path.join(HERE, "extension_protocol_driver.mjs")
    proc = subprocess.run(
        ["node", driver, f"http://127.0.0.1:{srv.server_port}", TOKEN],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, shell=(os.name == "nt"), timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    check(proc.returncode == 0 and "DRIVER_OK" in out,
          f"the compiled client completes heartbeat→claim→events ({out.strip()[:300]})")
    check(any(h.get("tools", {}).get("extension") for h in _state["heartbeats"]),
          "the heartbeat arrived in the extension's shape")
    check(len(_state["events"]) == 2
          and _state["events"][0]["type"] == "started"
          and _state["events"][1]["status"] == "succeeded",
          "started and done events were recorded")
    check(_state["jobs"] and _state["jobs"][0]["id"] == 1,
          "the agent-lane job was left for the old agent")
    print("EXTENSION_PROTOCOL_OK")
    srv.shutdown()


if __name__ == "__main__":
    main()
