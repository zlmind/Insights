// Ported from MultiPost-Extension src/sync/article/toutiao.ts, upstream
// commit 9e9138831b7a3c782d9010f7dfd1ae6d474ebe11. Apache-2.0 — NOTICE.
//
// Adapted for the Insights publisher:
// - 预览并发布 is never clicked (MultiPost's isAutoPublish gate) — the filled
//   editor stays open for the author, per draft-first;
// - the cover, when the card carries one, arrives as a data URL from the
//   service worker (no cross-origin read);
// - cover steps that come up missing are skipped rather than fatal — a
//   filled article without a cover still beats a dead run.

import { markdownToHtml } from "../../lib/markdown";

interface ToutiaoPayload {
  title: string;
  content: string;
  imageUrl?: string;
}

export type ToutiaoPublishResult =
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

async function uploadCover(imageUrl: string): Promise<void> {
  // 清除已有封面 → 打开添加面板 → 切到上传图片 → 塞文件 → 确认
  for (let i = 0; i < 20; i++) {
    const closeButton = document.querySelector(".article-cover-delete") as HTMLElement | null;
    if (!closeButton) break;
    closeButton.click();
    await sleep(500);
  }

  const addButton = document.querySelector('div[class="article-cover-add"]') as HTMLElement | null;
  if (!addButton) return; // no cover slot on this editor variant — skip
  addButton.dispatchEvent(new Event("click", { bubbles: true }));
  await sleep(1000);

  const tabs = document.querySelectorAll("div.byte-tabs-header-title");
  const uploadTab = Array.from(tabs).find((tab) => tab.textContent?.includes("上传图片"));
  if (uploadTab) {
    (uploadTab as HTMLElement).dispatchEvent(new Event("click", { bubbles: true }));
    await sleep(1000);
  }

  const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement | null;
  if (!fileInput) return;
  const response = await fetch(imageUrl);
  if (!response.ok) throw new Error(`封面读取失败：HTTP ${response.status}`);
  const buffer = await response.arrayBuffer();
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(new File([buffer], "cover.png", { type: "image/png" }));
  if (dataTransfer.files.length === 0) return;
  fileInput.files = dataTransfer.files;
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  fileInput.dispatchEvent(new Event("input", { bubbles: true }));
  await sleep(5000);

  const confirmButton = document.querySelector('button[data-e2e="imageUploadConfirm-btn"]');
  if (confirmButton) {
    confirmButton.dispatchEvent(new Event("click", { bubbles: true }));
    await sleep(2000);
  }
}

export async function publishToutiaoArticle(payload: ToutiaoPayload): Promise<ToutiaoPublishResult> {
  try {
    const titleTextarea = (await waitForElement(
      'textarea[placeholder="请输入文章标题（2～30个字）"]')) as HTMLTextAreaElement;
    titleTextarea.value = payload.title.slice(0, 30);
    titleTextarea.dispatchEvent(new Event("input", { bubbles: true }));
    titleTextarea.dispatchEvent(new Event("change", { bubbles: true }));

    const editor = (await waitForElement('div[contenteditable="true"]')) as HTMLDivElement;
    editor.focus();
    const pasteEvent = new ClipboardEvent("paste", {
      bubbles: true, cancelable: true, clipboardData: new DataTransfer(),
    });
    pasteEvent.clipboardData!.setData("text/html", markdownToHtml(payload.content || ""));
    editor.dispatchEvent(pasteEvent);
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    editor.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(5000);

    if (payload.imageUrl) {
      await uploadCover(payload.imageUrl);
    }

    // 预览并发布 is the author's click, not ours.
    return { ok: true, detail: "已填好草稿，请在打开的页面确认发布" };
  } catch (exc) {
    return { ok: false, code: "sync_failed",
             error: exc instanceof Error ? exc.message : String(exc) };
  }
}
