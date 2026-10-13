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
# One entry per tool that ever beat: its own last-beat time and the platforms
# it reported then. Executors beat on their own clocks, and a beat replaces
# only its own tool's entry — so the extension's and the agent's reports
# coexist inside the window instead of overwriting each other.
_TOOL_BEATS: dict = {}

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
    beat_time = time.time()
    _LAST_HEARTBEAT["t"] = beat_time
    _LAST_HEARTBEAT["payload"] = payload or {}
    tools = (payload or {}).get("tools") or {}
    for name, tool in tools.items():
        _TOOL_BEATS[name] = {"t": beat_time,
                             "platforms": (tool or {}).get("platforms") or []}


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
    """``{platform: {"logged_in": bool, "probed": bool, "age": float}}``.

    Merged across every tool that beat inside the window — the extension and
    the agent report on their own clocks, so the view outlives any single
    beat. Probed reports beat unprobed ones even when they are older ("we
    really checked" outranks "someone guessed", spec §3.1); within one class
    the fresher beat wins. An entry without ``probed`` reads as probed: the
    field post-dates the python agent's heartbeat, whose auth report is a
    real probe.
    """
    now = time.time()
    merged: dict = {}
    for name, beat in list(_TOOL_BEATS.items()):
        age = now - beat["t"]
        if age > HEARTBEAT_WINDOW_SECONDS:
            # An expired tool's guesses stop counting; drop the entry so a
            # long-lived process cannot accumulate dead tools forever.
            del _TOOL_BEATS[name]
            continue
        for entry in beat["platforms"] or []:
            if not (isinstance(entry, dict) and entry.get("id")):
                continue
            probed = bool(entry.get("probed", True))
            candidate = {"logged_in": bool(entry.get("logged_in")),
                         "probed": probed, "age": age}
            current = merged.get(entry["id"])
            if (current is None
                    or (probed and not current["probed"])
                    or (probed == current["probed"] and age < current["age"])):
                merged[entry["id"]] = candidate
    return merged


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
