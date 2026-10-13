import { checkWeixinVerdict, LoginVerdict } from "../lib/login";
import { configured, loadSettings, pairWithCode, ProtocolClient, saveSettings } from "../lib/protocol";

// The pairing form lives on two surfaces — the options page and the toolbar
// popup — and both are the same controls with the same ids. This module owns
// the wiring and the pure helpers once; each surface's entry script just
// calls initPairingView() (options re-exports the helpers for the tests).

export function originPattern(serverUrl: string): string | null {
  try {
    const u = new URL(serverUrl);
    return u.protocol.startsWith("http") ? `${u.protocol}//${u.host}/*` : null;
  } catch {
    return null;
  }
}

export function statusText(
  state: "ok" | "unauthorized" | "unreachable" | "saving",
): string {
  if (state === "ok") return "已连接";
  if (state === "unauthorized") return "密钥被拒绝（401），请核对 AGENT_TOKEN";
  if (state === "unreachable") return "服务器不可达，请检查地址";
  return "保存中…";
}

// The pairing code is typed by a human from a screen: tolerate stray
// separators and case, then insist on the eight-character shape.
export function normalizePairCode(input: string): string | null {
  const cleaned = input.trim().toUpperCase().replace(/[\s-]/g, "");
  return /^[A-Z0-9]{8}$/.test(cleaned) ? cleaned : null;
}

function el(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`pairing view is missing #${id}`);
  return node;
}

function setStatus(state: "ok" | "unauthorized" | "unreachable" | "saving"): void {
  el("status").textContent = statusText(state);
}

function loginProbe(): Promise<{ id: string; logged_in: boolean; probed: boolean }> {
  return checkWeixinVerdict().then((v: LoginVerdict) => (
    { id: "wechat", logged_in: v === "yes", probed: v !== "unknown" }));
}

async function testConnection(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  const client = new ProtocolClient(settings);
  try {
    await client.heartbeat({
      agent: "insights-ext",
      tools: { extension: { available: true, platformIds: ["wechat"],
        platforms: [await loginProbe()] } },
    });
    setStatus("ok");
  } catch (exc) {
    // Debug-first: the raw reason beats a guessed category — an unreachable
    // fetch, a rejected token and an empty storage all look alike otherwise.
    const text = String(exc);
    setStatus(text.includes("401") ? "unauthorized" : "unreachable");
    el("status").textContent += `（${text}｜地址=${settings.serverUrl || "空"}，token 长度=${settings.token.length}）`;
  }
  const dot = el("weixin-login");
  const verdict = await checkWeixinVerdict();
  dot.textContent = verdict === "yes" ? "公众号：已登录"
    : verdict === "no" ? "公众号：未登录" : "公众号：检测失败";
}

async function render(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  (el("server-url") as HTMLInputElement).value = settings.serverUrl;
  // The popup omits the manual token field on purpose — pairing is its only
  // path onto a token — so this lookup must stay soft where el() would throw.
  const tokenInput = document.getElementById("agent-token") as HTMLInputElement | null;
  if (tokenInput) tokenInput.value = settings.token;
  if (configured(settings)) void testConnection();
  else setStatus("unreachable");
}

async function ensurePermission(serverUrl: string): Promise<boolean> {
  const pattern = originPattern(serverUrl);
  if (!pattern) {
    el("status").textContent = "服务器地址必须是 http(s) URL";
    return false;
  }
  const granted = await chrome.permissions.request({ origins: [pattern] });
  if (!granted) {
    el("status").textContent = "未授予该服务器的访问权限，设置未保存";
    return false;
  }
  return true;
}

// One-click pairing: the queue page's code trades for the token here, so the
// secret never passes through anyone's hands.
async function onPair(): Promise<void> {
  const serverUrl = (el("server-url") as HTMLInputElement).value;
  const code = normalizePairCode((el("pair-code") as HTMLInputElement).value);
  if (!code) {
    el("status").textContent = "配对码是 8 位字母数字，见发布队列页的「配对浏览器扩展」";
    return;
  }
  if (!await ensurePermission(serverUrl)) return;
  el("status").textContent = "配对中…";
  try {
    const token = await pairWithCode(serverUrl.trim().replace(/\/+$/, ""), code);
    await saveSettings(chrome.storage.local, { serverUrl, token });
    const tokenInput = document.getElementById("agent-token") as HTMLInputElement | null;
    if (tokenInput) tokenInput.value = token;
    await testConnection();
  } catch (exc) {
    el("status").textContent = String(exc).includes("403")
      ? "配对码无效或已过期——回发布队列页重新生成一个"
      : `配对失败：${String(exc)}`;
  }
}

async function onSave(): Promise<void> {
  const serverUrl = (el("server-url") as HTMLInputElement).value;
  const tokenInput = document.getElementById("agent-token") as HTMLInputElement | null;
  const token = tokenInput ? tokenInput.value : "";
  if (!await ensurePermission(serverUrl)) return;
  setStatus("saving");
  await saveSettings(chrome.storage.local, { serverUrl, token });
  await testConnection();
}

export function initPairingView(): void {
  el("pair").addEventListener("click", () => {
    void onPair();
  });
  const save = document.getElementById("save");
  save?.addEventListener("click", () => {
    void onSave();
  });
  void render();
}
