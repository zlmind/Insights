import { describe, expect, it } from "vitest";
import { portalVerdict, zhihuVerdict } from "./platform-login";

describe("zhihuVerdict", () => {
  it("is yes when /api/v4/me answers 200 with an id", () => {
    expect(zhihuVerdict(200, JSON.stringify({ id: "u1", name: "x" }))).toBe("yes");
  });
  it("is no on 401/403", () => {
    expect(zhihuVerdict(401, "")).toBe("no");
    expect(zhihuVerdict(403, "")).toBe("no");
  });
  it("is unknown on other statuses, bodies without id, or bad json", () => {
    expect(zhihuVerdict(500, "")).toBe("unknown");
    expect(zhihuVerdict(200, "{}")).toBe("unknown");
    expect(zhihuVerdict(200, "not json")).toBe("unknown");
  });
});

describe("portalVerdict", () => {
  it("is no when the portal bounced to a login page", () => {
    expect(portalVerdict("https://mp.toutiao.com/auth/page/login/?x=1", true)).toBe("no");
    expect(portalVerdict("https://creator.douyin.com/?entry=sso", true)).toBe("no");
    expect(portalVerdict("https://passport.xiaohongshu.com/login", true)).toBe("no");
  });
  it("is yes when the portal served a creator page directly", () => {
    expect(portalVerdict("https://creator.xiaohongshu.com/publish/publish", true)).toBe("yes");
  });
  it("is unknown when the fetch did not succeed", () => {
    expect(portalVerdict("https://creator.xiaohongshu.com/", false)).toBe("unknown");
  });
});
