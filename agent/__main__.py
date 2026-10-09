"""`python -m agent`: load config, pick the driver, run the loop."""

from __future__ import annotations

from agent.api import AgentClient
from agent.config import load_config
from agent.drivers import get_driver
from agent.worker import main as run_loop


if __name__ == "__main__":
    cfg = load_config()
    run_loop(AgentClient(cfg), get_driver("article_sync"), cfg)
