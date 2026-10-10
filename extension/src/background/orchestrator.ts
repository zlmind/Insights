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

export interface Heartbeat {
  agent: string;
  busy: number | null;
  tools: { extension: { available: boolean; platformIds: string[];
    platforms: { id: string; logged_in: boolean }[] } };
}

export function buildHeartbeat(logins: Record<string, boolean>,
                               busyJobId: number | null): Heartbeat {
  return {
    agent: AGENT_NAME,
    busy: busyJobId,
    tools: {
      extension: {
        available: true,
        platformIds: PLATFORM_IDS,
        platforms: PLATFORM_IDS.map((id) => ({ id, logged_in: !!logins[id] })),
      },
    },
  };
}

// A publish can hold for minutes; hammering a down server every 5s is noise.
// Exponential from 5s, capped at one minute, reset by the caller on success.
export function backoffSeconds(consecutiveFailures: number): number {
  return Math.min(60, 5 * 2 ** Math.max(0, consecutiveFailures - 1));
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
  // The beat runs before the busy check on purpose: a publish can hold for
  // minutes, and the server greys the compose buttons after sixty seconds of
  // silence — the one gap the python agent's beat thread exists to avoid.
  const clock = deps.beatClock || { lastBeat: 0 };
  const every = (deps.beatEverySeconds ?? 30) * 1000;
  if (Date.now() - clock.lastBeat >= every) {
    clock.lastBeat = Date.now();
    let logins: Record<string, boolean> = {};
    try {
      logins = await deps.checkLogins();
    } catch { /* a lost login probe must not kill the beat */ }
    await deps.client.heartbeat(buildHeartbeat(logins, deps.busy.current))
      .catch(() => {});
  }
  if (deps.busy.current !== null) return false;
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
  const retryable = results.some(
    (r) => r.code === "sync_failed" || r.code === "timeout");
  if (results.length && ok === 0 && retryable) {
    // The server requeues only a `failed` event: an all-retryable failure
    // sends one so timeout/sync_failed burn an attempt instead of dying
    // terminally. needs_login-only failures keep the `done` shape below —
    // retrying cannot log the platform in, and `done` preserves the
    // per-platform results the queue page renders.
    await deps.client.sendEvent(job.id, {
      type: "failed", retryable: true,
      error: results.map((r) => r.error || r.code || "失败").join("；"),
      results,
    });
    return;
  }
  const status = !results.length ? "failed"
    : ok === results.length ? "succeeded"
    : ok > 0 ? "partial" : "failed";
  await deps.client.sendEvent(job.id, { type: "done", status, results });
}
