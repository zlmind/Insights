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
    # compare_digest is bytes-only: a header is decoded as latin-1, so any
    # non-ASCII byte in it would raise TypeError and turn the rejection into
    # a 500 unless both sides are compared as utf-8.
    return hmac.compare_digest(header[len("Bearer "):].encode("utf-8"),
                               expected.encode("utf-8"))


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


# ── Pairing: the extension bootstraps its bearer token with a short code ────
# In-memory, like the heartbeat: one pending code at a time, one process.
# The code is what lets a fresh extension obtain AGENT_TOKEN without anyone
# copying a 43-character secret by hand — so it is short-lived, single-use,
# and dies after five wrong guesses.

_PAIRING_TTL_SECONDS = 300
_PAIRING_MAX_ATTEMPTS = 5
_PAIRING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O/1/I/L to read aloud

_PENDING_PAIRING: dict = {"code": None, "expires": 0.0, "attempts": 0}


def create_pairing_code() -> str:
    """Mint a fresh pairing code, replacing any pending one."""
    import secrets
    code = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
    _PENDING_PAIRING["code"] = code
    _PENDING_PAIRING["expires"] = time.time() + _PAIRING_TTL_SECONDS
    _PENDING_PAIRING["attempts"] = 0
    return code


def redeem_pairing_code(code) -> str | None:
    """The bearer token for one correct, live code; ``None`` otherwise.

    Wrong guesses burn the code after ``_PAIRING_MAX_ATTEMPTS`` tries, so
    the unauthenticated bootstrap endpoint cannot be brute-forced within
    the code's lifetime. Matching ignores case and stray separators — the
    code is typed by a human from a screen.
    """
    if not isinstance(code, str):
        return None
    normalized = code.strip().upper().replace(" ", "").replace("-", "")
    pending = _PENDING_PAIRING["code"]
    if pending is None or time.time() > _PENDING_PAIRING["expires"]:
        return None
    if normalized != pending:
        _PENDING_PAIRING["attempts"] += 1
        if _PENDING_PAIRING["attempts"] >= _PAIRING_MAX_ATTEMPTS:
            _PENDING_PAIRING["code"] = None
        return None
    _PENDING_PAIRING["code"] = None
    return agent_token()
