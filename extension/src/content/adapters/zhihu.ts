// Ported from MultiPost-Extension src/sync/article/zhihu.ts, upstream
// commit 9e9138831b7a3c782d9010f7dfd1ae6d474ebe11. Apache-2.0 — NOTICE.
//
// Adapted for the Insights publisher:
// - publishArticle's isAutoPublish click is never sent — the filled editor
//   stays open for the author (draft-first; zhuanlan keeps editing state);
// - the cover, when the card carries one, arrives as a data URL from the
//   service worker (no cross-origin read).

import { markdownToHtml } from "../../lib/markdown";

interface ZhihuPayload {
  title: string;
  content: string;
  imageUrl?: string;
}

export type ZhihuPublishResult =
  | { ok: true; detail: string }
  | { ok: false; code: "sync_failed"; error: string };

function waitForElement(selector: string, timeout = 15000): Promise<Element> {
  return new Promise((resolve, reject) => {
    const element = document.querySelector(selector);
    if (element) {
      resolve(element);
      return;
    }
    const observer = new MutationObserver(() => {
      const element = document.querySelector(selector);
      if (element) {
        resolve(element);
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => {
      observer.disconnect();
      reject(new Error(`Element with selector "${selector}" not found within ${timeout}ms`));
    }, timeout);
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function pasteHtml(editor: HTMLElement, html: string): Promise<void> {
  editor.focus();
  const pasteEvent = new ClipboardEvent("paste", {
    bubbles: true, cancelable: true, clipboardData: new DataTransfer(),
  });
  pasteEvent.clipboardData!.setData("text/html", html);
  editor.dispatchEvent(pasteEvent);
  editor.dispatchEvent(new Event("input", { bubbles: true }));
  editor.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(5000); // zhuanlan re-hosts pasted images itself — give it time
}

async function uploadCover(imageUrl: string): Promise<void> {
  const fileInput = (await waitForElement('input[type="file"]')) as HTMLInputElement;
  const response = await fetch(imageUrl);
  if (!response.ok) throw new Error(`封面读取失败：HTTP ${response.status}`);
  const buffer = await response.arrayBuffer();
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(new File([buffer], "cover.png", { type: "image/png" }));
  if (dataTransfer.files.length === 0) throw new Error("封面装入失败");
  fileInput.files = dataTransfer.files;
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  fileInput.dispatchEvent(new Event("input", { bubbles: true }));
  await sleep(5000);
}

export async function publishZhihuArticle(payload: ZhihuPayload): Promise<ZhihuPublishResult> {
  try {
    const titleTextarea = (await waitForElement(
      'textarea[placeholder="请输入标题（最多 100 个字）"]')) as HTMLTextAreaElement;
    titleTextarea.value = payload.title.slice(0, 100);
    titleTextarea.dispatchEvent(new Event("input", { bubbles: true }));
    titleTextarea.dispatchEvent(new Event("change", { bubbles: true }));

    const editor = (await waitForElement('div[data-contents="true"]')) as HTMLDivElement;
    await pasteHtml(editor, markdownToHtml(payload.content || ""));

    // 封面可选：卡片带图才传（MultiPost 同款入口），没有就交给平台默认
    if (payload.imageUrl) {
      await uploadCover(payload.imageUrl);
    }

    // No publish click: zhuanlan's 编辑器 keeps the draft; the author confirms.
    return { ok: true, detail: "已填好草稿，请在打开的页面确认发布" };
  } catch (exc) {
    return { ok: false, code: "sync_failed",
             error: exc instanceof Error ? exc.message : String(exc) };
  }
}
