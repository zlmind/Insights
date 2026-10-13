import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { checkWeixinVerdict, parseWeixinToken, weixinVerdict } from "./login";

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

describe("weixinVerdict", () => {
  it("reads a token as logged in", () => {
    expect(weixinVerdict("123456")).toBe("yes");
  });
  it("reads an empty token as logged out — the page answered, so this is a real probe", () => {
    expect(weixinVerdict("")).toBe("no");
  });
});

describe("checkWeixinVerdict", () => {
  it("is yes when the fetched homepage carries a token", async () => {
    const f = (async () => new Response(loggedIn)) as unknown as typeof fetch;
    await expect(checkWeixinVerdict(f)).resolves.toBe("yes");
  });
  it("is no when the homepage loads without a token", async () => {
    const f = (async () => new Response(loggedOut)) as unknown as typeof fetch;
    await expect(checkWeixinVerdict(f)).resolves.toBe("no");
  });
  it("is unknown on network failure rather than lying", async () => {
    const f = (async () => {
      throw new Error("down");
    }) as unknown as typeof fetch;
    await expect(checkWeixinVerdict(f)).resolves.toBe("unknown");
  });
});
