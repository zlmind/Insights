// Ported from MultiPost-Extension (https://github.com/leaperone/MultiPost-Extension)
// src/sync/article/weixin.ts, upstream commit 9e9138831b7a3c782d9010f7dfd1ae6d474ebe11.
// Copyright MultiPost contributors, Apache License 2.0 — see ../../../NOTICE.
//
// Adapted for the Insights publisher:
// - draft-first result contract (publishWeixinArticle resolves instead of
//   navigating to the edit page — the SW closes this background tab itself);
// - float-tip UI and console debug noise removed;
// - cover falls back to the first content image, then to a bundled 1x1
//   placeholder (draft creation is the goal; cover quality is a known M1
//   tradeoff the real-machine E2E checklist confirms);
// - digest derived from the markdown, login state via lib/login.

import { buildDigest, markdownToHtml } from "../../lib/markdown";
import { parseWeixinToken } from "../../lib/login";

interface WeixinUploadResult {
  fileId: number;
  url: string;
}

interface CropedImage {
  url: string;
  fileId: number;
  height: number;
  width: number;
  ratio?: string;
}

interface CropConfig {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  x1_abs: number;
  y1_abs: number;
  x2_abs: number;
  y2_abs: number;
}

export type WeixinPublishResult =
  | { ok: true; detail: string }
  | { ok: false; code: "needs_login" | "sync_failed" | "timeout"; error: string };

// mp.weixin strips non-mmbiz hotlinks, so a content image the pipeline
// refused to re-host would render as a broken image in the draft while the
// UI reported success. Count them; any loss fails the publish.
export function uploadSummary(_total: number,
                              uploads: (WeixinUploadResult | null)[]):
    { ok: boolean; failed: number } {
  const failed = uploads.filter((u) => u === null).length;
  return { ok: failed === 0, failed };
}


const ADAPTER_TIMEOUT_MS = 240000;

export function classifyWeixinError(
  errMsg: string, pageHtml: string,
): { code: "needs_login" | "sync_failed"; error: string } {
  const message = errMsg || "";
  // An empty pageHtml means "no page context given" — classify by message
  // alone. A non-empty page without a token is a logged-out session.
  const pageChecked = pageHtml.length > 0;
  if ((pageChecked && !parseWeixinToken(pageHtml)) || /登录|token/i.test(message)) {
    return { code: "needs_login",
             error: message || "公众号未登录，请在本浏览器登录 mp.weixin.qq.com" };
  }
  return { code: "sync_failed", error: message || "公众号创建草稿失败" };
}

export function readAccount(pageHtml: string): { nickname: string; ticket: string; userName: string } {
  // MultiPost's parser (upstream 9e91388): scope the fields to the actual
  // window.wx.commonData object. The mp home page embeds other scripts whose
  // `ticket : "..."` lines are NOT the filetransfer ticket — whole-page
  // matching grabs one of those and mp rejects every upload with ret 200039.
  const dataMatch = pageHtml.match(/window\.wx\.commonData\s*=\s*\{([\s\S]*?)\};/);
  if (dataMatch) {
    const scope = dataMatch[1];
    const nickname = decodeURIComponent(
      scope.match(/nick_name\s*:\s*["']([^"']+)["']/)?.[1] || "");
    const ticket = scope.match(/ticket\s*:\s*["']([^"']+)["']/)?.[1] || "";
    const userName = scope.match(/user_name\s*:\s*["']([^"']+)["']/)?.[1] || "";
    return { nickname, ticket, userName };
  }
  // Fallback mirrors MultiPost's: quote the fields exactly, comma-terminated.
  const nickname = decodeURIComponent(
    pageHtml.match(/nick_name\s*:\s*["']([^"']+)["']/)?.[1]
    || pageHtml.match(/&nick_name\s*=\s*([^&"' ]+)/)?.[1] || "");
  const ticket = pageHtml.match(/ticket\s*:\s*"(\w+)",/)?.[1] || "";
  const userName = pageHtml.match(/user_name\s*:\s*"(\w+)",/)?.[1] || "";
  return { nickname, ticket, userName };
}

function calculateCropConfig(ratio: number, width: number, height: number): CropConfig {
  let x1: number;
  let y1: number;
  let x2: number;
  let y2: number;
  let x1_abs: number;
  let y1_abs: number;
  let x2_abs: number;
  let y2_abs: number;

  if (width / height > ratio) {
    const targetWidth = height * ratio;
    const cropPercent = (width - targetWidth) / 2 / width;
    x1 = cropPercent;
    y1 = 0;
    x2 = 1 - cropPercent;
    y2 = 1;
    x1_abs = Math.round(cropPercent * width);
    y1_abs = 0;
    x2_abs = Math.round((1 - cropPercent) * width);
    y2_abs = height;
  } else {
    const targetHeight = width / ratio;
    const cropPercent = (height - targetHeight) / 2 / height;
    x1 = 0;
    y1 = cropPercent;
    x2 = 1;
    y2 = 1 - cropPercent;
    x1_abs = 0;
    y1_abs = Math.round(cropPercent * height);
    x2_abs = width;
    y2_abs = Math.round((1 - cropPercent) * height);
  }
  return { x1, y1, x2, y2, x1_abs, y1_abs, x2_abs, y2_abs };
}

function getImageInfo(url: string): Promise<{ width: number; height: number; blob: Blob }> {
  return fetch(url).then((resp) => {
    if (!resp.ok) throw new Error("获取图片失败");
    return resp.blob();
  }).then((blob) => new Promise((resolve, reject) => {
    const img = new Image();
    const objectUrl = URL.createObjectURL(blob);
    img.onload = () => {
      resolve({ width: img.width, height: img.height, blob });
      URL.revokeObjectURL(objectUrl);
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("获取图片尺寸失败"));
    };
    img.src = objectUrl;
  }));
}

export async function publishWeixinArticle(payload: {
  title: string;
  content: string;
}): Promise<WeixinPublishResult> {
  const pageHtml = document.documentElement.innerHTML;
  const token = parseWeixinToken(pageHtml);
  if (!token) {
    return { ok: false, code: "needs_login",
             error: "公众号未登录，请在本浏览器登录 mp.weixin.qq.com" };
  }
  const { nickname, ticket, userName } = readAccount(pageHtml);
  // mp's own rejection reason for the last upload, surfaced in error strings.
  let uploadRejection = "";
  if (!ticket || !userName) {
    // The filetransfer upload is signed with these; without them every
    // upload is rejected server-side. Say so instead of a generic failure.
    return { ok: false, code: "sync_failed",
             error: "页面上没有 ticket/user_name：mp 页面结构可能已变化，无法上传素材" };
  }

  const race = <T,>(work: Promise<T>): Promise<T> =>
    Promise.race([
      work,
      new Promise<never>((_, reject) => {
        setTimeout(() => reject(new Error("__timeout__")), ADAPTER_TIMEOUT_MS);
      }),
    ]);

  try {
    return await race((async (): Promise<WeixinPublishResult> => {
      const title = payload.title || "未命名";
      const digest = buildDigest(payload.content || "");
      const html = markdownToHtml(payload.content || "");

      // Upload every content image through the mp pipeline and rewrite src,
      // the way the upstream editor does.
      const doc = new DOMParser().parseFromString(html, "text/html");
      doc.querySelectorAll("a").forEach((link) => {
        const replacement = doc.createElement("span");
        replacement.innerHTML = link.innerHTML;
        link.replaceWith(replacement);
      });
      const images = Array.from(doc.getElementsByTagName("img"));
      const uploads: (WeixinUploadResult | null)[] = [];
      let firstUpload: WeixinUploadResult | null = null;
      for (const img of images) {
        const src = img.getAttribute("src");
        if (!src) continue;
        const result = (await uploadImageBySource(src, token))
          ?? (await uploadImage({ url: src }, 8, { userName, ticket, token }));
        uploads.push(result);
        if (result) {
          if (!firstUpload) firstUpload = result;
          img.setAttribute("src", result.url);
        }
      }
      const summary = uploadSummary(images.length, uploads);
      if (!summary.ok) {
        return { ok: false, code: "sync_failed",
                 error: `${summary.failed} 张正文图片上传失败，草稿未创建` };
      }
      const processedContent = doc.body.innerHTML;

      // Cover: the first content image, when there is one. With none, create
      // the draft without cover fields — createArticle degrades them to empty
      // strings. mp's filetransfer answers data:-derived placeholder blobs
      // with the generic ret 200039 (differential test, 2026-10-13): the
      // placeholder path never worked and is gone.
      let cropedImages: CropedImage[] = [];
      if (firstUpload) {
        const info = await getImageInfo(firstUpload.url);
        const uploadResult = await uploadImage({ url: firstUpload.url }, 8,
          { userName, ticket, token });
        if (!uploadResult) return { ok: false, code: "sync_failed",
                                    error: `封面图片上传失败（${uploadRejection || "无详情"}）` };

        const ratios = [16 / 9, 1, 3 / 4];
        const cropConfigs = ratios.map((ratio) =>
          calculateCropConfig(ratio, info.width, info.height));
        const cropped = await cropImage(uploadResult, cropConfigs, token);
        if (!cropped) return { ok: false, code: "sync_failed", error: "封面图片裁剪失败" };
        cropped.forEach((img, index) => {
          img.ratio = ["16:9", "1:1", "3:4"][index];
        });
        cropedImages = cropped;
      }

      const appMsgId = await createArticle({
        token, nickname, title, digest, content: processedContent, cropedImages,
      });
      if (!appMsgId) {
        return { ok: false, code: "sync_failed", error: "创建草稿失败（平台未返回 appMsgId）" };
      }
      return { ok: true, detail: "已保存到公众号草稿箱" };
    })());
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : String(exc);
    if (message === "__timeout__") {
      return { ok: false, code: "timeout", error: "公众号适配器执行超时" };
    }
    const classified = classifyWeixinError(message, pageHtml);
    return { ok: false, ...classified };
  }

  // ---- ported internals (Apache-2.0, MultiPost-Extension weixin.ts) ----

  async function uploadImage(
    file: { url: string }, scene = 8,
    auth: { userName: string; ticket: string; token: string },
  ): Promise<WeixinUploadResult | null> {
    uploadRejection = "";
    const formData = new FormData();
    const blob = await (await fetch(file.url)).blob();

    formData.append("type", blob.type);
    formData.append("id", Date.now().toString());
    formData.append("name", `${Date.now()}.jpg`);
    formData.append("lastModifiedDate", new Date().toString());
    formData.append("size", blob.size.toString());
    formData.append("file", blob, `${Date.now()}.jpg`);

    const url = new URL("https://mp.weixin.qq.com/cgi-bin/filetransfer");
    const timestamp = Math.floor(Date.now() / 1000);
    url.searchParams.append("action", "upload_material");
    url.searchParams.append("f", "json");
    url.searchParams.append("scene", scene.toString());
    url.searchParams.append("writetype", "doublewrite");
    url.searchParams.append("groupid", "1");
    url.searchParams.append("ticket_id", auth.userName);
    url.searchParams.append("ticket", auth.ticket);
    url.searchParams.append("svr_time", timestamp.toString());
    url.searchParams.append("token", auth.token);
    url.searchParams.append("lang", "zh_CN");
    url.searchParams.append("seq", Date.now().toString());
    url.searchParams.append("t", Math.random().toString());

    const response = await fetch(url.toString(), { method: "POST", body: formData });
    const result = await response.json();
    if (result.base_resp.err_msg !== "ok") {
      // Surface everything mp sent back, plus who we claimed to be: the
      // caller's generic string hides whether this was a bad ticket, a freq
      // cap, an undersized image or a permission change.
      uploadRejection = `mp 回复 ${JSON.stringify(result.base_resp)}`
        + `（HTTP ${response.status}，ticket ${auth.ticket.length} 位以 ${auth.ticket.slice(0, 6)} 开头`
        + `，user=${auth.userName || "空"}，来源=${file.url.startsWith("data:") ? "占位图" : "正文图"}）`;
      console.warn("[insights] weixin upload rejected:", result.base_resp, result);
      return null;
    }
    return { fileId: Number.parseInt(result.content, 10), url: result.cdn_url };
  }

  async function uploadImageBySource(
    sourceUrl: string, token_: string,
  ): Promise<WeixinUploadResult | null> {
    const url = new URL("https://mp.weixin.qq.com/cgi-bin/uploadimg2cdn");
    url.searchParams.append("lang", "zh_CN");
    url.searchParams.append("token", token_);
    url.searchParams.append("t", Math.random().toString());

    const formData = new FormData();
    formData.append("imgurl", sourceUrl);
    formData.append("t", "ajax-editor-upload-img");
    formData.append("token", token_);
    formData.append("lang", "zh_CN");
    formData.append("f", "json");
    formData.append("ajax", "1");

    try {
      const response = await fetch(url.toString(), { method: "POST", body: formData });
      const result = await response.json();
      if (result.errcode !== 0 || !result.url) return null;
      return { fileId: Number(result.file_id) || 0, url: result.url };
    } catch {
      return null;
    }
  }

  async function cropImage(
    image: WeixinUploadResult, cropConfigs: CropConfig[], token_: string,
  ): Promise<CropedImage[] | null> {
    const formData = new FormData();
    formData.append("imgurl", image.url);
    formData.append("size_count", cropConfigs.length.toString());

    cropConfigs.forEach((config, index) => {
      formData.append(`size${index}_x1`, config.x1.toString());
      formData.append(`size${index}_y1`, config.y1.toString());
      formData.append(`size${index}_x2`, config.x2.toString());
      formData.append(`size${index}_y2`, config.y2.toString());
    });

    formData.append("token", token_);
    formData.append("lang", "zh_CN");
    formData.append("f", "json");
    formData.append("ajax", "1");

    const response = await fetch(
      "https://mp.weixin.qq.com/cgi-bin/cropimage?action=crop_multi",
      { method: "POST", body: formData });
    const result = await response.json();
    if (result.base_resp.err_msg !== "ok") return null;

    return result.result.map((item: { cdnurl: string; file_id: number;
                                      height: number; width: number }) => ({
      url: item.cdnurl,
      fileId: item.file_id,
      height: item.height,
      width: item.width,
    }));
  }

  // sub=create over operate_appmsg is the draft-box creation call; nothing
  // here touches publish/群发.
  async function createArticle(article: {
    token: string;
    nickname: string;
    title: string;
    digest: string;
    content: string;
    cropedImages: CropedImage[];
  }): Promise<string | null> {
    const { cropedImages } = article;
    const formData = new FormData();

    formData.append("token", article.token);
    formData.append("lang", "zh_CN");
    formData.append("f", "json");
    formData.append("ajax", "1");
    formData.append("random", Math.random().toString());

    formData.append("AppMsgId", "");
    formData.append("count", "1");
    formData.append("data_seq", "0");
    formData.append("operate_from", "Chrome");
    formData.append("isnew", "0");
    formData.append("ad_video_transition0", "");
    formData.append("can_reward0", "0");
    formData.append("related_video0", "");
    formData.append("is_video_recommend0", "-1");

    formData.append("title0", article.title || "");
    formData.append("author0", article.nickname || "");
    formData.append("writerid0", "0");
    formData.append("fileid0", "");
    formData.append("digest0", article.digest?.slice(0, 120) || "");
    formData.append("auto_gen_digest0", article.digest?.trim() ? "0" : "1");
    formData.append("content0", article.content || "");
    formData.append("sourceurl0", "");
    formData.append("need_open_comment0", "1");
    formData.append("only_fans_can_comment0", "0");

    const defaultImage =
      cropedImages.find((img) => img.ratio === "2.35:1" || img.ratio === "1:1")?.url || "";
    formData.append("cdn_url0", defaultImage);
    formData.append("cdn_235_1_url0", defaultImage);
    formData.append("cdn_16_9_url0",
      cropedImages.find((img) => img.ratio === "16:9")?.url || "");
    formData.append("cdn_3_4_url0",
      cropedImages.find((img) => img.ratio === "3:4")?.url || "");
    formData.append("cdn_1_1_url0",
      cropedImages.find((img) => img.ratio === "1:1")?.url || "");
    formData.append("cdn_url_back0",
      cropedImages.find((img) => img.ratio === "1:1")?.url || "");

    const cropList = [
      { ratio: "16_9", x1: 0, y1: 453, x2: 1178, y2: 1116,
        file_id: cropedImages.find((img) => img.ratio === "16:9")?.fileId },
      { ratio: "1_1", x1: 0, y1: 195, x2: 1178, y2: 1374,
        file_id: cropedImages.find((img) => img.ratio === "1:1")?.fileId },
      { ratio: "3_4", x1: 0, y1: 0, x2: 1177, y2: 1570,
        file_id: cropedImages.find((img) => img.ratio === "3:4")?.fileId },
    ];
    const cropListPercent = [
      { ratio: "16_9", x1: 0, y1: 0.28897292993630574, x2: 1, y2: 0.7110270700636943,
        file_id: cropedImages.find((img) => img.ratio === "16:9")?.fileId },
      { ratio: "1_1", x1: 0, y1: 0.12484076433121016, x2: 1, y2: 0.8751592356687898,
        file_id: cropedImages.find((img) => img.ratio === "1:1")?.fileId },
      { ratio: "3_4", x1: 2.1222410865878727e-21, y1: 0, x2: 0.9997877758913412, y2: 1,
        file_id: cropedImages.find((img) => img.ratio === "3:4")?.fileId },
    ];
    formData.append("crop_list0", JSON.stringify({
      crop_list: cropList, crop_list_percent: cropListPercent }));

    formData.append("music_id0", "");
    formData.append("video_id0", "");
    formData.append("voteid0", "");
    formData.append("voteismlt0", "");
    formData.append("supervoteid0", "");
    formData.append("cardid0", "");
    formData.append("cardquantity0", "");
    formData.append("cardlimit0", "");
    formData.append("vid_type0", "");
    formData.append("show_cover_pic0", "0");
    formData.append("shortvideofileid0", "");
    formData.append("copyright_type0", "0");
    formData.append("releasefirst0", "");
    formData.append("platform0", "");
    formData.append("reprint_permit_type0", "");
    formData.append("allow_reprint0", "");
    formData.append("allow_reprint_modify0", "");
    formData.append("original_article_type0", "");
    formData.append("ori_white_list0", "");
    formData.append("free_content0", "");
    formData.append("fee0", "0");
    formData.append("ad_id0", "");
    formData.append("guide_words0", "");
    formData.append("is_share_copyright0", "0");
    formData.append("share_copyright_url0", "");
    formData.append("source_article_type0", "");
    formData.append("reprint_recommend_title0", "");
    formData.append("reprint_recommend_content0", "");
    formData.append("share_page_type0", "0");
    formData.append("share_imageinfo0", JSON.stringify({ list: [] }));
    formData.append("share_video_id0", "");
    formData.append("dot0", "{}");
    formData.append("share_voice_id0", "");
    formData.append("insert_ad_mode0", "");
    formData.append("categories_list0", "[]");
    formData.append("compose_info0", '{"list":""}');

    const url = new URL("https://mp.weixin.qq.com/cgi-bin/operate_appmsg");
    url.searchParams.set("t", "ajax-response");
    url.searchParams.set("sub", "create");
    url.searchParams.set("type", "77");
    url.searchParams.set("token", article.token);
    url.searchParams.set("lang", "zh_CN");

    const response = await fetch(url.toString(), { method: "POST", body: formData });
    const result = await response.json();
    if (!result?.appMsgId) return null;
    return result.appMsgId;
  }
}
