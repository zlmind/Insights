import { describe, expect, it, vi } from "vitest";
import { ProtocolClient, loadSettings, pairWithCode } from "./protocol";

const settings = { serverUrl: "https://insights.example", token: "t0" };

function fetchOk(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(new Response(JSON.stringify(body),
    { status, headers: { "Content-Type": "application/json" } }));
}

describe("ProtocolClient", () => {
  it("sends the bearer header and JSON body on heartbeat", async () => {
    const f = fetchOk({ ok: true });
    await new ProtocolClient(settings, f as unknown as typeof fetch)
      .heartbeat({ agent: "insights-ext" });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("https://insights.example/agent/heartbeat");
    expect((init as RequestInit).headers as Record<string, string>)
      .toHaveProperty("Authorization", "Bearer t0");
    expect(JSON.parse((init as RequestInit).body as string))
      .toEqual({ agent: "insights-ext" });
  });

  it("maps a 204 claim to null and a 200 claim to the job", async () => {
    const noJob = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await new ProtocolClient(settings, noJob as typeof fetch).claim({}))
      .toBeNull();
    const job = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ id: 7, kind: "article_sync", payload: "{}" }), { status: 200 }));
    expect((await new ProtocolClient(settings, job as typeof fetch).claim({}))!.id)
      .toBe(7);
  });

  it("throws on HTTP errors and on missing configuration", async () => {
    const f = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }));
    await expect(new ProtocolClient(settings, f as typeof fetch).heartbeat({}))
      .rejects.toThrow("HTTP 401");
    await expect(new ProtocolClient({ serverUrl: "", token: "" },
      fetchOk({}) as typeof fetch).heartbeat({})).rejects.toThrow("未配置");
  });

  it("loadSettings trims the server URL and reads chrome storage", async () => {
    const storage = {
      get: async (_keys: string[]) =>
        ({ INSIGHTS_SERVER_URL: " https://s.example/ ", INSIGHTS_AGENT_TOKEN: "t" }),
      set: async (_i: Record<string, unknown>) => {},
    };
    const s = await loadSettings(storage as never);
    expect(s).toEqual({ serverUrl: "https://s.example", token: "t" });
  });
});

describe("pairWithCode", () => {
  it("exchanges a one-time code for the bearer token", async () => {
    const f = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ token: "t-new" }),
      { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(pairWithCode("https://s.example", "AB23CD45",
      f as unknown as typeof fetch)).resolves.toBe("t-new");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("https://s.example/agent/pair");
    expect((init as RequestInit).headers as Record<string, string>)
      .not.toHaveProperty("Authorization");
    expect(JSON.parse((init as RequestInit).body as string))
      .toEqual({ code: "AB23CD45" });
  });

  it("throws on a refused code and on a tokenless body", async () => {
    const f403 = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: "配对码无效或已过期" }), { status: 403 }));
    await expect(pairWithCode("https://s.example", "DEAD", f403 as typeof fetch))
      .rejects.toThrow("HTTP 403");
    const fEmpty = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({}), { status: 200 }));
    await expect(pairWithCode("https://s.example", "AB23CD45",
      fEmpty as typeof fetch)).rejects.toThrow("密钥");
  });
});

describe("ProtocolClient fetch receiver", () => {
  it("tolerates a native fetch that demands window as its receiver", async () => {
    // A bound-method call on Window.fetch throws "Illegal invocation" in a
    // real browser; vi.fn() never noticed, so pin the receiver explicitly.
    const nativeLike = function (this: unknown) {
      if (this !== undefined && this !== globalThis) {
        return Promise.reject(new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation"));
      }
      return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    } as unknown as typeof fetch;
    const client = new ProtocolClient(settings, nativeLike);
    await expect(client.heartbeat({ agent: "x" })).resolves.toBeUndefined();
  });
});
