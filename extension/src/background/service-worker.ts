import { checkWeixinVerdict } from "../lib/login";
import { checkDouyinLogin, checkPortalLogin, checkXiaohongshuLogin,
         checkZhihuLogin, LoginVerdict } from "../lib/platform-login";
import { sameServerImageUrl } from "../lib/image-url";
import { configured, loadSettings, ProtocolClient } from "../lib/protocol";
import { backoffSeconds, cachedCheckLogins, LoginCache, LoginVerdicts,
         PLATFORM_IDS, PlatformResult, runCycle } from "./orchestrator";

const busy = { current: null as number | null };
const beatClock = { lastBeat: 0 };
// One shared 30s cache: the 5s claim loop and the 30s heartbeat share at
// most one round of the five probes.
const loginCache: LoginCache = { at: 0, value: {} };

const PLATFORM_CHECKS: Record<string, () => Promise<LoginVerdict>> = {
  wechat: () => checkWeixinVerdict(),
  zhihu: () => checkZhihuLogin(),
  toutiao: () => checkPortalLogin("https://mp.toutiao.com/"),
  xiaohongshu: () => checkXiaohongshuLogin(),
  douyin: () => checkDouyinLogin(),
};

async function checkLogins(): Promise<LoginVerdicts> {
  return cachedCheckLogins(PLATFORM_CHECKS, loginCache, Date.now());
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

const PUBLISH_PAGES: Record<string, { url: string; keepTab: boolean }> = {
  // Weixin lands on the newly-created draft editor and stays open for review;
  // the other article platforms end at a filled editor the author confirms.
  wechat: { url: WEIXIN_HOME, keepTab: true },
  xiaohongshu: { url: "https://creator.xiaohongshu.com/publish/publish?target=image",
                 keepTab: true },
  zhihu: { url: "https://zhuanlan.zhihu.com/write", keepTab: true },
  toutiao: { url: "https://mp.toutiao.com/profile_v4/graphic/publish", keepTab: true },
};

// The bundled cover, as a data URL. The SW reads its own resource freely;
// handing the content script a data URL sidesteps page-CORS entirely — a
// direct chrome-extension:// fetch from the tab is what "Failed to fetch"
// was (job 18/19/20).
async function toDataUrl(url: string): Promise<string> {
  const blob = await (await fetch(url)).blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(blob);
  });
}

// A card with its own picture sends that picture; the bundled cover is the
// fallback for text-only cards on platforms that require an image. Both
// travel as data URLs — the SW fetches cross-origin freely, the content
// script cannot. The card's image_url records whatever loopback host the
// browser used at upload time; the SW only has host permission for the
// paired serverUrl origin, so the host is rewritten to match before fetching.
async function imageDataUrl(item: { image_url?: string },
                            serverUrl: string): Promise<string> {
  if (item.image_url) {
    try {
      return await toDataUrl(sameServerImageUrl(item.image_url, serverUrl));
    } catch {
      // fall through to the bundled cover
    }
  }
  return toDataUrl(chrome.runtime.getURL("assets/default-cover.png"));
}

async function publishViaBridge(target: string,
                                item: { target: string; content: string;
                                        image_url?: string },
                                title: string,
                                serverUrl: string): Promise<PlatformResult> {
  const page = PUBLISH_PAGES[target];
  if (!page) {
    return { platform: target, success: false, draft: false,
             code: "unknown_target", error: `未知平台 ${target}` };
  }
  const tab = await chrome.tabs.create({ url: page.url, active: false });
  await rememberTab(tab.id!);
  const message = { type: "publish", payload: {
      target, title, content: item.content,
      imageUrl: await imageDataUrl(item, serverUrl),
    } };
  try {
    await waitTabComplete(tab.id!);
    await waitBridgeReady(tab.id!);
    try {
      const resp = await chrome.tabs.sendMessage(tab.id!, message);
      return await settleResult(target, tab.id!, page, resp);
    } catch (firstError) {
      // A late SPA navigation can close the channel mid-work; one retry on
      // the (by then) settled document.
      const text = String(firstError);
      if (!text.includes("message channel closed")) throw firstError;
      await waitBridgeReady(tab.id!, 10000);
      const resp = await chrome.tabs.sendMessage(tab.id!, message);
      return await settleResult(target, tab.id!, page, resp);
    }
  } catch (exc) {
    if (!page.keepTab) {
      chrome.tabs.remove(tab.id!).catch(() => {});
      await forgetTab();
    }
    return { platform: target, success: false, draft: false, code: "sync_failed",
             error: String(exc) };
  }
}

// Turn an adapter response into a per-platform result, and for a successful
// wechat draft navigate the still-open tab to the draft's editor page so the
// author sees and can publish it. keepTab platforms keep their tab by design.
async function settleResult(
  target: string,
  tabId: number,
  page: { url: string; keepTab: boolean },
  resp: { ok?: boolean; detail?: string; url?: string; code?: string; error?: string } | undefined,
): Promise<PlatformResult> {
  if (!resp?.ok) {
    return { platform: target, success: false, draft: false,
             code: resp?.code || "sync_failed", error: resp?.error || "适配器无响应" };
  }
  if (resp.url) {
    await chrome.tabs.update(tabId, { url: resp.url }).catch(() => {});
  }
  if (!page.keepTab) {
    chrome.tabs.remove(tabId).catch(() => {});
    await forgetTab();
  }
  return { platform: target, success: true, draft: true, detail: resp.detail };
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

// SPA pages keep navigating after "complete" (XHS rewrites its own URL and
// rebuilds its DOM), and a message sent into a dying document closes the
// channel with the adapter's work lost. Ping until the bridge answers, then
// give the page a beat to settle before the real send.
async function waitBridgeReady(tabId: number, timeoutMs = 20000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await chrome.tabs.sendMessage(tabId, { type: "ping" });
      if (resp?.type === "pong") {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        return;
      }
    } catch {
      // no listener yet — the document is still coming up
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("适配器未就绪（页面加载超时）");
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
                     publishOne: (target, item, title) =>
                       publishViaBridge(target, item, title, settings.serverUrl) });
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
