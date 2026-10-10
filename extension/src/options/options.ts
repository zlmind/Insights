import { checkWeixinLogin } from "../lib/login";
import { configured, loadSettings, ProtocolClient, saveSettings } from "../lib/protocol";

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

function setStatus(state: "ok" | "unauthorized" | "unreachable" | "saving"): void {
  document.getElementById("status").textContent = statusText(state);
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
  const dot = document.getElementById("weixin-login");
  dot.textContent = (await checkWeixinLogin()) ? "公众号：已登录" : "公众号：未登录";
}

async function render(): Promise<void> {
  const settings = await loadSettings(chrome.storage.local);
  (document.getElementById("server-url") as HTMLInputElement).value = settings.serverUrl;
  (document.getElementById("agent-token") as HTMLInputElement).value = settings.token;
  if (configured(settings)) void testConnection();
  else setStatus("unreachable");
}

// Page wiring only runs where a DOM exists; the unit tests import the pure
// helpers from a node environment.
if (typeof document !== "undefined") {
document.getElementById("save")?.addEventListener("click", async () => {
  const serverUrl = (document.getElementById("server-url") as HTMLInputElement).value;
  const token = (document.getElementById("agent-token") as HTMLInputElement).value;
  const pattern = originPattern(serverUrl);
  if (!pattern) {
    document.getElementById("status").textContent = "服务器地址必须是 http(s) URL";
    return;
  }
  const granted = await chrome.permissions.request({ origins: [pattern] });
  if (!granted) {
    document.getElementById("status").textContent =
      "未授予该服务器的访问权限，设置未保存";
    return;
  }
  setStatus("saving");
  await saveSettings(chrome.storage.local, { serverUrl, token });
  await testConnection();
});
void render();
}
