// The mp.weixin.qq.com homepage embeds the CSRF token only for a logged-in
// session; its shape is what the ported adapter also parses (weixin.ts's
// readInfo). One parser, two consumers: heartbeat dots and the adapter.
import type { LoginVerdict } from "./platform-login";

const TOKEN_PATTERNS = [
  /window\.wx\.commonData\s*=\s*\{[^}]*?\bt:\s*["'](\d+)["']/,
  /"&token\s*=\s*(\d+)"/,
];

export function parseWeixinToken(html: string): string {
  for (const re of TOKEN_PATTERNS) {
    const m = html.match(re);
    if (m) return m[1];
  }
  return "";
}

export const WEIXIN_HOME = "https://mp.weixin.qq.com/";

export function weixinVerdict(token: string): LoginVerdict {
  return token ? "yes" : "no";
}

export async function checkWeixinVerdict(fetchImpl: typeof fetch = fetch): Promise<LoginVerdict> {
  try {
    const resp = await fetchImpl(WEIXIN_HOME, { credentials: "include" });
    // The homepage answers 200 either way; only its shape knows.
    if (!resp.ok) return "unknown";
    return weixinVerdict(parseWeixinToken(await resp.text()));
  } catch {
    return "unknown";
  }
}

export async function checkWeixinLogin(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  try {
    const resp = await fetchImpl(WEIXIN_HOME, { credentials: "include" });
    if (!resp.ok) return false;
    return Boolean(parseWeixinToken(await resp.text()));
  } catch {
    return false;
  }
}
