import { describe, expect, it, vi } from "vitest";
import { ProtocolClient, loadSettings } from "./protocol";

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
    expect((init as RequestInit).headers!["Authorization"]).toBe("Bearer t0");
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
