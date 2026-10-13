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

// Every probe carries its own timeout: the heartbeat fires before the claim,
// so one hung portal would stall the whole cycle — no beat, no pushes. A
// timed-out probe degrades to unknown like any other failure.
const PROBE_TIMEOUT_MS = 8000;

export async function checkZhihuLogin(fetchImpl: typeof fetch = fetch,
                                      timeoutMs = PROBE_TIMEOUT_MS): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl("https://www.zhihu.com/api/v4/me",
                              { credentials: "include",
                                signal: AbortSignal.timeout(timeoutMs) });
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
                                       fetchImpl: typeof fetch = fetch,
                                       timeoutMs = PROBE_TIMEOUT_MS): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl(url, { credentials: "include", redirect: "follow",
                                     signal: AbortSignal.timeout(timeoutMs) });
    return portalVerdict(r.url, r.ok);
  } catch {
    return "unknown";
  }
}

// Douyin, per MultiPost account/douyin.ts: the creator media user-info API
// answers 200 with a user object only for a live session.
export function douyinVerdict(status: number, body: string): LoginVerdict {
  if (status === 401 || status === 403) return "no";
  if (status !== 200) return "unknown";
  try {
    const data = JSON.parse(body) as { user?: unknown };
    return data.user ? "yes" : "no";
  } catch {
    return "unknown";
  }
}

export async function checkDouyinLogin(fetchImpl: typeof fetch = fetch,
                                       timeoutMs = PROBE_TIMEOUT_MS): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl("https://creator.douyin.com/web/api/media/user/info/",
                              { credentials: "include",
                                headers: { Accept: "application/json" },
                                signal: AbortSignal.timeout(timeoutMs) });
    return douyinVerdict(r.status, await r.text());
  } catch {
    return "unknown";
  }
}

// Xiaohongshu, per MultiPost account/rednote.ts: the explore page embeds
// window.__INITIAL_STATE__ whose user.loggedIn flag is the session truth.
// Full JSON.parse needs MultiPost's cleanup machinery; the flag itself is a
// stable boolean the regex reads straight out of the state blob.
export function rednoteVerdict(html: string): LoginVerdict {
  const state = html.match(/window\.__INITIAL_STATE__=(\{[\s\S]+?\})\s*;\s*<\/script>/)?.[1]
    || html.match(/window\.__INITIAL_STATE__=(\{[\s\S]+?\})\s*;/)?.[1]
    || html.match(/window\.__INITIAL_STATE__=(\{[\s\S]+)/)?.[1];
  if (!state) return "unknown";
  const loggedIn = state.match(/"loggedIn"\s*:\s*(true|false)/);
  if (!loggedIn) return "unknown";
  return loggedIn[1] === "true" ? "yes" : "no";
}

export async function checkRednoteLogin(fetchImpl: typeof fetch = fetch,
                                        timeoutMs = PROBE_TIMEOUT_MS): Promise<LoginVerdict> {
  try {
    const r = await fetchImpl("https://www.xiaohongshu.com/explore",
                              { credentials: "include",
                                signal: AbortSignal.timeout(timeoutMs) });
    if (!r.ok) return "unknown";
    return rednoteVerdict(await r.text());
  } catch {
    return "unknown";
  }
}
