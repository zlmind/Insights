import { publishWeixinArticle } from "./adapters/weixin";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "publish") return false;
  publishWeixinArticle(msg.payload as { title: string; content: string })
    .then(sendResponse)
    .catch((exc) => sendResponse(
      { ok: false, code: "sync_failed", error: String(exc) }));
  return true; // async response
});
