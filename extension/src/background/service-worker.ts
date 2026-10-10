import { checkWeixinLogin } from "../lib/login";
import { configured, loadSettings, ProtocolClient } from "../lib/protocol";
import { PLATFORM_IDS, runCycle } from "./orchestrator";

const busy = { current: null as number | null };
const beatClock = { lastBeat: 0 };
// Login probes hit the mp homepage: cache for 30s so the 5s claim loop and
// the 30s heartbeat share one fetch at most.
let loginCache = { at: 0, value: {} as Record<string, boolean> };

async function checkLogins(): Promise<Record<string, boolean>> {
  if (Date.now() - loginCache.at < 30000) return loginCache.value;
  const logins: Record<string, boolean> = {};
  for (const id of PLATFORM_IDS) {
    logins[id] = id === "wechat" ? await checkWeixinLogin() : false;
  }
  loginCache = { at: Date.now(), value: logins };
  return logins;
}

// Placeholder until the adapter bridge lands: refuse honestly instead of
// pretending. Replaced by publishViaBridge in the adapter task.
async function publishViaBridgePlaceholder(target: string): Promise<never> {
  throw Object.assign(new Error(`平台 ${target} 的适配器尚未接入`),
    { code: "unknown_target" });
}

async function cycle(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  if (!configured(settings)) return;
  const client = new ProtocolClient(settings);
  await runCycle({ client, checkLogins, busy, beatClock,
                   publishOne: publishViaBridgePlaceholder });
}

chrome.alarms.create("ext-poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {
  void cycle();
});
setInterval(() => {
  void cycle();
}, 5000);
void cycle();
