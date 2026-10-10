import { checkWeixinLogin } from "../lib/login";
import { configured, loadSettings, ProtocolClient } from "../lib/protocol";
import { backoffSeconds, PLATFORM_IDS, PlatformResult, runCycle } from "./orchestrator";

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

const WEIXIN_HOME = "https://mp.weixin.qq.com/";
const OPEN_TAB_KEY = "openPublishTab";

// The publish tab outlives nothing: a service worker that dies mid-publish
// (browser restart, laptop lid) would otherwise leave a stray mp.weixin tab.
// The id lives in session storage; a freshly started SW closes the leftover.
async function rememberTab(tabId: number): Promise<void> {
  await chrome.storage.session.set({ [OPEN_TAB_KEY]: tabId });
}

async function forgetTab(): Promise<void> {
  await chrome.storage.session.remove(OPEN_TAB_KEY);
}

async function closeLeftoverTab(): Promise<void> {
  const stored = await chrome.storage.session.get(OPEN_TAB_KEY);
  const tabId = stored[OPEN_TAB_KEY];
  if (typeof tabId === "number") {
    await chrome.tabs.remove(tabId).catch(() => {});
    await forgetTab();
  }
}

async function publishViaBridge(target: string,
                                item: { target: string; content: string },
                                title: string): Promise<PlatformResult> {
  if (target !== "wechat") {
    return { platform: target, success: false, draft: false,
             code: "unknown_target", error: `未知平台 ${target}` };
  }
  const tab = await chrome.tabs.create({ url: WEIXIN_HOME, active: false });
  await rememberTab(tab.id!);
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
    await forgetTab();
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

// One cycle at a time, decided synchronously: busy is only set after the
// claim round-trip inside runCycle, so two 5s ticks overlapping a slow claim
// could each pass a null-busy check and run two publishes at once.
let cycleInFlight = false;
let consecutiveFailures = 0;
let pausedUntil = 0;

async function cycle(): Promise<void> {
  if (cycleInFlight || Date.now() < pausedUntil) return;
  cycleInFlight = true;
  try {
    const settings = await loadSettings(chrome.storage.local);
    if (!configured(settings)) return;
    const client = new ProtocolClient(settings);
    await runCycle({ client, checkLogins, busy, beatClock,
                     publishOne: publishViaBridge });
    consecutiveFailures = 0;
  } catch {
    // The loop outlives any one cycle; back off while the server is down
    // instead of hammering it every five seconds.
    consecutiveFailures += 1;
    pausedUntil = Date.now() + backoffSeconds(consecutiveFailures) * 1000;
  } finally {
    cycleInFlight = false;
  }
}

chrome.alarms.create("ext-poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {
  void cycle();
});
setInterval(() => {
  void cycle();
}, 5000);
void closeLeftoverTab();
void cycle();
