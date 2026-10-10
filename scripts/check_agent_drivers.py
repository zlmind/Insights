"""Gate for the wechatsync driver: parsing, preflight, cleanup — no real CLI.

    python scripts/check_agent_drivers.py    AGENT_DRIVERS_OK

subprocess is faked, so this gate asserts what the driver does with what the
CLI might say: exit codes, logged-out preflight, timeouts, interactive
prompts, and that temp files never outlive a publish. It also pins how the
driver finds the CLI at all: npm's Windows shim is wechatsync.cmd, which the
bare name cannot launch.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _accounts_gate import check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeCLI:
    """Stands in for subprocess.run; records argv and file contents."""

    def __init__(self, script):
        self.calls = []
        self.contents = []             # the temp file's text at call time
        self._script = script          # def(args, timeout) -> (code, out, err)

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        for a in args:
            if a.endswith((".md", ".html")):
                with open(a, encoding="utf-8", newline="") as fh:
                    self.contents.append(fh.read())
        return self._script(args)


def _driver_with(cli, tmpdir):
    import agent.drivers as drivers
    d = drivers.WechatSyncDriver(bin_name="wechatsync", timeout=5)
    d._run = cli
    d._tmp_root = tmpdir
    d._logged_in_map = lambda: {"weixin": True, "zhihu": True, "toutiao": True}
    return d


def main():
    import agent.drivers as drivers

    payload = {"title": "标题", "items": [{"target": "wechat",
                                           "content": "# T\n\n正文"}]}

    # --- success ---
    cli = FakeCLI(lambda args: (0, "saved", ""))
    d = _driver_with(cli, tempfile.mkdtemp())
    results = d.publish_article(payload)
    check(results[0]["success"] is True and results[0]["draft"] is True,
          "exit 0 is a draft success")
    check(["wechatsync", "sync"] == cli.calls[0][:2] and
          cli.calls[0][cli.calls[0].index("-p") + 1] == "weixin",
          "the repo id wechat maps to wechatsync's weixin, argv is list-form")
    check(cli.contents == ["# T\n\n正文"],
          "the copy reaches the CLI byte-for-byte through the temp file")
    for call_args in cli.calls:
        for a in call_args:
            if a.endswith((".md", ".html")):
                check(not os.path.exists(a), "temp files are cleaned up")

    # --- not logged in: preflight refuses, no sync attempted ---
    d = _driver_with(FakeCLI(lambda args: (0, "", "")), tempfile.mkdtemp())
    d._logged_in_map = lambda: {"weixin": False}
    results = d.publish_article(payload)
    check(results[0]["success"] is False and
          results[0]["code"] == "needs_login",
          "a logged-out target is refused in preflight")
    check(d._run.calls == [], "no sync runs for a logged-out target")

    # --- failure classification ---
    cases = [
        ((1, "请先登录 wechatsync", ""), "sync_failed"),
        ((3, "", "需要扫码确认"), "interactive_prompt"),
        ((2, "", "chrome extension offline"), "extension_offline"),
    ]
    for (code, out, err), want in cases:
        d = _driver_with(FakeCLI(lambda args, c=code, o=out, e=err: (c, o, e)),
                         tempfile.mkdtemp())
        results = d.publish_article(payload)
        check(results[0]["success"] is False and results[0]["code"] == want,
              f"{(code, out, err)!r} classifies as {want}")

    # --- timeout ---
    def slow(args):
        raise TimeoutError()
    d = _driver_with(FakeCLI(slow), tempfile.mkdtemp())
    results = d.publish_article(payload)
    check(results[0]["code"] == "timeout" and
          results[0].get("retryable") is True, "a timeout is retryable")

    # --- multiple items: one CLI run each, per-target results ---
    multi = {"title": "T", "items": [
        {"target": "wechat", "content": "a"},
        {"target": "zhihu", "content": "b"},
        {"target": "unknown", "content": "c"}]}
    cli = FakeCLI(lambda args: (0, "saved", ""))
    d = _driver_with(cli, tempfile.mkdtemp())
    results = d.publish_article(multi)
    check(len(cli.calls) == 2, "only known targets reach the CLI")
    check([r["platform"] for r in results] == ["wechat", "zhihu", "unknown"],
          "one result per target, in order")
    check(results[2]["code"] == "unknown_target", "an unknown target says so")

    # --- platform listing (heartbeat payload) ---
    # The fixture JSON goes through the real `platforms --auth` parse, so the
    # assumed response shape is pinned here until the e2e gate meets the real
    # CLI. _driver_with's canned login map is put back to the parser, and the
    # expected map covers every REPO_TO_WS platform: one the CLI did not
    # mention reads as logged out.
    d = _driver_with(FakeCLI(lambda args: (0, json.dumps([
        {"id": "weixin", "authorized": True},
        {"id": "zhihu", "authorized": False}]), "")), tempfile.mkdtemp())
    d._logged_in_map = d._auth_map
    states = d.platforms()
    check({"wechat": True, "xiaohongshu": False, "douyin": False,
           "zhihu": False, "toutiao": False} ==
          {s["id"]: s["logged_in"] for s in states},
          "platforms maps auth state onto repo ids")

    # --- binary resolution: npm's Windows shim is wechatsync.cmd ---
    # CreateProcess does not consult PATHEXT for an extensionless name, so the
    # bare "wechatsync" raises FileNotFoundError forever; the driver must ask
    # shutil.which once and launch the resolved path.
    fake_dir = tempfile.mkdtemp()
    bare = os.path.join(fake_dir, "wechatsync")
    with open(bare, "w", encoding="utf-8") as fh:
        fh.write("")
    with open(os.path.join(fake_dir, "wechatsync.cmd"), "w",
              encoding="utf-8") as fh:
        fh.write("")
    if os.name != "nt":
        os.chmod(bare, 0o755)
    saved_path = os.environ.get("PATH")
    os.environ["PATH"] = fake_dir + os.pathsep + (saved_path or "")
    try:
        d = drivers.WechatSyncDriver(bin_name="wechatsync")
        check(os.path.dirname(d._bin) == fake_dir and
              os.path.basename(d._bin).startswith("wechatsync"),
              "the CLI shim resolves to its real path via shutil.which")
    finally:
        if saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved_path

    # --- no CLI installed: the fallback keeps the bare name ---
    saved_path = os.environ.get("PATH")
    os.environ["PATH"] = tempfile.mkdtemp()
    try:
        d = drivers.WechatSyncDriver(bin_name="wechatsync")
        check(d._bin == "wechatsync",
              "with nothing to resolve, the bare name survives")
    finally:
        if saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved_path

    print("AGENT_DRIVERS_OK")


if __name__ == "__main__":
    main()
