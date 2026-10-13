// Per-platform login probes for the heartbeat. Every check returns a
// three-valued verdict: "no" means "we probed and the site says logged
// out" — a network error is "unknown", never "no". The old code collapsed
// both, which made the server lie about platforms nobody checked.
export type LoginVerdict = "yes" | "no" | "unknown";

// Zhihu: the session endpoint answers 200 {id: ...} only with cookies.
export function zhihuVerdict(status: number, body: string): LoginVerdict {
  if (status === 401 || status === 403) return "no";
  if (status !== 200) return "unknown";
  try {
    const me = JSON.parse(body) as { id?: unknown };
    return typeof me.id === "string" && me.id ? "yes" : "unknown";
  } catch {
    return "unknown";
  }
}

export async function checkZhihuLogin(fetchImpl: typeof fetch = fetch): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl("https://www.zhihu.com/api/v4/me",
                              { credentials: "include" });
    return zhihuVerdict(r.status, await r.text());
  } catch {
    return "unknown";
  }
}

// Creator portals bounce logged-out visitors to a login/sso page; the
// final URL after redirects is the marker. A page that loads without a
// bounce reads as logged in — douyin's SPA may do exactly that while
// logged out, an accepted false-yes for now (spec §7); the queue result
// is the backstop a person actually sees.
export function portalVerdict(finalUrl: string, ok: boolean): LoginVerdict {
  if (!ok) return "unknown";
  return /login|sso|passport/i.test(finalUrl) ? "no" : "yes";
}

export async function checkPortalLogin(url: string,
                                       fetchImpl: typeof fetch = fetch): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl(url, { credentials: "include", redirect: "follow" });
    return portalVerdict(r.url, r.ok);
  } catch {
    return "unknown";
  }
}
