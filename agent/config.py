"""Agent configuration from the environment; required keys fail loudly."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass
class Config:
    server_url: str
    token: str
    agent_name: str
    poll_seconds: float
    sync_timeout: int
    wechatsync_bin: str


def load_config() -> Config:
    server_url = (os.getenv("INSIGHTS_SERVER_URL") or "").rstrip("/")
    token = os.getenv("INSIGHTS_AGENT_TOKEN") or ""
    missing = [name for name, value in
               (("INSIGHTS_SERVER_URL", server_url), ("INSIGHTS_AGENT_TOKEN", token))
               if not value]
    if missing:
        raise SystemExit(
            "缺少环境变量：" + "、".join(missing)
            + "。示例：INSIGHTS_SERVER_URL=https://your-server INSIGHTS_AGENT_TOKEN=... python -m agent")
    return Config(
        server_url=server_url,
        token=token,
        agent_name=os.getenv("AGENT_NAME") or socket.gethostname(),
        poll_seconds=float(os.getenv("AGENT_POLL_SECONDS") or 5),
        sync_timeout=int(os.getenv("AGENT_SYNC_TIMEOUT") or 300),
        wechatsync_bin=os.getenv("WECHATSYNC_BIN") or "wechatsync",
    )
