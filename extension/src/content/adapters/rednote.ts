// Ported from MultiPost-Extension src/sync/dynamic/rednote.ts, upstream
// commit 9e9138831b7a3c782d9010f7dfd1ae6d474ebe11. Apache-2.0 — NOTICE.
//
// Adapted for the Insights publisher:
// - the publish step stops: MultiPost can auto-click 发布 (its
//   isAutoPublish), we always fill and hand the open tab back — the product
//   promise is draft-first, a human confirms;
// - the image comes from one bundled asset via chrome.runtime.getURL, so no
//   cross-origin image fetch (MultiPost fetches caller-provided URLs);
// - no tag suffix (our payloads carry none).

interface RednotePayload {
  title: string;
  content: string;
  imageUrl: string;
}

export type RednotePublishResult =
  | { ok: true; detail: string }
  | { ok: false; code: "sync_failed"; error: string };

function waitForElement(selector: string, timeout = 10000): Promise<Element> {
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

async function uploadImages(imageUrl: string): Promise<void> {
  const fileInput = (await waitForElement('input[type="file"]')) as HTMLInputElement;
  const response = await fetch(imageUrl);
  if (!response.ok) throw new Error(`默认封面读取失败：HTTP ${response.status}`);
  const blob = await response.blob();
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(new File([blob], "cover.png", { type: blob.type }));
  if (dataTransfer.files.length === 0) throw new Error("默认封面装入失败");
  fileInput.files = dataTransfer.files;
  fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(5000); // MultiPost's wait: the platform digests the upload
}

export async function publishRednoteNote(payload: RednotePayload): Promise<RednotePublishResult> {
  try {
    await waitForElement('span[class="title"]');
    await sleep(1000);

    // 进入图文模式
    const uploadButtons = document.querySelectorAll('span[class="title"]');
    const uploadButton = Array.from(uploadButtons).find((element) =>
      element.textContent?.includes("上传图文")) as HTMLElement | undefined;
    if (!uploadButton) return { ok: false, code: "sync_failed", error: "未找到「上传图文」入口" };
    uploadButton.click();
    uploadButton.dispatchEvent(new Event("click", { bubbles: true }));
    await sleep(1000);

    await uploadImages(payload.imageUrl);

    // 标题
    const titleInput = (await waitForElement('input[type="text"]')) as HTMLInputElement;
    titleInput.value = payload.title || "";
    titleInput.dispatchEvent(new Event("input", { bubbles: true }));

    // 正文：粘贴事件，让平台走自己的输入处理
    const contentEditor = (await waitForElement('div[contenteditable="true"]')) as HTMLDivElement;
    contentEditor.focus();
    const paste = new ClipboardEvent("paste", {
      bubbles: true, cancelable: true, clipboardData: new DataTransfer(),
    });
    paste.clipboardData!.setData("text/plain", payload.content || "");
    contentEditor.dispatchEvent(paste);
    await sleep(1000);
    contentEditor.blur();

    // No publish click: the tab stays open for the author's confirmation.
    return { ok: true, detail: "已填好笔记，请在打开的页面确认发布" };
  } catch (exc) {
    return { ok: false, code: "sync_failed", error: exc instanceof Error ? exc.message : String(exc) };
  }
}
