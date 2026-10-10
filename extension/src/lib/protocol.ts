export interface Settings {
  serverUrl: string;
  token: string;
}

export interface StorageLike {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

export interface Job {
  id: number;
  kind: string;
  payload: string;
}

const STORAGE_KEYS = ["INSIGHTS_SERVER_URL", "INSIGHTS_AGENT_TOKEN"];

export async function loadSettings(storage: StorageLike): Promise<Settings> {
  const raw = await storage.get(STORAGE_KEYS);
  return {
    serverUrl: String(raw["INSIGHTS_SERVER_URL"] || "").trim().replace(/\/+$/, ""),
    token: String(raw["INSIGHTS_AGENT_TOKEN"] || ""),
  };
}

export async function saveSettings(storage: StorageLike, s: Settings): Promise<void> {
  await storage.set({
    INSIGHTS_SERVER_URL: s.serverUrl.trim().replace(/\/+$/, ""),
    INSIGHTS_AGENT_TOKEN: s.token,
  });
}

export function configured(s: Settings): boolean {
  return Boolean(s.serverUrl && s.token);
}

export class ProtocolClient {
  private s: Settings;
  private f: typeof fetch;

  constructor(settings: Settings, fetchImpl: typeof fetch = fetch) {
    this.s = settings;
    this.f = fetchImpl;
  }

  private async post(path: string, payload: object,
                     timeoutMs = 15000): Promise<Response> {
    if (!configured(this.s)) throw new Error("未配置服务器或密钥");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await this.f(this.s.serverUrl + path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + this.s.token,
        },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  async heartbeat(payload: object): Promise<void> {
    const resp = await this.post("/agent/heartbeat", payload);
    if (!resp.ok) throw new Error(`/agent/heartbeat -> HTTP ${resp.status}`);
  }

  async claim(body: object): Promise<Job | null> {
    const resp = await this.post("/agent/jobs/claim", body);
    if (resp.status === 204) return null;
    if (!resp.ok) throw new Error(`/agent/jobs/claim -> HTTP ${resp.status}`);
    return (await resp.json()) as Job;
  }

  async sendEvent(jobId: number, event: object): Promise<void> {
    const resp = await this.post(`/agent/jobs/${jobId}/events`, event);
    if (!resp.ok) {
      throw new Error(`/agent/jobs/${jobId}/events -> HTTP ${resp.status}`);
    }
  }
}
