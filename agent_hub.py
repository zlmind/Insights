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

# One bucket per user: the user's last heartbeat and one entry per tool that
# ever beat for them — its own last-beat time and the platforms it reported
# then. Executors beat on their own clocks, and a beat replaces only its own
# tool's entry, so the extension's and the agent's reports coexist inside the
# window instead of overwriting each other. The buckets are keyed by user id
# because a heartbeat is one user's machine reporting that user's browser: A's
# login states must never light B's strip.
_LAST_HEARTBEATS: dict[int, dict] = {}
_USER_TOOL_BEATS: dict[int, dict[str, dict]] = {}

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


def record_heartbeat(user_id: int, payload: dict) -> None:
    beat_time = time.time()
    _LAST_HEARTBEATS[user_id] = {"t": beat_time, "payload": payload or {}}
    beats = _USER_TOOL_BEATS.setdefault(user_id, {})
    tools = (payload or {}).get("tools") or {}
    for name, tool in tools.items():
        beats[name] = {"t": beat_time,
                       "platforms": (tool or {}).get("platforms") or []}


def heartbeat_snapshot(user_id: int) -> dict | None:
    """``{"age": seconds, "tools": ...}`` for the user's last heartbeat, or
    None when that user never beat (or has no identity to beat with)."""
    if user_id is None:
        return None
    beat = _LAST_HEARTBEATS.get(user_id)
    if beat is None or beat["t"] is None:
        return None
    return {"age": time.time() - beat["t"], **(beat["payload"] or {})}


def agent_online(user_id: int | None = None,
                 window: float = HEARTBEAT_WINDOW_SECONDS) -> bool:
    if user_id is None:
        return False
    beat = _LAST_HEARTBEATS.get(user_id)
    if beat is None or beat["t"] is None:
        return False
    return (time.time() - beat["t"]) <= window


def platform_login_states(user_id: int | None = None) -> dict:
    """``{platform: {"logged_in": bool, "probed": bool, "age": float}}``.

    Merged across every tool that beat inside the window — the extension and
    the agent report on their own clocks, so the view outlives any single
    beat. Probed reports beat unprobed ones even when they are older ("we
    really checked" outranks "someone guessed", spec §3.1); within one class
    the fresher beat wins. An entry without ``probed`` reads as probed: the
    field post-dates the python agent's heartbeat, whose auth report is a
    real probe.

    Everything happens inside ``user_id``'s own bucket: another user's beats
    are a different browser and never enter the merge. A user without
    identity reads as the empty view. When the bucket's every beat has
    expired it is deleted along with its user key, so a long-lived process
    accumulates no dead tools and leaks no stale user's platforms.
    """
    if user_id is None:
        return {}
    beats = _USER_TOOL_BEATS.get(user_id)
    if not beats:
        return {}
    now = time.time()
    merged: dict = {}
    for name, beat in list(beats.items()):
        age = now - beat["t"]
        if age > HEARTBEAT_WINDOW_SECONDS:
            # An expired tool's guesses stop counting; drop the entry so a
            # long-lived process cannot accumulate dead tools forever.
            del beats[name]
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
    if not beats:
        # The whole bucket expired: drop the user's key outright so the
        # process does not carry one dead entry per user forever.
        del _USER_TOOL_BEATS[user_id]
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


def login_states_view(user_id: int | None = None) -> dict:
    """The strip's payload for one user: per-platform ok/logged_out/unknown/
    offline.

    Offline is that user's whole state — no live heartbeat of theirs means
    nobody knows anything about their browser, so every platform flips at
    once (spec §3.2). With a live heartbeat, only probed reports decide
    ok/logged_out; everything else stays unknown. Without an identity there
    is no heartbeat to read, so the view is all offline.
    """
    online = agent_online(user_id)
    states = platform_login_states(user_id)
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
    snap = heartbeat_snapshot(user_id)
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
