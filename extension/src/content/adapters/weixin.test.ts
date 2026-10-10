import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { classifyWeixinError } from "./weixin";

const loggedOut = readFileSync(
  new URL("../../lib/fixtures/weixin-logged-out.html", import.meta.url), "utf-8");

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
