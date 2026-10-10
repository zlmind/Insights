"""Gate for the push button UI, result chips and the agent-jobs page.

    python scripts/check_agent_ui.py    AGENT_UI_OK

The button must exist only for domestic article platforms, must say why when
it is disabled (agent offline / platform logged out), and the card must show
per-platform draft results instead of a bare yes/no.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _accounts_gate import isolated_app, check  # noqa: E402


def main():
    directory, database, web, publisher, client = isolated_app()
    import agent_hub
    database.set_setting("WECHATSYNC_ENABLED_PLATFORMS", "wechat,zhihu,toutiao")

    post_id = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                           "正文")
    # The login check below needs a row of a platform the heartbeat reports
    # logged out: push buttons render per saved card row (one per platform),
    # so a wechat-only card has no zhihu button to carry the warning.
    database.add_standalone_post("freeform", "gate-seed", "zhihu",
                                 "正文")

    # --- offline first: the button exists and says why it is disabled ---
    html = client.get("/compose").get_data(as_text=True)
    check('btn-agent-push' in html, "the push button renders")
    check('推送离线' in html,
          "while offline the disabled button names the reason")
    check('请到' in html and '确认发布' in html,
          "draft semantics are spelled out: go confirm on the platform")
    check('/agent-jobs' in html, "the queue page is linked")

    # --- online: the same button is enabled and login dots show up ---
    agent_hub.record_heartbeat({"tools": {"wechatsync": {
        "available": True,
        "platforms": [{"id": "wechat", "logged_in": True},
                      {"id": "zhihu", "logged_in": False}]}}})
    html = client.get("/compose").get_data(as_text=True)
    check('推送离线' not in html,
          "a fresh heartbeat clears the offline reason")
    check('未登录' in html,
          "a logged-out platform surfaces its login state on the button")

    # A finished job shows per-platform results on the queue page.
    jid = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "T", "items": [{"target": "wechat", "content": "x"}]}))
    database.record_agent_job_event(jid, {
        "type": "done",
        "results": [{"platform": "wechat", "success": True, "draft": True,
                     "detail": "已保存到草稿箱，请到平台确认发布"}]})
    page = client.get("/agent-jobs").get_data(as_text=True)
    check('已保存到草稿箱' in page and '公众号' in page,
          "the queue page renders per-platform results with display names")
    check('pair-btn' in page and '配对浏览器扩展' in page,
          "the queue page carries the pairing entry for a fresh extension")

    print("AGENT_UI_OK")


if __name__ == "__main__":
    main()
