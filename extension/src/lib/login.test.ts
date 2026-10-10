import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { checkWeixinLogin, parseWeixinToken } from "./login";

const dir = new URL("./fixtures/", import.meta.url);
const loggedIn = readFileSync(new URL("weixin-logged-in.html", dir), "utf-8");
const loggedOut = readFileSync(new URL("weixin-logged-out.html", dir), "utf-8");

describe("parseWeixinToken", () => {
  it("finds the token in a logged-in page", () => {
    expect(parseWeixinToken(loggedIn)).toBe("123456");
  });
  it("returns empty on a logged-out page", () => {
    expect(parseWeixinToken(loggedOut)).toBe("");
  });
});

describe("checkWeixinLogin", () => {
  it("is true when the fetched homepage carries a token", async () => {
    const f = (async () => new Response(loggedIn)) as unknown as typeof fetch;
    await expect(checkWeixinLogin(f)).resolves.toBe(true);
  });
  it("is false on network failure rather than throwing", async () => {
    const f = (async () => {
      throw new Error("down");
    }) as unknown as typeof fetch;
    await expect(checkWeixinLogin(f)).resolves.toBe(false);
  });
});
