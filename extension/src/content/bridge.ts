import { publishRednoteNote } from "./adapters/rednote";
import { publishWeixinArticle } from "./adapters/weixin";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "publish") return false;
  const payload = msg.payload ?? {};
  const work = payload.target === "xiaohongshu"
    ? publishRednoteNote(payload as { title: string; content: string; imageUrl: string })
    : publishWeixinArticle(payload as { title: string; content: string });
  work.then(sendResponse)
    .catch((exc) => sendResponse(
      { ok: false, code: "sync_failed", error: String(exc) }));
  return true; // async response
});
