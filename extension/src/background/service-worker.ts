import { checkWeixinLogin } from "../lib/login";
import { configured, loadSettings, ProtocolClient } from "../lib/protocol";
import { PLATFORM_IDS, PlatformResult, runCycle } from "./orchestrator";

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
void publishViaBridgePlaceholder;

const WEIXIN_HOME = "https://mp.weixin.qq.com/";

async function publishViaBridge(target: string,
                                item: { target: string; content: string },
                                title: string): Promise<PlatformResult> {
  if (target !== "wechat") {
    return { platform: target, success: false, draft: false,
             code: "unknown_target", error: `未知平台 ${target}` };
  }
  const tab = await chrome.tabs.create({ url: WEIXIN_HOME, active: false });
  try {
    await waitTabComplete(tab.id!);
    const resp = await chrome.tabs.sendMessage(tab.id!,
      { type: "publish", payload: { title, content: item.content } });
    return resp?.ok
      ? { platform: "wechat", success: true, draft: true, detail: resp.detail }
      : { platform: "wechat", success: false, draft: false,
          code: resp?.code || "sync_failed", error: resp?.error || "适配器无响应" };
  } catch (exc) {
    return { platform: "wechat", success: false, draft: false, code: "sync_failed",
             error: String(exc) };
  } finally {
    chrome.tabs.remove(tab.id!).catch(() => {});
  }
}

function waitTabComplete(tabId: number, timeoutMs = 30000): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("标签页加载超时"));
    }, timeoutMs);
    const listener = (id: number, info: chrome.tabs.OnUpdatedInfo) => {
      if (id === tabId && info.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function cycle(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  if (!configured(settings)) return;
  const client = new ProtocolClient(settings);
  await runCycle({ client, checkLogins, busy, beatClock,
                   publishOne: publishViaBridge });
}

chrome.alarms.create("ext-poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {
  void cycle();
});
setInterval(() => {
  void cycle();
}, 5000);
void cycle();
