"""Drivers: one class per external publishing tool.

A driver owns three things — is the tool here, which of its platforms are
logged in, and how to push one article — and nothing else. The agent loop
never learns a tool's name. When wechatsync dies, a replacement driver lands
here and nothing above this file changes.

wechatsync is GPL-3.0 and is called as a separate CLI process only: no source
copied, no library linked, so Insights' MIT stays clean.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

REPO_TO_WS = {
    "wechat": "weixin",
    "xiaohongshu": "xiaohongshu",
    "douyin": "douyin",
    "zhihu": "zhihu",
    "toutiao": "toutiao",
}

_TEMP_ROOT = os.path.join(tempfile.gettempdir(), "insights-agent")

_INTERACTIVE_MARKERS = ("扫码", "验证码", "scan the qr", "verification code")
_OFFLINE_MARKERS = ("extension offline", "chrome", "扩展", "桥接")


def _run_cli(args, timeout):
    """List-argv, no shell, captured output; timeouts surface as TimeoutError."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError() from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _classify(code, out, err):
    """One CLI outcome -> a result error code, per the spec's taxonomy."""
    blob = f"{out}\n{err}".lower()
    raw = (err or out or f"exit {code}").strip()[:300]
    if any(m in blob for m in _INTERACTIVE_MARKERS):
        return "interactive_prompt", raw
    if any(m in blob for m in _OFFLINE_MARKERS):
        return "extension_offline", raw
    return "sync_failed", raw


class WechatSyncDriver:
    """Drives @wechatsync/cli: `auth` for login state, `sync` per target."""

    name = "wechatsync"

    def __init__(self, bin_name="wechatsync", timeout=300):
        # npm installs the Windows shim as wechatsync.cmd, and CreateProcess
        # does not consult PATHEXT for an extensionless name — so the bare
        # name raises FileNotFoundError forever. Resolve the real path once.
        self._bin = shutil.which(bin_name) or bin_name
        self._timeout = timeout
        self._run = _run_cli          # swapped by the gate
        self._logged_in_map = self._auth_map   # swapped by the gate
        self._tmp_root = _TEMP_ROOT   # swapped by the gate

    # --- availability (heartbeat) -------------------------------------
    def available(self):
        try:
            code, out, _ = self._run([self._bin, "--version"], 20)
        except (OSError, TimeoutError) as exc:
            return {"available": False, "version": None,
                    "error": f"wechatsync CLI 不可用：{exc}",
                    "hint": "npm install -g @wechatsync/cli"}
        if code != 0:
            return {"available": False, "version": None,
                    "error": f"wechatsync --version 退出码 {code}",
                    "hint": "npm install -g @wechatsync/cli"}
        lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        return {"available": True,
                "version": lines[-1] if lines else None,
                "error": None}

    # --- platforms (heartbeat) -----------------------------------------
    def _auth_map(self):
        """``{ws_id: logged_in}`` from the real `auth` command's text report.

        The CLI prints two sections — 已登录 (N): and 未登录 (N): — whose
        indented lines begin with the platform id; a platform the report
        does not mention reads as logged out. The bridge wait is bounded by
        the CLI's own --timeout, so ours only has to cover the rest.
        """
        try:
            code, out, _ = self._run(
                [self._bin, "--timeout", "35000", "auth"], 45)
        except (OSError, TimeoutError):
            return {}
        if code != 0:
            return {}
        logged_in = set()
        section = None
        for line in (out or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("已登录"):
                section = "in"
            elif stripped.startswith("未登录"):
                section = "out"
            elif section and stripped:
                first = stripped.split()[0] if stripped.split() else ""
                if section == "in" and re.fullmatch(r"[a-z0-9-]+", first):
                    logged_in.add(first)
        return {ws: ws in logged_in for ws in set(REPO_TO_WS.values())}

    def platforms(self):
        auth = self._logged_in_map()
        return [{"id": repo, "logged_in": auth.get(ws, False)}
                for repo, ws in REPO_TO_WS.items()]

    # --- publishing ------------------------------------------------------
    def publish_article(self, payload) -> list[dict]:
        title = (payload.get("title") or "未命名")[:120]
        auth = self._logged_in_map()
        results = []
        for item in payload.get("items") or []:
            target = item.get("target")
            ws_id = REPO_TO_WS.get(target)
            if not ws_id:
                results.append({"platform": target, "success": False,
                                "draft": False, "code": "unknown_target",
                                "error": f"未知平台 {target!r}"})
                continue
            if auth.get(ws_id) is False:
                results.append({"platform": target, "success": False,
                                "draft": False, "code": "needs_login",
                                "error": "该平台在本机浏览器未登录"})
                continue
            results.append(self._sync_one(ws_id, target, title,
                                          item.get("content") or ""))
        return results

    def _sync_one(self, ws_id, target, title, content) -> dict:
        os.makedirs(self._tmp_root, exist_ok=True)
        path = os.path.join(self._tmp_root, f"push-{os.getpid()}-{ws_id}.md")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        try:
            code, out, err = self._run(
                [self._bin, "sync", path, "-p", ws_id, "-t", title],
                self._timeout)
        except TimeoutError:
            return {"platform": target, "success": False, "draft": False,
                    "code": "timeout", "retryable": True,
                    "error": "wechatsync 超时"}
        except OSError as exc:
            return {"platform": target, "success": False, "draft": False,
                    "code": "tool_missing", "error": f"wechatsync 无法启动：{exc}"}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if code == 0:
            return {"platform": target, "success": True, "draft": True,
                    "detail": "已保存到草稿箱，请到平台确认发布"}
        error_code, message = _classify(code, out, err)
        result = {"platform": target, "success": False, "draft": False,
                  "code": error_code, "error": message}
        if error_code in ("timeout",):
            result["retryable"] = True
        return result


def get_driver(kind, bin_name="wechatsync", timeout=300):
    if kind == "article_sync":
        return WechatSyncDriver(bin_name=bin_name, timeout=timeout)
    raise SystemExit(f"没有能执行 {kind!r} 的驱动")
