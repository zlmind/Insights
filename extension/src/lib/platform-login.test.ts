import { describe, expect, it } from "vitest";
import { checkPortalLogin, checkXiaohongshuLogin, checkZhihuLogin, douyinVerdict,
         portalVerdict, rednoteVerdict, zhihuVerdict } from "./platform-login";

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

describe("douyinVerdict", () => {
  // MultiPost account/douyin.ts: creator.douyin.com/web/api/media/user/info/
  // answers 200 with a user object only for a live session.
  it("is yes when the user info carries a user", () => {
    expect(douyinVerdict(200, JSON.stringify({ user: { sec_uid: "x", nickname: "n" } }))).toBe("yes");
  });
  it("is no on a 200 without a user", () => {
    expect(douyinVerdict(200, "{}")).toBe("no");
  });
  it("is no on 401/403 and unknown otherwise", () => {
    expect(douyinVerdict(401, "")).toBe("no");
    expect(douyinVerdict(403, "")).toBe("no");
    expect(douyinVerdict(500, "")).toBe("unknown");
    expect(douyinVerdict(200, "not json")).toBe("unknown");
  });
});

describe("rednoteVerdict", () => {
  // MultiPost account/rednote.ts: www.xiaohongshu.com/explore embeds
  // window.__INITIAL_STATE__ whose user.loggedIn flag is the session truth.
  const page = (flag: string) =>
    `<html><script>window.__INITIAL_STATE__={"user":{"loggedIn":${flag},` +
    `"userInfo":{"user_id":"1","nickname":"n"}}};</script></html>`;

  it("is yes when the explore page's state says logged in", () => {
    expect(rednoteVerdict(page("true"))).toBe("yes");
  });
  it("is no when it says logged out", () => {
    expect(rednoteVerdict(page("false"))).toBe("no");
  });
  it("is unknown without a state blob or a loggedIn flag", () => {
    expect(rednoteVerdict("<html>login page</html>")).toBe("unknown");
    expect(rednoteVerdict('<script>window.__INITIAL_STATE__={"other":1};</script>')).toBe("unknown");
  });
});

describe("checkXiaohongshuLogin", () => {
  // The 去登录 link points at creator.xiaohongshu.com, and a login there may
  // not share cookies with www — while the reverse holds too. The probe asks
  // both surfaces and counts a yes from either (the strip's login_url and
  // MultiPost's own flow both live on the creator portal).
  const fake = (responses: Record<string, { ok: boolean; url: string; body: string }>) =>
    ((url: string | URL) => {
      const r = responses[String(url)];
      if (!r) return Promise.reject(new Error("no reply"));
      return Promise.resolve({ ok: r.ok, url: r.url, text: async () => r.body } as Response);
    }) as unknown as typeof fetch;

  const explore = (flag: "true" | "false") =>
    `https://www.xiaohongshu.com/explore`;

  it("counts a creator-portal session even when www says logged out", async () => {
    const f = fake({
      "https://www.xiaohongshu.com/explore": {
        ok: true, url: "https://www.xiaohongshu.com/explore",
        body: '<script>window.__INITIAL_STATE__={"user":{"loggedIn":false}};</script>' },
      "https://creator.xiaohongshu.com/": {
        ok: true, url: "https://creator.xiaohongshu.com/",
        body: "<html>creator ui</html>" },
    });
    await expect(checkXiaohongshuLogin(f)).resolves.toBe("yes");
  });

  it("counts a www session when the creator portal bounces", async () => {
    const f = fake({
      "https://www.xiaohongshu.com/explore": {
        ok: true, url: "https://www.xiaohongshu.com/explore",
        body: '<script>window.__INITIAL_STATE__={"user":{"loggedIn":true}};</script>' },
      "https://creator.xiaohongshu.com/": {
        ok: true, url: "https://passport.xiaohongshu.com/?service=creator",
        body: "login" },
    });
    await expect(checkXiaohongshuLogin(f)).resolves.toBe("yes");
  });

  it("is no when both surfaces say logged out, unknown when neither answers", async () => {
    const noBoth = fake({
      "https://www.xiaohongshu.com/explore": {
        ok: true, url: "https://www.xiaohongshu.com/explore",
        body: '<script>window.__INITIAL_STATE__={"user":{"loggedIn":false}};</script>' },
      "https://creator.xiaohongshu.com/": {
        ok: true, url: "https://passport.xiaohongshu.com/login", body: "" },
    });
    await expect(checkXiaohongshuLogin(noBoth)).resolves.toBe("no");

    const dead = fake({});
    await expect(checkXiaohongshuLogin(dead)).resolves.toBe("unknown");
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
