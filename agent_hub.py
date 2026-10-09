"""The server side of the local publishing agent.

The agent is not a browser session: it is a small process on the owner's
machine that polls in over HTTPS with one shared bearer token. That makes
three things true here. Auth is a constant-time token compare, not the login
wall. The heartbeat is an in-process timestamp, not a table, because its only
readers are the compose page and these endpoints in the same single app
process. And every queue mutation goes through ``database`` so the gate can
exercise the same functions the routes do.
"""

from __future__ import annotations

import hmac
import time

import database

_LAST_HEARTBEAT: dict = {"t": None, "payload": None}

HEARTBEAT_WINDOW_SECONDS = 60


def agent_token(db_path=None) -> str:
    """The configured shared token; empty means the agent door is closed."""
    if db_path is None:
        import appconfig
        return appconfig.get("AGENT_TOKEN") or ""
    return database.get_setting("AGENT_TOKEN", "", db_path=db_path) or ""


def check_agent_auth() -> bool:
    """True when the request carries exactly the configured bearer token."""
    from flask import request
    expected = agent_token()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):], expected)


def record_heartbeat(payload: dict) -> None:
    _LAST_HEARTBEAT["t"] = time.time()
    _LAST_HEARTBEAT["payload"] = payload or {}


def heartbeat_snapshot():
    """``{"age": seconds, "tools": ...}`` for the last heartbeat, or None."""
    if _LAST_HEARTBEAT["t"] is None:
        return None
    return {"age": time.time() - _LAST_HEARTBEAT["t"],
            **(_LAST_HEARTBEAT["payload"] or {})}


def agent_online(window: float = HEARTBEAT_WINDOW_SECONDS) -> bool:
    if _LAST_HEARTBEAT["t"] is None:
        return False
    return (time.time() - _LAST_HEARTBEAT["t"]) <= window


def platform_login_states() -> dict:
    """``{platform_id: logged_in}`` from the heartbeat, {} when offline."""
    snap = heartbeat_snapshot() or {}
    tools = snap.get("tools") or {}
    states = {}
    for tool in tools.values():
        for entry in (tool or {}).get("platforms") or []:
            if isinstance(entry, dict) and entry.get("id"):
                states[entry["id"]] = bool(entry.get("logged_in"))
    return states
