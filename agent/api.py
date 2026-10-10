"""The server's /agent/* API, from the agent's side. Standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class AgentError(RuntimeError):
    """The server rejected or could not be reached; the loop keeps running."""


class AgentClient:
    def __init__(self, config):
        self._cfg = config

    def _post(self, path, payload, timeout=15):
        req = urllib.request.Request(
            self._cfg.server_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self._cfg.token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raise AgentError(f"{path} -> HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise AgentError(f"{path} -> {exc.reason}") from exc
        except OSError as exc:
            # Read-phase socket failures (connection reset mid-body, timeout
            # while reading); URLError already subclasses OSError, so this
            # only catches what the handlers above did not.
            raise AgentError(f"{path} -> {exc}") from exc

    def heartbeat(self, payload: dict) -> None:
        self._post("/agent/heartbeat", payload)

    def claim(self):
        status, body = self._post("/agent/jobs/claim",
                                  {"agent": self._cfg.agent_name})
        if status == 200:
            return body
        return None

    def send_event(self, job_id: int, event: dict) -> None:
        self._post(f"/agent/jobs/{job_id}/events", event)
