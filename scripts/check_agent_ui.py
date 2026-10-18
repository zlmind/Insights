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
    uid = database.get_user_by_username('gate')['id']
    import agent_hub
    database.set_setting("WECHATSYNC_ENABLED_PLATFORMS", "wechat,zhihu,toutiao")

    post_id = database.add_standalone_post("freeform", "gate-seed", "wechat",
                                           "正文", user_id=uid)
    # The login check below needs a row of a platform the heartbeat reports
    # logged out: push buttons render per saved card row (one per platform),
    # so a wechat-only card has no zhihu button to carry the warning.
    database.add_standalone_post("freeform", "gate-seed", "zhihu",
                                 "正文", user_id=uid)

    # --- offline first: the button exists and says why it is disabled ---
    html = client.get("/compose").get_data(as_text=True)
    check('btn-agent-push' in html, "the push button renders")
    check('检测离线：发布助手未连接，去发布队列页恢复' in html,
          "while offline the disabled button names the reason in strip vocabulary")
    check('请到' in html and '确认发布' in html,
          "draft semantics are spelled out: go confirm on the platform")
    check('/agent-jobs' in html, "the queue page is linked")

    # The pair strip rides under the nav on every page while the extension
    # has no heartbeat, and hands the visitor straight to a minted code via
    # the #pair anchor. The queue page itself already carries the offline
    # badge and the button, so it does not repeat the strip. The checks match
    # the div's class attribute, not bare "pair-strip" — the stylesheet
    # carries the selector on every page.
    check('class="pair-strip"' in html and '/agent-jobs#pair' in html,
          "offline: the strip shows under the nav and links to /agent-jobs#pair")
    check('浏览器扩展未连接' not in html and '推送到公众号等平台需要它' in html,
          "the pair strip speaks the fixed vocabulary, no jargon")
    check('class="pair-strip"' not in client.get("/agent-jobs").get_data(as_text=True),
          "the queue page does not repeat the strip")

    # The login strip mounts above the generator and carries the vocabulary
    # the spec fixes: platform names, 暂不支持检测, the title 账号登录.
    check('id="loginStrip"' in html, "the login strip mounts on compose")
    check('/agent/login-states.json' in html,
          "the strip polls the login-states endpoint")
    check('暂不支持检测' in html,
          "the strip knows the unknown state's wording")
    check('账号登录' in html, "the strip's title uses the fixed vocabulary")

    # --- online: the same button is enabled and login dots show up ---
    # Online state is built through the endpoint, never by reaching into
    # record_heartbeat: the endpoint is the stable seam across refactors.
    database.create_agent_token(uid, "ui-token")
    client.post("/agent/heartbeat", headers={"Authorization": "Bearer ui-token"},
                json={"tools": {"wechatsync": {
                    "available": True,
                    "platforms": [{"id": "wechat", "logged_in": True},
                                  {"id": "zhihu", "logged_in": False}]}}})
    html = client.get("/compose").get_data(as_text=True)
    check('推送离线' not in html
          and '检测离线：发布助手未连接，去发布队列页恢复' not in html,
          "a fresh heartbeat clears the button's offline reason")
    check('未登录' in html,
          "a logged-out platform surfaces its login state on the button")
    check('class="pair-strip"' not in html,
          "a live heartbeat clears the pair strip")
    check('本机 agent' not in html,
          "client-facing copy never says agent")

    # A finished job shows per-platform results on the queue page.
    jid = database.enqueue_agent_job(
        "article_sync",
        json.dumps({"title": "T", "items": [{"target": "wechat", "content": "x"}]}),
        user_id=uid)
    database.record_agent_job_event(jid, {
        "type": "done",
        "results": [{"platform": "wechat", "success": True, "draft": True,
                     "detail": "已保存到草稿箱，请到平台确认发布"}]})
    page = client.get("/agent-jobs").get_data(as_text=True)
    check('已保存到草稿箱' in page and '公众号' in page,
          "the queue page renders per-platform results with display names")
    check('pair-btn' in page and '配对浏览器扩展' in page,
          "the queue page carries the pairing entry for a fresh extension")

    # The connection pill: green with a pulse while the助手 answers, red when
    # it does not — and /agent/status.json is what its poll repaints from.
    page = client.get("/agent-jobs").get_data(as_text=True)
    check('conn-pill is-on' in page and '发布助手已连接' in page,
          "a live heartbeat paints the pill green with 已连接")
    status = client.get("/agent/status.json").get_json()
    check(status.get("online") is True and isinstance(status.get("age"), float),
          "status.json reads online with the heartbeat age")

    for stale in list(agent_hub._TOOL_BEATS):
        agent_hub._TOOL_BEATS[stale]["t"] = 0
    agent_hub._LAST_HEARTBEAT["t"] = 0
    page = client.get("/agent-jobs").get_data(as_text=True)
    check('conn-pill is-off' in page and '发布助手未连接' in page,
          "a stale heartbeat paints the pill red with 未连接")
    status = client.get("/agent/status.json").get_json()
    check(status.get("online") is False and status.get("age") is not None,
          "status.json reads offline while the last beat is in the past")

    print("AGENT_UI_OK")


if __name__ == "__main__":
    main()
