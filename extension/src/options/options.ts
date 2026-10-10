import { checkWeixinLogin } from "../lib/login";
import { configured, loadSettings, pairWithCode, ProtocolClient, saveSettings } from "../lib/protocol";

export function originPattern(serverUrl: string): string | null {
  try {
    const u = new URL(serverUrl);
    return u.protocol.startsWith("http") ? `${u.protocol}//${u.host}/*` : null;
  } catch {
    return null;
  }
}

// The pairing code is typed by a human from a screen: tolerate stray
// separators and case, then insist on the eight-character shape.
export function normalizePairCode(input: string): string | null {
  const cleaned = input.trim().toUpperCase().replace(/[\s-]/g, "");
  return /^[A-Z0-9]{8}$/.test(cleaned) ? cleaned : null;
}

export function statusText(
  state: "ok" | "unauthorized" | "unreachable" | "saving",
): string {
  if (state === "ok") return "已连接";
  if (state === "unauthorized") return "密钥被拒绝（401），请核对 AGENT_TOKEN";
  if (state === "unreachable") return "服务器不可达，请检查地址";
  return "保存中…";
}

function el(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`options page is missing #${id}`);
  return node;
}

function setStatus(state: "ok" | "unauthorized" | "unreachable" | "saving"): void {
  el("status").textContent = statusText(state);
}

function loginProbe(): Promise<{ id: string; logged_in: boolean }> {
  return checkWeixinLogin().then((logged_in) => ({ id: "wechat", logged_in }));
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
    setStatus(String(exc).includes("401") ? "unauthorized" : "unreachable");
  }
  const dot = el("weixin-login");
  dot.textContent = (await checkWeixinLogin()) ? "公众号：已登录" : "公众号：未登录";
}

async function render(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  (el("server-url") as HTMLInputElement).value = settings.serverUrl;
  (el("agent-token") as HTMLInputElement).value = settings.token;
  if (configured(settings)) void testConnection();
  else setStatus("unreachable");
}

// Page wiring only runs where a DOM exists; the unit tests import the pure
// helpers from a node environment.
if (typeof document !== "undefined") {
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
document.getElementById("pair")?.addEventListener("click", async () => {
  const serverUrl = (el("server-url") as HTMLInputElement).value;
  const code = normalizePairCode((el("pair-code") as HTMLInputElement).value);
  if (!code) {
    el("status").textContent = "配对码是 8 位字母数字，见发布队列页的「配对浏览器扩展」";
    return;
  }
  if (!await ensurePermission(serverUrl)) return;
  el("status").textContent = "配对中…";
  try {
    const token = await pairWithCode(
      serverUrl.trim().replace(/\/+$/, ""), code);
    await saveSettings(chrome.storage.local, { serverUrl, token });
    (el("agent-token") as HTMLInputElement).value = token;
    await testConnection();
  } catch (exc) {
    el("status").textContent = String(exc).includes("403")
      ? "配对码无效或已过期——回发布队列页重新生成一个"
      : `配对失败：${String(exc)}`;
  }
});

document.getElementById("save")?.addEventListener("click", async () => {
  const serverUrl = (el("server-url") as HTMLInputElement).value;
  const token = (el("agent-token") as HTMLInputElement).value;
  if (!await ensurePermission(serverUrl)) return;
  setStatus("saving");
  await saveSettings(chrome.storage.local, { serverUrl, token });
  await testConnection();
});
void render();
}
