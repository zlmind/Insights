import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { classifyWeixinError, readAccount, uploadSummary } from "./weixin";

const loggedOut = readFileSync(
  new URL("../../lib/fixtures/weixin-logged-out.html", import.meta.url), "utf-8");

describe("readAccount", () => {
  it("keeps whole ticket and user_name values, including `=` and `-`", () => {
    const page = `window.wx.commonData = { t: "1888899577",
      ticket: "MzA3NTQ4NzQzNQ==_aB-cd", user_name: "gh_9f2e1a47b8c6",
      nick_name: "%E5%A4%A7%E5%AE%B6%E5%A5%BD" };`;
    expect(readAccount(page)).toEqual({
      nickname: "大家好",
      ticket: "MzA3NTQ4NzQzNQ==_aB-cd",
      userName: "gh_9f2e1a47b8c6",
    });
  });
  it("returns empty strings when the page has no credentials", () => {
    expect(readAccount("<html>logged out</html>")).toEqual({
      nickname: "", ticket: "", userName: "" });
  });
  it("reads credentials from window.wx.commonData, not the first ticket in the page", () => {
    // The mp home page embeds other scripts whose `ticket : "..."` lines are
    // NOT the filetransfer ticket (MultiPost scopes the parse to commonData
    // for exactly this reason — upstream commit 9e91388).
    const page = `<script>var cfg = { ticket: "decoy0123", user_name: "gh_decoy" };</script>
      <script>window.wx.commonData = { t: "1888899577",
        ticket: "ba44ba11real22ticket33value44", user_name: "gh_f955ef5e9782",
        nick_name: "%E5%A4%A7%E5%AE%B6%E5%A5%BD" };</script>`;
    expect(readAccount(page)).toEqual({
      nickname: "大家好",
      ticket: "ba44ba11real22ticket33value44",
      userName: "gh_f955ef5e9782",
    });
  });
});

describe("classifyWeixinError", () => {
  it("maps login-ish base_resp messages to needs_login", () => {
    expect(classifyWeixinError("invalid token", "")).toEqual({
      code: "needs_login", error: "invalid token" });
    expect(classifyWeixinError("登录已过期", "")).toEqual({
      code: "needs_login", error: "登录已过期" });
  });
  it("maps everything else to sync_failed", () => {
    expect(classifyWeixinError("内容包含敏感词", "").code).toBe("sync_failed");
  });
  it("an empty token from the page is needs_login", () => {
    expect(classifyWeixinError("", loggedOut).code).toBe("needs_login");
  });
});

describe("uploadSummary", () => {
  it("counts content images the mp pipeline refused to re-host", () => {
    const up = { fileId: 1, url: "https://mmbiz/q.jpg" };
    expect(uploadSummary(3, [up, null, up])).toEqual({ ok: false, failed: 1 });
  });
  it("is ok when every image re-hosted, and when there were none", () => {
    const up = { fileId: 1, url: "https://mmbiz/q.jpg" };
    expect(uploadSummary(2, [up, up]).ok).toBe(true);
    expect(uploadSummary(0, []).ok).toBe(true);
  });
});
