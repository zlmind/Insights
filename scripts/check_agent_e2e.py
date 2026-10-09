"""Optional real-CLI gate: push a scratch article to a logged-in platform.

    INSIGHTS_AGENT_E2E=1 python scripts/check_agent_e2e.py    AGENT_E2E_OK

Skipped (AGENT_E2E_SKIP) unless INSIGHTS_AGENT_E2E=1 and wechatsync responds.
Proves the spike's schema assumptions against the real CLI.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main():
    if os.getenv("INSIGHTS_AGENT_E2E") != "1":
        print("AGENT_E2E_SKIP")
        return 0
    from agent.drivers import WechatSyncDriver

    d = WechatSyncDriver()
    status = d.available()
    if not status["available"]:
        print(f"AGENT_E2E_SKIP  # {status['error']}")
        return 0
    results = d.publish_article({
        "title": "Insights agent e2e",
        "items": [{"target": os.getenv("INSIGHTS_AGENT_E2E_TARGET", "zhihu"),
                   "content": "# Insights agent e2e\n\n这是一条真机门测试草稿，可删除。"}],
    })
    print(json.dumps(results, ensure_ascii=False, indent=2))
    assert any(r.get("success") for r in results), "e2e push did not succeed"
    print("AGENT_E2E_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
