"""The server side of the local publishing agent.

The agent is not a browser session: it is a small process on the owner's
machine that polls in over HTTPS with a per-user bearer token. That makes
three things true here. Auth resolves the token to its user through the
agent_tokens table, not the login wall. The heartbeat is an in-process
timestamp, not a table, because its only readers are the compose page and
these endpoints in the same single app process. And every queue mutation goes
through ``database`` so the gate can exercise the same functions the routes
do.
"""

from __future__ import annotations

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
    """The configured shared token; empty means the agent door is closed.

    Legacy-migration semantics only: init_db reads this value once to seed the
    admin's first per-user agent_tokens row. New tokens come from
    ``database.create_agent_token``; nothing here gates a request anymore.
    """
    if db_path is None:
        import appconfig
        return appconfig.get("AGENT_TOKEN") or ""
    return database.get_setting("AGENT_TOKEN", "", db_path=db_path) or ""


def agent_credentials() -> tuple[int, str] | None:
    """``(user_id, token)`` for the request's bearer token, or None.

    The token is looked up by its sha256 in agent_tokens, so a missing, wrong
    or revoked token all read the same way: None. The raw token travels back
    with the id so the heartbeat endpoint can refresh the row's last_seen
    without parsing the header a second time.
    """
    from flask import request
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):]
    user_id = database.find_agent_user_by_token(token)
    if user_id is None:
        return None
    return (user_id, token)


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


# The strip's "go log in" targets: fixed server-side, one per platform, so
# the view never depends on an executor to tell users where to log in.
LOGIN_STATE_URLS = {
    "wechat": "https://mp.weixin.qq.com/",
    "zhihu": "https://www.zhihu.com/signin",
    "toutiao": "https://mp.toutiao.com/",
    "xiaohongshu": "https://creator.xiaohongshu.com/",
    "douyin": "https://creator.douyin.com/",
}


def login_states_view() -> dict:
    """The strip's payload: per-platform ok/logged_out/unknown/offline.

    Offline is a global state — no live heartbeat means nobody knows
    anything, so every platform flips at once (spec §3.2). With a live
    heartbeat, only probed reports decide ok/logged_out; everything else
    stays unknown.
    """
    online = agent_online()
    states = platform_login_states()
    platforms = {}
    for pid, url in LOGIN_STATE_URLS.items():
        entry = states.get(pid)
        if not online:
            state, at = "offline", None
        elif entry is None or not entry["probed"]:
            state, at = "unknown", None
        else:
            state = "ok" if entry["logged_in"] else "logged_out"
            at = int(entry["age"])
        platforms[pid] = {"state": state, "at": at, "login_url": url}
    snap = heartbeat_snapshot()
    return {"checked_at": int(snap["age"]) if online and snap else None,
            "platforms": platforms}


# ── Pairing: the extension bootstraps its bearer token with a short code ────
# In-memory, like the heartbeat: one pending code at a time, one process.
# The code is bound to the user who minted it: redeeming proves nothing about
# identity except that both sides saw the same screen, so the minter's user
# is who the freshly issued token will belong to. The code is what lets a
# fresh extension obtain a token without anyone copying a 43-character
# secret by hand — so it is short-lived, single-use, and dies after five
# wrong guesses.

_PAIRING_TTL_SECONDS = 300
_PAIRING_MAX_ATTEMPTS = 5
_PAIRING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O/1/I/L to read aloud

_PENDING_PAIRING: dict = {"code": None, "user_id": None,
                          "expires": 0.0, "attempts": 0}


def create_pairing_code(user_id: int) -> str:
    """Mint a fresh pairing code for ``user_id``, replacing any pending one."""
    import secrets
    code = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
    _PENDING_PAIRING["code"] = code
    _PENDING_PAIRING["user_id"] = user_id
    _PENDING_PAIRING["expires"] = time.time() + _PAIRING_TTL_SECONDS
    _PENDING_PAIRING["attempts"] = 0
    return code


def redeem_pairing_code(code) -> int | None:
    """The minter's ``user_id`` for one correct, live code; ``None`` otherwise.

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
    user_id = _PENDING_PAIRING["user_id"]
    _PENDING_PAIRING["user_id"] = None
    return user_id


def issue_agent_token(user_id: int, label: str = "") -> str:
    """A new bearer token for ``user_id``; the plaintext exists only now.

    The token is 32 random bytes in urlsafe encoding, stored as its sha256
    hash through ``database.create_agent_token``. The plaintext comes back
    exactly once, here, to be handed to the client that paired.
    """
    import secrets
    token = secrets.token_urlsafe(32)
    database.create_agent_token(user_id, token, label)
    return token
