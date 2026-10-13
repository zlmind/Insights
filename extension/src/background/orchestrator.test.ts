import { describe, expect, it, vi } from "vitest";
import type { LoginVerdict } from "../lib/platform-login";
import { backoffSeconds, buildHeartbeat, cachedCheckLogins, claimBody, publishJob, runCycle } from "./orchestrator";

const job = (id: number, executor?: string) => ({
  id,
  kind: "article_sync",
  payload: JSON.stringify({ title: "T",
    items: [{ target: "wechat", content: "c" }], ...(executor ? { executor } : {}) }),
});

function makeClient(claimResult: unknown) {
  return {
    heartbeat: vi.fn().mockResolvedValue(undefined),
    claim: vi.fn().mockResolvedValue(claimResult),
    sendEvent: vi.fn().mockResolvedValue(undefined),
  };
}

describe("heartbeat and claim shapes", () => {
  it("carries probed only for platforms the cycle really probed", () => {
    expect(buildHeartbeat({ wechat: "yes", zhihu: "no", douyin: "unknown" }, null)).toEqual({
      agent: "insights-ext", busy: null,
      tools: { extension: { available: true,
        platformIds: ["wechat", "zhihu", "toutiao", "xiaohongshu", "douyin"],
        platforms: [
          { id: "wechat", logged_in: true, probed: true },
          { id: "zhihu", logged_in: false, probed: true },
          { id: "toutiao", logged_in: false, probed: false },
          { id: "xiaohongshu", logged_in: false, probed: false },
          { id: "douyin", logged_in: false, probed: false },
        ] } },
    });
  });
  it("carries the job id while a publish holds the worker", () => {
    expect(buildHeartbeat({ wechat: "yes" }, 7).busy).toBe(7);
  });
  it("claims as the extension with its platform capabilities", () => {
    expect(claimBody(["wechat"])).toEqual({
      agent: "insights-ext", executor: "extension", platforms: ["wechat"] });
  });
  it("backs off exponentially and caps at a minute", () => {
    expect(backoffSeconds(1)).toBe(5);
    expect(backoffSeconds(2)).toBe(10);
    expect(backoffSeconds(3)).toBe(20);
    expect(backoffSeconds(9)).toBe(60);
  });
});

describe("cachedCheckLogins", () => {
  it("a failing probe reads unknown without poisoning the shared cache", async () => {
    const cache = { at: 0, value: {} as Record<string, LoginVerdict> };
    let zhihuCalls = 0;
    const checks = {
      wechat: async () => "yes" as LoginVerdict,
      zhihu: async () => {
        zhihuCalls++;
        throw new Error("down");
      },
    };
    const first = await cachedCheckLogins(checks, cache, 40000); // at=0, ttl long past
    expect(first).toEqual({ wechat: "yes", zhihu: "unknown" });
    const second = await cachedCheckLogins(checks, cache, 50000); // within ttl
    expect(second).toEqual({ wechat: "yes", zhihu: "unknown" });
    expect(zhihuCalls).toBe(1); // served from cache, not re-probed
    const third = await cachedCheckLogins(checks, cache, 80000); // ttl passed
    expect(zhihuCalls).toBe(2);
  });
});

describe("runCycle", () => {
  it("heartbeats then claims; an idle queue returns false", async () => {
    const client = makeClient(null);
    const deps = { client, checkLogins: async () => ({ wechat: "yes" }),
                   busy: { current: null } };
    expect(await runCycle(deps)).toBe(false);
    expect(client.heartbeat.mock.calls[0][0].tools.extension.available).toBe(true);
    expect(client.claim.mock.calls[0][0].executor).toBe("extension");
  });

  it("publishes a claimed job with started and done events", async () => {
    const client = makeClient(job(7));
    const publishOne = vi.fn().mockResolvedValue(
      { platform: "wechat", success: true, draft: true, detail: "已保存草稿" });
    await runCycle({ client, checkLogins: async () => ({}),
                     busy: { current: null }, publishOne });
    const types = client.sendEvent.mock.calls.map((c) => c[1].type);
    expect(types).toEqual(["started", "done"]);
    expect(client.sendEvent.mock.calls[1][1].status).toBe("succeeded");
  });

  it("never runs two jobs at once", async () => {
    const client = makeClient(job(7));
    const deps = { client, checkLogins: async () => ({}),
                   busy: { current: 3 }, publishOne: vi.fn() };
    expect(await runCycle(deps)).toBe(false);
    expect(client.claim).not.toHaveBeenCalled();
  });

  it("keeps beating while a job is running, with its id as busy", async () => {
    const client = makeClient(null);
    const deps = { client, checkLogins: vi.fn(async () => ({ wechat: "yes" })),
                   busy: { current: 42 }, beatClock: { lastBeat: 0 },
                   publishOne: vi.fn() };
    await runCycle(deps);
    expect(client.claim).not.toHaveBeenCalled();
    expect(client.heartbeat).toHaveBeenCalledTimes(1);
    expect(client.heartbeat.mock.calls[0][0].busy).toBe(42);
  });

  it("lets a claim rejection reach the caller so the loop can back off",
    async () => {
      const client = makeClient(null);
      client.claim = vi.fn().mockRejectedValue(new Error("server down"));
      await expect(runCycle({ client, checkLogins: async () => ({}),
        busy: { current: null }, publishOne: vi.fn() })).rejects.toThrow("server down");
    });

  it("keeps the heartbeat on its own 30s cadence while claiming every cycle",
    async () => {
      const client = makeClient(null);
      const deps = { client, checkLogins: vi.fn(async () => ({ wechat: "yes" })),
                     busy: { current: null }, beatClock: { lastBeat: Date.now() } };
      await runCycle(deps);
      await runCycle(deps);
      expect(client.heartbeat).not.toHaveBeenCalled();
      expect(client.claim).toHaveBeenCalledTimes(2);
      expect(deps.checkLogins).not.toHaveBeenCalled();
    });
});

describe("publishJob result normalization", () => {
  const deps = () => ({ client: makeClient(null),
    publishOne: vi.fn().mockResolvedValue(
      { platform: "wechat", success: true, draft: true }) });

  it("partial when some targets fail", async () => {
    const d = deps();
    const twoItems = { id: 8, kind: "article_sync",
      payload: JSON.stringify({ title: "T", items: [
        { target: "wechat", content: "a" }, { target: "wechat", content: "b" }] }) };
    d.publishOne.mockResolvedValueOnce(
      { platform: "wechat", success: false, draft: false,
        code: "needs_login", error: "未登录" });
    await publishJob(d, twoItems);
    const done = d.client.sendEvent.mock.calls[1][1];
    expect(done.status).toBe("partial");
    expect(done.results[0].code).toBe("needs_login");
  });

  it("all targets failing lands failed with the code kept", async () => {
    const d = deps();
    d.publishOne.mockResolvedValue(
      { platform: "wechat", success: false, draft: false,
        code: "needs_login", error: "未登录" });
    await publishJob(d, job(8));
    const done = d.client.sendEvent.mock.calls[1][1];
    expect(done.type).toBe("done");
    expect(done.status).toBe("failed");
    expect(done.results[0].code).toBe("needs_login");
  });

  it("a retryable all-failure reports failed with retryable so the server requeues",
    async () => {
      const d = deps();
      d.publishOne.mockResolvedValue(
        { platform: "wechat", success: false, draft: false,
          code: "timeout", error: "超时" });
      await publishJob(d, job(8));
      const evt = d.client.sendEvent.mock.calls[1][1];
      expect(evt.type).toBe("failed");
      expect(evt.retryable).toBe(true);
      expect(evt.results[0].code).toBe("timeout");
    });

  it("maps an exception to a retryable failed event", async () => {
    const d = deps();
    d.publishOne.mockRejectedValue(new Error("boom"));
    await publishJob(d, job(9));
    const evt = d.client.sendEvent.mock.calls[1][1];
    expect(evt.type).toBe("failed");
    expect(evt.retryable).toBe(true);
  });
});
