import type { Job } from "../lib/protocol";

export const AGENT_NAME = "insights-ext";
export const PLATFORM_IDS = ["wechat"];

export interface PlatformResult {
  platform: string;
  success: boolean;
  draft: boolean;
  detail?: string;
  error?: string;
  code?: string;
}

export function buildHeartbeat(logins: Record<string, boolean>): object {
  return {
    agent: AGENT_NAME,
    busy: null,
    tools: {
      extension: {
        available: true,
        platformIds: PLATFORM_IDS,
        platforms: PLATFORM_IDS.map((id) => ({ id, logged_in: !!logins[id] })),
      },
    },
  };
}

export function claimBody(platforms: string[]): object {
  return { agent: AGENT_NAME, executor: "extension", platforms };
}

interface BaseClient {
  heartbeat(p: object): Promise<void>;
  claim(b: object): Promise<Job | null>;
  sendEvent(jobId: number, event: object): Promise<void>;
}

export interface CycleDeps {
  client: BaseClient;
  checkLogins(): Promise<Record<string, boolean>>;
  busy: { current: number | null };
  beatClock?: { lastBeat: number };
  beatEverySeconds?: number;
  publishOne?(target: string, item: { target: string; content: string },
              title: string): Promise<PlatformResult>;
}

export async function runCycle(deps: CycleDeps): Promise<boolean> {
  if (deps.busy.current !== null) return false;
  // The claim loop runs every 5s, but the heartbeat (and the login probes it
  // carries) stay on their own 30s cadence: the mp homepage must not be
  // fetched five times more often than the spec's heartbeat window needs.
  const clock = deps.beatClock || { lastBeat: 0 };
  const every = (deps.beatEverySeconds ?? 30) * 1000;
  if (Date.now() - clock.lastBeat >= every) {
    clock.lastBeat = Date.now();
    let logins: Record<string, boolean> = {};
    try {
      logins = await deps.checkLogins();
    } catch { /* a lost login probe must not kill the beat */ }
    await deps.client.heartbeat(buildHeartbeat(logins)).catch(() => {});
  }
  const job = await deps.client.claim(claimBody(PLATFORM_IDS));
  if (!job) return false;
  deps.busy.current = job.id;
  try {
    await publishJob({ client: deps.client, publishOne: deps.publishOne! }, job);
  } finally {
    deps.busy.current = null;
  }
  return true;
}

export interface PublishDeps {
  client: BaseClient;
  publishOne(target: string, item: { target: string; content: string },
             title: string): Promise<PlatformResult>;
}

function unknownTarget(target: string): PlatformResult {
  return { platform: target, success: false, draft: false,
           code: "unknown_target", error: `未知平台 ${target}` };
}

export async function publishJob(deps: PublishDeps, job: Job): Promise<void> {
  await deps.client.sendEvent(job.id,
    { type: "started", agent: AGENT_NAME, tool: "extension" });
  let payload: { title?: string; items?: { target: string; content: string }[] };
  try {
    payload = JSON.parse(job.payload || "{}");
  } catch {
    payload = {};
  }
  const title = payload.title || "未命名";
  const results: PlatformResult[] = [];
  try {
    for (const item of payload.items || []) {
      const known = PLATFORM_IDS.includes(item.target);
      results.push(known
        ? await deps.publishOne(item.target, item, title)
        : unknownTarget(item.target));
    }
  } catch (exc) {
    await deps.client.sendEvent(job.id,
      { type: "failed", error: String(exc), retryable: true });
    return;
  }
  const ok = results.filter((r) => r.success).length;
  const status = results.length && ok === results.length ? "succeeded"
    : ok > 0 ? "partial" : "failed";
  await deps.client.sendEvent(job.id, { type: "done", status, results });
}
