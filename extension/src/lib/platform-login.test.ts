import { describe, expect, it } from "vitest";
import { checkPortalLogin, checkZhihuLogin, portalVerdict, zhihuVerdict } from "./platform-login";

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

describe("probe timeouts", () => {
  // A hanging portal must degrade to unknown on its own clock — the beat
  // runs before the claim, so one stuck fetch would stall the whole
  // extension: no heartbeat, no pushes (review: Important-2).
  const hanging = (url: URL | RequestInfo, init?: RequestInit): Promise<Response> =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort",
        () => reject(new DOMException("aborted", "AbortError")));
    });

  it("checkZhihuLogin resolves unknown when its timeout aborts", async () => {
    await expect(checkZhihuLogin(hanging, 5)).resolves.toBe("unknown");
  });
  it("checkPortalLogin resolves unknown when its timeout aborts", async () => {
    await expect(checkPortalLogin("https://creator.douyin.com/", hanging, 5))
      .resolves.toBe("unknown");
  });
});
