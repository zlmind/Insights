import { publishRednoteNote } from "./adapters/rednote";
import { publishToutiaoArticle } from "./adapters/toutiao";
import { publishWeixinArticle } from "./adapters/weixin";
import { publishZhihuArticle } from "./adapters/zhihu";

type PublishAdapter = (payload: Record<string, unknown>) => Promise<unknown>;

const ADAPTERS: Record<string, PublishAdapter> = {
  wechat: (p) => publishWeixinArticle(p as { title: string; content: string }),
  xiaohongshu: (p) => publishRednoteNote(p as { title: string; content: string; imageUrl: string }),
  zhihu: (p) => publishZhihuArticle(p as { title: string; content: string; imageUrl?: string }),
  toutiao: (p) => publishToutiaoArticle(p as { title: string; content: string; imageUrl?: string }),
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "ping") {
    sendResponse({ type: "pong" });
    return false;
  }
  if (msg?.type !== "publish") return false;
  const payload = msg.payload ?? {};
  const adapter = ADAPTERS[payload.target];
  const work: Promise<unknown> = adapter
    ? adapter(payload)
    : Promise.resolve({ ok: false, code: "sync_failed", error: `未知平台 ${payload.target}` });
  work.then(sendResponse)
    .catch((exc) => sendResponse(
      { ok: false, code: "sync_failed", error: String(exc) }));
  return true; // async response
});
