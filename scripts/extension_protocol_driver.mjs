// Drives the compiled protocol client against a real HTTP server passed as
// argv[2]; argv[3] is the token. The server has queued an agent-lane job and
// then an extension-stamped one: the first claim must return the extension
// job (lane filtering over real HTTP), the second the 204. Prints DRIVER_OK.
import { ProtocolClient, loadSettings } from "../extension/dist/lib/protocol.js";

const [baseUrl, token] = process.argv.slice(2);
const fakeStorage = {
  get: async () => ({ INSIGHTS_SERVER_URL: baseUrl, INSIGHTS_AGENT_TOKEN: token }),
  set: async () => {},
};
const settings = await loadSettings(fakeStorage);
const client = new ProtocolClient(settings);

await client.heartbeat({ agent: "insights-ext",
  tools: { extension: { available: true, platformIds: ["wechat"],
    platforms: [{ id: "wechat", logged_in: true }] } } });

const job = await client.claim({ agent: "insights-ext", executor: "extension",
  platforms: ["wechat"] });
if (!job || job.kind !== "article_sync") throw new Error("expected the stamped job");
if (JSON.parse(job.payload).executor !== "extension") {
  throw new Error("claimed a job that is not stamped for the extension");
}
await client.sendEvent(job.id, { type: "started", agent: "insights-ext",
  tool: "extension" });
await client.sendEvent(job.id, { type: "done", status: "succeeded",
  results: [{ platform: "wechat", success: true, draft: true }] });

const miss = await client.claim({ agent: "insights-ext", executor: "extension",
  platforms: ["wechat"] });
if (miss !== null) throw new Error("expected 204 once the lane is empty");

console.log("DRIVER_OK");
