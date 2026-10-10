import { describe, expect, it, vi } from "vitest";
import { buildHeartbeat, claimBody, publishJob, runCycle } from "./orchestrator";

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
  it("builds the same heartbeat shape as the python agent", () => {
    expect(buildHeartbeat({ wechat: true })).toEqual({
      agent: "insights-ext", busy: null,
      tools: { extension: { available: true, platformIds: ["wechat"],
        platforms: [{ id: "wechat", logged_in: true }] } },
    });
  });
  it("claims as the extension with its platform capabilities", () => {
    expect(claimBody(["wechat"])).toEqual({
      agent: "insights-ext", executor: "extension", platforms: ["wechat"] });
  });
});

describe("runCycle", () => {
  it("heartbeats then claims; an idle queue returns false", async () => {
    const client = makeClient(null);
    const deps = { client, checkLogins: async () => ({ wechat: true }),
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

  it("keeps the heartbeat on its own 30s cadence while claiming every cycle",
    async () => {
      const client = makeClient(null);
      const deps = { client, checkLogins: vi.fn(async () => ({ wechat: true })),
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
    expect(done.status).toBe("failed");
    expect(done.results[0].code).toBe("needs_login");
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
